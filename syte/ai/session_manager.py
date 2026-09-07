"""Persistent Background AI Agent Session Manager for Syte.

Maintains running agent tasks on the host VM across browser tab switches, page refreshes,
and disconnections. Handles multi-client event broadcasting, interactive user input gates,
and session UUID tracking.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import time
import uuid
from typing import Any, AsyncGenerator, Dict, List, Optional

from syte.database import get_project, update_project

logger = logging.getLogger("syte.ai.session_manager")


class ProjectAISession:
    """Manages the lifecycle and live event streams of an autonomous agent for a project / session UUID."""

    def __init__(self, project_id: str, session_id: Optional[str] = None):
        self.project_id = project_id
        self.session_id = session_id or str(uuid.uuid4())
        self.is_running = False
        self.current_turn = 0
        self.active_task: Optional[asyncio.Task] = None
        self.event_buffer: List[Dict[str, Any]] = []
        self.subscribers: List[asyncio.Queue] = []
        self.active_plan: Optional[Dict[str, Any]] = None
        self.pending_plan: Optional[Dict[str, Any]] = None
        self.pending_question: Optional[Dict[str, Any]] = None
        self.answer_queue: asyncio.Queue = asyncio.Queue()
        self.plan_decision_queue: asyncio.Queue = asyncio.Queue()
        self.created_at = datetime.now(timezone.utc).isoformat()
        self.last_activity = time.time()
        self.thinking_level: str = "medium"
        self.execution_speed: str = "balanced"
        self.lock = asyncio.Lock()

    def add_event(self, event: Dict[str, Any]) -> None:
        """Record event in buffer and broadcast to all active SSE listener queues."""
        self.last_activity = time.time()
        if "timestamp" not in event:
            event["timestamp"] = datetime.now(timezone.utc).isoformat()
        if "session_id" not in event:
            event["session_id"] = self.session_id
        if "project_id" not in event:
            event["project_id"] = self.project_id

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

        # Keep last 300 events in memory ring buffer
        self.event_buffer.append(event)
        if len(self.event_buffer) > 300:
            self.event_buffer.pop(0)

        # Broadcast to active subscriber queues
        dead_subs = []
        for q in list(self.subscribers):
            try:
                q.put_nowait(event)
            except Exception:
                dead_subs.append(q)
        for dead in dead_subs:
            if dead in self.subscribers:
                self.subscribers.remove(dead)

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
            "session_id": self.session_id,
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

    async def wait_for_plan_decision(self, plan_data: Dict[str, Any], timeout: float = 10.0, require_approval: bool = False) -> Dict[str, Any]:
        """Pause agent turn until user accepts, rejects, or auto-start timer finishes."""
        self.pending_plan = plan_data

        while not self.plan_decision_queue.empty():
            try:
                self.plan_decision_queue.get_nowait()
            except Exception:
                break

        self.add_event({
            "event": "plan_approval_required",
            "session_id": self.session_id,
            "plan": plan_data,
            "requires_approval": require_approval,
            "countdown_seconds": None if require_approval else int(timeout),
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        if require_approval:
            # Wait up to 10 minutes for manual user approval
            try:
                decision = await asyncio.wait_for(self.plan_decision_queue.get(), timeout=600.0)
                return decision
            except asyncio.TimeoutError:
                return {"action": "timeout", "message": "Plan approval timed out."}
            finally:
                self.pending_plan = None
        else:
            # 10s auto-start countdown unless paused/rejected
            try:
                decision = await asyncio.wait_for(self.plan_decision_queue.get(), timeout=timeout)
                return decision
            except asyncio.TimeoutError:
                return {"action": "auto_start", "message": "10s auto-start elapsed. Beginning implementation."}
            finally:
                self.pending_plan = None

    def resolve_plan_decision(self, decision_payload: Dict[str, Any]) -> bool:
        """Resolve a pending plan with user's decision."""
        self.pending_plan = None
        try:
            self.plan_decision_queue.put_nowait(decision_payload)
            return True
        except Exception:
            return False

    def clear(self) -> None:
        """Reset session state and event history."""
        if self.active_task and not self.active_task.done():
            self.active_task.cancel()
        self.is_running = False
        self.current_turn = 0
        self.event_buffer.clear()
        self.active_plan = None
        self.pending_plan = None
        self.pending_question = None
        while not self.answer_queue.empty():
            try:
                self.answer_queue.get_nowait()
            except Exception:
                break
        while not self.plan_decision_queue.empty():
            try:
                self.plan_decision_queue.get_nowait()
            except Exception:
                break

    def get_status_summary(self) -> Dict[str, Any]:
        """Return high-level summary of active session."""
        return {
            "session_id": self.session_id,
            "project_id": self.project_id,
            "is_running": self.is_running,
            "current_turn": self.current_turn,
            "active_plan": self.active_plan,
            "pending_plan": self.pending_plan,
            "pending_question": self.pending_question,
            "events_in_buffer": len(self.event_buffer),
            "subscribers_count": len(self.subscribers),
            "last_activity": self.last_activity,
        }


class AIAgentSessionManager:
    """Singleton managing background agent runs across all projects and session UUIDs on Syte."""

    _instance: Optional[AIAgentSessionManager] = None

    def __init__(self):
        self.sessions: Dict[str, ProjectAISession] = {}

    @classmethod
    def get_instance(cls) -> AIAgentSessionManager:
        if cls._instance is None:
            cls._instance = AIAgentSessionManager()
        return cls._instance

    def _resolve_key(self, project_id: str, session_id: Optional[str] = None) -> str:
        """Resolve internal mapping key. If session_id is provided, scope by project_id:session_id."""
        if session_id and session_id.strip():
            return f"{project_id}:{session_id.strip()}"
        return project_id

    def get_or_create_session(self, project_id: str, session_id: Optional[str] = None) -> ProjectAISession:
        if session_id and session_id.strip():
            sid = session_id.strip()
            key = self._resolve_key(project_id, sid)
            if key in self.sessions:
                return self.sessions[key]
            existing = self.get_session_by_id(sid)
            if existing:
                return existing
            sess = ProjectAISession(project_id, session_id=sid)
            self.sessions[key] = sess
            return sess

        # When session_id is omitted, prefer an actively running or latest session for project_id
        candidates = [s for s in self.sessions.values() if s.project_id == project_id]
        if candidates:
            running = [s for s in candidates if s.is_running]
            if running:
                return running[-1]
            candidates.sort(key=lambda s: getattr(s, "last_activity", 0))
            return candidates[-1]

        key = project_id
        if key not in self.sessions:
            self.sessions[key] = ProjectAISession(project_id)
        return self.sessions[key]

    def get_session_by_id(self, session_id: str) -> Optional[ProjectAISession]:
        """Find session by session_id across any project."""
        for key, sess in self.sessions.items():
            if sess.session_id == session_id:
                return sess
        return None

    def clear_session(self, project_id: str, session_id: Optional[str] = None) -> None:
        """Reset active session state and event buffer for project or specific session UUID."""
        key = self._resolve_key(project_id, session_id)
        if key in self.sessions:
            sess = self.sessions[key]
            sess.clear()

    async def stop_session(self, project_id: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """Cancel active background agent task and mark session stopped."""
        session = self.get_or_create_session(project_id, session_id=session_id)
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
            session.is_running = False
            session.add_event({
                "event": "stopped",
                "session_id": session.session_id,
                "message": "AI execution stopped by user.",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            return {"ok": True, "stopped": True, "session_id": session.session_id, "message": "Agent execution stopped."}
        return {"ok": True, "stopped": False, "session_id": session.session_id, "message": "No active agent task running."}

    async def start_turn(
        self,
        project_id: str,
        user_message: str,
        session_id: Optional[str] = None,
        settings_override: Optional[Dict[str, Any]] = None,
    ) -> ProjectAISession:
        """Spawn or run the autonomous agent turn in a background task."""
        from syte.ai.engine import AIAgentEngine

        session = self.get_or_create_session(project_id, session_id=session_id)
        if settings_override:
            if "thinking_level" in settings_override and settings_override["thinking_level"]:
                session.thinking_level = str(settings_override["thinking_level"]).strip().lower()
            if "execution_speed" in settings_override and settings_override["execution_speed"]:
                session.execution_speed = str(settings_override["execution_speed"]).strip().lower()

        async with session.lock:
            if session.is_running and session.active_task and not session.active_task.done():
                logger.info(f"Agent already running for session '{session.session_id}'. Attaching message to queue.")
                return session

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
                    session.add_event({"event": "cancelled", "session_id": session.session_id, "message": "Agent task was cancelled by user."})
                except Exception as exc:
                    logger.exception(f"Error in background AI turn for '{session.session_id}': {exc}")
                    session.add_event({"event": "error", "session_id": session.session_id, "error": str(exc)})
                finally:
                    session.is_running = False
                    session.add_event({"event": "session_idle", "session_id": session.session_id, "timestamp": datetime.now(timezone.utc).isoformat()})

            session.active_task = asyncio.create_task(_run_background_loop())
            return session

    async def subscribe(self, project_id: str, session_id: Optional[str] = None, replay: bool = False) -> AsyncGenerator[Dict[str, Any], None]:
        """Subscribe to live streaming events with keepalive heartbeat, optionally replaying recent buffer."""
        session = self.get_or_create_session(project_id, session_id=session_id)
        q: asyncio.Queue = asyncio.Queue()
        session.subscribers.append(q)

        # 1. Optionally replay existing buffered events
        if replay:
            for past_event in list(session.event_buffer):
                yield past_event

        # 2. Stream live events as they occur with 10s keepalive heartbeats
        try:
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
                        "session_id": session.session_id,
                        "project_id": session.project_id,
                        "is_running": session.is_running,
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
        finally:
            if q in session.subscribers:
                session.subscribers.remove(q)

    async def handle_user_answer(self, project_id: str, payload: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
        """Handle submission of general questions or secure environment variables."""
        session = self.get_or_create_session(project_id, session_id=session_id)
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
            return {"ok": True, "saved_to_env": True, "key": key, "session_id": session.session_id, "resumed_agent": resolved}

        # General question response
        resolved = session.resolve_user_answer({"answer": answer})
        return {"ok": True, "session_id": session.session_id, "resumed_agent": resolved}

    async def handle_user_plan_decision(self, project_id: str, payload: Dict[str, Any], session_id: Optional[str] = None) -> Dict[str, Any]:
        """Handle user plan decision (accept, start now, pause, revise)."""
        session = self.get_or_create_session(project_id, session_id=session_id)
        resolved = session.resolve_plan_decision(payload)
        session.add_event({
            "event": "plan_decision_received",
            "session_id": session.session_id,
            "decision": payload,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        return {"ok": True, "session_id": session.session_id, "resolved": resolved, "decision": payload}


# Global accessor
session_manager = AIAgentSessionManager.get_instance()
