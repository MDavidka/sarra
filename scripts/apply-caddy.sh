#!/usr/bin/env bash
# Regenerate Caddy config from Syte DB and reload Caddy (domain-only, no :port listeners).
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
VENV_DIR="${SYTE_DIR}/.venv"

export SYTE_DATA_DIR="$DATA_DIR"
export SYTE_WORKSPACES_DIR="$DATA_DIR/workspaces"
export SYTE_DB_PATH="$DATA_DIR/syte.db"

PYTHON=""
if [[ -x "$VENV_DIR/bin/python" ]]; then
  PYTHON="$VENV_DIR/bin/python"
elif command -v python3 &>/dev/null; then
  PYTHON="python3"
else
  echo "WARNING: Python not found — skipping Caddy config update"
  exit 0
fi

"$PYTHON" - <<PY
import asyncio
import sys

sys.path.insert(0, "${SYTE_DIR}")
from syte.certificates import apply_proxy_config

ok, msg = asyncio.run(apply_proxy_config())
print(msg)
if not ok:
    sys.exit(1)
PY
