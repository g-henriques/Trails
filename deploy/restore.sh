#!/bin/sh
# Replace the app data with a backup made by deploy/backup.sh.
#   deploy/restore.sh backups/<app>-YYYYMMDD-HHMMSS.<db|sql>.gz [-y]
. "$(dirname "$0")/lib.sh"

FILE="${1:-}"
if [ -z "$FILE" ] || [ ! -f "$FILE" ]; then
  echo "usage: $0 <backup file> [-y]" >&2
  exit 2
fi
# Check before stopping anything: an empty or truncated backup would restore nothing.
check_backup "$FILE" || { echo "$FILE is not a complete backup made by deploy/backup.sh" >&2; exit 1; }
if [ "${2:-}" != "-y" ]; then
  printf 'Replace the current %s data with %s? [y/N] ' "$APP_NAME" "$FILE"
  read -r answer
  case "$answer" in y | Y | yes | s | S | sim) ;; *) echo "Cancelled"; exit 1 ;; esac
fi

compose stop "$APP_SERVICE"
case "$DATA_KIND" in
  sqlite)
    ensure_data_dir
    DB="$DATA_DIR/$(basename "$SQLITE_PATH")"
    if [ -f "$DB" ]; then
      mv "$DB" "$DB.before-restore-$(date '+%Y%m%d-%H%M%S')"
      log "Previous database kept as $DB.before-restore-*"
    fi
    rm -f "$DB-journal" "$DB-wal" "$DB-shm"
    gzip -dc "$FILE" > "$DB"
    if [ "$(id -u)" = 0 ]; then chown "$CONTAINER_UID:$CONTAINER_UID" "$DB"; fi
    ;;
  postgres)
    gzip -dc "$FILE" | compose exec -T "$DB_SERVICE" psql -v ON_ERROR_STOP=1 -U "${POSTGRES_USER:-$APP_NAME}" -d "${POSTGRES_DB:-$APP_NAME}" > /dev/null
    ;;
esac
compose up -d "$APP_SERVICE"
log "Restored $FILE"
