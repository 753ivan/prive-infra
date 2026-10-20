"""Импорт результатов PimEyes (и любого другого сервиса без API) из файла:
PDF-отчёт («AI-generated report» на PROtect/Advanced), сохранённая HTML-страница результатов
или просто вставленный текст со ссылками. Из файла вытаскиваются все URL, служебные
(pimeyes.com, cdn, картинки-превью) отбрасываются, остальные становятся находками.
"""
from __future__ import annotations

import html
import io
import re
from urllib.parse import unquote, urlparse

from .search import domain_of

URL_RE = re.compile(r"""https?://[^\s"'<>\)\]\}]+""", re.I)
SKIP_DOMAINS = ("pimeyes.com", "facecheck.id", "lenso.ai", "search4faces.com", "camgirlfinder", "google.com", "gstatic.com",
                "googleapis.com", "cloudflare", "yandex.", "bing.com", "w3.org", "schema.org", "fonts.", "cdn.", "static.",
                "doubleclick", "googletagmanager", "facebook.com/tr", "adobe.com")
SKIP_EXT = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico", ".woff", ".woff2", ".ttf")


def extract_text(data: bytes, filename: str = "") -> str:
    name = (filename or "").lower()
    if name.endswith(".pdf") or data[:5] == b"%PDF-":
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data))
            parts = []
            for page in reader.pages:
                parts.append(page.extract_text() or "")
                # ссылки часто лежат в аннотациях, а не в тексте
                for a in page.get("/Annots", []) or []:
                    try:
                        uri = a.get_object().get("/A", {}).get("/URI")
                        if uri:
                            parts.append(str(uri))
                    except Exception:
                        continue
            return "\n".join(parts)
        except Exception:
            return data.decode("utf-8", errors="ignore")
    text = data.decode("utf-8", errors="ignore")
    if name.endswith((".html", ".htm")) or "<html" in text[:2000].lower():
        text = html.unescape(text)
        # href="..." даёт чистые ссылки, остальной HTML тоже сканируем
        hrefs = re.findall(r'href=["\']([^"\']+)["\']', text, re.I)
        text = "\n".join(hrefs) + "\n" + text
    return text


def extract_urls(text: str) -> list[str]:
    seen, out = set(), []
    for raw in URL_RE.findall(text):
        url = unquote(raw.rstrip(".,;:")).strip()
        # PimEyes оборачивает источники в редирект ?url=… — разворачиваем
        m = re.search(r"[?&](?:url|u|target|redirect)=(https?://[^&]+)", url)
        if m:
            url = unquote(m.group(1))
        low = url.lower()
        dom = domain_of(url)
        if not dom or any(s in low for s in SKIP_DOMAINS) or urlparse(url).path.lower().endswith(SKIP_EXT):
            continue
        key = url.rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        out.append(url)
    return out


def parse(data: bytes, filename: str = "") -> dict:
    text = extract_text(data, filename)
    urls = extract_urls(text)
    # оценки совпадения из PDF, если рядом с URL стоит процент
    scores = {}
    for m in re.finditer(r"(https?://\S+)[^\n%]{0,80}?(\d{2,3})\s?%", text):
        try:
            scores[m.group(1).rstrip(".,;:")] = min(int(m.group(2)), 100) / 100
        except ValueError:
            pass
    return {"urls": urls, "scores": scores, "chars": len(text)}
