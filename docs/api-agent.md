# Agent API

The GUI agent endpoints are project-scoped and use the same session as the chat panel.
The same MCP and skills management is also available on the token API (`X-API-Key` /
`Authorization: Bearer`) under `/api/agent_*` — see [Token API mirrors](#token-api-mirrors)
and the HTML docs at `/api/`.

MCP providers and skills can be **listed, added, enabled, disabled, and edited** from the
agent chat resource panel or directly via these APIs.

## Chat

### POST `/api/projects/{project_id}/agent/chat`

Start an agent turn. `thinking_level` accepts `1` (Instant) through `5` (Max).

```json
{
  "message": "Review the landing page spacing",
  "model_profile": "syra-base",
  "thinking_level": 3
}
```

## MCP connections

Manage Model Context Protocol providers per project. The built-in `syte` addon maps to
project `service` / `access` helpers. Custom stdio providers can be registered and
connected from the GUI or API.

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

Per-project skill catalog. Built-in skills inject guidance into the agent system
instruction when active. Enable/disable from the chat Skills panel or API; re-enable
with `parameters` to edit stored string parameters.

| Action | Method | Path |
|--------|--------|------|
| List | `GET` | `/api/projects/{project_id}/agent/skills` |
| Enable / edit parameters | `POST` | `/api/projects/{project_id}/agent/skills/{skill_id}/enable` |
| Disable | `DELETE` | `/api/projects/{project_id}/agent/skills/{skill_id}` |

Built-in skill ids: `website-editing`, `workspace-search`, `preview-access`,
`service-management`, `nextjs-app-router`, `cli-tools`.

### GET `/api/projects/{project_id}/agent/skills`

List the built-in skill catalog and each skill's active state / parameters for the project.

### POST `/api/projects/{project_id}/agent/skills/{skill_id}/enable`

Enable a skill. Sending `parameters` upserts string key/value settings (edit).

```json
{
  "parameters": {
    "theme": "bold"
  }
}
```

### DELETE `/api/projects/{project_id}/agent/skills/{skill_id}`

Disable a project skill (removes the active row; catalog entry remains available to re-enable).

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

### Skills

| Action | Endpoint |
|--------|----------|
| List | `GET /api/agent_skills?uuid=` |
| Enable / edit | `POST /api/agent_skills_enable` |
| Disable | `POST /api/agent_skills_disable` |

```json
{ "uuid": "my-site-a1b2c3", "skill_id": "website-editing", "parameters": { "theme": "bold" } }
```

```json
{ "uuid": "my-site-a1b2c3", "skill_id": "website-editing" }
```
