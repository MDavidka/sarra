# Agent API

The GUI agent endpoints are project-scoped and use the same session as the chat panel.
The same MCP and skills management is also available on the token API (`X-API-Key` /
`Authorization: Bearer`) under `/api/agent_*` — see [Token API mirrors](#token-api-mirrors)
and the HTML docs at `/api/`.

MCP providers and skills can be **listed, added, enabled, disabled, and edited** from the
agent chat resource panel or directly via these APIs.

For SSE event schemas (`token_delta`, `thinking_delta`, tool lifecycle, questions),
ordering guarantees, `since_id` incremental polling, and reconnection, see
[Agent Streaming API](./agent-streaming-api.md).

## Chat

### POST `/api/projects/{project_id}/agent/chat`

Start an agent turn. `thinking_level` accepts `1` (Instant) through `5` (Max).
Temperature / top_p apply to all providers; native thinking budgets apply **only**
when the selected model/provider supports them (Instant/Fast never send
`thinking` / `reasoning_effort`). Deep/Max (`thinking_level` 4–5) enforce a hard
plan gate: Deep/Max starts with `update_plan` (or a site planner seed). Substantive website work
uses a stricter clarification-or-plan gate: `ask_question` first when a material design choice is
missing, then `update_plan`; otherwise planning starts immediately before inspection or edits.

Built-in agent tools include `search_code` (ripgrep / Python fallback) for
workspace text search — prefer it over unbounded `list_files` / shell grep.

```json
{
  "message": "Review the landing page spacing",
  "model_profile": "syra-base",
  "thinking_level": 3,
  "improve_from_screenshot": false,
  "visual_analysis_id": null
}
```

Optional visual feedback fields:

- `improve_from_screenshot` — attach the latest visual analysis as primary critique
- `visual_analysis_id` — attach a specific analysis id

Streaming event schemas, `tool_error` codes, poll backoff, and visual analysis
response shapes: [Agent Streaming API](./agent-streaming-api.md).

## MCP connections

Manage Model Context Protocol providers per project. The built-in `syte` addon maps to
project `service` / `access` helpers. The built-in `web_search` addon searches the web
(Tavily/Brave when configured, otherwise DuckDuckGo Instant Answer). Custom stdio providers can be registered and connected from the GUI or API.
Connecting a custom addon boots the process briefly (`initialize` + `tools/list`)
and rejects broken providers with `status: error` instead of advertising placeholder
tools.

| Action | Method | Path |
|--------|--------|------|
| List | `GET` | `/api/projects/{project_id}/agent/mcp` |
| Add (register) | `POST` | `/api/projects/{project_id}/agent/mcp` |
| Enable (connect) | `POST` | `/api/projects/{project_id}/agent/mcp/connect` |
| Call tool | `POST` | `/api/projects/{project_id}/agent/mcp/call` |
| Disable (disconnect) | `DELETE` | `/api/projects/{project_id}/agent/mcp/{addon_id}` |
| Edit registration | `PUT` | `/api/projects/{project_id}/agent/mcp/{addon_id}` |

### GET `/api/projects/{project_id}/agent/mcp`

List built-in and registered MCP providers, including connection status and discovered tools.

### POST `/api/projects/{project_id}/agent/mcp`

Register (add) a stdio provider.

```json
{
  "name": "playwright",
  "command": "npx",
  "args": ["playwright-mcp"],
  "env": {},
  "description": "optional",
  "transport": "stdio"
}
```

### POST `/api/projects/{project_id}/agent/mcp/connect`

Connect (enable) a provider by its `addon` id or name.

```json
{
  "addon": "playwright"
}
```

### POST `/api/projects/{project_id}/agent/mcp/call`

Invoke a tool on a connected addon.

```json
{
  "addon": "syte",
  "tool": "syte_service",
  "arguments": { "action": "status" }
}
```

### PUT `/api/projects/{project_id}/agent/mcp/{addon_id}`

Edit a registered (non-builtin) provider's `name`, `description`, `command`, `args`,
`env`, or `transport`. Builtin `syte` cannot be edited.

```json
{
  "command": "npx",
  "args": ["-y", "@playwright/mcp@latest"],
  "description": "Updated Playwright MCP"
}
```

### DELETE `/api/projects/{project_id}/agent/mcp/{addon_id}`

Disconnect (disable) a provider without removing its registration.

## Skills

Per-project skill catalog: built-in skills plus custom skills you add. Active skills inject
guidance into the agent system instruction. Manage from the chat Skills panel or API.

| Action | Method | Path |
|--------|--------|------|
| List | `GET` | `/api/projects/{project_id}/agent/skills` |
| Add (custom) | `POST` | `/api/projects/{project_id}/agent/skills` |
| Enable / edit parameters | `POST` | `/api/projects/{project_id}/agent/skills/{skill_id}/enable` |
| Edit custom skill | `PUT` | `/api/projects/{project_id}/agent/skills/{skill_id}` |
| Disable | `DELETE` | `/api/projects/{project_id}/agent/skills/{skill_id}` |
| Delete custom skill | `DELETE` | `/api/projects/{project_id}/agent/skills/{skill_id}?purge=1` |

Built-in skill ids: `website-editing`, `workspace-search`, `preview-access`,
`service-management`, `nextjs-app-router`, `cli-tools`.

### GET `/api/projects/{project_id}/agent/skills`

List built-in and custom skills with active state / parameters. Custom entries include
`custom: true` and `content`.

### POST `/api/projects/{project_id}/agent/skills`

Add a custom skill. Defaults to enabling it immediately (`enable: true`).

```json
{
  "name": "Brand voice",
  "description": "Keep copy terse and product-led",
  "content": "Prefer short sentences. Never invent feature claims.",
  "enable": true,
  "parameters": {}
}
```

### POST `/api/projects/{project_id}/agent/skills/{skill_id}/enable`

Enable a built-in or custom skill. Sending `parameters` upserts string key/value settings.

```json
{
  "parameters": {
    "theme": "bold"
  }
}
```

### PUT `/api/projects/{project_id}/agent/skills/{skill_id}`

Edit a custom skill's `name`, `description`, `content`, and/or `parameters`.
Built-in skills cannot be edited this way (use enable with parameters).

```json
{
  "content": "Updated guidance for the agent.",
  "description": "Revised description"
}
```

### DELETE `/api/projects/{project_id}/agent/skills/{skill_id}`

Disable a project skill (removes the active row; catalog entry remains).

### DELETE `/api/projects/{project_id}/agent/skills/{skill_id}?purge=1`

Delete a custom skill definition entirely (also clears activation). Built-ins cannot be purged.

## Token API mirrors

Authenticate with `X-API-Key: syte_…` or `Authorization: Bearer syte_…`.

### MCP

| Action | Endpoint |
|--------|----------|
| List | `GET /api/agent_mcp?uuid=` |
| Add | `POST /api/agent_mcp_register` |
| Enable | `POST /api/agent_mcp_connect` |
| Call | `POST /api/agent_mcp_call` |
| Edit | `POST /api/agent_mcp_update` |
| Disable | `POST /api/agent_mcp_disconnect` |

Register body: `{ "uuid", "name", "command", "args?", "env?", "description?", "transport?" }`  
Connect / disconnect / call: `{ "uuid", "addon", … }`  
Update: `{ "uuid", "addon", "name?", "command?", "args?", "env?", "description?", "transport?" }`

Built-in MCP tools (`syte` / `web_search`) validate arguments before dispatch.
Invalid shapes return `{ "ok": false, "error": "invalid_arguments", "message": "…" }`
without executing the underlying action.

### Skills

| Action | Endpoint |
|--------|----------|
| List | `GET /api/agent_skills?uuid=` |
| Add | `POST /api/agent_skills_add` |
| Enable / edit params | `POST /api/agent_skills_enable` |
| Edit custom | `POST /api/agent_skills_update` |
| Disable | `POST /api/agent_skills_disable` |
| Delete custom | `POST /api/agent_skills_delete` |

```json
{
  "uuid": "my-site-a1b2c3",
  "name": "Brand voice",
  "content": "Prefer short sentences.",
  "description": "optional",
  "enable": true
}
```

```json
{ "uuid": "my-site-a1b2c3", "skill_id": "website-editing", "parameters": { "theme": "bold" } }
```

```json
{ "uuid": "my-site-a1b2c3", "skill_id": "brand-voice", "content": "Updated guidance" }
```

```json
{ "uuid": "my-site-a1b2c3", "skill_id": "website-editing" }
```


## Cost, speed, and token controls

Every provider call is bounded. Per-profile caps live in `syte/ai_providers.py`
(`PROFILE_PROVIDERS`) and are reported by `GET /api/ai_providers`:

| Cap | Meaning | Fallback when unset |
| --- | --- | --- |
| `max_tokens` | Completion ceiling per call | `DEFAULT_MAX_COMPLETION_TOKENS` (8192) |
| `max_input_tokens` | Estimated input ceiling; oldest history is trimmed above it | `DEFAULT_MAX_INPUT_TOKENS` (96k) |
| `max_history_messages` | Conversation window sent to the provider | `MAX_HISTORY_MESSAGES` (160) |
| `max_tool_result_chars` | Per-tool-result truncation | `MAX_TOOL_RESULT_CHARS` (16k) |

No profile is unbounded: an uncapped completion is both the slowest and the most
expensive failure mode. Trimming never drops the system prompt (it holds the
cacheable prefix) or the most recent turns, and `sanitize_provider_messages` is
re-run afterwards so `tool_calls`/`tool` pairs stay valid.

Token estimates come from `syte/token_efficiency.py` (`estimate_tokens`,
`trim_messages_to_budget`). They are intentionally dependency-free heuristics
used for *budgeting*; authoritative usage is returned by the provider in
`_usage` and recorded as a `usage` activity event.

### Prompt caching

The system prompt is assembled as a stable prefix plus a dynamic suffix
(`_build_syte_instruction_parts`). Keep volatile values out of the prefix —
anything that changes between turns invalidates the provider's prefix cache and
silently re-bills the whole instruction. Runtime state (service status) belongs
in `_project_runtime_block`, which lives in the dynamic suffix.

The design contract, theme vocabulary, and shadcn catalog (~4k tokens) are
injected only when the project is, or could become, a web UI — see
`_wants_design_contract`. Python/Go/Rust/CLI workspaces skip them.

### Vision cost

Screenshots are the most expensive context a turn can carry:

- Images entering the model context are the chat-optimized variants (~90 KB),
  not the full-resolution PNG, and carry `detail: "low"` for OpenAI-compatible
  vision endpoints.
- Structured visual analysis is an **extra vision call per screenshot**. It runs
  for the desktop viewport only, and can be disabled entirely:

```json
{ "key": "agent_visual_analysis_enabled", "value": "0" }
```

- Prefer `inspect_preview` (DevTools: load state, console errors, page summary)
  over `screenshot_preview`; it verifies a route without any image tokens.

### MCP tool schemas

`list_mcp_addons` returns addon status plus tool **names and short
descriptions**. Full JSON Schemas are opt-in, which keeps schema-heavy addons
from dominating the context:

```json
{ "include_schemas": true, "addon": "syte" }
```

### Optional: shadcn/ui MCP server

Syte already injects the pinned 57-component shadcn catalog into website
prompts, so this is **not** required. If you want live component sources, add
an MCP addon per project rather than enabling it globally — it runs third-party
code fetched at launch, so treat it as a supply-chain decision:

```json
{
  "uuid": "my-site-a1b2c3",
  "name": "shadcn",
  "transport": "stdio",
  "command": "npx",
  "args": ["-y", "@jpisnice/shadcn-ui-mcp-server"]
}
```

Register it with `POST /api/agent_mcp_register` (or
`POST /api/projects/{uuid}/agent/mcp`), then have the agent call
`connect_mcp` followed by `call_mcp`. Keep `include_schemas` off until the agent
actually needs a specific tool's arguments.
