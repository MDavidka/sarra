"""Durable per-workspace cloud-agent queue with immediate request IDs."""

from __future__ import annotations

import asyncio
import uuid
from collections import defaultdict
from typing import Any

from syte.agent_activity import record_agent_event
from syte.cloud_agent_store import enqueue_request, mark_request, pending_requests

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
    await record_agent_event(
        project_id,
        "request_started",
        role="user",
        title="Request",
        detail=message[:4000],
        payload={"message": message, "model_profile": model_profile, "request_id": request_id},
        source=source,
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
            source=source,
            auto_start=auto_start,
        )
    )
    _running[project_id] = task
    return {
        "ok": True,
        "request_id": request_id,
        "status": "accepted",
        "project_id": project_id,
        "stream_url": f"/api/projects/{project_id}/agent/activity/stream?live=1",
        "tagged_stream_url": f"/api/projects/{project_id}/agent/activity/stream?live=1&format=tagged",
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
    from syte.cloud_agent import _communicate_with_agent_impl

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
            )
            await mark_request(
                request_id,
                "completed" if result.get("ok") else "failed",
                error="" if result.get("ok") else str(result.get("message") or ""),
            )
            return result
        except asyncio.CancelledError:
            await mark_request(request_id, "cancelled", error="Superseded by a newer request")
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
                },
                source=source,
            )
            return {"ok": False, "request_id": request_id, "error": "agent_job_failed", "message": error}


async def resume_pending_requests() -> int:
    """Resume requests admitted before a VM/service restart."""
    resumed = 0
    for row in await pending_requests():
        project_id = str(row["project_id"])
        task = asyncio.create_task(
            _run_job(
                project_id,
                str(row["request_id"]),
                str(row["message"]),
                model_profile=row.get("model_profile"),
                source=str(row.get("source") or "recovery"),
                auto_start=bool(row.get("auto_start", 1)),
            )
        )
        _running[project_id] = task
        resumed += 1
    return resumed
