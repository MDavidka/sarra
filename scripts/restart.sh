#!/usr/bin/env bash
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=port-check.sh
source "$SYTE_DIR/scripts/port-check.sh"

echo "==> Restarting Syte (systemd)"
"$SYTE_DIR/scripts/stop.sh"
sleep 1
systemctl daemon-reload 2>/dev/null || true
systemctl reset-failed syte 2>/dev/null || true
systemctl start syte
sleep 3

if syte_systemd_active; then
  IP=$(hostname -I 2>/dev/null | awk '{print $1}' || echo "127.0.0.1")
  echo "✓ Syte running — http://${IP}:${SYTE_PORT}"
  systemctl status syte --no-pager -l | head -15
else
  echo "✗ Syte failed to start. Logs:"
  journalctl -u syte -n 30 --no-pager
  exit 1
fi
