#!/usr/bin/env bash
# Вызывается cron'ом каждую минуту: тянет main, применяет изменения и публикует отчёт в status/
# (Caddy отдаёт его по секретному URL, только чтение). Секреты (.env) в отчёт не попадают.
set -uo pipefail
cd /opt/prive
mkdir -p status
S=status/deploy.txt

git fetch -q origin main 2>>status/git.err
LOCAL=$(git rev-parse HEAD); REMOTE=$(git rev-parse origin/main)

if [ "$LOCAL" != "$REMOTE" ]; then
  {
    echo "=== DEPLOY $(date -Is) $LOCAL -> $REMOTE ==="
    git reset -q --hard origin/main && git log -1 --format='%h %s'
    docker compose pull -q --ignore-buildable 2>&1 | tail -3
    docker compose up -d --build --remove-orphans 2>&1 | grep -viE '^\s*$|Pulling fs layer|Downloading|Extracting|Waiting|Verifying' | tail -40
    echo "compose exit: ${PIPESTATUS[0]}"
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile 2>&1 | tail -2
    docker image prune -f -q >/dev/null
    echo "=== DONE $(date -Is) $(git rev-parse --short HEAD) ==="
  } > $S 2>&1
  cat $S >> /var/log/prive-deploy.log
fi

# состояние каждую минуту
{
  echo "updated: $(date -Is)   rev: $(git rev-parse --short HEAD)"
  echo; docker compose ps --format 'table {{.Name}}\t{{.Status}}' 2>&1
  echo; echo "--- ops (last 25) ---"; docker compose logs --tail=25 --no-log-prefix ops 2>&1 | sed -E 's/(TOKEN|PASSWORD|SECRET|KEY)=\S+/\1=***/g'
  echo; echo "--- caddy errors (last 10) ---"; docker compose logs --tail=200 --no-log-prefix caddy 2>&1 | grep -i '"level":"error"' | tail -10
  echo; echo "--- disk/mem ---"; df -h / | tail -1; free -m | head -2
} > status/state.txt 2>&1
