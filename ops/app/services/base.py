"""Общее для адаптеров: лог вызовов, статус настройки, HTTP-клиент."""
from __future__ import annotations

import httpx

from ..db import session_scope
from ..models import IntegrationLog

TIMEOUT = httpx.Timeout(30.0, connect=10.0)


def log_call(service: str, action: str, ok: bool, detail: str = ""):
    try:
        with session_scope() as db:
            db.add(IntegrationLog(service=service, action=action, ok=ok, detail=str(detail)[:2000]))
    except Exception:
        pass


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125 Safari/537.36"
    })


class NotConfigured(Exception):
    """Интеграция не настроена — вызывающий код переходит в сухой режим."""
