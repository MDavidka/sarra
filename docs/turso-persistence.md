# Turso persistence: request, activity, cost, subagents

Every agent turn is persisted in the configured Turso (libSQL) database. This
document describes the five tables and shows the exact write sequence the
backend performs for one request, including a delegated subagent.

Configuration lives in Settings → AI (`turso_database_url`, `turso_auth_token`).
When Turso is unset, sessions/events still work through the local SQLite
fallback in `syte.local_session_store`; the rollup tables below are remote-only.

## Tables

| Table | Purpose | Written by |
| --- | --- | --- |
| `agent_session` | One numbered chat session (turn) per user message | `open_session`, `close_session` |
| `agent_session_event` | Full activity trail (thinking, tools, screenshots, usage) | `record_event` |
| `agent_message` | Raw chat messages (user / assistant / tool) | `record_message` |
| `agent_request` | **One row per request**: the request text, its timestamp, how much activity it caused, and its final cost | `record_request`, `finalize_request` |
| `agent_subagent_task` | **One row per delegation**: task text, declared file scope, start time, outcome, cost | `record_subagent_task`, `finalize_subagent_task` |
| `agent_subagent_activity` | Activity produced *by a subagent* (its own chat tab feed) | `record_subagent_activity` |
| `project_profile` | Per-project user-facing metadata (name, icon) | `upsert_project_profile` |
| `user_mcp_credentials` | External service API keys the agent can use (GitHub, Jira, Slack, etc.) | `save_mcp_credential`, `get_mcp_credential`, `delete_mcp_credential` |

`agent_request.cost_usd` is deliberately `NULL` until generation finishes —
token usage (and therefore price) is only known at the end of the turn.

## Write sequence for one turn

```text
1. open_session(project_id, session_number, model_profile)   -> session uuid
2. record_request(request_id, project_id, request, ...)      -> status=running, cost=NULL
3. record_event(...) xN                                      -> activity trail
   record_message(...) xN                                    -> raw messages
4. record_subagent_task(task_id, ..., files=[...])           -> status=running, started_at
5. record_subagent_activity(task_id, ...) xN                 -> subagent tab feed
6. finalize_subagent_task(task_id, ..., usage, cost)         -> status + cost
7. finalize_request(request_id, ..., usage, cost,
                    activity_count, subagent_count)          -> status + COST (end)
8. close_session(session_id, status="completed")
```

`activity_count` is the number of non-stream activity events the request
produced; it is tracked in-process by `syte.agent_activity` and read back with
`activity_count_for_request(request_id)`.

## Worked example

User asks *"Add a pricing page"* on `syra-havy`, and the agent delegates the FAQ
page to a subagent.

### 1. Request accepted — `agent_request`

```python
await turso_store.record_request(
    "req-1", "proj-42", "Add a pricing page",
    session_id="8f1c…", session_number=7, source="gui",
    model_profile="syra-havy", model="glm-4.6", provider="nvidia",
    thinking_level=3,
)
```

Row right after insert:

```json
{
  "request_id": "req-1",
  "session_id": "8f1c…",
  "project_id": "proj-42",
  "session_number": 7,
  "source": "gui",
  "model_profile": "syra-havy",
  "request": "Add a pricing page",
  "status": "running",
  "timestamp": "2026-07-25T20:36:14.910085+00:00",
  "started_at": "2026-07-25T20:36:14.910085+00:00",
  "cost_usd": null,
  "activity_count": 0,
  "ended_at": null
}
```

### 2. Delegation — `agent_subagent_task`

The main agent must declare the file scope *before* handing work over, so no two
agents can write the same file (see `delegate_task.files`).

```python
await turso_store.record_subagent_task(
    "sub-abc", "proj-42", "Build the FAQ page",
    session_id="8f1c…", session_number=7, parent_request_id="req-1",
    mode="implementation", profile="syra-havy", model="claude-sonnet-4-6",
    background=True,
    files=["app/app/faq/page.tsx", "app/components/faq.tsx"],
)
```

```json
{
  "task_id": "sub-abc",
  "parent_request_id": "req-1",
  "task": "Build the FAQ page",
  "mode": "implementation",
  "background": true,
  "files": ["app/app/faq/page.tsx", "app/components/faq.tsx"],
  "status": "running",
  "started_at": "2026-07-25T20:36:14.910931+00:00",
  "ended_at": null,
  "cost_usd": null
}
```

### 3. Subagent activity — `agent_subagent_activity`

One row per line shown in the GUI's subagent tab:

```python
await turso_store.record_subagent_activity(
    "sub-abc", "proj-42", "tool_call_finished",
    session_id="8f1c…", parent_request_id="req-1", tool="write_file",
    title="write_file", detail='{"ok": true, "message": "wrote 2140 bytes"}',
    payload={"agent": "subagent", "task_id": "sub-abc", "step": 2},
)
```

### 4. Subagent finished — cost recorded

```python
await turso_store.finalize_subagent_task(
    "sub-abc", "proj-42", status="completed",
    result="Created app/app/faq/page.tsx and the FAQ component.",
    usage={"input_tokens": 300, "output_tokens": 120, "steps": 3},
    cost={"cost_usd": 0.0009, "label": "$0.0009 · 420 tokens"},
    activity_count=6,
)
```

### 5. End of generation — request cost

```python
await turso_store.finalize_request(
    "req-1", "proj-42", status="completed", reply="Added the pricing page.",
    usage={"input_tokens": 1200, "output_tokens": 400,
           "thinking_tokens": 100, "steps": 5},
    cost={"cost_usd": 0.0123, "label": "$0.0123 · 1700 tokens"},
    activity_count=17, subagent_count=1,
)
```

Final `agent_request` row:

```json
{
  "request_id": "req-1",
  "request": "Add a pricing page",
  "reply": "Added the pricing page.",
  "status": "completed",
  "timestamp": "2026-07-25T20:36:14.910085+00:00",
  "ended_at": "2026-07-25T20:36:29.480512+00:00",
  "activity_count": 17,
  "subagent_count": 1,
  "steps": 5,
  "input_tokens": 1200,
  "output_tokens": 400,
  "thinking_tokens": 100,
  "total_tokens": 1700,
  "cost_usd": 0.0123,
  "cost_label": "$0.0123 · 1700 tokens"
}
```

`status` is one of `running`, `completed`, `failed`, `cancelled`; failed and
cancelled turns are finalized too, with whatever usage/cost accrued.

## Reading it back

```python
row   = await turso_store.get_request("req-1", "proj-42")
tasks = await turso_store.list_subagent_tasks(session_id="8f1c…")
lines = await turso_store.list_subagent_activity("sub-abc")
```

Useful queries:

```sql
-- Spend per project, last 100 requests
SELECT project_id, SUM(cost_usd) AS usd, SUM(total_tokens) AS tokens
FROM agent_request WHERE status = 'completed' GROUP BY project_id;

-- Requests that produced a lot of activity but no reply
SELECT request_id, activity_count, status FROM agent_request
WHERE status != 'completed' ORDER BY activity_count DESC LIMIT 20;

-- Which files each subagent owned
SELECT task_id, mode, files, cost_usd, started_at, ended_at
FROM agent_subagent_task WHERE session_id = ?;
```

## Reliability notes

- Every write goes through `_write_with_retry`, which retries once on a
  transient failure and treats a `UNIQUE` conflict as "already recorded"
  (idempotent replay) rather than a lost row.
- Schema init is per-statement resilient: one rejected index never disables the
  other tables. Failures are surfaced through `turso_debug_status()` (the GUI
  "brain" indicator) instead of failing silently.
- `record_event` no longer reports a committed row as unsaved when the
  cosmetic `agent_session.updated_at` touch fails.
- Save helpers never raise into the turn: a persistence problem degrades
  observability, it never fails the user's request.
- **Hot path vs Turso:** `token_delta` / `thinking_delta` are batched
  (16–32 tokens or 300–500 chars), emitted as minimal-delta SSE frames, and
  **never** written to Turso. Cold activity events mirror to Turso in a
  **background task** so a slow remote DB cannot stall streaming or first
  token. Messages use the same fire-and-forget + end-of-turn resync pattern
  (`_resync_unsynced_messages`).
- SSE endpoints negotiate `gzip` / `br` via `Accept-Encoding` so external
  pages can keep the live feed cheap while Turso remains the durable store.

## Project profile

| Column | Type | Notes |
|--------|------|-------|
| `project_id` | TEXT PK | Syte project UUID |
| `name` | TEXT | Human-readable display name (max 200 chars) |
| `icon` | TEXT | URL or data-URI for project icon (max 2000 chars) |
| `metadata` | TEXT (JSON) | Arbitrary key/value pairs |
| `created_at` | TEXT | ISO-8601 timestamp |
| `updated_at` | TEXT | ISO-8601 timestamp |

One row per project. Upserted via `upsert_project_profile` — an external service
can set a project's name and icon in Turso so every Syte instance shows the
same profile.

## User MCP credentials

Stores API keys, tokens, and endpoint URLs for external services the agent is
allowed to call on behalf of the user (GitHub, Jira, Slack, Linear, etc.).

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | Auto-increment row id |
| `project_id` | TEXT | Syte project UUID |
| `service_name` | TEXT | Machine-readable slug, e.g. `github`, `jira`, `slack` (max 120 chars) |
| `display_name` | TEXT | Human-readable label, e.g. `GitHub` (max 200 chars) |
| `description` | TEXT | Free-text description of what this credential is for (max 1000 chars) |
| `api_key` | TEXT | The secret API key/token (max 2000 chars) |
| `api_url` | TEXT | Base URL for the service API, e.g. `https://api.github.com` (max 2000 chars) |
| `metadata` | TEXT (JSON) | Arbitrary key/value pairs (rate limits, org slug, etc.) |
| `status` | TEXT | `active` or `revoked` |
| `created_at` | TEXT | ISO-8601 timestamp |
| `updated_at` | TEXT | ISO-8601 timestamp |

Unique constraint: `(project_id, service_name)`.

**Security:** API keys stored at rest in Turso (TLS-encrypted). The agent tool
`mcp_credentials` returns masked keys (`••••XXXX` — only last 4 chars visible).
Only the server-side `call_external_api` tool reads the real key and uses it
for outbound requests. Keys are **never** echoed into LLM prompts.

## Accepted JSON format (external service batch save)

An external service saves project profile + credentials in a single batch call.
This is the exact accepted JSON schema.

### Endpoint

```
POST /api/projects/{project_id}/agent/credentials/batch
```

### Request body

```json
{
  "name": "My Project",
  "icon": "https://cdn.example.com/projects/proj-42/icon.png",
  "metadata": {
    "org_slug": "my-org",
    "region": "us-east-1"
  },
  "credentials": [
    {
      "service_name": "github",
      "display_name": "GitHub (org-bot)",
      "description": "Read/write access to my-org repos",
      "api_key": "ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
      "api_url": "https://api.github.com",
      "metadata": {
        "owner": "my-org",
        "scopes": ["repo", "read:org"]
      }
    },
    {
      "service_name": "jira",
      "display_name": "Jira Cloud",
      "description": "Project management and ticketing",
      "api_key": "ATATT3xFfGF0...",
      "api_url": "https://mycompany.atlassian.net",
      "metadata": {
        "email": "bot@mycompany.com",
        "project_key": "PROJ"
      }
    },
    {
      "service_name": "slack",
      "display_name": "Slack Bot",
      "description": "Post notifications to #deploys",
      "api_key": "xoxb-...",
      "api_url": "https://slack.com/api",
      "metadata": {
        "default_channel": "#deploys"
      }
    }
  ]
}
```

### Response

```json
{
  "ok": true,
  "project_id": "proj-42",
  "profile": {
    "project_id": "proj-42",
    "name": "My Project",
    "icon": "https://cdn.example.com/projects/proj-42/icon.png",
    "metadata": { "org_slug": "my-org", "region": "us-east-1" },
    "created_at": "2026-07-30T21:30:00.000Z",
    "updated_at": "2026-07-30T21:30:00.000Z"
  },
  "credentials": [
    {
      "project_id": "proj-42",
      "service_name": "github",
      "display_name": "GitHub (org-bot)",
      "description": "Read/write access to my-org repos",
      "api_key_masked": "••••xxxx",
      "api_url": "https://api.github.com",
      "metadata": { "owner": "my-org", "scopes": ["repo", "read:org"] },
      "status": "active",
      "created_at": "2026-07-30T21:30:00.000Z",
      "updated_at": "2026-07-30T21:30:00.000Z"
    }
  ]
}
```

### Field reference

| Field | Required | Type | Notes |
|-------|----------|------|-------|
| `name` | no | string | Project display name (max 200 chars) |
| `icon` | no | string | Project icon URL or data-URI (max 2000 chars) |
| `metadata` | no | object | Arbitrary project-level key/value pairs |
| `credentials` | no | array | List of credential objects to save |
| `credentials[].service_name` | **yes** | string | Machine slug, unique per project (max 120 chars) |
| `credentials[].display_name` | no | string | Human label (max 200 chars) |
| `credentials[].description` | no | string | Free-text (max 1000 chars) |
| `credentials[].api_key` | no | string | Secret API key/token (max 2000 chars) |
| `credentials[].api_url` | no | string | Service base URL (max 2000 chars) |
| `credentials[].metadata` | no | object | Arbitrary credential-level key/value pairs |

### Token API mirror

Authenticate with `X-API-Key: syte_…` or `Authorization: Bearer syte_…`.

```
POST /api/agent_credentials_batch
{
  "uuid": "proj-42",
  "name": "My Project",
  "icon": "...",
  "credentials": [...]
}
```

### Reading credentials

Credentials are never returned with the full secret over any route. API keys are
always masked (`••••XXXX` — only the last 4 chars are visible). The real key is
only read server-side by the `call_external_api` agent tool.

| Action | Token API | GUI (browser session) | Internal (sycord runtime) |
|--------|-----------|-----------------------|--------------------------|
| List all | `GET /api/agent_credentials?uuid=` | `GET /api/projects/{project_id}/agent/credentials` | `GET /projects/{project_id}/agent/credentials` |
| **Get one** | `GET /api/agent_credentials/{service_name}?uuid=` | `GET /api/projects/{project_id}/agent/credentials/{service_name}` | `GET /projects/{project_id}/agent/credentials/{service_name}` |
| Save | `POST /api/agent_credentials` | `POST /api/projects/{project_id}/agent/credentials` | `POST /projects/{project_id}/agent/credentials` |
| Batch save | `POST /api/agent_credentials_batch` | `POST /api/projects/{project_id}/agent/credentials/batch` | `POST /projects/{project_id}/agent/credentials/batch` |
| Revoke | `POST /api/agent_credentials_delete` | `DELETE /api/projects/{project_id}/agent/credentials/{service_name}` | `DELETE /projects/{project_id}/agent/credentials/{service_name}` |

`service_name` is lowercased on write and is the join key for every read.

#### Get-one: `GET /api/agent_credentials/{service_name}?uuid=`

Returns a single credential. The `api_key` field is masked; `api_key_masked`
contains the same masked value.

`GET /api/projects/{project_id}/agent/credentials/{service_name}` (GUI) and
`GET /projects/{project_id}/agent/credentials/{service_name}` (internal) return
the same object under the `credential` key (plus `project_id`/`service_name`).

**200 OK**

```json
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

**404 Not Found** — returned by the token and GUI routes when the project UUID
does not exist, or no `active` credential is stored for that `service_name`.
When remote Turso is not configured, `get_mcp_credential` resolves to `None`,
so get-one surfaces as a 404 rather than a 503 (the list route instead returns
an empty `credentials: []`).

```json
{ "ok": false, "error": "not_found",
  "message": "Credential not found for service 'github'" }
```

#### Credential object fields

| Field | Type | Notes |
|-------|------|-------|
| `id` | integer | Row id |
| `project_id` | string | Project UUID |
| `service_name` | string | Lowercased slug |
| `display_name` | string | Human label |
| `description` | string | Free-text |
| `api_key` | string | **Masked** (`••••XXXX`) |
| `api_key_masked` | string | Masked alias of `api_key` |
| `api_url` | string | Service base URL |
| `metadata` | object | Arbitrary key/value pairs |
| `status` | string | `active` or `revoked` |
| `created_at` | string | ISO-8601 |
| `updated_at` | string | ISO-8601 |

#### Save / batch / revoke error behaviour

When remote Turso is **not configured** (`turso_database_url` unset), writes
fail gracefully rather than raising a 500:

- **Token upsert** (`POST /api/agent_credentials`) and the **internal** route
  return HTTP **503** `{ "ok": false, "error": "turso_unavailable", … }`.
- The **GUI** single-save route returns HTTP **200** with
  `{ "ok": false, "error": "turso_unavailable", "message": "Turso is not configured — cannot save credential." }`
  (it never raises).
- The **batch** route returns HTTP **200** with an empty `credentials: []`
  list (each `save_mcp_credential` call resolves to `None` and is skipped).

Get-one never returns 503: when Turso is unavailable, `get_mcp_credential`
resolves to `None`, which the routes surface as a **404** (see above).
Revocation of a missing/non-existent `service_name` is a no-op that returns
`ok: true`.



## Agent tools

The cloud agent exposes two tools for working with stored credentials:

### `mcp_credentials`

Lists all active MCP credentials for the current project. Returns service names,
display names, descriptions, API base URLs, and **masked** keys. The agent
calls this first before attempting any external API work.

```
→ mcp_credentials()
← { "ok": true, "credentials": [{ "service_name": "github", "api_key_masked": "••••xxxx", ... }] }
```

### `call_external_api`

Makes an authenticated HTTP request using a stored credential. The agent passes
the `service_name` (from `mcp_credentials`), HTTP method, URL path (appended to
the stored `api_url`), optional JSON body, and optional extra headers. The
server looks up the real key, attaches it as a `Bearer` token, and makes the
request.

```
→ call_external_api(service_name="github", method="GET", path="/repos/my-org/my-repo/issues")
← { "ok": true, "status": 200, "service": "github", "data": [...] }
```

The auth header is automatically set to `Authorization: Bearer <stored_api_key>`.
Additional headers provided by the agent are merged on top.
