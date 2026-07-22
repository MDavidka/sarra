"""Tests for agent AI quality improvements (Linear audit DAV-128+)."""

from __future__ import annotations

import json

import pytest

from syte.cloud_agent import (
    _system_message_for_provider,
    _truncate_for_llm,
    _truncate_tool_payload,
)
from syte.thinking_levels import apply_prompt_cache_markers


def test_truncate_tool_payload_caps_large_json() -> None:
    huge = {"ok": True, "content": "x" * 80_000}
    encoded = _truncate_tool_payload(huge, max_chars=8_000)
    assert len(encoded) <= 8_000 + 50
    assert "truncated" in encoded.lower()


def test_truncate_for_llm_short_passthrough() -> None:
    assert _truncate_for_llm("hello") == "hello"


def test_system_message_splits_cache_for_anthropic() -> None:
    msg = _system_message_for_provider(
        "STATIC RULES",
        "DYNAMIC MEMORY",
        {"provider": "anthropic", "model": "claude-3-5"},
    )
    assert msg["role"] == "system"
    assert isinstance(msg["content"], list)
    assert msg["content"][0]["cache_control"] == {"type": "ephemeral"}
    assert msg["content"][0]["text"] == "STATIC RULES"
    assert msg["content"][1]["text"] == "DYNAMIC MEMORY"

    plain = _system_message_for_provider(
        "STATIC", "DYNAMIC", {"provider": "deepseek", "model": "deepseek-chat"},
    )
    assert plain["content"] == "STATIC\n\nDYNAMIC"


def test_apply_prompt_cache_markers_preserves_split_blocks() -> None:
    messages = [
        {
            "role": "system",
            "content": [
                {"type": "text", "text": "static", "cache_control": {"type": "ephemeral"}},
                {"type": "text", "text": "dynamic"},
            ],
        },
        {"role": "user", "content": "hi"},
    ]
    out = apply_prompt_cache_markers(messages, provider="anthropic", model="claude")
    assert out[0]["content"][0]["cache_control"]["type"] == "ephemeral"
    assert out[0]["content"][1]["text"] == "dynamic"


def test_truncate_tool_payload_list_files_style() -> None:
    files = [{"name": f"f{i}", "path": f"app/{i}"} for i in range(500)]
    encoded = _truncate_tool_payload({"ok": True, "files": files}, max_chars=16_000)
    data = json.loads(encoded) if encoded.startswith("{") else None
    assert data is None or data.get("truncated") is True or len(encoded) <= 16_000


def test_instant_thinking_omits_provider_reasoning_keys() -> None:
    from syte.thinking_levels import build_model_thinking_params, resolve_thinking_config

    cfg = resolve_thinking_config(1)
    params = build_model_thinking_params(
        cfg,
        provider="openai",
        model="deepseek-chat",
        api_base="https://api.deepseek.com/v1",
    )
    assert "thinking" not in params
    assert "reasoning_effort" not in params
    assert params["cache_prompt"] is True
    assert params["temperature"] == 0.1


def test_memory_context_block_ranks_mru() -> None:
    from syte.agent_memory import memory_context_block

    block = memory_context_block(
        {
            "summary_text": "Built the hero section",
            "key_decisions": ["Use navy accents"],
            "technical_state": "Next.js app router",
        },
        ["app/old.tsx", "app/mid.tsx", "app/new.tsx"],
    )
    assert "app/new.tsx" in block
    assert block.index("app/new.tsx") < block.index("app/old.tsx")
    assert "ranked, newest first" in block


def test_validate_builtin_mcp_arguments_rejects_bad_shapes() -> None:
    from syte.agent_artifacts import validate_builtin_mcp_arguments

    err = validate_builtin_mcp_arguments("syte_service", {"action": "status", "extra": 1})
    assert err and err["error"] == "invalid_arguments"

    err = validate_builtin_mcp_arguments("web_search", {})
    assert err and err["error"] == "invalid_arguments"

    assert validate_builtin_mcp_arguments("web_search", {"query": "next.js spacing"}) is None
    assert validate_builtin_mcp_arguments("syte_service", {"action": "preview_start"}) is None


@pytest.mark.asyncio
async def test_plan_gate_rejects_tools_until_update_plan() -> None:
    from syte.cloud_agent import _execute_tool

    ctx = {"mandatory_plan": True, "plan_submitted": False}
    blocked = await _execute_tool(
        "proj", "list_files", {"path": "app"}, context=ctx,
    )
    assert blocked["ok"] is False
    assert blocked["error"] == "plan_required"

    # ask_question remains allowed during planning.
    # (full ask_question path needs more context; gate itself must not block the name)
    # update_plan clears the gate via ctx mutation — simulate after a successful plan.
    ctx["plan_submitted"] = True
    # Without a real workspace this may fail path resolution, but must not be plan_required.
    result = await _execute_tool(
        "proj", "list_files", {"path": "app"}, context=ctx,
    )
    assert result.get("error") != "plan_required"


@pytest.mark.asyncio
async def test_website_plan_gate_allows_question_but_blocks_inspection() -> None:
    from syte.cloud_agent import _execute_tool

    ctx = {
        "mandatory_plan": True,
        "plan_submitted": False,
        "plan_gate_reason": "website",
        "question_required": True,
        "question_answered": False,
    }
    blocked = await _execute_tool("proj", "list_files", {"path": "app"}, context=ctx)
    assert blocked["error"] == "question_required"

    # Planning is also blocked until the question has been answered.
    plan = await _execute_tool(
        "proj",
        "update_plan",
        {"steps": ["Plan the site"]},
        context=ctx,
    )
    assert plan.get("error") == "question_required"

    # Once ask_question has returned successfully, update_plan becomes the
    # required next phase. Simulate the answer without entering the blocking UI path.
    ctx["question_answered"] = True
    empty_plan = await _execute_tool(
        "proj",
        "update_plan",
        {"steps": []},
        context=ctx,
    )
    assert empty_plan.get("error") == "empty_plan"


@pytest.mark.asyncio
async def test_search_code_python_fallback(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import cloud_agent
    from syte.config import settings
    from syte.cloud_agent import _tool_search_code

    data_dir = tmp_path / "data"
    ws = data_dir / "workspaces" / "proj"
    app = ws / "app"
    app.mkdir(parents=True)
    (app / "hello.py").write_text("def greet():\n    return 'hello-search-marker'\n")

    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    monkeypatch.setattr(cloud_agent, "workspace_path", lambda _pid: ws)
    monkeypatch.setattr(
        "syte.workspace_api._resolve_workspace_path",
        lambda _pid, rel: (ws / rel).resolve(),
    )
    # Force Python engine even if rg is installed.
    monkeypatch.setattr("shutil.which", lambda _name: None)

    result = await _tool_search_code("proj", {"pattern": "hello-search-marker", "path": "app"})
    assert result["ok"] is True
    assert result["engine"] == "python"
    assert result["match_count"] >= 1
    assert any("hello.py" in m["path"] for m in result["matches"])
