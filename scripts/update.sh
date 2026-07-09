#!/usr/bin/env bash
# Pull latest Syte code, reinstall if needed, and restart.
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SYTE_DIR"

BRANCH="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo main)"
echo "==> Updating Syte on branch: ${BRANCH}"

git fetch origin
git pull --ff-only origin "${BRANCH}" || git pull --ff-only

if [[ $EUID -eq 0 ]]; then
  "$SYTE_DIR/scripts/install.sh"
  "$SYTE_DIR/scripts/restart.sh"
else
  sudo "$SYTE_DIR/scripts/install.sh"
  sudo "$SYTE_DIR/scripts/restart.sh"
fi

echo ""
echo "==> Health check"
curl -fsS "http://127.0.0.1:${SYTE_PORT:-8787}/api/health" || true
echo ""
