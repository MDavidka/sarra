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
HOT_DELTA_EVENTS = frozenset({"token_delta", "thought_delta"})

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
    + b'event: ping\ndata: {"event":"ping"}\n\n'
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
    name = str(event.get("event") or "message")
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

    @property
    def pending(self) -> bool:
        return bool(self._buf)
