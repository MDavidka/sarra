"""Durable per-workspace cloud-agent queue with immediate request IDs."""

from __future__ import annotations

import asyncio
import json
import uuid
from collections import defaultdict
from typing import Any

from syte.agent_activity import record_agent_event
from syte.cloud_agent_store import (
    begin_turn_session,
    current_session_number,
    current_turso_session_id,
    ensure_latest_session,
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
_TRANSIENT_RETRY_ATTEMPTS = 2
_TRANSIENT_RETRY_DELAY_SECONDS = 0.75


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


_CANCEL_DRAIN_TIMEOUT_SECONDS = 2.0
# Projects whose current turn was stopped by the user rather than superseded by
# a newer message. Read by _run_job's cancellation handler so the transcript
# says "Stopped" instead of "Superseded by a newer request".
_user_cancelled: set[str] = set()


async def cancel_agent_job(project_id: str) -> tuple[bool, str]:
    """Cancel the in-flight agent job for a project (if any).

    Always reports success: once this returns, no turn is running for the
    project, which is the only guarantee a Stop action needs. ``interrupt_agent``
    already returned True on every branch, so the previous code could leave the
    durable job task running while telling the caller the turn was stopped.
    """
    from syte.cloud_agent import interrupt_agent

    _ok, message = await interrupt_agent(project_id)
    task = _running.pop(project_id, None)
    if task and not task.done():
        _user_cancelled.add(project_id)
        task.cancel()
        # Wait briefly for _run_job's cancellation handler so the terminal
        # event, request status and Turso session close *before* the caller
        # reads agent status — otherwise it still reports "processing".
        #
        # asyncio.wait (not wait_for) is deliberate: wait_for cancels what it is
        # waiting on when it times out, which would re-cancel a task already
        # part-way through writing its terminal event and lose that event.
        try:
            await asyncio.wait({task}, timeout=_CANCEL_DRAIN_TIMEOUT_SECONDS)
        except Exception:
            pass
        finally:
            _user_cancelled.discard(project_id)
        return True, "Agent job cancellation requested."
    return True, message or "No active cloud-agent turn."


async def submit_agent_request(
    project_id: str,
    message: str,
    *,
    model_profile: str | None = None,
    thinking_level: int | str | None = None,
    source: str = "api",
    auto_start: bool = True,
    idempotency_key: str | None = None,
    override_api_key: str | None = None,
    override_credentials: list[dict[str, Any]] | None = None,
    generation_options: dict[str, Any] | None = None,
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
            generation_options=generation_options,
        )
    except Exception:
        existing = await get_request(request_id)
        if existing:
            return await _idempotent_replay_payload(
                existing, project_id=project_id, thinking_level=thinking_level,
            )
        raise

    previous_turso_session_id = await current_turso_session_id(project_id)
    previous = _running.get(project_id)

    session_number = await ensure_latest_session(project_id, model_profile)
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
            thinking_level=thinking_level,
            source=source,
            auto_start=auto_start,
            session_number=session_number,
            message_index_start=1,
            turso_session_id=turso_session_id,
            override_api_key=override_api_key,
            override_credentials=override_credentials,
            generation_options=generation_options,
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
        "model_profile": model_profile,
        "thinking_level": thinking_level,
        "generation_options": generation_options or {},
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
        "model_profile": existing.get("model_profile"),
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


async def _run_agent_attempt(
    project_id: str,
    message: str,
    *,
    model_profile: str | None,
    source: str,
    auto_start: bool,
    thinking_level: int | str | None,
    request_id: str,
    session_number: int | None,
    message_index_start: int,
    turso_session_id: str | None,
    override_api_key: str | None,
    override_credentials: list[dict[str, Any]] | None = None,
    generation_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run the provider call and retry one transient external-API failure."""
    from syte.cloud_agent import _communicate_with_agent_impl, _failure_metadata

    for attempt in range(1, _TRANSIENT_RETRY_ATTEMPTS + 1):
        try:
            return await _communicate_with_agent_impl(
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
                override_api_key=override_api_key,
                override_credentials=override_credentials,
                generation_options=generation_options,
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _failure_metadata(exc)
            if not failure.get("retryable") or attempt >= _TRANSIENT_RETRY_ATTEMPTS:
                raise
            await record_agent_event(
                project_id,
                "status",
                title="Retrying provider request",
                detail="The external model request failed temporarily. Retrying once.",
                payload={
                    "request_id": request_id,
                    "model_profile": model_profile,
                    "attempt": attempt + 1,
                    "max_attempts": _TRANSIENT_RETRY_ATTEMPTS,
                    "status": "retrying",
                    "error_type": failure.get("error_type"),
                },
                source=source,
                turso_session_id=turso_session_id,
            )
            await asyncio.sleep(_TRANSIENT_RETRY_DELAY_SECONDS)

    raise RuntimeError("Agent request retry loop exited unexpectedly")


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
    override_api_key: str | None = None,
    override_credentials: list[dict[str, Any]] | None = None,
    generation_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    terminal_status: str | None = None
    async with project_agent_lock(project_id):
        try:
            await mark_request(request_id, "running")
            result = await _run_agent_attempt(
                project_id,
                message,
                model_profile=model_profile,
                thinking_level=thinking_level,
                source=source,
                auto_start=auto_start,
                request_id=request_id,
                session_number=session_number,
                message_index_start=message_index_start,
                turso_session_id=turso_session_id,
                override_api_key=override_api_key,
                override_credentials=override_credentials,
                generation_options=generation_options,
            )
            await mark_request(
                request_id,
                "completed" if result.get("ok") else "failed",
                error="" if result.get("ok") else str(result.get("message") or ""),
            )
            terminal_status = "completed" if result.get("ok") else "failed"
            return result
        except asyncio.CancelledError:
            stopped_by_user = project_id in _user_cancelled
            reason = (
                "Stopped by the user"
                if stopped_by_user else "Superseded by a newer request"
            )
            await mark_request(request_id, "cancelled", error=reason)
            await record_agent_event(
                project_id,
                "agent_stopped" if stopped_by_user else "request_failed",
                title="Stopped" if stopped_by_user else "Cancelled",
                detail=reason,
                payload={
                    "request_id": request_id,
                    "error": "cancelled",
                    "session": session_number,
                    "model_profile": model_profile,
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
            from syte.cloud_agent import _failure_metadata

            failure = _failure_metadata(exc)
            friendly = failure["message"] or error
            await mark_request(request_id, "failed", error=friendly)
            await record_agent_event(
                project_id,
                "request_failed",
                title=failure["title"],
                detail=friendly[:4000],
                payload={
                    "request_id": request_id,
                    "error": failure["error_type"],
                    "error_type": failure["error_type"],
                    "retryable": failure["retryable"],
                    "message": friendly,
                    "retry_message": message[:4000],
                    "session": session_number,
                    "model_profile": model_profile,
                    "mark_status": "d",
                    "mark_kind": "error",
                    **failure["detail"],
                },
                source=source,
                turso_session_id=turso_session_id,
            )
            terminal_status = "failed"
            return {
                "ok": False,
                "request_id": request_id,
                "error": failure["error_type"],
                "message": friendly,
                "error_type": failure["error_type"],
                "retryable": failure["retryable"],
                "model_profile": model_profile,
                **failure["detail"],
            }
        finally:
            # Always stamp a terminal status + ended_at so pollers never stay
            # stuck on status=open / "generating".
            if turso_session_id and terminal_status:
                await close_turso_session(turso_session_id, status=terminal_status)


def _parse_generation_options(value: Any) -> dict[str, Any]:
    """Load persisted turn controls without letting a malformed row block recovery."""
    if isinstance(value, dict):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return dict(parsed) if isinstance(parsed, dict) else {}


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
                generation_options=_parse_generation_options(row.get("generation_options")),
                session_number=session_number,
                message_index_start=1,
                turso_session_id=turso_session_id,
            )
        )
        _running[project_id] = task
        resumed += 1
    return resumed
