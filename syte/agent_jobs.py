"""Per-workspace agent job queue — async requests with immediate request_id."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from syte.agent_activity import record_agent_event

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
    """Queue an agent job and return immediately with request_id."""
    request_id = new_request_id()
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
        },
        source=source,
    )

    task = asyncio.create_task(
        _run_job(
            project_id,
            request_id,
            message,
            model_profile=model_profile,
            source=source,
            auto_start=auto_start,
        )
    )
    prev = _running.get(project_id)
    if prev and not prev.done():
        # Cancelling only the Syte task would leave the OpenHands run active.
        # Interrupt the native conversation first so the next request gets a
        # clean turn and the user sees a coherent activity stream.
        try:
            from syte.openhands_agent import interrupt_agent

            await interrupt_agent(project_id)
        except Exception:
            # The previous task still gets cancelled below; a failed interrupt
            # is surfaced by that task's normal runtime error handling.
            pass
        prev.cancel()
    _running[project_id] = task

    return {
        "ok": True,
        "request_id": request_id,
        "status": "accepted",
        "project_id": project_id,
        "stream_url": f"/api/projects/{project_id}/agent/activity/stream?live=1",
    }


async def _run_job(
    project_id: str,
    request_id: str,
    message: str,
    *,
    model_profile: str | None,
    source: str,
    auto_start: bool,
) -> dict[str, Any]:
    from syte.openhands_agent import _communicate_with_agent_impl

    async with project_agent_lock(project_id):
        try:
            return await _communicate_with_agent_impl(
                project_id,
                message,
                model_profile=model_profile,
                source=source,
                auto_start=auto_start,
                emit_request_started=False,
                request_id=request_id,
            )
        except asyncio.CancelledError:
            await record_agent_event(
                project_id,
                "request_failed",
                title="Cancelled",
                detail="Superseded by a newer request",
                payload={"request_id": request_id, "error": "cancelled"},
                source=source,
            )
            raise
        except Exception as exc:
            error = str(exc) or "Agent request failed"
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
                },
                source=source,
            )
            return {
                "ok": False,
                "request_id": request_id,
                "error": "agent_job_failed",
                "message": error,
            }
