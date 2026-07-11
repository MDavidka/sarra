"""Tests for agent activity feed."""

from types import SimpleNamespace

import pytest

from syte.agent_activity import (
    extract_events_from_state,
    ingest_agent_state,
    record_agent_event,
    sync_history_tracker_from_state,
    _map_tool_event,
)


def test_map_tool_event_write_file() -> None:
    event_type, title, detail, payload = _map_tool_event(
        "write_file",
        {"path": "src/app.tsx"},
    )
    assert event_type == "file_created"
    assert "src/app.tsx" in detail


def test_map_tool_event_terminal() -> None:
    event_type, title, detail, _payload = _map_tool_event(
        "run_terminal_cmd",
        {"command": "npm run lint"},
    )
    assert event_type == "command_run"
    assert "npm run lint" in detail


def test_extract_events_from_assistant_tool_use() -> None:
    state = {
        "session": {
            "history": [
                {
                    "message": {
                        "role": "user",
                        "content": "Fix the navbar",
                    }
                },
                {
                    "message": {
                        "role": "assistant",
                        "content": [
                            {"type": "thinking", "thinking": "Checking layout…"},
                            {
                                "type": "tool_use",
                                "name": "edit_file",
                                "input": {"path": "src/Nav.tsx"},
                            },
                            {"type": "text", "text": "Updated the navbar."},
                        ],
                    }
                },
            ]
        }
    }
    events, index = extract_events_from_state(state, source="agent")
    assert index == 2
    types = [e["event_type"] for e in events]
    assert "user_message" in types
    assert "thinking" in types
    assert "file_modified" in types
    assert "assistant_message" in types


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


@pytest.mark.asyncio
async def test_sync_history_tracker_skips_reingest(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import agent_activity

    db_path = tmp_path / "test.db"
    monkeypatch.setattr(
        "syte.agent_activity.settings",
        SimpleNamespace(resolved_db_path=db_path),
    )

    state = {
        "session": {
            "history": [
                {"message": {"role": "user", "content": "Hi"}},
                {"message": {"role": "assistant", "content": "Hello"}},
            ]
        }
    }
    sync_history_tracker_from_state("proj-2", state)
    recorded = await ingest_agent_state("proj-2", state)
    assert recorded == []
    listed = await agent_activity.list_agent_events("proj-2")
    assert listed == []
