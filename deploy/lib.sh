#!/bin/sh
# Shared helpers for the NAS scripts (POSIX sh; works with QNAP/BusyBox).
# Generated from the qnap-deploy skill (g-henriques/claude-skills); project values in deploy/project.conf.
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
. "$ROOT/deploy/project.conf"

# Read only the NAS settings from .env. The file is not sourced: other values
# (passwords, user agents, API keys) may contain spaces or shell characters.
if [ -f .env ]; then
  while IFS= read -r line || [ -n "$line" ]; do
    line="$(printf '%s' "$line" | tr -d '\r')"
    case "$line" in
      NAS_*=* | TZ=* | POSTGRES_USER=* | POSTGRES_DB=*)
        key="${line%%=*}"
        value="$(printf '%s' "${line#*=}" | sed -e 's/[[:space:]]#.*$//' -e 's/[[:space:]]*$//' -e "s/^[\"']//" -e "s/[\"']\$//")"
        export "$key=$value"
        ;;
    esac
  done < .env
fi

# QNAP: cron runs with a minimal PATH, so add Container Station's binaries.
if command -v getcfg >/dev/null 2>&1; then
  CS_PATH="$(getcfg container-station Install_Path -f /etc/config/qpkg.conf 2>/dev/null || true)"
  [ -n "$CS_PATH" ] && PATH="$CS_PATH/bin:$CS_PATH/usr/bin:$PATH"
fi
PATH="$PATH:/usr/local/bin:/opt/bin"
export PATH

if docker compose version >/dev/null 2>&1; then
  compose() { docker compose "$@"; }
elif command -v docker-compose >/dev/null 2>&1; then
  compose() { docker-compose "$@"; }
else
  echo "docker compose not found (is Container Station running?)" >&2
  exit 1
fi

# Private repository: read-only deploy key kept outside /root (QNAP wipes /root on reboot).
SSH_KEY="${NAS_SSH_KEY:-}"
if [ -n "$SSH_KEY" ] && [ -f "$SSH_KEY" ]; then
  SSH_DIR="$(dirname "$SSH_KEY")"
  export GIT_SSH_COMMAND="ssh -i $SSH_KEY -o IdentitiesOnly=yes -o UserKnownHostsFile=$SSH_DIR/known_hosts -o StrictHostKeyChecking=accept-new"
fi

# QNAP has no git by default: fall back to a throw-away git container.
if ! command -v git >/dev/null 2>&1; then
  git() {
    if [ -n "${SSH_DIR:-}" ]; then
      docker run --rm -v "$ROOT:$ROOT" -w "$ROOT" -v "$SSH_DIR:$SSH_DIR" -e GIT_SSH_COMMAND \
        "${NAS_GIT_IMAGE:-alpine/git:latest}" -c safe.directory='*' "$@"
    else
      docker run --rm -v "$ROOT:$ROOT" -w "$ROOT" "${NAS_GIT_IMAGE:-alpine/git:latest}" -c safe.directory='*' "$@"
    fi
  }
fi

DATA_DIR="${NAS_DATA_DIR:-./data}"
BACKUP_DIR="${NAS_BACKUP_DIR:-./backups}"

log() { printf '%s %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; }

running() { [ -n "$(compose ps -q --status running "$1" 2>/dev/null)" ]; }

# The container user must own the data folder (bind mounts are created as root).
ensure_data_dir() {
  [ "$DATA_KIND" = sqlite ] || return 0
  mkdir -p "$DATA_DIR"
  if [ "$(id -u)" = 0 ]; then
    chown "$CONTAINER_UID:$CONTAINER_UID" "$DATA_DIR"
  fi
}

# A backup is valid only if it is complete; never trust the exit code of a pipeline (sh has no pipefail).
check_backup() {
  gzip -t "$1" 2>/dev/null || return 1
  case "$DATA_KIND" in
    sqlite) [ "$(gzip -dc "$1" | head -c 15)" = "SQLite format 3" ] ;;
    postgres) gzip -dc "$1" | tail -n 20 | grep -q 'PostgreSQL database dump complete' ;;
    *) return 1 ;;
  esac
}

# Private GHCR image: log in before pulling. The token lives in a shared folder, because
# QNAP wipes /root (and the login docker keeps in /root/.docker) on every reboot.
ghcr_login() {
  TOKEN_FILE="${NAS_GHCR_TOKEN_FILE:-}"
  [ -n "$TOKEN_FILE" ] || return 0
  if [ ! -f "$TOKEN_FILE" ]; then
    log "NAS_GHCR_TOKEN_FILE=$TOKEN_FILE does not exist"
    return 1
  fi
  docker login ghcr.io -u "${NAS_GHCR_USER:-g-henriques}" --password-stdin < "$TOKEN_FILE" > /dev/null
}
