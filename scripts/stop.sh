#!/usr/bin/env bash
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=port-check.sh
source "$SYTE_DIR/scripts/port-check.sh"

echo "==> Stopping Syte"
free_syte_port

if port_in_use; then
  echo "WARNING: port ${SYTE_PORT} still in use:"
  show_port_user
  exit 1
fi

echo "✓ Port ${SYTE_PORT} is free."
