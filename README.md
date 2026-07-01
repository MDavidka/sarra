# Syte

**Syte** is a deployment service for Ubuntu servers. It manages app workspaces on a VM, publishes services to a public IP and port, issues TLS certificates for custom domains, and provides a modern web GUI for operations.

## Features

- **Workspace per project** — each deployed app gets an isolated directory on the VM (`/var/lib/syte/workspaces/<id>/`)
- **Public publishing** — apps are exposed on the server's public IP and an assigned port
- **Custom domains** — configure domains in Settings; Syte issues certificates via Caddy (automatic HTTPS)
- **Git deploy & update** — clone from git on deploy; pull latest and restart from Settings (data in `/data` is preserved)
- **Web GUI** — responsive black-and-white interface for managing services

## Quick Start

Install dependencies and start the web GUI:

```bash
git clone <your-repo-url> syte && cd syte
chmod +x scripts/*.sh
./scripts/install.sh
./scripts/start.sh
```

Open the GUI at **http://\<your-server-ip\>:8787**

### Production (systemd)

```bash
sudo ./scripts/install.sh
sudo systemctl start syte
sudo systemctl status syte
```

## Starter Script

Save and run this one-liner on your Ubuntu server to install and launch the Syte web GUI:

```bash
curl -fsSL https://raw.githubusercontent.com/YOUR_ORG/syte/main/scripts/bootstrap.sh | bash
```

Or manually:

```bash
#!/usr/bin/env bash
# Syte starter — installs and launches the web GUI
set -e
REPO_DIR="${SYTE_REPO_DIR:-$HOME/syte}"
git clone https://github.com/YOUR_ORG/syte.git "$REPO_DIR" 2>/dev/null || (cd "$REPO_DIR" && git pull)
cd "$REPO_DIR"
chmod +x scripts/*.sh
./scripts/install.sh
./scripts/start.sh
```

## Web GUI

| Page | Description |
|------|-------------|
| **Dashboard** | View all deployed services, status, and public URLs |
| **New Service** | Deploy an app from git (or empty workspace) with start command and env vars |
| **Settings** | Public IP, admin email, custom domain + certificate, git pull & restart |

### Custom Domain & Certificates

1. Point your domain's DNS A record to the server's public IP
2. Open **Settings → Custom Domain**
3. Select the service and enter the domain
4. Syte configures Caddy and issues a Let's Encrypt certificate automatically

### Update a Service

1. Open **Settings → Update Service**
2. Select the service and click **Pull & Restart**
3. Syte runs `git pull`, restarts the app, and keeps all data in the workspace `data/` directory

## Workspace Layout

```
/var/lib/syte/
├── syte.db              # Service registry
├── workspaces/
│   └── my-app-a1b2c3/
│       ├── app/         # Git repository
│       ├── data/        # Persistent data (preserved on update)
│       ├── .env         # Environment variables
│       └── app.log      # Application logs
└── pids/                # Process IDs
```

## API

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/projects` | List services |
| `POST` | `/api/projects` | Deploy new service |
| `POST` | `/api/projects/{id}/update` | Git pull & restart |
| `POST` | `/api/projects/{id}/domain` | Set domain & issue cert |
| `PUT` | `/api/settings` | Save server settings |
| `GET` | `/api/projects/{id}/logs` | View logs |

## Configuration

Environment variables (prefix `SYTE_`):

| Variable | Default | Description |
|----------|---------|-------------|
| `SYTE_DATA_DIR` | `/var/lib/syte` | Data root |
| `SYTE_HOST` | `0.0.0.0` | Bind address |
| `SYTE_PORT` | `8787` | GUI port |
| `SYTE_PUBLIC_IP` | auto-detect | Public IP override |

## Requirements

- Ubuntu 20.04+ (or Debian-based Linux)
- Python 3.10+
- Git
- Caddy (optional, for HTTPS custom domains)

## License

MIT
