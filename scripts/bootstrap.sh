#!/usr/bin/env bash
# Одноразовое подключение сервера к репозиторию prive-infra (GitOps).
# Запуск с Mac:  ssh root@2.29.103.21 'bash -s' < bootstrap.sh
set -euo pipefail
REPO="git@github.com:__GITHUB_USER__/prive-infra.git"

echo "==> 1/4 Deploy-ключ сервера"
if [ ! -f /root/.ssh/id_ed25519 ]; then
  ssh-keygen -t ed25519 -N "" -C "prive-prod-1-deploy" -f /root/.ssh/id_ed25519 -q
fi
ssh-keyscan -t ed25519 github.com >> /root/.ssh/known_hosts 2>/dev/null
echo
echo "---------- ПУБЛИЧНЫЙ DEPLOY-КЛЮЧ (добавить в GitHub → repo → Settings → Deploy keys) ----------"
cat /root/.ssh/id_ed25519.pub
echo "------------------------------------------------------------------------------------------------"
echo

echo "==> 2/4 Проверка доступа к GitHub"
if ! ssh -o BatchMode=yes -T git@github.com 2>&1 | grep -q "successfully authenticated"; then
  echo "Ключ ещё не добавлен в GitHub. Добавьте его (Deploy keys, read-only) и запустите скрипт повторно."
  exit 1
fi

echo "==> 3/4 Клонирование репозитория в /opt/prive"
cd /opt/prive
docker compose down 2>/dev/null || true
if [ ! -d .git ]; then
  git init -q
  git remote add origin "$REPO"
  git fetch -q origin main
  git reset -q --hard origin/main
  git branch -q -M main
  git branch -q --set-upstream-to=origin/main main
fi
chmod +x scripts/*.sh
docker compose up -d

echo "==> 4/4 Cron: авто-деплой каждую минуту"
cat > /etc/cron.d/prive-deploy <<'CRON'
* * * * * root /opt/prive/scripts/deploy.sh >> /var/log/prive-deploy.log 2>&1
CRON
chmod 644 /etc/cron.d/prive-deploy

echo
echo "Готово. Сервер следит за веткой main. Лог: /var/log/prive-deploy.log"
