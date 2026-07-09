"""Integration contract for sycord.com → Syte Continue cloud agent."""

from syte import __version__
from syte.agent_config import DEFAULT_MODEL, SYRA_MODELS


def build_agent_integration(base_url: str = "") -> dict:
    """Machine-readable contract — what sycord.com should call on Syte."""
    base = base_url.rstrip("/")
    api = f"{base}/api" if base else "/api"
    sycord_api = f"{base}/sycord/api" if base else "/sycord/api"

    return {
        "name": "Syte Continue Cloud Agent",
        "version": __version__,
        "description": (
            "Syte runs a long-lived `cn serve` process per project workspace. "
            "sycord.com controls and proxies to it via token + optional bridge secret."
        ),
        "architecture": {
            "sycord_com": "User-facing Syra chat UI and AI entrypoint",
            "syte": "VM/workspace backend — runs cn serve, exposes control + proxy APIs",
            "continue_hub_required": False,
            "model_backend": "Sycord OpenAI-compatible bridge (Gemini / DeepSeek)",
        },
        "authentication": {
            "primary": {
                "type": "api_key",
                "header": "X-API-Key",
                "alternative": "Authorization: Bearer <syte_api_token>",
            },
            "bridge": {
                "type": "shared_secret",
                "header": "X-Sycord-Bridge-Secret",
                "setting": "sycord_bridge_secret",
                "description": (
                    "Optional second factor for agent proxy/control from sycord.com. "
                    "Accepts either valid Syte API token OR matching bridge secret."
                ),
            },
        },
        "models": {
            "default": DEFAULT_MODEL,
            "available": {
                name: {"backend": desc} for name, desc in SYRA_MODELS.items()
            },
            "bridge_url_setting": "sycord_ai_bridge_url",
            "bridge_url_default": "https://sycord.com/api/ai/bridge/v1",
        },
        "lifecycle": {
            "port_range": "5000-5999",
            "idle_timeout_seconds": 86400,
            "config_path": "workspaces/{uuid}/.continue/config.yaml",
            "log_path": "workspaces/{uuid}/agent.log",
            "pid_path": "/var/lib/syte/pids/{uuid}.agent.pid",
            "workspace_cwd": "workspaces/{uuid}/app",
        },
        "workflow": [
            "1. Ensure project exists (project_connect or create_project) — save uuid",
            "2. POST /sycord/api/agent_start {uuid, model?} — start cn serve",
            "3. GET /sycord/api/agent_status?uuid= — poll until agent_ready=true",
            "4. Proxy Syra traffic: GET/POST /api/projects/{uuid}/agent/proxy/* → local cn serve",
            "5. GET /api/projects/{uuid}/agent/logs or SSE stream for debugging",
            "6. POST /sycord/api/agent_stop {uuid} when session ends",
        ],
        "continue_endpoints": {
            "description": "Proxied to local cn serve when agent_ready=true",
            "paths": {
                "GET /state": "Agent state snapshot",
                "POST /message": "Send message {message: string}",
                "POST /permission": "Approve/reject tool {requestId, approved}",
                "POST /pause": "Pause current run",
                "GET /diff": "Git diff vs main",
                "POST /exit": "Graceful shutdown",
            },
        },
        "syte_api_endpoints": [
            {"method": "POST", "path": f"{api}/start_agent", "body": {"uuid": "str", "model": "optional"}},
            {"method": "POST", "path": f"{api}/stop_agent", "body": {"uuid": "str"}},
            {"method": "POST", "path": f"{api}/restart_agent", "body": {"uuid": "str", "model": "optional"}},
            {"method": "GET", "path": f"{api}/agent_status?uuid=", "description": "Status + bridge health"},
            {"method": "GET", "path": f"{api}/agent_logs?uuid=&lines=200"},
            {"method": "GET", "path": f"{api}/projects/{{uuid}}/agent/logs/stream?live=1", "auth": "optional"},
            {"method": "GET", "path": f"{api}/projects/{{uuid}}/agent/proxy/{{path}}", "auth": "bridge_or_token"},
            {"method": "POST", "path": f"{api}/projects/{{uuid}}/agent/proxy/{{path}}", "auth": "bridge_or_token"},
            {"method": "GET", "path": f"{api}/agent_integration.json", "auth": False},
        ],
        "sycord_api_endpoints": [
            {"method": "POST", "path": f"{sycord_api}/agent_start", "body": {"uuid": "str", "model": "optional"}},
            {"method": "POST", "path": f"{sycord_api}/agent_stop", "body": {"uuid": "str"}},
            {"method": "POST", "path": f"{sycord_api}/agent_restart", "body": {"uuid": "str", "model": "optional"}},
            {"method": "GET", "path": f"{sycord_api}/agent_status?uuid="},
            {"method": "GET", "path": f"{sycord_api}/agent_integration.json", "auth": False},
        ],
        "response_fields": [
            "agent_running", "agent_ready", "agent_port", "agent_status",
            "agent_model", "agent_models", "agent_started_at", "agent_error",
            "agent_proxy_url", "agent_state_url", "agent_message_url",
            "agent_stream_url", "bridge_reachable", "bridge_message",
        ],
        "settings": {
            "sycord_ai_bridge_url": "OpenAI-compatible base URL for Continue models",
            "sycord_bridge_secret": "Shared secret — sent as Bearer / X-Sycord-Bridge-Secret",
        },
    }
