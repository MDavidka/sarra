"""Tests for cloud-agent diagnostics."""

import json
from pathlib import Path

import pytest


def test_mask_api_key() -> None:
    from syte.agent_debug import mask_api_key
    assert mask_api_key("1234567890") == "1234…7890"
    assert mask_api_key("") == ""


def test_inspect_cloud_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_debug import inspect_agent_config

    config = tmp_path / "runtime.json"
    config.write_text(json.dumps({"runtime": "kilo-cloud", "transport": "direct-provider"}))
    monkeypatch.setattr("syte.cloud_agent.agent_config_path", lambda _id: config)
    info = inspect_agent_config("proj")
    assert info["exists"] is True
    assert info["runtime"] == "kilo-cloud"
    assert info["transport"] == "direct-provider"


def test_inspect_invalid_cloud_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_debug import inspect_agent_config

    config = tmp_path / "runtime.json"
    config.write_text("{")
    monkeypatch.setattr("syte.cloud_agent.agent_config_path", lambda _id: config)
    assert "Invalid Syte cloud" in inspect_agent_config("proj")["snippet"]
