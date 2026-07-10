"""Tests for AI agent debug diagnostics."""

from pathlib import Path

import pytest

from syte.agent_debug import (
    inspect_agent_config,
    mask_api_key,
    probe_profile_provider,
)


def test_mask_api_key() -> None:
    assert mask_api_key("") == ""
    assert mask_api_key("short") == "••••"
    assert mask_api_key("sk-abcdefghijklmnop") == "sk-a…mnop"


def test_inspect_agent_config_detects_missing_env_refs(tmp_path: Path) -> None:
    config = tmp_path / "opencode.json"
    config.write_text('{"provider": {"syra-base": {"options": {"apiKey": "plain-text"}}}}')

    from syte import opencode_agent

    original = opencode_agent.agent_config_path
    opencode_agent.agent_config_path = lambda _id: config
    try:
        info = inspect_agent_config("proj-x")
    finally:
        opencode_agent.agent_config_path = original

    assert info["exists"] is True
    assert info["secret_syntax_ok"] is False
    assert info["env_refs"] == []


def test_inspect_agent_config_accepts_valid_env_refs(tmp_path: Path) -> None:
    config = tmp_path / "opencode.json"
    config.write_text(
        '{"provider": {"syra-base": {"options": {"apiKey": "{env:SYRA_BASE_API_KEY}"}}}}'
    )

    from syte import opencode_agent

    original = opencode_agent.agent_config_path
    opencode_agent.agent_config_path = lambda _id: config
    try:
        info = inspect_agent_config("proj-x")
    finally:
        opencode_agent.agent_config_path = original

    assert info["secret_syntax_ok"] is True
    assert info["env_refs"] == ["SYRA_BASE_API_KEY"]


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
