"""Workspace file operations and command execution (sandboxed)."""

import asyncio
import os
import re
import shlex
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from syte.config import settings
from syte.database import get_project, list_projects, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.project_enrich import enrich_ssl
from syte.workspace import ensure_workspace, read_env_vars, workspace_path, write_env_file

# Block only catastrophic host-wide commands; arbitrary project commands are allowed.
BLOCKED_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/",
    "> /dev/sda",
    "wget http",
    "curl http | sh",
    "curl http | bash",
)

# Builds via execute_command are allowed for the agent; external API may still prefer issue_deploy.
FORBIDDEN_BUILD_PATTERNS: tuple[str, ...] = ()


def _resolve_workspace_path(project_id: str, rel_path: str = "") -> Path:
    base = workspace_path(project_id).resolve()
    if not base.exists():
        base = ensure_workspace(project_id).resolve()
    rel = (rel_path or "").strip().lstrip("/")
    target = (base / rel).resolve() if rel else base
    if target != base and base not in target.parents:
        raise ValueError("Path traversal denied — path must stay inside workspace")
    return target


def _is_blocked(command: str) -> str | None:
    lower = command.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lower:
            return pattern
    return None


def _is_forbidden_build(command: str) -> str | None:
    """Return matched pattern if command tries to build outside issue_deploy."""
    lower = command.lower().strip()
    for pattern in FORBIDDEN_BUILD_PATTERNS:
        if re.search(pattern, lower):
            return pattern
    return None


def _append_command_log(project_id: str, command: str, cwd: str, exit_code: int) -> None:
    log_path = workspace_path(project_id) / "commands.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with log_path.open("a") as f:
        f.write(f"[{ts}] exit={exit_code} cwd={cwd} $ {command}\n")


async def workspace_get(project_id: str) -> dict | None:
    from syte import process_manager
    from syte.openhands_agent import ensure_agent_runtime, get_agent_status
    from syte.preview_manager import ensure_preview_address, preview_meta

    project = await get_project(project_id)
    if not project:
        return None
    project = await ensure_preview_address(project)
    project = await ensure_agent_runtime(project)
    ws = workspace_path(project_id)
    ip = settings.resolved_public_ip
    domain = project.get("domain") or ""
    url = build_https_url(domain) if domain else build_direct_url(ip, project["port"])
    return {
        "uuid": project["id"],
        "name": project["name"],
        "status": project.get("status", "stopped"),
        "running": process_manager.is_running(project_id, project.get("deploy_type", "shell")),
        "deploy_type": project.get("deploy_type", "shell"),
        "dockerfile_path": project.get("dockerfile_path"),
        "port": project["port"],
        "url": url,
        "direct_url": build_direct_url(ip, project["port"]),
        "domain": normalize_domain(domain) if domain else "",
        "domain_url": build_https_url(domain) if domain else "",
        "git_url": project.get("git_url"),
        "branch": project.get("branch", "main"),
        "start_command": project.get("start_command", ""),
        "env_vars": read_env_vars(project.get("env_vars", "{}")),
        "workspace_path": str(ws),
        "app_path": str(ws / "app"),
        "data_path": str(ws / "data"),
        "stream_url": f"/api/projects/{project_id}/logs/stream?live=1",
        **preview_meta(project),
        "agent": await get_agent_status(project_id),
        "ssl": enrich_ssl(project),
    }


async def workspace_list() -> list[dict]:
    result = []
    for p in await list_projects():
        detail = await workspace_get(p["id"])
        if detail:
            result.append(detail)
    return result


async def list_workspace_files(project_id: str, subpath: str = "") -> list[dict]:
    project = await get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    root = _resolve_workspace_path(project_id, subpath)
    if not root.exists():
        raise ValueError("Path not found")
    if root.is_file():
        return [{
            "name": root.name,
            "path": str(root.relative_to(workspace_path(project_id))),
            "type": "file",
            "size": root.stat().st_size,
        }]
    entries = []
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") and item.name not in (".env", ".gitkeep"):
            continue
        rel = item.relative_to(workspace_path(project_id))
        entries.append({
            "name": item.name,
            "path": str(rel),
            "type": "directory" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return entries


async def read_file(project_id: str, file_path: str, max_bytes: int = 512_000) -> tuple[bool, str | bytes, str]:
    project = await get_project(project_id)
    if not project:
        return False, "", "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    if not target.exists():
        return False, "", f"File not found: {file_path}"
    if target.is_dir():
        return False, "", "Path is a directory — use list_files"
    size = target.stat().st_size
    if size > max_bytes:
        return False, "", f"File too large ({size} bytes). Max {max_bytes}."
    raw = target.read_bytes()
    try:
        return True, raw.decode("utf-8"), "text"
    except UnicodeDecodeError:
        return True, raw, "binary"


async def write_file(project_id: str, file_path: str, content: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        return False, "Target path is a directory"
    from syte.agent_activity import record_workspace_activity

    existed = target.exists()
    target.write_text(content)
    await record_workspace_activity(
        project_id,
        "create_file" if not existed else "write_file",
        path=file_path,
        source="api",
    )
    return True, f"Wrote {len(content)} chars to {file_path}"


async def delete_file(project_id: str, file_path: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    if not target.exists():
        return False, f"File not found: {file_path}"
    if target.is_dir():
        return False, "Use delete_directory or a file path"
    ws = workspace_path(project_id).resolve()
    if target == ws or target == ws / "app":
        return False, "Cannot delete workspace root"
    target.unlink()
    from syte.agent_activity import record_workspace_activity

    await record_workspace_activity(project_id, "delete_file", path=file_path, source="api")
    return True, f"Deleted {file_path}"


async def upload_file(project_id: str, file_path: str, content: bytes) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        return False, "Target path is a directory"
    target.write_bytes(content)
    from syte.agent_activity import record_workspace_activity

    await record_workspace_activity(project_id, "upload_file", path=file_path, source="api")
    return True, f"Uploaded {len(content)} bytes to {file_path}"


async def set_env_vars(project_id: str, env_vars: dict[str, str], merge: bool = True) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    current = read_env_vars(project.get("env_vars", "{}"))
    if merge:
        current.update(env_vars)
    else:
        current = dict(env_vars)
    write_env_file(project_id, current)
    await update_project(project_id, {"env_vars": current})
    return True, f"Environment updated ({len(current)} vars)"


async def execute_command(
    project_id: str,
    command: str,
    cwd: str = "app",
    timeout: int = 300,
    env: dict[str, str] | None = None,
    *,
    source: str = "api",
) -> tuple[int, str]:
    """Run any custom shell command inside the workspace (sandboxed to workspace dir)."""
    project = await get_project(project_id)
    if not project:
        return 1, "Project not found"
    cmd = command.strip()
    if not cmd:
        return 1, "Empty command"
    blocked = _is_blocked(cmd)
    if blocked:
        return 1, f"Command blocked (host safety): {blocked}"

    build_blocked = _is_forbidden_build(cmd) if source not in ("agent", "gui", "mcp") else None
    if build_blocked:
        return 1, (
            "Build commands are not allowed via execute_command. "
            "Use POST /api/issue_deploy {\"uuid\": \"...\"} instead — "
            "that runs git pull + docker build (npm run build inside Dockerfile) + restart. "
            "For testing, use: npm run lint"
        )

    workdir = _resolve_workspace_path(project_id, cwd)
    if not workdir.is_dir():
        return 1, f"Working directory not found: {cwd}"

    merged_env = {**os.environ, **read_env_vars(project.get("env_vars", "{}")), **(env or {})}

    def _run() -> tuple[int, str]:
        result = subprocess.run(
            shlex.split(cmd),
            shell=False,
            cwd=workdir,
            env=merged_env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode, out.strip() or "(no output)"

    try:
        code, output = await asyncio.to_thread(_run)
        _append_command_log(project_id, cmd, cwd, code)
        from syte.agent_activity import record_workspace_activity

        await record_workspace_activity(
            project_id,
            "execute_command",
            command=cmd,
            source=source,
            detail=output[:500] if output else "",
        )
        return code, output
    except subprocess.TimeoutExpired:
        _append_command_log(project_id, cmd, cwd, 124)
        return 124, f"Command timed out after {timeout}s"


async def execute_commands(
    project_id: str,
    commands: list[dict],
    default_cwd: str = "app",
    env: dict[str, str] | None = None,
) -> list[dict]:
    """Run a sequence of custom commands; stops on first non-zero exit if stop_on_error."""
    results = []
    for item in commands:
        cmd = item.get("command", "")
        cwd = item.get("cwd", default_cwd)
        timeout = int(item.get("timeout", 300))
        stop_on_error = item.get("stop_on_error", True)
        code, output = await execute_command(project_id, cmd, cwd, timeout, env)
        entry = {"command": cmd, "cwd": cwd, "exit_code": code, "output": output, "ok": code == 0}
        results.append(entry)
        if stop_on_error and code != 0:
            break
    return results
