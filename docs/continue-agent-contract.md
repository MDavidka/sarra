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

## Model bridge

Syte writes a Continue `config.yaml` that points at an **OpenAI-compatible bridge**:

- `syra-nano` -> Gemini Flash
- `syra-base` -> DeepSeek Flash / chat
- `syra-havy` -> Gemini Pro

Configured in Syte settings:

- `continue_bridge_api_base`
- `continue_bridge_api_key`
- `continue_default_model_profile`
- `continue_syra_nano_model`
- `continue_syra_base_model`
- `continue_syra_havy_model`

No Continue Hub is required by default.

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
