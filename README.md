# prive-infra

Инфраструктура сервера **prive-prod-1** (Hetzner CPX22, Helsinki · `2.29.103.21`).

## Как это работает
- Всё, что в ветке `main`, автоматически оказывается на сервере в течение минуты
  (`scripts/deploy.sh` по cron → `git pull` → `docker compose up -d`).
- Секреты (`.env`) в репозитории не хранятся — лежат только на сервере в `/opt/prive/.env`.
- Откат = `git revert` нужного коммита.

## Структура
| Путь | Назначение |
|---|---|
| `docker-compose.yml` | Сервисы: Caddy (reverse proxy, авто-HTTPS) + приложение |
| `caddy/Caddyfile` | Домены, заголовки безопасности, маршрутизация |
| `site/` | Статический сайт getprive.me (index, sos, partners, private) |
| `ops/` | PRIVÉ Ops — панель операторов (FastAPI) + Telegram-бот через webhook; подробности в `ops/README.md` |
| `scripts/deploy.sh` | Авто-деплой (cron, раз в минуту) |
| `scripts/bootstrap.sh` | Одноразовое подключение сервера к репозиторию |

## Полезное на сервере
```bash
tail -f /var/log/prive-deploy.log      # лог деплоев
cd /opt/prive && docker compose ps     # состояние контейнеров
cd /opt/prive && docker compose logs -f caddy
```

## PRIVÉ Ops (панель + бот)
- Панель: https://ops.getprive.me (HTTP Basic: `ADMIN_USER` / `ADMIN_PASSWORD` из `.env`).
- Бот `@prive_scan_bot` работает через webhook панели: после первого запуска открыть панель → Интеграции → «Установить webhook».
- Секреты в `/opt/prive/.env` (см. `.env.example`). После изменения: `cd /opt/prive && docker compose up -d --force-recreate ops`.
- База: PostgreSQL (volume `pgdata`); файлы (кружки, эталоны, доказательства): volume `ops_storage`.
- Бэкапы: ежедневно в `/opt/prive/backups/` (дамп БД + архив хранилища, 14 последних).
- Deep-links с сайта: `t.me/prive_scan_bot?start=sos|scan|partner|private`.
