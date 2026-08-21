from syte.cloud_agent import _normalize_tool_call_ids


def test_tool_call_ids_preserve_provider_assigned_identifiers() -> None:
    calls = [
        {
            "id": "provider-call-1",
            "type": "function",
            "function": {"name": "list_files", "arguments": '{"path":"."}'},
        }
    ]

    assert _normalize_tool_call_ids(calls) == calls


def test_tool_call_ids_synthesize_stable_unique_ids_when_gateway_omits_them() -> None:
    calls = [
        {"type": "function", "function": {"name": "list_files", "arguments": "{}"}},
        {"type": "function", "function": {"name": "list_files", "arguments": "{}"}},
        {"id": "duplicate", "type": "function", "function": {"name": "read_file"}},
        {"id": "duplicate", "type": "function", "function": {"name": "read_file"}},
    ]

    normalized = _normalize_tool_call_ids(calls)

    assert [call["id"] for call in normalized] == [
        "call_list_files_1",
        "call_list_files_2",
        "duplicate",
        "call_read_file_4",
    ]
    assert len({call["id"] for call in normalized}) == len(normalized)


def test_normalized_ids_pair_with_following_tool_results() -> None:
    calls = _normalize_tool_call_ids(
        [
            {"type": "function", "function": {"name": "list_files"}},
            {"type": "function", "function": {"name": "read_file"}},
        ]
    )

    tool_results = [
        {"role": "tool", "tool_call_id": call["id"], "content": "{}"}
        for call in calls
    ]

    assert [result["tool_call_id"] for result in tool_results] == [
        "call_list_files_1",
        "call_read_file_2",
    ]


def test_tool_call_ids_do_not_mutate_the_provider_response() -> None:
    calls = [{"type": "function", "function": {"name": "write file"}}]

    normalized = _normalize_tool_call_ids(calls)

    assert calls[0].get("id") is None
    assert normalized[0]["id"] == "call_write_file_1"
    assert normalized[0]["function"] is not calls[0]["function"]
