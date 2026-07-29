"""Tests for Vertex AI Express Mode key shapes and native transport."""

from __future__ import annotations

import json

from syte.ai_providers import VERTEX_API_BASE, key_mismatch_hint
from syte.gemini_native import (
    VERTEX_EXPRESS_API_BASE,
    gemini_response_to_openai_message,
    looks_like_google_ai_studio_key,
    looks_like_google_auth_key,
    looks_like_vertex_api_key,
    openai_messages_to_gemini,
    openai_tools_to_gemini,
    should_use_native_gemini,
    vertex_generate_content_url,
)


def test_vertex_api_base_is_aiplatform_express() -> None:
    assert VERTEX_API_BASE == VERTEX_EXPRESS_API_BASE
    assert "aiplatform.googleapis.com" in VERTEX_API_BASE
    assert "generativelanguage.googleapis.com" not in VERTEX_API_BASE


def test_google_key_shapes() -> None:
    assert looks_like_google_auth_key("AQ.AbCdEf123")
    assert looks_like_google_ai_studio_key("AQ.AbCdEf123")
    assert looks_like_google_ai_studio_key("AIzaSyDummyTrafficKey")
    assert looks_like_vertex_api_key("AQ.AbCdEf123")
    assert looks_like_vertex_api_key("some-vertex-express-key")
    assert not looks_like_vertex_api_key("sk-or-v1-abc")


def test_nano_accepts_vertex_express_keys() -> None:
    assert key_mismatch_hint("syra-nano", "vertex-express-key-xyz") == ""
    hint = key_mismatch_hint("syra-nano", "sk-openai-looking")
    assert "Vertex" in hint
    assert "OpenAI-style" in hint


def test_should_use_native_for_all_vertex_express_keys() -> None:
    base = VERTEX_EXPRESS_API_BASE
    assert should_use_native_gemini("AQ.AbCdEf123", base) is True
    assert should_use_native_gemini("any-cloud-key", base) is True
    assert should_use_native_gemini("AIzaSyDummy", base) is True
    assert should_use_native_gemini("AQ.AbCdEf123", "https://api.deepseek.com/v1") is False


def test_vertex_generate_url_uses_query_key() -> None:
    url = vertex_generate_content_url("gemini-3.1-flash-lite", "my-secret-key")
    assert url.startswith(
        "https://aiplatform.googleapis.com/v1/publishers/google/models/gemini-3.1-flash-lite:generateContent?key="
    )
    assert "my-secret-key" in url
    assert "generativelanguage.googleapis.com" not in url


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
    assert contents[1]["parts"][0]["functionCall"]["name"] == "list_files"
    assert contents[2]["parts"][0]["functionResponse"]["name"] == "list_files"

    tools = openai_tools_to_gemini([{
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "List files",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "additionalProperties": False,
            },
        },
    }])
    blob = json.dumps(tools)
    assert "additionalProperties" not in blob
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
    assert message["content"] == "Done"
    assert message["tool_calls"][0]["function"]["name"] == "list_files"


def test_sanitize_strips_additional_properties_from_tools() -> None:
    from syte.cloud_agent import TOOLS
    from syte.gemini_native import sanitize_gemini_schema

    tools = openai_tools_to_gemini(TOOLS)
    assert "additionalProperties" not in json.dumps(tools)

    raw = {
        "type": "object",
        "properties": {
            "env_vars": {"type": "object", "additionalProperties": {"type": "string"}},
            "merge": {"type": "boolean", "default": True},
        },
        "required": ["env_vars"],
        "additionalProperties": False,
    }
    cleaned = sanitize_gemini_schema(raw)
    assert "additionalProperties" not in cleaned
    assert "default" not in cleaned["properties"]["merge"]


def test_explain_vertex_api_key_errors() -> None:
    from syte.gemini_native import explain_google_api_error, format_google_http_error

    detail = (
        '{"error":{"code":403,"message":"Requests to this API aiplatform.googleapis.com '
        'are blocked.","status":"PERMISSION_DENIED","details":[{"reason":"API_KEY_SERVICE_BLOCKED"}]}}'
    )
    hint = explain_google_api_error(detail, status_code=403)
    assert "Vertex" in hint
    assert "aistudio.google.com" not in hint.lower()
    assert "Express" in hint or "aiplatform" in hint

    oauth = format_google_http_error(
        status_code=401,
        reason="Unauthorized",
        url="https://aiplatform.googleapis.com/v1/publishers/google/models/x:generateContent?key=***",
        detail='API keys are not supported by this API. Expected OAuth2',
    )
    assert "Express Mode" in oauth
