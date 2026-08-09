#!/usr/bin/env bash
set -euo pipefail

SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
VENV_DIR="${SYTE_DIR}/.venv"

if [[ $EUID -eq 0 ]]; then
  INSTALL_LOG="${SYTE_INSTALL_LOG:-${DATA_DIR}/install.log}"
  mkdir -p "$(dirname "$INSTALL_LOG")"
else
  INSTALL_LOG="${SYTE_INSTALL_LOG:-/tmp/syte-install.log}"
fi
# Keep credentials and package-manager output readable only by the installer user.
touch "$INSTALL_LOG"
chmod 600 "$INSTALL_LOG" 2>/dev/null || true
# Keep a complete install transcript: package managers, Docker, Caddy, and
# systemd output are the useful evidence when the Router tab cannot start.
exec > >(tee -a "$INSTALL_LOG") 2>&1
trap 'status=$?; if [[ $status -ne 0 ]]; then echo "[ERROR] install.sh failed at line ${LINENO} (exit ${status}); full log: ${INSTALL_LOG}"; fi' EXIT
if [[ "${SYTE_INSTALL_DEBUG:-0}" == "1" ]]; then
  set -x
fi

echo "==> Installing Syte deployment service"
echo "    Installer log: ${INSTALL_LOG}"

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
  apt-get install -y -qq python3 python3-pip python3-venv python3.12 python3.12-venv git curl nodejs npm

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

  # Headless Chromium for agent screenshot_preview (desktop + phone viewports).
  if ! command -v chromium &>/dev/null \
    && ! command -v chromium-browser &>/dev/null \
    && ! command -v google-chrome &>/dev/null \
    && ! command -v google-chrome-stable &>/dev/null; then
    echo "==> Installing Chromium (agent preview screenshots)"
    apt-get install -y -qq chromium-browser 2>/dev/null \
      || apt-get install -y -qq chromium 2>/dev/null \
      || echo "Chromium install skipped — screenshots need chromium; set SYTE_CHROMIUM_PATH or apt install chromium"
  fi
elif [[ "$INSTALL_SYSTEM" == true ]] && command -v dnf &>/dev/null; then
  if [[ -r /etc/os-release ]]; then
    # shellcheck disable=SC1091
    . /etc/os-release
  fi
  if [[ "${ID:-}" != "almalinux" ]]; then
    echo "ERROR: automatic DNF host setup supports AlmaLinux only (detected ${PRETTY_NAME:-unknown})."
    exit 1
  fi
  echo "==> Safely migrating the AlmaLinux host to Docker CE"
  chmod +x "$SYTE_DIR/scripts/recover-docker-almalinux.sh"
  "$SYTE_DIR/scripts/recover-docker-almalinux.sh"

  echo "==> Installing AlmaLinux application dependencies"
  dnf -y install dnf-plugins-core curl git firewalld python3.12 python3.12-pip python3.12-devel nodejs npm

  if ! command -v caddy &>/dev/null; then
    echo "==> Installing Caddy (reverse proxy + automatic HTTPS)"
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/setup.rpm.sh' | bash
    dnf -y install caddy
  fi

  echo "==> Enabling AlmaLinux services required by the Router tab"
  systemctl enable --now docker firewalld
  firewall-cmd --permanent --add-service=http
  firewall-cmd --permanent --add-service=https
  firewall-cmd --reload
fi

# Python venv
echo "==> Setting up Python environment"
PYTHON_BIN="${SYTE_PYTHON_BIN:-python3}"
if ! "$PYTHON_BIN" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
  if command -v python3.12 >/dev/null 2>&1; then
    PYTHON_BIN="python3.12"
  else
    echo "Python 3.12 or newer is required for the OpenHands Agent Server."
    echo "Install Python 3.12 or set SYTE_PYTHON_BIN to a compatible interpreter."
    exit 1
  fi
fi
if ! "$PYTHON_BIN" -m venv "$VENV_DIR" 2>/dev/null; then
  echo "    venv unavailable — installing with pip --user"
  "$PYTHON_BIN" -m pip install --user -r "$SYTE_DIR/requirements.txt" -q
  cat > "$SYTE_DIR/scripts/start.sh.local" << 'WRAPPER'
#!/usr/bin/env bash
SYTE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export PATH="$HOME/.local/bin:$PATH"
export SYTE_DATA_DIR="${SYTE_DATA_DIR:-/var/lib/syte}"
mkdir -p "$SYTE_DATA_DIR/workspaces" "$SYTE_DATA_DIR/pids"
PYTHON_BIN="${SYTE_PYTHON_BIN:-python3.12}"
exec "$PYTHON_BIN" -m uvicorn syte.main:app --host "${SYTE_HOST:-0.0.0.0}" --port "${SYTE_PORT:-8787}" --app-dir "$SYTE_DIR"
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
  systemctl enable caddy 2>/dev/null || echo "WARN: Caddy could not be enabled; HTTPS may not work."
  chmod +x "$SYTE_DIR/scripts/"*.sh
  "$SYTE_DIR/scripts/stop.sh" 2>/dev/null || true
  "$SYTE_DIR/scripts/apply-caddy.sh" || echo "WARN: initial Caddy configuration failed; inspect the installer log."
  if ! systemctl start caddy; then
    echo "WARN: Caddy failed to start — recent diagnostics:"
    systemctl status caddy --no-pager || true
    journalctl -u caddy -n 80 --no-pager || true
  fi
  if ! systemctl start syte; then
    echo "ERROR: Syte failed to start — recent diagnostics:"
    systemctl status syte --no-pager || true
    journalctl -u syte -n 120 --no-pager || true
    exit 1
  fi
  echo "    Services enabled: syte, caddy (24/7)"
  echo "    Manage with: sudo ./scripts/restart.sh"
  if ! curl -fsS "http://127.0.0.1:${SYTE_PORT:-8787}/api/health"; then
    echo "WARN: Syte health endpoint did not answer; recent service diagnostics:"
    systemctl status syte --no-pager || true
    journalctl -u syte -n 120 --no-pager || true
  else
    echo ""
  fi
fi

echo ""
echo "✓ Syte installed."
echo "  Start the web GUI:  sudo ./scripts/restart.sh"
if [[ "$INSTALL_SYSTEM" == true ]]; then
  echo "  Or:                 sudo systemctl start syte"
  echo "  Do NOT also run ./scripts/start.sh — only one instance on port 8787"
fi
