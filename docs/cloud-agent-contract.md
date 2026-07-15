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

## Compatibility health route

The old internal proxy path remains only for authenticated health checks:
`/agent/proxy`, `/agent/proxy/ready`, `/agent/proxy/health`, and
`/agent/proxy/alive`. Conversation proxy routes return HTTP 410. Use Syte's
communicate, change, activity, and lifecycle endpoints for all agent work.
