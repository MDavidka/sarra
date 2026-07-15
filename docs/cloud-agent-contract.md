# Syte Cloud Agent Contract

Syte runs a VM-native background coding agent inside the main service process.
The architecture follows KiloCode's cloud-session principles: durable admitted
inputs, serialized per-session execution, persisted messages, structured events,
and restartable work. It does not launch a CLI or HTTP server per project.

## Runtime

- Project code: `workspaces/<uuid>/app/`
- Runtime metadata: `workspaces/<uuid>/data/cloud-agent/runtime.json`
- Agent instructions: `workspaces/<uuid>/data/cloud-agent/SYTE_AGENT.md`
- Runtime log: `workspaces/<uuid>/data/cloud-agent/agent.log`
- Conversation history and pending requests: Syte SQLite database
- Durable, UUID-addressable activity per turn: Turso (libSQL) — see Activity API below
- Durable, UUID-addressable chat messages per turn (raw user/assistant/tool
  rows, mirrored live): Turso (libSQL), single shared `agent_message` table
  — see "Durable message store (Turso)" below

The agent uses only Syte's configured Syra profiles and their existing fixed
OpenAI-compatible endpoints:

- `syra-nano`: Gemini Flash
- `syra-base`: DeepSeek Chat
- `syra-havy`: Gemini Pro

Provider keys remain in Syte system settings. They are sent directly to the
selected provider and are never copied into project runtime files.

## Lifecycle and reliability

`start` validates configuration and marks the embedded runtime ready without a
process spawn or port wait. `warm` is idempotent. `stop` disables a project
agent and interrupts an active turn. `restart` clears the durable conversation
and creates a fresh session.

Background requests are inserted into SQLite before execution. Requests that
were running when the VM or service stopped are returned to `pending` and
resumed at startup. Turns are serialized per project, provider calls use bounded
exponential retries, and every accepted request produces a terminal success,
failure, or cancellation event.

## Agent execution

The cloud agent calls the selected provider with non-streaming chat completions
and structured tools. Available tools are limited to Syte operations:

- list, read, write, and delete workspace files
- execute commands inside the project workspace
- inspect or control project service, preview, deploy, and logs

The system instruction is generated for Syte and includes project access rules,
workspace location, verification requirements, and credential handling.

## Activity API

Background chat returns a request ID immediately, together with a durable
Turso (libSQL) session id: `turso_session_id`. There is no live activity
stream — every event produced while the agent works on a turn (the request,
its plan, tool calls, and the final reply) is written to that session as it
happens, and clients fetch the whole session document by UUID instead of
holding open a streaming connection:

`GET /api/agent_session/{session_id}?since_id=N`

The response is the session's metadata plus its `events` array (only events
with `id > since_id` when polling an still-`open` session). This works
identically on the public, `/api/internal`, and `/sycord/api` mirrors
(`GET /api/internal/agent_session/{id}`, `GET /sycord/api/agent_session/{id}`).
To discover recent session ids for a project without already having one, use:

`GET /api/agent_sessions?uuid={uuid}&limit=50`

which lists the newest sessions first (each with a `session_url`). Turso must
be configured (`turso_database_url`, optional `turso_auth_token`, saved from
the Syte GUI's AI tab) for these routes to return data; if it is not
configured, `agent_change`/`agent_communicate` still work, but no durable
session is created and `agent_sessions`/`agent_session` report
`turso_configured: false` / 503 respectively. A fast, always-available local
mirror (not durable across database moves) remains at
`GET /api/agent_activity?since_id=N` (optional `session=last` or `session=2`)
and its `/api/internal` and `/sycord/api` mirrors, for callers that have not
configured Turso yet.

Each accepted turn produces this ordered sequence of session events,
correlated by `payload.request_id`:

```
request_started (role=user)
  -> processing
  -> [thinking]                         # optional plan/reasoning
  -> (tool_call_started -> tool_call_finished)*
  -> request_completed | request_failed # exactly one, terminal
```

Payload fields: `tool_call_started` carries `tool` and `arguments`;
`tool_call_finished` carries `tool` and `ok` (boolean success);
`request_completed` carries `reply`; `request_failed` carries `error` and
`retry_message`. Lifecycle events `agent_started`, `agent_stopped`, and
`agent_restarted` are emitted on start/stop/restart.

A session document looks like:

```json
{
  "ok": true,
  "id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
  "project_id": "myapp-a1b2c3",
  "session_number": 1,
  "model_profile": "syra-base",
  "status": "completed",
  "created_at": "2026-07-15T12:00:00+00:00",
  "updated_at": "2026-07-15T12:00:04+00:00",
  "events": [
    {"id": 1, "event_type": "request_started", "role": "user", "detail": "Add dark mode"},
    {"id": 2, "event_type": "processing", "detail": "Cloud agent accepted the durable request"},
    {"id": 3, "event_type": "tool_call_started", "payload": {"tool": "write_file"}},
    {"id": 4, "event_type": "tool_call_finished", "payload": {"tool": "write_file", "ok": true}},
    {"id": 5, "event_type": "request_completed", "payload": {"reply": "Added dark mode"}}
  ]
}
```

`status` is `open` while the turn is in progress, and `completed`, `failed`,
or `cancelled` once it finishes. Clients that want to observe an in-progress
turn poll `GET /api/agent_session/{id}?since_id=<highest event.id seen>` on a
short interval (a few seconds) until `status != "open"`.

## Durable message store (Turso) — "brain" save status

Every chat message the cloud agent produces — the user's message, each
assistant reply (including tool-call requests), and every tool result — is
persisted twice:

1. **Locally**, in Syte's own SQLite database, table `agent_messages`
   (module `syte.cloud_agent_store`). This is the always-available store the
   agent reads back from to rebuild provider context and is never skipped.
2. **Durably in Turso**, table `agent_message` (module `syte.turso_store`).
   This is a best-effort mirror: if Turso is unreachable or not configured,
   the local write still succeeds and the turn completes normally — nothing
   is ever lost from the local store, but that specific message will not
   exist in Turso until connectivity is restored (Syte does not currently
   retry failed Turso writes after the fact — see "Retry & recovery" below).

### One shared table, sessions kept separate by column, not by table

There is exactly **one** `agent_message` table in the configured Turso
database. It is shared by every project and every chat session — messages
are **never** split into per-project or per-session tables. Instead, each
row carries:

- `session_id` — the durable Turso session UUID (see `agent_session` above);
  this is the primary way clients scope a query to "this one conversation
  turn."
- `project_id` — the Syte project UUID, for cross-session queries scoped to
  one project.
- `session_number` — the project-local incrementing chat-session counter
  (matches `agent_sessions.session_counter` / `agent_messages.session_number`
  in the local SQLite store), for cross-session queries scoped to one
  numbered conversation.
- `local_message_id` — the SQLite `agent_messages.id` this row mirrors (join
  key back to the local store; unique per `project_id`).
- `request_id`, `role` (`user` | `assistant` | `tool`), `content`,
  `tool_call_id`, `tool_calls` (JSON), `reasoning_content`, `created_at`.

```sql
CREATE TABLE IF NOT EXISTS agent_message (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    session_number INTEGER NOT NULL DEFAULT 0,
    local_message_id INTEGER,
    request_id TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL,
    content TEXT NOT NULL DEFAULT '',
    tool_call_id TEXT,
    tool_calls TEXT,
    reasoning_content TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX idx_agent_message_session ON agent_message(session_id, id);
CREATE INDEX idx_agent_message_project ON agent_message(project_id, session_number, id);
CREATE UNIQUE INDEX idx_agent_message_local_id
  ON agent_message(project_id, local_message_id) WHERE local_message_id IS NOT NULL;
```

This schema is created automatically (idempotent `CREATE TABLE/INDEX IF NOT
EXISTS`) the first time any Turso call is made after the database URL is
configured — no manual migration step is required.

### How to reach Turso

- **Connection settings** are stored as regular Syte system settings (the
  same local SQLite `system_settings` table everything else in Settings
  uses) — **not** environment variables:
  - `turso_database_url` — e.g. `libsql://<db-name>-<org>.turso.io` (a
    remote Turso database) or, for local/dev testing without a live Turso
    server, a local libSQL file URL such as `file:/path/to/local.db`.
  - `turso_auth_token` — optional; required for remote `libsql://` databases,
    omitted for local `file:` URLs.
  - Set both from the Syte GUI's **Settings → AI** tab, or via
    `PUT /api/settings` with `turso_database_url` / `turso_auth_token` in the
    JSON body.
- **Client library**: [`libsql-client`](https://pypi.org/project/libsql-client/)
  (pinned `>=0.3.1,<1` in `requirements.txt` / `pyproject.toml`), the official
  async Python client for Turso/libSQL. Syte creates one client per
  `(url, token)` pair via `libsql_client.create_client(url, auth_token=token)`
  and caches it (`syte.turso_store._client_cache`); saving new settings calls
  `reset_client_cache()` so the next call picks up the new connection.
- **No connection = no-op, not an error.** Every read/write function in
  `syte.turso_store` (`open_session`, `close_session`, `record_event`,
  `record_message`, `list_messages`, `count_messages`, `get_session`,
  `list_sessions_for_project`, …) returns `None`, `False`, or an empty
  list/dict when `turso_database_url` is unset, and never raises out of the
  agent's request pipeline. Local persistence and the chat turn itself are
  completely unaffected.

### Real-time save on every message, including API-started sessions

For a session **started directly via the API** — `POST /api/agent_change`,
the internal `/agent/change` route, or `POST /sycord/api/agent_change`, all
of which go through `syte.agent_jobs.submit_agent_request` — the Turso
session is opened and its `request_started` event is recorded **before the
background worker even begins**, not after the turn finishes. From that
point on, `syte.cloud_agent._persist_message()` runs after every single
local `append_message()` call — for the admitted user message, for each
assistant turn (including ones that only contain tool calls), and for every
tool result — and immediately mirrors that one message into the shared
`agent_message` Turso table via `record_message()`. There is no batching and
no "sync at the end": each message is durably saved (or attempted) the
moment it is produced, whether the turn was started synchronously
(`agent_communicate`) or as a background job (`agent_change` /
`submit_agent_request`).

### Retry & recovery

A message is flagged `turso_synced = 1` in the local `agent_messages` table
only after its `record_message()` mirror write to Turso actually succeeds.
If that single write fails (Turso down, network blip, etc.), the local
message is still saved and the turn is **not** blocked or failed — but the
row is left `turso_synced = 0` and is not automatically retried later. The
next message in the same turn is still attempted independently, so a single
transient failure does not cascade. Operators who need a fully caught-up
mirror after an outage should re-run the affected turn, or add a background
reconciliation job (not built in) that finds `turso_synced = 0` rows and
replays `record_message()` for them.

### Checking save status — the "brain" indicator

`GET /api/projects/{project_id}/agent/turso_sync` (mirrored at
`GET /api/agent_turso_sync?uuid=`, `GET /api/internal/projects/{project_id}/agent/turso_sync`,
and `GET /sycord/api/agent_turso_sync?uuid=`) reports the aggregate save
status for the project's **current** chat session:

```json
{
  "ok": true,
  "project_id": "myapp-a1b2c3",
  "turso_configured": true,
  "session": 3,
  "turso_session_id": "b6f2b6b6c2e94e2e9e3e4b6c2e94e2e9",
  "total_messages": 6,
  "synced_messages": 6,
  "all_saved": true
}
```

- `all_saved: true` → every message locally recorded for the current session
  has been mirrored to Turso → the Syte GUI's debug-chat "brain" icon (next
  to the activity status dot) renders **green**.
- `all_saved: false` → at least one message in the current session has not
  been synced (a `record_message()` call failed, or Turso only just went
  unreachable mid-turn) → the brain icon renders **red** and pulses.
- `turso_configured: false` → Turso is not set up at all; the brain icon
  renders a neutral gray/dim state (nothing is being claimed as unsaved,
  since local persistence is unaffected either way). This same object is
  also embedded as `agent_turso_sync` in every `agent_status` response
  (`GET /api/agent_status`, `/api/internal/projects/{id}/agent`,
  `/sycord/api/agent_status`) so callers already polling agent status do not
  need a second round-trip.

The GUI polls this route every 3 seconds while the "Agent chat" tab is open
(`syte/static/app.js`, `startDebugChatBrainPoll` / `pollDebugChatBrainOnce`),
independent of the 2-second activity poll, so the brain icon updates live as
new messages are produced during an in-progress turn.

## Compatibility health route

The old internal proxy path remains only for authenticated health checks:
`/agent/proxy`, `/agent/proxy/ready`, `/agent/proxy/health`, and
`/agent/proxy/alive`. Conversation proxy routes return HTTP 410. Use Syte's
communicate, change, activity, and lifecycle endpoints for all agent work.
