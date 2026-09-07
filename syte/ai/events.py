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
from typing import Any, Dict, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


_AIEventType = Literal[
    "user_message_received",
    "user_input_received",
    "status",
    "thought_delta",
    "token_delta",
    "tool_call_start",
    "tool_call_result",
    "done",
    "error",
    "stopped",
    "cancelled",
    "session_idle",
    "user_input_required",
    "ping",
    "stream_gap",
]


class _Base(BaseModel):
    """Common config for every SSE event."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    event: _AIEventType
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class UserMessageReceived(_Base):
    event: Literal["user_message_received"]
    content: str


class UserInputReceived(_Base):
    event: Literal["user_input_received"]
    tool_call_id: str
    tool_name: str
    user_response: Dict[str, Any]


class Status(_Base):
    event: Literal["status"]
    message: str
    turn: Optional[int] = None
    tool_name: Optional[str] = None
    file_path: Optional[str] = None
    command: Optional[str] = None


class ThoughtDelta(_Base):
    """Hot-path minimal-delta frame — keep the payload tiny."""

    event: Literal["thought_delta"]
    delta: str


class TokenDelta(_Base):
    """Hot-path minimal-delta frame — keep the payload tiny."""

    event: Literal["token_delta"]
    delta: str


class ToolCallStart(_Base):
    event: Literal["tool_call_start"]
    tool_call_id: str
    tool_name: str
    arguments: Dict[str, Any]
    file_path: Optional[str] = None
    command: Optional[str] = None
    message: Optional[str] = None


class ToolCallResult(_Base):
    event: Literal["tool_call_result"]
    tool_call_id: str
    tool_name: str
    result: Dict[str, Any]
    file_path: Optional[str] = None
    command: Optional[str] = None


class Done(_Base):
    event: Literal["done"]
    reply: str


class ErrorEvent(_Base):
    event: Literal["error"]
    error: str


class Stopped(_Base):
    event: Literal["stopped"]
    message: str


class Cancelled(_Base):
    event: Literal["cancelled"]
    message: str


class SessionIdle(_Base):
    event: Literal["session_idle"]


class UserInputRequired(_Base):
    event: Literal["user_input_required"]
    question_data: Dict[str, Any]


class Ping(_Base):
    event: Literal["ping"]
    is_running: bool = False


class StreamGap(_Base):
    """Emitted to a specific subscriber whose bounded queue overflowed."""

    event: Literal["stream_gap"]
    dropped: int
    last_id: Optional[int] = None


AIEvent = Union[
    UserMessageReceived,
    UserInputReceived,
    Status,
    ThoughtDelta,
    TokenDelta,
    ToolCallStart,
    ToolCallResult,
    Done,
    ErrorEvent,
    Stopped,
    Cancelled,
    SessionIdle,
    UserInputRequired,
    Ping,
    StreamGap,
]


def build_event(payload: Dict[str, Any]) -> AIEvent:
    """Construct the right ``AIEvent`` subclass from a raw engine dict.

    Unknown event types are coerced into :class:`Status` so the stream
    never silently drops a frame the engine just learned to emit.
    """

    event_name = str(payload.get("event") or "status")
    data = dict(payload)
    try:
        cls = _REGISTRY.get(event_name, Status)
        return cls.model_validate(data)
    except Exception:
        # Last-resort: emit a Status with the original payload as `message`.
        return Status(
            event="status",
            message=str(data.get("message") or event_name),
            **{k: v for k, v in data.items() if k not in ("event", "timestamp", "message")},
        )


_REGISTRY: Dict[str, type] = {
    "user_message_received": UserMessageReceived,
    "user_input_received": UserInputReceived,
    "status": Status,
    "thought_delta": ThoughtDelta,
    "token_delta": TokenDelta,
    "tool_call_start": ToolCallStart,
    "tool_call_result": ToolCallResult,
    "done": Done,
    "error": ErrorEvent,
    "stopped": Stopped,
    "cancelled": Cancelled,
    "session_idle": SessionIdle,
    "user_input_required": UserInputRequired,
    "ping": Ping,
    "stream_gap": StreamGap,
}


def sse_frame(name: str, data: Dict[str, Any], event_id: Optional[int] = None) -> bytes:
    """Serialize a single SSE frame as ``bytes`` — ready to yield.

    Using bytes (not str) lets Starlette stream directly to the socket
    without an extra encode/decode round-trip per frame.
    """

    payload = build_event(data).model_dump(exclude_none=True)
    # Orjson-style hot path: build_event already validated & normalized,
    # so we use the stdlib encoder (fast enough for the cold path) but
    # avoid separators whitespace to shrink the wire payload.
    import json

    encoded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if event_id is None:
        return f"event: {name}\ndata: {encoded}\n\n".encode("utf-8")
    return f"id: {event_id}\nevent: {name}\ndata: {encoded}\n\n".encode("utf-8")