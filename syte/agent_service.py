"""Project service control for the debug-chat agent (start/stop/deploy/preview/run)."""

from __future__ import annotations

from typing import Any

from syte import process_manager
from syte.database import get_project
from syte.preview_manager import get_preview_status, preview_meta, start_preview, stop_preview_async
from syte.workspace_api import execute_command


SERVICE_ACTIONS = frozenset({
    "status",
    "start",
    "stop",
    "deploy",
    "preview_start",
    "preview_stop",
    "update",
    "run",
    "logs",
    "preview_logs",
})


async def list_service_capabilities(project_id: str) -> dict[str, Any]:
    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}
    preview = preview_meta(project)
    return {
        "ok": True,
        "project_id": project_id,
        "name": project.get("name"),
        "status": project.get("status"),
        "running": process_manager.is_running(project_id, project.get("deploy_type", "shell")),
        "preview_running": preview.get("preview_running"),
        "preview_url": preview.get("preview_url"),
        "start_command": project.get("start_command") or "",
        "actions": [
            {"action": "status", "description": "Project + preview status"},
            {"action": "start", "description": "Start production service"},
            {"action": "stop", "description": "Stop production service"},
            {"action": "deploy", "description": "Git pull + build + deploy"},
            {"action": "preview_start", "description": "Start dev preview (HMR)"},
            {"action": "preview_stop", "description": "Stop dev preview"},
            {"action": "update", "description": "Git pull + restart service"},
            {"action": "run", "description": "Run shell command in workspace (command=, cwd=app)"},
            {"action": "logs", "description": "Deployment logs (lines=)"},
            {"action": "preview_logs", "description": "Preview dev-server logs"},
        ],
        "cli": "syte-service <action> [arg]",
    }


async def run_service_action(
    project_id: str,
    action: str,
    *,
    command: str | None = None,
    cwd: str = "app",
    lines: int = 200,
    timeout: int = 300,
    source: str = "agent",
) -> dict[str, Any]:
    from syte import deployment
    from syte.preview_manager import get_preview_logs

    project = await get_project(project_id)
    if not project:
        return {"ok": False, "error": "not_found", "message": "Project not found"}

    act = (action or "status").strip().lower()
    if act not in SERVICE_ACTIONS:
        return {"ok": False, "error": "unknown_action", "message": f"Unknown action: {action}"}

    if act == "status":
        preview = preview_meta(project)
        return {
            "ok": True,
            "action": "status",
            "status": project.get("status"),
            "running": process_manager.is_running(project_id, project.get("deploy_type", "shell")),
            "url": project.get("domain") or project.get("port"),
            "preview_running": preview.get("preview_running"),
            "preview_ready": preview.get("preview_ready"),
            "preview_url": preview.get("preview_url"),
        }

    if act == "start":
        updated, message = await deployment.start_service(project_id)
        return {"ok": bool(updated), "action": "start", "message": message, "running": bool(updated and updated.get("status") == "running")}

    if act == "stop":
        updated, message = await deployment.stop_service(project_id)
        return {"ok": bool(updated), "action": "stop", "message": message}

    if act == "deploy":
        updated, message = await deployment.issue_deploy(project_id)
        return {"ok": bool(updated), "action": "deploy", "message": message, "status": (updated or {}).get("status")}

    if act == "preview_start":
        ok, message, meta = await start_preview(project_id)
        return {"ok": ok, "action": "preview_start", "message": message, **(meta or {})}

    if act == "preview_stop":
        await stop_preview_async(project_id)
        meta, message = await get_preview_status(project_id)
        return {"ok": True, "action": "preview_stop", "message": message or "Preview stopped", **(meta or {})}

    if act == "update":
        updated, message = await deployment.update_service(project_id)
        return {"ok": bool(updated), "action": "update", "message": message}

    if act == "run":
        cmd = (command or "").strip()
        if not cmd:
            return {"ok": False, "error": "missing_command", "message": "run requires command="}
        code, output = await execute_command(
            project_id,
            cmd,
            cwd=cwd or "app",
            timeout=timeout,
            source=source,
        )
        return {
            "ok": code == 0,
            "action": "run",
            "command": cmd,
            "cwd": cwd,
            "exit_code": code,
            "output": output,
        }

    if act == "logs":
        n = max(20, min(int(lines or 200), 2000))
        text = process_manager.get_logs(project_id, n, project.get("deploy_type", "shell"))
        return {"ok": True, "action": "logs", "lines": n, "logs": text}

    if act == "preview_logs":
        n = max(20, min(int(lines or 200), 2000))
        text = get_preview_logs(project_id, lines=n)
        return {"ok": True, "action": "preview_logs", "lines": n, "logs": text}

    return {"ok": False, "error": "unknown_action", "message": action}
