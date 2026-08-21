from __future__ import annotations

from syte.cloud_agent_store import without_historical_tool_turns


def test_9router_recovery_removes_completed_historical_tool_turns() -> None:
    messages = [
        {"role": "user", "content": "Inspect the project"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_list_1",
                "type": "function",
                "function": {"name": "list_files", "arguments": '{"path":"app"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_list_1", "content": '{"ok":true}'},
        {"role": "assistant", "content": "The project uses Next.js."},
        {"role": "user", "content": "What did you find?"},
    ]

    recovered = without_historical_tool_turns(messages)

    assert [message["role"] for message in recovered] == ["user", "assistant", "user"]
    assert recovered[1]["content"] == "The project uses Next.js."
    assert not any(message.get("tool_calls") for message in recovered)
    assert not any(message.get("tool_call_id") for message in recovered)


def test_9router_recovery_keeps_assistant_text_that_accompanied_a_tool_call() -> None:
    messages = [
        {
            "role": "assistant",
            "content": "I will inspect the workspace.",
            "tool_calls": [{
                "id": "call_read_1",
                "type": "function",
                "function": {"name": "read_file", "arguments": '{"path":"app/package.json"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_read_1", "content": '{"ok":true}'},
    ]

    assert without_historical_tool_turns(messages) == [
        {"role": "assistant", "content": "I will inspect the workspace."}
    ]
