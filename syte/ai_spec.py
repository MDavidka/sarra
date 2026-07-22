"""Machine-readable API specification for AI agents."""

from syte import __version__
from syte.design_contract import build_design_contract_spec, build_system_prompt
from syte.thinking_levels import thinking_levels_spec


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
    design = build_design_contract_spec()
    return {
        "name": "Syte Deployment API",
        "version": __version__,
        "description": "Deploy Next.js websites on a Linux server. Follow design_contract for all UI generation. Deploy via issue_deploy only.",
        "base_url": f"{base}/api" if base else "/api",
        "documentation": f"{base}/api/" if base else "/api/",
        "authentication": auth,
        "system_prompt": build_system_prompt(),
        "design_contract": design,
        "deploy_rules": design["deploy_rules"],
        "errors": {
            "401_missing_api_key": "Send X-API-Key or Authorization: Bearer header",
            "401_invalid_api_key": "Token revoked or incorrect",
            "400_invalid_path": "File path escapes workspace sandbox",
            "400_create_failed": "Duplicate UUID or validation error",
            "400_build_forbidden": "npm run build blocked — use POST /api/issue_deploy instead",
            "404_not_found": "Project UUID does not exist",
        },
        "workflow_create_website_from_git": [
            "1. POST /api/tokens → save token",
            "2. POST /api/create_project {name, git_url, branch} → uuid (do NOT set deploy:true)",
            "3. POST /api/issue_deploy {uuid} → git pull + docker build + start",
            "4. GET /api/projects/{uuid}/logs/stream?live=1 (SSE) → watch deploy",
            "5. GET /api/workspace_get?uuid= → confirm url, ssl.active, and running=true",
            "6. POST /api/validate_design?uuid= → design contract linter",
            "7. POST /api/set_domain {uuid, domain} → optional custom HTTPS domain (auto-assigned on deploy if omitted)",
        ],
        "workflow_create_website_from_scratch": [
            "1. Read system_prompt + design_contract — follow Sycord Design Contract",
            "2. POST /api/create_project {name} only → uuid + execute_command.body",
            "3. POST /api/write_file — scaffold Next.js + shadcn/ui + Tailwind per design_contract",
            "4. POST /api/execute_command — npm install, npm run lint (testing ONLY, never npm run build)",
            "5. POST /api/start_preview {uuid} → live HMR preview in seconds (preview_url)",
            "6. POST /api/write_file — iterate; preview hot-reloads automatically",
            "7. POST /api/validate_design?uuid= → check design contract",
            "8. POST /api/issue_deploy {uuid} → production docker build + start",
            "9. POST /api/stop_preview {uuid} → stop dev server when done",
        ],
        "workflow_live_preview": [
            "1. Set preview_base_domain (e.g. sycord.site) + Cloudflare token in Settings; wildcard DNS *.sycord.site → server",
            "2. POST /api/start_preview {uuid} → preview_url e.g. https://previewk-mysite.sycord.site",
            "3. GET /api/preview_status?uuid= → poll until preview_ready=true; check ssl.preview.active",
            "4. Open preview_url — HMR live; POST /api/write_file edits hot-reload",
            "5. GET /api/projects/{uuid}/preview/logs/stream?live=1 → dev server logs",
            "6. POST /api/stop_preview {uuid} when finished",
        ],
        "preview_api": {
            "description": "Fast live preview via next dev/vite — HTTPS on wildcard GUI zone",
            "domain_format": "preview{random_letter}-{appname}.sycord.site (e.g. previewk-mysite.sycord.site)",
            "domain_rules": {
                "pattern": "preview{a-z}-{appname-slug}.{gui-zone}",
                "example": "https://previewk-mysite.sycord.site",
                "ssl": "Automatic via wildcard *.sycord.site — no per-preview DNS",
                "vite_allowed_hosts": "Auto: patches vite.config.* with server.allowedHosts: true on start_preview",
                "nextjs_origins": "Auto: allowedDevOrigins patched in next.config on start_preview",
                "iframe_embed": "frame-ancestors restricted to sycord.com + GUI domain (preview_embed_mode=any for *)",
                "preview_base_domain": "Settings preview_base_domain — separate wildcard zone for preview URLs (default: GUI zone)",
                "preview_host_pattern": "preview{a-z}-{appname}.{preview_zone}",
                "not_used": "No preview.app.example.com third-level subdomains",
                "no_gui_domain": "preview_url = preview_direct_url (http://IP:4000+)",
            },
            "endpoints": {
                "start": "POST /api/start_preview {\"uuid\":\"...\"}",
                "status": "GET /api/preview_status?uuid=...",
                "stop": "POST /api/stop_preview {\"uuid\":\"...\"}",
                "logs": "GET /api/projects/{uuid}/preview/logs/stream?live=1",
            },
            "response_fields": [
                "preview_url", "preview_domain", "preview_domain_url",
                "preview_direct_url", "preview_ready", "preview_running",
                "preview_port", "preview_stream_url", "preview_dns_hint",
                "ssl.preview", "ssl.badge", "ssl.badge_label",
                "iframe.all_ok", "iframe.items", "iframe.frame_csp",
            ],
            "iframe_debug": "GET /api/projects/{uuid}/preview/iframe-check",
        },
        "production_ssl": {
            "description": "Production HTTPS via wildcard zone or per-host Caddy auto-HTTPS",
            "domain_format": "{app-slug}.{zone} (e.g. mysite.sycord.site) — auto-assigned on issue_deploy when no domain set",
            "domain_rules": {
                "auto_assign": "On successful deploy, Syte assigns {slug}.{preview_zone} when projects.domain is empty",
                "custom": "POST /api/set_domain overrides auto-assigned hostname",
                "ssl": "Automatic via wildcard *.{zone} when Cloudflare token configured",
                "no_zone": "url = http://{public_ip}:{port} until preview zone is configured",
            },
            "response_fields": [
                "url", "domain", "domain_url", "ssl.production", "ssl.badge", "ssl.badge_label",
            ],
        },
        "endpoints": [
            {"method": "GET", "path": "/api/server_info", "auth": True, "description": "Server IP, version, URLs"},
            {"method": "GET", "path": "/api/ai.json", "auth": False, "description": "This spec + design_contract + system_prompt"},
            {"method": "GET", "path": "/api/validate_design?uuid=", "auth": True, "description": "Run design contract linter on project"},
            {"method": "GET", "path": "/api/workspace_list", "auth": True, "description": "List all projects"},
            {"method": "GET", "path": "/api/workspace_get?uuid=", "auth": True, "description": "Single project details + URLs"},
            {"method": "GET", "path": "/api/list_files?uuid=&path=", "auth": True, "description": "List files in workspace"},
            {"method": "POST", "path": "/api/read_file", "auth": True, "body": {"uuid": "str", "path": "str"}},
            {"method": "POST", "path": "/api/write_file", "auth": True, "body": {"uuid": "str", "path": "str", "content": "str"}},
            {"method": "POST", "path": "/api/upload_file", "auth": True, "body": "multipart: uuid, path, file"},
            {"method": "POST", "path": "/api/delete_file", "auth": True, "body": {"uuid": "str", "path": "str"}},
            {"method": "POST", "path": "/api/execute_command", "auth": True, "body": {"uuid": "str", "command": "shell cmd (no build)", "cwd": "app", "timeout": 300, "env": {}}, "note": "npm run build FORBIDDEN — use issue_deploy"},
            {"method": "POST", "path": "/api/execute_commands", "auth": True, "body": {"uuid": "str", "commands": [{"command": "str", "cwd": "app"}]}},
            {"method": "POST", "path": "/api/set_env", "auth": True, "body": {"uuid": "str", "env_vars": {}, "merge": True}},
            {"method": "POST", "path": "/api/create_project", "auth": True, "body": {"name": "str (required)", "uuid": "optional", "git_url": "optional", "git_provider": "optional", "branch": "main", "start_command": "optional", "domain": "optional", "env_vars": {}, "deploy": "bool, default false — prefer issue_deploy"}, "response_includes": "uuid, execute_command.body, issue_deploy.body, design_contract_url"},
            {"method": "POST", "path": "/api/issue_deploy", "auth": True, "body": {"uuid": "str"}, "description": "Git pull + docker build + restart — production deploy"},
            {"method": "POST", "path": "/api/start_preview", "auth": True, "body": {"uuid": "str"}, "description": "Fast dev preview (next dev/vite, HMR, ~5s)"},
            {"method": "POST", "path": "/api/stop_preview", "auth": True, "body": {"uuid": "str"}},
            {"method": "GET", "path": "/api/preview_status?uuid=", "auth": True, "description": "preview_url, preview_ready, preview_running"},
            {"method": "GET", "path": "/api/projects/{uuid}/preview/logs/stream?live=1", "auth": "optional", "description": "SSE preview dev server logs"},
            {"method": "POST", "path": "/api/start_service", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/stop_service", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/set_domain", "auth": True, "body": {"uuid": "str", "domain": "app.example.com"}},
            {"method": "POST", "path": "/api/delete_project", "auth": True, "body": {"uuid": "str"}},
            {"method": "GET", "path": "/api/get_logs?uuid=&lines=200", "auth": True, "description": "Snapshot of deploy/runtime logs"},
            {"method": "GET", "path": "/api/projects/{uuid}/logs/stream?live=1", "auth": "optional", "description": "SSE live deploy logs"},
            {"method": "GET", "path": "/api/agent_status?uuid=", "auth": True, "description": "Syte cloud agent status + agent_proxy_url"},
            {"method": "POST", "path": "/api/agent_warm", "auth": True, "body": {"uuid": "str"}, "description": "Non-blocking, deduplicated runtime prewarm for instant chat"},
            {"method": "POST", "path": "/api/agent_start", "auth": True, "body": {"uuid": "str"}, "description": "Start Syte cloud runtime"},
            {"method": "POST", "path": "/api/agent_stop", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/agent_restart", "auth": True, "body": {"uuid": "str"}},
            {"method": "POST", "path": "/api/agent_settings", "auth": True, "body": {"uuid": "str", "model_profile": "syra-nano|syra-base|syra-havy"}},
            {"method": "GET", "path": "/api/agent_logs?uuid=&lines=200", "auth": True, "description": "Syte cloud runtime log snapshot"},
            {"method": "GET", "path": "/api/agent_dashboard", "auth": True, "description": "DPFA/MNOA metrics + onboarding state"},
            {"method": "POST", "path": "/api/agent_test", "auth": True, "body": {"uuid": "str"}, "description": "Probe CLI + bridge + communicate"},
            {"method": "POST", "path": "/api/agent_communicate", "auth": True, "body": {"uuid": "str", "message": "str", "model_profile": "optional", "thinking_level": "optional 1-5"}},
            {"method": "POST", "path": "/api/agent_change", "auth": True, "body": {"uuid": "str", "message": "str", "model_profile": "optional", "model_name": "optional", "thinking_level": "optional 1-5"}, "description": "Async code change — returns request_id + turso_session_id immediately; fetch agent_session/{id} for the durable record"},
            {"method": "GET", "path": "/api/agent_activity?uuid=&since_id=0", "auth": True, "description": "Local SQLite activity snapshot (incremental with since_id; optional session=last|N)"},
            {"method": "GET", "path": "/api/agent_sessions?uuid=", "auth": True, "description": "List durable Turso agent-session UUIDs for a project (newest first)"},
            {"method": "GET", "path": "/api/agent_session/{session_id}?since_id=0", "auth": True, "description": "Fetch a durable agent activity session (metadata + events) from Turso by UUID"},
            {"method": "GET", "path": "/api/agent_screenshots?uuid=", "auth": True, "description": "List saved desktop/phone preview screenshots for a project"},
            {"method": "GET", "path": "/api/projects/{uuid}/agent/screenshots/{id}", "auth": False, "description": "Fetch screenshot PNG (?variant=thumb for compact chat image)"},
            {"method": "GET", "path": "/api/agent_plans?uuid=", "auth": True, "description": "List persisted agent plans (update_plan / thinking steps)"},
            {"method": "GET", "path": "/api/agent_questions?uuid=&status=", "auth": True, "description": "List interactive agent questions (pending/answered)"},
            {"method": "POST", "path": "/api/agent_answer_question", "auth": True, "body": {"uuid": "str", "question_id": "str", "answer": "str|number|string[]|object"}, "description": "Answer an ask_question / request_env prompt so the agent can continue"},
            {"method": "GET", "path": "/api/agent_stops?uuid=", "auth": True, "description": "List session stop markers (stop/interrupt/cancel) with stopped_at timestamps"},
            {"method": "GET", "path": "/api/agent_mcp?uuid=", "auth": True, "description": "List available MCP addons (built-in syte + registered); add/enable/disable/edit/call via agent_mcp_*"},
            {"method": "POST", "path": "/api/agent_mcp_register", "auth": True, "body": {"uuid": "str", "name": "str", "command": "str", "args": [], "env": {}, "description": "optional"}, "description": "Add (register) a custom MCP stdio provider"},
            {"method": "POST", "path": "/api/agent_mcp_connect", "auth": True, "body": {"uuid": "str", "addon": "str"}, "description": "Enable (connect) an MCP addon"},
            {"method": "POST", "path": "/api/agent_mcp_call", "auth": True, "body": {"uuid": "str", "addon": "str", "tool": "str", "arguments": {}}},
            {"method": "POST", "path": "/api/agent_mcp_update", "auth": True, "body": {"uuid": "str", "addon": "str", "name": "optional", "command": "optional", "args": [], "env": {}, "description": "optional"}, "description": "Edit a registered (non-builtin) MCP addon"},
            {"method": "POST", "path": "/api/agent_mcp_disconnect", "auth": True, "body": {"uuid": "str", "addon": "str"}, "description": "Disable (disconnect) an MCP addon without deleting registration"},
            {"method": "GET", "path": "/api/agent_skills?uuid=", "auth": True, "description": "List built-in + custom skills with active state/parameters for a project"},
            {"method": "POST", "path": "/api/agent_skills_add", "auth": True, "body": {"uuid": "str", "name": "str", "content": "str", "description": "optional", "enable": True, "parameters": {}}, "description": "Add a custom skill (optionally enable immediately)"},
            {"method": "POST", "path": "/api/agent_skills_update", "auth": True, "body": {"uuid": "str", "skill_id": "str", "name": "optional", "content": "optional", "description": "optional", "parameters": {}}, "description": "Edit a custom skill definition"},
            {"method": "POST", "path": "/api/agent_skills_enable", "auth": True, "body": {"uuid": "str", "skill_id": "str", "parameters": {}}, "description": "Enable a skill or edit its string parameters"},
            {"method": "POST", "path": "/api/agent_skills_disable", "auth": True, "body": {"uuid": "str", "skill_id": "str"}, "description": "Disable a project skill"},
            {"method": "POST", "path": "/api/agent_skills_delete", "auth": True, "body": {"uuid": "str", "skill_id": "str"}, "description": "Delete a custom skill definition"},
            {"method": "GET", "path": "/api/projects/{uuid}/agent/mcp", "auth": False, "description": "GUI mirror — list/add/connect/call/update/disconnect MCP"},
            {"method": "GET", "path": "/api/projects/{uuid}/agent/skills", "auth": False, "description": "GUI mirror — list/add/enable/disable/edit/delete skills"},
            {"method": "GET", "path": "/api/projects/{uuid}/agent/logs/stream?live=1", "auth": "optional", "description": "SSE Syte cloud agent logs"},
            {"method": "POST", "path": "/api/tokens", "auth": False, "body": {"name": "str"}, "description": "Create API key (GUI)"},
        ],
        "agent_session": {
            "description": (
                "Continuous per-workspace Syte cloud runtime. One durable session per project; "
                "change requests are async jobs that return request_id immediately. Every turn's "
                "activity (request, plan, tool calls, reply) is written as it happens to a durable "
                "Turso (libSQL) session identified by a UUID — see turso_sessions below. There is no "
                "live activity stream any more; fetch the session document by its id instead."
            ),
            "documentation": f"{base}/api/#agent" if base else "/api/#agent",
            "model_profiles": {
                "syra-nano": "Fast — Gemini Flash class",
                "syra-base": "Balanced — DeepSeek chat class",
                "syra-havy": "Capable — Gemini Pro class",
            },
            "thinking_level": thinking_levels_spec(),
            "gui_configuration": (
                "Syte GUI → AI tab — internal secret, per-profile Verted/DeepSeek API keys, and "
                "turso_database_url / turso_auth_token for durable session storage"
            ),
            "metrics": {
                "dpfa": "Dedicated Performance For Agents — CPU percent on VM",
                "mnoa": "Maximum Number Of Agents — running agents vs configured max",
            },
            "async_change_request": {
                "description": "Default for sycord.com and POST /api/agent_change — non-blocking",
                "submit": "POST /api/agent_change or POST /sycord/api/agent_change {uuid, message, model_profile?, thinking_level?}",
                "immediate_response": {
                    "ok": True,
                    "request_id": "req_abc123def456",
                    "status": "accepted",
                    "turso_session_id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                    "session_url": "/api/agent_session/b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
                },
                "legacy_sync": "POST with ?wait=1 on /sycord/api/agent_change or POST /api/agent_communicate for blocking reply",
            },
            "sycord_change_flow": [
                "1. User requests code change on sycord.com",
                "2. Prewarm with POST /api/agent_warm {uuid}; the supervisor keeps used agents alive",
                "3. sycord.com POST /sycord/api/agent_change {uuid, message, model_profile} — returns request_id + turso_session_id immediately",
                "4. sycord.com polls GET /sycord/api/agent_session/{turso_session_id} (or the internal route with X-Syra-Internal-Secret) until status != 'open'",
                "5. Each event in session.events is one of request_started -> processing -> [thinking|question|screenshot] -> (tool_call_started/tool_call_finished)* -> request_completed|request_failed|agent_stopped; correlate by payload.request_id",
                "6. The durable request runs serialized per project against the persistent Syte cloud conversation",
                "7. When a question event appears, POST /api/agent_answer_question (or GUI widget) so the turn can continue",
            ],
            "tools": [
                "list_files", "read_file", "write_file", "delete_file", "run_command", "service",
                "update_plan", "screenshot_preview", "inspect_preview", "ask_question", "env_get", "env_set",
                "request_env", "list_mcp_addons", "connect_mcp", "call_mcp", "delegate_task",
            ],
            "code_policy": {
                "any_code": "Agent builds libraries, CLIs, APIs, scripts, backends, mobile, data jobs, or websites — not website-only",
                "websites_require_shadcn": "Website/web UI work must use shadcn/ui + Lucide + Inter + Tailwind per design_contract",
            },
            "turso_sessions": {
                "description": (
                    "Durable, UUID-addressable record of one agent turn, replacing the old SSE "
                    "activity stream. Requires turso_database_url configured in the AI tab; if unset, "
                    "agent_change/agent_communicate still work but no durable session is created."
                ),
                "list": "GET /api/agent_sessions?uuid= — recent session ids for a project, newest first",
                "fetch": "GET /api/agent_session/{session_id}?since_id=0 — session metadata + events; since_id fetches only newer events for polling an 'open' session",
                "local_snapshot": "GET /api/agent_activity?uuid=&since_id=0&session=last — fast local SQLite mirror (not durable across DB moves)",
                "sycord_list": "GET /sycord/api/agent_sessions?uuid=",
                "sycord_fetch": "GET /sycord/api/agent_session/{session_id}",
                "internal_list": "GET /api/internal/projects/{uuid}/agent/sessions",
                "internal_fetch": "GET /api/internal/agent_session/{session_id}",
                "session_fields": ["id", "project_id", "session_number", "model_profile", "status", "created_at", "updated_at", "events"],
                "session_status_values": ["open", "completed", "failed", "cancelled", "stopped"],
                "event_types": [
                    "request_started",
                    "processing",
                    "thinking",
                    "thinking_delta",
                    "token_delta",
                    "screenshot",
                    "question",
                    "question_answered",
                    "tool_call_started",
                    "tool_call_finished",
                    "tool_error",
                    "file_created",
                    "file_modified",
                    "file_deleted",
                    "request_completed",
                    "request_failed",
                    "agent_started",
                    "agent_stopped",
                    "agent_restarted",
                    "session_stopped",
                ],
                "activity_sse": {
                    "endpoint": "GET /api/projects/{uuid}/agent/activity/stream?since_id=0&session=last",
                    "format": "text/event-stream — each frame is `data: {json}\\n\\n`",
                    "note": (
                        "Live activity mirror; disconnect only stops the SSE reader — "
                        "use POST interrupt/stop to cancel the agent turn (DAV-131)."
                    ),
                    "poll_backoff": {
                        "initial_ms": 500,
                        "max_ms": 5000,
                        "strategy": "double after empty polls; reset when events arrive",
                        "since_id": (
                            "Returns events with id > since_id. Empty set when caught up; "
                            "no 410 wrap — clients keep the highest seen id."
                        ),
                    },
                    "payload_marks": {
                        "g": "in progress / green",
                        "d": "done / delivered",
                    },
                    "tool_error_types": [
                        "plan_required",
                        "invalid_arguments",
                        "invalid_pattern",
                        "invalid_path",
                        "unknown_tool",
                        "not_found",
                        "timeout",
                        "search_failed",
                        "tool_failed",
                        "mcp_dispatch_unsupported",
                        "builtin_readonly",
                    ],
                    "tools": {
                        "search_code": (
                            "Ripgrep-style workspace search (pattern, path?, glob?, max_matches?). "
                            "Prefer over unbounded list_files / shell grep."
                        ),
                        "mandatory_plan_gate": (
                            "thinking_level 4–5: first tool must be update_plan "
                            "(error_type=plan_required otherwise)."
                        ),
                    },
                },
                "visual_analyses": {
                    "list": "GET /api/projects/{uuid}/agent/visual_analyses",
                    "fields": [
                        "id", "project_id", "screenshot_id", "score", "summary",
                        "issues", "suggestions", "created_at",
                    ],
                },
                "turn_lifecycle": (
                    "request_started -> processing -> [thinking|thinking_delta|token_delta|question|screenshot] -> "
                    "(tool_call_started -> tool_call_finished)* -> "
                    "request_completed|request_failed|agent_stopped; correlate by payload.request_id"
                ),
            },
            "artifacts": {
                "plans": "GET /api/agent_plans?uuid= — structured steps from update_plan / thinking",
                "screenshots": "GET /api/agent_screenshots?uuid= + image at /api/projects/{uuid}/agent/screenshots/{id}",
                "questions": "GET /api/agent_questions?uuid= ; answer with POST /api/agent_answer_question",
                "stops": "GET /api/agent_stops?uuid= — stopped_at markers for stop/interrupt/cancel",
                "mcp": "GET /api/agent_mcp?uuid= ; register/connect/call/update/disconnect via agent_mcp_* (add/enable/disable/edit)",
                "skills": "GET /api/agent_skills?uuid= ; add via agent_skills_add ; enable/edit via agent_skills_enable/update ; disable/delete via agent_skills_disable/delete",
            },
            "workflow": [
                "1. GET /api/agent_status?uuid= — check agent_status, agent_running, sessions_url",
                "2. POST /api/agent_warm {uuid} when opening a project; returns immediately",
                "3. POST /api/agent_change {uuid, message} — async; save request_id and turso_session_id from response",
                "4. GET /api/agent_session/{turso_session_id} — poll until status != 'open' to see the completed turn (request, plan, tool calls, reply)",
                "5. If a question event appears, POST /api/agent_answer_question before the turn can finish",
                "6. POST /api/agent_settings {uuid, model_profile} — switch profile mid-session",
                "7. GET /api/agent_activity?uuid=&since_id=N — local snapshot if Turso is not configured",
                "8. POST /api/agent_stop {uuid} marks the session stopped in DB (agent_stops + Turso status=stopped)",
            ],
            "status_fields": [
                "agent_status", "agent_running", "agent_warming", "agent_port", "agent_proxy_url",
                "agent_model_profile", "agent_backend", "agent_workspace_path",
                "agent_log_path", "agent_last_error",
            ],
            "internal_routes": {
                "description": "sycord.com server-to-server — X-Syra-Internal-Secret header",
                "base": f"{base}/api/internal" if base else "/api/internal",
                "endpoints": [
                    "GET /projects/{uuid}/agent",
                    "POST /projects/{uuid}/agent/warm",
                    "POST /projects/{uuid}/agent/start",
                    "POST /projects/{uuid}/agent/stop",
                    "POST /projects/{uuid}/agent/restart",
                    "POST /projects/{uuid}/agent/change",
                    "POST /projects/{uuid}/agent/communicate",
                    "POST /projects/{uuid}/agent/test",
                    "GET /projects/{uuid}/agent/logs",
                    "GET /projects/{uuid}/agent/activity",
                    "GET /projects/{uuid}/agent/sessions",
                    "GET /agent_session/{session_id}",
                    "GET|POST /projects/{uuid}/agent/proxy[/{path}]",
                ],
            },
            "errors": {
                "agent_not_running": "Start agent before communicate/proxy",
                "agent_start_failed": "Syte cloud runtime not installed or configuration invalid",
                "backend_unreachable": "Provider API base not reachable",
                "cloud_runtime_not_installed": "Install the project's Python dependencies",
                "internal_secret_not_configured": "Set syra_internal_secret in Settings → Keys",
                "turso_not_configured": "Set turso_database_url (and turso_auth_token) in Settings → AI tab before fetching agent_session/agent_sessions",
            },
        },
        "create_project_response": {
            "description": "AI agents: use execute_command for lint/install only; deploy via issue_deploy.body",
            "fields": {
                "uuid": "project id for all subsequent API calls",
                "design_contract": "GET /api/ai.json → design_contract — mandatory UI rules",
                "system_prompt": "GET /api/ai.json → system_prompt — inject as AI system instruction",
                "execute_command": "scaffolding + npm install + npm run lint ONLY",
                "issue_deploy": "POST /api/issue_deploy — git pull + docker build + start",
                "validate_design": "GET /api/validate_design?uuid= — run after generation",
            },
            "example": {
                "ok": True,
                "uuid": "my-site-a1b2c3",
                "status": "created",
                "execute_command": {
                    "method": "POST",
                    "path": "/api/execute_command",
                    "body": {"uuid": "my-site-a1b2c3", "command": "npm run lint", "cwd": "app", "timeout": 300},
                },
                "issue_deploy": {"method": "POST", "path": "/api/issue_deploy", "body": {"uuid": "my-site-a1b2c3"}},
            },
        },
        "execute_command_examples": [
            {"command": "npm install", "cwd": "app", "purpose": "install dependencies"},
            {"command": "npm run lint", "cwd": "app", "purpose": "bug testing — allowed"},
            {"command": "ls -la", "cwd": "app", "purpose": "inspect files"},
            {"command": "mkdir -p src/components/ui", "cwd": "app", "purpose": "scaffold"},
            {"command": "npx create-next-app@latest . --yes", "cwd": "app", "purpose": "scaffold Next.js"},
        ],
        "execute_command_forbidden": [
            "npm run build",
            "yarn build",
            "pnpm build",
            "next build",
            "Use POST /api/issue_deploy {uuid} for all builds",
        ],
        "sycord_api": {
            "description": (
                "Separate integration API for Sycord websites and external projects. "
                "project_connect returns uuid — your app MUST persist it before any other call."
            ),
            "base_url": f"{base}/sycord/api" if base else "/sycord/api",
            "documentation": f"{base}/sycord/api/" if base else "/sycord/api/",
            "integration_guide": f"{base}/sycord/api/integration.json" if base else "/sycord/api/integration.json",
            "spec": f"{base}/sycord/api/spec.json" if base else "/sycord/api/spec.json",
            "uuid_persistence": {
                "required": True,
                "response_field": "uuid",
                "also_in": ["persist.uuid", "project.uuid", "next_steps.save_uuid"],
                "instruction": "Save uuid in your database after project_connect. Required for upload, preview, issue_deployment, container_get, domain, and agent_* calls.",
                "optional_custom_uuid": "Pass body.uuid on project_connect to use your own id",
            },
            "stacks": ["nextjs", "python", "javascript", "html5"],
            "workflow": [
                "1. POST /sycord/api/project_connect {name, stack} → SAVE response.uuid",
                "2. POST /sycord/api/upload — multipart file upload (uuid required)",
                "3. POST /sycord/api/preview_start {uuid} — fast dev preview with HMR",
                "4. GET /sycord/api/preview_status?uuid= — poll until preview_ready=true",
                "5. POST /sycord/api/agent_change {uuid, message} — async AI code change; returns turso_session_id",
                "6. GET /sycord/api/agent_session/{turso_session_id} — poll the durable Turso session until status != 'open'",
                "7. POST /sycord/api/issue_deployment {uuid} — docker build + deploy",
                "8. GET /sycord/api/container_get?uuid= — container status",
                "9. POST /sycord/api/domain {uuid, domain} — optional custom domain",
            ],
            "agent_integration": {
                "description": "Sycord backend uses same API token as other /sycord/api routes",
                "status": "GET /sycord/api/agent_status?uuid=",
                "submit_change": "POST /sycord/api/agent_change {uuid, message, model_profile?, wait?}",
                "activity_snapshot": "GET /sycord/api/agent_activity?uuid=&since_id=0",
                "sessions_list": "GET /sycord/api/agent_sessions?uuid=",
                "session_fetch": "GET /sycord/api/agent_session/{session_id}?since_id=0 — durable Turso record, no streaming",
                "async_response": {"ok": True, "request_id": "req_…", "status": "accepted", "turso_session_id": "…", "session_url": "/sycord/api/agent_session/…"},
            },
            "project_connect_response": {
                "save": "uuid",
                "example_fields": ["uuid", "persist", "project", "next_steps.save_uuid"],
            },
            "endpoints": [
                {"method": "POST", "path": "/sycord/api/project_connect", "auth": True, "returns_uuid": True},
                {"method": "GET", "path": "/sycord/api/container_get?uuid=", "auth": True},
                {"method": "POST", "path": "/sycord/api/upload", "auth": True},
                {"method": "POST", "path": "/sycord/api/preview_start", "auth": True},
                {"method": "GET", "path": "/sycord/api/preview_status?uuid=", "auth": True},
                {"method": "POST", "path": "/sycord/api/preview_stop", "auth": True},
                {"method": "GET", "path": "/sycord/api/agent_status?uuid=", "auth": True},
                {"method": "POST", "path": "/sycord/api/agent_change", "auth": True, "description": "Async code change — returns request_id + turso_session_id"},
                {"method": "GET", "path": "/sycord/api/agent_activity?uuid=&since_id=", "auth": True},
                {"method": "GET", "path": "/sycord/api/agent_sessions?uuid=", "auth": True},
                {"method": "GET", "path": "/sycord/api/agent_session/{session_id}", "auth": True},
                {"method": "POST", "path": "/sycord/api/domain", "auth": True},
                {"method": "POST", "path": "/sycord/api/issue_deployment", "auth": True},
                {"method": "GET", "path": "/sycord/api/spec.json", "auth": False},
                {"method": "GET", "path": "/sycord/api/integration.json", "auth": False},
            ],
        },
    }
