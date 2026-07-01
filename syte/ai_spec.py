"""Machine-readable API specification for AI agents."""

from syte import __version__


def build_ai_spec(base_url: str = "") -> dict:
    base = base_url.rstrip("/")
    auth = {
        "type": "api_key",
        "header": "X-API-Key",
        "alternative": "Authorization: Bearer <token>",
        "token_prefix": "syte_",
        "create_token": "POST /api/tokens with {\"name\": \"my-agent\"} — no auth required (GUI/local). Token shown once.",
        "example_header": "X-API-Key: syte_xxxxxxxxxxxxxxxx",
    }
    return {
        "name": "Syte Deployment API",
        "version": __version__,
        "description": "Deploy websites and apps on a Linux server. Clone git repos, run any shell command, upload files, set domains, stream live deploy logs.",
        "base_url": f"{base}/api" if base else "/api",
        "documentation": f"{base}/api/" if base else "/api/",
        "authentication": auth,
        "errors": {
            "401_missing_api_key": "Send X-API-Key or Authorization: Bearer header",
            "401_invalid_api_key": "Token revoked or incorrect",
            "400_invalid_path": "File path escapes workspace sandbox",
            "400_create_failed": "Duplicate UUID or validation error",
            "404_not_found": "Project UUID does not exist",
        },
        "workflow_create_website_from_git": [
            "1. POST /api/tokens → save token",
            "2. POST /api/create_project {name, git_url, branch, deploy: true} → uuid + stream_url",
            "3. GET stream_url?live=1 (SSE) → watch deploy logs",
            "4. GET /api/workspace_get?uuid= → confirm url and running=true",
            "5. POST /api/set_domain {uuid, domain} → optional HTTPS domain",
        ],
        "workflow_create_website_from_scratch": [
            "1. POST /api/create_project {name} only — no git, no files required → uuid + execute_command.body",
            "2. POST /api/write_file {uuid, path: 'app/package.json', content: '...'}",
            "3. POST /api/write_file {uuid, path: 'app/Dockerfile', content: '...'}",
            "4. POST execute_command.body from create_project response (or {uuid, command, cwd: 'app'})",
            "5. POST /api/issue_deploy {uuid} → build and start when ready",
            "6. GET /api/get_logs?uuid= → check output",
        ],
        "endpoints": [
            {"method": "GET", "path": "/api/server_info", "auth": True, "description": "Server IP, version, URLs"},
            {"method": "GET", "path": "/api/workspace_list", "auth": True, "description": "List all projects"},
            {"method": "GET", "path": "/api/workspace_get?uuid=", "auth": True, "description": "Single project details + URLs"},
            {"method": "GET", "path": "/api/list_files?uuid=&path=", "auth": True, "description": "List files in workspace"},
            {"method": "POST", "path": "/api/read_file", "auth": True, "body": {"uuid": "str", "path": "str"}},
            {"method": "POST", "path": "/api/write_file", "auth": True, "body": {"uuid": "str", "path": "str", "content": "str"}},
            {"method": "POST", "path": "/api/upload_file", "auth": True, "body": "multipart: uuid, path, file"},
            {"method": "POST", "path": "/api/delete_file", "auth": True, "body": {"uuid": "str", "path": "str"}},
            {"method": "POST", "path": "/api/execute_command", "auth": True, "body": {"uuid": "str", "command": "any shell cmd", "cwd": "app", "timeout": 300, "env": {}}},
            {"method": "POST", "path": "/api/execute_commands", "auth": True, "body": {"uuid": "str", "commands": [{"command": "str", "cwd": "app"}]}},
            {"method": "POST", "path": "/api/set_env", "auth": True, "body": {"uuid": "str", "env_vars": {}, "merge": True}},
            {"method": "POST", "path": "/api/create_project", "auth": True, "body": {"name": "str (required)", "uuid": "optional", "git_url": "optional", "git_provider": "optional", "branch": "main", "start_command": "optional", "domain": "optional", "env_vars": {}, "deploy": "bool, default false — set true to deploy immediately"}, "response_includes": "uuid, execute_command.body with filled uuid, issue_deploy.body, next_steps, paths"},
            {"method": "POST", "path": "/api/issue_deploy", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/start_service", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/stop_service", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/set_domain", "auth": True, "body": {"uuid": "str", "domain": "app.example.com"}},
            {"method": "POST", "path": "/api/delete_project", "auth": True, "body": {"uuid": "str"}},
            {"method": "GET", "path": "/api/get_logs?uuid=&lines=200", "auth": True, "description": "Snapshot of deploy/runtime logs"},
            {"method": "GET", "path": "/api/projects/{uuid}/logs/stream?live=1", "auth": "optional", "description": "SSE live deploy logs"},
            {"method": "POST", "path": "/api/tokens", "auth": False, "body": {"name": "str"}, "description": "Create API key (GUI)"},
        ],
        "create_project_response": {
            "description": "AI agents: use execute_command.body from this response — uuid is pre-filled",
            "fields": {
                "uuid": "project id for all subsequent API calls",
                "execute_command": "POST /api/execute_command with body containing uuid, command, cwd",
                "execute_command.body_minimal": "minimal example: {uuid, command, cwd: app}",
                "issue_deploy": "POST /api/issue_deploy when files are ready",
                "next_steps": "human-readable checklist",
                "paths": "workspace, app, data directories on server",
            },
            "example": {
                "ok": True,
                "uuid": "my-site-a1b2c3",
                "status": "created",
                "execute_command": {
                    "method": "POST",
                    "path": "/api/execute_command",
                    "body": {"uuid": "my-site-a1b2c3", "command": "npm install", "cwd": "app", "timeout": 300},
                },
                "issue_deploy": {"method": "POST", "path": "/api/issue_deploy", "body": {"uuid": "my-site-a1b2c3"}},
            },
        },
        "execute_command_examples": [
            {"command": "npm install", "cwd": "app"},
            {"command": "npm run build", "cwd": "app"},
            {"command": "ls -la", "cwd": "app"},
            {"command": "cat package.json", "cwd": "app"},
            {"command": "mkdir -p src/components", "cwd": "app"},
            {"command": "npx create-next-app@latest . --yes", "cwd": "app"},
        ],
    }
