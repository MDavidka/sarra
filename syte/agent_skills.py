"""Default Continue agent skills, rules, and workspace helpers for Syte projects."""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from syte.config import settings

SKILL_FILES: dict[str, str] = {
    "website-editing.md": """# Website editing

You are editing a live website project in the Syte workspace.

- Application source lives under `app/` (relative to the agent cwd).
- Make focused, minimal changes that match the existing stack and style.
- Prefer editing existing files over creating new ones unless necessary.
- After file changes, mention whether preview hot-reload should pick them up.
- Do not run production builds (`npm run build`, `next build`) — use preview instead.
""",
    "workspace-search.md": """# Workspace search

Use built-in tools to explore the codebase before editing:

- **Search** filenames and content with grep/ripgrep (`rg`, `grep -r`).
- **Read** files before rewriting them.
- **List** directories with `ls` when unsure of structure.

Always search first when the user asks to find or change something across the site.
""",
    "preview-access.md": """# Preview access (Syte)

The dev preview may already be running. Use the `syte-access` helper (on PATH when the agent starts):

```bash
syte-access status          # preview URL, running state, ports
syte-access url             # print preview URL only
syte-access fetch [url]     # fetch HTML/text (defaults to preview URL)
syte-access read [url]      # alias for fetch — read page content
syte-access logs [lines]    # tail preview dev-server log (default 200 lines)
syte-access screenshot      # capture preview screenshot when available
```

Custom URLs saved in project access config can be fetched with `syte-access fetch <url>`.

Use preview access to verify visual changes, read rendered HTML, and inspect dev-server logs.
""",
    "service-management.md": """# Service management (Syte)

Use **`syte-service`** for all project lifecycle actions (preferred over raw systemctl/docker):

```bash
syte-service status
syte-service start              # start production service
syte-service stop               # stop production service
syte-service deploy             # git pull + build + deploy
syte-service preview_start      # dev preview with HMR
syte-service preview_stop
syte-service update             # git pull + restart
syte-service run "npm run lint" # run command in app/ workspace
syte-service logs 200           # deployment logs
syte-service preview_logs 200   # preview dev-server log
```

For `run`, pass the full shell command as one quoted argument. Default cwd is `app/`.
""",
    "mcp-tools.md": """# MCP tools (required)

This project exposes **Syte MCP tools** — always prefer them over ad-hoc shell:

| Tool | Use for |
|------|---------|
| `syte_service` | start/stop/deploy/preview/run/logs |
| `syte_access` | preview URL fetch, screenshot, preview logs |

CLI equivalents on PATH: `syte-service`, `syte-access`.

Do not bypass MCP/CLI helpers with raw curl to localhost unless debugging.
""",
}

ACCESS_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${SYTE_PROJECT_ID:?SYTE_PROJECT_ID not set}"
BASE="${SYTE_API_BASE:-http://127.0.0.1:__PORT__}"
ACTION="${1:-status}"
ARG="${2:-}"
PAYLOAD=$(ACTION="$ACTION" ARG="$ARG" python3 - <<'PY'
import json, os
action = os.environ.get("ACTION", "status")
arg = os.environ.get("ARG", "")
body = {"action": action}
if action in ("fetch", "read") and arg:
    body["url"] = arg
if action == "logs":
    try:
        body["lines"] = int(arg or 200)
    except ValueError:
        body["lines"] = 200
print(json.dumps(body))
PY
)
curl -sS -X POST "$BASE/api/projects/$PROJECT_ID/agent/access" \\
  -H "Content-Type: application/json" \\
  -d "$PAYLOAD"
echo
"""

SERVICE_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
PROJECT_ID="${SYTE_PROJECT_ID:?SYTE_PROJECT_ID not set}"
BASE="${SYTE_API_BASE:-http://127.0.0.1:__PORT__}"
ACTION="${1:-status}"
ARG="${2:-}"
PAYLOAD=$(ACTION="$ACTION" ARG="$ARG" python3 - <<'PY'
import json, os
action = os.environ.get("ACTION", "status")
arg = os.environ.get("ARG", "")
body = {"action": action}
if action == "run":
    body["command"] = arg
elif action in ("logs", "preview_logs"):
    try:
        body["lines"] = int(arg or 200)
    except ValueError:
        body["lines"] = 200
print(json.dumps(body))
PY
)
curl -sS -X POST "$BASE/api/projects/$PROJECT_ID/agent/service" \\
  -H "Content-Type: application/json" \\
  -d "$PAYLOAD"
echo
"""


def agent_access_config_path(project_id: str, agent_root: Path) -> Path:
    return agent_root / "access.json"


def default_access_config() -> dict[str, Any]:
    return {
        "custom_urls": [],
        "preview_tools": [
            "status",
            "url",
            "fetch",
            "read",
            "logs",
            "screenshot",
        ],
    }


async def read_access_config(project_id: str, agent_root: Path | None = None) -> dict[str, Any]:
    from syte.continue_agent import agent_root as default_root

    root = agent_root or default_root(project_id)
    path = agent_access_config_path(project_id, root)
    if not path.exists():
        return default_access_config()
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return default_access_config()
        merged = default_access_config()
        merged.update(data)
        if not isinstance(merged.get("custom_urls"), list):
            merged["custom_urls"] = []
        return merged
    except (json.JSONDecodeError, OSError):
        return default_access_config()


async def write_access_config(project_id: str, config: dict[str, Any], agent_root: Path | None = None) -> Path:
    from syte.continue_agent import agent_root as default_root

    root = agent_root or default_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
    path = agent_access_config_path(project_id, root)
    payload = default_access_config()
    if isinstance(config.get("custom_urls"), list):
        payload["custom_urls"] = [str(u).strip() for u in config["custom_urls"] if str(u).strip()]
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def build_agent_rules(project_id: str, access_config: dict[str, Any]) -> list[dict[str, str]]:
    custom_urls = access_config.get("custom_urls") or []
    custom_block = ""
    if custom_urls:
        urls = "\n".join(f"- {u}" for u in custom_urls)
        custom_block = f"\n\nAdditional URLs you may fetch with `syte-access fetch <url>`:\n{urls}"

    return [
        {
            "name": "Syte website agent",
            "rule": (
                "You edit websites inside the Syte workspace. Work in the app/ directory, "
                "match existing conventions, and keep changes small and verifiable. "
                "Search the codebase before rewriting files."
            ),
        },
        {
            "name": "MCP and CLI tools (required)",
            "rule": (
                "Always use MCP tools `syte_service` and `syte_access`, or the CLI helpers "
                "`syte-service` and `syte-access` on PATH. "
                "Use syte_service for deploy/start/stop/preview/run/logs. "
                "Use syte_access for preview URL fetch and screenshots. "
                "Do not use raw systemctl, docker, or undocumented curl shortcuts."
            ),
        },
        {
            "name": "Service management",
            "rule": (
                "To start/stop/deploy/preview/run commands: `syte-service <action>`. "
                "Examples: syte-service preview_start, syte-service deploy, "
                "syte-service run \"npm run dev\". "
                "Check syte-service status before assuming preview or production is running."
            ),
        },
        {
            "name": "Preview and access tools",
            "rule": (
                "Use `syte-access` for preview: status, url, fetch/read page HTML, "
                "logs (dev server output), and screenshot."
                f"{custom_block}"
            ),
        },
        {
            "name": "File operations",
            "rule": (
                "When changing the site: read files first, then create/edit/delete as needed. "
                "Every file change should be intentional and verifiable via preview."
            ),
        },
    ]


def write_agent_skills(project_id: str, agent_root: Path) -> list[Path]:
    """Write skill markdown files and syte-access helper into the agent workspace."""
    skills_dir = agent_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, content in SKILL_FILES.items():
        path = skills_dir / name
        path.write_text(content.strip() + "\n")
        written.append(path)

    bin_dir = agent_root / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    script_path = bin_dir / "syte-access"
    script_path.write_text(ACCESS_SCRIPT.replace("__PORT__", str(settings.port)))
    script_path.chmod(script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(script_path)

    service_path = bin_dir / "syte-service"
    service_path.write_text(SERVICE_SCRIPT.replace("__PORT__", str(settings.port)))
    service_path.chmod(service_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(service_path)
    return written


def mcp_server_config(project_id: str, agent_root: Path) -> dict[str, Any]:
    """Continue MCP server block for config.yaml."""
    import sys

    python = sys.executable
    return {
        "name": "syte-tools",
        "command": python,
        "args": ["-m", "syte.mcp_stdio"],
        "env": {
            "SYTE_PROJECT_ID": project_id,
            "SYTE_API_BASE": f"http://127.0.0.1:{settings.port}",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        },
    }


def agent_path_env(project_id: str, agent_root: Path) -> dict[str, str]:
    bin_dir = agent_root / "bin"
    path = os.environ.get("PATH", "")
    bin_str = str(bin_dir)
    return {
        "SYTE_PROJECT_ID": project_id,
        "SYTE_API_BASE": f"http://127.0.0.1:{settings.port}",
        "PATH": f"{bin_str}:{path}" if bin_str not in path else path,
    }
