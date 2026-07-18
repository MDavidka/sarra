#!/usr/bin/env bash
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
VENV_DIR="${SYTE_DIR}/.venv"
HOST="${SYTE_HOST:-0.0.0.0}"
PORT="${SYTE_PORT:-8787}"

# shellcheck source=port-check.sh
source "$SYTE_DIR/scripts/port-check.sh"

mkdir -p "$DATA_DIR/workspaces" "$DATA_DIR/pids"

export SYTE_DATA_DIR="$DATA_DIR"
export SYTE_WORKSPACES_DIR="$DATA_DIR/workspaces"
export SYTE_DB_PATH="$DATA_DIR/syte.db"

if syte_systemd_active; then
  IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")
  echo ""
  echo "  Syte is already running via systemd."
  echo "  Web GUI:  http://${IP}:${PORT}"
  echo ""
  echo "  Use:  sudo ./scripts/restart.sh   (restart)"
  echo "        sudo systemctl status syte  (status)"
  exit 0
fi

if port_in_use; then
  echo ""
  echo "  ERROR: Port ${PORT} is already in use."
  show_port_user
  echo ""
  echo "  Fix:  sudo ./scripts/stop.sh"
  echo "        sudo ./scripts/restart.sh"
  echo ""
  echo "  Do NOT run start.sh if systemd syte is enabled — use systemctl only."
  exit 1
fi

if [[ ! -d "$VENV_DIR" ]] && ! command -v uvicorn &>/dev/null; then
  echo "Virtual environment not found. Run ./scripts/install.sh first."
  exit 1
fi

PUBLIC_IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "localhost")

echo ""
echo "  ◆ Syte — Deployment Service"
echo "  ────────────────────────────"
echo "  Web GUI:  http://${PUBLIC_IP}:${PORT}"
echo "  Local:    http://127.0.0.1:${PORT}"
echo ""

if [[ -x "$VENV_DIR/bin/uvicorn" ]]; then
  UVICORN="$VENV_DIR/bin/uvicorn"
elif [[ -x "$HOME/.local/bin/uvicorn" ]]; then
  UVICORN="$HOME/.local/bin/uvicorn"
else
  UVICORN="python3 -m uvicorn"
fi

exec $UVICORN syte.main:app \
  --host "$HOST" \
  --port "$PORT" \
  --app-dir "$SYTE_DIR"
