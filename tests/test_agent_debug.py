"""Tests for AI agent debug diagnostics."""

import json
from pathlib import Path

import pytest

from syte.agent_debug import inspect_agent_config, mask_api_key, probe_profile_provider


def test_mask_api_key() -> None:
    assert mask_api_key("") == ""
    assert mask_api_key("short") == "••••"
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-a…mnop"


def test_inspect_agent_config_detects_invalid_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "agent_server_config.json"
    config.write_text("{ not-json")
    monkeypatch.setattr("syte.openhands_agent.agent_config_path", lambda _id: config)

    info = inspect_agent_config("proj-x")

    assert info["exists"] is True
    assert info["session_key_configured"] is False
    assert "Invalid OpenHands" in info["snippet"]


def test_inspect_agent_config_redacts_private_keys(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = tmp_path / "agent_server_config.json"
    config.write_text(json.dumps({
        "session_api_keys": ["session-secret"],
        "secret_key": "encryption-secret",
        "conversations_path": "/tmp/conversations",
    }))
    monkeypatch.setattr("syte.openhands_agent.agent_config_path", lambda _id: config)

    info = inspect_agent_config("proj-x")

    assert info["session_key_configured"] is True
    assert info["runtime"] == "openhands"
    assert info["conversations_path"] == "/tmp/conversations"
    assert "session-secret" not in info["snippet"]
    assert "encryption-secret" not in info["snippet"]
    assert "<redacted>" in info["snippet"]


@pytest.mark.asyncio
async def test_probe_profile_provider_marks_chat_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeResponse:
        def __init__(self, status_code: int, text: str):
            self.status_code = status_code
            self.text = text

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None):
            return FakeResponse(401, '{"error":"auth"}')

        async def post(self, url, headers=None, json=None):
            return FakeResponse(200, '{"choices":[{"message":{"content":"ok"}}]}')

    monkeypatch.setattr("syte.agent_debug.httpx.AsyncClient", FakeClient)
    result = await probe_profile_provider("syra-base", "test-key")
    assert result["ok"] is True
    assert len(result["probes"]) == 2
    assert result["probes"][0]["ok"] is False
    assert result["probes"][1]["ok"] is True
