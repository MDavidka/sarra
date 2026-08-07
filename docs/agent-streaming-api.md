# Syte Agent Streaming API

## Overview

Syte's cloud agent emits real-time events via Server-Sent Events (SSE) at:

- `GET /api/projects/{id}/agent/activity/stream` (session auth)
- `GET /api/agent_activity/stream?uuid=` (token API — external pages)
- `GET /sycord/api/agent_activity/stream?uuid=` (Sycord token API)
- Polling mirror: `GET /api/agent_activity?uuid=&since_id=N` / `GET /api/projects/{id}/agent/activity?since_id=N`
- Durable Turso sessions: `GET /api/agent_session/{turso_session_id}?since_id=N`

SSE frames are emitted as:

```
id: {event_id}
event: {event_type}
data: {JSON event object}
```

When the client sends `Accept-Encoding: gzip` (or `br` if Brotli is installed),
the SSE body is streamed with `Content-Encoding: gzip|br` so token frames stay
small on the wire.

### Hot path (token / thinking deltas)

`token_delta` and `thinking_delta` use a **minimal-delta** wire shape — only raw
text + a tiny header. Verbose fields (`project_id`, `role`, `title`, `source`,
`created_at`, `mark_kind`, …) are stripped on emit and on history replay.

```json
{
  "id": 42,
  "event_type": "token_delta",
  "detail": "partial text",
  "payload": {
    "delta": "partial text",
    "request_id": "req-…",
    "session": 42,
    "agent": "main"
  }
}
```

Deltas are **batched** before one frame: collect **2–32 tokens** or
**40–500 characters** (≈15 ms idle flush) — the first frame reaches the client
almost instantly, and slow trickles flush every 15 ms instead of every 80 ms.
Hot events **never** mirror to Turso; durable content lands in final
`assistant_message` / tool / request events. Cold (non-hot) events mirror to
Turso in the **background** so remote DB latency never blocks SSE or TTFT.

Cold (non-hot) events still use the full activity object:

```json
{
  "id": 42,
  "project_id": "proj_abc",
  "event_type": "tool_call_started",
  "role": "assistant",
  "title": "write_file",
  "detail": "…",
  "payload": { "...": "event-specific fields" },
  "source": "api",
  "created_at": "2026-07-20T14:30:00+00:00"
}
```

Clients should prefer `event_type` + `payload` for parsing.

### Control frames

Besides activity events the stream emits three control frames. They never belong
to the transcript and must not be rendered as messages.

| frame | when | client action |
| --- | --- | --- |
| `retry: 2000` | once, before the first frame | browsers adopt it as the reconnect delay |
| `: heartbeat` + `event: heartbeat` | every 10s of silence | treat the connection as alive |
| `event: stream_gap` | events were dropped for this subscriber under backpressure | backfill with `GET …/agent/activity?since_id=…&session=last` |

Heartbeats are sent **both** as an SSE comment (`: heartbeat`) and as a named
data frame. The comment alone is invisible to proxies that only count `data:`
frames, and to clients that never see comments at all. The 10s cadence is
deliberately below common proxy/CDN idle timeouts.

`stream_gap` carries `{"dropped": n, "last_id": id}`. It exists because the
server drops the oldest queued event when a slow subscriber's queue fills; a
client that ignores this frame will silently miss events.

> **Compression note:** the stream is optionally brotli/gzip/deflate encoded and
> the compressor is flushed after **every** frame. A compressor that only
> flushes on close buffers frames indefinitely, which is indistinguishable from
> a dead connection. Brotli is only negotiated when the installed build exposes
> `flush()`.

There is **no** stream event named `running since` / `start`. Runtime readiness
uses `GET /agent_status` → `agent_last_started_at` / `agent_running`. Turn busy
is `processing` / `request_started`.

### Chat lanes: `payload.agent`

Every event carries `payload.agent`, the lane it belongs to:

| value | meaning | rendered in |
| --- | --- | --- |
| `"main"` | produced by the main agent | Main tab |
| `"subagent"` | produced by a delegated subagent | subagent tab |

Subagent events additionally carry `payload.subagent_task_id` (and
`subagent_mode` / `subagent_task`), so a client can group several concurrent
subagents. The Syte GUI keeps the two feeds in separate panels: the Main tab
never shows subagent activity and vice versa. The subagent tab appears the first
time a subagent starts.

> **Browser `EventSource` note:** `EventSource.onmessage` only receives frames with
> no `event:` field (or `event: message`). Named frames such as `event: token_delta`
> require `addEventListener("token_delta", …)` (or an equivalent per-type listener).
> The Syte GUI binds listeners for every activity event type.

## Event Types

### `token_delta`

Streamed LLM output tokens (batched, slim wire).

**payload (minimal):**

```json
{
  "request_id": "req-…",
  "session": 42,
  "delta": "partial text",
  "agent": "main"
}
```

`token_delta` (and `thinking_delta`) are **hot stream** events: they are pushed
live over SSE and skip durable Turso mirroring to keep latency low. Poll
clients should still use `since_id` for other event types; hot deltas may only
appear on the live SSE channel.

### `thinking_delta`

Streamed model reasoning / chain-of-thought chunks (when the selected model
supports native thinking and the turn's `thinking_level` enables it).

**payload (minimal):**

```json
{
  "request_id": "req-…",
  "session": 42,
  "delta": "partial reasoning text",
  "agent": "main"
}
```

Notes:

- Bind `addEventListener("thinking_delta", …)` — `EventSource.onmessage` will not
  receive named frames.
- History persistence may truncate long reasoning (`… [thinking truncated]`); the
  live SSE stream still emits the full deltas.
- If the client requests thinking on a model that does not support it, the
  backend emits a one-shot `status` event (`Thinking not supported`) and omits
  native thinking params from the provider payload.

### `tool_call_started` / `tool_call_finished`

Agent invokes a tool / tool returns.

**payload (started):**

```json
{
  "request_id": "req-…",
  "session": 42,
  "message_index": 3,
  "mark": "S42003(g)",
  "tool": "write_file",
  "arguments": { "path": "app/app/page.tsx", "content": "…" },
  "phase": "started"
}
```

**payload (finished):** includes `phase: "finished"`, `ok`, and a truncated `result`.

### `question`

Agent asks the user an interactive question (blocking until answered).

**payload:**

```json
{
  "question_id": "q_abc123",
  "question_type": "choice",
  "options": ["Blue", "Green", "Purple"],
  "min_value": null,
  "max_value": null,
  "step_value": null,
  "default_value": null,
  "status": "pending",
  "session": 42,
  "request_id": "req-…"
}
```

Answer via `POST /api/projects/{id}/agent/questions/{question_id}/answer` with
`{"answer": "Blue"}` (or the matching Sycord / token-API answer endpoint).

### `thinking` (plan)

Agent publishes or updates an execution plan (`update_plan` tool or extracted thinking).

**payload:**

```json
{
  "plan_id": "plan_xyz",
  "steps": ["Step 1: …", "Step 2: …"],
  "session": 42,
  "request_id": "req-…",
  "step": 3,
  "phase": "before_tools",
  "thinking_tools": ["write_file"],
  "thinking_targets": ["write_file app/app/pricing/page.tsx"],
  "thinking_where": "step 3 · before write_file app/app/pricing/page.tsx"
}
```

The `step` / `phase` / `thinking_*` fields describe **where** the model was
thinking, so a client can label the block instead of showing bare reasoning
text. `phase` is one of `planning`, `site_plan`, `before_tools`, `final_answer`.
`thinking_where` is a pre-rendered human label; the individual fields are
provided so clients can format their own.

### `subagent_scope`

Published in the **main** lane immediately before work is delegated. It declares
the file scope handed to the subagent: those paths are locked for the duration,
so neither the main agent nor another subagent can write them (attempts fail with
`file_reserved_by_subagent` / `file_scope_conflict`).

**payload:**

```json
{
  "agent": "main",
  "task_id": "sub-abc",
  "task": "Build the FAQ page",
  "mode": "implementation",
  "files": ["app/app/faq/page.tsx", "app/components/faq.tsx"],
  "reserved_files": ["app/app/faq/page.tsx", "app/components/faq.tsx"],
  "locked": true,
  "background": true,
  "session": 42,
  "request_id": "req-…"
}
```

`files` is required for `mode=implementation` delegations; research (read-only)
delegations may omit it, in which case `locked` is `false`.

### `subagent_started` / `subagent_completed` / `subagent_failed`

Subagent lifecycle, emitted in the **subagent** lane
(`payload.agent = "subagent"`). Terminal events include `usage` and `cost`.

**payload:**

```json
{
  "agent": "subagent",
  "task_id": "sub-abc",
  "mode": "implementation",
  "profile": "syra-nano",
  "files": ["app/app/faq/page.tsx"],
  "ok": true,
  "usage": { "input_tokens": 300, "output_tokens": 120, "steps": 3 },
  "cost": { "cost_usd": 0.0009, "label": "$0.0009 · 420 tokens" },
  "session": 42,
  "request_id": "req-…"
}
```

The subagent's own tool calls and reasoning arrive as ordinary
`tool_call_started` / `tool_call_finished` / `thinking` / `assistant_message`
events tagged with `agent: "subagent"` and the same `task_id`. They are also
persisted to `agent_subagent_activity` (see
[Turso persistence](turso-persistence.md)).

### `screenshot`

Agent captured a preview screenshot (optionally with visual analysis ids).

**payload:**

```json
{
  "route": "/",
  "url": "https://preview.example/",
  "screenshots": [
    {
      "id": "screenshot_123",
      "viewport": "desktop",
      "width": 1280,
      "height": 800,
      "image_url": "/api/projects/{id}/agent/screenshots/screenshot_123",
      "thumb_url": "/api/projects/{id}/agent/screenshots/screenshot_123?variant=thumb",
      "ok": true
    }
  ],
  "session": 42,
  "request_id": "req-…"
}
```

Related visual analyses are available at
`GET /api/projects/{id}/agent/visual_analyses`.

Screenshot captures are rate limited per turn (`MAX_SCREENSHOTS_PER_TURN`, plus a
cooldown per route). A repeat of a route already captured while no file has been
written since is answered from cache — the tool result carries
`skipped: true, reason: "unchanged_since_last_capture"` and no new `screenshot`
event is emitted. Over-budget or too-frequent calls return
`screenshot_budget_exhausted` / `screenshot_rate_limited` so the model stops
polling and uses `inspect_preview` for load/console checks instead.

### `request_started` / `request_completed` / `request_failed`

Turn lifecycle markers. `request_completed` is the normal successful end of a turn.

### `agent_stopped`

Turn or session was stopped/interrupted (user cancel, stop API, or cooperative
cancel). Prefer treating this as terminal for the current turn. Closely related
to `session_stopped`; clients should handle **both**.

**payload:**

```json
{
  "stopped_at": "2026-07-20T14:30:00+00:00",
  "reason": "interrupted",
  "session": 42,
  "turso_session_id": "ts_abc",
  "stop_id": 7
}
```

`reason` values include `stopped`, `interrupted`, `cancelled`, and `completed`.

### `session_stopped`

Emitted when a turn/session is **actually stopped** (user cancel, stop API, or
interrupt). Successful turns emit ``request_completed`` only — they no longer
emit ``session_stopped`` with ``reason: completed``, so clients do not treat the
agent as idle while follow-up work continues in the same chat session.

**payload:**

```json
{
  "reason": "interrupted",
  "stopped_at": "2026-07-20T14:30:00+00:00",
  "session": 42,
  "turso_session_id": "ts_abc",
  "request_id": "req-…"
}
```

`reason` values include `stopped`, `interrupted`, and `cancelled`.
### `tool_error`

Structured tool failure for observability (does not replace `tool_call_finished`).

**payload:**

```json
{
  "tool": "run_command",
  "error_type": "timeout",
  "retryable": true,
  "session": 42,
  "request_id": "req-…"
}
```

#### Common `error_type` values

| `error_type` | Meaning | Typical `retryable` |
|--------------|---------|---------------------|
| `plan_required` | Deep/Max gate: call `update_plan` first | `true` |
| `invalid_pattern` | `search_code` pattern missing/invalid | `false` |
| `invalid_path` | Path outside workspace | `false` |
| `invalid_arguments` | MCP/builtin tool schema validation failed | `false` |
| `unknown_tool` | MCP tool name not registered | `false` |
| `not_found` | Addon/project/resource missing | `false` |
| `timeout` / `search_failed` | Subprocess or network timeout | `true` |
| `tool_failed` | Generic tool failure (see `message`) | varies |
| `cancelled` | Tool aborted by interrupt/stop | `false` |
| `subagent_timeout` | Subagent wall-clock timeout | `true` |
| `subagent_queue_full` | Too many background subagents for the project | `true` |
| `research_readonly` | Mutating tool blocked in research-mode subagent | `true` |
| `await_timeout` | `await_subagent` timed out; subagent still running | `true` |
| `mcp_dispatch_unsupported` | Custom MCP stdio dispatch disabled | `false` |
| `builtin_readonly` | Attempted to edit built-in MCP addon | `false` |

## Event Ordering Guarantees

- `token_delta` events arrive in-order within a single assistant message
- `tool_call_started` → `tool_call_finished` pairs are sequential per tool call
- `question` is blocking; the agent waits until answered (or times out)
- `request_completed`, `request_failed`, or `session_stopped` ends the turn

## Reconnection & poll backoff

SSE / poll clients should:

1. Track the last seen event `id` (and optionally `payload.session`)
2. On disconnect, reconnect with `?since_id={last_id}` (and keep `session=last` /
   `session={N}` if you were filtering)
3. The server returns events with `id > since_id` only — never a full replay of
   older rows. Example: after processing id `120`, reconnect with
   `GET /api/projects/{id}/agent/activity?since_id=120&session=last`
4. SSE reconnect uses the same rule: `GET …/activity/stream?since_id=120`. When
   `since_id > 0`, the backlog window is kept small (delta-oriented); cold
   connects (`since_id=0`) still receive a bounded recent backlog
5. If `since_id` is ahead of the store, the result set is empty until new events
   arrive (no wrap / no 410)
6. Polling backoff recommendation: start at **500ms**, double after empty polls up to
   **5s**, reset to 500ms when new events arrive; keep a long-poll style SSE open when
   possible instead of busy-polling
7. Cap concurrent pollers per session to 1 in the BFF to avoid stampeding Turso
8. **Never poll with `since_id=0` after the first snapshot** — that re-downloads the
   full recent backlog on every tick and is the main cause of growing payload size

Optional: `session=last` or `session={N}` filters to the latest / specific numbered
chat session.

### Status fields vs busy

- `agent_busy` / in-memory turn marker: true only while a turn task is active
- `agent_status=running`: runtime is ready (not the same as busy). Interrupt clears
  busy immediately but leaves `running` so the next message can start without
  `start_agent` again. Use `agent_busy` (or SSE `agent_stopped`) for UI spinners.

### Minimal incremental poll example

```js
let sinceId = 0;
async function pollActivity(projectId) {
  const url = `/api/projects/${projectId}/agent/activity?since_id=${sinceId}&session=last`;
  const res = await fetch(url, { credentials: "same-origin" });
  const data = await res.json();
  for (const event of data.events || []) {
    sinceId = Math.max(sinceId, event.id);
    // handle event.event_type / event.payload
  }
}
```

## Visual analyses

Related visual analyses are available at:

`GET /api/projects/{id}/agent/visual_analyses`

**Response shape (array items):**

```json
{
  "id": "va_…",
  "project_id": "proj_…",
  "screenshot_id": 123,
  "score": 0.72,
  "summary": "Spacing on the hero feels tight…",
  "issues": [{"severity": "spacing", "detail": "…"}],
  "suggestions": ["Increase hero padding"],
  "created_at": "2026-07-20T14:30:00+00:00"
}
```

Use `visual_analysis_id` on chat / `agent_change` to attach a specific analysis as
critique context, or `improve_from_screenshot: true` for the latest analysis.
