#!/bin/sh
# Pull the latest version from GitHub and redeploy if something changed. Also does
# the first start after cloning (--force). Run by cron or by hand:
#   /share/Container/<app>/deploy/nas-update.sh [--force]
# Steps: fetch → backup → fast-forward → build/pull → restart → wait for HEALTH_PATH.
. "$(dirname "$0")/lib.sh"

BRANCH="${NAS_BRANCH:-main}"
git fetch --quiet origin "$BRANCH"
LOCAL="$(git rev-parse HEAD)"
REMOTE="$(git rev-parse "origin/$BRANCH")"
if [ "$LOCAL" = "$REMOTE" ] && [ "${1:-}" != "--force" ]; then
  log "Already at $(git rev-parse --short HEAD); nothing to do"
  exit 0
fi

log "Updating $(git rev-parse --short "$LOCAL") → $(git rev-parse --short "$REMOTE")"
"$ROOT/deploy/backup.sh"
git merge --ff-only --quiet "origin/$BRANCH"
GIT_SHA="$(git rev-parse --short HEAD)"
export GIT_SHA

IMAGE="${NAS_IMAGE:-$LOCAL_IMAGE}"
if [ "$IMAGE" = "$LOCAL_IMAGE" ]; then
  compose build "$APP_SERVICE"
else
  # Separate lines: with `a && b`, set -e would not stop on a failed login.
  ghcr_login
  compose pull "$APP_SERVICE"
fi
ensure_data_dir
compose up -d --remove-orphans

PORT="${NAS_PORT:-$DEFAULT_PORT}"
HOST_IP="${NAS_BIND_IP:-0.0.0.0}"
[ "$HOST_IP" = "0.0.0.0" ] && HOST_IP=127.0.0.1
URL="http://$HOST_IP:$PORT$HEALTH_PATH"
i=0
until wget -qO- "$URL" >/dev/null 2>&1 || curl -fsS "$URL" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -ge 60 ]; then
    log "$APP_NAME did not become healthy; recent logs:"
    compose logs --tail 50 "$APP_SERVICE"
    exit 1
  fi
  sleep 2
done
log "$APP_NAME $GIT_SHA is up: http://$HOST_IP:$PORT"
docker image prune -f >/dev/null 2>&1 || true
