"""Structured API responses for AI agents."""


def build_create_project_response(project: dict, workspace: dict | None, message: str) -> dict:
    uid = project["id"]
    return {
        "ok": True,
        "uuid": uid,
        "name": project["name"],
        "port": project["port"],
        "status": project.get("status", "created"),
        "message": message,
        "workspace": workspace,
        "paths": {
            "workspace": workspace.get("workspace_path") if workspace else f"/var/lib/syte/workspaces/{uid}",
            "app": workspace.get("app_path") if workspace else f"/var/lib/syte/workspaces/{uid}/app",
            "data": workspace.get("data_path") if workspace else f"/var/lib/syte/workspaces/{uid}/data",
        },
        "next_steps": [
            f"POST /api/write_file — add source files to uuid={uid}",
            f"POST /api/execute_command — run shell commands in cwd=app",
            f"POST /api/upload_file — upload binary assets",
            f"POST /api/issue_deploy — build & start when ready",
        ],
        "execute_command": {
            "method": "POST",
            "path": "/api/execute_command",
            "description": "Run any shell command in this project using the uuid below",
            "body": {
                "uuid": uid,
                "command": "npm install",
                "cwd": "app",
                "timeout": 300,
                "env": {},
            },
            "body_minimal": {
                "uuid": uid,
                "command": "ls -la",
                "cwd": "app",
            },
        },
        "write_file": {
            "method": "POST",
            "path": "/api/write_file",
            "body": {
                "uuid": uid,
                "path": "app/index.html",
                "content": "<!DOCTYPE html><html><body>Hello</body></html>",
            },
        },
        "issue_deploy": {
            "method": "POST",
            "path": "/api/issue_deploy",
            "description": "Deploy after files are ready (git clone, docker build, or shell start)",
            "body": {"uuid": uid},
        },
        "stream_url": f"/api/projects/{uid}/logs/stream?live=1",
        "get_logs": f"/api/get_logs?uuid={uid}",
        "workspace_get": f"/api/workspace_get?uuid={uid}",
    }
