# Agent API

The GUI agent endpoints are project-scoped and use the same session as the chat panel.

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

### GET `/api/projects/{project_id}/agent/mcp`

List built-in and registered MCP providers, including connection status and discovered tools.

### POST `/api/projects/{project_id}/agent/mcp`

Register a stdio provider.

```json
{
  "name": "playwright",
  "command": "npx",
  "args": ["playwright-mcp"],
  "env": {}
}
```

### POST `/api/projects/{project_id}/agent/mcp/connect`

Connect a provider by its `addon` id or name.

### DELETE `/api/projects/{project_id}/agent/mcp/{addon_id}`

Disconnect a provider without removing its registration.

## Skills

### GET `/api/projects/{project_id}/agent/skills`

List the built-in skill catalog and each skill's active state for the project.

### POST `/api/projects/{project_id}/agent/skills/{skill_id}/enable`

Enable a skill, optionally storing string parameters for the project.

```json
{
  "parameters": {
    "theme": "bold"
  }
}
```

### DELETE `/api/projects/{project_id}/agent/skills/{skill_id}`

Disable a project skill.
