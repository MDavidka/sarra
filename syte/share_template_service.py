"""Syte-hosted Share It template catalog and secure instance provisioning."""
from __future__ import annotations

import hashlib
import json
import secrets
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from syte import deployment
from syte.config import settings
from syte.auth import hash_password, verify_password
from syte.database import get_project, update_project
from syte import process_manager
from syte.workspace import ensure_workspace, workspace_path

_TEMPLATE_ROOT = Path(__file__).resolve().parent / "share_templates"
_TEMPLATE_CATALOG = ({
    "id": "control-plane-nextjs",
    "name": "Control Plane",
    "summary": "Dark Next.js control dashboard for hosted client and server management.",
    "description": "A Syte-hosted Next.js template with client records, server status, deployment controls, audit-ready activity, and a shadcn-inspired #101010 interface.",
    "framework": "Next.js · shadcn/ui",
    "runtime": "Node.js 20",
    "source_dir": "control-plane-nextjs",
    "icon": "layout-dashboard",
},)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def ensure_share_templates() -> None:
    """Idempotently register Syte-owned templates; external URLs are never accepted."""
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        for template in _TEMPLATE_CATALOG:
            await db.execute(
                """INSERT INTO share_templates
                (id, name, summary, description, framework, runtime, source_dir, icon, is_syte_hosted, is_available, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET name=excluded.name, summary=excluded.summary,
                    description=excluded.description, framework=excluded.framework, runtime=excluded.runtime,
                    source_dir=excluded.source_dir, icon=excluded.icon, is_syte_hosted=1, is_available=1, updated_at=excluded.updated_at""",
                (template["id"], template["name"], template["summary"], template["description"], template["framework"], template["runtime"], template["source_dir"], template["icon"], now, now),
            )
        await db.commit()


async def list_share_templates() -> list[dict[str, Any]]:
    await ensure_share_templates()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT id, name, summary, description, framework, runtime, icon, is_syte_hosted FROM share_templates WHERE is_available = 1 ORDER BY name") as cur:
            return [dict(row) for row in await cur.fetchall()]


async def get_share_template(template_id: str) -> dict[str, Any] | None:
    await ensure_share_templates()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM share_templates WHERE id = ? AND is_available = 1 AND is_syte_hosted = 1", (template_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


def _provisioned_project_summary(project: dict[str, Any]) -> dict[str, Any]:
    """Return only the non-secret project fields suitable for browser responses."""
    return {
        "id": project.get("id"),
        "name": project.get("name"),
        "status": project.get("status"),
        "port": project.get("port"),
        "domain": project.get("domain") or "",
        "url": project.get("url") or "",
    }


def _platform_url() -> str:
    domain = str(getattr(settings, "gui_domain", "") or "").strip()
    if domain:
        return f"https://{domain}"
    # Hosted Docker workloads reach the host-bound Syte service via the
    # default bridge gateway. The URL and instance key remain server-only.
    return "http://172.17.0.1:8787"


async def provision_share_template(template_id: str, name: str, owner_account_id: str = "", access_password: str = "") -> tuple[dict[str, Any], str]:
    template = await get_share_template(template_id)
    if not template:
        raise ValueError("That template is unavailable or is not hosted by Syte.")
    safe_name = name.strip()
    if not safe_name or len(safe_name) > 120:
        raise ValueError("Provide a template instance name of up to 120 characters.")
    source = (_TEMPLATE_ROOT / str(template["source_dir"])).resolve()
    if _TEMPLATE_ROOT not in source.parents or not source.is_dir() or not (source / "package.json").is_file():
        raise ValueError("The Syte-hosted template source is not available.")
    project, message = await deployment.create_project_record(name=safe_name, deploy_now=False, in_app_notifications=True)
    if not project:
        raise ValueError(message)
    instance_id = uuid.uuid4().hex[:16]
    instance_key = f"syte_tpl_{secrets.token_urlsafe(32)}"
    workspace = ensure_workspace(project["id"])
    # Syte's deployment engine builds the workspace `app` directory.
    destination = workspace / "app"
    try:
        shutil.copytree(source, destination, dirs_exist_ok=False)
        env_vars = {
            "SYTE_SHARE_INSTANCE_ID": instance_id,
            "SYTE_SHARE_INSTANCE_KEY": instance_key,
            "SYTE_SHARE_API_BASE": _platform_url(),
            "NEXT_TELEMETRY_DISABLED": "1",
        }
        updated = await update_project(project["id"], {
            "deploy_type": "docker",
            "dockerfile_path": "Dockerfile",
            "start_command": "npm run start",
            "healthcheck_path": "/api/health",
            "env_vars": env_vars,
            "status": "created",
        })
        now = _now()
        async with aiosqlite.connect(settings.resolved_db_path) as db:
            await db.execute(
                """INSERT INTO share_instances (id, template_id, project_id, owner_account_id, instance_key_hash, status, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, 'ready', ?, ?)""",
                (instance_id, template_id, project["id"], owner_account_id, _hash(instance_key), now, now),
            )
            await db.commit()
        if access_password:
            await configure_share_instance_access(instance_id, access_password)
        return {
            "instance": {"id": instance_id, "template_id": template_id, "project_id": project["id"], "status": "ready"},
            "project": _provisioned_project_summary(updated or project),
        }, instance_key
    except Exception:
        shutil.rmtree(destination, ignore_errors=True)
        raise


async def authenticate_share_instance(instance_id: str, raw_key: str) -> dict[str, Any] | None:
    if not instance_id or not raw_key:
        return None
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM share_instances WHERE id = ? AND instance_key_hash = ?", (instance_id, _hash(raw_key))) as cur:
            row = await cur.fetchone()
        if row:
            await db.execute("UPDATE share_instances SET last_used_at = ?, updated_at = updated_at WHERE id = ?", (_now(), instance_id))
            await db.commit()
            return dict(row)
    return None


async def configure_share_instance_access(instance_id: str, password: str) -> None:
    """Set an owner-chosen dashboard password, never the platform credential."""
    encoded = hash_password(password)
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute(
            "UPDATE share_instances SET access_password_hash = ?, access_configured_at = ?, updated_at = ? WHERE id = ?",
            (encoded, now, now, instance_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("Hosted template instance was not found.")
        await db.commit()


async def verify_share_instance_access(instance_id: str, password: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT access_password_hash FROM share_instances WHERE id = ?", (instance_id,)) as cur:
            row = await cur.fetchone()
    return bool(row and row["access_password_hash"] and verify_password(password, str(row["access_password_hash"])))


async def rotate_share_instance_access(instance_id: str, current_password: str, new_password: str) -> bool:
    if not await verify_share_instance_access(instance_id, current_password):
        return False
    await configure_share_instance_access(instance_id, new_password)
    return True


async def share_instance_terminal(instance: dict[str, Any], command: str) -> dict[str, Any]:
    """Run a deliberately restricted, project-scoped operational terminal command."""
    project = await get_project(str(instance["project_id"]))
    if not project:
        raise ValueError("The hosted project is no longer available.")
    command = command.strip().lower()
    if command == "status":
        output = f"project={project['id']}\\nstatus={project.get('status') or 'stopped'}\\nport={project.get('port')}\\ndeploy_type={project.get('deploy_type') or 'shell'}"
    elif command == "logs":
        output = process_manager.get_logs(str(project["id"]), lines=120, deploy_type=str(project.get("deploy_type") or "shell"))
    elif command == "health":
        output = f"status={project.get('status') or 'stopped'}\\nhealthcheck={project.get('healthcheck_path') or '/'}\\nservice={'running' if project.get('status') == 'running' else 'not-running'}"
    else:
        raise ValueError("Unsupported terminal command. Use status, logs, or health.")
    return {"command": command, "output": output[-24000:], "project_id": project["id"]}


_FILE_DENYLIST = {".env", ".git", "node_modules", ".next", "__pycache__"}


def _project_file_path(project_id: str, relative_path: str = "") -> Path:
    root = (workspace_path(project_id) / "app").resolve()
    relative = Path(relative_path.strip().lstrip("/"))
    if any(part in _FILE_DENYLIST for part in relative.parts):
        raise ValueError("That path is not available in the project file manager.")
    candidate = (root / relative).resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError("Invalid project path.")
    return candidate


async def share_instance_files(instance: dict[str, Any], relative_path: str = "") -> dict[str, Any]:
    project_id = str(instance["project_id"])
    target = _project_file_path(project_id, relative_path)
    if not target.exists():
        raise ValueError("File or folder not found.")
    if target.is_file():
        if target.stat().st_size > 256_000:
            raise ValueError("File is too large to open in the project file manager.")
        return {"path": relative_path.strip("/"), "type": "file", "content": target.read_text(errors="replace")}
    entries = []
    for item in sorted(target.iterdir(), key=lambda path: (not path.is_dir(), path.name.lower()))[:250]:
        if item.name in _FILE_DENYLIST:
            continue
        entries.append({"name": item.name, "path": str(item.relative_to(workspace_path(project_id) / "app")), "type": "folder" if item.is_dir() else "file", "size": None if item.is_dir() else item.stat().st_size})
    return {"path": relative_path.strip("/"), "type": "folder", "entries": entries}


async def write_share_instance_file(instance: dict[str, Any], relative_path: str, content: str) -> dict[str, Any]:
    if len(content.encode("utf-8")) > 256_000:
        raise ValueError("File content exceeds the 256 KB project editor limit.")
    project_id = str(instance["project_id"])
    target = _project_file_path(project_id, relative_path)
    if not target.name or target.exists() and target.is_dir():
        raise ValueError("Choose a project file path.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return {"path": str(target.relative_to(workspace_path(project_id) / "app")), "bytes": len(content.encode("utf-8")), "message": "Project file saved. Deploy to apply runtime changes."}


async def share_instance_startup(instance: dict[str, Any], updates: dict[str, str] | None = None) -> dict[str, Any]:
    project_id = str(instance["project_id"])
    project = await get_project(project_id)
    if not project:
        raise ValueError("The hosted project is no longer available.")
    if updates:
        allowed: dict[str, str] = {}
        healthcheck = str(updates.get("healthcheck_path") or "").strip()
        if healthcheck:
            if not healthcheck.startswith("/") or len(healthcheck) > 240:
                raise ValueError("Health check path must be a relative URL path.")
            allowed["healthcheck_path"] = healthcheck
        start_command = str(updates.get("start_command") or "").strip()
        if start_command:
            if len(start_command) > 500 or any(token in start_command for token in ("\n", "\r")):
                raise ValueError("Startup command is invalid.")
            allowed["start_command"] = start_command
        if allowed:
            project = await update_project(project_id, allowed) or project
    return {"project_id": project_id, "deploy_type": project.get("deploy_type") or "shell", "start_command": project.get("start_command") or "", "healthcheck_path": project.get("healthcheck_path") or "/", "status": project.get("status") or "stopped", "port": project.get("port")}


async def share_instance_overview(instance: dict[str, Any]) -> dict[str, Any]:
    project = await get_project(str(instance["project_id"]))
    if not project:
        raise ValueError("The hosted project is no longer available.")
    env = project.get("env_vars") or "{}"
    try:
        variables = json.loads(env) if isinstance(env, str) else env
    except json.JSONDecodeError:
        variables = {}
    return {
        "instance": {"id": instance["id"], "template_id": instance["template_id"], "status": instance["status"]},
        "project": {"id": project["id"], "name": project["name"], "domain": project.get("domain") or "", "status": project.get("status") or "stopped", "running": project.get("status") in {"running", "deploying"}, "url": f"https://{project['domain']}" if project.get("domain") else "", "created_at": project.get("created_at"), "variables": len(variables) if isinstance(variables, dict) else 0},
    }


async def run_share_instance_action(instance: dict[str, Any], action: str) -> dict[str, Any]:
    project_id = str(instance["project_id"])
    if action == "start":
        project, message = await deployment.start_service(project_id)
    elif action == "stop":
        project, message = await deployment.stop_service(project_id)
    elif action == "deploy":
        project, message = await deployment.issue_deploy(project_id)
    else:
        raise ValueError("Unsupported template instance action.")
    if not project:
        raise ValueError(message)
    return {"project_id": project_id, "status": project.get("status"), "message": message}
