"""Workspace file operations and command execution (sandboxed)."""

import asyncio
import shlex
import subprocess
from pathlib import Path

from syte.database import get_project, list_projects
from syte.workspace import ensure_workspace, workspace_path

BLOCKED_COMMANDS = ("rm -rf /", "mkfs", ":(){ :|:& };:")


def _resolve_workspace_path(project_id: str, rel_path: str = "") -> Path:
    project = None  # validated by caller
    base = workspace_path(project_id).resolve()
    if not base.exists():
        base = ensure_workspace(project_id).resolve()
    rel = (rel_path or "").strip().lstrip("/")
    target = (base / rel).resolve() if rel else base
    if target != base and base not in target.parents:
        raise ValueError("Path traversal denied — file must stay inside workspace")
    return target


async def workspace_list() -> list[dict]:
    projects = await list_projects()
    result = []
    for p in projects:
        ws = workspace_path(p["id"])
        result.append({
            "uuid": p["id"],
            "name": p["name"],
            "status": p.get("status", "stopped"),
            "deploy_type": p.get("deploy_type", "shell"),
            "port": p["port"],
            "git_url": p.get("git_url"),
            "branch": p.get("branch", "main"),
            "domain": p.get("domain"),
            "workspace_path": str(ws),
            "app_path": str(ws / "app"),
            "data_path": str(ws / "data"),
        })
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
        rel = item.relative_to(workspace_path(project_id))
        entries.append({
            "name": item.name,
            "path": str(rel),
            "type": "directory" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return entries


async def delete_file(project_id: str, file_path: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    if not target.exists():
        return False, f"File not found: {file_path}"
    if target.is_dir():
        return False, "Use a file path, not a directory"
    ws = workspace_path(project_id).resolve()
    if target == ws or target == ws / "app":
        return False, "Cannot delete workspace root"
    target.unlink()
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
    return True, f"Uploaded {len(content)} bytes to {file_path}"


async def execute_command(
    project_id: str,
    command: str,
    cwd: str = "app",
    timeout: int = 120,
) -> tuple[int, str]:
    project = await get_project(project_id)
    if not project:
        return 1, "Project not found"
    cmd = command.strip()
    if not cmd:
        return 1, "Empty command"
    lower = cmd.lower()
    for blocked in BLOCKED_COMMANDS:
        if blocked in lower:
            return 1, f"Command blocked: {blocked}"
    workdir = _resolve_workspace_path(project_id, cwd)
    if not workdir.is_dir():
        return 1, f"Working directory not found: {cwd}"

    def _run() -> tuple[int, str]:
        result = subprocess.run(
            cmd,
            shell=True,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = (result.stdout or "") + (result.stderr or "")
        return result.returncode, out.strip() or "(no output)"

    try:
        return await asyncio.to_thread(_run)
    except subprocess.TimeoutExpired:
        return 1, f"Command timed out after {timeout}s"
