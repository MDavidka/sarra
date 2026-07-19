"""Default Syte cloud skills, rules, and workspace helpers for Syte projects."""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from syte.config import settings

SKILL_FILES: dict[str, str] = {
    "website-editing.md": """# Website editing

You are editing a live website project in the Syte workspace.

- Application source lives under `app/` (relative to the agent cwd).
- Make focused, minimal changes that match the existing stack and style.
- Prefer editing existing files over creating new ones unless necessary.
- After file changes, mention whether preview hot-reload should pick them up.
- Do not run production builds (`npm run build`, `next build`) — use preview instead.
- For site creation or redesign, deliver a complete styled home page that uses the
  project's existing design system and verify it in preview at desktop and mobile sizes.
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
syte-service preview_start      # dev preview with HMR
syte-service preview_stop
syte-service run "npm run lint" # run command in app/ workspace
syte-service logs 200           # deployment logs
syte-service preview_logs 200   # preview dev-server log
```

For `run`, pass the full shell command as one quoted argument. Default cwd is `app/`.
Production start, stop, update, deploy, and build actions are intentionally unavailable
to the agent. Test only with the isolated preview server.
""",
    "nextjs-app-router.md": """# Next.js App Router correctness

The Next.js project root is the workspace `app/` folder (where `package.json` lives and where
`next dev` runs). App Router routes therefore live one level deeper, under `app/app/`.

## File paths (write_file is relative to the workspace root)

| What | Correct path |
|------|--------------|
| Root layout | `app/app/layout.tsx` |
| Home page | `app/app/page.tsx` |
| A route | `app/app/login/page.tsx`, `app/app/dashboard/page.tsx` |
| Global CSS | `app/app/globals.css` (import once in `app/app/layout.tsx`) |
| Components | `app/components/ui/button.tsx` |
| tsconfig | `app/tsconfig.json` |
| Tailwind config | `app/tailwind.config.js` |

Writing `app/login/page.tsx` puts the file at the project root, **outside** the router dir —
Next.js ignores it and the route silently never appears. Always nest routes under `app/app/`.

## App Router rules (avoid common failures)

- **No `_document.tsx` / `_app.tsx`** — those are Pages Router files and are ignored by the App
  Router. Put `<html>`/`<body>`, providers, and metadata in `app/app/layout.tsx`.
- **`@/` alias** needs `"baseUrl": "."` and `"paths": { "@/*": ["./*"] }` in `tsconfig.json`,
  or every `@/components/...` import fails with `Module not found`.
- **`globals.css`** must contain `@tailwind base; @tailwind components; @tailwind utilities;`
  plus any CSS variables, and be imported once in the root layout.
- **`tailwind.config.js`** `content` must list real globs, e.g.
  `['./app/**/*.{ts,tsx}', './components/**/*.{ts,tsx}']` (never `[]`), and its theme colors
  should match the CSS variables.

## Verifying

- `write_file` overwrites the whole file and reports the verified on-disk size. Send the complete
  body every time; an empty-file warning means you truncated it — re-send the full contents.
- After edits, re-read key files or `list_files` to confirm they persisted.
- Production builds (`npm run build`, `next build`) are blocked. Verify with the dev preview and
  `npm run lint`. If the preview previously 500'd, `preview_stop` then `preview_start` to clear the
  cached failed compilation before judging the result.
""",
    "cli-tools.md": """# Syte CLI tools (required)

Use the **`syte-service`** and **`syte-access`** helpers on PATH for all Syte operations:

| Helper | Use for |
|--------|---------|
| `syte-service` | start/stop/deploy/preview/run/logs |
| `syte-access` | preview URL fetch, screenshot, preview logs |

Examples:

```bash
syte-service preview_start
syte-access status
syte-access fetch
```

Do not bypass these helpers with raw systemctl, docker, or undocumented curl shortcuts.
""",
}

# The catalog is deliberately derived from the markdown that is written into
# each agent workspace.  This keeps CLI access and prompt/API access in sync.
SKILL_REGISTRY: dict[str, dict[str, Any]] = {
    "website-editing": {
        "id": "website-editing",
        "name": "Website editing",
        "description": "Focused, preview-verified changes for live websites.",
        "content": SKILL_FILES["website-editing.md"],
        "priority": 10,
    },
    "workspace-search": {
        "id": "workspace-search",
        "name": "Workspace search",
        "description": "Search before editing and use the workspace tools safely.",
        "content": SKILL_FILES["workspace-search.md"],
        "priority": 20,
    },
    "preview-access": {
        "id": "preview-access",
        "name": "Preview access",
        "description": "Use the Syte preview helper to inspect pages and logs.",
        "content": SKILL_FILES["preview-access.md"],
        "priority": 30,
    },
    "service-management": {
        "id": "service-management",
        "name": "Service management",
        "description": "Manage preview and verification commands through Syte helpers.",
        "content": SKILL_FILES["service-management.md"],
        "priority": 40,
    },
    "nextjs-app-router": {
        "id": "nextjs-app-router",
        "name": "Next.js App Router",
        "description": "Avoid common App Router paths and configuration mistakes.",
        "content": SKILL_FILES["nextjs-app-router.md"],
        "priority": 50,
    },
    "cli-tools": {
        "id": "cli-tools",
        "name": "Syte CLI tools",
        "description": "Use the supported Syte service and access helpers.",
        "content": SKILL_FILES["cli-tools.md"],
        "priority": 60,
    },
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

MCP_SCRIPT = """#!/usr/bin/env bash
set -euo pipefail
: "${SYTE_PROJECT_ID:?SYTE_PROJECT_ID not set}"
: "${SYTE_API_BASE:?SYTE_API_BASE not set}"
: "${PYTHONPATH:?PYTHONPATH not set}"
exec python3 -m syte.mcp_stdio
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


async def get_project_skills(project_id: str) -> list[dict[str, Any]]:
    """Return the catalog with the project's enabled state and parameters."""
    from syte.agent_artifacts import ensure_artifact_tables

    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(
            "SELECT skill_id, parameters, enabled_at FROM agent_project_skills "
            "WHERE project_id = ?",
            (project_id,),
        ) as cursor:
            rows = await cursor.fetchall()

    active: dict[str, tuple[dict[str, str], str]] = {}
    for skill_id, raw_parameters, enabled_at in rows:
        try:
            parameters = json.loads(raw_parameters or "{}")
        except json.JSONDecodeError:
            parameters = {}
        active[skill_id] = (parameters if isinstance(parameters, dict) else {}, enabled_at)

    skills: list[dict[str, Any]] = []
    for skill in sorted(SKILL_REGISTRY.values(), key=lambda item: item["priority"]):
        parameters, enabled_at = active.get(skill["id"], ({}, None))
        skills.append({
            "id": skill["id"],
            "name": skill["name"],
            "description": skill["description"],
            "priority": skill["priority"],
            "parameters": parameters,
            "active": skill["id"] in active,
            "enabled_at": enabled_at,
        })
    return skills


async def enable_skill(
    project_id: str,
    skill_id: str,
    parameters: dict[str, str] | None = None,
) -> dict[str, Any]:
    from syte.agent_artifacts import ensure_artifact_tables

    skill = SKILL_REGISTRY.get(skill_id)
    if not skill:
        return {"ok": False, "error": "not_found", "message": f"Skill not found: {skill_id}"}
    await ensure_artifact_tables()
    now = datetime.now(timezone.utc).isoformat()
    clean_parameters = {str(key): str(value) for key, value in (parameters or {}).items()}
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_project_skills (project_id, skill_id, parameters, enabled_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(project_id, skill_id) DO UPDATE SET "
            "parameters = excluded.parameters, enabled_at = excluded.enabled_at",
            (project_id, skill_id, json.dumps(clean_parameters, ensure_ascii=False), now),
        )
        await db.commit()
    return {"ok": True, "skill": next(s for s in await get_project_skills(project_id) if s["id"] == skill_id)}


async def disable_skill(project_id: str, skill_id: str) -> dict[str, Any]:
    from syte.agent_artifacts import ensure_artifact_tables

    await ensure_artifact_tables()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute(
            "DELETE FROM agent_project_skills WHERE project_id = ? AND skill_id = ?",
            (project_id, skill_id),
        )
        await db.commit()
    if not cursor.rowcount:
        return {"ok": False, "error": "not_found", "message": f"Skill is not active: {skill_id}"}
    return {"ok": True, "skill_id": skill_id, "active": False}


async def read_access_config(project_id: str, agent_root: Path | None = None) -> dict[str, Any]:
    from syte.cloud_agent import agent_root as default_root

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
    from syte.cloud_agent import agent_root as default_root

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
            "name": "CLI tools (required)",
            "rule": (
                "Always use the CLI helpers `syte-service` and `syte-access` on PATH. "
                "Use syte-service for preview/run/logs. "
                "Use syte-access for preview URL fetch and screenshots. "
                "Do not use raw systemctl, docker, or undocumented curl shortcuts."
            ),
        },
        {
            "name": "Service management",
            "rule": (
                "To control preview or run verification commands: `syte-service <action>`. "
                "Examples: syte-service preview_start and syte-service run \"npm run lint\". "
                "Never use production start, stop, deploy, update, or build actions for testing. "
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
        {
            "name": "Home page quality",
            "rule": (
                "For site creation or redesign work, make the home page complete and styled. "
                "Integrate existing typography, color, spacing, components, and responsive behavior; "
                "verify desktop and mobile preview instead of leaving a bare scaffold."
            ),
        },
        {
            "name": "Next.js App Router",
            "rule": (
                "The Next.js project root is the workspace app/ folder, so App Router routes live under "
                "app/app/ (e.g. app/app/login/page.tsx), globals.css at app/app/globals.css, and config at "
                "app/tsconfig.json and app/tailwind.config.js. Never create _document.tsx or _app.tsx (Pages "
                "Router, ignored by App Router) — use app/app/layout.tsx. Ensure tsconfig has baseUrl \".\" and "
                "paths {\"@/*\": [\"./*\"]}, globals.css has the @tailwind directives and is imported in the "
                "layout, and tailwind content globs cover ./app and ./components. write_file overwrites whole "
                "files and verifies size; re-read after writes and treat an empty-file warning as a failure."
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

    mcp_path = bin_dir / "syte-mcp"
    mcp_path.write_text(MCP_SCRIPT)
    mcp_path.chmod(mcp_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    written.append(mcp_path)
    return written


def mcp_server_config(project_id: str, agent_root: Path) -> dict[str, Any]:
    """Syte MCP stdio descriptor for integrations that opt into MCP."""
    return {
        "name": "syte-tools",
        "command": str(agent_root / "bin" / "syte-mcp"),
        "args": [],
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
