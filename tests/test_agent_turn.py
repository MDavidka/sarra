"""Tests for the OpenHands turn streaming state machine."""

from syte.agent_activity import extract_events_from_openhands_event
from syte.agent_turn import TurnState


def status(value: str) -> dict:
    return {
        "kind": "ConversationStateUpdateEvent",
        "key": "execution_status",
        "value": value,
    }


def test_stale_terminal_status_does_not_finish_new_turn() -> None:
    state = TurnState(initial_status="finished")

    state.process(status("finished"))

    assert state.started is False
    assert state.complete is False

    state.process(status("running"))
    state.process(status("finished"))

    assert state.complete is True


def test_running_to_idle_is_a_terminal_transition() -> None:
    state = TurnState(initial_status="idle")

    state.process(status("running"))
    state.process(status("idle"))

    assert state.complete is True


def test_function_call_name_is_correlated_with_its_result() -> None:
    state = TurnState(initial_status="idle")
    action = {
        "kind": "ActionEvent",
        "tool_call_id": "call-1",
        "tool_call": {
            "function": {
                "name": "execute_command",
                "arguments": '{"command":"pwd"}',
            }
        },
    }

    state.process(action)
    result = state.process({
        "kind": "ObservationEvent",
        "tool_call_id": "call-1",
        "observation": {"content": "/workspace"},
    })

    assert result["tool_name"] == "execute_command"


def test_nested_function_call_maps_name_and_arguments() -> None:
    events = extract_events_from_openhands_event({
        "kind": "ActionEvent",
        "tool_call_id": "call-2",
        "tool_call": {
            "function": {
                "name": "write_file",
                "arguments": '{"path":"src/app.py","content":"pass"}',
            }
        },
    })

    assert events[0]["event_type"] == "file_created"
    assert events[0]["payload"]["tool"] == "write_file"
    assert events[0]["payload"]["path"] == "src/app.py"
