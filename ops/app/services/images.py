"""M4 (своя часть) — поиск точных и почти точных копий эталонных фото + чистка метаданных.

pHash (perceptual hash) устойчив к пересжатию, масштабированию и лёгкой обрезке.
Расстояние Хэмминга ≤ 6 из 64 бит считаем точной копией, ≤ 12 — похожей.
Поиск по лицу (эмбеддинги) — отдельный этап, сюда подключается позже тем же интерфейсом.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from urllib.parse import urljoin

import imagehash
from PIL import Image, ImageOps

from ..config import settings
from .base import client, log_call

EXACT = 6
SIMILAR = 12
IMG_RE = re.compile(r"""<img[^>]+src=["']([^"']+)["']""", re.I)
OG_RE = re.compile(r"""<meta[^>]+property=["']og:image["'][^>]+content=["']([^"']+)["']""", re.I)


def phash_file(path: Path) -> str:
    with Image.open(path) as im:
        return str(imagehash.phash(ImageOps.exif_transpose(im).convert("RGB")))


def phash_bytes(data: bytes) -> str | None:
    try:
        with Image.open(io.BytesIO(data)) as im:
            if im.width < 120 or im.height < 120:  # иконки и аватарки пропускаем
                return None
            return str(imagehash.phash(ImageOps.exif_transpose(im).convert("RGB")))
    except Exception:
        return None


def distance(h1: str, h2: str) -> int:
    return imagehash.hex_to_hash(h1) - imagehash.hex_to_hash(h2)


def reference_hashes(client_row) -> list[tuple[str, str]]:
    """[(file, phash)] для эталонов клиентки; хеши кэшируются в JSON эталона."""
    out, changed = [], False
    photos = list(client_row.reference_photos or [])
    for p in photos:
        if not p.get("phash"):
            try:
                p["phash"] = phash_file(settings.storage_dir / p["file"])
                changed = True
            except Exception:
                continue
        out.append((p["file"], p["phash"]))
    if changed:
        client_row.reference_photos = photos
    return out


def page_images(url: str, max_images: int = 40) -> list[str]:
    with client() as c:
        r = c.get(url)
        r.raise_for_status()
        html = r.text
    urls = [m for m in OG_RE.findall(html)] + IMG_RE.findall(html)
    seen, out = set(), []
    for u in urls:
        if u.startswith("data:"):
            continue
        full = urljoin(url, u)
        if full not in seen:
            seen.add(full)
            out.append(full)
    return out[:max_images]


def match_page(url: str, refs: list[tuple[str, str]]) -> dict:
    """Скачивает картинки со страницы и сравнивает с эталонами.
    Возвращает лучшее совпадение: {match_type, confidence, image_url, ref_file, distance}."""
    best = {"match_type": "", "confidence": None, "image_url": "", "ref_file": "", "distance": 64, "images": 0}
    try:
        imgs = page_images(url)
    except Exception as e:  # noqa: BLE001
        log_call("phash", f"page {url}", False, repr(e))
        return best
    best["images"] = len(imgs)
    with client() as c:
        for iu in imgs:
            try:
                r = c.get(iu)
                if r.status_code != 200 or len(r.content) > 15_000_000:
                    continue
                h = phash_bytes(r.content)
                if not h:
                    continue
                for ref_file, ref_hash in refs:
                    d = distance(h, ref_hash)
                    if d < best["distance"]:
                        best.update({"distance": d, "image_url": iu, "ref_file": ref_file})
            except Exception:
                continue
    if best["distance"] <= EXACT:
        best["match_type"], best["confidence"] = "exact", round(1 - best["distance"] / 64, 2)
    elif best["distance"] <= SIMILAR:
        best["match_type"], best["confidence"] = "similar", round(1 - best["distance"] / 64, 2)
    log_call("phash", f"page {url}", True, f"{best['images']} img, best d={best['distance']} {best['match_type']}")
    return best


def strip_metadata(data: bytes, out_format: str = "JPEG") -> bytes:
    """«Невидимка», шаг 2: убирает EXIF/GPS/ICC, пересохраняет без метаданных."""
    with Image.open(io.BytesIO(data)) as im:
        im = ImageOps.exif_transpose(im)
        clean = Image.new(im.mode if im.mode in ("RGB", "RGBA", "L") else "RGB", im.size)
        clean.putdata(list(im.convert(clean.mode).getdata()))
        buf = io.BytesIO()
        if out_format == "JPEG" and clean.mode == "RGBA":
            clean = clean.convert("RGB")
        clean.save(buf, format=out_format, quality=92)
        return buf.getvalue()
