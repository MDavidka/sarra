"""Syte internal API for sycord.com -> Syte runtime calls."""

from __future__ import annotations

from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import httpx

from syte.auth import verify_internal_service_request
from syte.openhands_agent import (
    agent_local_url,
    agent_session_headers,
    communicate_with_agent,
    get_agent_logs,
    get_agent_status,
    interrupt_agent,
    restart_agent,
    start_agent,
    stop_agent,
    test_agent,
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
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_activity import list_agent_events

    await _require_project(project_id)
    events = await list_agent_events(project_id, since_id=since_id, limit=limit)
    return {
        "ok": True,
        "project_id": project_id,
        "events": events,
        "since_id": since_id,
        "stream_url": f"/api/internal/projects/{project_id}/agent/activity/stream?live=1",
    }


@router.get("/projects/{project_id}/agent/activity/stream")
async def internal_agent_activity_stream(
    project_id: str,
    request: Request,
    live: bool = False,
    since_id: int = 0,
    format: str = "sse",
    types: str = "",
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.log_stream import stream_agent_activity, stream_agent_activity_formatted

    await _require_project(project_id)
    fmt = (format or "sse").strip().lower()
    type_filter = [t.strip() for t in types.split(",") if t.strip()] or None
    if fmt in ("text", "jsonl", "plain"):
        generator = stream_agent_activity_formatted(
            project_id,
            live_only=live,
            since_id=since_id,
            output_format="jsonl" if fmt == "jsonl" else "text",
            type_filter=type_filter,
        )
        media = "application/x-ndjson" if fmt == "jsonl" else "text/plain; charset=utf-8"
    else:
        generator = stream_agent_activity(project_id, live_only=live, since_id=since_id)
        media = "text/event-stream"
    return StreamingResponse(
        generator,
        media_type=media,
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
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
    """sycord.com → Syte: request an OpenHands workspace change by UUID."""
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
    status = await get_agent_status(project_id)
    if not status:
        raise HTTPException(404, "Project not found")
    if not status.get("agent_port"):
        raise HTTPException(503, "OpenHands agent has no allocated port")
    if not status.get("agent_running"):
        raise HTTPException(503, "OpenHands agent is not running")

    normalized_path = path.strip("/")
    allowed_paths = ("ready", "health", "alive", "api/conversations")
    if not any(
        normalized_path == allowed or normalized_path.startswith(f"{allowed}/")
        for allowed in allowed_paths
    ):
        raise HTTPException(
            404,
            "Only OpenHands health and conversation endpoints may be proxied. "
            "Use Syte agent routes for chat and activity streaming.",
        )

    upstream = agent_local_url(status["agent_port"]).rstrip("/")
    target = upstream + ("/" + path.lstrip("/") if path else "")
    query_items = [(k, v) for k, v in request.query_params.multi_items() if k != "internal_secret"]
    if query_items:
        target += "?" + urlencode(query_items)

    headers = {
        key: value
        for key, value in request.headers.items()
        if key.lower()
        not in {"host", "content-length", "x-syra-internal-secret", "x-session-api-key"}
    }
    headers.update(agent_session_headers(project_id))
    body = await request.body()
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        upstream_response = await client.request(
            request.method,
            target,
            headers=headers,
            content=body,
        )
    response_headers = {
        key: value
        for key, value in upstream_response.headers.items()
        if key.lower() not in {"content-encoding", "transfer-encoding", "connection"}
    }
    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=response_headers,
    )
