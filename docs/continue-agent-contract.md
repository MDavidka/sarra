# Syte Continue Agent Contract

Syte hosts a **persistent Continue `cn serve` runtime per project/workspace** so `sycord.com` can use coding-agent capabilities without owning the VM/runtime lifecycle.

## Runtime model

- **One Continue agent per Syte project**
- Runs inside the same workspace Syte already manages
- Backed by project-local files under:
  - `workspaces/<uuid>/data/continue/config.yaml`
  - `workspaces/<uuid>/data/continue/home/`
  - `workspaces/<uuid>/data/continue/serve.log`
- Uses a Syte-managed localhost port from `5200-5999`

## Model providers

Syte writes a Continue `config.yaml` with fixed OpenAI-compatible endpoints per profile:

- `syra-nano` -> **Verted** (Gemini Flash) — `https://generativelanguage.googleapis.com/v1beta/openai`
- `syra-base` -> **DeepSeek** — `https://api.deepseek.com/v1`
- `syra-havy` -> **Verted** (Gemini Pro) — `https://generativelanguage.googleapis.com/v1beta/openai`

Configured in Syte GUI → AI tab (settings sheet):

- `continue_syra_nano_api_key`
- `continue_syra_base_api_key`
- `continue_syra_havy_api_key`
- `continue_default_model_profile`
- `syra_internal_secret`

Each key is injected as `SYRA_NANO_API_KEY`, `SYRA_BASE_API_KEY`, or `SYRA_HAVY_API_KEY` on the VM.

## Internal auth

Sycord server-to-server calls authenticate with:

- Header: `X-Syra-Internal-Secret: <secret>`
- Or: `Authorization: Bearer <secret>`

Configured in Syte settings as:

- `syra_internal_secret`

## Internal endpoints for sycord.com

All internal endpoints are rooted at:

`/api/internal`

### Agent discovery / status

`GET /api/internal/projects/{project_id}/agent`

Returns:

- running/stopped/starting/error
- allocated port
- stable proxy URL
- workspace/log/config paths
- selected model profile + bridge target
- backend reachability
- last started time
- last error

### Lifecycle control

- `POST /api/internal/projects/{project_id}/agent/start`
- `POST /api/internal/projects/{project_id}/agent/stop`
- `POST /api/internal/projects/{project_id}/agent/restart`

### Logs

`GET /api/internal/projects/{project_id}/agent/logs?lines=200`

### Activity feed (real-time, for sycord.com UI)

Cursor-like structured events — thinking, file edits, commands, chat messages.

**Snapshot (poll):**

`GET /api/internal/projects/{project_id}/agent/activity?since_id=0&limit=200`

**Live stream (SSE):**

`GET /api/internal/projects/{project_id}/agent/activity/stream?live=1&since_id=0`

Auth: `X-Syra-Internal-Secret`

SSE payload:

```json
{"type": "activity", "event": {"id": 1, "event_type": "user_message", "role": "user", "title": "User", "detail": "...", "source": "sycord", "created_at": "..."}}
```

**event_type values:** `user_message`, `assistant_message`, `thinking`, `tool_call`, `command_run`, `file_created`, `file_modified`, `file_deleted`, `file_read`, `request_started`, `request_completed`, `request_failed`, `agent_started`, `agent_stopped`, `processing`

**sycord.com integration:**

1. Open `EventSource` on `/api/internal/projects/{uuid}/agent/activity/stream?live=1` when user opens chat
2. `POST /api/internal/projects/{uuid}/agent/change` with user message
3. Render each `activity` event in the chat UI (group by request)
4. On reconnect, pass `since_id` from last received event id

Public API equivalents (X-API-Key): `GET /api/agent_activity?uuid=`, `GET /api/projects/{uuid}/agent/activity/stream?live=1`

Full docs: `GET /api/#agent-activity`

### Stable proxy to Continue HTTP service

- `GET /api/internal/projects/{project_id}/agent/proxy`
- `POST /api/internal/projects/{project_id}/agent/proxy`
- `GET /api/internal/projects/{project_id}/agent/proxy/{path}`
- `POST /api/internal/projects/{project_id}/agent/proxy/{path}`

Sycord should use the **proxy URL** returned by agent status rather than connecting to the raw localhost/port.

## Admin/debug endpoints in Syte

These exist for the Syte admin surface and token-authenticated automation:

- `GET /api/projects/{project_id}/agent`
- `POST /api/projects/{project_id}/agent/start`
- `POST /api/projects/{project_id}/agent/stop`
- `POST /api/projects/{project_id}/agent/restart`
- `GET /api/projects/{project_id}/agent/logs`
- `GET /api/projects/{project_id}/agent/logs/stream?live=1`
- `GET /api/agent_status?uuid=...`
- `POST /api/agent_start`
- `POST /api/agent_stop`
- `POST /api/agent_restart`
- `GET /api/agent_logs?uuid=...`
- `POST /api/agent_settings`

## Notes

- The Continue agent is independent from preview/deploy processes.
- Preview/dev server HMR and deploy runtime remain unchanged.
- The Continue agent reuses the same project workspace and env vars as Syte.
