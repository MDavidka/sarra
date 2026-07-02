"""Structured API responses for AI agents."""

from syte.design_contract import build_design_contract_spec


def build_create_project_response(project: dict, workspace: dict | None, message: str) -> dict:
    uid = project["id"]
    design = build_design_contract_spec()
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
        "design_contract": design,
        "design_contract_url": "/api/ai.json",
        "system_prompt_hint": "Read GET /api/ai.json → system_prompt before generating UI",
        "deploy_rules": design["deploy_rules"],
        "next_steps": [
            f"Read design_contract — follow Sycord Design Contract (shadcn/ui + Lucide + Inter)",
            f"POST /api/write_file — scaffold Next.js app in uuid={uid}",
            f"POST /api/execute_command — npm install, npm run lint (NOT npm run build)",
            f"GET /api/validate_design?uuid={uid} — run design linter",
            f"POST /api/issue_deploy — {{\"uuid\": \"{uid}\"}} git pull + docker build + start",
        ],
        "execute_command": {
            "method": "POST",
            "path": "/api/execute_command",
            "description": "Scaffolding and lint only — npm run build is FORBIDDEN, use issue_deploy",
            "body": {
                "uuid": uid,
                "command": "npm run lint",
                "cwd": "app",
                "timeout": 300,
                "env": {},
            },
            "body_minimal": {
                "uuid": uid,
                "command": "npm install",
                "cwd": "app",
            },
            "forbidden": ["npm run build", "yarn build", "next build"],
        },
        "write_file": {
            "method": "POST",
            "path": "/api/write_file",
            "body": {
                "uuid": uid,
                "path": "app/package.json",
                "content": '{"name":"app","scripts":{"dev":"next dev","build":"next build","lint":"next lint"}}',
            },
        },
        "issue_deploy": {
            "method": "POST",
            "path": "/api/issue_deploy",
            "description": "Git pull + docker build (includes npm run build in Dockerfile) + restart — ONLY way to deploy",
            "body": {"uuid": uid},
        },
        "validate_design": f"/api/validate_design?uuid={uid}",
        "stream_url": f"/api/projects/{uid}/logs/stream?live=1",
        "get_logs": f"/api/get_logs?uuid={uid}",
        "workspace_get": f"/api/workspace_get?uuid={uid}",
    }
