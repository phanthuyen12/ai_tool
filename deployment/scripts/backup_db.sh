#!/usr/bin/env sh
set -eu

BACKUP_DIR="${BACKUP_DIR:-./backups}"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
mkdir -p "$BACKUP_DIR"

docker compose -f "$(dirname "$0")/../docker-compose.yml" exec -T postgres \
  pg_dump -U "${POSTGRES_USER:-postgres}" "${POSTGRES_DB:-ai_video_studio}" \
  | gzip > "${BACKUP_DIR}/ai_video_studio_${TIMESTAMP}.sql.gz"

# Keep only latest 7 daily backups to protect VPS disk
find "$BACKUP_DIR" -name "ai_video_studio_*.sql.gz" -type f -mtime +7 -delete
echo "Backup saved to ${BACKUP_DIR}/ai_video_studio_${TIMESTAMP}.sql.gz"

