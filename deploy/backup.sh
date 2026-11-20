#!/usr/bin/env sh
# Nightly encrypted backup (Linux server). Cron example, 02:30 every night:
#   30 2 * * * cd /opt/loan_tracker && ./deploy/backup.sh >> backups/backup.log 2>&1
# Needs BACKUP_PASSPHRASE in the environment or in .env (deploy/server.sh install sets it). Copy backups/ offsite
# (rclone, rsync to another machine) — a backup on the same disk is not a backup.
set -eu
cd "$(dirname "$0")/.."
# read only this one value: sourcing all of .env breaks on values with spaces
if [ -z "${BACKUP_PASSPHRASE:-}" ] && [ -f .env ]; then
  BACKUP_PASSPHRASE=$(sed -n 's/^BACKUP_PASSPHRASE=//p' .env | tail -1)
fi
: "${BACKUP_PASSPHRASE:?set BACKUP_PASSPHRASE}"
mkdir -p backups
stamp=$(date +%Y-%m-%d_%H%M)
out="backups/loan_tracker_${stamp}.sql.gz.gpg"
docker compose exec -T db pg_dump -U loan_tracker -d loan_tracker --no-owner \
  | gzip -9 \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase "$BACKUP_PASSPHRASE" -o "$out"
echo "$(date -Is) wrote $out ($(du -h "$out" | cut -f1))"
# documents (already AES-encrypted; wrapped again so names and sizes are hidden too)
docs="backups/loan_tracker_docs_${stamp}.tar.gz.gpg"
docker compose exec -T app tar -C /data -cf - documents \
  | gzip -9 \
  | gpg --batch --yes --symmetric --cipher-algo AES256 --passphrase "$BACKUP_PASSPHRASE" -o "$docs"
echo "$(date -Is) wrote $docs ($(du -h "$docs" | cut -f1))"
# keep 60 days
find backups -name 'loan_tracker_*.gpg' -mtime +60 -delete
# NOT in any backup: DOCUMENT_KEY. Keep it in your password manager; without it
# the document files cannot be opened.

# RESTORE (into an EMPTY database — test this monthly):
#   gpg --batch --passphrase "$BACKUP_PASSPHRASE" -d backups/FILE.sql.gz.gpg | gunzip \
#     | docker compose exec -T db psql -U loan_tracker -d loan_tracker_restore
#   gpg --batch --passphrase "$BACKUP_PASSPHRASE" -d backups/loan_tracker_docs_FILE.tar.gz.gpg | gunzip \
#     | docker compose exec -T app tar -C /data -xf -
