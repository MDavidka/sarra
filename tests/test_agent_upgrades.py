"""Tests for Phase 1–6 agent stability / capability upgrades."""

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


def test_build_model_thinking_params_applies_temperature_top_p() -> None:
    from syte.thinking_levels import build_model_thinking_params, resolve_thinking_config

    cfg = resolve_thinking_config(4)
    params = build_model_thinking_params(
        cfg,
        provider="openai",
        model="gemini-3.6-flash",
        api_base="https://generativelanguage.googleapis.com/v1beta/openai",
    )
    assert params["temperature"] == 0.3
    assert params["top_p"] == 0.98
    # Gemini 3.x accepts reasoning_effort for native thinking depth.
    assert params["reasoning_effort"] == "high"
    assert params["thinking_applied"] is True
    assert "thinking" not in params

def test_build_model_thinking_params_deepseek_thinking_and_cache() -> None:
    from syte.thinking_levels import build_model_thinking_params, resolve_thinking_config

    cfg = resolve_thinking_config(5)
    params = build_model_thinking_params(
        cfg,
        provider="openai",
        model="deepseek-chat",
        api_base="https://api.deepseek.com/v1",
    )
    assert params["thinking"]["type"] == "enabled"
    assert params["thinking"]["budget_tokens"] == 8192
    assert params["cache_prompt"] is True
    assert params["top_p"] == 1.0


def test_build_model_thinking_params_fast_level_no_thinking_key() -> None:
    from syte.thinking_levels import build_model_thinking_params, deepseek_thinking_payload, resolve_thinking_config

    cfg = resolve_thinking_config(2)
    assert deepseek_thinking_payload(cfg) is None
    params = build_model_thinking_params(
        cfg,
        provider="openai",
        model="deepseek-chat",
        api_base="https://api.deepseek.com/v1",
    )
    assert "thinking" not in params
    assert params["cache_prompt"] is True


def test_apply_prompt_cache_markers_anthropic_only() -> None:
    from syte.thinking_levels import apply_prompt_cache_markers

    messages = [{"role": "system", "content": "static"}, {"role": "user", "content": "hi"}]
    deepseek = apply_prompt_cache_markers(
        messages, provider="openai", model="deepseek-chat", api_base="https://api.deepseek.com/v1",
    )
    assert isinstance(deepseek[0]["content"], str)

    claude = apply_prompt_cache_markers(
        messages, provider="anthropic", model="claude-4", api_base="https://api.anthropic.com",
    )
    assert isinstance(claude[0]["content"], list)
    assert claude[0]["content"][0]["cache_control"]["type"] == "ephemeral"


def test_circuit_breaker_opens_after_failures() -> None:
    from syte.agent_errors import (
        ProviderError,
        check_circuit_breaker,
        record_circuit_failure,
        reset_circuit_breaker,
    )

    reset_circuit_breaker()
    for _ in range(3):
        record_circuit_failure("openai", "deepseek-chat")
    with pytest.raises(ProviderError) as exc:
        check_circuit_breaker("openai", "deepseek-chat")
    assert exc.value.error_type == "circuit_open"
    reset_circuit_breaker()


def test_site_planner_detection_and_order() -> None:
    from syte.site_planner import (
        is_complex_site_request,
        is_substantive_site_request,
        is_website_request,
        order_subtasks,
        site_request_needs_clarification,
    )

    assert is_complex_site_request(
        "Please build a website with a landing page, pricing, and about pages for our AI startup"
    )
    assert not is_complex_site_request("fix the button")
    assert is_website_request("Create a landing page for a bakery")
    assert is_substantive_site_request("Create a landing page for a bakery")
    assert site_request_needs_clarification("Create a landing page for a bakery")
    assert not site_request_needs_clarification(
        "Create a bold dark-tech landing page for a bakery with menu and contact sections"
    )
    assert not is_substantive_site_request("Fix the button label")

    ordered = order_subtasks([
        {"task": "B", "deps": ["A"], "files": []},
        {"task": "A", "deps": [], "files": []},
        {"task": "C", "deps": ["B"], "files": []},
    ])
    assert [t["task"] for t in ordered] == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_instruction_cache_excludes_memory(tmp_data_dir: Path) -> None:
    from syte.agent_memory import save_summary, upsert_session_meta
    from syte.cloud_agent import _build_syte_instruction, _instruction_cache, invalidate_instruction_cache
    from syte.database import create_project, init_db
    from syte.workspace import ensure_workspace

    await init_db()
    await create_project({"id": "mem1", "name": "Memory Test", "port": 3100, "start_command": ""})
    ensure_workspace("mem1")
    invalidate_instruction_cache("mem1")

    first = await _build_syte_instruction("mem1")
    assert "Memory Test" in first
    assert "Project metadata" in first
    cache_keys = [k for k in _instruction_cache if k[0] == "mem1"]
    assert len(cache_keys) == 1
    cached_static = _instruction_cache[cache_keys[0]]
    assert "Project memory" not in cached_static

    await upsert_session_meta("mem1", 1, status="completed", active_files=["app/app/page.tsx"])
    await save_summary(
        "mem1",
        summary_text="Story so far: UNIQUE_MEMORY_MARKER_42 redesigned hero",
        up_to_session_number=1,
        key_decisions=["Chose bold theme"],
        technical_state="Touched page.tsx",
    )
    second = await _build_syte_instruction("mem1")
    assert "UNIQUE_MEMORY_MARKER_42" in second
    # Static cache entry unchanged (still no memory inside)
    assert "UNIQUE_MEMORY_MARKER_42" not in _instruction_cache[cache_keys[0]]


@pytest.mark.asyncio
async def test_match_active_skills_keywords(tmp_data_dir: Path) -> None:
    from syte.agent_skills import match_active_skills, skill_hint_block
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "sk1", "name": "Skills", "port": 3101, "start_command": ""})
    matched = await match_active_skills("sk1", "Please look up the latest Next.js docs online")
    ids = {s["id"] for s in matched}
    assert "web-search" in ids
    hint = skill_hint_block(matched)
    assert "Web Search" in hint or "web_search" in hint


@pytest.mark.asyncio
async def test_web_search_mcp_builtin(tmp_data_dir: Path) -> None:
    from syte.agent_artifacts import call_mcp_addon, connect_mcp_addon, list_mcp_addons
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "mcp1", "name": "MCP", "port": 3102, "start_command": ""})
    addons = await list_mcp_addons("mcp1")
    names = {a["name"] for a in addons}
    assert "syte" in names
    assert "web_search" in names
    connected = await connect_mcp_addon("mcp1", "web_search")
    assert connected["ok"] is True
    # DuckDuckGo Instant Answer should succeed without an API key.
    result = await call_mcp_addon("mcp1", "web_search", "web_search", {"query": "Next.js"})
    assert result.get("ok") is True
    assert result.get("provider") in {"duckduckgo", "tavily", "brave"}
