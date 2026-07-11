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

## Chat and streaming

`POST /api/projects/{uuid}/agent/chat` returns a request ID immediately by
default. Subscribe to:

`GET /api/projects/{uuid}/agent/activity/stream?live=1&since_id=N`

The stream emits persisted activity events:

- request lifecycle: `request_started`, `request_completed`, `request_failed`
- assistant output: `token_delta`, `message_snapshot`, `assistant_message`
- agent actions: file, command, tool, thinking, and service events

OpenHands native WebSocket events are mapped to these stable events so the GUI
and Sycord clients do not depend on an Agent Server wire format.

## Controls

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
