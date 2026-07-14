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
    assert result["session"] == 1
    assert [message["role"] for message in history] == ["user", "assistant", "tool", "assistant"]


@pytest.mark.asyncio
async def test_conversation_messages_loads_only_last_session(tmp_data_dir: Path) -> None:
    from syte.cloud_agent_store import append_message, begin_turn_session, conversation_messages
    from syte.database import init_db

    project_id = "session-hist-proj"
    await init_db()
    s1 = await begin_turn_session(project_id, "syra-base")
    await append_message(project_id, "req-1", "user", "old turn", session_number=s1)
    await append_message(project_id, "req-1", "assistant", "old reply", session_number=s1)
    s2 = await begin_turn_session(project_id, "syra-base")
    await append_message(project_id, "req-2", "user", "new turn", session_number=s2)
    await append_message(project_id, "req-2", "assistant", "new reply", session_number=s2)

    assert s1 == 1 and s2 == 2
    history = await conversation_messages(project_id, last_session_only=True)
    assert [m["content"] for m in history] == ["new turn", "new reply"]

    all_history = await conversation_messages(project_id, last_session_only=False)
    assert [m["content"] for m in all_history] == [
        "old turn", "old reply", "new turn", "new reply",
    ]


@pytest.mark.asyncio
async def test_activity_events_filter_last_session(tmp_data_dir: Path) -> None:
    from syte.agent_activity import list_agent_events, record_agent_event
    from syte.database import init_db

    await init_db()
    project_id = "mark-session-proj"
    await record_agent_event(
        project_id, "request_started", detail="one",
        payload={"session": 1, "message_index": 1, "mark_status": "d", "mark_kind": "user"},
    )
    await record_agent_event(
        project_id, "tool_call_finished", detail="tool",
        payload={"session": 1, "message_index": 2, "mark_status": "d", "mark_kind": "tool"},
    )
    await record_agent_event(
        project_id, "request_started", detail="two",
        payload={"session": 2, "message_index": 1, "mark_status": "d", "mark_kind": "user"},
    )
    await record_agent_event(
        project_id, "thinking", detail="plan",
        payload={"session": 2, "message_index": 2, "mark_status": "g", "mark_kind": "plan"},
    )

    last = await list_agent_events(project_id, session="last")
    assert [e["payload"]["session"] for e in last] == [2, 2]
    assert last[1]["payload"]["mark_status"] == "g"

    only_first = await list_agent_events(project_id, session=1)
    assert [e["payload"]["session"] for e in only_first] == [1, 1]


@pytest.mark.asyncio
async def test_conversation_messages_drops_orphaned_leading_tool_message(tmp_data_dir: Path) -> None:
    from syte.cloud_agent_store import append_message, conversation_messages
    from syte.database import init_db

    project_id = "history-proj"
    await init_db()
    await append_message(project_id, "req", "user", "start")
    for i in range(45):
        await append_message(
            project_id, "req", "assistant", "",
            tool_calls=[{"id": f"call-{i}", "type": "function",
                         "function": {"name": "noop", "arguments": "{}"}}],
        )
        await append_message(project_id, "req", "tool", "result", tool_call_id=f"call-{i}")

    # A window boundary that would otherwise split a tool_calls/tool pair
    # must not leave a leading "tool" message without its assistant call,
    # since OpenAI-compatible providers (e.g. DeepSeek) reject that shape.
    history = await conversation_messages(project_id, limit=79, last_session_only=False)

    assert history[0]["role"] != "tool"


@pytest.mark.asyncio
async def test_conversation_messages_fills_incomplete_tool_calls(tmp_data_dir: Path) -> None:
    from syte.cloud_agent_store import append_message, conversation_messages
    from syte.database import init_db

    project_id = "incomplete-proj"
    await init_db()
    await append_message(project_id, "req", "user", "list files")
    await append_message(
        project_id,
        "req",
        "assistant",
        "",
        tool_calls=[{
            "id": "call-missing",
            "type": "function",
            "function": {"name": "list_files", "arguments": '{"path":"app/missing"}'},
        }],
    )
    # Simulate a crashed turn: assistant tool_calls persisted, tool result did not.
    await append_message(project_id, "req-2", "user", "try again")

    history = await conversation_messages(project_id, last_session_only=False)
    assert [m["role"] for m in history] == ["user", "assistant", "tool", "user"]
    assert history[2]["tool_call_id"] == "call-missing"
    assert "tool_result_missing" in history[2]["content"]


@pytest.mark.asyncio
async def test_execute_tool_returns_error_instead_of_raising(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.cloud_agent import _execute_tool
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "tool-err", "name": "Tool", "port": 3001, "start_command": ""})

    async def boom(*_args, **_kwargs):
        raise ValueError("Path not found")

    monkeypatch.setattr("syte.workspace_api.list_workspace_files", boom)
    result = await _execute_tool("tool-err", "list_files", {"path": "app/does-not-exist"})
    assert result["ok"] is False
    assert result["error"] == "tool_failed"
    assert "Path not found" in result["message"]


@pytest.mark.asyncio
async def test_provider_does_not_retry_http_400(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.cloud_agent import _provider_completion

    calls = 0

    class Response:
        status_code = 400
        reason_phrase = "Bad Request"
        text = '{"error":{"message":"Missing tool result"}}'
        request = type("Req", (), {"url": "https://api.deepseek.com/v1/chat/completions"})()

        def json(self):
            return {}

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
            return Response()

    monkeypatch.setattr("syte.cloud_agent.httpx.AsyncClient", Client)
    sleeps: list[float] = []

    async def fake_sleep(delay: float):
        sleeps.append(delay)

    monkeypatch.setattr("syte.cloud_agent.asyncio.sleep", fake_sleep)
    with pytest.raises(RuntimeError, match="400 Bad Request") as exc_info:
        await _provider_completion(
            {"model": "deepseek-chat", "api_key": "key", "api_base": "https://api.deepseek.com/v1"},
            [{"role": "user", "content": "hello"}],
        )
    assert calls == 1
    assert sleeps == []
    assert "Missing tool result" in str(exc_info.value)


@pytest.mark.asyncio
async def test_provider_disables_deepseek_thinking(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.cloud_agent import _provider_completion

    captured: dict = {}

    class Response:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    class Client:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return None
        async def post(self, url, headers=None, json=None):
            captured["json"] = json
            return Response()

    monkeypatch.setattr("syte.cloud_agent.httpx.AsyncClient", Client)
    await _provider_completion(
        {"model": "deepseek-chat", "api_key": "key", "api_base": "https://api.deepseek.com/v1"},
        [{"role": "user", "content": "hello"}],
    )
    assert captured["json"]["thinking"] == {"type": "disabled"}


@pytest.mark.asyncio
async def test_instruction_describes_preview_planning_and_homepage(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _build_syte_instruction

    project = await _project("instruction-proj")
    instruction = await _build_syte_instruction(project["id"])

    assert "update_plan" in instruction
    assert "delegate_task" in instruction
    assert "development preview" in instruction
    assert "Never deploy" in instruction
    assert "home page" in instruction


@pytest.mark.asyncio
async def test_update_plan_tool_returns_structured_plan(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _execute_tool

    await _project("plan-proj")
    result = await _execute_tool("plan-proj", "update_plan", {"steps": ["Inspect", "Verify"]})

    assert result == {"ok": True, "steps": ["Inspect", "Verify"], "note": ""}
