"""Continue CLI extras: MCP servers, rules, and skills directories."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from syte.database import get_setting
from syte.workspace import workspace_path


def agent_home(project_id: str) -> Path:
    from syte.continue_agent import agent_home as _home

    return _home(project_id)


def _yaml_quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


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


async def load_continue_rules() -> list[str]:
    """Optional rules (file paths or inline strings), one per line in settings."""
    raw = (await get_setting("continue_rules", "")).strip()
    if not raw:
        return []
    return [line.strip() for line in raw.splitlines() if line.strip()]


def discover_workspace_mcp_files(project_id: str) -> list[Path]:
    """Optional per-project MCP YAML files under app/.continue/mcpServers/."""
    root = workspace_path(project_id) / "app" / ".continue" / "mcpServers"
    if not root.is_dir():
        return []
    return sorted(root.glob("*.yaml")) + sorted(root.glob("*.yml"))


def ensure_skills_directories(project_id: str) -> dict[str, str]:
    """Create Continue skills dirs (SKILL.md) per continue.dev CLI."""
    paths: dict[str, str] = {}
    for base in (
        agent_home(project_id) / ".continue" / "skills",
        workspace_path(project_id) / "app" / ".continue" / "skills",
    ):
        base.mkdir(parents=True, exist_ok=True)
        readme = base / "README.txt"
        if not readme.exists():
            readme.write_text(
                "Place SKILL.md files here for Continue CLI agent skills.\n"
                "See https://docs.continue.dev/guides/cli\n"
            )
        paths[base.name] = str(base)
    return paths


def render_mcp_servers_yaml(servers: list[dict[str, Any]]) -> list[str]:
    if not servers:
        return []
    lines = ["mcpServers:"]
    for server in servers:
        name = str(server.get("name") or "").strip()
        command = str(server.get("command") or "").strip()
        if not name or not command:
            continue
        lines.append(f"  - name: {_yaml_quote(name)}")
        lines.append(f"    command: {_yaml_quote(command)}")
        args = server.get("args") or []
        if isinstance(args, list) and args:
            lines.append("    args:")
            for arg in args:
                lines.append(f"      - {_yaml_quote(str(arg))}")
        env = server.get("env") or {}
        if isinstance(env, dict) and env:
            lines.append("    env:")
            for key, value in env.items():
                lines.append(f"      {key}: {_yaml_quote(str(value))}")
        cwd = server.get("cwd")
        if cwd:
            lines.append(f"    cwd: {_yaml_quote(str(cwd))}")
    return lines if len(lines) > 1 else []


def render_rules_yaml(rules: list[str]) -> list[str]:
    if not rules:
        return []
    lines = ["rules:"]
    for rule in rules:
        lines.append(f"  - {_yaml_quote(rule)}")
    return lines


def list_skill_files(project_id: str) -> list[str]:
    found: list[str] = []
    for base in (
        agent_home(project_id) / ".continue" / "skills",
        workspace_path(project_id) / "app" / ".continue" / "skills",
    ):
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("SKILL.md")):
            found.append(str(path.relative_to(base.parent.parent)))
    return found
