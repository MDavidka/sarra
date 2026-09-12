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

import asyncio
import json
import time
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


def get_normalized_models_catalog(settings_data: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Build a unified, deduplicated list of available model profiles for Syte and external clients."""
    curated = [
        {"id": "gpt-4o", "name": "GPT-4o (Omni)", "profile": "gpt-4o", "provider": "openai", "enabled": True, "active": True},
        {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "profile": "gpt-4o-mini", "provider": "openai", "enabled": True, "active": True},
        {"id": "o3-mini", "name": "o3-mini (Reasoning)", "profile": "o3-mini", "provider": "openai", "enabled": True, "active": True},
        {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "profile": "claude-3-5-sonnet-20241022", "provider": "anthropic", "enabled": True, "active": True},
        {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "profile": "claude-3-5-haiku-20241022", "provider": "anthropic", "enabled": True, "active": True},
        {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "profile": "gemini-2.0-flash", "provider": "google", "enabled": True, "active": True},
        {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "profile": "gemini-1.5-pro", "provider": "google", "enabled": True, "active": True},
        {"id": "deepseek-chat", "name": "DeepSeek V3", "profile": "deepseek-chat", "provider": "deepseek", "enabled": True, "active": True},
        {"id": "deepseek-reasoner", "name": "DeepSeek R1 (Reasoner)", "profile": "deepseek-reasoner", "provider": "deepseek", "enabled": True, "active": True},
        {"id": "qwen2.5-coder", "name": "Qwen 2.5 Coder", "profile": "qwen2.5-coder", "provider": "qwen", "enabled": True, "active": True},
        {"id": "qwen3.8-flash", "name": "Qwen 3.8 Flash", "profile": "qwen3.8-flash", "provider": "custom", "enabled": True, "active": True},
        {"id": "syra-base", "name": "Syra Base (Autonomous)", "profile": "syra-base", "provider": "syra", "enabled": True, "active": True},
        {"id": "syra-nano", "name": "Syra Nano (Fast)", "profile": "syra-nano", "provider": "syra", "enabled": True, "active": True},
        {"id": "syra-havy", "name": "Syra Heavy (Deep)", "profile": "syra-havy", "provider": "syra", "enabled": True, "active": True},
        {"id": "syra-ultra", "name": "Syra Ultra (Full-Stack)", "profile": "syra-ultra", "provider": "syra", "enabled": True, "active": True},
    ]

    custom_str = settings_data.get("custom_models") or ""
    if custom_str:
        for m in custom_str.split(","):
            m = m.strip()
            if m and not any(c["id"] == m or c["profile"] == m for c in curated):
                curated.append({
                    "id": m,
                    "name": m,
                    "profile": m,
                    "provider": settings_data.get("provider", "custom"),
                    "enabled": True,
                    "active": True,
                })

    for sp in settings_data.get("saved_providers") or []:
        if isinstance(sp, dict):
            p_name = sp.get("name") or sp.get("provider") or "Custom"
            models_sub = sp.get("models_list") or ([sp.get("model")] if sp.get("model") else [])
            for m in models_sub:
                if m and not any(c["id"] == m or c["profile"] == m for c in curated):
                    curated.append({
                        "id": m,
                        "name": f"{m} ({p_name})",
                        "profile": m,
                        "provider": sp.get("provider", "custom"),
                        "enabled": sp.get("active", True),
                        "active": sp.get("active", True),
                    })

    return curated


@router.get("/health")
async def stream_health() -> Dict[str, Any]:
    return {"ok": True, "stream": "alive"}


@router.get("/models")
async def stream_models(
    request: Request,
    stream: bool = Query(False, description="Stream models as Server-Sent Events"),
):
    """List available AI models or stream them over Better-SSE."""
    from syte.database import get_ai_builder_settings
    settings_data = await get_ai_builder_settings("global")
    models_list = get_normalized_models_catalog(settings_data)

    accept = request.headers.get("accept", "")
    wants_stream = stream or ("text/event-stream" in accept)

    if wants_stream:
        async def _stream_models_gen():
            yield f"retry: {RETRY_MS}\n\n".encode("ascii")
            for m in models_list:
                payload = json.dumps({"model": m}, separators=(",", ":"))
                yield f"event: model_stream\ndata: {payload}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"

        return _sse_response(_stream_models_gen())

    return {
        "ok": True,
        "available_models": models_list,
        "models": models_list,
        "ai_tab_models": models_list,
        "saved_providers": settings_data.get("saved_providers", []),
        "current_model": settings_data.get("model", "gpt-4o"),
        "current_provider": settings_data.get("provider", "openai"),
    }


@router.post("/projects/{project_id}/chat")
async def stream_chat(
    project_id: str,
    body: Dict[str, Any],
    request: Request,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Start (or attach to) an autonomous agent turn and stream events over Better-SSE."""
    await _require_project(project_id)
    message = str(body.get("message") or "").strip()
    if not message:
        raise HTTPException(400, "message is required")

    overrides = {k: v for k, v in body.items() if k != "message" and v is not None}
    session = session_manager.get_or_create_session(project_id)
    since_id = session.last_event_id

    # Start the agent turn
    await session_manager.start_turn(
        project_id=project_id,
        user_message=message,
        settings_override=overrides if overrides else None,
    )

    async def frames():
        async for frame in session.subscribe(since_id=since_id, request=request):
            yield frame

    return _sse_response(frames())


@router.get("/projects/{project_id}/events")
async def stream_events(
    project_id: str,
    request: Request,
    since_id: int = Query(0, ge=-1, description="Replay events with id > since_id; -1 replays full window"),
    replay: bool = Query(False, description="Shorthand for since_id=-1"),
    last_event_id: Optional[str] = Header(None, alias="Last-Event-ID"),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Subscribe to the live agent event stream for a project session."""
    await _require_project(project_id)
    if last_event_id and last_event_id.isdigit() and since_id <= 0:
        since_id = int(last_event_id)
    session = session_manager.get_or_create_session(project_id)

    async def frames():
        async for frame in session.subscribe(since_id=since_id, replay=replay, request=request):
            yield frame

    return _sse_response(frames())


@router.post("/projects/{project_id}/command")
async def stream_project_command(
    project_id: str,
    body: Dict[str, Any],
    request: Request,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Execute a shell command inside the project workspace and stream stdout/stderr live over SSE."""
    project = await _require_project(project_id)
    command = str(body.get("command") or "").strip()
    if not command:
        raise HTTPException(400, "command parameter is required")

    cwd_rel = str(body.get("cwd") or "app").strip().lstrip("/\\")
    from syte.workspace import ensure_workspace
    ws_dir = ensure_workspace(project_id) / cwd_rel
    if not ws_dir.exists():
        ws_dir = ensure_workspace(project_id)

    start_time = time.time()

    async def command_generator():
        yield f"retry: {RETRY_MS}\n\n".encode("ascii")
        
        start_payload = json.dumps({
            "event": "command_start",
            "command": command,
            "cwd": str(ws_dir),
            "project_id": project_id,
            "timestamp": time.time(),
        }, separators=(",", ":"))
        yield f"event: command_start\ndata: {start_payload}\n\n".encode("utf-8")

        proc = await asyncio.create_subprocess_shell(
            command,
            cwd=str(ws_dir),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        if proc.stdout:
            while True:
                if await request.is_disconnected():
                    proc.kill()
                    return
                line = await proc.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                out_payload = json.dumps({
                    "event": "command_output",
                    "type": "stdout",
                    "stream": "stdout",
                    "text": decoded,
                    "line": decoded.rstrip("\r\n"),
                }, separators=(",", ":"))
                yield f"event: command_output\ndata: {out_payload}\n\n".encode("utf-8")

        if proc.stderr:
            while True:
                if await request.is_disconnected():
                    proc.kill()
                    return
                line = await proc.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace")
                out_payload = json.dumps({
                    "event": "command_output",
                    "type": "stderr",
                    "stream": "stderr",
                    "text": decoded,
                    "line": decoded.rstrip("\r\n"),
                }, separators=(",", ":"))
                yield f"event: command_output\ndata: {out_payload}\n\n".encode("utf-8")

        await proc.wait()
        duration_ms = round((time.time() - start_time) * 1000, 1)

        end_payload = json.dumps({
            "event": "command_end",
            "command": command,
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
            "timestamp": time.time(),
        }, separators=(",", ":"))
        yield f"event: command_end\ndata: {end_payload}\n\n".encode("utf-8")
        yield b"event: done\ndata: [DONE]\n\n"

    return _sse_response(command_generator())


@router.get("/projects/{project_id}/status")
async def stream_status(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Detailed live snapshot of the AI session status, active model, and progress."""
    await _require_project(project_id)
    session = session_manager.get_or_create_session(project_id)
    from syte.database import get_ai_builder_settings
    settings_data = await get_ai_builder_settings(project_id)
    
    summary = session.get_status_summary()
    summary["current_model"] = settings_data.get("model", "gpt-4o")
    summary["current_provider"] = settings_data.get("provider", "openai")
    return {"ok": True, "session": summary}


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
    """Answer a pending interactive agent question."""
    await _require_project(project_id)
    return await session_manager.handle_user_answer(project_id, body)


@router.post("/projects/{project_id}/stop")
@router.post("/projects/{project_id}/interrupt")
async def stream_stop(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Interrupt the running turn and keep runtime session warm."""
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

