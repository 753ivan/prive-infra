#!/usr/bin/env bash
# Вызывается cron'ом каждую минуту. Тянет изменения из git и применяет их.
set -euo pipefail
cd /opt/prive

git fetch -q origin main
LOCAL=$(git rev-parse HEAD)
REMOTE=$(git rev-parse origin/main)
[ "$LOCAL" = "$REMOTE" ] && exit 0

echo "[$(date -Is)] deploy $LOCAL -> $REMOTE"
git reset -q --hard origin/main

docker compose pull -q
docker compose up -d --remove-orphans
docker image prune -f -q >/dev/null

# Перечитать Caddyfile без даунтайма
docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>/dev/null || true

echo "[$(date -Is)] done $(git rev-parse --short HEAD)"
