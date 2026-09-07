"""Machine-readable API and platform documentation schema generator for Syte.
Accessible at /docs/raw, /docs/raw.json, and /api/docs/raw for AI agents, external integrations, and code generators.
"""

from __future__ import annotations

from typing import Any, Dict, List
__version__ = "1.0.0"


def get_raw_docs_json(base_url: str = "https://sycord.site") -> Dict[str, Any]:
    """Generate structured, AI-understandable JSON documentation of the Syte platform."""
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Syte Cloud & AI Agent Management API",
        "description": "Unified platform API for managing cloud deployments, Docker containers, reverse proxies, environment configurations, and interactive autonomous AI agent streaming turns on Syte.",
        "version": __version__,
        "base_url": base_url.rstrip("/"),
        "documentation_url": f"{base_url.rstrip('/')}/docs",
        "raw_json_url": f"{base_url.rstrip('/')}/docs/raw",
        "authentication": {
            "type": "api_key_or_session",
            "headers": {
                "X-API-Key": {
                    "type": "string",
                    "description": "Operator bootstrap token or scoped API Key for external scripts and agents.",
                    "example": "syra123",
                },
                "Authorization": {
                    "type": "string",
                    "description": "Bearer token authentication.",
                    "example": "Bearer syra123",
                },
                "X-CSRF-Token": {
                    "type": "string",
                    "description": "Required for unsafe browser session mutations.",
                },
            },
            "cookies": {
                "syte_session": "Operator cookie session for browser UI.",
            },
        },
        "concepts": {
            "project_id_uuid": {
                "description": "Unique identifier (UUID or slugified string, e.g. 'test018-c448cf' or 'global') representing an application workspace, deployment target, or container instance.",
                "global_target": "'global' is used for host-wide AI settings or cross-project commands.",
            },
            "session_id_uuid": {
                "description": "Unique Session UUID (e.g. '7f9d8a2b-3c4e-5a6f-8b9c-0d1e2f3a4b5c') used to track, route, isolate, and reconnect to individual concurrent agent streaming turns.",
                "usage": "Passed in request body 'session_id', path parameter /sessions/{session_id}/stream, or returned in 'X-Syte-Session-ID' HTTP response header and event payloads.",
            },
            "sse_streaming": {
                "description": "Streaming endpoints use Server-Sent Events (text/event-stream) to broadcast real-time thoughts, tokens, tool invocations, and completion states.",
                "event_types": [
                    {"event": "user_message_received", "description": "Acknowledges incoming prompt with session_id."},
                    {"event": "status", "description": "Current agent action or model thinking state."},
                    {"event": "thought_delta", "description": "Streamed reasoning / CoT token chunk."},
                    {"event": "thought", "description": "Full accumulated reasoning block."},
                    {"event": "token", "description": "Streamed assistant response token chunk."},
                    {"event": "tool_call_start", "description": "Autonomous tool invocation initiation with name and arguments."},
                    {"event": "tool_call_result", "description": "Result of executed tool (e.g., file edit, shell command)."},
                    {"event": "done", "description": "Final turn completion containing assistant text and status."},
                    {"event": "error", "description": "Execution failure or timeout detail."},
                    {"event": "stopped", "description": "Turn canceled by operator request."},
                ],
            },
        },
        "endpoints": [
            # AI Agent & Streaming APIs with UUID session support
            {
                "group": "AI Agent & Streaming",
                "method": "POST",
                "path": "/api/projects/{project_id}/ai/sessions/{session_id}/stream",
                "aliases": ["/api/projects/{project_id}/ai/stream", "/api/ai/stream"],
                "summary": "Stream agent messages and thoughts by Project & Session UUID",
                "description": "Initiates an agent turn and streams Server-Sent Events (SSE) including thought traces, tokens, tool calls, and completion status. Explicit session UUID isolation ensures clients only stream their own session.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "type": "string",
                        "description": "Target project UUID or 'global'.",
                        "example": "test018-c448cf",
                    },
                    {
                        "name": "session_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                        "description": "Unique Session UUID for isolated conversation turn.",
                        "example": "7f9d8a2b-3c4e-5a6f-8b9c-0d1e2f3a4b5c",
                    },
                ],
                "request_body": {
                    "type": "object",
                    "required": ["message"],
                    "properties": {
                        "message": {"type": "string", "description": "Prompt or instruction for the AI agent."},
                        "session_id": {"type": "string", "description": "Optional session UUID in payload."},
                        "provider": {"type": "string", "description": "Target LLM provider (openai, anthropic, gemini, deepseek, openrouter, ollama, custom)."},
                        "model": {"type": "string", "description": "Model ID to execute (e.g. gpt-4o, glm-5.3-flash, deepseek-chat)."},
                        "temperature": {"type": "number", "minimum": 0.0, "maximum": 2.0, "description": "Sampling temperature."},
                        "max_tokens": {"type": "integer", "description": "Token limit for model response."},
                        "system_prompt": {"type": "string", "description": "Custom system instructions overriding defaults."},
                        "stream_tokens_only": {"type": "boolean", "default": False, "description": "When true, yields only token chunks and [DONE]."},
                        "execution_speed": {"type": "string", "enum": ["ultra_fast", "balanced", "deep_reasoning"]},
                    },
                },
                "responses": {
                    "200": {
                        "content_type": "text/event-stream",
                        "headers": {
                            "X-Syte-Session-ID": "Assigned or requested Session UUID",
                            "X-Syte-Project-ID": "Target Project UUID",
                        },
                        "description": "Continuous stream of Server-Sent Events tagged with session UUID.",
                    },
                    "401": {"description": "Unauthorized — missing or invalid operator token."},
                    "404": {"description": "Project UUID not found."},
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "POST",
                "path": "/api/projects/{project_id}/ai/sessions/{session_id}/cancel",
                "aliases": ["/api/projects/{project_id}/ai/cancel", "/api/ai/cancel"],
                "summary": "Cancel active agent turn for specific Session UUID",
                "description": "Terminates ongoing LLM generation or background tool execution for the specified project and session UUID.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "type": "string",
                        "description": "Project UUID or 'global'.",
                    },
                    {
                        "name": "session_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                        "description": "Optional specific session UUID.",
                    },
                ],
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": {"ok": True, "stopped": True, "session_id": "7f9d8a2b...", "message": "Session stopped"},
                    }
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "GET",
                "path": "/api/projects/{project_id}/ai/sessions/{session_id}",
                "aliases": ["/api/projects/{project_id}/ai/session"],
                "summary": "Get session state, plan, and question gates by Session UUID",
                "description": "Returns session turn count, running status, active plan, and pending user input questions.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "type": "string",
                    },
                    {
                        "name": "session_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                    },
                ],
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": {
                            "ok": True,
                            "session": {
                                "session_id": "7f9d8a2b-3c4e-5a6f-8b9c-0d1e2f3a4b5c",
                                "project_id": "test018-c448cf",
                                "is_running": True,
                                "current_turn": 2,
                            },
                        },
                    }
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "GET",
                "path": "/api/projects/{project_id}/ai/models",
                "aliases": ["/api/ai/models"],
                "summary": "List available AI models",
                "description": "Queries provider APIs dynamically (OpenAI, OpenRouter, DeepSeek, Ollama, Gemini) to return available models.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                        "default": "global",
                        "description": "Project UUID to use project-specific credentials.",
                    },
                    {
                        "name": "provider",
                        "in": "query",
                        "required": False,
                        "type": "string",
                        "description": "Optional provider filter.",
                    },
                ],
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": {
                            "ok": True,
                            "provider": "custom",
                            "current_model": "glm-5.3-flash",
                            "models": [{"id": "glm-5.3-flash", "name": "glm-5.3-flash", "context_window": 32000}],
                            "count": 1,
                        },
                    }
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "GET",
                "path": "/api/projects/{project_id}/ai/settings",
                "aliases": ["/api/ai/settings"],
                "summary": "Get AI agent configuration",
                "description": "Fetches current model, provider, masked credentials, speed profile, and prompt rules.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                        "default": "global",
                    }
                ],
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": {
                            "ok": True,
                            "settings": {
                                "project_id": "global",
                                "provider": "custom",
                                "model": "glm-5.3-flash",
                                "api_key_masked": "sk-1••••••••q03w",
                                "execution_speed": "balanced",
                            },
                        },
                    }
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "PUT",
                "path": "/api/projects/{project_id}/ai/settings",
                "aliases": ["/api/ai/settings"],
                "summary": "Update AI agent configuration",
                "description": "Updates provider, model, API keys, temperature, intelligence level, or custom system prompts.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": False,
                        "type": "string",
                        "default": "global",
                    }
                ],
                "request_body": {
                    "type": "object",
                    "properties": {
                        "provider": {"type": "string"},
                        "model": {"type": "string"},
                        "api_key": {"type": "string"},
                        "base_url": {"type": "string"},
                        "temperature": {"type": "number"},
                        "max_tokens": {"type": "integer"},
                        "system_prompt": {"type": "string"},
                        "execution_speed": {"type": "string"},
                        "intelligence_level": {"type": "string"},
                    },
                },
                "responses": {
                    "200": {"content_type": "application/json", "example": {"ok": True, "settings": {}}},
                },
            },
            {
                "group": "AI Agent & Streaming",
                "method": "GET",
                "path": "/api/projects/{project_id}/ai/history",
                "summary": "Retrieve project AI conversation history",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "AI Agent & Streaming",
                "method": "DELETE",
                "path": "/api/projects/{project_id}/ai/history",
                "summary": "Clear project AI conversation history and reset session",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Project & UUID Management APIs
            {
                "group": "Projects & UUID Management",
                "method": "GET",
                "path": "/api/projects",
                "summary": "List all projects with UUIDs and runtime status",
                "description": "Returns full list of deployed projects including UUID (id), port, domain, preview URL, git metadata, and SSL details.",
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": [
                            {
                                "id": "test018-c448cf",
                                "name": "test018",
                                "port": 3002,
                                "status": "running",
                                "preview_url": "https://previewa-test018.sycord.site",
                                "url": "https://test018.sycord.site",
                            }
                        ],
                    }
                },
            },
            {
                "group": "Projects & UUID Management",
                "method": "GET",
                "path": "/api/projects/{project_id}",
                "summary": "Get specific project details by UUID",
                "description": "Retrieves comprehensive configuration, container state, preview health, and TLS configuration for a single project UUID.",
                "parameters": [
                    {
                        "name": "project_id",
                        "in": "path",
                        "required": True,
                        "type": "string",
                        "description": "Unique Project UUID / ID.",
                        "example": "test018-c448cf",
                    }
                ],
                "responses": {
                    "200": {"content_type": "application/json", "description": "Enriched project payload."},
                    "404": {"description": "Project UUID not found."},
                },
            },
            {
                "group": "Projects & UUID Management",
                "method": "POST",
                "path": "/api/projects",
                "summary": "Create or deploy new project",
                "description": "Provisions a new service container, allocates an isolated port, creates workspace, and configures proxy routing.",
                "request_body": {
                    "type": "object",
                    "required": ["name"],
                    "properties": {
                        "name": {"type": "string", "example": "my-web-app"},
                        "git_url": {"type": "string", "example": "https://github.com/org/repo.git"},
                        "branch": {"type": "string", "default": "main"},
                        "start_command": {"type": "string", "example": "npm start"},
                        "env_vars": {"type": "object"},
                        "domain": {"type": "string"},
                    },
                },
                "responses": {
                    "200": {"content_type": "application/json", "description": "Created project with assigned UUID and port."},
                },
            },
            {
                "group": "Projects & UUID Management",
                "method": "POST",
                "path": "/api/projects/{project_id}/start",
                "summary": "Start project container/process",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Projects & UUID Management",
                "method": "POST",
                "path": "/api/projects/{project_id}/stop",
                "summary": "Stop project container/process",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Projects & UUID Management",
                "method": "POST",
                "path": "/api/projects/{project_id}/restart",
                "summary": "Restart project container/process",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Projects & UUID Management",
                "method": "POST",
                "path": "/api/projects/{project_id}/update",
                "summary": "Pull latest Git commits and restart project",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Projects & UUID Management",
                "method": "DELETE",
                "path": "/api/projects/{project_id}",
                "summary": "Delete project and release assigned ports",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Environment & Secrets APIs
            {
                "group": "Environment Variables & Secrets",
                "method": "PUT",
                "path": "/api/projects/{project_id}/environment",
                "summary": "Set or update project environment variable",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "request_body": {
                    "type": "object",
                    "required": ["key", "value"],
                    "properties": {
                        "key": {"type": "string", "example": "DATABASE_URL"},
                        "value": {"type": "string", "example": "postgresql://..."},
                        "original_key": {"type": "string"},
                    },
                },
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Environment Variables & Secrets",
                "method": "DELETE",
                "path": "/api/projects/{project_id}/environment/{key}",
                "summary": "Delete an environment variable",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "key", "in": "path", "required": True, "type": "string"},
                ],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Builds & Deployment Logs
            {
                "group": "Builds & Logs",
                "method": "POST",
                "path": "/api/projects/{project_id}/builds/trigger",
                "summary": "Trigger a manual build & deploy run",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Builds & Logs",
                "method": "POST",
                "path": "/api/projects/{project_id}/deployments/cancel",
                "aliases": ["/api/projects/{project_id}/builds/cancel", "/api/deploy_cancel"],
                "summary": "Cancel active build or in-flight deployment",
                "description": "Aborts running docker build process or background deploy task for the specified project.",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "example": {"ok": True, "project_id": "test018-c448cf", "message": "Deploy cancellation requested."},
                    }
                },
            },
            {
                "group": "Builds & Logs",
                "method": "GET",
                "path": "/api/projects/{project_id}/logs/stream",
                "summary": "Stream live build and runtime logs (SSE)",
                "description": "Real-time follow for deployment and container stdout/stderr output.",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "live", "in": "query", "type": "boolean", "default": False},
                ],
                "responses": {"200": {"content_type": "text/event-stream"}},
            },
            {
                "group": "Builds & Logs",
                "method": "GET",
                "path": "/api/projects/{project_id}/logs",
                "summary": "Fetch tail lines of container runtime logs",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "lines", "in": "query", "type": "integer", "default": 500},
                ],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Workspace File Management
            {
                "group": "Workspace Files",
                "method": "GET",
                "path": "/api/projects/{project_id}/workspace/files",
                "summary": "List files and directories in project workspace",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "path", "in": "query", "type": "string", "default": ""},
                ],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Workspace Files",
                "method": "GET",
                "path": "/api/projects/{project_id}/workspace/file",
                "summary": "Read file contents from workspace",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "path", "in": "query", "required": True, "type": "string"},
                ],
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Workspace Files",
                "method": "POST",
                "path": "/api/projects/{project_id}/workspace/file",
                "summary": "Write or update file content in workspace",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "request_body": {
                    "type": "object",
                    "required": ["path", "content"],
                    "properties": {
                        "path": {"type": "string", "example": "src/index.js"},
                        "content": {"type": "string", "example": "console.log('Hello');"},
                    },
                },
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Workspace Files",
                "method": "DELETE",
                "path": "/api/projects/{project_id}/workspace/file",
                "summary": "Delete file or directory from workspace",
                "parameters": [
                    {"name": "project_id", "in": "path", "required": True, "type": "string"},
                    {"name": "path", "in": "query", "required": True, "type": "string"},
                ],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Domains & Proxy
            {
                "group": "Domains & Routing",
                "method": "POST",
                "path": "/api/projects/{project_id}/domain",
                "summary": "Attach custom domain and provision SSL",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "request_body": {
                    "type": "object",
                    "required": ["domain"],
                    "properties": {
                        "domain": {"type": "string", "example": "app.example.com"},
                        "email": {"type": "string", "example": "admin@example.com"},
                    },
                },
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "Domains & Routing",
                "method": "DELETE",
                "path": "/api/projects/{project_id}/domain",
                "summary": "Detach custom domain from project",
                "parameters": [{"name": "project_id", "in": "path", "required": True, "type": "string"}],
                "responses": {"200": {"content_type": "application/json"}},
            },

            # Server & System Info
            {
                "group": "System & Swarm",
                "method": "GET",
                "path": "/api/system",
                "summary": "Get host hardware utilization and node stats",
                "description": "Returns CPU, RAM, Disk, active service count, direct URL, and public IP.",
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "System & Swarm",
                "method": "GET",
                "path": "/api/tokens",
                "summary": "List configured API access tokens",
                "responses": {"200": {"content_type": "application/json"}},
            },
            {
                "group": "System & Observability",
                "method": "GET",
                "path": "/de/fetch",
                "aliases": ["/debug", "/api/debug", "/api/de/fetch"],
                "summary": "10-minute server activity, error diagnostics, and VM hardware telemetry (JSON)",
                "description": "Returns sanitized JSON telemetry for the last 10 minutes: failed, stale, malformed, bots, suspicion, correct, too many data, VM CPU/RAM/Disk stats, login attempts, and resource utilization warnings. All environment variable names, credentials, and sensitive parameters are masked with [red].",
                "responses": {
                    "200": {
                        "content_type": "application/json",
                        "description": "Sanitized JSON telemetry report with [red] maskings.",
                    }
                },
            },
        ],
    }
