"""Tests for agent AI quality improvements (Linear audit DAV-128+)."""

from __future__ import annotations

import json

from syte.cloud_agent import (
    _system_message_for_provider,
    _truncate_for_llm,
    _truncate_tool_payload,
)
from syte.thinking_levels import apply_prompt_cache_markers


def test_truncate_tool_payload_caps_large_json() -> None:
    huge = {"ok": True, "content": "x" * 80_000}
    encoded = _truncate_tool_payload(huge, max_chars=8_000)
    assert len(encoded) <= 8_000 + 50
    assert "truncated" in encoded.lower()


def test_truncate_for_llm_short_passthrough() -> None:
    assert _truncate_for_llm("hello") == "hello"


def test_system_message_splits_cache_for_anthropic() -> None:
    msg = _system_message_for_provider(
        "STATIC RULES",
        "DYNAMIC MEMORY",
        {"provider": "anthropic", "model": "claude-3-5"},
    )
    assert msg["role"] == "system"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msg["content"][0]["text"] == "STATIC RULES"
    assert msg["content"][1]["text"] == "DYNAMIC MEMORY"

    plain = _system_message_for_provider(
        "STATIC", "DYNAMIC", {"provider": "deepseek", "model": "deepseek-chat"},
    )
    assert plain["content"] == "STATIC\n\nDYNAMIC"


def test_apply_prompt_cache_markers_preserves_split_blocks() -> None:
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "dynamic"},
            ],
        },
        {"role": "user", "content": "hi"},
    ]
    out = apply_prompt_cache_markers(messages, provider="anthropic", model="claude")
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert out[0]["content"][1]["text"] == "dynamic"


def test_truncate_tool_payload_list_files_style() -> None:
    files = [{"name": f"f{i}", "path": f"app/{i}"} for i in range(500)]
    encoded = _truncate_tool_payload({"ok": True, "files": files}, max_chars=16_000)
    data = json.loads(encoded) if encoded.startswith("{") else None
    assert data is None or data.get("truncated") is True or len(encoded) <= 16_000
