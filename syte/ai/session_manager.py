"""Persistent Background AI Agent Session Manager for Syte.

Maintains running agent tasks on the host VM across browser tab switches, page refreshes,
and disconnections. Handles multi-client event broadcasting, interactive user input gates,
and state synchronization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
from typing import Any, AsyncGenerator, Dict, List, Optional

from syte.database import get_project, update_project

logger = logging.getLogger("syte.ai.session_manager")


# Per-subscriber queue capacity. Bounded so a slow client cannot balloon
# memory. When the queue fills, the oldest queued event is dropped and a
# ``stream_gap`` control frame is queued once so the client can backfill.
_SUBSCRIBER_QUEUE_MAX = 256


class ProjectAISession:
    """Manages the lifecycle and live event streams of an autonomous agent for a project."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.is_running = False
        self.current_turn = 0
        self.active_task: Optional[asyncio.Task] = None
        self.event_buffer: List[Dict[str, Any]] = []
        # Track the oldest event-id still queued per subscriber so we can
        # populate `stream_gap.last_id` after an overflow drop.
        self.subscribers: List[asyncio.Queue] = []
        self.subscriber_head_id: Dict[int, int] = {}
        self._next_event_id = 1
        self.active_plan: Optional[Dict[str, Any]] = None
        self.pending_question: Optional[Dict[str, Any]] = None
        self.answer_queue: asyncio.Queue = asyncio.Queue()
        self.last_activity = time.time()
        self.lock = asyncio.Lock()

    def _allocate_event_id(self) -> int:
        eid = self._next_event_id
        self._next_event_id += 1
        return eid

    def add_event(self, event: Dict[str, Any]) -> None:
        """Record event in buffer and broadcast to all active SSE listener queues."""
        self.last_activity = time.time()
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()

        # Update active plan in memory if plan events occur
        evt_type = event.get("event")
        if evt_type == "tool_call_result":
            tool_name = event.get("tool_name")
            result = event.get("result") or {}
            if tool_name == "syte_create_plan" and result.get("plan"):
                self.active_plan = result["plan"]
            elif tool_name == "syte_update_plan_step" and self.active_plan:
                step_id = str(result.get("step_id") or "")
                status = str(result.get("status") or "")
                notes = result.get("notes")
                plan_steps = self.active_plan.get("steps") if isinstance(self.active_plan, dict) else None
                for s in (plan_steps or []):
                    if isinstance(s, dict) and str(s.get("id")) == step_id:
                        s["status"] = status
                        if notes:
                            s["notes"] = notes

        # Assign a monotonically-increasing id so clients can dedupe on
        # reconnect with `since_id`. Stored alongside the event in the buffer.
        eid = self._allocate_event_id()
        event_with_id = {**event, "id": eid}

        # Keep last 300 events in memory ring buffer (id stays attached)
        self.event_buffer.append(event_with_id)
        if len(self.event_buffer) > 300:
            self.event_buffer.pop(0)

        # Broadcast to active subscriber queues with bounded backpressure.
        # Slow clients have their oldest queued event dropped and receive a
        # one-shot ``stream_gap`` so they can backfill via the polling mirror.
        # We coalesce: a single gap covers a burst of drops so the queue is
        # never dominated by gap frames under sustained overflow.
        for q in list(self.subscribers):
            dropped = 0
            while True:
                try:
                    q.put_nowait(event_with_id)
                    break
                except asyncio.QueueFull:
                    try:
                        q.get_nowait()
                        dropped += 1
                    except Exception:
                        break
            if dropped:
                gap = {
                    "event": "stream_gap",
                    "dropped": dropped,
                    "last_id": eid,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                # Always replace the oldest queued event with the gap so the
                # signal survives sustained overflow (queue is bounded).
                try:
                    q.get_nowait()
                    q.put_nowait(gap)
                except Exception:
                    pass

    async def wait_for_user_answer(self, question_data: Dict[str, Any], timeout: float = 300.0) -> Dict[str, Any]:
        """Pause agent turn until the user provides an answer or secret from the UI."""
        self.pending_question = question_data

        # Clear any stale answers
        while not self.answer_queue.empty():
            try:
                self.answer_queue.get_nowait()
            except Exception:
                break

        self.add_event({
            "event": "user_input_required",
            "question_data": question_data,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        try:
            answer = await asyncio.wait_for(self.answer_queue.get(), timeout=timeout)
            return answer
        except asyncio.TimeoutError:
            return {"timeout": True, "answer": "No response received within timeout. Proceeding with best defaults."}
        finally:
            self.pending_question = None

    def resolve_user_answer(self, answer_payload: Dict[str, Any]) -> bool:
        """Resolve a pending question with the user's answer."""
        self.pending_question = None
        try:
            self.answer_queue.put_nowait(answer_payload)
            return True
        except Exception:
            return False

    def clear(self) -> None:
        """Reset the session buffers, plan, and pending questions."""
        self.event_buffer.clear()
        self.active_plan = None
        self.pending_question = None
        while not self.answer_queue.empty():
            try:
                self.answer_queue.get_nowait()
            except Exception:
                break

    def get_status_summary(self) -> Dict[str, Any]:
        """Return high-level summary of active session."""
        return {
            "project_id": self.project_id,
            "is_running": self.is_running,
            "current_turn": self.current_turn,
            "active_plan": self.active_plan,
            "pending_question": self.pending_question,
            "events_in_buffer": len(self.event_buffer),
            "subscribers_count": len(self.subscribers),
            "last_activity": self.last_activity,
        }


class AIAgentSessionManager:
    """Singleton managing background agent runs across all projects on Syte."""

    _instance: Optional[AIAgentSessionManager] = None

    def __init__(self):
        self.sessions: Dict[str, ProjectAISession] = {}

    @classmethod
    def get_instance(cls) -> AIAgentSessionManager:
        if cls._instance is None:
            cls._instance = AIAgentSessionManager()
        return cls._instance

    def get_or_create_session(self, project_id: str) -> ProjectAISession:
        if project_id not in self.sessions:
            self.sessions[project_id] = ProjectAISession(project_id)
        return self.sessions[project_id]

    def clear_session(self, project_id: str) -> None:
        """Reset active session state and event buffer for project."""
        if project_id in self.sessions:
            sess = self.sessions[project_id]
            sess.clear()

    async def stop_session(self, project_id: str) -> Dict[str, Any]:
        """Cancel active background agent task and mark session stopped."""
        session = self.get_or_create_session(project_id)
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
            session.is_running = False
            session.add_event({
                "event": "stopped",
                "message": "AI execution stopped by user.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return {"ok": True, "stopped": True, "message": "Agent execution stopped."}
        return {"ok": True, "stopped": False, "message": "No active agent task running."}

    async def start_turn(
        self,
        project_id: str,
        user_message: str,
        settings_override: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Spawn or run the autonomous agent turn in a background task."""
        from syte.ai.engine import AIAgentEngine

        session = self.get_or_create_session(project_id)
        async with session.lock:
            if session.is_running and session.active_task and not session.active_task.done():
                logger.info(f"Agent already running for project '{project_id}'. Attaching message to queue.")
                return

            session.is_running = True
            session.current_turn += 1
            # Filter out old transient token deltas from buffer to prevent replay bloat
            session.event_buffer = [
                e for e in session.event_buffer
                if e.get("event") not in ("token_delta", "thought_delta", "status")
            ]

            async def _run_background_loop():
                engine = AIAgentEngine(project_id, session=session)
                try:
                    async for event in engine.run_agent_turn(
                        user_message=user_message,
                        settings_override=settings_override,
                    ):
                        session.add_event(event)
                except asyncio.CancelledError:
                    session.add_event({"event": "cancelled", "message": "Agent task was cancelled by user."})
                except Exception as exc:
                    logger.exception(f"Error in background AI turn for '{project_id}': {exc}")
                    session.add_event({"event": "error", "error": str(exc)})
                finally:
                    session.is_running = False
                    session.add_event({"event": "session_idle", "timestamp": datetime.now(timezone.utc).isoformat()})

            session.active_task = asyncio.create_task(_run_background_loop())

    async def subscribe(
        self,
        project_id: str,
        replay: bool = False,
        since_id: Optional[int] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Subscribe to live streaming events with keepalive heartbeat.

        ``since_id``: when set with ``replay=True``, only replay events with
        ``id > since_id``. Keeps the backlog window small on reconnect so
        delta-oriented clients don't re-download the full recent history.

        The per-subscriber queue is bounded (see ``_SUBSCRIBER_QUEUE_MAX``);
        overflow drops the oldest queued event and emits a ``stream_gap``
        control frame so the client can backfill via the polling mirror.
        """
        session = self.get_or_create_session(project_id)
        q: asyncio.Queue = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_MAX)
        session.subscribers.append(q)

        try:
            # 1. Optionally replay events with id > since_id from buffer
            if replay:
                for past_event in list(session.event_buffer):
                    eid = past_event.get("id") or 0
                    if since_id is None or eid > since_id:
                        yield past_event

            # 2. Stream live events as they occur with 10s keepalive heartbeats
            while True:
                try:
                    event = await asyncio.wait_for(q.get(), timeout=10.0)
                    yield event
                    if event.get("event") == "done" and not session.is_running:
                        await asyncio.sleep(0.05)
                        break
                except asyncio.TimeoutError:
                    # Emit periodic keepalive event so reverse proxies/browsers never timeout
                    yield {
                        "event": "ping",
                        "is_running": session.is_running,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
        finally:
            if q in session.subscribers:
                session.subscribers.remove(q)

    async def handle_user_answer(self, project_id: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Handle submission of general questions or secure environment variables."""
        session = self.get_or_create_session(project_id)
        is_secret = bool(payload.get("is_secret") or payload.get("is_secret_request"))
        key = str(payload.get("key") or "").strip()
        secret_value = str(payload.get("secret_value") or payload.get("value") or "").strip()
        answer = payload.get("answer")

        if is_secret and key:
            # 1. Secure Server-Side Storage: Save secret directly to project env / .env
            if project_id != "global":
                project = await get_project(project_id)
                if project:
                    current_env = dict(project.get("env_vars") or {})
                    current_env[key] = secret_value
                    await update_project(project_id, {"env_vars": current_env})

            # 2. Pass zero-knowledge masked token back to the AI loop
            masked_token = {
                "status": "stored",
                "key": key,
                "saved_to_env": True,
                "message": f"Environment variable '{key}' has been securely saved to the project .env on the server. The raw secret is masked from model context for security. In your code, reference it via process.env.{key} or os.environ['{key}'].",
            }
            resolved = session.resolve_user_answer(masked_token)
            return {"ok": True, "saved_to_env": True, "key": key, "resumed_agent": resolved}

        # General question response
        resolved = session.resolve_user_answer({"answer": answer})
        return {"ok": True, "resumed_agent": resolved}


# Global accessor
session_manager = AIAgentSessionManager.get_instance()
