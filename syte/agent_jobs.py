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
from syte.turso_store import close_session as close_turso_session
from syte.turso_store import open_session as open_turso_session

_project_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_running: dict[str, asyncio.Task[Any]] = {}


def new_request_id() -> str:
    return f"req_{uuid.uuid4().hex[:12]}"


def project_agent_lock(project_id: str) -> asyncio.Lock:
    """Return the shared lock that serializes turns for one conversation."""
    return _project_locks[project_id]


def _normalize_idempotency_key(key: str | None) -> str | None:
    raw = (key or "").strip()
    if not raw:
        return None
    # Keep filesystem/DB-safe; clients may send UUIDs or opaque tokens.
    cleaned = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")
    if not cleaned or len(cleaned) > 128:
        return None
    return f"idem_{cleaned}"


def is_agent_job_running(project_id: str) -> bool:
    """Return True when a durable agent job task is still in flight."""
    task = _running.get(project_id)
    return bool(task and not task.done())


async def cancel_agent_job(project_id: str) -> tuple[bool, str]:
    """Cancel the in-flight agent job for a project (if any)."""
    from syte.cloud_agent import interrupt_agent

    ok, message = await interrupt_agent(project_id)
    task = _running.pop(project_id, None)
    if task and not task.done():
        task.cancel()
        return True, "Agent job cancellation requested."
    return ok, message


async def submit_agent_request(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api",
    auto_start: bool = True,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Admit a durable agent request and return immediately.

    When ``idempotency_key`` is provided, a repeated submit with the same key
    returns the existing request instead of queuing a duplicate job.
    """
    from syte.cloud_agent_store import get_request

    request_id = _normalize_idempotency_key(idempotency_key) or new_request_id()
    existing = await get_request(request_id)
    if existing:
        return await _idempotent_replay_payload(
            existing, project_id=project_id, thinking_level=thinking_level,
        )

    try:
        await enqueue_request(
            request_id,
            project_id,
            message,
            model_profile=model_profile,
            source=source,
            auto_start=auto_start,
        )
    except Exception:
        # Race: another request with the same idempotency key just inserted.
        existing = await get_request(request_id)
        if existing:
            return await _idempotent_replay_payload(
                existing, project_id=project_id, thinking_level=thinking_level,
            )
        raise

    # Session opens when the user message is admitted so a durable Turso
    # session (see syte.turso_store) exists from the very first event, before
    # the worker starts tools. Local SQLite fallback guarantees a session id
    # even when remote Turso is unset (required by sycord-pages).
    session_number = await begin_turn_session(project_id, model_profile)
    turso_session_id = await open_turso_session(
        project_id, session_number=session_number, model_profile=model_profile,
    )
    if turso_session_id:
        await set_turso_session_id(project_id, turso_session_id)
        await _store_request_turso_session(request_id, turso_session_id)
    await record_agent_event(
        project_id,
        "request_started",
        role="user",
        title="Request",
        detail=message[:4000],
        payload={
            "message": message,
            "model_profile": model_profile,
            "thinking_level": thinking_level,
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

    previous = _running.get(project_id)
    if previous and not previous.done():
        try:
            from syte.cloud_agent import interrupt_agent

            await interrupt_agent(project_id)
        except Exception:
            pass
        previous.cancel()

    task = asyncio.create_task(
        _run_job(
            project_id,
            request_id,
            message,
            model_profile=model_profile,
            thinking_level=thinking_level,
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
        "thinking_level": thinking_level,
        "session_url": f"/api/agent_session/{turso_session_id}" if turso_session_id else None,
    }


async def _idempotent_replay_payload(
    existing: dict[str, Any],
    *,
    project_id: str,
    thinking_level: int | str | None,
) -> dict[str, Any]:
    """Rebuild the accept payload for a repeated idempotency key.

    Must include ``turso_session_id`` — sycord-pages rejects accepts without it.
    """
    request_id = str(existing.get("request_id") or "")
    turso_session_id = (
        (existing.get("turso_session_id") or "").strip()
        or await current_turso_session_id(project_id)
    )
    session_number = await current_session_number(project_id)
    return {
        "ok": True,
        "request_id": request_id,
        "status": existing.get("status") or "accepted",
        "project_id": project_id,
        "session": session_number or None,
        "turso_session_id": turso_session_id or None,
        "idempotent_replay": True,
        "thinking_level": thinking_level,
        "session_url": (
            f"/api/agent_session/{turso_session_id}" if turso_session_id else None
        ),
    }


async def _store_request_turso_session(request_id: str, turso_session_id: str) -> None:
    """Persist the session id on the request row for idempotent replays."""
    from syte.cloud_agent_store import set_request_turso_session_id

    try:
        await set_request_turso_session_id(request_id, turso_session_id)
    except Exception:
        # Non-fatal — current_turso_session_id still covers most replays.
        pass


async def _run_job(
    project_id: str,
    request_id: str,
    message: str,
    *,
    model_profile: str | None,
    source: str,
    auto_start: bool,
    thinking_level: int | str | None = None,
    session_number: int | None = None,
    message_index_start: int = 0,
    turso_session_id: str | None = None,
) -> dict[str, Any]:
    from syte.cloud_agent import _communicate_with_agent_impl

    async with project_agent_lock(project_id):
        try:
            await mark_request(request_id, "running")
            result = await _communicate_with_agent_impl(
                project_id,
                message,
                model_profile=model_profile,
                thinking_level=thinking_level,
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
            await close_turso_session(
                turso_session_id, status="completed" if result.get("ok") else "failed"
            )
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
            await close_turso_session(turso_session_id, status="cancelled")
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
            await close_turso_session(turso_session_id, status="failed")
            return {"ok": False, "request_id": request_id, "error": "agent_job_failed", "message": error}


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
        turso_session_id = await current_turso_session_id(project_id)
        if not turso_session_id:
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
