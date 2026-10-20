"""Настройки: все ключи берутся из переменных окружения (.env).
Отсутствующий ключ не ломает систему — интеграция помечается «не настроено»
и конвейер работает в сухом режиме (dry-run) для этого шага.
"""
from __future__ import annotations

import os
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=BASE_DIR / ".env", extra="ignore")

    # --- приложение ---
    app_name: str = "PRIVÉ Ops"
    admin_user: str = "operator"
    admin_password: str = "change-me"
    database_url: str = f"sqlite:///{BASE_DIR / 'app' / 'data' / 'prive.db'}"
    storage_dir: Path = BASE_DIR / "app" / "data" / "storage"
    public_base_url: str = "http://localhost:8000"
    reference_photo_ttl_days: int = 7  # «не храним лишнее»

    # --- поиск по тексту (M3) ---
    google_cse_key: str = ""
    google_cse_cx: str = ""
    brave_search_key: str = ""
    serpapi_key: str = ""  # для Yandex / Google Images

    # --- поиск по лицу (M4, внешние) ---
    facecheck_api_token: str = ""
    facecheck_demo: bool = False
    search4faces_api_key: str = ""  # VK/OK/TikTok, JSON-RPC  # True — тестовый режим без списания кредитов (индекс урезан)

    # --- утечки ---
    hibp_api_key: str = ""
    dehashed_email: str = ""
    dehashed_api_key: str = ""

    # --- доказательства (M5) ---
    archive_org_access_key: str = ""  # Save Page Now, S3-style keys
    archive_org_secret_key: str = ""
    enable_playwright: bool = False  # скриншоты страниц (нужен playwright + chromium)

    # --- отправка жалоб (M6) ---
    mail_provider: str = ""  # postmark | mailgun | ""
    postmark_token: str = ""
    mailgun_api_key: str = ""
    mailgun_domain: str = ""
    mail_from: str = "abuse@prive.example"
    mail_reply_to: str = ""

    # --- Telegram (M1) ---
    telegram_bot_token: str = ""
    telegram_operator_chat_id: str = ""  # чат дежурных для SOS-эскалации
    telegram_webhook_secret: str = ""
    usdt_trc20_address: str = ""  # кошелёк для оплаты, показывается в боте

    # --- планировщик ---
    scheduler_enabled: bool = True
    takedown_recheck_days: int = 7


settings = Settings()
settings.storage_dir.mkdir(parents=True, exist_ok=True)
Path(settings.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)
