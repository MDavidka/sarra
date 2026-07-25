"""Tests for Google AI Studio / Vertex Gemini key shapes and native transport."""

from __future__ import annotations

from syte.ai_providers import key_mismatch_hint
from syte.gemini_native import (
    GEMINI_NATIVE_API_BASE,
    gemini_response_to_openai_message,
    looks_like_google_ai_studio_key,
    looks_like_google_auth_key,
    openai_messages_to_gemini,
    openai_tools_to_gemini,
    should_use_native_gemini,
)


def test_google_key_shapes_accept_aq_and_aiza() -> None:
    assert looks_like_google_auth_key("AQ.AbCdEf123")
    assert looks_like_google_ai_studio_key("AQ.AbCdEf123")
    assert looks_like_google_ai_studio_key("AIzaSyDummyTrafficKey")
    assert not looks_like_google_auth_key("AIzaSyDummyTrafficKey")
    assert not looks_like_google_ai_studio_key("sk-or-v1-abc")


def test_nano_havy_no_longer_reject_aq_keys() -> None:
    assert key_mismatch_hint("syra-nano", "AQ.AbCdEf123") == ""
    assert key_mismatch_hint("syra-havy", "AIzaSyDummyTrafficKey") == ""
    hint = key_mismatch_hint("syra-nano", "sk-openai-looking")
    assert "AIza" in hint or "AQ." in hint
    assert "OpenAI-style" in hint


def test_should_use_native_for_aq_on_gemini_base() -> None:
    base = "https://generativelanguage.googleapis.com/v1beta/openai"
    assert should_use_native_gemini("AQ.AbCdEf123", base) is True
    assert should_use_native_gemini("AIzaSyDummy", base) is False
    assert should_use_native_gemini("AQ.AbCdEf123", "https://api.deepseek.com/v1") is False


def test_openai_to_gemini_roundtrip_shapes() -> None:
    system, contents = openai_messages_to_gemini([
        {"role": "system", "content": "You are Syte."},
        {"role": "user", "content": "List files"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_files", "arguments": '{"path":"."}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": '{"files":["a.py"]}'},
    ])
    assert system is not None
    assert system["parts"][0]["text"] == "You are Syte."
    assert contents[0]["role"] == "user"
    assert contents[1]["role"] == "model"
    assert contents[1]["parts"][0]["functionCall"]["name"] == "list_files"
    assert contents[2]["role"] == "user"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "list_files"

    tools = openai_tools_to_gemini([{
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        },
    }])
    assert tools[0]["functionDeclarations"][0]["name"] == "list_files"

    message = gemini_response_to_openai_message({
        "candidates": [{
            "content": {
                "parts": [
                    {"text": "Done"},
                    {"functionCall": {"name": "list_files", "args": {"path": "."}}},
                ]
            }
        }]
    })
    assert message["role"] == "assistant"
    assert message["content"] == "Done"
    assert message["tool_calls"][0]["function"]["name"] == "list_files"
    assert GEMINI_NATIVE_API_BASE.endswith("/v1beta")
