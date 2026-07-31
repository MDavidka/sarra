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
  "model_profile": "auto",
  "thinking_level": 3,
  "improve_from_screenshot": false,
  "visual_analysis_id": null
}
```

Omit `model_profile` or set it to `auto` to let Syte pick `syra-nano` /
`syra-ultra` / `syra-havy` from the message. Explicit profiles still win.

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

## Session failure log

Activity events are pruned and replay-window limited, and they mix success with
failure. The failure log is a small, failure-only table that answers "what
actually went wrong in this session?" — failed tools, requests, subagents,
provider errors and preview checks, for the main agent **and** every subagent.
In the GUI it opens by **double-clicking the brain icon** in the chat status bar.

Rows are recorded automatically from activity events; expected control flow
(`plan_required`, `question_required`, `research_readonly`, file-scope refusals,
`await_timeout`) is deliberately excluded so real problems are not buried.

### GET `/api/projects/{project_id}/agent/failures`

| Query | Default | Notes |
|-------|---------|-------|
| `session` | `last` | `last`, `all`, or a session number |
| `limit` | `200` | 1–1000. `summary` is computed independently, so `limit=1` gives an exact count with a tiny payload |
| `kind` | – | `request` \| `subagent` \| `tool` \| `provider` \| `session` \| `preview` \| `design` |

```json
{
  "ok": true,
  "session": "last",
  "failures": [
    {
      "id": 42,
      "session": 4,
      "request_id": "rq-1",
      "agent": "subagent",
      "subagent_task_id": "sub-9f2c",
      "kind": "tool",
      "tool": "write_file",
      "error": "outside_file_scope",
      "message": "…",
      "target": "app/app/page.tsx",
      "retryable": false,
      "created_at": "2026-07-27T16:06:32.336996+00:00"
    }
  ],
  "summary": { "total": 3, "by_kind": { "tool": 2, "provider": 1 }, "by_tool": { "write_file": 2 }, "sessions": [4, 3] }
}
```

### DELETE `/api/projects/{project_id}/agent/failures?session=last`

Clears the log for that scope: `{ "ok": true, "removed": 3 }`.

## Subagent tasks

Durable record of every delegated task, written to local SQLite **and** Turso.
The local copy is what `await_subagent` recovers from after a restart and what
reveals the GUI subagent tab on load, so a subagent stays visible even when its
activity events have aged out of the replay window.

### GET `/api/projects/{project_id}/agent/subagents?session=last&limit=50`

```json
{
  "ok": true,
  "session": "last",
  "count": 2,
  "running": 1,
  "failed": 0,
  "subagents": [
    {
      "task_id": "sub-9f2c1a",
      "session": 4,
      "parent_request_id": "rq-1",
      "task": "find the hero component",
      "mode": "research",
      "profile": "syra-nano",
      "background": true,
      "files": ["app/app/page.tsx"],
      "status": "completed",
      "result": "…",
      "error": "",
      "usage": { "total_tokens": 1840 },
      "cost": { "cost_usd": 0.0011 },
      "activity_count": 12,
      "started_at": "2026-07-27T16:05:00+00:00",
      "finished_at": "2026-07-27T16:05:41+00:00"
    }
  ]
}
```

`status` is one of `running`, `completed`, `partial`, `failed`, `timeout`,
`cancelled`. Rows left at `running` by a restart are swept to `cancelled` the
next time the project's agent is warmed.

## MCP stdio client

External MCP clients (IDE plugins, agent chat UIs, automation scripts) can connect to Syte
via stdio by spawning the built-in MCP server. The server implements JSON-RPC 2.0 over
stdin/stdout with Content-Length framing (no extra dependencies).

### Spawn command

```bash
SYTE_PROJECT_ID=<project-uuid> SYTE_API_BASE=https://syte.example.com python3 -m syte.mcp_stdio
```

### Connection flow

1. Resolve the project UUID from the Syte GUI or API.
2. Set `SYTE_PROJECT_ID` and `SYTE_API_BASE` environment variables.
3. Spawn the process with stdio attached.
4. Send `initialize`, then `tools/list` to discover tools.
5. Send `tools/call` with `name` + `arguments` to invoke a tool.

### Available tools

| Tool | Description |
|------|-------------|
| `syte_service` | Control service + preview (status, preview_start, preview_stop, run, logs, preview_logs). Production start/stop/deploy/update are blocked for agent safety. |
| `syte_access` | Preview access (status, url, fetch, read, logs, screenshot) |
| `syte_info` | Return project routes, API base, spawn command, and docs links |

### Example: list tools

```json
{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}
```

### Example: call a tool

```json
{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"syte_service","arguments":{"action":"status"}}}
```

Responses include `isError` and the tool result as JSON text in `content[0].text`.

## Model stream

### GET `/api/agent_models/stream`

Stream available AI model profiles for agent UIs. Requires `X-API-Key` or
`Authorization: Bearer` authentication.

**Headers:** `Accept: text/event-stream`

The server sends one `snapshot` event on connect, then periodic `heartbeat` events.

```json
event: snapshot
data: {"ok": true, "source": "provider_catalog", "models": [
  {"name": "syra-nano", "display": "Go — Gemini 2.5 Flash"},
  {"name": "syra-ultra", "display": "Air — Aliyun Qwen3.7-Plus"},
  {"name": "syra-havy", "display": "Metal — VyceAI Claude Sonnet 4.6"}
]}

event: heartbeat
data: {"ok": true}
```

Use this endpoint to populate model selector dropdowns without polling the full
`/api/ai.json` spec.

## Token API mirrors

Authenticate with `X-API-Key: syte_…` or `Authorization: Bearer syte_…`.

### Failures and subagents

| Action | Endpoint |
|--------|----------|
| Failure log | `GET /api/agent_failures?uuid=&session=last&limit=200&kind=` |
| Subagent tasks | `GET /api/agent_subagents?uuid=&session=last&limit=50` |

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

### MCP credentials

Per-project credential store for external service API keys (GitHub, Jira,
Slack, etc.) that the agent may call via the `call_external_api` tool. Keys are
stored at rest in Turso (TLS-encrypted) and are **never** returned unmasked over
any route — only the last 4 chars are exposed (`api_key_masked`, e.g.
`••••xxxx`); the real secret is read server-side by `call_external_api` alone.

Authenticate with `X-API-Key: syte_…` or `Authorization: Bearer syte_…`.

| Action | Endpoint | Request body | Returns |
|--------|----------|--------------|---------|
| List | `GET /api/agent_credentials?uuid=` | — | `{ ok, uuid, credentials[] }` |
| **Get one** | `GET /api/agent_credentials/{service_name}?uuid=` | — | `{ ok, uuid, service_name, credential }` |
| Save | `POST /api/agent_credentials` | `AgentMcpCredentialBody` | `{ ok, …credential }` |
| Batch save | `POST /api/agent_credentials_batch` | `AgentMcpCredentialBatchBody` | `{ ok, uuid, profile, credentials[] }` |
| Revoke | `POST /api/agent_credentials_delete` | `{ "uuid", "service_name" }` | `{ ok, uuid, service_name }` |

> `service_name` is lowercased on write and is the join key for every
> read/get/update/delete call. The get-one path param matches the stored slug
> exactly (case-insensitive).

#### `AgentMcpCredentialBody`

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `uuid` | **yes** | string | Project UUID |
| `service_name` | **yes** | string | Machine slug, e.g. `github` (max 120 chars; lowercased) |
| `display_name` | no | string | Human label (max 200 chars) |
| `description` | no | string | Free-text (max 1000 chars) |
| `api_key` | no | string | Secret (max 2000 chars) |
| `api_url` | no | string | Service base URL (max 2000 chars) |
| `metadata` | no | object | Arbitrary key/value pairs |

Save (upsert) is idempotent on `(project_id, service_name)`: re-saving rotates
the key/URL and keeps the same row.

#### GET `GET /api/agent_credentials/{service_name}?uuid=`

Returns a single credential for `service_name` on the project. The API key is
**masked** — only the last 4 characters are returned.

**200 — credential found**

```json
// GET /api/agent_credentials/github?uuid=proj-42
{
  "ok": true,
  "uuid": "proj-42",
  "service_name": "github",
  "credential": {
    "id": 7,
    "project_id": "proj-42",
    "service_name": "github",
    "display_name": "GitHub (org-bot)",
    "description": "Read/write access to my-org repos",
    "api_key": "••••xxxx",
    "api_key_masked": "••••xxxx",
    "api_url": "https://api.github.com",
    "metadata": { "owner": "my-org", "scopes": ["repo", "read:org"] },
    "status": "active",
    "created_at": "2026-07-30T21:30:00.000Z",
    "updated_at": "2026-07-30T21:30:00.000Z"
  }
}
```

**404 — not found**

Returned when the project UUID does not exist **or** no credential is stored for
that `service_name` on the project (this also covers the case where remote Turso
is not configured, since `get_mcp_credential` then resolves to `None`):

```json
{
  "ok": false,
  "error": "not_found",
  "message": "Credential not found for service 'github'"
}
```

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | Row id in `user_mcp_credentials` |
| `project_id` | string | Project UUID |
| `service_name` | string | Lowercased slug |
| `display_name` | string | Human label |
| `description` | string | Free-text |
| `api_key` | string | **Masked** (`••••XXXX`) |
| `api_key_masked` | string | Masked (alias of `api_key`) |
| `api_url` | string | Service base URL |
| `metadata` | object | Arbitrary key/value pairs |
| `status` | string | `active` or `revoked` |
| `created_at` | string | ISO-8601 |
| `updated_at` | string | ISO-8601 |

#### Batch save `POST /api/agent_credentials_batch`

Exact accepted JSON schema for an external service to bulk-save credentials and
profile in one call — see `docs/turso-persistence.md` for the full worked example.
The `credentials` array items accept the same fields as `AgentMcpCredentialBody`
(minus `uuid`):

```json
// POST /api/agent_credentials_batch
{
  "uuid": "proj-42",
  "name": "My Project",
  "icon": "https://cdn.example.com/projects/proj-42/icon.png",
  "metadata": { "org_slug": "my-org", "region": "us-east-1" },
  "credentials": [
    {
      "service_name": "github",
      "display_name": "GitHub (org-bot)",
      "api_key": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "api_url": "https://api.github.com",
      "metadata": { "owner": "my-org", "scopes": ["repo", "read:org"] }
    }
  ]
}
```

Response: `{ "ok": true, "uuid": "proj-42", "profile": {…}, "credentials": [ {…masked…} ] }`.


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
