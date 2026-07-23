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
- `syra-ultra`: Forge grok-4.5 (`https://forge-gateway-api.fly.dev/v1`)

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
and structured tools. Available tools:

- list, read, write, and delete workspace files
- execute commands inside the project workspace
- inspect or control the isolated development preview (`service`)
- `update_plan` — publish steps; **persisted** in `agent_plans` and shown as chat thinking
- `screenshot_preview` — capture **desktop (1280×800) + phone (390×844)** screenshots of a
  preview route; saved under `data/cloud-agent/screenshots/`, mirrored in `agent_screenshots`,
  emitted as `screenshot` activity events (optimized thumbnails for chat), and injected as
  vision `image_url` parts for providers that support images (Gemini profiles; DeepSeek gets
  text metadata + URLs only). Requires a headless Chromium/Chrome on the Syte host
  (`chromium-browser` / `chromium` / `google-chrome`, installed by `scripts/install.sh`, or
  override with `SYTE_CHROMIUM_PATH`).
- `inspect_preview` — fetch preview HTML/text and (by default) open the route in Chromium
  DevTools to collect **browser console logs, page exceptions, network failures, and load
  status**. Use after UI edits to confirm the site actually loads with a clean console.
- `ask_question` — interactive mid-turn questions (`answer` / `input` / `slider` / `choice` /
  `multi_choice`); blocks until answered via API/GUI or times out
- `env_get` / `env_set` / `request_env` — project env access; `request_env` asks the user for
  missing keys as a question and writes the answer into env
- `list_mcp_addons` / `connect_mcp` / `call_mcp` — available MCP addons (built-in `syte` plus
  registered custom addons)
- `delegate_task` — bounded subagent

### Code policy

The agent builds **any** kind of code (libraries, CLIs, APIs, scripts, backends, data jobs,
etc.). It must **not** assume every request is a website. When the work *is* a website / web UI,
it must follow the Sycord Design Contract (shadcn/ui under `components/ui/*`, Lucide, Inter,
Tailwind tokens). The contract exposes a pinned catalog of 57 individual shadcn components and
patterns; shadcn Blocks and application-level direct Radix imports are rejected. Radix primitives
must stay behind local `components/ui/*` wrappers. **Do not** use HeroUI, NextUI, Chakra, MUI, or
Ant Design.

For a new website or substantive redesign, the runtime enforces a clarification-or-plan gate.
When the brief lacks a material design choice, the agent asks one batched question before planning.
Otherwise it plans first. File inspection and edits remain blocked until that sequence completes.

### Session stop markers

`stop`, `interrupt`, and cancelled turns write a row to local `agent_session_stops` with
`stopped_at`, emit `agent_stopped` (payload includes `stopped_at` / `reason` / `stop_id`), and
close the Turso session with status `stopped` (stop) or `cancelled` (interrupt/cancel). List
stops with `GET /api/agent_stops?uuid=` or `GET /api/projects/{id}/agent/stops`.

### MCP connections and skills

MCP providers and skills are managed per project from the agent chat resource panel
(**add / enable / disable / edit**) and the same operations are available directly via
API (GUI session routes and token `/api/agent_*` mirrors). Full reference:
[`docs/api-agent.md`](api-agent.md).

**MCP**

- Built-in `syte` addon tools: `syte_service` → project service actions,
  `syte_access` → preview access actions.
- Register custom stdio providers, connect/disconnect them, call tools, and edit
  non-builtin registrations.
- Agent tools: `list_mcp_addons` / `connect_mcp` / `call_mcp`.

**Skills**

- Built-in catalog: `website-editing`, `workspace-search`, `preview-access`,
  `service-management`, `nextjs-app-router`, `cli-tools`.
- Custom skills can be **added** (name + markdown/content guidance), edited, enabled,
  disabled, or deleted per project.
- Active skills (built-in and custom) are injected under `## Active Skills` in the
  system instruction.
- Enable stores optional string `parameters` (re-enable upserts / edits them);
  disable removes the project activation row; purge deletes a custom definition.

### Artifact APIs

| Resource | List | Notes |
|----------|------|--------|
| Screenshots | `GET /api/agent_screenshots?uuid=` | PNG at `/api/projects/{uuid}/agent/screenshots/{id}?variant=thumb\|full` |
| Plans | `GET /api/agent_plans?uuid=` | Steps + note from `update_plan` / thinking |
| Questions | `GET /api/agent_questions?uuid=` | Answer: `POST /api/agent_answer_question` |
| Skills | `GET /api/agent_skills?uuid=` | Add: `POST /api/agent_skills_add`; enable: `agent_skills_enable`; update: `agent_skills_update`; disable: `agent_skills_disable`; delete custom: `agent_skills_delete` |
| MCP addons | `GET /api/agent_mcp?uuid=` | Register / connect / call / update / disconnect via `agent_mcp_*` |
| Stops | `GET /api/agent_stops?uuid=` | Includes `stopped_at` |

### Layered memory, visual feedback, and design profiles

Syte keeps a local SQLite memory layer alongside Turso sessions so clients
(sycord.com) can resume work without re-scanning the whole workspace:

| Store | Purpose |
|-------|---------|
| `agent_session_meta` | Per-turn status, `active_files[]`, Turso session id |
| `agent_summaries` | Compressed “story so far” + key decisions after long sessions |
| `workspace_index` | Path / hash / semantic tags (`hero`, `navbar`, `colors`, …) |
| `visual_analyses` | Structured screenshot critiques (layout, issues, suggestions) |
| `design_profiles` | Persisted tokens + CSS + agent instructions per project |

**Resume:** `GET /api/projects/{id}/agent/sessions?resume=1` (and Sycord
`/sycord/api/agent_sessions?resume=1`) returns `resume_session`, `last_work`,
`active_files`, and `latest_summary`.

**Streaming:** Optional SSE at `/api/projects/{id}/agent/activity/stream` (and
`/sycord/api/agent_activity/stream`) for token-level updates; Turso session
polling remains the durable source of truth.

**Visual loop:** `screenshot_preview` stores a `visual_analyses` row. Call
`POST /sycord/api/improve_from_screenshot` (or chat with
`improve_from_screenshot=true`) to inject that analysis as the primary design
critique with a “minimal diffs” hint.

**Design system:** `POST /sycord/api/design_profile` with `theme_key` /
`style_key` (`saas-minimal`, `fintech-dark`, `ai-landing`, …) writes tokens into
SQLite and `data/design-system/`, then injects them into every turn’s system
prompt.

**External summary:** `GET /sycord/api/project_summary?uuid=` returns deployment
URL, design tokens, pages/active files, and last agent summary id. Configure
`webhook_urls` in system settings to receive `site.deployed` and
`agent.session.completed` events.

**Model routing:** When `model_profile` and `thinking_level` are omitted, short
copy tweaks auto-select `syra-nano`; full landing rebuilds / screenshot remakes
prefer `syra-havy`.

The system instruction is generated for Syte and includes project access rules,
enabled skills, workspace location, design contract (for websites), verification
requirements, and credential handling.

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
  -> [thinking | question | screenshot]   # optional plan / user prompt / preview shots
  -> (tool_call_started -> tool_call_finished)*
  -> request_completed | request_failed | agent_stopped  # terminal
```

Payload fields: `tool_call_started` carries `tool` and `arguments`;
`tool_call_finished` carries `tool` and `ok` (boolean success);
`request_completed` carries `reply`; `request_failed` carries `error` and
`retry_message`; `screenshot` carries `screenshots[]` with viewport metadata +
`image_url` / optional `chat_image_base64`; `question` carries `question_id`,
`question_type`, and widget fields; `agent_stopped` carries `stopped_at` and
`reason`. Lifecycle events `agent_started`, `agent_stopped`, and
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
`cancelled`, or `stopped` once it finishes. Clients that want to observe an
in-progress turn poll `GET /api/agent_session/{id}?since_id=<highest event.id seen>`
on a short interval (a few seconds) until `status != "open"`. When a `question`
event is pending, answer it (`POST /api/agent_answer_question` or the GUI widget)
before expecting the turn to complete.

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
    Paste the dashboard value as-is: Syte rewrites remote `libsql://`
    URLs to `https://` before connecting. That rewrite is required for
    AWS-hosted Turso databases (`*.aws-*.turso.io`), which reject
    WebSocket (`wss://`) upgrades with HTTP 400 / "Invalid response
    status". You may also paste an `https://…` URL directly.
  - `turso_auth_token` — optional; required for remote `libsql://` /
    `https://` databases, omitted for local `file:` URLs.
  - Set both from the Syte GUI's **Settings → AI** tab, or via
    `PUT /api/settings` with `turso_database_url` / `turso_auth_token` in the
    JSON body.
- **Client library**: [`libsql-client`](https://pypi.org/project/libsql-client/)
  (pinned `>=0.3.1,<1` in `requirements.txt` / `pyproject.toml`), the official
  async Python client for Turso/libSQL. Syte creates one client per
  `(url, token)` pair via `libsql_client.create_client(url, auth_token=token)`
  after normalizing the URL with `syte.turso_store.normalize_turso_url`,
  and caches it (`syte.turso_store._client_cache`); saving new settings calls
  `reset_client_cache()` so the next call picks up the new connection.
- **No connection = no-op, not an error.** Every read/write function in
  `syte.turso_store` (`open_session`, `close_session`, `record_event`,
  `record_message`, `list_messages`, `count_messages`, `get_session`,
  `list_sessions_for_project`, …) returns `None`, `False`, or an empty
  list/dict when `turso_database_url` is unset, and never raises out of the
  agent's request pipeline. Local persistence and the chat turn itself are
  completely unaffected.

### Real-time save on every message

`syte.cloud_agent._persist_message()` runs after every single local
`append_message()` call — for the admitted user message, for each assistant
turn (including ones that only contain tool calls), and for every tool
result — and immediately mirrors that one message into the shared
`agent_message` Turso table via `record_message()`. There is no batching and
no "sync at the end": each message is durably saved (or attempted) the
moment it is produced. This is identical whether the turn was started
synchronously (`agent_communicate`) or as a background job (`agent_change` /
`submit_agent_request`) — both paths call the same `_persist_message()` for
every message.

The one difference for a session **started directly via the API**
(`POST /api/agent_change`, the internal `/agent/change` route, or
`POST /sycord/api/agent_change`, all of which go through
`syte.agent_jobs.submit_agent_request`) is *when the Turso session itself is
opened*: it is created and its `request_started` event is recorded
synchronously during admission, **before** the background worker begins
processing the turn — so a durable, pollable session (and its first
activity event) exists immediately even though the actual chat messages are
still written message-by-message as the worker runs, exactly as they are for
the synchronous `agent_communicate` path.

### Retry & recovery

A message is flagged `turso_synced = 1` in the local `agent_messages` table
only after its `record_message()` mirror write to Turso actually succeeds.
If that single write fails (Turso down, network blip, etc.), the local
message is still saved and the turn is **not** blocked or failed — but the
row is left `turso_synced = 0` and is not automatically retried later. The
next message in the same turn is still attempted independently, so a single
transient failure does not cascade. `record_message()` is safe to retry
(the Turso `agent_message` table has a unique `(project_id,
local_message_id)` index, so re-sending an already-mirrored message returns
the existing row instead of creating a duplicate or a false failure), but
Syte does not currently run that retry automatically. Operators who need a
fully caught-up mirror after an outage should re-run the affected turn, or
add a background reconciliation job (not built in) that finds
`turso_synced = 0` rows and replays `record_message()` for them.

### Known limitation: sync status is keyed by session number, not session UUID

`turso_message_sync_status()` (and therefore the brain indicator) aggregates
local `agent_messages` rows by `(project_id, session_number)`, not by the
live `turso_session_id`. In the normal case these always refer to the same
Turso session. The one edge case where they can diverge: if the service
restarts mid-turn, `resume_pending_requests()` may reuse the project's
existing `session_number` while opening a **new** Turso session UUID for the
resumed turn. In that narrow window the sync aggregate would count rows
against a `session_number` that spans two different `turso_session_id`
values. This does not lose or corrupt any data — every row still records
its own correct `session_id` — but the aggregate `total_messages` /
`synced_messages` counts for that session number could include messages
mirrored into an earlier, now-superseded Turso session. Treat the brain
indicator as an operational health signal, not a byte-for-byte audit of one
specific Turso session UUID; use `GET /api/agent_session/{id}` for an exact,
UUID-scoped view of one session's contents.

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

### Diagnosing a stuck-red brain — `agent/turso_debug`

If the brain indicator stays red even though `turso_database_url` /
`turso_auth_token` look correct, call the companion diagnostic route:

`GET /api/projects/{project_id}/agent/turso_debug` (mirrored at
`GET /api/agent_turso_debug?uuid=`,
`GET /api/internal/projects/{project_id}/agent/turso_debug`, and
`GET /sycord/api/agent_turso_debug?uuid=`). Unlike `agent/turso_sync`
(which only reports counts), this route performs a **live round-trip**
against the configured database (build the client, run `SELECT 1`) and
reports exactly what's wrong:

```json
{
  "ok": true,
  "project_id": "myapp-a1b2c3",
  "configured": true,
  "database_url": "libsql://my-db-my-org.turso.io",
  "effective_url": "https://my-db-my-org.turso.io",
  "auth_token_set": true,
  "reachable": true,
  "error": "",
  "hint": "",
  "schema_ready": true,
  "schema_errors": ""
}
```

- `configured: false` → `turso_database_url` is empty; set it in
  Settings → AI.
- `reachable: false` → the URL/token pair is set but the live `SELECT 1`
  round-trip failed; `error` has the underlying exception text (bad token,
  network-unreachable host, wrong URL scheme, etc.). `hint` may include an
  operator-facing explanation when the failure matches a known pattern —
  most importantly the AWS Turso WebSocket rejection
  (`400` / `Invalid response status` / `protocol upgrade not supported`),
  which means the client tried `wss://` against a host that only speaks
  HTTPS. Syte rewrites `libsql://` → `https://` automatically
  (`effective_url`); if you still see that error after upgrading, re-save
  the Turso settings to clear any cached WebSocket client.
- `effective_url` → the URL actually passed to `libsql-client` after the
  `libsql://` → `https://` rewrite (identical to `database_url` when no
  rewrite applies, e.g. `file:` or already-`https:` URLs).
- `schema_errors` non-empty → the connection itself is fine, but one or more
  `CREATE TABLE` / `CREATE INDEX` statements were rejected by this specific
  Turso database (e.g. an index feature not supported by that
  database/plan). **This previously caused a permanently red brain despite
  fully valid credentials**: schema initialization used to abort entirely
  on the first failing statement, so every later call kept re-hitting (and
  re-failing on) the same bad statement forever. Schema init is now
  per-statement resilient — a single bad `CREATE INDEX` no longer blocks
  the `agent_message` table (or any other table) from being usable — but
  `schema_errors` still reports which statement failed so it can be
  corrected.

A second, related bug also caused false-red statuses: `record_message()`
used to run the `INSERT INTO agent_message` and the secondary, purely
cosmetic `UPDATE agent_session SET updated_at = ...` "touch" in the same
`try`/`except`. If the touch failed *after* the insert had already
committed, the exception handler still returned `None` — reporting a
message that was genuinely saved in Turso as unsynced. The touch is now
isolated in its own `try`/`except` so a message is reported saved as soon
as its `INSERT` succeeds, regardless of whether the secondary bookkeeping
write succeeds.

The Syte GUI automatically logs this diagnostic to the browser console
(grouped under `[Syte][turso]`) the moment the brain indicator turns red or
shows "not configured," so opening devtools while reproducing the issue is
usually enough to see the exact cause without a separate `curl` call.

## Compatibility health route

The old internal proxy path remains only for authenticated health checks:
`/agent/proxy`, `/agent/proxy/ready`, `/agent/proxy/health`, and
`/agent/proxy/alive`. Conversation proxy routes return HTTP 410. Use Syte's
communicate, change, activity, and lifecycle endpoints for all agent work.
