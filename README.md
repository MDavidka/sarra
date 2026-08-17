# Syte

**Syte** is a deployment service for Ubuntu servers. It manages app workspaces on a VM, publishes services to a public IP and port, issues TLS certificates for custom domains, and provides a modern web GUI for operations.

## Features

- **Workspace per project** — each deployed app gets an isolated directory on the VM (`/var/lib/syte/workspaces/<id>/`)
- **Public publishing** — apps are exposed on the server's public IP and an assigned port
- **Custom GUI domain** — configure a domain for the Syte web interface in Settings; Syte issues certificates via Caddy (automatic HTTPS)
- **Syte self-update** — pull the newest Syte version from git and restart from Settings (workspace data preserved)
- **Web GUI** — responsive black-and-white interface with Lucide icon navigation
- **Deployment history** — every manual or automated deployment is recorded with status, trigger, duration, and failure detail
- **Health checks** — probe each service's public URL from the dashboard and API
- **Resource controls** — configure per-project Docker memory and CPU limits while retaining server-wide safety defaults
- **Management dashboard** — view live health and recent deployment runs beside each project's lifecycle controls

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
| **Settings** | Public IP, admin email, web GUI domain + certificate, Syte self-update |

### Web GUI Domain & Certificates

1. Point your domain's DNS A record to the server's public IP
2. Open **Settings → Web GUI Domain**
3. Enter the domain (e.g. `syte.yourdomain.com`)
4. Syte configures Caddy and issues a Let's Encrypt certificate automatically

### Update Syte

1. Open **Settings → Update Syte**
2. Click **Update Syte**
3. Syte pulls the latest git version, refreshes dependencies, and restarts. All workspace data on the VM is preserved.

### Update a Deployed Service

1. Open a service from the Dashboard
2. Click **Pull & Restart**
3. Syte runs `git pull`, restarts the app, and keeps data in the workspace `data/` directory

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
| `POST` | `/api/system/update` | Pull newest Syte version & restart |
| `PUT` | `/api/settings` | Save server settings |
| `GET` | `/api/projects/{id}/logs` | View logs |
| `GET` | `/api/projects/{id}/deployments` | View deployment history |
| `GET` | `/api/projects/{id}/health` | Probe the configured public health URL |
| `PUT` | `/api/projects/{id}/deployment-config` | Update deployment type, commands, health checks, env, auto-deploy flag, and Docker resource limits |

### Agent MCP & skills

Per-project MCP providers and skills can be **added, enabled, disabled, and edited**
from the agent chat UI or directly via API (session routes under
`/api/projects/{id}/agent/mcp` and `/agent/skills`, plus token mirrors
`/api/agent_mcp*` and `/api/agent_skills*`). Custom skills can be added with
name + guidance content. See [`docs/api-agent.md`](docs/api-agent.md).

## Platform layer (Coolify parity)

`syte/platform/` adds the PaaS resource model and deployment engine Syte was
missing next to [Coolify](https://github.com/coollabsio/coolify):

- **Build packs** — deploy without writing a Dockerfile. Detects and generates
  for Node, Bun, Deno, Python, Go, Rust, PHP, Ruby, Java, Elixir, .NET and
  static sites, with framework handling for Next.js, Nuxt, Remix, SvelteKit,
  Astro, Django, Laravel, Rails and Phoenix.
- **Managed databases** — PostgreSQL, MySQL, MariaDB, MongoDB, Redis, KeyDB,
  Dragonfly and ClickHouse, with generated credentials, connection URLs,
  readiness probes and logical backup/restore commands.
- **Resource model** — team → server → project → environment → resource
  (application / database / service), plus deployments, PR previews, scoped
  environment variables, persistent volumes, scheduled tasks, backups,
  notification channels and git sources.
- **Git integration** — repository identity matching across URL forms and
  webhook signature verification for GitHub, GitLab, Gitea and Bitbucket.

The existing Syte management layer complements this platform model with
isolated project workspaces, Docker and shell lifecycle actions, streaming logs,
custom domains, previews, deployment history, live health probes, and per-project
Docker CPU/memory limits. The service dashboard exposes the health and recent
deployment state backed by the deployment-history and health APIs.

See [`docs/platform-coolify-parity.md`](docs/platform-coolify-parity.md) for the
design, operational details, and remaining roadmap.

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
