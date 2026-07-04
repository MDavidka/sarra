"""Machine-readable Sycord API specification."""

from syte import __version__
from syte.sycord.integration_guide import build_backend_integration
from syte.sycord.scaffold import STACKS


def _prefix(base_url: str) -> str:
    base = base_url.rstrip("/")
    return f"{base}/sycord/api" if base else "/sycord/api"


def project_connect_example() -> dict:
    return {
        "ok": True,
        "uuid": "testproject-a1b2c3",
        "message": "Empty project testproject-a1b2c3 created. Scaffolded: app/package.json, app/Dockerfile…",
        "persist": {
            "save_uuid": True,
            "uuid": "testproject-a1b2c3",
            "instruction": (
                "Save uuid in your Sycord project database (e.g. projects.syte_uuid). "
                "Every follow-up call — upload, issue_deployment, container_get, domain — requires this uuid."
            ),
            "endpoints_using_uuid": [
                "POST /sycord/api/upload — form field uuid",
                "POST /sycord/api/issue_deployment — body.uuid",
                "GET /sycord/api/container_get?uuid=",
                "POST /sycord/api/domain — body.uuid",
            ],
        },
        "project": {
            "uuid": "testproject-a1b2c3",
            "name": "testproject",
            "domain": "testproject.sycord.site",
            "url": "https://testproject.sycord.site",
            "stack": "nextjs",
            "status": "created",
            "port": 3010,
            "workspace_path": "/var/syte/workspaces/testproject-a1b2c3",
            "app_path": "/var/syte/workspaces/testproject-a1b2c3/app",
            "created_at": "2026-07-04T18:00:00Z",
        },
        "subdomain_pattern": "{slug}.sycord.site",
        "next_steps": {
            "upload": "POST /sycord/api/upload",
            "deploy": "POST /sycord/api/issue_deployment",
            "container": "GET /sycord/api/container_get?uuid=testproject-a1b2c3",
        },
    }


def build_sycord_spec(base_url: str = "") -> dict:
    prefix = _prefix(base_url)
    stacks = list(STACKS)
    host = base_url.rstrip("/") if base_url else "https://your-syte-host.com"
    return {
        "name": "Sycord Deployer API",
        "version": __version__,
        "description": (
            "Connect Sycord websites and external projects to the Syte deployer. "
            "project_connect returns a uuid — your application must persist it."
        ),
        "base_url": prefix,
        "documentation": f"{prefix}/" if base_url else "/sycord/api/",
        "integration_guide": f"{prefix}/integration.json",
        "authentication": {
            "type": "api_key",
            "header": "X-API-Key",
            "alternative": "Authorization: Bearer <token>",
            "query_param": "api_key (SSE streams only)",
            "create_token": "POST /api/tokens {\"name\": \"sycord\"} — Syte GUI → Users",
            "example_header": "X-API-Key: syte_xxxxxxxxxxxxxxxx",
        },
        "uuid_persistence": {
            "required": True,
            "when": "Immediately after POST /sycord/api/project_connect",
            "field": "uuid",
            "also_in": "response.persist.uuid, response.project.uuid",
            "format": "{slugified-name}-{6 hex chars} e.g. testproject-a1b2c3",
            "custom_uuid": "Optional body.uuid on project_connect if you need a fixed id",
            "instruction": (
                "Store the returned uuid in your project record before any other Sycord API call. "
                "Re-connecting the same name creates a new uuid unless you pass body.uuid."
            ),
            "example_database": {
                "table": "sycord_projects",
                "columns": {
                    "id": "your internal id",
                    "name": "user-facing project name",
                    "syte_uuid": "from project_connect response.uuid — REQUIRED",
                    "syte_domain": "from response.project.domain",
                    "syte_url": "from response.project.url",
                },
            },
        },
        "stacks": stacks,
        "workflow": [
            "1. POST /sycord/api/project_connect {name, stack} → save response.uuid",
            "2. POST /sycord/api/upload {uuid, path, file} — add or update files",
            "3. POST /sycord/api/issue_deployment {uuid} — docker build + deploy",
            "4. GET /sycord/api/container_get?uuid= — poll until running=true",
            "5. POST /sycord/api/domain {uuid, domain} — optional custom hostname",
        ],
        "errors": {
            "401_missing_api_key": "Send X-API-Key or Authorization: Bearer",
            "401_invalid_api_key": "Token revoked or incorrect",
            "400_invalid_stack": f"stack must be one of: {', '.join(stacks)}",
            "400_connect_failed": "Duplicate subdomain or validation error",
            "400_upload_failed": "Bad path or project not found",
            "404_not_found": "uuid does not exist",
        },
        "backend_integration": build_backend_integration(host),
        "endpoints": [
            {
                "method": "POST",
                "path": f"{prefix}/project_connect",
                "auth": True,
                "summary": "Create Syte project — returns uuid to save",
                "request": {
                    "content_type": "application/json",
                    "body": {
                        "name": "string (required) — project name, used for subdomain slug",
                        "stack": f"{' | '.join(stacks)} (default nextjs)",
                        "uuid": "string (optional) — your custom Syte project id",
                        "env_vars": "object (optional) — KEY=value pairs",
                    },
                },
                "response": {
                    "save_field": "uuid",
                    "example": project_connect_example(),
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/container_get",
                "auth": True,
                "summary": "Docker container status for a connected project",
                "request": {"query": {"uuid": "string (required) — from project_connect"}},
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "container_name": "syte-testproject-a1b2c3",
                        "exists": True,
                        "running": True,
                        "state": "running",
                        "image": "syte-testproject-a1b2c3",
                        "url": "https://testproject.sycord.site",
                        "domain": "testproject.sycord.site",
                        "host_port": 3010,
                        "status": "running",
                    }
                },
            },
            {
                "method": "POST",
                "path": f"{prefix}/upload",
                "auth": True,
                "summary": "Upload a file into the project workspace",
                "request": {
                    "content_type": "multipart/form-data",
                    "fields": {
                        "uuid": "string (required)",
                        "path": "string (required) e.g. app/app/page.tsx",
                        "file": "binary (required)",
                    },
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "path": "app/app/page.tsx",
                        "bytes": 1024,
                        "message": "Uploaded 1024 bytes to app/app/page.tsx",
                    }
                },
            },
            {
                "method": "POST",
                "path": f"{prefix}/domain",
                "auth": True,
                "summary": "Set production HTTPS domain (Caddy TLS)",
                "request": {
                    "content_type": "application/json",
                    "body": {
                        "uuid": "string (required)",
                        "domain": "string (required) e.g. myapp.sycord.site",
                    },
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "domain": "myapp.sycord.site",
                        "url": "https://myapp.sycord.site",
                        "message": "Domain set to myapp.sycord.site…",
                    }
                },
            },
            {
                "method": "POST",
                "path": f"{prefix}/issue_deployment",
                "auth": True,
                "summary": "Docker build + deploy (background)",
                "request": {
                    "content_type": "application/json",
                    "body": {"uuid": "string (required)"},
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "message": "Deploy issued for testproject-a1b2c3…",
                        "stream_url": "/api/projects/testproject-a1b2c3/logs/stream?live=1",
                        "status": "deploying",
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/spec.json",
                "auth": False,
                "summary": "This machine-readable specification",
            },
        ],
    }
