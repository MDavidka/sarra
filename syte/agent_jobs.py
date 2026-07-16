"""Durable per-workspace cloud-agent queue with immediate request IDs."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from syte.agent_activity import record_agent_event
from syte.cloud_agent_store import (
    begin_turn_session,
    current_session_number,
    current_turso_session_id,
    enqueue_request,
    mark_request,
    pending_requests,
    set_turso_session_id,
)
from syte.turso_store import close_open_sessions_for_project
from syte.turso_store import close_session as close_turso_session
from syte.turso_store import open_session as open_turso_session

_project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_running: dict[str, asyncio.Task[Any]] = {}


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def project_agent_lock(project_id: str) -> asyncio.Lock:
    """Return the shared lock that serializes turns for one conversation."""
    return _project_locks[project_id]


async def submit_agent_request(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    source: str = "api",
    auto_start: bool = True,
) -> dict[str, Any]:
    """Admit a durable agent request and return immediately."""
    request_id = new_request_id()
    await enqueue_request(
        request_id,
        project_id,
        message,
        model_profile=model_profile,
        source=source,
        auto_start=auto_start,
    )
    # Capture the previous durable session *before* opening a new one so an
    # interrupt/cancel closes the superseded turn, not the admitted one.
    previous_turso_session_id = await current_turso_session_id(project_id)
    previous = _running.get(project_id)

    # Session opens when the user message is admitted so a durable Turso
    # session (see syte.turso_store) exists from the very first event, before
    # the worker starts tools.
    session_number = await begin_turn_session(project_id, model_profile)
    turso_session_id = await open_turso_session(
        project_id, session_number=session_number, model_profile=model_profile,
    )
    if turso_session_id:
        await set_turso_session_id(project_id, turso_session_id)
    await record_agent_event(
        project_id,
        "request_started",
        role="user",
        title="Request",
        detail=message[:4000],
        payload={
            "message": message,
            "model_profile": model_profile,
            "request_id": request_id,
            "session": session_number,
            "message_index": 1,
            "mark": f"S{session_number}001(d)",
            "mark_status": "d",
            "mark_kind": "user",
            "session_started": True,
        },
        source=source,
        turso_session_id=turso_session_id,
    )

    if previous and not previous.done():
        try:
            from syte.cloud_agent import interrupt_agent

            await interrupt_agent(
                project_id, turso_session_id=previous_turso_session_id,
            )
        except Exception:
            pass
        previous.cancel()

    task = asyncio.create_task(
        _run_job(
            project_id,
            request_id,
            message,
            model_profile=model_profile,
            source=source,
            auto_start=auto_start,
            session_number=session_number,
            message_index_start=1,
            turso_session_id=turso_session_id,
        )
    )
    _running[project_id] = task
    return {
        "ok": True,
        "request_id": request_id,
        "session": session_number,
        "turso_session_id": turso_session_id,
        "status": "accepted",
        "project_id": project_id,
        "session_url": f"/api/agent_session/{turso_session_id}" if turso_session_id else None,
    }


async def _run_job(
    project_id: str,
    request_id: str,
    message: str,
    *,
    model_profile: str | None,
    source: str,
    auto_start: bool,
    session_number: int | None = None,
    message_index_start: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    from syte.cloud_agent import _communicate_with_agent_impl

    terminal_status: str | None = None
    async with project_agent_lock(project_id):
        try:
            await mark_request(request_id, "running")
            result = await _communicate_with_agent_impl(
                project_id,
                message,
                model_profile=model_profile,
                source=source,
                auto_start=auto_start,
                emit_request_started=False,
                request_id=request_id,
                session_number=session_number,
                message_index_start=message_index_start,
                turso_session_id=turso_session_id,
            )
            await mark_request(
                request_id,
                "completed" if result.get("ok") else "failed",
                error="" if result.get("ok") else str(result.get("message") or ""),
            )
            terminal_status = "completed" if result.get("ok") else "failed"
            return result
        except asyncio.CancelledError:
            await mark_request(request_id, "cancelled", error="Superseded by a newer request")
            await record_agent_event(
                project_id,
                "request_failed",
                title="Cancelled",
                detail="Superseded by a newer request",
                payload={
                    "request_id": request_id,
                    "error": "cancelled",
                    "session": session_number,
                    "mark_status": "d",
                    "mark_kind": "error",
                },
                source=source,
                turso_session_id=turso_session_id,
            )
            terminal_status = "cancelled"
            raise
        except Exception as exc:
            error = str(exc) or "Agent request failed"
            await mark_request(request_id, "failed", error=error)
            await record_agent_event(
                project_id,
                "request_failed",
                title="Request failed",
                detail=error[:4000],
                payload={
                    "request_id": request_id,
                    "error": "agent_job_failed",
                    "message": error,
                    "retry_message": message[:4000],
                    "session": session_number,
                    "mark_status": "d",
                    "mark_kind": "error",
                },
                source=source,
                turso_session_id=turso_session_id,
            )
            terminal_status = "failed"
            return {"ok": False, "request_id": request_id, "error": "agent_job_failed", "message": error}
        finally:
            # Always stamp a terminal Turso status + ended_at so pollers never
            # stay stuck on status=open / "generating".
            if turso_session_id and terminal_status:
                await close_turso_session(turso_session_id, status=terminal_status)


async def resume_pending_requests() -> int:
    """Resume requests admitted before a VM/service restart."""
    resumed = 0
    for row in await pending_requests():
        project_id = str(row["project_id"])
        session_number = await current_session_number(project_id)
        if session_number <= 0:
            session_number = await begin_turn_session(
                project_id, row.get("model_profile"),
            )
        # Orphaned open sessions from the crashed turn must not stay
        # "generating" forever — close them before opening a fresh one.
        await close_open_sessions_for_project(project_id, status="cancelled")
        turso_session_id = await open_turso_session(
            project_id, session_number=session_number, model_profile=row.get("model_profile"),
        )
        if turso_session_id:
            await set_turso_session_id(project_id, turso_session_id)
        task = asyncio.create_task(
            _run_job(
                project_id,
                str(row["request_id"]),
                str(row["message"]),
                model_profile=row.get("model_profile"),
                source=str(row.get("source") or "recovery"),
                auto_start=bool(row.get("auto_start", 1)),
                session_number=session_number,
                message_index_start=1,
                turso_session_id=turso_session_id,
            )
        )
        _running[project_id] = task
        resumed += 1
    return resumed
