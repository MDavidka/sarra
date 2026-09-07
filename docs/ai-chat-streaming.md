# Syte AI Chat & Activity SSE Streaming

The dedicated streaming window (`/api/stream`), the wire contract, the latency
model, and **how every AI-chat tool type is streamed**.

| Document | Scope |
| --- | --- |
| This file | AI Builder chat/activity stream: `/api/stream/*`, `/api/projects/{id}/ai/*` |
| [agent-streaming-api.md](agent-streaming-api.md) | Sycord cloud-agent activity API (separate subsystem) |
| [turso-persistence.md](turso-persistence.md) | Durable session mirroring |

Machine-readable version of the endpoint map: `GET /api/stream`.

---

## 1. The streaming API window

All SSE traffic lives under one prefix so it is trivial to proxy, tune, and
monitor:

| Endpoint | Method | Purpose |
| --- | --- | --- |
| `/api/stream` | GET | Discovery catalog (endpoints, tuning knobs, reconnect rules) |
| `/api/stream/health` | GET | Liveness probe (exempt from rate limiting) |
| `/api/stream/projects/{id}/chat` | POST | Start/attach an agent turn, stream it as SSE |
| `/api/stream/projects/{id}/events` | GET | Subscribe / reconnect (`since_id`, `replay`, `Last-Event-ID`) |
| `/api/stream/projects/{id}/activity` | GET | JSON poll mirror (`since_id`, `limit`) |
| `/api/stream/projects/{id}/status` | GET | Session snapshot: busy, plan, pending question, `last_event_id` |
| `/api/stream/projects/{id}/answer` | POST | Resolve a pending question / secret gate |
| `/api/stream/projects/{id}/stop` | POST | Interrupt the running turn |
| `/api/stream/projects/{id}/logs/stream` | GET | Deploy/build/app log tail SSE |
| `/api/stream/projects/{id}/preview/logs/stream` | GET | Preview dev-server log tail SSE |

Legacy equivalents (`/api/projects/{id}/ai/chat`, `/ai/events`) remain mounted
for the GUI and share the same optimized core.

Auth: `chat`, `events`, `activity`, `status`, `answer`, `stop` require an
operator session cookie or API token (`X-API-Key` / `Authorization`). Log
streams verify the key only when one is sent (matches legacy behavior).

## 2. Wire format

Every frame is standard SSE, compact-JSON encoded (`separators=(",",":")`,
`ensure_ascii=False`):

```
retry: 2000

id: 42
event: tool_call_result
data: {"event":"tool_call_result","tool_name":"syte_write_file",...,"id":42,"timestamp":"2026-09-07T16:30:00.123+00:00"}
```

* The JSON payload **always embeds `event`** — fetch-based clients (the Syte
  GUI) can parse only `data:` lines; native `EventSource` clients can bind
  per-type listeners on the `event:` line.
* `id:` is a monotonic per-session counter. `EventSource` reconnects send it
  back as `Last-Event-ID`; fetch clients pass `?since_id=`.
* Response headers: `Cache-Control: no-cache, no-store, no-transform`,
  `X-Accel-Buffering: no` (defeats nginx/Caddy buffering). No `Connection`
  header — it is hop-by-hop and invalid to set from the app.

### Control frames (never part of the transcript)

| Frame | When | Client action |
| --- | --- | --- |
| `retry: 2000` | once, before the first frame | adopt as reconnect delay |
| `: heartbeat` + `event: ping` | every 10 s of silence | treat connection as alive; ignore |
| `event: stream_gap` | frames dropped for this subscriber | backfill via `GET …/activity?since_id={last_id}` |

`stream_gap` payloads: `{"event":"stream_gap","dropped":n,"last_id":id,"reason":"backpressure"|"replay_window_expired"}`.
`backpressure` = this client's bounded queue (256 frames) overflowed;
`replay_window_expired` = the requested `since_id` fell out of the 300-event
retention window.

## 3. Latency model (what is fast, and why)

The pipeline is: provider SSE → `providers.stream_chat` → `engine` yields →
`session.add_event` → per-subscriber queue → socket.

| Stage | Mechanism | Cost |
| --- | --- | --- |
| Provider → engine | pooled `httpx.AsyncClient` (HTTP/2, keep-alive), consumed with `aiter_lines()` directly on the event loop | zero thread hops (the old `urllib` path paid a `asyncio.to_thread` hop **per SSE line**) |
| Engine → session | plain dict yields; hot deltas carry no timestamp | negligible |
| Session broadcast | event JSON encoded **once** to `bytes`; every subscriber queue receives the same immutable frame | O(1) serialization per event, not O(clients) |
| Session → wire | hot deltas coalesced: ≤ 32 deltas / 500 chars / 15 ms idle window → one frame; cold events flush pending deltas first (order preserved) | first token ≤ 15 ms; ~20–60× fewer frames on token storms |
| Backpressure | bounded 256-frame queues; oldest frame dropped + `stream_gap` | a slow client can never stall the agent loop |
| Connection reuse | TLS session reuse across turns via shared client pool | removes handshake from TTFT of every turn after the first |
| End-of-stream | replay reaching a terminal event on an idle session closes the response immediately | no zombie connections |

Turn-scoped fields: every event from one turn carries the same
`request_id` (`req-<12 hex>`) and `turn` number, so clients can group,
deduplicate, and measure.

## 4. Event catalog (chat/activity stream)

### Hot (batched, live-only, never replayed)

| Event | Payload | Notes |
| --- | --- | --- |
| `token_delta` | `{event, delta, request_id, turn, [batch_count], id, timestamp}` | assistant text; merged frames carry `batch_count` |
| `thought_delta` | same shape | model reasoning (DeepSeek/Qwen `reasoning_content`, Anthropic `thinking_delta`) |

### Cold (retained in the 300-event replay window)

| Event | Payload highlights | Meaning |
| --- | --- | --- |
| `user_message_received` | `content, request_id` | turn accepted |
| `tool_call_start` | `tool_call_id, tool_name, arguments, file_path, command, message, request_id, turn` | tool invocation begins |
| `tool_call_result` | `tool_call_id, tool_name, result, duration_ms, ok, request_id, turn` | tool returned (see §5 per tool) |
| `user_input_required` | `question_data` | interactive gate opened |
| `user_input_received` | `tool_call_id, tool_name, user_response` | gate resolved |
| `done` | `reply, request_id, turn, turn_duration_ms` | turn completed successfully |
| `stopped` / `cancelled` | `message` | user/stop-API interrupt |
| `error` | `error` | fatal turn error |
| `session_idle` | — | background task finished; stream closes after this |

### Transient (live-only, excluded from replay — like hot deltas)

| Event | Payload | Meaning |
| --- | --- | --- |
| `status` | `message, [tool_name, file_path, command], request_id, turn` | human-readable progress line; UI shows as the spinner label |

## 5. Per-tool streaming contract

Every tool in `syte/ai/tools.py` follows the same three-frame pattern:

```
event: status            → spinner label ("Creating file: app/page.tsx…")
event: tool_call_start   → arguments (file `content` truncated to 400 chars + content_bytes)
   … tool runs …
event: tool_call_result  → result (large string fields truncated to ~2000 chars, lists to 40 items)
```

Streamed results are **wire-light copies**: `content`, `stdout`, `stderr`,
`output`, `tree`, `diff` are truncated (`…[truncated N chars for stream]…`)
and `logs`/`files`/`matches`/`results` lists keep the last 40 entries with a
`{key}_total` count. The model context and the DB always receive the full,
untruncated result. `duration_ms` + `ok` are on every `tool_call_result`.

### 5.1 File tools

| Tool | `tool_call_start.arguments` | `tool_call_result.result` |
| --- | --- | --- |
| `syte_read_file` | `{path}` | `{ok, path, size_bytes, content*}` — `content` streamed truncated; full text only to model/DB |
| `syte_read_file_lines` | `{path, start_line, end_line}` | `{ok, path, start_line, end_line, total_lines, content*}` (`content` = numbered lines) |
| `syte_write_file` | `{path, content*}` — streamed `content` ≤ 400 chars + `content_bytes` | `{ok, path, written_bytes, message}` |
| `syte_edit_file` | `{path, old_text, new_text}` | `{ok, path, message}` |
| `syte_move_file` | `{source_path, destination_path}` | `{ok, source, destination, message}` |
| `syte_delete_file` | `{path}` | `{ok, path, message}` |
| `syte_list_files` | `{directory, max_depth}` | `{ok, directory, count, files*}` |
| `syte_get_workspace_tree` | `{max_depth}` | `{ok, workspace_tree*}` |
| `syte_search_files` | `{query, file_pattern}` | `{ok, query, matches_count, matches*}` (`matches` = `{file,line,content≤180}`) |

UI note: the GUI renders `syte_write_file` / `syte_edit_file` as file badges
from `tool_call_start.file_path` — the badge appears the moment the call
starts, before the result frame.

### 5.2 Shell / execution tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_run_command` | `{command}` | `{ok, command, exit_code, stdout*, stderr*}`; failed runs also carry `remediation_directive` (model-only guidance) |

Commands run up to 120 s. Between `tool_call_start` and `tool_call_result`
the GUI keeps a spinner row keyed by `tool_call_id`; the result frame
replaces it with the terminal card (exit badge + truncated output).

### 5.3 Git tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_git_status` | `{}` | `{ok, status, recent_commits[], branch, git_url}` |
| `syte_git_commit` | `{message, files[]}` | `{ok, stdout*, stderr*, message}` |
| `syte_git_push` | `{branch}` | `{ok, stdout*, stderr*}` |
| `syte_git_pull` | `{branch}` | `{ok, stdout*, stderr*}` |
| `syte_git_create_branch` | `{branch_name}` | `{ok, stdout*, stderr*, branch}` |

### 5.4 GitHub account tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_github_account_info` | `{}` | `{ok, connected, account{login,scopes,…}}` |
| `syte_github_list_repos` | `{query}` | `{ok, connected, count, repositories*}` |

### 5.5 Deployment / log tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_create_deployment` | `{}` | `{ok, status:"queued", message, project_id}` — the deploy itself streams on the **log stream** (`/api/stream/projects/{id}/logs/stream`), not the chat stream |
| `syte_get_deployment_logs` | `{limit, filter_keyword, log_level, diagnose}` | `{ok, logs*(last 40 streamed), lines_count, has_errors, diagnosis}` |
| `syte_get_router_logs` | `{search, status_code, limit}` | `{ok, router_logs*, count}` |

### 5.6 Preview tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_start_preview` | `{}` | `{ok, message, preview_url, preview_domain, preview_port, status, stack}` — GUI renders a preview banner from `result.preview_url` |
| `syte_stop_preview` | `{}` | `{ok, message}` |
| `syte_get_preview_status` | `{}` | `{ok, running, preview_url, status, meta}` |

Live dev-server output after `syte_start_preview` streams on
`/api/stream/projects/{id}/preview/logs/stream` (frames:
`{"type":"preview"|"session","text":line}` + periodic `{"type":"ping"}`).

### 5.7 Platform / environment tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_get_performance` | `{}` | `{ok, project_running, cpu_percent, ram_used_mb, ram_total_mb, disk_percent}` |
| `syte_get_environment` | `{}` | `{ok, environment_variables}` — values are server-side; keep redaction in UI |
| `syte_set_environment` | `{key, value}` | `{ok, key, message}` — never echoes the value into the stream |
| `syte_manage_domains` | `{action, domain}` | `{ok, domains[]}` or `{ok, domain, message}` |

### 5.8 Planning tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_create_plan` | `{title, steps[], rationale}` | `{ok, plan{title,steps[{id,title,status,description}],rationale}, steps_count, message}` — GUI renders/refreshes the plan card from `result.plan` |
| `syte_update_plan_step` | `{step_id, status, notes}` | `{ok, step_id, status, notes, message}` |

The session also keeps `active_plan` server-side (`GET …/status`), so a
reconnecting client can rehydrate the plan card without replaying tool
frames. If the model emits a markdown step list instead of calling
`syte_create_plan`, the engine auto-synthesizes the plan and streams it as a
`tool_call_result` with `tool_call_id: "auto_plan_1"`.

### 5.9 Skills tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_list_skills` | `{}` | `{ok, skills[], count}` |
| `syte_discover_skills` | `{category, query, detailed}` | `{ok, categories/skills…}` |
| `syte_load_skill` | `{skill_name}` | `{ok, skill_name, content*, message}` — GUI renders a skill badge from `result.skill_name` |

### 5.10 Interactive tools (blocking gates)

`syte_ask_question` and `syte_ask_env_var` stream **two** result frames:

```
event: tool_call_result        → result.requires_user_input = true
                                 question: {question, options[], allow_custom}
                                 secret:   {key, description, hint, is_secret_request}
event: user_input_required     → question_data (same payload, drives the card UI)
   … turn pauses (≤ 300 s) …
event: user_input_received     → {tool_call_id, tool_name, user_response}
```

Answer with `POST …/answer`:
`{"answer": "Blue"}` or `{"is_secret": true, "key": "STRIPE_KEY", "value": "sk_live_…"}`.
Secrets are stored server-side in the project env and **never** streamed
back — `user_response` carries only the masked confirmation. On timeout the
gate resolves with `{"timeout": true, "answer": "…proceeding with defaults"}`.

### 5.11 Quality tools

| Tool | Start args | Result |
| --- | --- | --- |
| `syte_security_lint_scan` | `{paths?}` | `{ok, scanned_files_count, syntax_errors[], security_issues[], …}` — GUI renders a scan summary card |

## 6. Reconnection & backfill

1. Track the last `id` you processed (or `payload.id`).
2. Reconnect `GET …/events?since_id={id}` (or let `EventSource` send
   `Last-Event-ID` automatically).
3. You receive retained **cold** events with `id > since_id`, then live
   frames. Hot deltas from the gap are *not* replayed — if the gap spans a
   completed assistant message, fetch history from
   `GET /api/projects/{id}/ai/history`.
4. `stream_gap{reason:"replay_window_expired"}` → your cursor is older than
   the 300-event window; refresh from history and resubscribe with
   `since_id = status.last_event_id` (see `GET …/status`).
5. Poll fallback: `GET …/activity?since_id={id}&limit=200` returns
   `{events, last_id, is_running}` — same ids, same objects as the stream.
6. A stream closes cleanly after `session_idle` (or after replaying a
   terminal event to an idle client). End-of-stream ≠ error; reconnect on the
   next user turn.

## 7. Client examples

### fetch + ReadableStream (what the Syte GUI does)

```js
const res = await fetch(`/api/stream/projects/${id}/chat`, {
  method: "POST", credentials: "same-origin",
  headers: { "Content-Type": "application/json", "X-Syte-CSRF": csrf },
  body: JSON.stringify({ message }),
});
const reader = res.body.getReader(), dec = new TextDecoder();
let buf = "";
for (;;) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  const lines = buf.split("\n"); buf = lines.pop();
  for (const line of lines) {
    if (!line.startsWith("data: ")) continue;
    const evt = JSON.parse(line.slice(6));   // evt.event works for every type
    handle(evt);
  }
}
```

### EventSource (read-only activity view)

```js
const es = new EventSource(`/api/stream/projects/${id}/events?since_id=${lastId}`,
                           { withCredentials: true });
es.addEventListener("token_delta", e => append(JSON.parse(e.data).delta));
es.addEventListener("stream_gap",  e => backfill(JSON.parse(e.data)));
// remember: named frames need per-type listeners; es.onmessage only sees
// frames without an event: field.
```

### Poll fallback

```js
let since = 0;
setInterval(async () => {
  const { events, last_id } = await fetch(
    `/api/stream/projects/${id}/activity?since_id=${since}`).then(r => r.json());
  since = last_id;
  events.forEach(handle);
}, 800);
```

## 8. Tuning knobs

All in `syte/sse_core.py` (surfaced live by `GET /api/stream`):

| Constant | Default | Effect |
| --- | --- | --- |
| `HOT_FLUSH_CHARS` | 500 | max merged delta chars |
| `HOT_FLUSH_COUNT` | 32 | max merged deltas per frame |
| `HOT_FLUSH_SECONDS` | 0.015 | idle flush window (upper bound on added latency) |
| `SUBSCRIBER_QUEUE_SIZE` | 256 | per-client queue depth before drop+gap |
| `HEARTBEAT_SECONDS` | 10 | silence keepalive cadence |
| `RETRY_MS` | 2000 | advertised browser reconnect delay |
| `EVENT_BUFFER_SIZE` | 300 | cold-event replay window |

## 9. Log stream frames

`/api/stream/projects/{id}/logs/stream` and `…/preview/logs/stream` emit
`data: {"type":"deploy"|"build"|"app"|"container"|"preview"|"session"|"ping","text":…}`
— plain JSON lines (no `id:`/replay semantics). They share the SSE headers
and heartbeat philosophy but not the activity event model.
