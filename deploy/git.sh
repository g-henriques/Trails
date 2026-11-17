#!/bin/sh
# Run git on this checkout with the NAS setup (deploy key from .env; on QNAP,
# git runs in a throw-away container). Example: deploy/git.sh log --oneline -5
. "$(dirname "$0")/lib.sh"
git "$@"
