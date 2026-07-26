"""Chat session continuity, preview inspect logs, subagent partial, event prune."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_ensure_latest_session_reuses_counter(tmp_data_dir: Path) -> None:
    from syte.cloud_agent_store import (
        begin_turn_session,
        current_session_number,
        ensure_latest_session,
    )
    from syte.database import init_db

    await init_db()
    first = await begin_turn_session("proj-reuse", "syra-base")
    assert first == 1
    second = await ensure_latest_session("proj-reuse", "syra-base")
    third = await ensure_latest_session("proj-reuse", "syra-nano")
    assert second == 1
    assert third == 1
    assert await current_session_number("proj-reuse") == 1
    forced = await ensure_latest_session("proj-reuse", "syra-base", force_new=True)
    assert forced == 2


@pytest.mark.asyncio
async def test_preview_allowlist_accepts_domain_and_direct() -> None:
    from syte.preview_access import _is_allowed_url, _preview_allowlist

    urls = {
        "preview_url": "https://previewa-demo.sycord.site",
        "preview_domain_url": "https://previewa-demo.sycord.site",
        "preview_direct_url": "http://203.0.113.10:4010",
        "preview_fetch_url": "http://203.0.113.10:4010",
    }
    primary, extras = _preview_allowlist(urls, [])
    assert primary.startswith("http://203.0.113.10")
    assert any("sycord.site" in u for u in extras)
    assert _is_allowed_url(
        "https://previewa-demo.sycord.site/",
        primary,
        extras,
    )
    assert _is_allowed_url("http://203.0.113.10:4010/", primary, extras)
    assert not _is_allowed_url("http://evil.example/", primary, extras)


@pytest.mark.asyncio
async def test_inspect_preview_attaches_logs_on_failure(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.cloud_agent import _tool_inspect_preview
    from syte.database import create_project, init_db

    await init_db()
    await create_project(
        {"id": "insp-fail", "name": "insp", "port": 3098, "start_command": ""}
    )

    async def fake_access(project_id, action, **kwargs):
        if action == "status":
            return {
                "ok": True,
                "preview_url": "https://previewa-demo.sycord.site",
                "preview_fetch_url": "http://127.0.0.1:4011",
                "preview_direct_url": "http://127.0.0.1:4011",
            }
        if action == "fetch":
            return {
                "ok": False,
                "error": "url_not_allowed",
                "message": "URL not allowed — use preview URL or add it in Debug Chat access settings",
            }
        if action == "console":
            return {
                "ok": False,
                "error": "url_not_allowed",
                "message": "URL not allowed for console inspect",
            }
        if action == "logs":
            return {"ok": True, "logs": "ready - started server on 0.0.0.0:4011\nGET / 200"}
        return {"ok": False, "error": "unexpected"}

    monkeypatch.setattr("syte.preview_access.run_access_action", fake_access)
    result = await _tool_inspect_preview("insp-fail", {"route": "/"}, {})
    assert result["ok"] is False
    assert "preview_logs" in result
    assert "started server" in result["preview_logs"]


@pytest.mark.asyncio
async def test_subagent_step_limit_returns_partial(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import cloud_agent

    monkeypatch.setattr(cloud_agent, "MAX_SUBAGENT_RESEARCH_STEPS", 2)
    project_id = "sub-partial"
    calls = {"n": 0}

    async def fake_completion(model_arg, messages, **kwargs):
        calls["n"] += 1
        return {
            "role": "assistant",
            "content": f"Found navbar in app/components/nav.tsx (step {calls['n']})",
            "tool_calls": [
                {
                    "id": f"c{calls['n']}",
                    "type": "function",
                    "function": {
                        "name": "list_files",
                        "arguments": '{"path":"app"}',
                    },
                }
            ],
            "_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    async def fake_tool(*args, **kwargs):
        return {"ok": True, "files": ["app/page.tsx"]}

    monkeypatch.setattr(cloud_agent, "_provider_completion", fake_completion)
    monkeypatch.setattr(cloud_agent, "_execute_tool", fake_tool)
    monkeypatch.setattr(cloud_agent, "record_agent_event", AsyncMock())

    result = await cloud_agent._run_subagent_loop(
        project_id,
        "Find the navbar",
        {"provider": "x", "model": "y", "api_base": "", "api_key": "k", "profile": "syra-nano"},
        mode="research",
        task_id="sub-1",
    )
    assert result["ok"] is True
    assert result.get("partial") is True
    assert "navbar" in result["result"]
    assert result["error"] == "subagent_step_limit"


@pytest.mark.asyncio
async def test_cold_events_do_not_prune_every_write(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import agent_activity
    from syte.database import init_db

    await init_db()
    pruned: list[str] = []

    async def fake_prune(project_id: str) -> None:
        pruned.append(project_id)

    monkeypatch.setattr(agent_activity, "_prune_agent_events", fake_prune)
    monkeypatch.setattr(agent_activity, "_COLD_PRUNE_EVERY", 5)
    agent_activity._cold_event_counts.clear()

    for i in range(4):
        await agent_activity.record_agent_event(
            "prune-proj",
            "tool_call_finished",
            title=f"t{i}",
            detail="ok",
        )
    assert pruned == []
    await agent_activity.record_agent_event(
        "prune-proj",
        "tool_call_finished",
        title="t5",
        detail="ok",
    )
    assert pruned == ["prune-proj"]
