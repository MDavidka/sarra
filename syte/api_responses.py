"""Structured responses for project workspace creation."""


def build_create_project_response(project: dict, workspace: dict | None, message: str) -> dict:
    """Build a concise response for a newly created deployment project."""
    project_id = project["id"]
    workspace_root = f"/var/lib/syte/workspaces/{project_id}"
    return {
        "ok": True,
        "uuid": project_id,
        "name": project["name"],
        "port": project["port"],
        "preview_domain": project.get("preview_domain"),
        "preview_url": f"https://{project['preview_domain']}" if project.get("preview_domain") else None,
        "status": project.get("status", "created"),
        "message": message,
        "workspace": workspace,
        "paths": {
            "workspace": workspace.get("workspace_path") if workspace else workspace_root,
            "app": workspace.get("app_path") if workspace else f"{workspace_root}/app",
            "data": workspace.get("data_path") if workspace else f"{workspace_root}/data",
        },
        "next_steps": [
            "Write project files into the workspace.",
            "Run dependency installation or lint commands through the workspace API.",
            "Start a preview before issuing a production deployment.",
        ],
        "issue_deploy": {"method": "POST", "path": "/api/issue_deploy", "body": {"uuid": project_id}},
        "start_preview": {"method": "POST", "path": "/api/start_preview", "body": {"uuid": project_id}},
        "stop_preview": {"method": "POST", "path": "/api/stop_preview", "body": {"uuid": project_id}},
        "preview_status": f"/api/preview_status?uuid={project_id}",
        "stream_url": f"/api/projects/{project_id}/logs/stream?live=1",
        "get_logs": f"/api/get_logs?uuid={project_id}",
        "workspace_get": f"/api/workspace_get?uuid={project_id}",
    }
