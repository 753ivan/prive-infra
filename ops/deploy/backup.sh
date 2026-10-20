#!/bin/sh
# Ежедневный бэкап: дамп БД + архив хранилища (доказательства, отчёты). Эталоны живут ≤7 дней, но тоже попадают — храните бэкапы на зашифрованном диске.
set -e
STAMP=$(date -u +%Y%m%d_%H%M)
pg_dump -h db -U prive prive | gzip > /backups/db_$STAMP.sql.gz
tar czf /backups/storage_$STAMP.tgz -C /srv/app/data storage 2>/dev/null || true
# оставляем 14 последних
ls -1t /backups/db_*.sql.gz | tail -n +15 | xargs -r rm -f
ls -1t /backups/storage_*.tgz | tail -n +15 | xargs -r rm -f
echo "backup $STAMP ok"
