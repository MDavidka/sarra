"""Tests for OpenHands Agent Server runtime management."""

import asyncio
import json
import sys
import types
from pathlib import Path

import pytest
from starlette.requests import Request

from syte.auth import verify_internal_service_request
from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_ensure_agent_runtime_assigns_port_and_profile(tmp_data_dir: Path) -> None:
    from syte.database import create_project, get_project, init_db, set_setting
    from syte.openhands_agent import OPENHANDS_RUNTIME, ensure_agent_runtime

    await init_db()
    await set_setting("agent_default_model_profile", "syra-havy")
    await create_project({
        "id": "proj-1",
        "name": "Agent Test",
        "port": 3000,
        "start_command": "",
    })

    project = await get_project("proj-1")
    project = await ensure_agent_runtime(project or {})

    assert project["agent_port"] == settings.agent_port_start
    assert project["agent_runtime"] == OPENHANDS_RUNTIME
    assert project["agent_status"] == "stopped"
    assert project["agent_model_profile"] == "syra-havy"


@pytest.mark.asyncio
async def test_write_agent_config_creates_private_server_config(tmp_data_dir: Path) -> None:
    from syte.database import create_project, get_project, init_db, set_setting, update_project
    from syte.openhands_agent import agent_config_path, agent_instruction_path, write_agent_config

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-2",
        "name": "Bridge Test",
        "port": 3001,
        "start_command": "",
    })
    await update_project("proj-2", {"agent_model_profile": "syra-base"})

    project = await get_project("proj-2")
    path = await write_agent_config(project or {})
    config = json.loads(path.read_text())

    assert path == agent_config_path("proj-2")
    assert config["session_api_keys"]
    assert config["secret_key"]
    assert config["workspace_path"].endswith("/workspaces/proj-2/app")
    assert config["max_concurrent_runs"] == 1
    assert "base-key" not in path.read_text()
    assert "OpenHands coding agent" in agent_instruction_path("proj-2").read_text()


@pytest.mark.asyncio
async def test_start_agent_reports_missing_server(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, get_project, init_db
    from syte.openhands_agent import start_agent

    await init_db()
    await create_project({
        "id": "proj-3",
        "name": "No Agent Server",
        "port": 3002,
        "start_command": "",
    })
    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: False)

    ok, message, meta = await start_agent("proj-3")
    project = await get_project("proj-3")

    assert ok is False
    assert "OpenHands Agent Server is not installed" in message
    assert meta == {}
    assert project["agent_status"] == "error"


@pytest.mark.asyncio
async def test_get_agent_status_exposes_proxy_and_backend_state(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import get_agent_status

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-4",
        "name": "Status",
        "port": 3003,
        "start_command": "",
    })
    await update_project("proj-4", {"agent_port": 5333, "agent_status": "running"})
    monkeypatch.setattr("syte.openhands_agent.is_agent_running", lambda _id: True)

    async def fake_probe(_port):
        return {"ok": True, "url": "http://127.0.0.1:5333/health", "status_code": 200}

    async def fake_backend(_project):
        return {"ok": True, "status_code": 200, "url": "https://api.deepseek.com/v1/models", "error": ""}

    monkeypatch.setattr("syte.openhands_agent.probe_agent_http", fake_probe)
    monkeypatch.setattr("syte.openhands_agent.backend_health", fake_backend)
    status = await get_agent_status("proj-4", request_base="https://sycord.site")

    assert status["agent_runtime"] == "openhands"
    assert status["agent_running"] is True
    assert status["agent_proxy_url"] == "https://sycord.site/api/internal/projects/proj-4/agent/proxy"
    assert status["agent_backend"]["ok"] is True


@pytest.mark.asyncio
async def test_write_agent_config_requires_active_profile_key(tmp_data_dir: Path) -> None:
    from syte.database import create_project, get_project, init_db, set_setting, update_project
    from syte.openhands_agent import write_agent_config

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-5",
        "name": "Missing Active Key",
        "port": 3005,
        "start_command": "",
    })
    await update_project("proj-5", {"agent_model_profile": "syra-nano"})

    project = await get_project("proj-5")
    with pytest.raises(RuntimeError, match="syra-nano"):
        await write_agent_config(project or {})


def test_build_agent_server_command_uses_loopback() -> None:
    from syte.openhands_agent import build_agent_server_command

    cmd = build_agent_server_command("/tmp/agent_server_config.json", 5200)
    assert "--host 127.0.0.1" in cmd
    assert "--port 5200" in cmd
    assert "openhands.agent_server" in cmd


@pytest.mark.asyncio
async def test_stream_turn_polls_terminal_status_without_running_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.openhands_agent import _stream_conversation_turn

    class FakeResponse:
        def __init__(self, payload: dict):
            self.status_code = 200
            self.content = b"{}"
            self._payload = payload

        def json(self):
            return self._payload

    class FakeClient:
        posted: list[dict] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            self.posted.append({"url": url, "headers": headers, "json": json})
            return FakeResponse({})

        async def get(self, url, headers=None):
            if url.endswith("/agent_final_response"):
                return FakeResponse({"response": "ok"})
            return FakeResponse({"execution_status": "finished"})

    class FakeWebSocket:
        sent: list[str] = []

        async def send(self, data):
            self.sent.append(data)

        async def recv(self):
            raise asyncio.TimeoutError

    class FakeConnection:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, *args):
            return False

    client_module = types.ModuleType("websockets.asyncio.client")
    client_module.connect = lambda *args, **kwargs: FakeConnection()
    asyncio_module = types.ModuleType("websockets.asyncio")
    asyncio_module.client = client_module
    websockets_module = types.ModuleType("websockets")
    websockets_module.asyncio = asyncio_module
    monkeypatch.setitem(sys.modules, "websockets", websockets_module)
    monkeypatch.setitem(sys.modules, "websockets.asyncio", asyncio_module)
    monkeypatch.setitem(sys.modules, "websockets.asyncio.client", client_module)
    monkeypatch.setattr("syte.openhands_agent.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "syte.openhands_agent.agent_session_headers",
        lambda _project_id: {"X-Session-API-Key": "session-key"},
    )

    reply, state, failure = await _stream_conversation_turn(
        "proj-1",
        port=5200,
        conversation_id="conversation-1",
        message="hello",
        request_id="req-1",
        source="test",
    )

    assert (reply, state, failure) == ("ok", "finished", "")
    assert FakeClient.posted[0]["json"]["content"] == [{"type": "text", "text": "hello"}]
    assert json.loads(FakeWebSocket.sent[0])["session_api_key"] == "session-key"


@pytest.mark.asyncio
async def test_communicate_with_agent_requires_api_key(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, update_project
    from syte.openhands_agent import communicate_with_agent

    await init_db()
    await create_project({
        "id": "proj-chat",
        "name": "Chat",
        "port": 3010,
        "start_command": "",
    })
    await update_project("proj-chat", {"agent_model_profile": "syra-base"})
    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)

    result = await communicate_with_agent("proj-chat", "hello", source="gui")

    assert result["ok"] is False
    assert result["error"] == "api_key_missing"
    assert "API key" in result["message"]


@pytest.mark.asyncio
async def test_verify_internal_service_request_accepts_shared_secret(tmp_data_dir: Path) -> None:
    from syte.database import init_db, set_setting

    await init_db()
    await set_setting("syra_internal_secret", "top-secret")
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/api/internal/projects/proj/agent",
        "headers": [(b"x-syra-internal-secret", b"top-secret")],
        "query_string": b"",
    }
    request = Request(scope)
    result = await verify_internal_service_request(request)
    assert result["auth"] == "internal-secret"
