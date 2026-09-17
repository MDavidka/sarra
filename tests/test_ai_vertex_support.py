"""Unit tests for explicit Google Cloud Vertex AI and Gemini provider support in Syte."""

import json
import os
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from syte.ai.providers import (
    DEFAULT_BASE_URLS,
    ENV_KEY_MAP,
    UnifiedAIClient,
    VertexAuthManager,
    _normalize_base_url,
    _normalize_google_model,
)


def test_default_urls_and_env_keys():
    assert "vertex" in DEFAULT_BASE_URLS
    assert "gemini" in DEFAULT_BASE_URLS
    assert "VERTEX_API_KEY" in ENV_KEY_MAP["vertex"]
    assert "GOOGLE_APPLICATION_CREDENTIALS" in ENV_KEY_MAP["vertex"]
    assert "GEMINI_API_KEY" in ENV_KEY_MAP["gemini"]


def test_vertex_project_and_location_resolution(monkeypatch):
    monkeypatch.delenv("VERTEX_PROJECT_ID", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_PROJECT", raising=False)
    monkeypatch.delenv("GCP_PROJECT", raising=False)
    monkeypatch.delenv("PROJECT_ID", raising=False)

    # SA info takes precedence over empty env
    sa = {"project_id": "my-sa-project-123"}
    assert VertexAuthManager.resolve_gcp_project(sa_info=sa) == "my-sa-project-123"

    # Explicit project argument takes highest precedence
    assert VertexAuthManager.resolve_gcp_project("explicit-proj", sa_info=sa) == "explicit-proj"

    # Env var resolution
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "env-gcp-proj")
    assert VertexAuthManager.resolve_gcp_project() == "env-gcp-proj"

    # Location resolution
    monkeypatch.delenv("VERTEX_LOCATION", raising=False)
    monkeypatch.delenv("GOOGLE_CLOUD_REGION", raising=False)
    assert VertexAuthManager.resolve_gcp_location() == "us-central1"

    monkeypatch.setenv("VERTEX_LOCATION", "europe-west4")
    assert VertexAuthManager.resolve_gcp_location() == "europe-west4"


def test_service_account_parsing(tmp_path):
    sa_dict = {
        "type": "service_account",
        "project_id": "test-project",
        "client_email": "test@test-project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEFAASCBKgwggSkAgEAAoIBAQC3...\n-----END PRIVATE KEY-----\n",
    }
    sa_json = json.dumps(sa_dict)

    # Parse raw JSON
    parsed = VertexAuthManager.parse_service_account(sa_json)
    assert parsed is not None
    assert parsed["project_id"] == "test-project"

    # Parse JSON from file
    sa_file = tmp_path / "sa.json"
    sa_file.write_text(sa_json, encoding="utf-8")
    parsed_file = VertexAuthManager.parse_service_account(str(sa_file))
    assert parsed_file is not None
    assert parsed_file["client_email"] == "test@test-project.iam.gserviceaccount.com"

    # Invalid input
    assert VertexAuthManager.parse_service_account("invalid_key_string") is None


def test_normalize_google_model():
    assert _normalize_google_model("gemini-2.5-flash", is_vertex=True) == "gemini-2.5-flash"
    assert _normalize_google_model("gemini-2.5-flash-lite\u2060", is_vertex=True) == "gemini-2.5-flash-lite"
    assert _normalize_google_model("gemini-2.0-flash", is_vertex=True) == "gemini-2.0-flash"
    assert _normalize_google_model("gemini-1.5-pro-002", is_vertex=True) == "gemini-1.5-pro-002"
    assert _normalize_google_model("gemini-1.5-pro-002", is_vertex=False) == "gemini-1.5-pro"


def test_normalize_base_url_vertex(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "demo-proj")
    monkeypatch.setenv("VERTEX_LOCATION", "us-central1")

    url = _normalize_base_url("vertex", "")
    assert "https://us-central1-aiplatform.googleapis.com/v1/projects/demo-proj/locations/us-central1/publishers/google" in url

    gemini_url = _normalize_base_url("gemini", "")
    assert gemini_url == "https://generativelanguage.googleapis.com/v1beta/openai"


@pytest.mark.asyncio
async def test_vertex_missing_credentials():
    client = UnifiedAIClient(
        provider="vertex",
        model="gemini-2.0-flash",
        api_key="",
    )
    res = await client.test_connection()
    assert res["ok"] is False
    assert "Missing credentials" in res["error"]


def test_format_vertex_contents_and_tools():
    from syte.ai.providers import format_vertex_contents, format_vertex_tools

    messages = [
        {"role": "system", "content": "System instruction"},
        {"role": "user", "content": "Read the main file"},
        {
            "role": "assistant",
            "content": "Reading...",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "syte_read_file", "arguments": '{"path": "main.py"}'},
                }
            ],
        },
        {"role": "tool", "name": "syte_read_file", "content": '{"ok": true, "content": "print(1)"}'},
    ]

    contents = format_vertex_contents(messages)
    assert len(contents) == 3
    assert contents[0]["role"] == "user"
    assert contents[0]["parts"][0]["text"] == "Read the main file"
    assert contents[1]["role"] == "model"
    assert "functionCall" in contents[1]["parts"][1]
    assert contents[2]["role"] == "user"
    assert "functionResponse" in contents[2]["parts"][0]

    tools = [
        {
            "type": "function",
            "function": {
                "name": "syte_read_file",
                "description": "Read file contents",
                "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
            },
        }
    ]
    formatted_tools = format_vertex_tools(tools)
    assert formatted_tools is not None
    assert "functionDeclarations" in formatted_tools[0]
    assert formatted_tools[0]["functionDeclarations"][0]["name"] == "syte_read_file"

