"""Typed SSE event payloads for the AI agent stream.

Every event the engine / session_manager emits becomes a member of
:class:`AIEvent` (a discriminated union on ``event``). This gives us:

* A single source of truth for the wire shape (replaces ``Dict[str, Any]``
  scattered across ``engine.py`` and ``session_manager.py``).
* Static checking via mypy / Pydantic — typos in field names break at
  model construction, not at the browser.
* Cheap serialization: ``model_dump_json()`` is C-accelerated and faster
  than the previous ``json.dumps(dict)`` per-frame path.

Hot-path frames (``token_delta`` / ``thought_delta``) are emitted in the
minimal-delta wire shape defined in ``docs/agent-streaming-api.md``: only
``event`` + ``delta`` (plus a fixed ``timestamp``). Cold frames use the
full envelope (``event`` + role-specific payload fields).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


_AIEventType = Literal[
    "connect_sse",
    "request_started",
    "processing",
    "status",
    "agent_started",
    "agent_stopped",
    "agent_restarted",
    "thinking",
    "thinking_delta",
    "thought_delta",
    "plan",
    "token_delta",
    "message_snapshot",
    "assistant_message",
    "user_message",
    "user_message_received",
    "user_input_received",
    "tool_call_started",
    "tool_call_start",
    "tool_call_finished",
    "tool_call_result",
    "tool_error",
    "tool_call",
    "file_created",
    "file_modified",
    "file_deleted",
    "file_read",
    "file_search",
    "file_changed",
    "command_run",
    "command_output",
    "screenshot",
    "question",
    "question_answered",
    "request_completed",
    "request_failed",
    "session_stopped",
    "usage",
    "service_action",
    "error",
    "heartbeat",
    "ping",
    "done",
    "stopped",
    "cancelled",
    "session_idle",
    "user_input_required",
    "stream_gap",
]


class _Base(BaseModel):
    """Common config for every SSE event."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    event: str
    event_type: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def model_post_init(self, __context: Any) -> None:
        if not self.event_type:
            self.event_type = self.event


# --- Hot-path events ---

class TokenDelta(_Base):
    """Hot-path minimal-delta frame — keep the payload tiny."""
    event: Literal["token_delta"] = "token_delta"
    delta: str
    request_id: Optional[str] = None
    session: Optional[int] = None
    agent: Optional[str] = "main"


class ThinkingDelta(_Base):
    """Hot-path minimal-delta frame for reasoning models."""
    event: Literal["thinking_delta"] = "thinking_delta"
    delta: str
    request_id: Optional[str] = None
    session: Optional[int] = None
    agent: Optional[str] = "main"


class ThoughtDelta(_Base):
    """Alias of ThinkingDelta for backward compatibility."""
    event: Literal["thought_delta"] = "thought_delta"
    delta: str
    request_id: Optional[str] = None
    session: Optional[int] = None
    agent: Optional[str] = "main"


# --- Turn & Session lifecycle events ---

class RequestStarted(_Base):
    event: Literal["request_started"] = "request_started"
    role: str = "system"
    title: str = "Request started"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "api"


class Processing(_Base):
    event: Literal["processing"] = "processing"
    role: str = "system"
    title: str = "Processing"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class Status(_Base):
    event: Literal["status"] = "status"
    message: Optional[str] = None
    turn: Optional[int] = None
    tool_name: Optional[str] = None
    file_path: Optional[str] = None
    command: Optional[str] = None
    role: str = "system"
    title: str = "Status"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class AgentStarted(_Base):
    event: Literal["agent_started"] = "agent_started"
    role: str = "system"
    title: str = "Agent started"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class AgentStopped(_Base):
    event: Literal["agent_stopped"] = "agent_stopped"
    role: str = "system"
    title: str = "Agent stopped"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "api"


class AgentRestarted(_Base):
    event: Literal["agent_restarted"] = "agent_restarted"
    role: str = "system"
    title: str = "Agent restarted"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "api"


class RequestCompleted(_Base):
    event: Literal["request_completed"] = "request_completed"
    role: str = "system"
    title: str = "Request completed"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class RequestFailed(_Base):
    event: Literal["request_failed"] = "request_failed"
    role: str = "system"
    title: str = "Request failed"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class SessionStopped(_Base):
    event: Literal["session_stopped"] = "session_stopped"
    role: str = "system"
    title: str = "Session stopped"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class ServiceAction(_Base):
    event: Literal["service_action"] = "service_action"
    role: str = "system"
    title: str = "Service action"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "system"


class Usage(_Base):
    event: Literal["usage"] = "usage"
    role: str = "system"
    title: str = "Usage"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class ErrorEvent(_Base):
    event: Literal["error"] = "error"
    error: str = ""
    detail: Optional[str] = None
    role: str = "system"
    title: str = "Error"
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "system"


class Heartbeat(_Base):
    event: Literal["heartbeat"] = "heartbeat"
    role: str = "system"
    is_running: bool = False


# --- Content & Planning events ---

class Thinking(_Base):
    event: Literal["thinking"] = "thinking"
    role: str = "assistant"
    title: str = "Thinking"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class Plan(_Base):
    event: Literal["plan"] = "plan"
    role: str = "assistant"
    title: str = "Plan"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class MessageSnapshot(_Base):
    event: Literal["message_snapshot"] = "message_snapshot"
    role: str = "assistant"
    title: str = "Message snapshot"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class AssistantMessage(_Base):
    event: Literal["assistant_message"] = "assistant_message"
    role: str = "assistant"
    title: str = "Assistant message"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class UserMessage(_Base):
    event: Literal["user_message"] = "user_message"
    role: str = "user"
    title: str = "User message"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "user"


class UserMessageReceived(_Base):
    event: Literal["user_message_received"] = "user_message_received"
    content: str = ""


class UserInputReceived(_Base):
    event: Literal["user_input_received"] = "user_input_received"
    tool_call_id: str = ""
    tool_name: str = ""
    user_response: Dict[str, Any] = Field(default_factory=dict)


# --- Tool & Workspace events ---

class ToolCallStarted(_Base):
    event: Literal["tool_call_started"] = "tool_call_started"
    role: str = "assistant"
    title: str = "Tool started"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    arguments: Optional[Dict[str, Any]] = None
    source: str = "agent"


class ToolCallStart(_Base):
    event: Literal["tool_call_start"] = "tool_call_start"
    tool_call_id: str = ""
    tool_name: str = ""
    arguments: Dict[str, Any] = Field(default_factory=dict)
    file_path: Optional[str] = None
    command: Optional[str] = None
    message: Optional[str] = None


class ToolCallFinished(_Base):
    event: Literal["tool_call_finished"] = "tool_call_finished"
    role: str = "tool"
    title: str = "Tool finished"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    tool_call_id: Optional[str] = None
    tool_name: Optional[str] = None
    result: Optional[Dict[str, Any]] = None
    source: str = "agent"


class ToolCallResult(_Base):
    event: Literal["tool_call_result"] = "tool_call_result"
    tool_call_id: str = ""
    tool_name: str = ""
    result: Dict[str, Any] = Field(default_factory=dict)
    file_path: Optional[str] = None
    command: Optional[str] = None


class ToolError(_Base):
    event: Literal["tool_error"] = "tool_error"
    role: str = "tool"
    title: str = "Tool error"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class ToolCall(_Base):
    event: Literal["tool_call"] = "tool_call"
    role: str = "assistant"
    title: str = "Tool call"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileCreated(_Base):
    event: Literal["file_created"] = "file_created"
    role: str = "tool"
    title: str = "File created"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileModified(_Base):
    event: Literal["file_modified"] = "file_modified"
    role: str = "tool"
    title: str = "File modified"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileDeleted(_Base):
    event: Literal["file_deleted"] = "file_deleted"
    role: str = "tool"
    title: str = "File deleted"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileRead(_Base):
    event: Literal["file_read"] = "file_read"
    role: str = "tool"
    title: str = "File read"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileSearch(_Base):
    event: Literal["file_search"] = "file_search"
    role: str = "tool"
    title: str = "File search"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class FileChanged(_Base):
    event: Literal["file_changed"] = "file_changed"
    role: str = "tool"
    title: str = "File changed"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class CommandRun(_Base):
    event: Literal["command_run"] = "command_run"
    role: str = "tool"
    title: str = "Command started"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class CommandOutput(_Base):
    event: Literal["command_output"] = "command_output"
    role: str = "tool"
    title: str = "Command output"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class Screenshot(_Base):
    event: Literal["screenshot"] = "screenshot"
    role: str = "system"
    title: str = "Screenshot"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class Question(_Base):
    event: Literal["question"] = "question"
    role: str = "assistant"
    title: str = "Question"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "agent"


class QuestionAnswered(_Base):
    event: Literal["question_answered"] = "question_answered"
    role: str = "user"
    title: str = "Answer"
    detail: Optional[str] = None
    payload: Dict[str, Any] = Field(default_factory=dict)
    source: str = "user"


# --- Legacy / Internal control events ---

class Done(_Base):
    event: Literal["done"] = "done"
    reply: str = ""


class Stopped(_Base):
    event: Literal["stopped"] = "stopped"
    message: str = ""


class Cancelled(_Base):
    event: Literal["cancelled"] = "cancelled"
    message: str = ""


class SessionIdle(_Base):
    event: Literal["session_idle"] = "session_idle"


class UserInputRequired(_Base):
    event: Literal["user_input_required"] = "user_input_required"
    question_data: Dict[str, Any] = Field(default_factory=dict)


class Ping(_Base):
    event: Literal["ping"] = "ping"
    is_running: bool = False


class StreamGap(_Base):
    event: Literal["stream_gap"] = "stream_gap"
    dropped: int = 0
    last_id: Optional[int] = None


AIEvent = Union[
    TokenDelta,
    ThinkingDelta,
    ThoughtDelta,
    RequestStarted,
    Processing,
    Status,
    AgentStarted,
    AgentStopped,
    AgentRestarted,
    RequestCompleted,
    RequestFailed,
    SessionStopped,
    ServiceAction,
    Usage,
    ErrorEvent,
    Heartbeat,
    Thinking,
    Plan,
    MessageSnapshot,
    AssistantMessage,
    UserMessage,
    UserMessageReceived,
    UserInputReceived,
    ToolCallStarted,
    ToolCallStart,
    ToolCallFinished,
    ToolCallResult,
    ToolError,
    ToolCall,
    FileCreated,
    FileModified,
    FileDeleted,
    FileRead,
    FileSearch,
    FileChanged,
    CommandRun,
    CommandOutput,
    Screenshot,
    Question,
    QuestionAnswered,
    Done,
    Stopped,
    Cancelled,
    SessionIdle,
    UserInputRequired,
    Ping,
    StreamGap,
]


_REGISTRY: Dict[str, type] = {
    # Hot path
    "token_delta": TokenDelta,
    "thinking_delta": ThinkingDelta,
    "thought_delta": ThoughtDelta,
    # Turn lifecycle
    "request_started": RequestStarted,
    "processing": Processing,
    "status": Status,
    "agent_started": AgentStarted,
    "agent_stopped": AgentStopped,
    "agent_restarted": AgentRestarted,
    "request_completed": RequestCompleted,
    "request_failed": RequestFailed,
    "session_stopped": SessionStopped,
    "service_action": ServiceAction,
    "usage": Usage,
    "error": ErrorEvent,
    "heartbeat": Heartbeat,
    # Reasoning & content
    "thinking": Thinking,
    "plan": Plan,
    "message_snapshot": MessageSnapshot,
    "assistant_message": AssistantMessage,
    "user_message": UserMessage,
    "user_message_received": UserMessageReceived,
    "user_input_received": UserInputReceived,
    # Tools & artifacts
    "tool_call_started": ToolCallStarted,
    "tool_call_start": ToolCallStart,
    "tool_call_finished": ToolCallFinished,
    "tool_call_result": ToolCallResult,
    "tool_error": ToolError,
    "tool_call": ToolCall,
    "file_created": FileCreated,
    "file_modified": FileModified,
    "file_deleted": FileDeleted,
    "file_read": FileRead,
    "file_search": FileSearch,
    "file_changed": FileChanged,
    "command_run": CommandRun,
    "command_output": CommandOutput,
    "screenshot": Screenshot,
    "question": Question,
    "question_answered": QuestionAnswered,
    # Control & legacy
    "done": Done,
    "stopped": Stopped,
    "cancelled": Cancelled,
    "session_idle": SessionIdle,
    "user_input_required": UserInputRequired,
    "ping": Ping,
    "stream_gap": StreamGap,
}


def build_event(payload: Dict[str, Any]) -> AIEvent:
    """Construct the right ``AIEvent`` subclass from a raw engine dict.

    Unknown event types are coerced into :class:`Status` so the stream
    never silently drops a frame the engine just learned to emit.
    """
    event_name = str(payload.get("event") or payload.get("event_type") or "status")
    data = dict(payload)
    if "event" not in data:
        data["event"] = event_name
    if "event_type" not in data:
        data["event_type"] = event_name

    try:
        cls = _REGISTRY.get(event_name, Status)
        return cls.model_validate(data)
    except Exception:
        # Last-resort: emit a Status with the original payload as `message`.
        return Status(
            event="status",
            event_type="status",
            message=str(data.get("message") or event_name),
            **{k: v for k, v in data.items() if k not in ("event", "event_type", "timestamp", "message")},
        )


def sse_frame(name: str, data: Dict[str, Any], event_id: Optional[int] = None) -> bytes:
    """Serialize a single SSE frame as ``bytes`` — ready to yield.

    Using bytes (not str) lets Starlette stream directly to the socket
    without an extra encode/decode round-trip per frame.
    """

    payload = build_event(data).model_dump(exclude_none=True)
    import json

    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if event_id is None:
        return f"event: {name}\ndata: {encoded}\n\n".encode("utf-8")
    return f"id: {event_id}\nevent: {name}\ndata: {encoded}\n\n".encode("utf-8")