# Syte OpenCode Agent Contract

Syte hosts a **persistent [OpenCode](https://github.com/anomalyco/opencode) `opencode serve` runtime per project/workspace** so `sycord.com` can use coding-agent capabilities without owning the VM/runtime lifecycle.

## Runtime model

- **One OpenCode agent per Syte project**
- Runs inside the same workspace Syte already manages
- Backed by project-local files under:
  - `workspaces/<uuid>/data/opencode/opencode.json`
  - `workspaces/<uuid>/data/opencode/home/.config/opencode/`
  - `workspaces/<uuid>/data/opencode/serve.log`
- Uses a Syte-managed localhost port from `5200-5999`
- OpenAPI spec at `http://127.0.0.1:<port>/doc`

Install on the VM: `npm install -g opencode-ai`

## Model providers

Syte writes an OpenCode `opencode.json` with custom OpenAI-compatible providers per profile:

- `syra-nano` → **Verted** (Gemini Flash) — `https://generativelanguage.googleapis.com/v1beta/openai`
- `syra-base` → **DeepSeek** — `https://api.deepseek.com/v1`
- `syra-havy` → **Verted** (Gemini Pro) — `https://generativelanguage.googleapis.com/v1beta/openai`

Configured in Syte GUI → AI tab (settings keys unchanged for compatibility):

- `continue_syra_nano_api_key`
- `continue_syra_base_api_key`
- `continue_syra_havy_api_key`
- `continue_default_model_profile`
- `continue_mcp_servers` — JSON array → `opencode.json` `mcp` block (`type: local`, `command` array)
- `continue_rules` — one per line → `opencode.json` `instructions`
- `agent_max_count` — max concurrent AI agents (MNOA)
- `syra_internal_secret`

Each key is injected as `SYRA_NANO_API_KEY`, `SYRA_BASE_API_KEY`, or `SYRA_HAVY_API_KEY` via `{env:VAR}` in config and process environment.

## OpenCode HTTP API (via proxy)

| Method | Path | Description |
| --- | --- | --- |
| `GET` | `/global/health` | Server health |
| `POST` | `/session` | Create session |
| `GET` | `/session/status` | Busy/idle per session |
| `GET` | `/session/:id/message` | List messages + parts |
| `POST` | `/session/:id/message` | Send prompt (sync) |
| `POST` | `/session/:id/prompt_async` | Send prompt (async) |
| `GET` | `/event` | SSE event stream |

Syte `communicate_with_agent` uses `POST /session/:id/message` with:

```json
{
  "parts": [{"type": "text", "text": "..."}],
  "model": {"providerID": "syra-base", "modelID": "deepseek-chat"}
}
```

## Internal auth

Sycord server-to-server calls authenticate with:

- Header: `X-Syra-Internal-Secret: <secret>`
- Or: `Authorization: Bearer <secret>`

## Internal endpoints for sycord.com

Root: `/api/internal`

### Agent status

`GET /api/internal/projects/{project_id}/agent`

Returns `agent_engine: "opencode"`, port, proxy URL, session id, model profile, backend health.

### Lifecycle

- `POST /api/internal/projects/{project_id}/agent/start`
- `POST /api/internal/projects/{project_id}/agent/stop`
- `POST /api/internal/projects/{project_id}/agent/restart`

### Communicate / change

- `POST /api/internal/projects/{project_id}/agent/communicate`
- `POST /api/internal/projects/{project_id}/agent/change` — sycord.com user change requests

### Activity feed

- `GET /api/internal/projects/{project_id}/agent/activity?since_id=0`
- `GET /api/internal/projects/{project_id}/agent/activity/stream?live=1` (SSE)

### Proxy

`/api/internal/projects/{project_id}/agent/proxy[/{path}]` — forward to OpenCode HTTP API.

## Notes

- OpenCode agent is independent from preview/deploy processes.
- Agent reuses the same project workspace and env vars as Syte.
- Optional `OPENCODE_SERVER_PASSWORD` enables HTTP basic auth on the local server.
