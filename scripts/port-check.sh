#!/usr/bin/env bash
# Shared port / process checks for Syte
set -euo pipefail

SYTE_PORT="${SYTE_PORT:-8787}"

port_in_use() {
  ss -tlnp 2>/dev/null | grep -q ":${SYTE_PORT} " || \
  lsof -i ":${SYTE_PORT}" -sTCP:LISTEN &>/dev/null
}

show_port_user() {
  echo "Port ${SYTE_PORT} is in use by:"
  ss -tlnp 2>/dev/null | grep ":${SYTE_PORT} " || true
  lsof -i ":${SYTE_PORT}" -sTCP:LISTEN 2>/dev/null || true
}

syte_systemd_active() {
  systemctl is-active --quiet syte 2>/dev/null
}

free_syte_port() {
  systemctl stop syte 2>/dev/null || true
  pkill -f "uvicorn syte.main:app" 2>/dev/null || true
  sleep 1
  if port_in_use; then
    fuser -k "${SYTE_PORT}/tcp" 2>/dev/null || true
    sleep 1
  fi
}
