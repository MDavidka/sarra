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
                "POST /sycord/api/preview_start — body.uuid",
                "GET /sycord/api/preview_status?uuid=",
                "POST /sycord/api/preview_stop — body.uuid",
                "GET /sycord/api/agent_status?uuid=",
                "POST /sycord/api/agent_change — body.uuid + message",
                "GET /sycord/api/agent_activity?uuid=&since_id=",
                "GET /sycord/api/agent_sessions?uuid= — list durable Turso session ids",
                "GET /sycord/api/agent_session/{session_id} — fetch one durable session",
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
            "save_uuid": "testproject-a1b2c3",
            "upload": "POST /sycord/api/upload",
            "preview": "POST /sycord/api/preview_start",
            "deploy": "POST /sycord/api/issue_deployment",
            "container": "GET /sycord/api/container_get?uuid=testproject-a1b2c3",
            "agent": "POST /sycord/api/agent_change — body {\"uuid\":\"testproject-a1b2c3\",\"message\":\"…\"}",
            "agent_sessions": "GET /sycord/api/agent_sessions?uuid=testproject-a1b2c3",
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
            "3. POST /sycord/api/preview_start {uuid} — fast dev preview with HMR (~5s)",
            "4. GET /sycord/api/preview_status?uuid= — poll until preview_ready=true",
            "5. POST /sycord/api/agent_change {uuid, message} — async AI code change; returns request_id + turso_session_id",
            "6. GET /sycord/api/agent_session/{session_id} — fetch the durable Turso session (poll until status != 'open')",
            "7. POST /sycord/api/issue_deployment {uuid} — docker build + deploy",
            "8. GET /sycord/api/container_get?uuid= — poll until running=true",
            "9. POST /sycord/api/domain {uuid, domain} — optional custom hostname",
        ],
        "agent_session": {
            "description": (
                "Continuous always-warm Syte cloud runtime per used project. Change requests are "
                "async jobs. Every turn's activity (request, plan, tool calls, reply) is written "
                "durably to a Turso (libSQL) session identified by a UUID as it happens — there is "
                "no live stream any more; fetch the session document by its id instead."
            ),
            "status": f"{prefix}/agent_status?uuid=",
            "warm": "POST /api/agent_warm {uuid} — non-blocking and deduplicated",
            "submit": f"POST {prefix}/agent_change",
            "activity_snapshot": f"GET {prefix}/agent_activity?uuid=&since_id=",
            "sessions_list": f"GET {prefix}/agent_sessions?uuid=",
            "session_fetch": f"GET {prefix}/agent_session/{{session_id}}",
            "turso_configuration": (
                "Set turso_database_url (and optional turso_auth_token) in the Syte GUI's "
                "AI tab. Until configured, agent_session/agent_sessions return "
                "turso_configured=false / an empty session list, but agent_change still works."
            ),
            "async_response": {
                "ok": True,
                "request_id": "req_abc123def456",
                "status": "accepted",
                "turso_session_id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                "session_url": "/sycord/api/agent_session/b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
            },
            "session_document": {
                "id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                "project_id": "myapp-a1b2c3",
                "session_number": 1,
                "model_profile": "syra-base",
                "status": "open | completed | failed | cancelled",
                "created_at": "2026-07-15T12:00:00+00:00",
                "updated_at": "2026-07-15T12:00:04+00:00",
                "events": [
                    {"id": 1, "event_type": "request_started", "role": "user", "detail": "Add dark mode"},
                    {"id": 2, "event_type": "processing", "role": "system", "detail": "Cloud agent accepted the durable request"},
                    {"id": 3, "event_type": "tool_call_started", "payload": {"tool": "write_file"}},
                    {"id": 4, "event_type": "tool_call_finished", "payload": {"tool": "write_file", "ok": True}},
                    {"id": 5, "event_type": "request_completed", "payload": {"reply": "Added dark mode"}},
                ],
            },
            "event_types": [
                "request_started",
                "processing",
                "file_created",
                "file_modified",
                "file_deleted",
                "tool_call_started",
                "tool_call_finished",
                "thinking",
                "request_completed",
                "request_failed",
            ],
            "model_profiles": ["syra-nano", "syra-base", "syra-havy", "syra-ultra"],
            "legacy_sync": "POST agent_change with wait:true for blocking reply",
        },
        "errors": {
            "401_missing_api_key": "Send X-API-Key or Authorization: Bearer",
            "401_invalid_api_key": "Token revoked or incorrect",
            "400_invalid_stack": f"stack must be one of: {', '.join(stacks)}",
            "400_connect_failed": "Duplicate subdomain or validation error",
            "400_upload_failed": "Bad path or project not found",
            "400_preview_failed": "No runnable app detected (or preview process failed). Auto-detects Next/Vite/CRA/Astro/Nuxt/Express/Python/static HTML",
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
                "method": "POST",
                "path": f"{prefix}/preview_start",
                "auth": True,
                "summary": "Start fast dev preview (next dev / vite, HMR, ~5s)",
                "request": {
                    "content_type": "application/json",
                    "body": {"uuid": "string (required)"},
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "message": "Preview on https://previewk-testproject.sycord.site — ready (HMR live)",
                        "preview_url": "https://previewk-testproject.sycord.site",
                        "preview_domain": "previewk-testproject.sycord.site",
                        "preview_domain_url": "https://previewk-testproject.sycord.site",
                        "preview_direct_url": "http://203.0.113.10:4001",
                        "preview_ready": True,
                        "preview_running": True,
                        "preview_port": 4001,
                        "preview_status": "running",
                        "preview_stream_url": "/api/projects/testproject-a1b2c3/preview/logs/stream?live=1",
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/preview_status",
                "auth": True,
                "summary": "Preview dev server status — poll until preview_ready=true",
                "request": {"query": {"uuid": "string (required) — from project_connect"}},
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "preview_url": "https://previewk-testproject.sycord.site",
                        "preview_domain": "previewk-testproject.sycord.site",
                        "preview_ready": True,
                        "preview_running": True,
                        "preview_port": 4001,
                        "preview_status": "running",
                        "preview_stream_url": "/api/projects/testproject-a1b2c3/preview/logs/stream?live=1",
                    }
                },
            },
            {
                "method": "POST",
                "path": f"{prefix}/preview_stop",
                "auth": True,
                "summary": "Stop preview dev server",
                "request": {
                    "content_type": "application/json",
                    "body": {"uuid": "string (required)"},
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "testproject-a1b2c3",
                        "message": "Preview stopped",
                        "preview_running": False,
                        "preview_ready": False,
                        "preview_status": "stopped",
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/spec.json",
                "auth": False,
                "summary": "This machine-readable specification",
            },
            {
                "method": "GET",
                "path": f"{prefix}/agent_status",
                "auth": True,
                "summary": "Continuous workspace agent status",
                "request": {"query": {"uuid": "string (required)"}},
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "myapp-a1b2c3",
                        "agent_status": "running",
                        "agent_running": True,
                        "agent_healthy": True,
                        "agent_port": 5204,
                        "activity_stream_url": "/api/projects/myapp-a1b2c3/agent/activity/stream?live=1",
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/agent_activity",
                "auth": True,
                "summary": "Agent activity snapshot (incremental with since_id); local SQLite, not durable across DB moves",
                "request": {"query": {"uuid": "string (required)", "since_id": "integer (default 0)", "limit": "integer (default 200)"}},
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "myapp-a1b2c3",
                        "since_id": 0,
                        "events": [
                            {
                                "id": 15,
                                "event_type": "request_completed",
                                "role": "assistant",
                                "detail": "Added ThemeToggle component",
                                "payload": {"request_id": "req_abc123def456"},
                            },
                        ],
                        "sessions_url": "/sycord/api/agent_sessions?uuid=myapp-a1b2c3",
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/agent_sessions",
                "auth": True,
                "summary": "List durable Turso agent-session UUIDs for a project (newest first)",
                "request": {"query": {"uuid": "string (required)", "limit": "integer (default 50)"}},
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "myapp-a1b2c3",
                        "turso_configured": True,
                        "sessions": [
                            {
                                "id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                                "session_number": 1,
                                "status": "completed",
                                "session_url": "/sycord/api/agent_session/b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                            }
                        ],
                    }
                },
            },
            {
                "method": "GET",
                "path": f"{prefix}/agent_session/{{session_id}}",
                "auth": True,
                "summary": "Fetch one durable agent session (metadata + events) from Turso by UUID",
                "request": {"query": {"since_id": "integer (default 0) — only events after this id"}},
                "response": {
                    "example": {
                        "ok": True,
                        "id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                        "project_id": "myapp-a1b2c3",
                        "status": "completed",
                        "events": [
                            {"id": 1, "event_type": "request_started", "detail": "Add dark mode"},
                            {"id": 5, "event_type": "request_completed", "payload": {"reply": "Added dark mode"}},
                        ],
                    }
                },
            },
            {
                "method": "POST",
                "path": f"{prefix}/agent_change",
                "auth": True,
                "summary": "Request code change — returns request_id + turso_session_id immediately (async)",
                "request": {
                    "content_type": "application/json",
                    "body": {
                        "uuid": "string (required)",
                        "message": "string (required)",
                        "model_profile": "syra-nano | syra-base | syra-havy | syra-ultra (optional)",
                        "wait": "bool (default false) — set true for blocking legacy mode",
                    },
                },
                "response": {
                    "example": {
                        "ok": True,
                        "uuid": "myapp-a1b2c3",
                        "request_id": "req_abc123def456",
                        "status": "accepted",
                        "turso_session_id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                        "session_url": "/sycord/api/agent_session/b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                    }
                },
            },
        ],
    }
