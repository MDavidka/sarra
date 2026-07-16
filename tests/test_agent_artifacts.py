"""Tests for agent artifacts: plans, screenshots, questions, MCP, stops."""

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


async def _project(project_id: str = "art-proj") -> dict:
    from syte.database import create_project, get_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({"id": project_id, "name": "Art", "port": 3000, "start_command": ""})
    return (await get_project(project_id)) or {}


@pytest.mark.asyncio
async def test_save_and_list_plans(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import list_plans, save_plan

    project = await _project()
    plan = await save_plan(
        project["id"],
        ["Inspect", "Edit", "Verify"],
        note="first pass",
        request_id="req_1",
        session_number=1,
    )
    assert plan["id"] > 0
    plans = await list_plans(project["id"])
    assert len(plans) == 1
    assert plans[0]["steps"] == ["Inspect", "Edit", "Verify"]
    assert plans[0]["note"] == "first pass"


@pytest.mark.asyncio
async def test_screenshot_record_and_bytes(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import get_screenshot, list_screenshots, read_screenshot_bytes, save_screenshot_record
    from syte.workspace import ensure_workspace

    project = await _project("shot-proj")
    ensure_workspace(project["id"])
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    record = await save_screenshot_record(
        project["id"],
        viewport="desktop",
        width=1280,
        height=800,
        png_bytes=png,
        route="/",
        url="http://127.0.0.1:3000/",
        request_id="req_shot",
        session_number=2,
        thumb_bytes=png,
    )
    assert record["id"].startswith("shot_")
    listed = await list_screenshots(project["id"])
    assert listed[0]["viewport"] == "desktop"
    assert "image_url" in listed[0]
    loaded = await get_screenshot(project["id"], record["id"])
    assert loaded is not None
    assert read_screenshot_bytes(loaded) == png
    assert read_screenshot_bytes(loaded, variant="thumb") == png


@pytest.mark.asyncio
async def test_question_answer_unblocks_waiter(tmp_data_dir: Path) -> None:
    import asyncio

    from syte.agent_artifacts import answer_question, create_question, wait_for_answer

    project = await _project("q-proj")
    question = await create_question(
        project["id"],
        "Pick a color",
        "choice",
        options=["red", "blue"],
        request_id="req_q",
        session_number=1,
    )
    task = asyncio.create_task(wait_for_answer(question["id"], timeout_s=5))
    await asyncio.sleep(0.05)
    result = await answer_question(project["id"], question["id"], "blue")
    assert result["ok"] is True
    assert await task == "blue"


@pytest.mark.asyncio
async def test_mcp_builtin_connect_and_call(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_artifacts import call_mcp_addon, connect_mcp_addon, list_mcp_addons

    project = await _project("mcp-proj")

    async def fake_service(*_args, **_kwargs):
        return {"ok": True, "action": "status", "preview_running": False}

    monkeypatch.setattr("syte.agent_service.run_service_action", fake_service)
    addons = await list_mcp_addons(project["id"])
    assert any(a["name"] == "syte" for a in addons)
    connected = await connect_mcp_addon(project["id"], "syte")
    assert connected["ok"] is True
    assert any(t["name"] == "syte_service" for t in connected["tools"])
    called = await call_mcp_addon(project["id"], "syte", "syte_service", {"action": "status"})
    assert called["ok"] is True


@pytest.mark.asyncio
async def test_mark_session_stopped(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import list_session_stops, mark_session_stopped

    project = await _project("stop-proj")
    stop = await mark_session_stopped(
        project["id"],
        reason="stopped",
        source="test",
        session_number=3,
        turso_session_id="abc123",
    )
    assert stop["stopped_at"]
    stops = await list_session_stops(project["id"])
    assert stops[0]["reason"] == "stopped"
    assert stops[0]["turso_session_id"] == "abc123"


@pytest.mark.asyncio
async def test_update_plan_tool_persists(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import list_plans
    from syte.cloud_agent import _execute_tool

    project = await _project("plan-tool")
    result = await _execute_tool(
        project["id"],
        "update_plan",
        {"steps": ["A", "B"], "note": "n"},
        context={"request_id": "r1", "session_number": 1},
    )
    assert result["ok"] is True
    assert result["plan_id"]
    plans = await list_plans(project["id"])
    assert plans[0]["steps"] == ["A", "B"]


@pytest.mark.asyncio
async def test_env_tools_round_trip(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _execute_tool
    from syte.workspace import ensure_workspace

    project = await _project("env-tool")
    ensure_workspace(project["id"])
    set_result = await _execute_tool(
        project["id"],
        "env_set",
        {"env_vars": {"FOO": "bar"}, "merge": True},
        context={},
    )
    assert set_result["ok"] is True
    keys_only = await _execute_tool(project["id"], "env_get", {}, context={})
    assert "FOO" in keys_only["keys"]
    values = await _execute_tool(project["id"], "env_get", {"keys": ["FOO"]}, context={})
    assert values["env"]["FOO"] == "bar"


@pytest.mark.asyncio
async def test_stop_agent_marks_db(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import list_session_stops
    from syte.agent_activity import list_agent_events
    from syte.cloud_agent import start_agent, stop_agent

    project = await _project("stop-agent")
    ok, _, _ = await start_agent(project["id"])
    assert ok is True
    ok, message = await stop_agent(project["id"])
    assert ok is True
    assert "stopped" in message.lower()
    stops = await list_session_stops(project["id"])
    assert stops and stops[0]["reason"] == "stopped"
    events = await list_agent_events(project["id"])
    assert any(e["event_type"] == "agent_stopped" and e["payload"].get("stopped_at") for e in events)


@pytest.mark.asyncio
async def test_instruction_mentions_any_code_and_shadcn(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _build_syte_instruction

    project = await _project("instr")
    text = await _build_syte_instruction(project["id"])
    assert "ANY kind of code" in text
    assert "shadcn" in text.lower()
    assert "ask_question" in text
    assert "screenshot_preview" in text


@pytest.mark.asyncio
async def test_tools_include_new_capabilities() -> None:
    from syte.cloud_agent import TOOLS

    names = {t["function"]["name"] for t in TOOLS}
    for required in {
        "screenshot_preview",
        "ask_question",
        "env_get",
        "env_set",
        "request_env",
        "list_mcp_addons",
        "connect_mcp",
        "call_mcp",
        "update_plan",
    }:
        assert required in names
