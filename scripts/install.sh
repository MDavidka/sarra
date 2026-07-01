#!/usr/bin/env bash
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
VENV_DIR="${SYTE_DIR}/.venv"

echo "==> Installing Syte deployment service"

if [[ $EUID -ne 0 ]]; then
  echo "Run with sudo for system-wide install: sudo ./scripts/install.sh"
  INSTALL_SYSTEM=false
else
  INSTALL_SYSTEM=true
fi

# System packages (requires root)
if [[ "$INSTALL_SYSTEM" == true ]] && command -v apt-get &>/dev/null; then
  echo "==> Installing system dependencies"
  apt-get update -qq
  apt-get install -y -qq python3 python3-pip python3-venv git curl nodejs npm

  if ! command -v docker &>/dev/null; then
    echo "==> Installing Docker (for Dockerfile deployments)"
    apt-get install -y -qq docker.io 2>/dev/null || echo "Docker install skipped — install manually for Dockerfile deploys"
  fi

  if ! command -v npm &>/dev/null; then
    echo "==> Installing Node.js + npm"
    apt-get install -y -qq nodejs npm 2>/dev/null || {
      curl -fsSL https://deb.nodesource.com/setup_20.x | bash - 2>/dev/null || true
      apt-get install -y -qq nodejs 2>/dev/null || echo "Node.js install skipped"
    }
  fi

  if ! command -v caddy &>/dev/null; then
    echo "==> Installing Caddy (reverse proxy + auto TLS)"
    apt-get install -y -qq debian-keyring debian-archive-keyring apt-transport-https curl 2>/dev/null || true
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg 2>/dev/null || true
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | tee /etc/apt/sources.list.d/caddy-stable.list 2>/dev/null || true
    apt-get update -qq && apt-get install -y -qq caddy 2>/dev/null || echo "Caddy install skipped — install manually for HTTPS"
  fi
fi

# Python venv
echo "==> Setting up Python environment"
if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
  echo "    venv unavailable — installing with pip --user"
  pip3 install --user -r "$SYTE_DIR/requirements.txt" -q
  cat > "$SYTE_DIR/scripts/start.sh.local" << 'WRAPPER'
#!/usr/bin/env bash
SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
export SYTE_DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
mkdir -p "$SYTE_DATA_DIR/workspaces" "$SYTE_DATA_DIR/pids"
exec python3 -m uvicorn syte.main:app --host "${SYTE_HOST:-0.0.0.0}" --port "${SYTE_PORT:-8787}" --app-dir "$SYTE_DIR"
WRAPPER
  chmod +x "$SYTE_DIR/scripts/start.sh.local"
  echo "    Use ./scripts/start.sh.local to start"
else
  "$VENV_DIR/bin/pip" install --upgrade pip -q
  "$VENV_DIR/bin/pip" install -r "$SYTE_DIR/requirements.txt" -q
fi

# Brand icon
ICON="$SYTE_DIR/syte/static/icon.png"
if [[ ! -f "$ICON" ]]; then
  echo "==> Downloading brand icon"
  curl -fsSL "https://i.ibb.co/HM3PGdS/IMG-0615.png" -o "$ICON" 2>/dev/null || true
fi

# Data directories
echo "==> Creating data directories"
mkdir -p "$DATA_DIR/workspaces" "$DATA_DIR/pids"
chmod 755 "$DATA_DIR"

# Systemd service
if [[ "$INSTALL_SYSTEM" == true ]]; then
  echo "==> Installing systemd services"
  sed "s|__SYTE_DIR__|${SYTE_DIR}|g; s|__DATA_DIR__|${DATA_DIR}|g" \
    "$SYTE_DIR/systemd/syte.service" > /etc/systemd/system/syte.service
  systemctl daemon-reload
  systemctl enable syte
  systemctl enable caddy 2>/dev/null || true
  chmod +x "$SYTE_DIR/scripts/"*.sh
  "$SYTE_DIR/scripts/stop.sh" 2>/dev/null || true
  "$SYTE_DIR/scripts/apply-caddy.sh" 2>/dev/null || true
  systemctl start caddy 2>/dev/null || true
  systemctl start syte 2>/dev/null || true
  echo "    Services enabled: syte, caddy (24/7)"
  echo "    Manage with: sudo ./scripts/restart.sh"
fi

echo ""
echo "✓ Syte installed."
echo "  Start the web GUI:  sudo ./scripts/restart.sh"
if [[ "$INSTALL_SYSTEM" == true ]]; then
  echo "  Or:                 sudo systemctl start syte"
  echo "  Do NOT also run ./scripts/start.sh — only one instance on port 8787"
fi
