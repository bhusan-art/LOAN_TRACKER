#!/usr/bin/env sh
# Nightly encrypted backup (Linux server). Cron example, 02:30 every night:
#   30 2 * * * cd /opt/loan_tracker && ./deploy/backup.sh >> backups/backup.log 2>&1
# Needs BACKUP_PASSPHRASE in the environment (or .env). Copy backups/ offsite
# (rclone, rsync to another machine) — a backup on the same disk is not a backup.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] && . ./.env
: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
mkdir -p backups
stamp=$(date +%Y-%m-%d_%H%M)
out="backups/loan_tracker_${stamp}.sql.gz.gpg"
docker compose exec -T db pg_dump -U loan_tracker -d loan_tracker --no-owner \
  | gzip -9 \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase "$BACKUP_PASSPHRASE" -o "$out"
echo "$(date -Is) wrote $out ($(du -h "$out" | cut -f1))"
# keep 60 days
find backups -name 'loan_tracker_*.sql.gz.gpg' -mtime +60 -delete

# RESTORE (into an EMPTY database — test this monthly):
#   gpg --batch --passphrase "$BACKUP_PASSPHRASE" -d backups/FILE.sql.gz.gpg | gunzip \
#     | docker compose exec -T db psql -U loan_tracker -d loan_tracker_restore
