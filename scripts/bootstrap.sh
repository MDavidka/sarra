#!/usr/bin/env bash
# Syte bootstrap — clone, install, and start the web GUI
set -euo pipefail

REPO_URL="${SYTE_REPO_URL:-https://github.com/YOUR_ORG/syte.git}"
REPO_DIR="${SYTE_REPO_DIR:-$HOME/syte}"

echo "==> Syte bootstrap"
if [[ -d "$REPO_DIR/.git" ]]; then
  echo "==> Updating existing install at $REPO_DIR"
  git -C "$REPO_DIR" pull --ff-only
else
  echo "==> Cloning Syte to $REPO_DIR"
  git clone "$REPO_URL" "$REPO_DIR"
fi

cd "$REPO_DIR"
chmod +x scripts/*.sh
./scripts/install.sh
exec ./scripts/start.sh
