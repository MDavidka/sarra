"""Deterministic streaming coordinator for one OpenHands conversation turn."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

import httpx


TERMINAL_STATUSES = frozenset(
    {"finished", "error", "stuck", "paused", "waiting_for_confirmation"}
)


def event_kind(event: dict[str, Any]) -> str:
    return str(event.get("kind") or event.get("type") or "").lower()


def execution_status(event: dict[str, Any]) -> str:
    if event_kind(event) != "conversationstateupdateevent":
        return ""
    key = str(event.get("key") or "")
    value = event.get("value")
    if key == "execution_status":
        return str(value or "").lower()
    if key == "full_state" and isinstance(value, dict):
        return str(value.get("execution_status") or "").lower()
    return ""


def message_text(event: dict[str, Any], *, role: str = "assistant") -> str:
    if event_kind(event) != "messageevent":
        return ""
    message = event.get("llm_message") or event.get("message")
    if not isinstance(message, dict) or str(message.get("role") or "") != role:
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return str(content or "")
    return "".join(
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict)
    )


def _tool_name(event: dict[str, Any]) -> str:
    direct = event.get("tool_name")
    if direct:
        return str(direct)
    tool_call = event.get("tool_call")
    if isinstance(tool_call, dict):
        if tool_call.get("name"):
            return str(tool_call["name"])
        function = tool_call.get("function")
        if isinstance(function, dict) and function.get("name"):
            return str(function["name"])
    action = event.get("action")
    if isinstance(action, dict) and action.get("kind"):
        return str(action["kind"])
    return ""


@dataclass
class TurnState:
    """State machine that rejects terminal events left over from a prior run."""

    initial_status: str = ""
    status: str = ""
    started: bool = False
    saw_running: bool = False
    final_reply: str = ""
    token_snapshot: str = ""
    failure: str = ""
    tool_calls: dict[str, str] = field(default_factory=dict)

    def process(self, event: dict[str, Any]) -> dict[str, Any]:
        kind = event_kind(event)
        normalized = dict(event)
        tool_call_id = str(event.get("tool_call_id") or "")
        tool_name = _tool_name(event)
        if kind == "actionevent" and tool_call_id and tool_name:
            self.tool_calls[tool_call_id] = tool_name
        elif kind in {"observationevent", "agenterrorevent", "userrejectobservation"}:
            if not tool_name and tool_call_id:
                tool_name = self.tool_calls.get(tool_call_id, "")
            if tool_name:
                normalized["tool_name"] = tool_name

        if kind in {"streamingdeltaevent", "tokenevent"}:
            self.started = True
            self.token_snapshot += str(event.get("content") or event.get("delta") or "")
        reply = message_text(event)
        if reply:
            self.started = True
            self.final_reply = reply
        if kind in {"actionevent", "observationevent"}:
            self.started = True

        state = execution_status(event)
        if state:
            if state == "running" or state != self.initial_status:
                self.started = True
            self.saw_running = self.saw_running or state == "running"
            self.status = state
        if kind in {"conversationerrorevent", "servererrorevent"}:
            self.started = True
            self.status = "error"
            self.failure = str(
                event.get("detail")
                or event.get("message")
                or event.get("error")
                or event.get("code")
                or "OpenHands could not process the request"
            )
        return normalized

    @property
    def complete(self) -> bool:
        return self.started and (
            self.status in TERMINAL_STATUSES
            or (self.status == "idle" and self.saw_running)
        )


async def _conversation_status(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    headers: dict[str, str],
    conversation_id: str,
) -> str:
    response = await client.get(
        f"{base_url}/api/conversations/{conversation_id}",
        headers=headers,
        timeout=1.0,
    )
    if response.status_code >= 400:
        return ""
    try:
        data = response.json() if response.content else {}
    except ValueError:
        return ""
    return str(data.get("execution_status") or "").lower() if isinstance(data, dict) else ""


async def stream_turn(
    *,
    client: httpx.AsyncClient,
    base_url: str,
    websocket_url: str,
    headers: dict[str, str],
    conversation_id: str,
    message: str,
    timeout_s: float,
    send_message: Callable[..., Awaitable[None]],
    ingest_event: Callable[[dict[str, Any], str], Awaitable[Any]],
    get_final_response: Callable[..., Awaitable[str]],
) -> tuple[str, str, str]:
    """Stream one run, correlating functions and ignoring stale terminal state."""
    try:
        initial_status = await _conversation_status(
            client,
            base_url=base_url,
            headers=headers,
            conversation_id=conversation_id,
        )
    except httpx.HTTPError:
        initial_status = ""
    state = TurnState(initial_status=initial_status)
    deadline = time.monotonic() + timeout_s

    try:
        from websockets.asyncio.client import connect
    except ImportError as exc:
        raise RuntimeError("OpenHands streaming requires the websockets Python package") from exc

    async with connect(
        websocket_url,
        open_timeout=5,
        ping_interval=20,
        ping_timeout=20,
    ) as websocket:
        await websocket.send(
            json.dumps({"type": "auth", "session_api_key": headers["X-Session-API-Key"]})
        )
        socket_open = True
        pending: str | bytes | None = None
        try:
            pending = await asyncio.wait_for(websocket.recv(), timeout=0.5)
        except (asyncio.TimeoutError, TypeError, ValueError):
            pass

        await send_message(
            client,
            base_url=base_url,
            headers=headers,
            conversation_id=conversation_id,
            message=message,
        )

        while time.monotonic() < deadline:
            raw = pending
            pending = None
            if raw is None and socket_open:
                try:
                    raw = await asyncio.wait_for(websocket.recv(), timeout=0.5)
                except asyncio.TimeoutError:
                    pass
                except Exception:
                    socket_open = False

            if raw is None:
                try:
                    polled = await _conversation_status(
                        client,
                        base_url=base_url,
                        headers=headers,
                        conversation_id=conversation_id,
                    )
                except httpx.HTTPError:
                    await asyncio.sleep(0.1)
                    continue
                if polled:
                    state.process({
                        "kind": "ConversationStateUpdateEvent",
                        "key": "execution_status",
                        "value": polled,
                    })
                if state.complete:
                    break
                if not socket_open:
                    await asyncio.sleep(0.1)
                continue
            try:
                event = json.loads(raw)
            except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                continue
            if not isinstance(event, dict):
                continue
            normalized = state.process(event)
            if event_kind(normalized) not in {"conversationerrorevent", "servererrorevent"}:
                await ingest_event(normalized, state.token_snapshot)
            if state.failure or state.complete:
                break
        else:
            state.status = "timeout"

    if not state.final_reply:
        for attempt in range(3):
            state.final_reply = await get_final_response(
                client,
                base_url=base_url,
                headers=headers,
                conversation_id=conversation_id,
            )
            if state.final_reply:
                break
            await asyncio.sleep(0.1 * (attempt + 1))

    if state.status == "timeout":
        state.failure = state.failure or "OpenHands did not finish before the request timeout"
    elif state.status in {"error", "stuck", "paused"}:
        state.failure = state.failure or f"OpenHands conversation {state.status}"
    elif state.status == "waiting_for_confirmation":
        state.failure = state.failure or (
            "OpenHands is waiting for tool confirmation, which this chat cannot approve"
        )
    return state.final_reply, state.status or "finished", state.failure
