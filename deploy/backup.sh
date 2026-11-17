#!/bin/sh
# Consistent backup of the app data to $NAS_BACKUP_DIR, keeping the newest $NAS_BACKUP_KEEP.
# Exits non-zero and keeps older backups when anything fails, so nas-update.sh never
# updates without a backup and a failing night cannot rotate good backups away.
#   /share/Container/<app>/deploy/backup.sh
. "$(dirname "$0")/lib.sh"

case "$DATA_KIND" in
  none) log "No data to back up"; exit 0 ;;
  sqlite) EXT=db.gz ;;
  postgres) EXT=sql.gz ;;
  *) log "Unknown DATA_KIND=$DATA_KIND"; exit 1 ;;
esac
mkdir -p "$BACKUP_DIR"
FILE="$BACKUP_DIR/$APP_NAME-$(date '+%Y%m%d-%H%M%S').$EXT"
TMP="$FILE.tmp"
trap 'rm -f "$TMP" "$TMP.gz"' EXIT

case "$DATA_KIND" in
  sqlite)
    if [ ! -f "$DATA_DIR/$(basename "$SQLITE_PATH")" ]; then
      log "No database yet; nothing to back up"
      exit 0
    fi
    # Online backup API: safe while the app writes. Written next to the database
    # (often the only writable path), streamed out, then removed.
    PY="
import os, sqlite3, sys
db = '$SQLITE_PATH'; tmp = os.path.join(os.path.dirname(db), '.backup.db')
if os.path.exists(tmp): os.remove(tmp)
src = sqlite3.connect(db); dst = sqlite3.connect(tmp); src.backup(dst); src.close()
ok = dst.execute('PRAGMA integrity_check').fetchone()[0]; dst.close()
if ok != 'ok': os.remove(tmp); sys.exit('integrity check failed: ' + ok)
sys.stdout.buffer.write(open(tmp, 'rb').read()); os.remove(tmp)
"
    # Prefer the running container: a fresh `run` may need an image that is not pulled yet.
    if running "$APP_SERVICE"; then RUN="exec -T"; else RUN="run --rm --no-deps -T"; fi
    # project.conf may define sqlite_backup (for images without python3); it gets the
    # compose arguments ("exec -T" or "run ...") and must write the database to stdout.
    if command -v sqlite_backup >/dev/null 2>&1; then
      CMD="sqlite_backup"
    else
      CMD="default_sqlite_backup"
    fi
    # shellcheck disable=SC2086 # $RUN is a list of compose arguments
    default_sqlite_backup() { compose $1 "$APP_SERVICE" python3 -c "$PY"; }
    if ! "$CMD" "$RUN" > "$TMP"; then
      log "Backup FAILED: SQLite backup returned an error; older backups kept"
      exit 1
    fi
    ;;
  postgres)
    if ! running "$DB_SERVICE"; then
      log "Database container is not running; skipping backup"
      exit 0
    fi
    if ! compose exec -T "$DB_SERVICE" pg_dump -U "${POSTGRES_USER:-$APP_NAME}" -d "${POSTGRES_DB:-$APP_NAME}" --clean --if-exists > "$TMP"; then
      log "Backup FAILED: pg_dump returned an error; older backups kept"
      exit 1
    fi
    ;;
esac
gzip -c "$TMP" > "$TMP.gz"
if ! check_backup "$TMP.gz"; then
  log "Backup FAILED: the backup is incomplete; older backups kept"
  exit 1
fi
mv "$TMP.gz" "$FILE"
log "Backup done: $FILE ($(du -h "$FILE" | cut -f1))"

KEEP="${NAS_BACKUP_KEEP:-14}"
ls -1t "$BACKUP_DIR/$APP_NAME"-*."$EXT" 2>/dev/null | tail -n +"$((KEEP + 1))" | while read -r old; do
  rm -f "$old"
done
