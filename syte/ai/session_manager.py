"""Persistent Background AI Agent Session Manager for Syte.

Maintains running agent tasks on the host VM across browser tab switches, page
refreshes, and disconnections. Handles multi-client event broadcasting,
interactive user input gates, and state synchronization.

Streaming hot path (see ``syte/sse_core.py``):

* Events are JSON-encoded **once** per emit and fanned out as immutable
  ``bytes`` SSE frames to every subscriber — N clients no longer pay N
  serializations.
* ``token_delta`` / ``thought_delta`` are coalesced by a :class:`DeltaBatcher`
  (≤ 32 deltas / 500 chars / 15 ms idle window) so per-token frames never
  flood the socket, while the first burst still lands within one scheduler
  tick.
* Every event carries a monotonic per-session ``id`` so clients can reconnect
  with ``?since_id=`` and replay exactly the gap — no duplicate or lost
  frames inside the retained ring window.
* Subscriber queues are bounded; overflow drops the oldest queued frame and
  surfaces a ``stream_gap`` control frame instead of blocking the agent.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, AsyncIterator, Deque, Dict, List, Optional, Tuple

from syte.database import get_project, update_project
from syte.sse_core import (
    HEARTBEAT_FRAME,
    HEARTBEAT_SECONDS,
    HOT_DELTA_EVENTS,
    RETRY_FRAME,
    SUBSCRIBER_QUEUE_SIZE,
    DeltaBatcher,
    encode_sse_frame,
    stream_gap_frame,
    utc_now_iso,
)

logger = logging.getLogger("syte.ai.session_manager")

EVENT_BUFFER_SIZE = 300
TERMINAL_EVENTS = frozenset({"session_idle", "done", "cancelled", "error"})


class Subscriber:
    """Bounded fan-out queue for one live SSE connection."""

    __slots__ = ("queue", "dropped")

    def __init__(self) -> None:
        self.queue: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self.dropped = 0


class ProjectAISession:
    """Manages the lifecycle and live event streams of an autonomous agent for a project."""

    def __init__(self, project_id: str):
        self.project_id = project_id
        self.is_running = False
        self.current_turn = 0
        self.active_task: Optional[asyncio.Task] = None
        self.active_plan: Optional[Dict[str, Any]] = None
        self.pending_question: Optional[Dict[str, Any]] = None
        self.answer_queue: asyncio.Queue = asyncio.Queue()
        self.last_activity = time.time()
        self.lock = asyncio.Lock()

        # Monotonic per-session event id (starts at 1; 0 means "no events seen").
        self._next_seq = 0
        # Ring of (seq, sse_frame_bytes, event_dict) — the replay window.
        self._ring: Deque[Tuple[int, bytes, Dict[str, Any]]] = deque(maxlen=EVENT_BUFFER_SIZE)
        self._subs: List[Subscriber] = []
        self._batcher = DeltaBatcher(self._broadcast)

    # ------------------------------------------------------------------
    # Event ingest / fan-out
    # ------------------------------------------------------------------

    @property
    def event_buffer(self) -> List[Dict[str, Any]]:
        """Compatibility view of the retained replay window (for diagnostics)."""
        return [evt for _seq, _frame, evt in self._ring]

    @property
    def subscribers(self) -> List[Subscriber]:
        return self._subs

    @property
    def last_event_id(self) -> int:
        return self._next_seq

    def add_event(self, event: Dict[str, Any]) -> None:
        """Record + broadcast one agent event.

        Hot deltas are coalesced; cold events flush pending deltas first so
        frame order on the wire matches causal order (deltas → tool call →
        done). The event dict must not be mutated after this call — the same
        object is shared with every subscriber and the replay ring.
        """
        self.last_activity = time.time()
        if str(event.get("event") or "") in HOT_DELTA_EVENTS and isinstance(event.get("delta"), str):
            self._batcher.push(event)
            return
        self._batcher.flush_all()
        self._broadcast(event)

    def _broadcast(self, event: Dict[str, Any]) -> None:
        evt_type = str(event.get("event") or "message")

        if "timestamp" not in event:
            event["timestamp"] = utc_now_iso()

        # Mirror plan state in memory so reconnecting clients can rehydrate
        # the plan without replaying every tool result.
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

        self._next_seq += 1
        seq = self._next_seq
        event["id"] = seq
        frame = encode_sse_frame(event, event_id=seq)

        # Transient per-turn chatter is excluded from the replay window so a
        # reconnect never re-downloads a whole previous turn's token stream.
        if evt_type not in ("token_delta", "thought_delta", "status"):
            self._ring.append((seq, frame, event))

        for sub in list(self._subs):
            try:
                sub.queue.put_nowait((frame, event))
            except asyncio.QueueFull:
                try:
                    sub.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                sub.dropped += 1
                try:
                    sub.queue.put_nowait((frame, event))
                except asyncio.QueueFull:
                    pass
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Subscription (SSE)
    # ------------------------------------------------------------------

    async def subscribe(self, since_id: int = 0, replay: bool = False) -> AsyncIterator[bytes]:
        """Async generator of ready-to-send SSE ``bytes`` frames.

        ``since_id`` replays only events newer than the client's last seen id
        (browser ``Last-Event-ID`` semantics). ``replay=True`` with
        ``since_id=0`` replays the whole retained window.
        """
        if replay and since_id <= 0:
            since_id = -1  # replay everything in the ring

        sub = Subscriber()
        self._subs.append(sub)
        try:
            yield RETRY_FRAME

            ring = list(self._ring)
            if since_id > 0 and ring and since_id < ring[0][0] - 1:
                yield stream_gap_frame(
                    dropped=ring[0][0] - since_id - 1,
                    last_id=max(ring[0][0] - 1, 0),
                    reason="replay_window_expired",
                )
            for seq, frame, _evt in ring:
                if seq > since_id:
                    yield frame

            # If the session already ended (last cold event terminal) and no
            # turn is running, close now instead of holding a connection that
            # can never produce more frames — clients reconnect per turn.
            if ring and not self.is_running:
                last_cold = ring[-1][2]
                if str(last_cold.get("event") or "") in TERMINAL_EVENTS:
                    return

            while True:
                try:
                    frame, event = await asyncio.wait_for(sub.queue.get(), timeout=HEARTBEAT_SECONDS)
                except asyncio.TimeoutError:
                    yield HEARTBEAT_FRAME
                    continue

                if sub.dropped:
                    dropped, sub.dropped = sub.dropped, 0
                    yield stream_gap_frame(dropped=dropped, last_id=max(self._next_seq - 1, 0))

                yield frame

                evt_type = str(event.get("event") or "") if event else ""
                if evt_type in TERMINAL_EVENTS and not self.is_running:
                    # Give the turn loop a beat to enqueue any trailing cold
                    # events, then close so the client's EventSource can
                    # reconnect (or stop) on a clean end-of-stream.
                    await asyncio.sleep(0.05)
                    while not sub.queue.empty():
                        frame, _event = sub.queue.get_nowait()
                        yield frame
                    break
        finally:
            if sub in self._subs:
                self._subs.remove(sub)

    # ------------------------------------------------------------------
    # Interactive question gate
    # ------------------------------------------------------------------

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
        self._batcher.clear()
        self._ring.clear()
        self.active_plan = None
        self.pending_question = None
        while not self.answer_queue.empty():
            try:
                self.answer_queue.get_nowait()
            except Exception:
                break

    def get_events_since(self, since_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        """Polling mirror of the replay window: cold events with ``id > since_id``."""
        events = [evt for seq, _frame, evt in self._ring if seq > since_id]
        return events[-limit:] if limit > 0 else events

    def get_status_summary(self) -> Dict[str, Any]:
        """Return high-level summary of active session."""
        return {
            "project_id": self.project_id,
            "is_running": self.is_running,
            "current_turn": self.current_turn,
            "last_event_id": self._next_seq,
            "active_plan": self.active_plan,
            "pending_question": self.pending_question,
            "events_in_buffer": len(self._ring),
            "subscribers_count": len(self._subs),
            "last_activity": self.last_activity,
        }


class AIAgentSessionManager:
    """Singleton managing background agent runs across all projects on Syte."""

    _instance: Optional["AIAgentSessionManager"] = None

    def __init__(self):
        self.sessions: Dict[str, ProjectAISession] = {}

    @classmethod
    def get_instance(cls) -> "AIAgentSessionManager":
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
            # Filter out old transient token/status chatter from the replay
            # window to prevent reconnect bloat on the next subscriber.
            session._ring = deque(
                [
                    entry
                    for entry in session._ring
                    if str(entry[2].get("event") or "") not in ("token_delta", "thought_delta", "status")
                ],
                maxlen=EVENT_BUFFER_SIZE,
            )

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
                    session.add_event({"event": "session_idle"})

            session.active_task = asyncio.create_task(_run_background_loop())

    async def subscribe(
        self,
        project_id: str,
        replay: bool = False,
        since_id: int = 0,
    ) -> AsyncIterator[bytes]:
        """Subscribe to live SSE byte frames for a project's agent session."""
        session = self.get_or_create_session(project_id)
        async for frame in session.subscribe(since_id=since_id, replay=replay):
            yield frame

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
