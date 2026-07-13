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


@pytest.fixture(autouse=True)
def _isolate_pooled_agent_clients():
    """Clear the per-port pooled HTTP client cache around every test.

    ``_get_agent_client`` memoizes clients in a module-global dict for
    performance. Without clearing it, a monkeypatched ``httpx.AsyncClient``
    from one test can leak into the next through the cache.
    """
    from syte import openhands_agent

    openhands_agent._agent_http_clients.clear()
    yield
    openhands_agent._agent_http_clients.clear()


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
    instruction = agent_instruction_path("proj-2").read_text()
    assert "OpenHands coding agent" in instruction
    assert "Before your first tool call, present a short concrete plan" in instruction


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
async def test_get_agent_status_can_skip_provider_probe(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import get_agent_status

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-fast-status",
        "name": "Fast status",
        "port": 3004,
        "start_command": "",
    })
    await update_project(
        "proj-fast-status",
        {"agent_port": 5334, "agent_model_profile": "syra-base"},
    )

    async def fake_probe(_port):
        return {"ok": False, "url": "http://127.0.0.1:5334/ready", "status_code": None}

    async def unexpected_backend_probe(_project):
        raise AssertionError("provider health must not run on the chat hot path")

    monkeypatch.setattr("syte.openhands_agent.probe_agent_http", fake_probe)
    monkeypatch.setattr("syte.openhands_agent.backend_health", unexpected_backend_probe)

    status = await get_agent_status("proj-fast-status", check_backend=False)

    assert status["agent_backend"]["ok"] is True
    assert status["agent_backend"]["probes"] == []


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


def test_openhands_installed_handles_missing_top_level_package(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.openhands_agent import openhands_installed

    def missing_package(_name: str):
        raise ModuleNotFoundError("No module named 'openhands'")

    monkeypatch.setattr("syte.openhands_agent.importlib.util.find_spec", missing_package)

    assert openhands_installed() is False


def test_build_agent_server_command_uses_loopback() -> None:
    from syte.openhands_agent import build_agent_server_command

    cmd = build_agent_server_command("/tmp/agent_server_config.json", 5200)
    assert "--host 127.0.0.1" in cmd
    assert "--port 5200" in cmd
    assert "openhands.agent_server" in cmd


@pytest.mark.asyncio
async def test_agent_health_requires_ready_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.openhands_agent import probe_agent_http

    class FakeResponse:
        status_code = 503

    class FakeClient:
        urls: list[str] = []

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url):
            self.urls.append(url)
            return FakeResponse()

    monkeypatch.setattr("syte.openhands_agent.httpx.AsyncClient", FakeClient)
    result = await probe_agent_http(5335)

    assert result["ok"] is False
    assert result["status_code"] == 503
    assert FakeClient.urls == ["http://127.0.0.1:5335/ready"]


@pytest.mark.asyncio
async def test_create_conversation_uses_supported_agent_context(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openhands.sdk.conversation.request import StartConversationRequest

    from syte.database import create_project, get_project, init_db, set_setting, update_project
    from syte.openhands_agent import _ensure_conversation, selected_model_metadata, write_agent_config

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-conversation",
        "name": "Conversation",
        "port": 3006,
        "start_command": "",
    })
    await update_project(
        "proj-conversation",
        {"agent_model_profile": "syra-base", "agent_port": 5336},
    )
    project = await get_project("proj-conversation")
    await write_agent_config(project or {})
    model = await selected_model_metadata(project or {})

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"id": "conversation-1"}

    class FakeClient:
        payload: dict = {}

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, _url, headers=None, json=None):
            self.payload = json
            FakeClient.payload = json
            return FakeResponse()

        async def get(self, url, headers=None, **kwargs):
            return FakeResponse()

    async def fake_wait(*_args, **_kwargs):
        return "idle"

    monkeypatch.setattr("syte.openhands_agent.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("syte.openhands_agent._wait_for_conversation_status", fake_wait)

    conversation_id, created = await _ensure_conversation(
        project or {},
        port=5336,
        model=model,
    )
    request = StartConversationRequest.model_validate(FakeClient.payload)

    assert conversation_id == "conversation-1"
    assert created is True
    assert request.initial_message is None
    assert request.agent.agent_context is not None
    assert "think before acting" in (
        request.agent.agent_context.system_message_suffix or ""
    )
    assert "mcpServers" not in FakeClient.payload["agent"]["mcp_config"]
    assert FakeClient.payload["agent"]["mcp_config"]["syte-tools"]["command"].endswith(
        "/syte-mcp"
    )


@pytest.mark.asyncio
async def test_stream_turn_waits_through_initial_idle_status(
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
        status_checks = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, url, headers=None, json=None):
            self.posted.append({"url": url, "headers": headers, "json": json})
            return FakeResponse({})

        async def get(self, url, headers=None, **kwargs):
            if url.endswith("/agent_final_response"):
                return FakeResponse({"response": "ok"})
            FakeClient.status_checks += 1
            if FakeClient.status_checks <= 8:
                return FakeResponse({"execution_status": "idle"})
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
    assert FakeClient.status_checks == 9
    assert FakeClient.posted[0]["json"]["content"] == [{"type": "text", "text": "hello"}]
    assert json.loads(FakeWebSocket.sent[0])["session_api_key"] == "session-key"


def test_message_send_server_error_is_recoverable_only_for_5xx() -> None:
    from syte.openhands_agent import _is_message_send_server_error

    assert _is_message_send_server_error(
        RuntimeError("OpenHands message send returned HTTP 500: Internal Server Error")
    )
    assert _is_message_send_server_error(
        RuntimeError("OpenHands message send returned HTTP 504: Gateway Timeout")
    )
    assert not _is_message_send_server_error(
        RuntimeError("OpenHands message send returned HTTP 409: already running")
    )
    assert not _is_message_send_server_error(RuntimeError("provider authentication failed"))


@pytest.mark.asyncio
async def test_send_conversation_message_retries_transient_server_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.openhands_agent import _send_conversation_message

    class FakeResponse:
        def __init__(self, status_code: int):
            self.status_code = status_code
            self.content = b"{}"
            self.text = "temporary failure"

        def json(self):
            return {"detail": "temporary failure"}

    class FakeClient:
        statuses = [500, 503, 200]
        calls = 0

        async def post(self, _url, headers=None, json=None):
            status = self.statuses[self.calls]
            self.calls += 1
            return FakeResponse(status)

    delays: list[float] = []

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr("syte.openhands_agent.asyncio.sleep", fake_sleep)
    client = FakeClient()
    await _send_conversation_message(
        client,
        base_url="http://127.0.0.1:5200",
        headers={"X-Session-API-Key": "session-key"},
        conversation_id="conversation-1",
        message="hello",
    )

    assert client.calls == 3
    assert delays == [0.25, 0.5]


@pytest.mark.asyncio
async def test_ensure_conversation_reuses_only_idle_or_finished(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, get_project, init_db, set_setting, update_project
    from syte.openhands_agent import _ensure_conversation, selected_model_metadata, write_agent_config

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-reuse-status",
        "name": "Reuse Status",
        "port": 3007,
        "start_command": "",
    })
    await update_project(
        "proj-reuse-status",
        {
            "agent_model_profile": "syra-base",
            "agent_port": 5338,
            "agent_conversation_id": "conversation-running",
        },
    )
    project = await get_project("proj-reuse-status")
    await write_agent_config(project or {})
    model = await selected_model_metadata(project or {})
    from syte.openhands_agent import agent_root

    meta_path = agent_root("proj-reuse-status") / "conversation-meta.json"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    from syte.openhands_agent import AGENT_INSTRUCTION_VERSION

    meta_path.write_text(json.dumps({"tooling_version": AGENT_INSTRUCTION_VERSION}) + "\n")

    class FakeResponse:
        def __init__(self, status_code: int, payload: dict | None = None):
            self.status_code = status_code
            self.content = b"{}"
            self._payload = payload or {}

        def json(self):
            return self._payload

    class FakeClient:
        get_calls = 0
        post_calls = 0

        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, headers=None, **kwargs):
            FakeClient.get_calls += 1
            if "conversation-running" in url:
                return FakeResponse(200, {"execution_status": "running"})
            if "conversation-new" in url:
                return FakeResponse(200, {"execution_status": "idle"})
            return FakeResponse(404)

        async def post(self, url, headers=None, json=None):
            FakeClient.post_calls += 1
            if url.endswith("/interrupt"):
                return FakeResponse(200)
            return FakeResponse(200, {"id": "conversation-new"})

    recovered: list[str] = []

    async def fake_recover(project_id, *, port, conversation_id):
        recovered.append(conversation_id)
        return False

    async def fake_wait(*_args, **_kwargs):
        return "idle"

    monkeypatch.setattr("syte.openhands_agent.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("syte.openhands_agent._recover_conversation_for_send", fake_recover)
    monkeypatch.setattr("syte.openhands_agent._wait_for_conversation_status", fake_wait)

    conversation_id, created = await _ensure_conversation(
        project or {},
        port=5338,
        model=model,
    )

    assert recovered == ["conversation-running"]
    assert conversation_id == "conversation-new"
    assert created is True
    assert FakeClient.post_calls >= 1


@pytest.mark.asyncio
async def test_communicate_with_agent_recovers_from_message_send_server_error(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import communicate_with_agent

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-send-recovery",
        "name": "Send Recovery",
        "port": 3018,
        "start_command": "",
    })
    await update_project(
        "proj-send-recovery",
        {"agent_model_profile": "syra-base", "agent_port": 5339},
    )

    ensure_calls = 0

    async def fake_ensure(*_args, **_kwargs):
        nonlocal ensure_calls
        ensure_calls += 1
        return (f"conversation-{ensure_calls}", ensure_calls == 1)

    stream_calls = 0

    async def fake_stream(*_args, **_kwargs):
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            raise RuntimeError(
                "OpenHands message send returned HTTP 500: Internal Server Error"
            )
        return "recovered", "finished", ""

    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)
    monkeypatch.setattr("syte.openhands_agent._agent_instruction_is_current", lambda _id: True)

    async def fake_status(*_args, **_kwargs):
        return {
            "agent_running": True,
            "agent_healthy": True,
            "agent_port": 5339,
            "agent_model": {"model": "test-model", "profile": "syra-base"},
        }

    async def fake_switch(*_args, **_kwargs):
        return None

    monkeypatch.setattr("syte.openhands_agent.get_agent_status", fake_status)
    monkeypatch.setattr("syte.openhands_agent._ensure_conversation", fake_ensure)
    monkeypatch.setattr("syte.openhands_agent._switch_conversation_llm", fake_switch)
    monkeypatch.setattr("syte.openhands_agent._stream_conversation_turn", fake_stream)

    result = await communicate_with_agent("proj-send-recovery", "hello", source="test")

    assert result["ok"] is True
    assert result["reply"] == "recovered"
    assert ensure_calls == 2
    assert stream_calls == 2


def test_is_mcp_session_error_detects_connection_closed_in_agent_log(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.openhands_agent import _is_mcp_session_error, agent_log_path

    project_id = "proj-mcp-log"
    log_path = agent_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "INFO starting\nMcpError: Connection closed\nmcp/shared/session.py:306\n"
    )

    error = RuntimeError("OpenHands message send returned HTTP 500: Internal Server Error")
    assert _is_mcp_session_error(error, project_id=project_id) is True
    assert _is_mcp_session_error(error, project_id="missing-project") is False


@pytest.mark.asyncio
async def test_communicate_with_agent_restarts_agent_on_mcp_session_error(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import communicate_with_agent

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-mcp-recovery",
        "name": "MCP Recovery",
        "port": 3019,
        "start_command": "",
    })
    await update_project(
        "proj-mcp-recovery",
        {"agent_model_profile": "syra-base", "agent_port": 5340},
    )

    ensure_calls = 0
    restart_calls = 0
    stream_calls = 0

    async def fake_ensure(*_args, **_kwargs):
        nonlocal ensure_calls
        ensure_calls += 1
        return (f"conversation-{ensure_calls}", ensure_calls > 1)

    async def fake_restart(project_id):
        nonlocal restart_calls
        restart_calls += 1
        return True, "restarted", {"agent_port": 5340}

    async def fake_stream(*_args, **_kwargs):
        nonlocal stream_calls
        stream_calls += 1
        if stream_calls == 1:
            raise RuntimeError(
                "OpenHands message send returned HTTP 500: Internal Server Error\n\n"
                "OpenHands agent log (last 20 lines):\nMcpError: Connection closed"
            )
        return "recovered", "finished", ""

    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)
    monkeypatch.setattr("syte.openhands_agent._agent_instruction_is_current", lambda _id: True)

    async def fake_status(*_args, **_kwargs):
        return {
            "agent_running": True,
            "agent_healthy": True,
            "agent_port": 5340,
            "agent_model": {"model": "test-model", "profile": "syra-base"},
        }

    async def fake_switch(*_args, **_kwargs):
        return None

    monkeypatch.setattr("syte.openhands_agent.get_agent_status", fake_status)
    monkeypatch.setattr("syte.openhands_agent._ensure_conversation", fake_ensure)
    monkeypatch.setattr("syte.openhands_agent._switch_conversation_llm", fake_switch)
    monkeypatch.setattr("syte.openhands_agent._stream_conversation_turn", fake_stream)
    monkeypatch.setattr("syte.openhands_agent.restart_agent", fake_restart)

    result = await communicate_with_agent("proj-mcp-recovery", "hello", source="test")

    assert result["ok"] is True
    assert result["reply"] == "recovered"
    assert restart_calls == 1
    assert ensure_calls == 2
    assert stream_calls == 2


@pytest.mark.asyncio
async def test_stream_turn_ignores_previous_finished_state(
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

    class FakeWebSocket:
        received = 0
        events = [
            {"kind": "ConversationStateUpdateEvent", "key": "execution_status", "value": "finished"},
            {
                "kind": "MessageEvent",
                "llm_message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "hello"}],
                },
            },
            asyncio.TimeoutError(),
            {"kind": "ConversationStateUpdateEvent", "key": "execution_status", "value": "running"},
            {
                "kind": "MessageEvent",
                "llm_message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "new answer"}],
                },
            },
            {"kind": "ConversationStateUpdateEvent", "key": "execution_status", "value": "finished"},
        ]

        async def send(self, _data):
            pass

        async def recv(self):
            event = self.events[FakeWebSocket.received]
            FakeWebSocket.received += 1
            if isinstance(event, BaseException):
                raise event
            return json.dumps(event)

    class FakeConnection:
        async def __aenter__(self):
            return FakeWebSocket()

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, _url, headers=None, json=None):
            return FakeResponse({})

        async def get(self, url, headers=None, **kwargs):
            if url.endswith("/agent_final_response"):
                reply = "new answer" if FakeWebSocket.received == 6 else "old answer"
                return FakeResponse({"response": reply})
            return FakeResponse({"execution_status": "finished"})

    async def ignore_event(*args, **kwargs):
        return []

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
    monkeypatch.setattr("syte.agent_activity.ingest_openhands_event", ignore_event)
    monkeypatch.setattr(
        "syte.openhands_agent.agent_session_headers",
        lambda _project_id: {"X-Session-API-Key": "session-key"},
    )

    result = await _stream_conversation_turn(
        "proj-1",
        port=5200,
        conversation_id="conversation-1",
        message="hello",
        request_id="req-1",
        source="test",
    )

    assert result == ("new answer", "finished", "")
    assert FakeWebSocket.received == 6


@pytest.mark.asyncio
async def test_communicate_with_agent_requires_api_key(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.agent_activity import list_agent_events
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
    failures = [
        event
        for event in await list_agent_events("proj-chat")
        if event["event_type"] == "request_failed"
    ]
    assert len(failures) == 1
    assert failures[0]["payload"]["error"] == "api_key_missing"
    assert failures[0]["payload"]["retry_message"] == "hello"


@pytest.mark.asyncio
async def test_communicate_with_agent_recovers_from_connection_failure(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import communicate_with_agent

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-connection-recovery",
        "name": "Connection Recovery",
        "port": 3017,
        "start_command": "",
    })
    await update_project(
        "proj-connection-recovery",
        {"agent_model_profile": "syra-base", "agent_port": 5337},
    )

    attempts = 0
    restarts = 0

    async def fake_start(_project_id: str, **_kwargs):
        nonlocal restarts
        restarts += 1
        return True, "restarted", {"agent_port": 5337}

    async def fake_ensure(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("All connection attempts failed")
        return "conversation-1", False

    async def fake_switch(*_args, **_kwargs):
        return None

    async def fake_stream(*_args, **_kwargs):
        return "recovered", "finished", ""

    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)
    monkeypatch.setattr("syte.openhands_agent._agent_instruction_is_current", lambda _id: True)

    async def fake_status(*_args, **_kwargs):
        return {
            "agent_running": True,
            "agent_healthy": True,
            "agent_port": 5337,
            "agent_model": {"model": "test-model"},
        }

    monkeypatch.setattr("syte.openhands_agent.get_agent_status", fake_status)
    monkeypatch.setattr("syte.openhands_agent.restart_agent", fake_start)
    monkeypatch.setattr("syte.openhands_agent._ensure_conversation", fake_ensure)
    monkeypatch.setattr("syte.openhands_agent._switch_conversation_llm", fake_switch)
    monkeypatch.setattr("syte.openhands_agent._stream_conversation_turn", fake_stream)

    result = await communicate_with_agent(
        "proj-connection-recovery",
        "hello",
        source="test",
    )

    assert result["ok"] is True
    assert result["reply"] == "recovered"
    assert attempts == 2
    assert restarts == 1


@pytest.mark.asyncio
async def test_communicate_with_agent_recovers_from_mcp_http_failure(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import httpx

    from syte.database import (
        create_project,
        get_project,
        init_db,
        set_setting,
        update_project,
    )
    from syte.openhands_agent import agent_log_path, communicate_with_agent

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-mcp-http-recovery",
        "name": "MCP HTTP Recovery",
        "port": 3020,
        "start_command": "",
    })
    await update_project(
        "proj-mcp-http-recovery",
        {
            "agent_model_profile": "syra-base",
            "agent_port": 5341,
            "agent_conversation_id": "stale-conversation",
        },
    )
    log_path = agent_log_path("proj-mcp-http-recovery")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("McpError: Connection closed\n")

    attempts = 0
    restarts = 0

    async def fake_restart(_project_id: str, **_kwargs):
        nonlocal restarts
        restarts += 1
        return True, "restarted", {"agent_port": 5341}

    async def fake_ensure(*_args, **_kwargs):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ReadError("event connection closed")
        await update_project(
            "proj-mcp-http-recovery",
            {"agent_conversation_id": "conversation-2"},
        )
        return "conversation-2", True

    async def fake_stream(*_args, **_kwargs):
        return "recovered", "finished", ""

    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)
    monkeypatch.setattr("syte.openhands_agent._agent_instruction_is_current", lambda _id: True)

    async def fake_status(*_args, **_kwargs):
        return {
            "agent_running": True,
            "agent_healthy": True,
            "agent_port": 5341,
            "agent_model": {"model": "test-model"},
        }

    monkeypatch.setattr("syte.openhands_agent.get_agent_status", fake_status)
    monkeypatch.setattr("syte.openhands_agent.restart_agent", fake_restart)
    monkeypatch.setattr("syte.openhands_agent._ensure_conversation", fake_ensure)
    monkeypatch.setattr("syte.openhands_agent._stream_conversation_turn", fake_stream)

    result = await communicate_with_agent(
        "proj-mcp-http-recovery",
        "hello",
        source="test",
    )

    project = await get_project("proj-mcp-http-recovery")
    assert result["ok"] is True
    assert result["reply"] == "recovered"
    assert project["agent_conversation_id"] == "conversation-2"
    assert attempts == 2
    assert restarts == 1


@pytest.mark.asyncio
async def test_background_failure_emits_request_scoped_terminal_event(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.agent_activity import list_agent_events
    from syte.agent_jobs import _running
    from syte.database import create_project, init_db, update_project
    from syte.openhands_agent import communicate_with_agent

    await init_db()
    await create_project({
        "id": "proj-background-chat",
        "name": "Background Chat",
        "port": 3011,
        "start_command": "",
    })
    await update_project(
        "proj-background-chat",
        {"agent_model_profile": "syra-base"},
    )
    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)

    accepted = await communicate_with_agent(
        "proj-background-chat",
        "fix the headline",
        source="gui",
        background=True,
    )
    await _running["proj-background-chat"]

    failures = [
        event
        for event in await list_agent_events("proj-background-chat")
        if event["event_type"] == "request_failed"
    ]
    assert accepted["status"] == "accepted"
    assert len(failures) == 1
    assert failures[0]["payload"]["request_id"] == accepted["request_id"]
    assert failures[0]["payload"]["error"] == "api_key_missing"
    assert failures[0]["payload"]["retry_message"] == "fix the headline"


@pytest.mark.asyncio
async def test_warm_agent_deduplicates_background_start(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.database import create_project, init_db, set_setting, update_project
    from syte.openhands_agent import (
        agent_warm_in_progress,
        get_agent_status,
        warm_agent,
    )

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({
        "id": "proj-warm",
        "name": "Warm Agent",
        "port": 3012,
        "start_command": "",
    })
    await update_project("proj-warm", {"agent_model_profile": "syra-base"})

    started = asyncio.Event()
    release = asyncio.Event()
    calls = 0

    async def fake_start(_project_id):
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return True, "ready", {"agent_status": "running"}

    async def fake_probe(_port):
        return {"ok": False, "status_code": None, "url": None}

    monkeypatch.setattr("syte.openhands_agent.openhands_installed", lambda: True)
    monkeypatch.setattr("syte.openhands_agent.start_agent", fake_start)
    monkeypatch.setattr("syte.openhands_agent.probe_agent_http", fake_probe)

    try:
        first = await warm_agent("proj-warm", source="test")
        second = await warm_agent("proj-warm", source="test")
        await asyncio.wait_for(started.wait(), timeout=1)
        status = await get_agent_status("proj-warm", check_backend=False)

        assert first["status"] == "warming"
        assert first["already_warming"] is False
        assert second["already_warming"] is True
        assert calls == 1
        assert agent_warm_in_progress("proj-warm") is True
        assert status["agent_status"] == "starting"
        assert status["agent_warming"] is True
    finally:
        release.set()
        for _ in range(20):
            if not agent_warm_in_progress("proj-warm"):
                break
            await asyncio.sleep(0)


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
