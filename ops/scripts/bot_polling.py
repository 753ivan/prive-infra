"""Запуск бота без публичного домена (long polling): python -m scripts.bot_polling
Для продакшена предпочтителен webhook (кнопка в панели), polling — для теста и резерва."""
import time

from app.bot import handle_update
from app.db import Base, engine, migrate_light
from app.services import telegram as tg

Base.metadata.create_all(engine)
migrate_light()
try:
    tg.delete_webhook()
except Exception:
    pass
offset = None
print("polling…")
while True:
    try:
        for upd in tg.get_updates(offset):
            offset = upd["update_id"] + 1
            try:
                handle_update(upd)
            except Exception as e:  # noqa: BLE001
                print("update error:", repr(e))
    except KeyboardInterrupt:
        break
    except Exception as e:  # noqa: BLE001
        print("poll error:", repr(e))
        time.sleep(5)
