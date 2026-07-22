"""Regression coverage for Sarra Linear audit fixes (DAV-179…DAV-188)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


async def _project(project_id: str) -> dict:
    from syte.database import create_project, get_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({"id": project_id, "name": project_id, "port": 3000, "start_command": ""})
    return (await get_project(project_id)) or {}


@pytest.mark.asyncio
async def test_interrupt_clears_busy_status_immediately(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _active_turns, get_agent_status, interrupt_agent, start_agent

    project = await _project("busy-proj")
    ok, _, _ = await start_agent(project["id"])
    assert ok is True

    async def hang():
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    _active_turns[project["id"]] = task
    status = await get_agent_status(project["id"], check_backend=False)
    assert status["agent_busy"] is True
    assert status["agent_status"] == "processing"

    interrupted, message = await interrupt_agent(project["id"])
    assert interrupted is True
    assert "interrupted" in message.lower()
    status_after = await get_agent_status(project["id"], check_backend=False)
    assert status_after["agent_busy"] is False
    assert status_after["agent_status"] == "running"
    assert status_after["agent_running"] is True
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_project_brief_mtime_cache(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import _read_project_brief, invalidate_instruction_cache
    from syte.workspace import ensure_workspace, workspace_path

    project = await _project("brief-proj")
    ensure_workspace(project["id"])
    brief_dir = workspace_path(project["id"]) / ".syte"
    brief_dir.mkdir(parents=True, exist_ok=True)
    path = brief_dir / "PROJECT_BRIEF.md"
    path.write_text("v1 brief\n", encoding="utf-8")
    invalidate_instruction_cache(project["id"])

    first = _read_project_brief(project["id"])
    assert "v1 brief" in first
    second = _read_project_brief(project["id"])
    assert second == first

    path.write_text("v2 brief changed\n", encoding="utf-8")
    third = _read_project_brief(project["id"])
    assert "v2 brief" in third


@pytest.mark.asyncio
async def test_sse_backlog_uses_smaller_limit_with_since_id(tmp_data_dir: Path) -> None:
    from syte.agent_activity import activity_sse_generator, record_agent_event
    from syte.database import init_db

    await init_db()
    first = await record_agent_event(
        "proj-sse-limit",
        "token_delta",
        detail="a",
        payload={"session": 1, "delta": "a"},
    )
    await record_agent_event(
        "proj-sse-limit",
        "token_delta",
        detail="b",
        payload={"session": 1, "delta": "b"},
    )

    with patch("syte.agent_activity.list_agent_events", new_callable=AsyncMock) as listed:
        listed.return_value = []
        agen = activity_sse_generator(
            "proj-sse-limit",
            since_id=int(first["id"]),
            session="last",
            heartbeat_seconds=0.01,
        )
        try:
            await agen.__anext__()
        except StopAsyncIteration:
            pass
        finally:
            await agen.aclose()
        listed.assert_awaited()
        kwargs = listed.await_args.kwargs
        assert kwargs["since_id"] == int(first["id"])
        assert kwargs["limit"] == 100


@pytest.mark.asyncio
async def test_planner_timeout_default_raised() -> None:
    from syte.site_planner import PLANNER_TIMEOUT_S, fallback_site_plan

    assert PLANNER_TIMEOUT_S >= 8.0
    plan = fallback_site_plan("Build a full website with landing page with pricing and docs")
    assert any(
        "pricing" in str(item.get("task") or "").lower()
        or "landing" in str(item.get("task") or "").lower()
        for item in plan
    )


@pytest.mark.asyncio
async def test_thinking_params_mark_unsupported_models() -> None:
    from syte.thinking_levels import build_model_thinking_params

    params = build_model_thinking_params(
        {"thinking_enabled": True, "thinking_budget_tokens": 2048, "reasoning_effort": "high"},
        provider="openai",
        model="gpt-4o-mini",
        api_base="https://api.openai.com/v1",
    )
    assert params["thinking_requested"] is True
    assert params["thinking_supported"] is False
    assert params["thinking_applied"] is False
    assert "thinking" not in params
    assert "reasoning_effort" not in params


@pytest.mark.asyncio
async def test_mcp_boot_rejects_broken_addon(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_artifacts import connect_mcp_addon, list_mcp_addons, register_mcp_addon

    async def boom(**_kwargs):
        return {"ok": False, "error": "boot_failed", "message": "segfault", "tools": []}

    monkeypatch.setattr("syte.agent_artifacts.discover_mcp_stdio_tools", boom)
    project = await _project("mcp-boot-proj")
    registered = await register_mcp_addon(
        project["id"],
        name="broken",
        command="false",
        args=[],
    )
    connected = await connect_mcp_addon(project["id"], registered["id"])
    assert connected["ok"] is False
    assert connected["status"] == "error"
    listed = await list_mcp_addons(project["id"])
    custom = next(a for a in listed if a["id"] == registered["id"])
    assert custom["status"] == "error"
