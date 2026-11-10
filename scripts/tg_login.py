#!/usr/bin/env python3
"""Сгенерировать TELEGRAM_SESSION (StringSession) — запускать ЛОКАЛЬНО (Mac/ПК), НЕ на сервере.

    pip install telethon
    python scripts/tg_login.py

Спросит API_ID и API_HASH (my.telegram.org → API development tools), номер телефона,
код из Telegram (и пароль 2FA, если включён), затем напечатает строку сессии.

Эту строку — в /opt/prive/.env на сервере как TELEGRAM_SESSION, и больше нигде её не хранить
(ни в чате, ни в git): она = полный доступ к аккаунту. Применить на сервере:
    cd /opt/prive && docker compose up -d ops
"""
import sys

try:
    from telethon.sync import TelegramClient
    from telethon.sessions import StringSession
except ImportError:
    sys.exit("Нет telethon. Установите локально:  pip install telethon")


def main() -> None:
    try:
        api_id = int(input("API_ID: ").strip())
    except ValueError:
        sys.exit("API_ID должен быть числом")
    api_hash = input("API_HASH: ").strip()
    if not api_hash:
        sys.exit("API_HASH пуст")

    # Вход интерактивный: Telethon сам спросит телефон, код из Telegram и пароль 2FA.
    with TelegramClient(StringSession(), api_id, api_hash) as client:
        session = client.session.save()
        me = client.get_me()

    print(f"\nВошли как: {getattr(me, 'username', None) or getattr(me, 'first_name', '?')}")
    print("\n=== TELEGRAM_SESSION (в /opt/prive/.env на сервере; никому не показывать) ===")
    print(session)


if __name__ == "__main__":
    main()
