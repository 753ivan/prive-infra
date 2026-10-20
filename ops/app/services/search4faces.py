"""Search4faces — поиск по лицу в VK / OK / TikTok (JSON-RPC API). Главный для СНГ.
Сервис возвращает имя, возраст, город профиля — в реестр НЕ сохраняем, только URL и сходство:
правило «не пробиваем людей» распространяется и на найденные профили.
Docs: https://search4faces.com/api.html
"""
from __future__ import annotations

import base64

from ..config import settings
from .base import NotConfigured, client, log_call

URL = "https://search4faces.com/api/json-rpc/v1"
SOURCES = ["vkokn_avatar", "vk_wall", "tt_avatar"]  # свежие аватары VK/OK, фото со стен VK, TikTok


def _rpc(method: str, params: dict) -> dict:
    with client() as c:
        r = c.post(URL, headers={"x-authorization-token": settings.search4faces_api_key, "Content-Type": "application/json"},
                   json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
        r.raise_for_status()
        data = r.json()
    if "error" in data:
        raise RuntimeError(str(data["error"]))
    return data.get("result", {})


def search(image_bytes: bytes, min_score: float = 70.0, results: int = 50) -> list[dict]:
    if not settings.search4faces_api_key:
        raise NotConfigured("search4faces")
    det = _rpc("detectFaces", {"image": base64.b64encode(image_bytes).decode()})
    faces = det.get("faces", [])
    if not faces:
        log_call("search4faces", "detect", True, "лицо не найдено")
        return []
    face = max(faces, key=lambda f: f.get("width", 0) * f.get("height", 0))
    out, seen = [], set()
    for src in SOURCES:
        try:
            res = _rpc("searchFace", {"image": det["image"], "face": {k: face[k] for k in ("x", "y", "width", "height")},
                                      "source": src, "hidden": True, "results": results, "lang": "ru"})
        except Exception as e:  # noqa: BLE001
            log_call("search4faces", f"search {src}", False, repr(e))
            continue
        items = res.get("profiles", res.get("results", [])) or []
        n = 0
        for it in items:
            score = float(it.get("score", it.get("similarity", 0)))
            url = it.get("profile", it.get("url", "")) or ""
            if score < min_score or not url or url in seen:
                continue
            seen.add(url)
            n += 1
            # имя/возраст/город намеренно отбрасываются
            out.append({"url": url, "title": f"Search4faces {src} {score:.0f}%", "snippet": it.get("photo", ""), "source": "search4faces",
                        "match_type": "exact" if score >= 90 else "similar", "confidence": round(score / 100, 2)})
        log_call("search4faces", f"search {src}", True, f"{len(items)} items, {n} ≥ {min_score}")
    return out


def status() -> dict:
    return {"search4faces": bool(settings.search4faces_api_key)}
