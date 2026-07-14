"""Syte internal API for sycord.com -> Syte runtime calls."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field


from syte.auth import verify_internal_service_request
from syte.cloud_agent import (
    communicate_with_agent,
    get_agent_logs,
    get_agent_status,
    interrupt_agent,
    restart_agent,
    start_agent,
    stop_agent,
    test_agent,
    warm_agent,
)
from syte.database import get_project

router = APIRouter(tags=["Syte Internal API"])


class InternalAgentChangeRequest(BaseModel):
    message: str = Field(..., description="User change request from sycord.com")
    model_profile: str | None = Field(None, description="syra-nano | syra-base | syra-havy")
    model_name: str | None = Field(None, description="Alias used by sycord.com")


class InternalAgentCommunicateRequest(BaseModel):
    message: str
    model_profile: str | None = None
    model_name: str | None = None


async def _require_project(project_id: str) -> dict:
    project = await get_project(project_id)
    if not project:
        raise HTTPException(404, "Project not found")
    return project


@router.get("/projects/{project_id}/agent")
async def internal_agent_status(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/start")
async def internal_agent_start(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message, _meta = await start_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/warm")
async def internal_agent_warm(
    project_id: str,
    _auth: dict = Depends(verify_internal_service_request),
):
    """Schedule a persistent runtime and return before it becomes ready."""
    await _require_project(project_id)
    result = await warm_agent(project_id, source="internal")
    return {
        **result,
        "status_url": f"/api/internal/projects/{project_id}/agent",
        "stream_url": (
            f"/api/internal/projects/{project_id}/agent/activity/stream?live=1"
        ),
    }


@router.post("/projects/{project_id}/agent/stop")
async def internal_agent_stop(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message = await stop_agent(project_id)
    return {
        "ok": ok,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/interrupt")
async def internal_agent_interrupt(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message = await interrupt_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/restart")
async def internal_agent_restart(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    ok, message, _meta = await restart_agent(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.get("/projects/{project_id}/agent/logs")
async def internal_agent_logs(
    project_id: str,
    lines: int = 200,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "logs": get_agent_logs(project_id, max(1, min(lines, 2000))),
    }


@router.get("/projects/{project_id}/agent/activity")
async def internal_agent_activity(
    project_id: str,
    since_id: int = 0,
    limit: int = 200,
    session: str = "",
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_activity import list_agent_events

    await _require_project(project_id)
    events = await list_agent_events(
        project_id,
        since_id=since_id,
        limit=limit,
        session=session or None,
    )
    return {
        "ok": True,
        "project_id": project_id,
        "events": events,
        "since_id": since_id,
        "session": session or None,
        "stream_url": f"/api/internal/projects/{project_id}/agent/activity/stream?live=1",
        "tagged_stream_url": (
            f"/api/internal/projects/{project_id}/agent/activity/stream"
            "?live=1&format=tagged"
        ),
        "marked_stream_url": (
            f"/api/internal/projects/{project_id}/agent/activity/stream"
            "?live=1&format=marked"
        ),
    }


@router.get("/projects/{project_id}/agent/activity/stream")
async def internal_agent_activity_stream(
    project_id: str,
    request: Request,
    live: bool = False,
    since_id: int = 0,
    format: Literal[
        "sse",
        "tagged",
        "tagged_sse",
        "tags",
        "marked",
        "marks",
        "text",
        "plain",
        "jsonl",
    ] = "sse",
    types: str = "",
    session: str = "",
    last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    _auth: dict = Depends(verify_internal_service_request),
):
    """Server-to-server mirror of the public agent activity stream.

    Identical replay/live semantics and wire formats as
    ``/api/projects/{uuid}/agent/activity/stream`` but authenticated with the
    ``X-Syra-Internal-Secret`` header for the sycord.com backend. Supports
    ``since_id`` and the ``Last-Event-ID`` header for gapless reconnection, and
    ``session=last`` to replay only the newest ``[sessionN]`` block; see
    ``syte.log_stream`` for the full frame protocol.
    """
    from syte.log_stream import (
        stream_agent_activity,
        stream_agent_activity_formatted,
        stream_agent_activity_marked,
        stream_agent_activity_tagged,
    )

    await _require_project(project_id)
    if not since_id and last_event_id:
        try:
            since_id = max(0, int(last_event_id))
        except (TypeError, ValueError):
            since_id = 0
    fmt = (format or "sse").strip().lower()
    type_filter = [t.strip() for t in types.split(",") if t.strip()] or None
    session_scope: str | None = session.strip() or None
    if fmt in ("tagged", "tagged_sse", "tags"):
        generator = stream_agent_activity_tagged(
            project_id,
            live_only=live,
            since_id=since_id,
            type_filter=type_filter,
            session=session_scope,
        )
        media = "text/event-stream"
        stream_format = "tagged-v1"
    elif fmt in ("marked", "marks"):
        generator = stream_agent_activity_marked(
            project_id,
            live_only=live,
            since_id=since_id,
            type_filter=type_filter,
            session=session_scope,
        )
        media = "text/event-stream"
        stream_format = "marked-v1"
    elif fmt in ("text", "jsonl", "plain"):
        generator = stream_agent_activity_formatted(
            project_id,
            live_only=live,
            since_id=since_id,
            output_format="jsonl" if fmt == "jsonl" else "text",
            type_filter=type_filter,
            session=session_scope,
        )
        media = "application/x-ndjson" if fmt == "jsonl" else "text/plain; charset=utf-8"
        stream_format = fmt
    else:
        generator = stream_agent_activity(
            project_id, live_only=live, since_id=since_id, session=session_scope
        )
        media = "text/event-stream"
        stream_format = fmt
    return StreamingResponse(
        generator,
        media_type=media,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Syte-Stream-Format": stream_format,
        },
    )


@router.get("/agent/dashboard")
async def internal_agent_dashboard(_auth: dict = Depends(verify_internal_service_request)):
    from syte.agent_metrics import get_dashboard_metrics

    return {"ok": True, **(await get_dashboard_metrics())}


@router.post("/projects/{project_id}/agent/test")
async def internal_agent_test(
    project_id: str,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    result = await test_agent(project_id, source="internal")
    if not result.get("ok"):
        raise HTTPException(400, detail={"error": result.get("error"), "message": result.get("message"), **result})
    return result


@router.post("/projects/{project_id}/agent/communicate")
async def internal_agent_communicate(
    project_id: str,
    body: InternalAgentCommunicateRequest,
    _auth: dict = Depends(verify_internal_service_request),
):
    await _require_project(project_id)
    profile = body.model_profile or body.model_name
    result = await communicate_with_agent(
        project_id,
        body.message,
        model_profile=profile,
        source="internal",
    )
    if not result.get("ok"):
        raise HTTPException(400, detail=result)
    return result


@router.post("/projects/{project_id}/agent/change")
async def internal_agent_change(
    project_id: str,
    body: InternalAgentChangeRequest,
    _auth: dict = Depends(verify_internal_service_request),
):
    """sycord.com → Syte: request a Syte cloud workspace change by UUID."""
    await _require_project(project_id)
    profile = body.model_profile or body.model_name
    result = await communicate_with_agent(
        project_id,
        body.message,
        model_profile=profile,
        source="sycord",
        background=True,
    )
    if not result.get("ok"):
        raise HTTPException(400, detail=result)
    return {
        **result,
        "project_id": project_id,
        "change_applied": None,
        "status": result.get("status", "accepted"),
    }


@router.api_route(
    "/projects/{project_id}/agent/proxy",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
@router.api_route(
    "/projects/{project_id}/agent/proxy/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
)
async def internal_agent_proxy(
    project_id: str,
    request: Request,
    path: str = "",
    _auth: dict = Depends(verify_internal_service_request),
):
    """Compatibility health endpoint for the embedded cloud runtime."""
    del request
    status = await get_agent_status(project_id, check_backend=False)
    if not status:
        raise HTTPException(404, "Project not found")
    normalized = path.strip("/")
    if normalized in {"", "ready", "health", "alive"}:
        return {
            "ok": status.get("agent_healthy", False),
            "runtime": status.get("agent_runtime"),
            "status": status.get("agent_status"),
            "embedded": True,
        }
    raise HTTPException(
        410,
        "The per-project agent server API was removed. Use Syte agent communicate, "
        "change, activity, and lifecycle routes.",
    )
