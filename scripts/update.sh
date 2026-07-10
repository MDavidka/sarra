#!/usr/bin/env bash
# Pull latest Syte code, reinstall if needed, and restart.
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$SYTE_DIR"

VENV_DIR="${SYTE_DIR}/.venv"
PYTHON="${VENV_DIR}/bin/python"
PIP="${VENV_DIR}/bin/pip"

if [[ ! -x "$PYTHON" ]]; then
  echo "==> Creating Python virtualenv"
  python3 -m venv "$VENV_DIR"
fi

echo "==> Installing Python dependencies"
"$PIP" install --upgrade pip -q
"$PIP" install -r "${SYTE_DIR}/requirements.txt" -q

export PYTHONPATH="${SYTE_DIR}"

echo "==> Resolving update source"
"$PYTHON" - <<'PY'
from pathlib import Path
import sys

from syte.self_update import _git_sync_update_target
from syte.update_source import resolve_update_target

target = resolve_update_target(Path.cwd())
print(f"==> Update source: {target.label}")
print(f"==> Branch/ref: {target.branch}")
ok, message, _checkout_ref = _git_sync_update_target(target)
print(message)
sys.exit(0 if ok else 1)
PY

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
