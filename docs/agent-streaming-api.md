# Syte Agent Streaming API

## Overview

Syte's cloud agent emits real-time events via Server-Sent Events (SSE) at:

- `GET /api/projects/{id}/agent/activity/stream` (session auth)
- `GET /sycord/projects/{project_id}/activity` (token auth)
- Polling mirror: `GET /api/projects/{id}/agent/activity?since_id=N`
- Durable Turso sessions: `GET /api/agent_session/{turso_session_id}?since_id=N`

SSE frames are emitted as:

```
id: {event_id}
event: {event_type}
data: {JSON event object}
```

The `data` payload is always a full activity event:

```json
{
  "id": 42,
  "project_id": "proj_abc",
  "event_type": "token_delta",
  "role": "assistant",
  "title": "Stream",
  "detail": "…",
  "payload": { "...": "event-specific fields" },
  "source": "api",
  "created_at": "2026-07-20T14:30:00+00:00"
}
```

Clients should prefer `event_type` + `payload` for parsing. Heartbeats are sent as
SSE comments: `: heartbeat`.

> **Browser `EventSource` note:** `EventSource.onmessage` only receives frames with
> no `event:` field (or `event: message`). Named frames such as `event: token_delta`
> require `addEventListener("token_delta", …)` (or an equivalent per-type listener).
> The Syte GUI binds listeners for every activity event type.

## Event Types

### `token_delta`

Streamed LLM output tokens.

**payload:**

```json
{
  "request_id": "req-…",
  "session": 42,
  "delta": "partial text",
  "mark_kind": "stream"
}
```

`token_delta` (and `thinking_delta`) are **hot stream** events: they are pushed
live over SSE and skip durable Turso mirroring to keep latency low. Poll
clients should still use `since_id` for other event types; hot deltas may only
appear on the live SSE channel.

### `thinking_delta`

Streamed model reasoning / chain-of-thought chunks (when the selected model
supports native thinking and the turn's `thinking_level` enables it).

**payload:**

```json
{
  "request_id": "req-…",
  "session": 42,
  "delta": "partial reasoning text",
  "mark_kind": "thinking"
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
  "request_id": "req-…"
}
```

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

Session ended (completed, interrupted, or errored). Always treat this as terminal
for the turn when present.

**payload:**

```json
{
  "reason": "completed",
  "stopped_at": "2026-07-20T14:30:00+00:00",
  "session": 42,
  "turso_session_id": "ts_abc",
  "request_id": "req-…"
}
```

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
