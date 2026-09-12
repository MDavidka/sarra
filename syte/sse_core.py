"""Shared low-latency Server-Sent Events plumbing for all Syte streaming endpoints.

Design goals (in priority order):

1. **Serialize once, fan out many.** Each event is JSON-encoded to ``bytes``
   exactly once at emit time; every subscriber queue carries the same immutable
   frame. N clients cost O(1) serialization instead of O(N).
2. **Zero added latency for the first token.** Hot deltas (``token_delta`` /
   ``thought_delta``) are coalesced by :class:`DeltaBatcher` into one frame per
   ~15 ms burst window — the first frame of a burst leaves within a scheduler
   tick, and slow trickles flush on the idle deadline instead of one frame per
   token.
3. **Bounded memory under backpressure.** Each subscriber gets a bounded
   queue; when it overflows the oldest queued frame is dropped and a
   ``stream_gap`` control frame tells the client to backfill via
   ``?since_id=``. A slow client can never pin the event loop or grow memory.
4. **Proxy-proof keepalives.** Every connection starts with ``retry:`` so
   browsers adopt the reconnect delay, and silence is broken by a heartbeat
   that is *both* an SSE comment (invisible to JS, keeps proxies warm) and a
   named ``ping`` data frame (visible to clients that never see comments).
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional

# Event types on the per-token hot path. They skip durable persistence and
# are coalesced before hitting the wire.
HOT_DELTA_EVENTS = frozenset({"token_delta", "thought_delta", "thinking_delta"})

# Batching window for hot deltas: flush a burst when it reaches the char or
# count cap, or after ``HOT_FLUSH_SECONDS`` of accumulation — whichever first.
HOT_FLUSH_CHARS = 500
HOT_FLUSH_COUNT = 32
HOT_FLUSH_SECONDS = 0.015

# Per-subscriber queue depth. Overflow drops the oldest frame and surfaces a
# ``stream_gap`` so clients know to backfill.
SUBSCRIBER_QUEUE_SIZE = 256

# Seconds of silence before a heartbeat frame is emitted. Deliberately below
# common reverse-proxy / CDN idle timeouts (30–60 s).
HEARTBEAT_SECONDS = 10.0

# Reconnect delay advertised to browser EventSource clients.
RETRY_MS = 2000

RETRY_FRAME = f"retry: {RETRY_MS}\n\n".encode("ascii")
HEARTBEAT_FRAME = (
    b": heartbeat\n\n"
    + b'event: heartbeat\ndata: {"event":"heartbeat","event_type":"heartbeat"}\n\n'
    + b'event: ping\ndata: {"event":"ping","event_type":"ping"}\n\n'
)

# Headers shared by every SSE response. ``no-transform`` stops intermediaries
# from re-buffering; ``X-Accel-Buffering`` disables nginx/Caddy response
# buffering. ``Connection`` is intentionally *not* set — it is a hop-by-hop
# header and invalid to manage from the application layer.
SSE_HEADERS = {
    "Cache-Control": "no-cache, no-store, no-transform",
    "Pragma": "no-cache",
    "X-Accel-Buffering": "no",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode_sse_frame(event: Dict[str, Any], event_id: Optional[int] = None) -> bytes:
    """Encode one activity event dict into a compact SSE frame.

    The JSON payload always embeds ``event`` so clients that only read
    ``data:`` lines (the Syte GUI uses fetch + manual parsing) still see the
    type, while ``event:`` lines let native ``EventSource`` listeners bind
    per type.
    """
    name = str(event.get("event") or event.get("event_type") or "message")
    if "event" not in event:
        event["event"] = name
    if "event_type" not in event:
        event["event_type"] = name
    data = json.dumps(event, separators=(",", ":"), ensure_ascii=False)
    if event_id is None:
        return f"event: {name}\ndata: {data}\n\n".encode("utf-8")
    return f"id: {event_id}\nevent: {name}\ndata: {data}\n\n".encode("utf-8")


def stream_gap_frame(dropped: int, last_id: int, reason: str = "backpressure") -> bytes:
    """Frame telling a client it missed events and must backfill via since_id."""
    return encode_sse_frame(
        {"event": "stream_gap", "dropped": dropped, "last_id": last_id, "reason": reason}
    )


class DeltaBatcher:
    """Coalesces hot token/thought deltas into one frame per burst window.

    ``emit`` is called with a merged event dict (same shape as the input,
    ``delta`` concatenated, extra ``batch_count`` field). When no asyncio loop
    is running (unit tests, sync callers) every push flushes synchronously so
    ordering semantics are preserved.
    """

    def __init__(
        self,
        emit: Callable[[Dict[str, Any]], None],
        max_chars: int = HOT_FLUSH_CHARS,
        max_count: int = HOT_FLUSH_COUNT,
        idle_seconds: float = HOT_FLUSH_SECONDS,
    ) -> None:
        self._emit = emit
        self._max_chars = max_chars
        self._max_count = max_count
        self._idle_seconds = idle_seconds
        # key (event type) -> {"template": dict, "parts": list, "chars": int, "count": int, "timer": TimerHandle|None}
        self._buf: Dict[str, Dict[str, Any]] = {}

    def push(self, event: Dict[str, Any]) -> None:
        key = str(event.get("event"))
        delta = str(event.get("delta") or "")
        entry = self._buf.get(key)
        if entry is None:
            template = dict(event)
            template.pop("delta", None)
            entry = self._buf[key] = {
                "template": template,
                "parts": [delta],
                "chars": len(delta),
                "count": 1,
                "timer": None,
            }
        else:
            entry["parts"].append(delta)
            entry["chars"] += len(delta)
            entry["count"] += 1

        if entry["chars"] >= self._max_chars or entry["count"] >= self._max_count:
            self.flush(key)
            return

        if entry["timer"] is None:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No loop available — flush synchronously to keep ordering.
                self.flush(key)
                return
            entry["timer"] = loop.call_later(self._idle_seconds, self._on_timer, key)

    def _on_timer(self, key: str) -> None:
        entry = self._buf.get(key)
        if entry is not None:
            entry["timer"] = None
            self.flush(key)

    def flush(self, key: str) -> None:
        entry = self._buf.pop(key, None)
        if entry is None:
            return
        if entry["timer"] is not None:
            entry["timer"].cancel()
        merged = dict(entry["template"])
        merged["delta"] = "".join(entry["parts"])
        if entry["count"] > 1:
            merged["batch_count"] = entry["count"]
        self._emit(merged)

    def flush_all(self) -> None:
        for key in list(self._buf):
            self.flush(key)

    def clear(self) -> None:
        for entry in self._buf.values():
            if entry["timer"] is not None:
                entry["timer"].cancel()
        self._buf.clear()

# ---------------------------------------------------------------------------
# Better-SSE Session & Channel Architecture (https://github.com/MatthewWid/better-sse)
# ---------------------------------------------------------------------------

import time
import uuid
from collections import deque
from typing import Set, Tuple

CHANNEL_HISTORY_SIZE = 300


class Session:
    """Represents a single SSE client connection (better-sse Session pattern)."""

    def __init__(
        self,
        session_id: Optional[str] = None,
        last_event_id: int = 0,
        state: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session_id: str = session_id or str(uuid.uuid4())
        self.last_event_id: int = last_event_id
        self.created_at: float = time.time()
        self.state: Dict[str, Any] = state or {}
        self.is_connected: bool = True
        self.queue: asyncio.Queue[Tuple[bytes, Optional[Dict[str, Any]]]] = asyncio.Queue(
            maxsize=SUBSCRIBER_QUEUE_SIZE
        )
        self.dropped: int = 0
        self.channels: Set["Channel"] = set()
        self._disconnect_callbacks: List[Callable[["Session"], None]] = []

    def on_disconnect(self, callback: Callable[["Session"], None]) -> None:
        """Register a callback for when the client disconnects."""
        self._disconnect_callbacks.append(callback)

    def push(
        self,
        data: Any,
        event: str = "message",
        event_id: Optional[int] = None,
    ) -> None:
        """Push an event frame directly to this specific session."""
        if not self.is_connected:
            return

        if isinstance(data, dict):
            payload = dict(data)
            payload.setdefault("event", event)
            payload.setdefault("event_type", event)
        else:
            payload = {"event": event, "event_type": event, "data": data}

        if event_id is not None:
            self.last_event_id = max(self.last_event_id, event_id)
            payload["id"] = event_id

        frame = encode_sse_frame(payload, event_id=event_id)
        self._enqueue(frame, payload)

    def ping(self, comment: str = "heartbeat") -> None:
        """Send a keep-alive comment / ping frame to keep connection warm."""
        if not self.is_connected:
            return
        self._enqueue(HEARTBEAT_FRAME, None)

    def _enqueue(self, frame: bytes, event: Optional[Dict[str, Any]]) -> None:
        try:
            self.queue.put_nowait((frame, event))
        except asyncio.QueueFull:
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            self.dropped += 1
            try:
                self.queue.put_nowait((frame, event))
            except asyncio.QueueFull:
                pass
        except Exception:
            pass

    async def iterate(
        self,
        since_id: int = 0,
        replay: bool = False,
        timeout: float = HEARTBEAT_SECONDS,
        request: Optional[Any] = None,
    ) -> AsyncIterator[bytes]:
        """Yield ready-to-stream SSE bytes with retry header, keepalive, and gap handling."""
        try:
            yield RETRY_FRAME

            while self.is_connected:
                if request is not None and hasattr(request, "is_disconnected"):
                    try:
                        if await request.is_disconnected():
                            break
                    except Exception:
                        pass

                try:
                    frame, event = await asyncio.wait_for(self.queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    if not self.is_connected:
                        break
                    yield HEARTBEAT_FRAME
                    continue

                if not self.is_connected:
                    break

                if not frame:
                    continue

                if self.dropped > 0:
                    dropped, self.dropped = self.dropped, 0
                    yield stream_gap_frame(dropped=dropped, last_id=self.last_event_id)

                if event and "id" in event and isinstance(event["id"], int):
                    self.last_event_id = max(self.last_event_id, event["id"])

                yield frame
        except (asyncio.CancelledError, GeneratorExit):
            pass
        finally:
            self.close()

    def close(self) -> None:
        """Close this session and deregister from all channels."""
        if not self.is_connected:
            return
        self.is_connected = False
        for channel in list(self.channels):
            channel.deregister(self)
        for cb in self._disconnect_callbacks:
            try:
                cb(self)
            except Exception:
                pass
        self._disconnect_callbacks.clear()
        try:
            self.queue.put_nowait((b"", None))
        except Exception:
            pass


class Channel:
    """Pub/Sub broadcast group for subtabs (better-sse Channel pattern)."""

    def __init__(self, name: str, history_size: int = CHANNEL_HISTORY_SIZE) -> None:
        self.name: str = name
        self.history_size: int = history_size
        self.sessions: Set[Session] = set()
        self._next_seq: int = 0
        self._ring: Deque[Tuple[int, bytes, Dict[str, Any]]] = deque(maxlen=history_size)
        self._batcher: DeltaBatcher = DeltaBatcher(self._broadcast_event)

    @property
    def session_count(self) -> int:
        return len(self.sessions)

    @property
    def last_event_id(self) -> int:
        return self._next_seq

    def register(
        self,
        session: Session,
        since_id: int = 0,
        replay: bool = False,
    ) -> None:
        """Register a session to receive broadcast events on this channel."""
        self.sessions.add(session)
        session.channels.add(self)

        if replay or since_id > 0:
            effective_since = -1 if replay and since_id <= 0 else since_id
            ring = list(self._ring)
            if effective_since > 0 and ring and effective_since < ring[0][0] - 1:
                gap = stream_gap_frame(
                    dropped=ring[0][0] - effective_since - 1,
                    last_id=max(ring[0][0] - 1, 0),
                    reason="replay_window_expired",
                )
                session._enqueue(gap, None)

            for seq, frame, evt in ring:
                if seq > effective_since:
                    session._enqueue(frame, evt)

    def deregister(self, session: Session) -> None:
        """Remove a session from this channel."""
        self.sessions.discard(session)
        session.channels.discard(self)

    def broadcast(
        self,
        data: Any,
        event: str = "message",
        event_id: Optional[int] = None,
        filter_fn: Optional[Callable[[Session], bool]] = None,
    ) -> None:
        """Broadcast an event payload to all registered sessions on this channel/subtab."""
        if isinstance(data, dict):
            payload = dict(data)
            payload.setdefault("event", event)
            payload.setdefault("event_type", event)
        else:
            payload = {"event": event, "event_type": event, "data": data}

        if str(payload.get("event") or "") in HOT_DELTA_EVENTS and isinstance(payload.get("delta"), str):
            self._batcher.push(payload)
            return

        self._batcher.flush_all()
        self._broadcast_event(payload, forced_id=event_id, filter_fn=filter_fn)

    def push_hot_delta(self, event: Dict[str, Any]) -> None:
        """Push high frequency token or thought deltas into the coalescing batcher."""
        self._batcher.push(event)

    def _broadcast_event(
        self,
        payload: Dict[str, Any],
        forced_id: Optional[int] = None,
        filter_fn: Optional[Callable[[Session], bool]] = None,
    ) -> None:
        evt_type = str(payload.get("event") or payload.get("event_type") or "message")
        if "timestamp" not in payload:
            payload["timestamp"] = utc_now_iso()

        if forced_id is not None:
            seq = forced_id
            self._next_seq = max(self._next_seq, forced_id)
        else:
            self._next_seq += 1
            seq = self._next_seq

        payload["id"] = seq
        frame = encode_sse_frame(payload, event_id=seq)

        # Exclude hot transient deltas from replay ring
        if evt_type not in ("token_delta", "thought_delta", "thinking_delta", "status"):
            self._ring.append((seq, frame, payload))

        for session in list(self.sessions):
            if filter_fn is None or filter_fn(session):
                session._enqueue(frame, payload)

    async def replay_for(self, since_id: int = 0, replay: bool = False) -> AsyncIterator[bytes]:
        """Yield historical frames from this channel's replay ring."""
        effective_since = -1 if replay and since_id <= 0 else since_id
        ring = list(self._ring)
        if effective_since > 0 and ring and effective_since < ring[0][0] - 1:
            yield stream_gap_frame(
                dropped=ring[0][0] - effective_since - 1,
                last_id=max(ring[0][0] - 1, 0),
                reason="replay_window_expired",
            )
        for seq, frame, _evt in ring:
            if seq > effective_since:
                yield frame

    def get_history(self, since_id: int = 0, limit: int = 200) -> List[Dict[str, Any]]:
        """Get event dicts since `since_id` for polling mirrors."""
        events = [evt for seq, _frame, evt in self._ring if seq > since_id]
        return events[-limit:] if limit > 0 else events

    def clear(self) -> None:
        """Clear the channel's batcher and replay history."""
        self._batcher.clear()
        self._ring.clear()


class ChannelHub:
    """Central registry and routing hub for better-sse Channels and Subtabs."""

    _instance: Optional["ChannelHub"] = None

    def __init__(self) -> None:
        self._channels: Dict[str, Channel] = {}

    @classmethod
    def get_instance(cls) -> "ChannelHub":
        if cls._instance is None:
            cls._instance = ChannelHub()
        return cls._instance

    def get_channel(self, name: str, create: bool = True) -> Optional[Channel]:
        """Get or lazily create a named channel."""
        if name not in self._channels:
            if not create:
                return None
            self._channels[name] = Channel(name)
        return self._channels[name]

    def get_subtab_channel(self, project_id: str, subtab: str) -> Channel:
        """Get or create the dedicated channel for a specific project subtab."""
        normalized_subtab = subtab.strip().lower()
        channel_name = f"project:{project_id}:subtab:{normalized_subtab}"
        return self.get_channel(channel_name)  # type: ignore[return-value]

    def broadcast_to_subtab(
        self,
        project_id: str,
        subtab: str,
        data: Any,
        event: str = "message",
    ) -> None:
        """Broadcast an event to a project's subtab channel and its general channel."""
        subtab_ch = self.get_subtab_channel(project_id, subtab)
        subtab_ch.broadcast(data, event=event)

        # Also mirror to the project's aggregate channel
        if subtab != "all":
            agg_ch = self.get_channel(f"project:{project_id}:all")
            if agg_ch and agg_ch.session_count > 0:
                if isinstance(data, dict):
                    enriched = dict(data)
                    enriched["_subtab"] = subtab
                else:
                    enriched = {"_subtab": subtab, "data": data}
                agg_ch.broadcast(enriched, event=event)

    def broadcast_to_project(
        self,
        project_id: str,
        data: Any,
        event: str = "message",
    ) -> None:
        """Broadcast an event across all subtabs and the project's aggregate channel."""
        agg_ch = self.get_channel(f"project:{project_id}:all")
        if agg_ch:
            agg_ch.broadcast(data, event=event)

    def create_session(
        self,
        last_event_id: int = 0,
        state: Optional[Dict[str, Any]] = None,
    ) -> Session:
        """Factory for creating a new SSE client Session."""
        return Session(last_event_id=last_event_id, state=state)

    def list_channels(self) -> List[Dict[str, Any]]:
        """Diagnostic list of active channels and subscriber counts."""
        return [
            {
                "name": ch.name,
                "subscribers": ch.session_count,
                "last_event_id": ch.last_event_id,
                "history_length": len(ch._ring),
            }
            for ch in self._channels.values()
        ]


# Global hub instance
channel_hub = ChannelHub.get_instance()
