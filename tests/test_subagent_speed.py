"""Subagent routing, await lifecycle, and concurrency fixes."""

from __future__ import annotations

import asyncio
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


async def _project(name: str) -> dict[str, Any]:
    from syte.database import create_project, get_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_nano_api_key", "go-key")
    await set_setting("agent_syra_nano_api_key", "AQ.nano-test-key")
    await create_project({"id": f"sub-{name}", "name": name, "port": 3099, "start_command": ""})
    return (await get_project(f"sub-{name}")) or {}


@pytest.mark.asyncio
async def test_subagent_wall_clock_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import cloud_agent

    monkeypatch.setattr(cloud_agent, "SUBAGENT_TIMEOUT_S", 0.05)
    monkeypatch.setattr(cloud_agent, "SUBAGENT_RESEARCH_TIMEOUT_S", 0.05)

    async def hang(*args, **kwargs):
        await asyncio.sleep(10)
        return {"ok": True}

    monkeypatch.setattr(cloud_agent, "_run_subagent_loop", hang)
    result = await cloud_agent._run_subagent(
        "proj",
        "do stuff",
        {"provider": "x", "model": "y", "api_base": "", "api_key": "k", "profile": "syra-nano"},
        mode="implementation",
    )
    assert result["ok"] is False
    assert result["error"] == "subagent_timeout"
    assert result["retryable"] is True


@pytest.mark.asyncio
async def test_cancel_background_subagents_stores_result(tmp_data_dir: Path) -> None:
    from syte.cloud_agent import (
        _background_subagent_results,
        _background_subagents,
        cancel_background_subagents,
    )

    project = await _project("bg-cancel")

    async def hang():
        await asyncio.Event().wait()

    task = asyncio.create_task(hang())
    key = f"{project['id']}:bg-test"
    _background_subagents[key] = task

    assert cancel_background_subagents(project["id"]) == 1
    assert key not in _background_subagents
    stored = _background_subagent_results.get(key)
    assert stored is not None
    assert stored["error"] == "subagent_cancelled"
    await asyncio.sleep(0)
    assert task.cancelled() or task.done()
    assert cancel_background_subagents(project["id"]) == 0


@pytest.mark.asyncio
async def test_background_subagent_result_is_awaitable(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import cloud_agent

    project = await _project("await-bg")
    parent = {
        "provider": "x",
        "model": "y",
        "api_base": "",
        "api_key": "parent-key",
        "profile": "syra-havy",
    }

    async def fake_resolve(parent_model, task, *, mode=None):
        return (
            {**parent_model, "profile": "syra-nano", "api_key": "nano-key"},
            {"mode": "research", "effective_profile": "syra-nano", "reason": "test"},
        )

    async def fake_run(project_id, task, model, **kwargs):
        await asyncio.sleep(0.05)
        return {
            "ok": True,
            "task": task,
            "result": "found navbar at app/components/nav.tsx",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "steps": 1},
        }

    monkeypatch.setattr(cloud_agent, "_resolve_subagent_model", fake_resolve)
    monkeypatch.setattr(cloud_agent, "_run_subagent", fake_run)
    monkeypatch.setattr(cloud_agent, "record_agent_event", AsyncMock())

    started = await cloud_agent._tool_delegate_task(
        project["id"],
        {"task": "Find the navbar", "background": True, "mode": "research"},
        model=parent,
        context={"request_id": "req-1"},
    )
    assert started["ok"] is True
    assert started["status"] == "running"
    task_id = started["task_id"]

    collected = await cloud_agent._tool_await_subagent(
        project["id"], {"task_id": task_id, "timeout_s": 5}
    )
    assert collected["ok"] is True
    assert collected["awaited"] is True
    assert "navbar" in collected["result"]


@pytest.mark.asyncio
async def test_background_subagent_concurrency_cap(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import cloud_agent

    project = await _project("cap-bg")
    parent = {
        "provider": "x",
        "model": "y",
        "api_base": "",
        "api_key": "k",
        "profile": "syra-nano",
    }

    async def fake_resolve(parent_model, task, *, mode=None):
        return parent_model, {"mode": "research", "effective_profile": "syra-nano", "reason": "t"}

    async def hang_run(*args, **kwargs):
        await asyncio.Event().wait()
        return {"ok": True, "result": "never"}

    monkeypatch.setattr(cloud_agent, "_resolve_subagent_model", fake_resolve)
    monkeypatch.setattr(cloud_agent, "_run_subagent", hang_run)
    monkeypatch.setattr(cloud_agent, "record_agent_event", AsyncMock())
    monkeypatch.setattr(cloud_agent, "MAX_BACKGROUND_SUBAGENTS_PER_PROJECT", 1)

    first = await cloud_agent._tool_delegate_task(
        project["id"],
        {"task": "scan one", "background": True},
        model=parent,
    )
    assert first["ok"] is True

    second = await cloud_agent._tool_delegate_task(
        project["id"],
        {"task": "scan two", "background": True},
        model=parent,
    )
    assert second["ok"] is False
    assert second["error"] == "subagent_queue_full"

    cloud_agent.cancel_background_subagents(project["id"])


@pytest.mark.asyncio
async def test_research_mode_blocks_writes(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import cloud_agent

    project = await _project("readonly")
    model = {
        "provider": "x",
        "model": "y",
        "api_base": "",
        "api_key": "k",
        "profile": "syra-nano",
    }

    async def fake_completion(model_arg, messages, **kwargs):
        if not any(m.get("role") == "tool" for m in messages):
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "type": "function",
                        "function": {
                            "name": "write_file",
                            "arguments": '{"path":"app/x.ts","content":"nope"}',
                        },
                    }
                ],
                "_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            }
        tool_msg = next(m for m in messages if m.get("role") == "tool")
        assert "research_readonly" in tool_msg["content"]
        return {
            "role": "assistant",
            "content": "Blocked write as expected.",
            "_usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    monkeypatch.setattr(cloud_agent, "_provider_completion", fake_completion)
    result = await cloud_agent._run_subagent_loop(
        project["id"], "try to write", model, mode="research"
    )
    assert result["ok"] is True
    assert "Blocked write" in result["result"]
    assert result["usage"]["steps"] >= 1


@pytest.mark.asyncio
async def test_subagent_uses_routed_cheaper_model(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte import cloud_agent

    parent = {
        "provider": "x",
        "model": "pro",
        "api_base": "",
        "api_key": "parent",
        "profile": "syra-havy",
    }
    seen: dict[str, Any] = {}

    async def fake_meta(profile):
        key = "other-key"
        return {
            "provider": "x",
            "model": profile,
            "api_base": "",
            "api_key": key,
            "profile": profile,
        }

    async def fake_run(project_id, task, model, **kwargs):
        seen["model"] = model
        seen["mode"] = kwargs.get("mode")
        return {"ok": True, "result": "ok", "task": task}

    monkeypatch.setattr(cloud_agent, "model_metadata_for_profile", fake_meta)
    monkeypatch.setattr(cloud_agent, "_run_subagent", fake_run)
    monkeypatch.setattr(cloud_agent, "record_agent_event", AsyncMock())

    result = await cloud_agent._tool_delegate_task(
        "proj",
        {"task": "Find auth middleware", "mode": "research"},
        model=parent,
    )
    assert result["ok"] is True
    assert seen["model"]["profile"] == "syra-nano"
    assert seen["mode"] == "research"
    assert result["profile"] == "syra-nano"


@pytest.mark.asyncio
async def test_update_plan_assigns_main_and_subagent(
    tmp_data_dir: Path,
) -> None:
    from syte.cloud_agent import _tool_update_plan

    project = await _project("plan-assign")
    result = await _tool_update_plan(
        project["id"],
        {
            "steps": ["Find navbar component", "Rewrite hero copy"],
            "assignees": ["subagent", "main"],
            "note": "split work",
        },
        {"request_id": "req-plan", "session_number": 1},
    )
    assert result["ok"] is True
    assert result["assignments"][0]["assignee"] == "subagent"
    assert result["assignments"][1]["assignee"] == "main"
    assert result["steps"][0].startswith("[subagent]")
    assert "delegate_task" in result["guidance"]


@pytest.mark.asyncio
async def test_communicate_auto_profile_alias(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import cloud_agent

    captured: dict[str, Any] = {}

    async def fake_impl(project_id, message, **kwargs):
        captured.update(kwargs)
        return {"ok": True, "reply": "done"}

    monkeypatch.setattr(cloud_agent, "_communicate_with_agent_impl", fake_impl)
    result = await cloud_agent.communicate_with_agent(
        "p1",
        "change button text to Go",
        model_profile="auto",
        background=False,
    )
    assert result["ok"] is True
    assert result["model_routing"]["auto_applied"] is True
    assert captured["model_profile"] == "syra-nano"
