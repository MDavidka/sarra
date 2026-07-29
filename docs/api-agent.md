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
`syra-base` / `syra-havy` from the message. Explicit profiles, including
`syra-kimi` for ChinaAPI Kimi K3, still win.

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
      "profile": "syra-subagent",
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
