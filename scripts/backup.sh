#!/usr/bin/env bash
# On-disk Postgres backup with rotation. Meant to run from cron every 30 min.
#
#   crontab -e  ->  */30 * * * * /home/USER/andrejsala/scripts/backup.sh
#
# Writes gzipped dumps to ./backups and keeps the newest KEEP files.
set -euo pipefail

# cron runs with a bare PATH — prepend the usual docker locations (Linux VM +
# macOS/Homebrew) while keeping whatever PATH the caller already has.
export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/homebrew/bin:${PATH:-}"

cd "$(dirname "$0")/.."          # repo root (where docker-compose.yml + .env live)
mkdir -p backups

KEEP=10                          # keep only the newest 10 dumps

# read just the two names we need from .env (avoids sourcing the whole file)
DB_USER="$(grep -E '^POSTGRES_USER=' .env | cut -d= -f2-)"
DB_NAME="$(grep -E '^POSTGRES_DB=' .env | cut -d= -f2-)"

TS="$(date +%Y%m%d-%H%M%S)"
OUT="backups/andrejsala-${TS}.sql.gz"

COMPOSE="docker compose -f docker-compose.yml -f docker-compose.prod.yml"

# dump via the db container's local socket (trusted), gzip on the host
$COMPOSE exec -T db pg_dump -U "$DB_USER" "$DB_NAME" | gzip > "$OUT"

# fail loudly if the dump came out empty/broken, and don't keep a bad file
if [ ! -s "$OUT" ] || [ "$(gzip -t "$OUT" 2>&1)" != "" ]; then
  echo "$(date '+%F %T') BACKUP FAILED (empty/corrupt): $OUT" >&2
  rm -f "$OUT"
  exit 1
fi

# rotation: delete everything older than the newest KEEP
ls -1t backups/andrejsala-*.sql.gz 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f

echo "$(date '+%F %T') OK -> $OUT ($(du -h "$OUT" | cut -f1))"
