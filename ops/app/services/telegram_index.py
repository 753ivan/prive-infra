"""Индексация Telegram-каналов в собственный индекс лиц (services/face_index).

Telegram — главный канал перепродажи украденного контента в СНГ, и боту чужие
каналы не видны. Поэтому фото из заданных каналов-каталогов забираются через
MTProto (Telethon, user-сессия) и кладутся в индекс лиц — бесплатно, в обход Bot API.

Модуль ОПЦИОНАЛЬНЫЙ и инертный: без пакета telethon или без ключей available()=False,
и конвейер просто не трогает Telegram — как остальные адаптеры в сухом режиме.

Ключи (в .env на сервере):
  TELEGRAM_API_ID, TELEGRAM_API_HASH  — my.telegram.org → API development tools
  TELEGRAM_SESSION                    — StringSession, сгенерировать scripts/tg_login.py (локально)
  TELEGRAM_INDEX_CHANNELS             — каналы через запятую, напр. @catalog1,@catalog2
"""
from __future__ import annotations

import asyncio
from importlib.util import find_spec

from ..config import settings
from ..db import session_scope
from . import face_index as fidx_svc
from .base import log_call


def _creds() -> tuple[int, str, str] | None:
    """(api_id, api_hash, session) если всё задано и api_id — число, иначе None."""
    try:
        api_id = int(settings.telegram_api_id or 0)
    except (TypeError, ValueError):
        return None
    if api_id and settings.telegram_api_hash and settings.telegram_session:
        return api_id, settings.telegram_api_hash, settings.telegram_session
    return None


def available() -> bool:
    """True только если установлен telethon И заданы все три ключа."""
    return find_spec("telethon") is not None and _creds() is not None


def channels() -> list[str]:
    """Список каналов из TELEGRAM_INDEX_CHANNELS, без @ и пустых."""
    raw = settings.telegram_index_channels or ""
    return [c.strip().lstrip("@") for c in raw.split(",") if c.strip()]


def _msg_url(channel: str, msg_id: int) -> str:
    return f"https://t.me/{channel.lstrip('@')}/{msg_id}"


async def _afetch(channel: str, limit: int) -> list[tuple[str, bytes]]:
    """Качает фото из последних `limit` сообщений канала → [(url, bytes), …]."""
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id, api_hash, session = _creds()
    out: list[tuple[str, bytes]] = []
    async with TelegramClient(StringSession(session), api_id, api_hash) as tg:
        async for msg in tg.iter_messages(channel, limit=limit):
            if not getattr(msg, "photo", None):
                continue
            data = await msg.download_media(file=bytes)
            if data:
                out.append((_msg_url(channel, msg.id), data))
    return out


def _fetch_channel(channel: str, limit: int) -> list[tuple[str, bytes]]:
    return asyncio.run(_afetch(channel, limit))


def index_channel(channel: str, limit: int = 200, _fetch=None) -> dict:
    """Индексирует фото одного канала в индекс лиц (source='telegram').
    `_fetch(channel, limit) -> [(url, bytes), …]` — инъекция для тестов (без сети)."""
    channel = channel.lstrip("@")
    fetch = _fetch or _fetch_channel
    try:
        items = fetch(channel, limit)
    except Exception as e:  # noqa: BLE001
        log_call("telegram_index", f"fetch {channel}", False, repr(e))
        return {"channel": channel, "images": 0, "faces": 0, "error": repr(e)}
    images = faces = 0
    with session_scope() as db:
        for url, data in items:
            try:
                k = fidx_svc.index_image(db, "telegram", url, url, data)
            except Exception:  # noqa: BLE001
                continue
            faces += k
            images += 1 if k else 0
    log_call("telegram_index", f"index {channel}", True, f"{images} img, {faces} faces")
    return {"channel": channel, "images": images, "faces": faces}


def index_all(limit: int = 200, _fetch=None) -> dict:
    """Проходит по всем каналам из TELEGRAM_INDEX_CHANNELS."""
    total: dict = {"channels": 0, "images": 0, "faces": 0, "per_channel": []}
    for ch in channels():
        r = index_channel(ch, limit, _fetch=_fetch)
        total["channels"] += 1
        total["images"] += r.get("images", 0)
        total["faces"] += r.get("faces", 0)
        total["per_channel"].append(r)
    return total


def status() -> dict:
    """Для панели: установлен ли telethon, заданы ли ключи, какие каналы."""
    return {
        "available": available(),
        "telethon_installed": find_spec("telethon") is not None,
        "configured": _creds() is not None,
        "channels": channels(),
    }
