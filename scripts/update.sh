#!/usr/bin/env bash
# Pull latest Syte code, reinstall if needed, and restart.
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SYTE_DIR"

UPDATE_BRANCH="${SYTE_UPDATE_BRANCH:-main}"
echo "==> Updating Syte on branch: ${UPDATE_BRANCH}"

git fetch origin "${UPDATE_BRANCH}"

if git status --porcelain | grep -q .; then
  echo "==> Stashing local changes"
  git stash push -u -m "syte-update-autostash" || true
fi

CURRENT="$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo HEAD)"
if [[ "${CURRENT}" != "${UPDATE_BRANCH}" ]]; then
  echo "==> Checking out ${UPDATE_BRANCH}"
  git checkout "${UPDATE_BRANCH}" 2>/dev/null || git checkout -B "${UPDATE_BRANCH}" "origin/${UPDATE_BRANCH}"
fi

if ! git pull --ff-only origin "${UPDATE_BRANCH}"; then
  echo "==> Fast-forward failed — resetting to origin/${UPDATE_BRANCH}"
  git reset --hard "origin/${UPDATE_BRANCH}"
fi

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
