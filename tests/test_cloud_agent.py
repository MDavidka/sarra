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
async def test_ensure_migrates_legacy_agent_messages_without_session_number(
    tmp_data_dir: Path,
) -> None:
    """Existing DBs lack session_number; ensure must ALTER before INSERT/INDEX."""
    import aiosqlite

    from syte.cloud_agent_store import (
        _SCHEMA_EPOCH,
        _ensured_paths,
        append_message,
        begin_turn_session,
        ensure_cloud_agent_tables,
    )

    db_path = settings.resolved_db_path
    db_path.parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as db:
        await db.executescript(
            """
            CREATE TABLE agent_sessions (
                project_id TEXT PRIMARY KEY,
                model_profile TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE agent_messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                request_id TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                tool_call_id TEXT,
                tool_calls TEXT,
                reasoning_content TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE cloud_agent_requests (
                request_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                message TEXT NOT NULL,
                model_profile TEXT,
                source TEXT NOT NULL,
                auto_start INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                started_at TEXT,
                finished_at TEXT
            );
            """
        )
        await db.commit()

    # Simulate a long-lived process that already "ensured" the old schema.
    _ensured_paths[str(db_path)] = 1
    assert _ensured_paths[str(db_path)] != _SCHEMA_EPOCH

    await ensure_cloud_agent_tables()
    session = await begin_turn_session("legacy-proj", "syra-base")
    await append_message(
        "legacy-proj", "req-legacy", "user", "hello", session_number=session,
    )

    async with aiosqlite.connect(db_path) as db:
        async with db.execute("PRAGMA table_info(agent_messages)") as cur:
            cols = {row[1] for row in await cur.fetchall()}
        async with db.execute(
            "SELECT session_number, content FROM agent_messages WHERE project_id = ?",
            ("legacy-proj",),
        ) as cur:
            row = await cur.fetchone()

    assert "session_number" in cols
    assert row == (session, "hello")


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
async def test_communicate_writes_durable_turso_session(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Turso is configured, a turn's activity is mirrored to a durable session."""
    from syte import turso_store
    from syte.cloud_agent import _communicate_with_agent_impl

    project = await _project("turso-proj")
    turso_db = tmp_data_dir / "turso-local.db"

    async def fake_turso_settings():
        return f"file:{turso_db}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_turso_settings)
    turso_store.reset_client_cache()

    async def fake_provider(*_args, **_kwargs):
        return {"role": "assistant", "content": "Done."}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)

    result = await _communicate_with_agent_impl(project["id"], "hello", request_id="req-turso")

    assert result["ok"] is True
    assert result["turso_session_id"]
    session = await turso_store.get_session(result["turso_session_id"])
    assert session is not None
    assert session["status"] == "completed"
    event_types = [e["event_type"] for e in session["events"]]
    assert "request_started" in event_types
    assert "request_completed" in event_types
    turso_store.reset_client_cache()


@pytest.mark.asyncio
async def test_communicate_without_turso_configured_still_succeeds(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Turso is optional — a turn completes normally when it is not configured."""
    from syte.cloud_agent import _communicate_with_agent_impl

    project = await _project("no-turso-proj")

    async def fake_provider(*_args, **_kwargs):
        return {"role": "assistant", "content": "Done."}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)

    result = await _communicate_with_agent_impl(project["id"], "hello", request_id="req-no-turso")

    assert result["ok"] is True
    assert result["turso_session_id"] is None


@pytest.mark.asyncio
async def test_communicate_persists_every_message_to_turso(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every user/assistant/tool message is mirrored to the shared Turso
    ``agent_message`` table in real time, and the aggregate sync status
    (the GUI's green/red 'brain' indicator) reports all_saved=True."""
    from syte import turso_store
    from syte.cloud_agent import _communicate_with_agent_impl, turso_message_sync_status

    project = await _project("turso-msgs-proj")
    turso_db = tmp_data_dir / "turso-local-msgs.db"

    async def fake_turso_settings():
        return f"file:{turso_db}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_turso_settings)
    turso_store.reset_client_cache()

    replies = iter([
        {"role": "assistant", "content": "", "tool_calls": [{
            "id": "call-1", "type": "function",
            "function": {"name": "read_file", "arguments": '{"path":"app/README.md"}'},
        }]},
        {"role": "assistant", "content": "Done."},
    ])

    async def fake_provider(*_args, **_kwargs):
        return next(replies)

    async def fake_tool(*_args, **_kwargs):
        return {"ok": True, "content": "hello"}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)
    monkeypatch.setattr("syte.cloud_agent._execute_tool", fake_tool)

    result = await _communicate_with_agent_impl(project["id"], "inspect", request_id="req-turso-msgs")
    assert result["ok"] is True
    session_id = result["turso_session_id"]
    assert session_id

    messages = await turso_store.list_messages(session_id)
    assert [m["role"] for m in messages] == ["user", "assistant", "tool", "assistant"]
    assert await turso_store.count_messages(session_id) == 4

    sync = await turso_message_sync_status(project["id"])
    assert sync["turso_configured"] is True
    assert sync["total_messages"] == 4
    assert sync["synced_messages"] == 4
    assert sync["all_saved"] is True
    turso_store.reset_client_cache()


@pytest.mark.asyncio
async def test_turso_sync_status_without_turso_reports_all_saved(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When Turso is not configured, the brain indicator stays green (there
    is nothing unsaved to report) rather than falsely alarming red."""
    from syte.cloud_agent import _communicate_with_agent_impl, turso_message_sync_status

    project = await _project("no-turso-msgs-proj")

    async def fake_provider(*_args, **_kwargs):
        return {"role": "assistant", "content": "Done."}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)
    result = await _communicate_with_agent_impl(project["id"], "hello", request_id="req-no-turso-msgs")
    assert result["ok"] is True
    assert result["turso_session_id"] is None

    sync = await turso_message_sync_status(project["id"])
    assert sync["turso_configured"] is False
    assert sync["all_saved"] is True


@pytest.mark.asyncio
async def test_instruction_describes_preview_planning_and_homepage(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _build_syte_instruction

    project = await _project("instruction-proj")
    instruction = await _build_syte_instruction(project["id"])

    assert "update_plan" in instruction
    assert "delegate_task" in instruction
    assert "isolated preview" in instruction or "development preview" in instruction
    assert "Never deploy" in instruction
    assert "shadcn" in instruction.lower()
    assert "ANY kind of code" in instruction


@pytest.mark.asyncio
async def test_update_plan_tool_returns_structured_plan(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _execute_tool

    await _project("plan-proj")
    result = await _execute_tool("plan-proj", "update_plan", {"steps": ["Inspect", "Verify"]})

    assert result["ok"] is True
    assert result["steps"] == ["Inspect", "Verify"]
    assert result["note"] == ""
    assert result.get("plan_id")
