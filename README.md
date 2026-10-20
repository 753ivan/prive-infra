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
| `bot/` | Telegram-бот: заявки на проверку / SOS / партнёры → группа операторов, ответы реплаем |
| `scripts/deploy.sh` | Авто-деплой (cron, раз в минуту) |
| `scripts/bootstrap.sh` | Одноразовое подключение сервера к репозиторию |

## Полезное на сервере
```bash
tail -f /var/log/prive-deploy.log      # лог деплоев
cd /opt/prive && docker compose ps     # состояние контейнеров
cd /opt/prive && docker compose logs -f caddy
```

## Telegram-бот
Секреты в `/opt/prive/.env` на сервере:
```
BOT_TOKEN=...        # от @BotFather
ADMIN_CHAT_ID=-100…  # закрытая группа операторов; узнать: добавить бота в группу, написать /id
```
После изменения `.env`: `cd /opt/prive && docker compose up -d --force-recreate bot`.
Оператор отвечает клиентке **реплаем** на любое сообщение заявки в группе. Слово «помощь» от клиентки = сигнал принуждения.
