# Syte OpenHands Agent Contract

Syte runs one persistent OpenHands Agent Server for each project workspace. The
server owns the durable conversation, while Syte owns process lifecycle,
provider configuration, access isolation, and the browser-facing activity feed.

## Runtime

- Project data: `workspaces/<uuid>/data/openhands/`
- Agent Server configuration: `agent_server_config.json`
- Conversation storage: `conversations/`
- Agent log: `agent-server.log`
- Workspace: `workspaces/<uuid>/app/`
- Runtime status and conversation ID are persisted on the project record.

OpenHands receives the existing Syra provider profiles:

- `syra-nano` — Gemini Flash through its OpenAI-compatible endpoint
- `syra-base` — DeepSeek Chat
- `syra-havy` — Gemini Pro through its OpenAI-compatible endpoint

Provider keys are stored under the `agent_*` settings namespace and sent only
to the loopback Agent Server when a conversation is created or its model is
switched.

### Always-on warm lifecycle

Opening Agent chat calls `POST /api/projects/{uuid}/agent/warm`. This endpoint
returns immediately and starts OpenHands in a deduplicated background task.
After the server reaches `/ready`, the project keeps `agent_status=running`.
The supervisor health-checks every desired-running project and schedules a
restart if its process exits or `/ready` stops succeeding. A deliberate
`POST .../agent/stop` changes the desired status to `stopped`, so the supervisor
does not resurrect it.

Warm-up endpoints:

- GUI: `POST /api/projects/{uuid}/agent/warm`
- token API: `POST /api/agent_warm` with `{"uuid":"..."}`
- internal: `POST /api/internal/projects/{uuid}/agent/warm`

Calls are idempotent while startup is in flight. Inspect `agent_warming`,
`agent_status`, and `agent_healthy` on the status response. A cold process still
has a one-time startup cost; prewarming moves that cost before the first message.
Warm processes and durable conversations make later turns connect immediately.

## Chat and streaming

`POST /api/projects/{uuid}/agent/chat` returns a request ID immediately by
default. Subscribe to:

`GET /api/projects/{uuid}/agent/activity/stream?live=1&since_id=N`

The stream emits persisted activity events:

- request lifecycle: `request_started`, `request_completed`, `request_failed`
- assistant output: `token_delta`, `message_snapshot`, `assistant_message`
- agent state: `processing`, `thinking`
- agent actions: file, command, tool, and service events

OpenHands native WebSocket events are mapped to these stable events so the GUI
and Sycord clients do not depend on an Agent Server wire format.

Every turn is correlated by `event.payload.request_id`. Save the highest
`event.id` and reconnect with `since_id=<id>` to replay anything missed.
Successful turns end in exactly one `request_completed`; failed or interrupted
turns end in exactly one `request_failed`.

### Recommended real-time client flow

1. Open the project chat and call `POST /agent/warm`. This is asynchronous;
   wait for the status response to report `agent_healthy=true` before showing a
   cold-start error.
2. Connect to the activity stream before sending the message. Use
   `live=1&since_id=<last_seen_id>` so a reconnect cannot miss the terminal
   event.
3. Call `POST /agent/chat` and store its `request_id`. The response means the
   request was accepted, not that the model has finished.
4. Render `token_delta` events as they arrive, but treat
   `message_snapshot`/`assistant_message` as the durable final text.
5. Stop the spinner only on `request_completed` or `request_failed` for the
   matching `request_id`.

The stream is a replayable event log, not a one-shot response body. Persist the
largest event `id` received (for example in browser storage), close the
connection on network errors, and reconnect with that cursor. Do not create a
second chat request just because the SSE connection was interrupted.

Syte retries transient OpenHands message-send responses (`500`, `502`, `503`,
and `504`) with bounded backoff. A final `request_failed` event means the
bounded retry was exhausted or the error was non-transient; its payload
contains `message`, `error`, and `retry_message` so a client can offer a safe
manual retry without losing the original prompt. Provider authentication,
invalid messages, and missing API keys are not retried.

Minimal browser pattern:

```js
let lastEventId = Number(localStorage.getItem('syte-agent-last-id') || 0);
let stream;

function connectActivity(projectId) {
  stream?.close();
  stream = new EventSource(
    `/api/projects/${projectId}/agent/activity/stream?live=1&since_id=${lastEventId}`
  );
  stream.onmessage = ({ data }) => {
    const frame = JSON.parse(data);
    if (frame.type !== 'activity') return;
    const event = frame.event;
    lastEventId = Math.max(lastEventId, event.id || 0);
    localStorage.setItem('syte-agent-last-id', String(lastEventId));
    renderAgentEvent(event);
  };
  stream.onerror = () => {
    stream.close();
    setTimeout(() => connectActivity(projectId), 1000);
  };
}
```

For server-side consumers, `format=jsonl` is easier to parse than SSE and
supports the same `live`, `since_id`, and `types` parameters. Always correlate
events by `event.payload.request_id`, because multiple requests can be replayed
through one long-lived stream.

### Stream encodings

The default is JSON SSE:

```text
data: {"type":"activity","event":{"id":21,"event_type":"thinking","detail":"Inspect first","payload":{"request_id":"req_abc"}}}
```

`format=tagged` is an opt-in SSE protocol for clients that want direct markers:

```text
data: [start]<{"id":20,"request_id":"req_abc","type":"request_started","text":"Fix navigation"}>

data: [think]<{"id":21,"request_id":"req_abc","type":"thinking","text":"Inspect the current navigation"}>

data: [tool:start]<{"id":22,"request_id":"req_abc","type":"file_read","text":"src/Nav.tsx","phase":"started","tool_call_id":"call_1"}>

data: [tool:result]<{"id":23,"request_id":"req_abc","type":"tool_call_finished","text":"Read complete","phase":"finished","tool_call_id":"call_1"}>

data: [delta]<{"id":24,"request_id":"req_abc","type":"token_delta","text":"Updated"}>

data: [done]<{"id":25,"request_id":"req_abc","type":"request_completed","text":"Updated navigation"}>
```

Tagged vocabulary:

- `[start]` — request accepted
- `[processing]` — OpenHands run started
- `[think]` — plan or reasoning update
- `[tool:start]` / `[tool:result]` — tool call and matching observation
- `[delta]` / `[message]` — streamed token and complete assistant message
- `[done]` / `[error]` — terminal success or failure
- `[status]`, `[session]`, `[ping]` — runtime and connection state

The content between `<` and `>` is compact JSON; text newlines are JSON-escaped.
`format=tagged` remains `text/event-stream`. In contrast, `format=text` is raw
`text/plain` for curl/fetch, and `format=jsonl` is NDJSON.

Useful query parameters:

- `live=1` — replay, then remain connected
- `since_id=N` — replay only events after N
- `types=thinking,command_run` — optional event-type filter for tagged/text/JSONL
- `api_key=syte_...` — browser EventSource authentication when required

## Controls

- `POST /api/projects/{uuid}/agent/warm`
- `POST /api/projects/{uuid}/agent/start`
- `POST /api/projects/{uuid}/agent/interrupt`
- `POST /api/projects/{uuid}/agent/stop`
- `POST /api/projects/{uuid}/agent/restart`

Interrupting a turn preserves the conversation and emits the normal terminal
request event once OpenHands has stopped processing.

## Native API

The authenticated internal proxy supports OpenHands health and conversation
routes only: `/ready`, `/health`, `/alive`, and `/api/conversations/*`.
Application clients should prefer Syte chat and activity endpoints; they handle
provider selection, durable event history, and reconnect cursors.
