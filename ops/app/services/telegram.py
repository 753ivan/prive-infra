"""M1 — Telegram Bot API: отправка (с клавиатурами), файлы, webhook/polling.
Сценарий диалога — в app/bot.py."""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta

from ..config import settings
from .base import NotConfigured, client, log_call

API = "https://api.telegram.org/bot{token}/{method}"


def _call(method: str, **payload):
    if not settings.telegram_bot_token:
        raise NotConfigured("telegram")
    payload = {k: v for k, v in payload.items() if v is not None}
    with client() as c:
        r = c.post(API.format(token=settings.telegram_bot_token, method=method), json=payload)
        r.raise_for_status()
        return r.json()


def keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """[[('Текст','callback_data'), …], …] → inline-клавиатура."""
    return {"inline_keyboard": [[{"text": t, "callback_data": d} for t, d in row] for row in rows]}


def send_message(chat_id: str, text: str, reply_markup: dict | None = None) -> bool:
    try:
        _call("sendMessage", chat_id=chat_id, text=text, parse_mode="HTML", disable_web_page_preview=True, reply_markup=reply_markup)
        log_call("telegram", f"send {chat_id}", True, text[:80])
        return True
    except NotConfigured:
        log_call("telegram", f"send {chat_id}", False, "не настроено (dry-run): " + text[:160])
        return False
    except Exception as e:  # noqa: BLE001
        log_call("telegram", f"send {chat_id}", False, repr(e))
        return False


def answer_callback(callback_id: str, text: str = ""):
    try:
        _call("answerCallbackQuery", callback_query_id=callback_id, text=text or None)
    except Exception:
        pass


def send_document(chat_id: str, path, caption: str = "") -> bool:
    if not settings.telegram_bot_token:
        log_call("telegram", f"document {chat_id}", False, "не настроено (dry-run)")
        return False
    try:
        with client() as c, open(path, "rb") as f:
            r = c.post(API.format(token=settings.telegram_bot_token, method="sendDocument"),
                       data={"chat_id": chat_id, "caption": caption}, files={"document": f})
            r.raise_for_status()
        return True
    except Exception as e:  # noqa: BLE001
        log_call("telegram", f"document {chat_id}", False, repr(e))
        return False


def alert_operators(text: str) -> bool:
    if not settings.telegram_operator_chat_id:
        log_call("telegram", "alert", False, "TELEGRAM_OPERATOR_CHAT_ID не задан: " + text[:160])
        return False
    return send_message(settings.telegram_operator_chat_id, "🚨 " + text)


def download_file(file_id: str, dest_dir) -> tuple[str, str]:
    info = _call("getFile", file_id=file_id)["result"]
    url = f"https://api.telegram.org/file/bot{settings.telegram_bot_token}/{info['file_path']}"
    with client() as c:
        data = c.get(url).content
    sha = hashlib.sha256(data).hexdigest()
    dest_dir.mkdir(parents=True, exist_ok=True)
    ext = info["file_path"].rsplit(".", 1)[-1] if "." in info["file_path"] else "bin"
    path = dest_dir / f"{sha[:16]}.{ext}"
    path.write_bytes(data)
    return str(path.relative_to(settings.storage_dir)), sha


def set_webhook(public_base_url: str) -> dict:
    return _call("setWebhook", url=f"{public_base_url}/telegram/webhook",
                 secret_token=settings.telegram_webhook_secret or None,
                 allowed_updates=["message", "callback_query"])


def delete_webhook() -> dict:
    return _call("deleteWebhook", drop_pending_updates=False)


def get_updates(offset: int | None, timeout: int = 25) -> list[dict]:
    return _call("getUpdates", offset=offset, timeout=timeout, allowed_updates=["message", "callback_query"]).get("result", [])


def webhook_info() -> dict:
    """Что Telegram знает о webhook: адрес, очередь, последняя ошибка доставки. Токен не раскрывается."""
    try:
        r = _call("getWebhookInfo").get("result", {})
    except NotConfigured:
        return {"configured": False}
    except Exception as e:  # noqa: BLE001
        return {"error": repr(e)[:200]}
    expected = f"{settings.public_base_url}/telegram/webhook"
    return {"url": r.get("url", ""), "expected": expected, "ok": r.get("url") == expected,
            "pending": r.get("pending_update_count", 0), "last_error": r.get("last_error_message", ""),
            "last_error_at": r.get("last_error_date")}


def status() -> dict:
    return {"bot": bool(settings.telegram_bot_token), "operator_chat": bool(settings.telegram_operator_chat_id),
            "webhook": webhook_info() if settings.telegram_bot_token else None}


def photo_expiry() -> datetime:
    return datetime.utcnow() + timedelta(days=settings.reference_photo_ttl_days)
