"""Syte internal API for sycord.com -> Syte runtime calls."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
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
    thinking_level: int | None = Field(None, ge=1, le=5, description="1 Instant … 5 Max")
    idempotency_key: str | None = Field(
        None, description="Optional client key — retries return the same request_id"
    )


class InternalAgentCommunicateRequest(BaseModel):
    message: str
    model_profile: str | None = None
    model_name: str | None = None
    thinking_level: int | None = Field(None, ge=1, le=5, description="1 Instant … 5 Max")


class InternalQuestionAnswerRequest(BaseModel):
    answer: str | int | float | list[str] | dict


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
        "sessions_url": f"/api/internal/projects/{project_id}/agent/sessions",
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
    from syte.agent_jobs import cancel_agent_job

    ok, message = await cancel_agent_job(project_id)
    if not ok:
        raise HTTPException(400, message)
    return {
        "ok": True,
        "message": message,
        **(await get_agent_status(project_id, request_base=str(request.base_url).rstrip("/"))),
    }


@router.post("/projects/{project_id}/agent/cancel")
async def internal_agent_cancel(
    project_id: str,
    request: Request,
    _auth: dict = Depends(verify_internal_service_request),
):
    """Explicit cancel alias — stops the active agent turn/job without a new message."""
    await _require_project(project_id)
    from syte.agent_jobs import cancel_agent_job

    ok, message = await cancel_agent_job(project_id)
    return {
        "ok": ok,
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
    """Local SQLite activity snapshot. For the durable, UUID-addressable record
    of a turn use the Turso session routes instead (``agent/sessions`` /
    ``agent_session/{session_id}``)."""
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
        "sessions_url": f"/api/internal/projects/{project_id}/agent/sessions",
    }


@router.get("/projects/{project_id}/agent/screenshots")
async def internal_agent_screenshots(
    project_id: str,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_artifacts import list_screenshots

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, "screenshots": await list_screenshots(project_id, limit=limit)}


@router.get("/projects/{project_id}/agent/plans")
async def internal_agent_plans(
    project_id: str,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_artifacts import list_plans

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, "plans": await list_plans(project_id, limit=limit)}


@router.get("/projects/{project_id}/agent/questions")
async def internal_agent_questions(
    project_id: str,
    status: str | None = None,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_artifacts import list_questions

    await _require_project(project_id)
    return {
        "ok": True,
        "project_id": project_id,
        "questions": await list_questions(project_id, status=status, limit=limit),
    }


@router.post("/projects/{project_id}/agent/questions/{question_id}/answer")
async def internal_agent_answer_question(
    project_id: str,
    question_id: str,
    body: InternalQuestionAnswerRequest,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_activity import record_agent_event
    from syte.agent_artifacts import answer_question
    from syte.cloud_agent_store import current_turso_session_id

    await _require_project(project_id)
    result = await answer_question(project_id, question_id, body.answer)
    if not result.get("ok"):
        raise HTTPException(404 if result.get("error") == "not_found" else 400, result.get("message") or "Failed")
    if not result.get("already_answered"):
        turso_session_id = await current_turso_session_id(project_id)
        await record_agent_event(
            project_id,
            "question_answered",
            role="user",
            title="Answer",
            detail=str(result.get("answer") or "")[:4000],
            payload={"question_id": question_id, "answer": result.get("answer")},
            source="internal",
            turso_session_id=turso_session_id,
        )
    return result


@router.get("/projects/{project_id}/agent/stops")
async def internal_agent_stops(
    project_id: str,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_artifacts import list_session_stops

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, "stops": await list_session_stops(project_id, limit=limit)}


@router.get("/projects/{project_id}/agent/mcp")
async def internal_agent_mcp(
    project_id: str,
    _auth: dict = Depends(verify_internal_service_request),
):
    from syte.agent_artifacts import list_mcp_addons

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, "addons": await list_mcp_addons(project_id)}


@router.get("/projects/{project_id}/agent/turso_sync")
async def internal_agent_turso_sync(
    project_id: str,
    _auth: dict = Depends(verify_internal_service_request),
):
    """Aggregate 'all messages saved to Turso' status for the brain indicator."""
    from syte.cloud_agent import turso_message_sync_status

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, **(await turso_message_sync_status(project_id))}


@router.get("/projects/{project_id}/agent/turso_debug")
async def internal_agent_turso_debug(
    project_id: str,
    _auth: dict = Depends(verify_internal_service_request),
):
    """Diagnose why the brain indicator is red — live Turso connectivity + schema check."""
    from syte.turso_store import turso_debug_status

    await _require_project(project_id)
    return {"ok": True, "project_id": project_id, **(await turso_debug_status())}


@router.get("/projects/{project_id}/agent/sessions")
async def internal_agent_sessions(
    project_id: str,
    limit: int = 50,
    _auth: dict = Depends(verify_internal_service_request),
):
    """List durable Turso agent-session UUIDs for a project (newest first)."""
    from syte.turso_store import list_sessions_for_project, turso_configured

    await _require_project(project_id)
    if not await turso_configured():
        return {
            "ok": True,
            "project_id": project_id,
            "turso_configured": False,
            "sessions": [],
            "message": "Turso is not configured — set turso_database_url in Settings -> AI tab.",
        }
    sessions = await list_sessions_for_project(project_id, limit=limit)
    return {
        "ok": True,
        "project_id": project_id,
        "turso_configured": True,
        "sessions": [
            {**s, "session_url": f"/api/internal/agent_session/{s['id']}"} for s in sessions
        ],
    }


@router.get("/agent_session/{session_id}")
async def internal_get_agent_session(
    session_id: str,
    since_id: int = 0,
    _auth: dict = Depends(verify_internal_service_request),
):
    """Server-to-server Turso access route — fetch a durable agent session by UUID.

    Replaces the old ``/agent/activity/stream`` SSE mirror. sycord.com now
    fetches the session document produced while the agent worked instead of
    holding open a streaming connection.
    """
    from syte.turso_store import get_session, turso_configured

    if not await turso_configured():
        raise HTTPException(
            503,
            "Turso is not configured — set turso_database_url (and turso_auth_token) "
            "in Settings -> AI tab before fetching agent sessions.",
        )
    session = await get_session(session_id, since_id=since_id)
    if not session:
        raise HTTPException(404, "Agent session not found")
    return {"ok": True, **session}


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
        thinking_level=body.thinking_level,
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
        thinking_level=body.thinking_level,
        source="sycord",
        background=True,
        idempotency_key=body.idempotency_key,
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
