"""Default Syte cloud skills, rules, and workspace helpers for Syte projects."""

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


# Skill id → markdown filename (under data/cloud-agent/skills/).
SKILL_CATALOG: dict[str, dict[str, str]] = {
    "website-editing": {
        "file": "website-editing.md",
        "title": "Website editing",
        "description": "Focused live-site edits under app/ with preview verification.",
        "rule_name": "Syte website agent",
    },
    "workspace-search": {
        "file": "workspace-search.md",
        "title": "Workspace search",
        "description": "Search/read/list before rewriting files.",
        "rule_name": "File operations",
    },
    "preview-access": {
        "file": "preview-access.md",
        "title": "Preview access",
        "description": "syte-access helpers for preview URL, HTML, logs, screenshots.",
        "rule_name": "Preview and access tools",
    },
    "service-management": {
        "file": "service-management.md",
        "title": "Service management",
        "description": "syte-service for preview/run/logs (no production lifecycle).",
        "rule_name": "Service management",
    },
    "nextjs-app-router": {
        "file": "nextjs-app-router.md",
        "title": "Next.js App Router",
        "description": "Correct app/app/ routes, layout, Tailwind, and tsconfig paths.",
        "rule_name": "Next.js App Router",
    },
    "cli-tools": {
        "file": "cli-tools.md",
        "title": "CLI tools",
        "description": "Require syte-service / syte-access instead of raw systemctl/docker.",
        "rule_name": "CLI tools (required)",
    },
}


def agent_access_config_path(project_id: str, agent_root: Path) -> Path:
    return agent_root / "access.json"


def agent_skills_config_path(project_id: str, agent_root: Path) -> Path:
    return agent_root / "skills.json"


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


def default_skills_config() -> dict[str, Any]:
    """Per-project skill + MCP connection settings (written to skills.json)."""
    return {
        "enabled_skills": list(SKILL_CATALOG.keys()),
        "mcp": {
            "enabled": True,
            "auto_connect_builtin": True,
            "auto_connect_addons": [],
        },
    }


def available_skills() -> list[dict[str, Any]]:
    return [
        {
            "id": skill_id,
            "file": meta["file"],
            "title": meta["title"],
            "description": meta["description"],
        }
        for skill_id, meta in SKILL_CATALOG.items()
    ]


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


def _normalize_enabled_skills(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return list(SKILL_CATALOG.keys())
    known = set(SKILL_CATALOG.keys())
    out: list[str] = []
    for item in raw:
        skill_id = str(item or "").strip()
        if skill_id in known and skill_id not in out:
            out.append(skill_id)
    return out


def _normalize_mcp_config(raw: Any) -> dict[str, Any]:
    base = default_skills_config()["mcp"]
    if not isinstance(raw, dict):
        return base
    addons_raw = raw.get("auto_connect_addons")
    addons: list[str] = []
    if isinstance(addons_raw, list):
        for item in addons_raw:
            name = str(item or "").strip()
            if name and name not in addons:
                addons.append(name[:120])
    return {
        "enabled": bool(raw.get("enabled", True)),
        "auto_connect_builtin": bool(raw.get("auto_connect_builtin", True)),
        "auto_connect_addons": addons,
    }


async def read_skills_config(project_id: str, agent_root: Path | None = None) -> dict[str, Any]:
    from syte.cloud_agent import agent_root as default_root

    root = agent_root or default_root(project_id)
    path = agent_skills_config_path(project_id, root)
    payload = default_skills_config()
    if not path.exists():
        return payload
    try:
        data = json.loads(path.read_text())
        if not isinstance(data, dict):
            return payload
        payload["enabled_skills"] = _normalize_enabled_skills(data.get("enabled_skills"))
        payload["mcp"] = _normalize_mcp_config(data.get("mcp"))
        return payload
    except (json.JSONDecodeError, OSError):
        return payload


async def write_skills_config(
    project_id: str, config: dict[str, Any], agent_root: Path | None = None
) -> Path:
    from syte.cloud_agent import agent_root as default_root

    root = agent_root or default_root(project_id)
    root.mkdir(parents=True, exist_ok=True)
    path = agent_skills_config_path(project_id, root)
    payload = default_skills_config()
    if "enabled_skills" in config:
        payload["enabled_skills"] = _normalize_enabled_skills(config.get("enabled_skills"))
    if "mcp" in config:
        payload["mcp"] = _normalize_mcp_config(config.get("mcp"))
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


async def apply_mcp_connection_settings(project_id: str, skills_config: dict[str, Any] | None = None) -> dict[str, Any]:
    """Connect project MCP addons according to skills.json mcp settings."""
    from syte.agent_artifacts import connect_mcp_addon, list_mcp_addons

    root = None
    try:
        from syte.cloud_agent import agent_root as default_root

        root = default_root(project_id)
    except Exception:
        root = None
    config = skills_config or await read_skills_config(project_id, root)
    mcp = _normalize_mcp_config((config or {}).get("mcp"))
    result: dict[str, Any] = {
        "enabled": mcp["enabled"],
        "connected": [],
        "skipped": [],
        "server": mcp_server_config(project_id, root) if root is not None else None,
    }
    if not mcp["enabled"]:
        return result
    addons = await list_mcp_addons(project_id)
    wanted: list[str] = []
    if mcp["auto_connect_builtin"]:
        wanted.append("syte")
    wanted.extend(mcp["auto_connect_addons"])
    for name in wanted:
        addon = next((a for a in addons if a["name"] == name or a["id"] == name), None)
        if not addon:
            result["skipped"].append({"addon": name, "reason": "not_found"})
            continue
        connected = await connect_mcp_addon(project_id, addon["id"])
        if connected.get("ok"):
            result["connected"].append({
                "id": connected.get("id"),
                "name": connected.get("name"),
                "tools": connected.get("tools") or [],
            })
        else:
            result["skipped"].append({
                "addon": name,
                "reason": connected.get("error") or "connect_failed",
            })
    return result


def build_agent_rules(
    project_id: str,
    access_config: dict[str, Any],
    *,
    enabled_skills: list[str] | None = None,
) -> list[dict[str, str]]:
    custom_urls = access_config.get("custom_urls") or []
    custom_block = ""
    if custom_urls:
        urls = "\n".join(f"- {u}" for u in custom_urls)
        custom_block = f"\n\nAdditional URLs you may fetch with `syte-access fetch <url>`:\n{urls}"

    enabled = set(enabled_skills if enabled_skills is not None else SKILL_CATALOG.keys())
    all_rules: list[dict[str, str]] = [
        {
            "name": "Syte website agent",
            "skill_id": "website-editing",
            "rule": (
                "You edit websites inside the Syte workspace. Work in the app/ directory, "
                "match existing conventions, and keep changes small and verifiable. "
                "Search the codebase before rewriting files."
            ),
        },
        {
            "name": "CLI tools (required)",
            "skill_id": "cli-tools",
            "rule": (
                "Always use the CLI helpers `syte-service` and `syte-access` on PATH. "
                "Use syte-service for preview/run/logs. "
                "Use syte-access for preview URL fetch and screenshots. "
                "Do not use raw systemctl, docker, or undocumented curl shortcuts."
            ),
        },
        {
            "name": "Service management",
            "skill_id": "service-management",
            "rule": (
                "To control preview or run verification commands: `syte-service <action>`. "
                "Examples: syte-service preview_start and syte-service run \"npm run lint\". "
                "Never use production start, stop, deploy, update, or build actions for testing. "
                "Check syte-service status before assuming preview or production is running."
            ),
        },
        {
            "name": "Preview and access tools",
            "skill_id": "preview-access",
            "rule": (
                "Use `syte-access` for preview: status, url, fetch/read page HTML, "
                "logs (dev server output), and screenshot."
                f"{custom_block}"
            ),
        },
        {
            "name": "File operations",
            "skill_id": "workspace-search",
            "rule": (
                "When changing the site: read files first, then create/edit/delete as needed. "
                "Every file change should be intentional and verifiable via preview."
            ),
        },
        {
            "name": "Home page quality",
            "skill_id": "website-editing",
            "rule": (
                "For site creation or redesign work, make the home page complete and styled. "
                "Integrate existing typography, color, spacing, components, and responsive behavior; "
                "verify desktop and mobile preview instead of leaving a bare scaffold."
            ),
        },
        {
            "name": "Next.js App Router",
            "skill_id": "nextjs-app-router",
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
        {
            "name": "MCP project tools",
            "skill_id": "__mcp__",
            "rule": (
                "Use list_mcp_addons / connect_mcp / call_mcp for project MCP addons. "
                "Built-in addon `syte` exposes syte_service and syte_access (same as the CLI helpers). "
                "Register extra stdio MCP servers via the project MCP APIs when needed."
            ),
        },
    ]
    out: list[dict[str, str]] = []
    for item in all_rules:
        skill_id = item.get("skill_id") or ""
        if skill_id == "__mcp__":
            # MCP rule is controlled by skills.json mcp.enabled (caller may strip).
            out.append({"name": item["name"], "rule": item["rule"]})
            continue
        if skill_id in enabled:
            out.append({"name": item["name"], "rule": item["rule"]})
    return out


def write_agent_skills(
    project_id: str,
    agent_root: Path,
    *,
    enabled_skills: list[str] | None = None,
    mcp_enabled: bool = True,
) -> list[Path]:
    """Write enabled skill markdown files and syte-* helpers into the agent workspace."""
    skills_dir = agent_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    enabled = set(enabled_skills if enabled_skills is not None else SKILL_CATALOG.keys())
    enabled_files = {
        SKILL_CATALOG[skill_id]["file"]
        for skill_id in enabled
        if skill_id in SKILL_CATALOG
    }

    for name, content in SKILL_FILES.items():
        path = skills_dir / name
        if name in enabled_files:
            path.write_text(content.strip() + "\n")
            written.append(path)
        elif path.exists():
            try:
                path.unlink()
            except OSError:
                pass

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
    if mcp_enabled:
        mcp_path.write_text(MCP_SCRIPT)
        mcp_path.chmod(mcp_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(mcp_path)
    elif mcp_path.exists():
        try:
            mcp_path.unlink()
        except OSError:
            pass
    return written


def mcp_server_config(project_id: str, agent_root: Path | None) -> dict[str, Any]:
    """Syte MCP stdio descriptor for integrations that opt into MCP."""
    bin_cmd = "syte-mcp"
    if agent_root is not None:
        bin_cmd = str(agent_root / "bin" / "syte-mcp")
    return {
        "name": "syte-tools",
        "transport": "stdio",
        "command": bin_cmd,
        "args": [],
        "env": {
            "SYTE_PROJECT_ID": project_id,
            "SYTE_API_BASE": f"http://127.0.0.1:{settings.port}",
            "PYTHONPATH": str(Path(__file__).resolve().parent.parent),
        },
        "tools": [
            {
                "name": "syte_service",
                "description": "Control project preview/service/logs (function → /agent/service)",
            },
            {
                "name": "syte_access",
                "description": "Preview URL fetch/logs/screenshot (function → /agent/access)",
            },
        ],
        "documentation": "/api/#agent-mcp",
        "project_routes": {
            "list": f"/api/projects/{project_id}/agent/mcp",
            "register": f"/api/projects/{project_id}/agent/mcp",
            "connect": f"/api/projects/{project_id}/agent/mcp/connect",
            "call": f"/api/projects/{project_id}/agent/mcp/call",
            "skills": f"/api/projects/{project_id}/agent/skills",
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
