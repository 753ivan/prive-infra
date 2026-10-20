"""FaceCheck.ID — поиск по лицу через API (WL). Индекс: соцсети, доски объявлений, новости.
3 кредита за поиск (≈$0,30). Эталон уходит на их сервер — только фото, без имени и ника.
Docs: https://facecheck.id/Face-Search/API
"""
from __future__ import annotations

import time

from ..config import settings
from .base import NotConfigured, client, log_call

BASE = "https://facecheck.id/api"


def search(image_bytes: bytes, min_score: int = 70, timeout_s: int = 90) -> list[dict]:
    if not settings.facecheck_api_token:
        raise NotConfigured("facecheck")
    headers = {"Authorization": settings.facecheck_api_token, "accept": "application/json"}
    with client() as c:
        r = c.post(f"{BASE}/upload_pic", headers=headers, files={"images": ("ref.jpg", image_bytes, "image/jpeg")})
        r.raise_for_status()
        up = r.json()
        if up.get("error"):
            log_call("facecheck", "upload", False, str(up.get("error")))
            return []
        id_search = up["id_search"]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            r = c.post(f"{BASE}/search", headers=headers, json={"id_search": id_search, "with_progress": True, "status_only": False, "demo": settings.facecheck_demo})
            r.raise_for_status()
            data = r.json()
            if data.get("error"):
                log_call("facecheck", "search", False, str(data["error"]))
                return []
            if data.get("output"):
                items = data["output"].get("items", [])
                out = [{"url": i["url"], "title": f"FaceCheck {i['score']}%", "snippet": "", "source": "facecheck",
                        "match_type": "similar" if i["score"] < 90 else "exact", "confidence": round(i["score"] / 100, 2)}
                       for i in items if i.get("score", 0) >= min_score]
                log_call("facecheck", "search", True, f"{len(items)} items, {len(out)} ≥ {min_score}")
                return out
            time.sleep(1.5)
    log_call("facecheck", "search", False, "timeout")
    return []


def status() -> dict:
    return {"facecheck": bool(settings.facecheck_api_token), "demo": settings.facecheck_demo}
