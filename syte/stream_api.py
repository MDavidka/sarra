"""Syte Streaming API — the dedicated SSE window for AI chat and activity.

Every real-time surface lives under ``/api/stream`` so latency-critical
traffic is easy to identify, proxy, and tune:

* ``GET  /api/stream``                                   — machine-readable capability catalog
* ``GET  /api/stream/health``                            — liveness probe (never rate-limited)
* ``POST /api/stream/projects/{id}/chat``                — start an agent turn + open SSE
* ``GET  /api/stream/projects/{id}/events``              — subscribe / reconnect to agent SSE
* ``GET  /api/stream/projects/{id}/activity``            — JSON poll mirror (``since_id``)
* ``GET  /api/stream/projects/{id}/status``              — session snapshot (busy / plan)
* ``POST /api/stream/projects/{id}/answer``              — answer a pending question gate
* ``POST /api/stream/projects/{id}/stop``                — interrupt the running turn
* ``GET  /api/stream/projects/{id}/logs/stream``         — deploy/build log SSE
* ``GET  /api/stream/projects/{id}/preview/logs/stream`` — preview dev-server SSE

All SSE responses share the optimized plumbing in ``syte/sse_core.py``:
pre-serialized frames, monotonic event ids (``id:`` lines + ``Last-Event-ID``
reconnect), bounded per-client queues with ``stream_gap`` backpressure
signaling, hot-delta batching, and comment+data heartbeats.

See ``docs/ai-chat-streaming.md`` for the full wire contract, including how
each AI chat tool is streamed.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from syte import auth
from syte.ai.session_manager import session_manager
from syte.auth import verify_operator_session_or_token
from syte.database import get_project
from syte.log_stream import stream_preview_logs, stream_project_logs
from syte.sse_core import (
    HEARTBEAT_SECONDS,
    HOT_FLUSH_CHARS,
    HOT_FLUSH_COUNT,
    HOT_FLUSH_SECONDS,
    RETRY_MS,
    SSE_HEADERS,
    SUBSCRIBER_QUEUE_SIZE,
    Channel,
    Session,
    channel_hub,
)

router = APIRouter(prefix="/api/stream", tags=["Syte Stream API"])

EVENT_BUFFER_SIZE = 300


async def _require_project(project_id: str) -> Dict[str, Any]:
    if project_id == "global":
        return {"id": "global", "deploy_type": "shell"}
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


def _sse_response(generator) -> StreamingResponse:
    return StreamingResponse(
        generator,
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.get("")
@router.get("/")
async def stream_api_catalog() -> Dict[str, Any]:
    """Discovery document describing the streaming surface and its tuning."""
    return {
        "ok": True,
        "api": "syte-stream",
        "library": "better-sse",
        "transport": "text/event-stream (SSE)",
        "endpoints": {
            "subtab_stream": "GET /api/stream/projects/{project_id}/subtabs/{subtab}?since_id=0&replay=false",
            "subtab_broadcast": "POST /api/stream/projects/{project_id}/subtabs/{subtab}/broadcast",
            "channel_stream": "GET /api/stream/channels/{channel_name}?since_id=0&replay=false",
            "channel_broadcast": "POST /api/stream/channels/{channel_name}/broadcast",
            "channels_list": "GET /api/stream/channels",
            "chat": "POST /api/stream/projects/{project_id}/chat",
            "events": "GET /api/stream/projects/{project_id}/events?since_id=0&replay=false",
            "activity_poll": "GET /api/stream/projects/{project_id}/activity?since_id=0&limit=200",
            "status": "GET /api/stream/projects/{project_id}/status",
            "answer": "POST /api/stream/projects/{project_id}/answer",
            "stop": "POST /api/stream/projects/{project_id}/stop",
            "logs": "GET /api/stream/projects/{project_id}/logs/stream?live=false",
            "preview_logs": "GET /api/stream/projects/{project_id}/preview/logs/stream?live=false",
        },
        "better_sse": {
            "supported_subtabs": [
                "general", "build", "sources", "domains", "env",
                "release", "firewall", "redirects", "logs", "speed", "ai", "settings"
            ],
            "sessions": "Stateful Session objects with disconnect callbacks and keep-alive heartbeats",
            "channels": "Pub/Sub Channel broadcast groups with ring-buffer replay and backpressure queues",
        },
        "reconnect": {
            "retry_ms": RETRY_MS,
            "last_event_id": "Frames carry id: lines; EventSource resends Last-Event-ID automatically; fetch clients pass ?since_id=",
            "replay_window_events": EVENT_BUFFER_SIZE,
            "replay_window_policy": "cold events retained; token/thought deltas and status chatter excluded",
        },
        "hot_path": {
            "batched_events": ["token_delta", "thought_delta"],
            "flush_chars": HOT_FLUSH_CHARS,
            "flush_count": HOT_FLUSH_COUNT,
            "flush_idle_seconds": HOT_FLUSH_SECONDS,
        },
        "backpressure": {
            "subscriber_queue_frames": SUBSCRIBER_QUEUE_SIZE,
            "policy": "drop oldest queued frame, emit stream_gap {dropped, last_id, reason}",
        },
        "keepalive": {
            "heartbeat_seconds": HEARTBEAT_SECONDS,
            "frames": "comment ': heartbeat' + named 'ping' data frame",
        },
        "docs": "docs/ai-chat-streaming.md",
    }


@router.get("/health")
async def stream_health() -> Dict[str, Any]:
    return {"ok": True, "stream": "alive"}


@router.post("/projects/{project_id}/chat")
async def stream_chat(
    project_id: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Start (or attach to) an autonomous agent turn and stream events as SSE.

    Body: ``{"message": "...", "provider?", "model?", "temperature?",
    "thinking_level?"}``. If a turn is already running for the project the
    call attaches to the live stream instead of starting a second one.
    """
    await _require_project(project_id)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    overrides = {k: v for k, v in body.items() if k != "message" and v is not None}
    # Capture the cursor *before* starting the turn: the stream then contains
    # exactly this turn's events even if the turn finishes before the first
    # frame is read, and no stale events from previous turns are replayed.
    session = session_manager.get_or_create_session(project_id)
    since_id = session.last_event_id
    await session_manager.start_turn(project_id=project_id, user_message=message, settings_override=overrides)

    async def frames():
        async for frame in session.subscribe(since_id=since_id):
            yield frame

    return _sse_response(frames())


@router.get("/projects/{project_id}/events")
async def stream_events(
    project_id: str,
    since_id: int = Query(0, ge=-1, description="Replay events with id > since_id; -1 replays full window"),
    replay: bool = Query(False, description="Shorthand for since_id=-1"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Subscribe to the live agent event stream for a project session.

    Honors browser ``EventSource`` auto-reconnect via the ``Last-Event-ID``
    header; fetch-based clients can pass ``?since_id=`` instead.
    """
    await _require_project(project_id)
    if last_event_id and last_event_id.isdigit() and since_id <= 0:
        since_id = int(last_event_id)
    session = session_manager.get_or_create_session(project_id)

    async def frames():
        async for frame in session.subscribe(since_id=since_id, replay=replay):
            yield frame

    return _sse_response(frames())


@router.get("/projects/{project_id}/status")
async def stream_status(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    await _require_project(project_id)
    session = session_manager.get_or_create_session(project_id)
    return {"ok": True, "session": session.get_status_summary()}


@router.get("/projects/{project_id}/activity")
async def stream_activity_poll(
    project_id: str,
    since_id: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=EVENT_BUFFER_SIZE),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """JSON polling mirror of the event stream for clients without SSE."""
    await _require_project(project_id)
    session = session_manager.get_or_create_session(project_id)
    events = session.get_events_since(since_id, limit)
    return {
        "ok": True,
        "events": events,
        "last_id": session.last_event_id,
        "is_running": session.is_running,
    }


@router.post("/projects/{project_id}/answer")
async def stream_answer(
    project_id: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    await _require_project(project_id)
    return await session_manager.handle_user_answer(project_id, body)


@router.post("/projects/{project_id}/stop")
async def stream_stop(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    await _require_project(project_id)
    return await session_manager.stop_session(project_id)


async def _verify_log_stream_key(request: Request) -> None:
    """Mirror the legacy log-stream auth: verify the API key when one is sent."""
    key = request.headers.get("x-api-key") or request.query_params.get("api_key") or request.headers.get("authorization")
    if key:
        await auth.verify_api_token_from_request(request)


@router.get("/projects/{project_id}/logs/stream")
async def stream_logs(
    project_id: str,
    request: Request,
    live: bool = Query(False, description="Skip the historical snapshot, stream only new lines"),
):
    """Deployment / build / app log tail over SSE (same frames as legacy)."""
    project = await _require_project(project_id)
    await _verify_log_stream_key(request)
    return _sse_response(
        stream_project_logs(project_id, project.get("deploy_type", "shell"), live_only=live)
    )


@router.get("/projects/{project_id}/preview/logs/stream")
async def stream_preview(
    project_id: str,
    request: Request,
    live: bool = Query(False),
):
    """Preview dev-server log tail over SSE."""
    await _require_project(project_id)
    await _verify_log_stream_key(request)
    return _sse_response(stream_preview_logs(project_id, live_only=live))


# ---------------------------------------------------------------------------
# Better-SSE Subtab & Channel Streaming Endpoints
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/subtabs/{subtab}")
async def stream_project_subtab(
    project_id: str,
    subtab: str,
    request: Request,
    since_id: int = Query(0, ge=-1, description="Replay events with id > since_id; -1 replays full buffer"),
    replay: bool = Query(False, description="Shorthand for since_id=-1"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Subscribe to a dedicated Better-SSE stream for a specific project subtab.

    Supported subtabs include: `general`, `build`, `sources`, `domains`, `env`,
    `release`, `firewall`, `redirects`, `logs`, `speed`, `ai`, `rollbacks`, `settings`.
    """
    await _require_project(project_id)
    if last_event_id and last_event_id.isdigit() and since_id <= 0:
        since_id = int(last_event_id)

    # Resolve subtab channel
    channel = channel_hub.get_subtab_channel(project_id, subtab)
    session = channel_hub.create_session(
        last_event_id=max(since_id, 0),
        state={"project_id": project_id, "subtab": subtab},
    )
    channel.register(session, since_id=since_id, replay=replay)

    return _sse_response(session.iterate(since_id=since_id, replay=replay, request=request))


@router.post("/projects/{project_id}/subtabs/{subtab}/broadcast")
async def broadcast_project_subtab(
    project_id: str,
    subtab: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Broadcast an event into a project subtab channel via Better-SSE."""
    await _require_project(project_id)
    event_name = str(body.get("event") or body.get("event_type") or f"subtab:{subtab}")
    payload = {k: v for k, v in body.items() if k not in ("event", "event_type")}
    payload["event"] = event_name
    payload["event_type"] = event_name
    payload["subtab"] = subtab
    payload["project_id"] = project_id

    channel_hub.broadcast_to_subtab(project_id, subtab, payload, event=event_name)
    return {"ok": True, "broadcast": True, "subtab": subtab, "event": event_name}


@router.get("/channels/{channel_name}")
async def stream_generic_channel(
    channel_name: str,
    request: Request,
    since_id: int = Query(0, ge=-1),
    replay: bool = Query(False),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Subscribe to any named Better-SSE channel directly."""
    if last_event_id and last_event_id.isdigit() and since_id <= 0:
        since_id = int(last_event_id)

    channel = channel_hub.get_channel(channel_name)
    if not channel:
        raise HTTPException(404, "Channel not found")

    session = channel_hub.create_session(
        last_event_id=max(since_id, 0),
        state={"channel": channel_name},
    )
    channel.register(session, since_id=since_id, replay=replay)

    return _sse_response(session.iterate(since_id=since_id, replay=replay, request=request))


@router.post("/channels/{channel_name}/broadcast")
async def broadcast_generic_channel(
    channel_name: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Broadcast an event payload to all subscribers of a Better-SSE channel."""
    channel = channel_hub.get_channel(channel_name)
    if not channel:
        raise HTTPException(404, "Channel not found")

    event_name = str(body.get("event") or body.get("event_type") or "message")
    channel.broadcast(body, event=event_name)
    return {"ok": True, "broadcast": True, "channel": channel_name, "subscribers": channel.session_count}


@router.get("/channels")
async def list_stream_channels(
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """List active Better-SSE channels, subscribers, and stats."""
    return {"ok": True, "channels": channel_hub.list_channels()}

