"""Tests for the VM-native Syte cloud agent."""

import json
from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


async def _project(project_id: str = "cloud-proj") -> dict:
    from syte.database import create_project, get_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({"id": project_id, "name": "Cloud", "port": 3000, "start_command": ""})
    return (await get_project(project_id)) or {}


@pytest.mark.asyncio
async def test_runtime_uses_no_project_port_and_writes_cloud_metadata(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import CLOUD_RUNTIME, agent_config_path, ensure_agent_runtime, write_agent_config

    project = await ensure_agent_runtime(await _project())
    path = await write_agent_config(project)
    config = json.loads(path.read_text())

    assert project["agent_runtime"] == CLOUD_RUNTIME
    assert project["agent_port"] is None
    assert config["transport"] == "direct-provider"
    assert config["streaming"] is False
    assert agent_config_path(project["id"]) == path
    assert "API key" not in path.read_text()


@pytest.mark.asyncio
async def test_start_is_embedded_and_immediately_ready(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import get_agent_status, start_agent

    project = await _project("start-proj")
    ok, message, status = await start_agent(project["id"])

    assert ok is True
    assert "ready" in message.lower()
    assert status["agent_runtime_type"] == "cloud"
    assert status["agent_port"] is None
    assert status["agent_running"] is True
    assert (await get_agent_status(project["id"], check_backend=False))["agent_healthy"] is True


@pytest.mark.asyncio
async def test_provider_retries_transient_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.cloud_agent import _provider_completion

    calls = 0

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                import httpx
                raise httpx.ConnectError("temporary")
            return Response()

    monkeypatch.setattr("syte.cloud_agent.httpx.AsyncClient", Client)
    monkeypatch.setattr("syte.cloud_agent.asyncio.sleep", lambda *_args: _noop())
    result = await _provider_completion(
        {"model": "deepseek-chat", "api_key": "key", "api_base": "https://provider", "profile": "syra-base"},
        [{"role": "user", "content": "hello"}],
    )
    assert result["content"] == "done"
    assert calls == 2


async def _noop():
    return None


@pytest.mark.asyncio
async def test_tool_loop_persists_messages_and_completes(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.cloud_agent import _communicate_with_agent_impl
    from syte.cloud_agent_store import conversation_messages

    project = await _project("tool-proj")
    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"app/README.md"}'},
        }]},
        {"role": "assistant", "content": "Finished the inspection."},
    ])

    async def fake_provider(*_args, **_kwargs):
        return next(replies)

    async def fake_tool(*_args, **_kwargs):
        return {"ok": True, "content": "hello"}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)
    monkeypatch.setattr("syte.cloud_agent._execute_tool", fake_tool)
    result = await _communicate_with_agent_impl(project["id"], "inspect", request_id="req-tool")
    history = await conversation_messages(project["id"])

    assert result["ok"] is True
    assert result["reply"] == "Finished the inspection."
    assert [message["role"] for message in history] == ["user", "assistant", "tool", "assistant"]
