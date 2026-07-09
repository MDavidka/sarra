#!/usr/bin/env bash
# Shared port / process checks for Syte
set -euo pipefail

SYTE_PORT="${SYTE_PORT:-8787}"

port_in_use() {
  ss -tlnp 2>/dev/null | grep -q ":${SYTE_PORT} " || \
  lsof -i ":${SYTE_PORT}" -sTCP:LISTEN &>/dev/null
}

port_listener_pid() {
  ss -tlnp 2>/dev/null | grep ":${SYTE_PORT} " | sed -n 's/.*pid=\([0-9]*\).*/\1/p' | head -1
}

show_port_user() {
  echo "Port ${SYTE_PORT} is in use by:"
  ss -tlnp 2>/dev/null | grep ":${SYTE_PORT} " || true
  lsof -i ":${SYTE_PORT}" -sTCP:LISTEN 2>/dev/null || true
}

syte_systemd_active() {
  systemctl is-active --quiet syte 2>/dev/null
}

kill_port_listener() {
  local pid
  pid=$(port_listener_pid)
  if [[ -n "$pid" ]]; then
    echo "    Stopping process on port ${SYTE_PORT} (pid ${pid})"
    kill "$pid" 2>/dev/null || true
    sleep 2
    if kill -0 "$pid" 2>/dev/null; then
      kill -9 "$pid" 2>/dev/null || true
      sleep 1
    fi
  fi
}

free_syte_port() {
  if syte_systemd_active; then
    systemctl stop syte 2>/dev/null || true
    sleep 2
  fi
  kill_port_listener
}
