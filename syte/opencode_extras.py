"""OpenCode extras: MCP servers, instructions, and skills directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from syte.database import get_setting
from syte.workspace import workspace_path


def agent_home(project_id: str) -> Path:
    from syte.opencode_agent import agent_home as _home

    return _home(project_id)


async def load_mcp_servers() -> list[dict[str, Any]]:
    """Global MCP servers from Syte settings (JSON array)."""
    raw = (await get_setting("continue_mcp_servers", "")).strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return []
    return data if isinstance(data, list) else []


async def load_agent_rules() -> list[str]:
    """Optional rules (file paths or inline strings), one per line in settings."""
    raw = (await get_setting("continue_rules", "")).strip()
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def discover_workspace_mcp_files(project_id: str) -> list[Path]:
    """Optional per-project MCP JSON under app/.opencode/mcp/."""
    root = workspace_path(project_id) / "app" / ".opencode" / "mcp"
    if not root.is_dir():
        return []
    return sorted(root.glob("*.json"))


def ensure_skills_directories(project_id: str) -> dict[str, str]:
    """Create OpenCode skills dirs (SKILL.md) per opencode.ai docs."""
    paths: dict[str, str] = {}
    for base in (
        agent_home(project_id) / ".config" / "opencode" / "skills",
        workspace_path(project_id) / "app" / ".opencode" / "skills",
    ):
        base.mkdir(parents=True, exist_ok=True)
        readme = base / "README.txt"
        if not readme.exists():
            readme.write_text(
                "Place SKILL.md files here for OpenCode agent skills.\n"
                "See https://opencode.ai/docs/\n"
            )
        paths[base.name] = str(base)
    return paths


def render_mcp_servers_dict(servers: list[dict[str, Any]]) -> dict[str, Any]:
    """Convert Syte MCP JSON into OpenCode opencode.json mcp block."""
    rendered: dict[str, Any] = {}
    for server in servers:
        name = str(server.get("name") or "").strip()
        command = server.get("command")
        if not name:
            continue
        if isinstance(command, list):
            cmd = [str(part) for part in command if str(part).strip()]
        else:
            cmd = [str(command).strip()] if str(command or "").strip() else []
            args = server.get("args") or []
            if isinstance(args, list):
                cmd.extend(str(arg) for arg in args if str(arg).strip())
        if not cmd:
            continue
        entry: dict[str, Any] = {
            "type": "local",
            "command": cmd,
            "enabled": server.get("enabled", True),
        }
        env = server.get("env") or server.get("environment") or {}
        if isinstance(env, dict) and env:
            entry["environment"] = {str(k): str(v) for k, v in env.items()}
        cwd = server.get("cwd")
        if cwd:
            entry["cwd"] = str(cwd)
        rendered[name] = entry
    return rendered


def list_skill_files(project_id: str) -> list[str]:
    found: list[str] = []
    for base in (
        agent_home(project_id) / ".config" / "opencode" / "skills",
        workspace_path(project_id) / "app" / ".opencode" / "skills",
    ):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("SKILL.md")):
            found.append(str(path.relative_to(base.parent.parent)))
    return found
