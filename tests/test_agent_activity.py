"""Tests for the OpenHands-backed agent activity feed."""

from types import SimpleNamespace

import pytest

from syte.agent_activity import (
    _map_tool_event,
    extract_events_from_openhands_event,
    record_agent_event,
)


def test_map_tool_event_write_file() -> None:
    event_type, title, detail, payload = _map_tool_event(
        "write_file",
        {"path": "src/app.tsx"},
    )
    assert event_type == "file_created"
    assert title == "Create file"
    assert "src/app.tsx" in detail
    assert payload["path"] == "src/app.tsx"


def test_map_tool_event_terminal() -> None:
    event_type, title, detail, _payload = _map_tool_event(
        "run_terminal_cmd",
        {"command": "npm run lint"},
    )
    assert event_type == "command_run"
    assert title == "Ran command"
    assert "npm run lint" in detail


def test_map_file_editor_operations() -> None:
    event_type, title, detail, _ = _map_tool_event(
        "file_editor",
        {"command": "view", "path": "src/app.tsx"},
    )

    assert (event_type, title, detail) == ("file_read", "Read file", "src/app.tsx")


def test_extract_streaming_delta_event() -> None:
    events = extract_events_from_openhands_event(
        {"kind": "StreamingDeltaEvent", "id": "evt-1", "content": "Hello"},
        request_id="req-1",
        token_snapshot="Hello",
    )

    assert events == [{
        "event_type": "token_delta",
        "role": "assistant",
        "title": "Assistant",
        "detail": "Hello",
        "payload": {
            "request_id": "req-1",
            "openhands_event_id": "evt-1",
            "runtime": "openhands",
            "delta": "Hello",
            "snapshot": "Hello",
        },
        "source": "openhands",
    }]


def test_extract_action_event_maps_terminal_tool() -> None:
    events = extract_events_from_openhands_event(
        {
            "kind": "ActionEvent",
            "id": "evt-2",
            "tool_name": "terminal",
            "tool_call_id": "call-2",
            "summary": "Run the test suite",
            "action": {"kind": "TerminalAction", "command": "pytest -q"},
        },
        request_id="req-2",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "command_run"
    assert events[0]["detail"] == "Run the test suite"
    assert events[0]["payload"]["tool_call_id"] == "call-2"
    assert events[0]["payload"]["command"] == "pytest -q"


def test_extract_observation_event_maps_command_output() -> None:
    events = extract_events_from_openhands_event(
        {
            "kind": "ObservationEvent",
            "id": "evt-3",
            "tool_name": "terminal",
            "tool_call_id": "call-3",
            "observation": {"content": "1 passed", "is_error": False},
        },
        request_id="req-3",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "command_output"
    assert events[0]["detail"] == "1 passed"
    assert events[0]["payload"]["phase"] == "finished"


def test_extract_message_and_state_events() -> None:
    message_events = extract_events_from_openhands_event(
        {
            "kind": "MessageEvent",
            "id": "evt-4",
            "llm_message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "Done"}],
            },
        },
        request_id="req-4",
    )
    state_events = extract_events_from_openhands_event(
        {
            "kind": "ConversationStateUpdateEvent",
            "id": "evt-5",
            "key": "execution_status",
            "value": "running",
        },
        request_id="req-4",
    )

    assert message_events[0]["event_type"] == "assistant_message"
    assert message_events[0]["detail"] == "Done"
    assert state_events[0]["event_type"] == "processing"
    assert state_events[0]["payload"]["execution_status"] == "running"


def test_extract_server_error_event_finishes_request() -> None:
    events = extract_events_from_openhands_event(
        {
            "kind": "ServerErrorEvent",
            "id": "evt-error",
            "code": "invalid_event",
            "message": "The OpenHands event payload was rejected",
        },
        request_id="req-error",
    )

    assert len(events) == 1
    assert events[0]["event_type"] == "request_failed"
    assert events[0]["detail"] == "The OpenHands event payload was rejected"
    assert events[0]["payload"]["request_id"] == "req-error"
    assert events[0]["payload"]["error"] == "openhands_server_error"


@pytest.mark.asyncio
async def test_record_and_list_events(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import agent_activity

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "syte.agent_activity.settings",
        SimpleNamespace(resolved_db_path=db_path),
    )

    event = await record_agent_event(
        "proj-1",
        "user_message",
        role="user",
        title="User",
        detail="Hello",
        source="sycord",
    )
    assert event["id"] > 0
    assert event["event_type"] == "user_message"
    assert event["created_at"]
    assert event["payload"] == {}

    listed = await agent_activity.list_agent_events("proj-1")
    assert len(listed) == 1
    assert listed[0]["detail"] == "Hello"
