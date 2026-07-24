"""Regression tests for Sarra DAV-192 child audit fixes (DAV-193…205)."""

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


def test_shell_path_violation_blocks_host_escapes(tmp_path, monkeypatch) -> None:
    from syte import config as config_mod
    from syte.workspace_api import _shell_path_violation

    monkeypatch.setattr(config_mod.settings, "workspaces_dir", tmp_path)
    (tmp_path / "proj" / "app").mkdir(parents=True)

    assert _shell_path_violation("proj", "cat /etc/passwd") is not None
    assert _shell_path_violation("proj", "ls ../../") is not None
    assert _shell_path_violation("proj", "ls /") is not None
    assert _shell_path_violation("proj", "python3 -c 'print(1)'") is not None
    assert _shell_path_violation("proj", "npm run lint") is None
    assert _shell_path_violation("proj", "cat package.json") is None
    # ../ from app/ stays inside the project workspace root — allowed.
    assert _shell_path_violation("proj", "ls ../") is None


@pytest.mark.asyncio
async def test_execute_command_agent_source_blocks_etc_passwd(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import workspace_api
    from syte.workspace import ensure_workspace

    project = await _project("shell-bound")
    ensure_workspace(project["id"])

    code, output = await workspace_api.execute_command(
        project["id"], "cat /etc/passwd", source="agent"
    )
    assert code == 1
    assert "workspace boundary" in output


@pytest.mark.asyncio
async def test_run_command_timeout_returns_structured_error(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.cloud_agent import _execute_tool
    from syte.workspace import ensure_workspace

    project = await _project("tool-timeout")
    ensure_workspace(project["id"])

    async def fake_execute_command(*args, **kwargs):
        return 124, "Command timed out after 1s"

    monkeypatch.setattr(
        "syte.workspace_api.execute_command", fake_execute_command
    )
    result = await _execute_tool(
        project["id"],
        "run_command",
        {"command": "sleep 999", "timeout": 1},
    )
    assert result["ok"] is False
    assert result["error"] == "timeout"
    assert result["retryable"] is True
    assert result["exit_code"] == 124


@pytest.mark.asyncio
async def test_subagent_wall_clock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import cloud_agent

    monkeypatch.setattr(cloud_agent, "SUBAGENT_TIMEOUT_S", 0.05)

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)
        return {"ok": True}

    monkeypatch.setattr(cloud_agent, "_run_subagent_loop", hang)
    result = await cloud_agent._run_subagent(
        "proj", "do stuff", {"provider": "x", "model": "y", "api_base": "", "api_key": "k"}
    )
    assert result["ok"] is False
    assert result["error"] == "subagent_timeout"
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_interrupt_cancels_background_subagents(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import (
        _background_subagents,
        cancel_background_subagents,
        interrupt_agent,
        start_agent,
    )

    project = await _project("bg-cancel")
    ok, _, _ = await start_agent(project["id"])
    assert ok is True

    async def hang():
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    key = f"{project['id']}:bg-test"
    _background_subagents[key] = task

    interrupted, _ = await interrupt_agent(project["id"])
    assert interrupted is True
    assert key not in _background_subagents
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    # cancel helper is idempotent
    assert cancel_background_subagents(project["id"]) == 0


@pytest.mark.asyncio
async def test_test_agent_is_isolated_probe(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.agent_activity import list_agent_events
    from syte.cloud_agent import test_agent
    from syte.cloud_agent_store import conversation_messages

    project = await _project("test-iso")

    async def fake_completion(model, messages, **kwargs):
        assert kwargs.get("tools") is None
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_completion)
    communicate = AsyncMock()
    monkeypatch.setattr("syte.cloud_agent.communicate_with_agent", communicate)

    result = await test_agent(project["id"])
    assert result["ok"] is True
    assert result.get("isolated") is True
    communicate.assert_not_called()

    events = await list_agent_events(project["id"], since_id=0, limit=50)
    assert events == []
    messages = await conversation_messages(project["id"])
    assert messages == []


def test_validate_mcp_tool_schema_rejects_bad_types() -> None:
    from syte.agent_artifacts import validate_mcp_tool_schema

    assert validate_mcp_tool_schema({"name": "good", "inputSchema": {"type": "object"}}) is None
    assert validate_mcp_tool_schema({"name": "bad", "inputSchema": {"type": "string"}}) is not None
    assert validate_mcp_tool_schema({
        "name": "bad-prop",
        "inputSchema": {
            "type": "object",
            "properties": {"x": "nope"},
        },
    }) is not None
    assert validate_mcp_tool_schema({
        "name": "ok-prop",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "string", "description": "x"}},
            "required": ["x"],
        },
    }) is None


@pytest.mark.asyncio
async def test_inspect_preview_tool_fetches_allowlisted_url(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.cloud_agent import _execute_tool

    project = await _project("inspect-prev")

    async def fake_access(project_id, action, **kwargs):
        if action == "status":
            return {"ok": True, "preview_url": "http://127.0.0.1:4001"}
        if action == "fetch":
            return {
                "ok": True,
                "url": kwargs.get("url"),
                "status_code": 200,
                "content_type": "text/html",
                "content": "<html>login</html>",
            }
        if action == "console":
            return {
                "ok": True,
                "load_ok": True,
                "title": "Login",
                "ready_state": "complete",
                "console_logs": [],
                "page_errors": [],
                "network_failures": [],
                "console_error_count": 0,
                "page_error_count": 0,
                "message": "Preview loaded (complete)",
            }
        return {"ok": False, "error": "unexpected"}

    monkeypatch.setattr("syte.preview_access.run_access_action", fake_access)
    result = await _execute_tool(
        project["id"],
        "inspect_preview",
        {"route": "/login"},
    )
    assert result["ok"] is True
    assert result["action"] == "inspect_preview"
    assert "login" in str(result.get("content") or "")
    assert result.get("load_ok") is True
    assert result.get("console_error_count") == 0


@pytest.mark.asyncio
async def test_workspace_lookup_uses_ttl_cache(tmp_data_dir: Path) -> None:
    from syte.agent_memory import (
        invalidate_workspace_lookup_cache,
        lookup_workspace_paths,
        upsert_workspace_file,
    )
    from syte.database import init_db

    await init_db()
    await upsert_workspace_file("cache-proj", "app/page.tsx", content=" cons t x = 1")
    invalidate_workspace_lookup_cache("cache-proj")

    first = await lookup_workspace_paths("cache-proj", query="page", limit=10)
    assert first
    with patch("syte.agent_memory.aiosqlite.connect") as connect:
        second = await lookup_workspace_paths("cache-proj", query="page", limit=10)
        connect.assert_not_called()
    assert second == first
