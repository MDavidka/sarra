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
- Durable sessions, messages, and pending requests: Syte SQLite database

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

Background chat returns a request ID immediately. Clients observe progress at:

`GET /api/projects/{uuid}/agent/activity/stream?live=1&since_id=N`

On connect the stream replays up to 500 persisted events with `id > since_id`,
then forwards live events. Because events are persisted *before* they are
broadcast, a client that records the highest `event.id` and reconnects with
`since_id=<id>` (or the standard SSE `Last-Event-ID` header, which the endpoint
translates to `since_id`) recovers every missed event with no gaps and no
duplicates.

Each accepted turn produces this ordered sequence, correlated by
`payload.request_id`:

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
`agent_restarted` are emitted on start/stop/restart. `token_delta` and
`message_snapshot` are reserved event types for a future token-streaming
provider and are not emitted by the current non-streaming runtime.

Besides `activity` events the stream emits control frames: a one-time
`retry: 5000` directive, a `session` marker (when `live=1`), a `ping` heartbeat
every 10 seconds carrying the current `since_id`, and a terminal `reconnect`
hint after the 3600-second per-connection deadline. Five encodings are stable
for Sycord clients and selected with `?format=`:

- `sse` (default) — JSON SSE; `activity` frames include an `id:` line.
- `tagged` — compact `[tag]<json>` records over `text/event-stream`.
- `marked` — session marks: `[boot]`, `[sessionN]`, and
  `S{session}{msg}(d|g)-<kind>text` so receivers can track going/done progress
  and reload only the latest session (`?session=last` on snapshots).
- `text` — plain text lines (`text/plain`; use `fetch`/`curl`, not EventSource).
- `jsonl` — one JSON object per line (`application/x-ndjson`).

### Marked stream (`?format=marked`)

Each user message opens a numbered chat session. The agent loads provider
history only from that latest session (prior sessions remain available on the
activity stream for clients). Marked lines look like:

```
data: [boot]
data: [session1]
data: S1001(d)-<user>Add dark mode
data: S1002(g)-<tool>read_file {"path":"app/page.tsx"}
data: S1003(d)-<tool>read_file ...
data: S1004(d)-<plan>1. Inspect theme 2. Patch toggle
data: [session2]
data: S2001(d)-<user>Also fix mobile nav
data: S2003(g)-<plan>Updating header
```

- `[boot]` — once on connect
- `[sessionN]` — when a user message starts agent work for session N
- `S{N}{mmm}(d|g)` — session number + zero-padded message index; `(d)` done,
  `(g)` going
- `<tool>` / `<plan>` / `<user>` / `<message>` / `<error>` / `<status>` — kind

An optional `types=` query parameter filters the tagged/marked/text/jsonl encodings by
event type. Snapshot polling is available at
`GET /api/projects/{uuid}/agent/activity?since_id=N` (optional `session=last`
or `session=2`) and its `/api/internal` and `/sycord/api` mirrors.

## Compatibility health route

The old internal proxy path remains only for authenticated health checks:
`/agent/proxy`, `/agent/proxy/ready`, `/agent/proxy/health`, and
`/agent/proxy/alive`. Conversation proxy routes return HTTP 410. Use Syte's
communicate, change, activity, and lifecycle endpoints for all agent work.
