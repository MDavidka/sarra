"""Tests for layered agent memory, visual analysis, design profiles, and routing."""

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


@pytest.mark.asyncio
async def test_session_meta_and_active_files(tmp_data_dir: Path) -> None:
    from syte.agent_memory import (
        get_session_meta,
        touch_active_file,
        upsert_session_meta,
    )

    await upsert_session_meta(
        "p1", 1, turso_session_id="turso-1", status="open", model_profile="syra-base",
    )
    files = await touch_active_file("p1", 1, "app/app/page.tsx")
    assert "app/app/page.tsx" in files
    files = await touch_active_file("p1", 1, "app/components/Hero.tsx")
    assert files[-1] == "app/components/Hero.tsx"
    meta = await get_session_meta("p1", 1)
    assert meta is not None
    assert meta["turso_session_id"] == "turso-1"
    assert "app/app/page.tsx" in meta["active_files"]


@pytest.mark.asyncio
async def test_summary_and_memory_snapshot(tmp_data_dir: Path) -> None:
    from syte.agent_memory import (
        project_memory_snapshot,
        save_summary,
        upsert_session_meta,
    )
    from syte.cloud_agent_store import ensure_session, set_turso_session_id
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "p2", "name": "P2", "port": 3050, "start_command": ""})
    await ensure_session("p2", "syra-base")
    await set_turso_session_id("p2", "sess-abc")
    await upsert_session_meta(
        "p2", 3, turso_session_id="sess-abc", status="completed",
        active_files=["app/app/page.tsx", "components/Hero.tsx"],
    )
    summary = await save_summary(
        "p2",
        summary_text="Story so far (session 3):\nUser asked: redesign hero",
        up_to_session_number=3,
        key_decisions=["Chose bold theme"],
        technical_state="Touched Hero.tsx",
        session_id="sess-abc",
    )
    snap = await project_memory_snapshot("p2")
    assert snap["latest_summary"]["id"] == summary["id"]
    assert "hero" in snap["last_work"].lower() or "story" in snap["last_work"].lower()
    assert snap["resume_session"]["turso_session_id"] == "sess-abc"
    assert "components/Hero.tsx" in snap["active_files"]


@pytest.mark.asyncio
async def test_workspace_index_tags(tmp_data_dir: Path) -> None:
    from syte.agent_memory import (
        lookup_workspace_paths,
        prompt_tags_from_message,
        semantic_tags_for_path,
        upsert_workspace_file,
    )

    assert "hero" in semantic_tags_for_path("app/components/Hero.tsx")
    assert "navbar" in semantic_tags_for_path("app/components/Navbar.tsx")
    await upsert_workspace_file("p3", "app/components/Hero.tsx", content="export function Hero(){}")
    await upsert_workspace_file("p3", "app/app/globals.css", content=":root{}")
    hits = await lookup_workspace_paths("p3", tags=["hero"], limit=10)
    assert any(h["path"].endswith("Hero.tsx") for h in hits)
    assert "hero" in prompt_tags_from_message("Please fix the hero spacing")
    assert "navbar" in prompt_tags_from_message("Update the navbar colors")


@pytest.mark.asyncio
async def test_visual_analysis_roundtrip(tmp_data_dir: Path) -> None:
    from syte.agent_memory import (
        get_visual_analysis,
        latest_visual_analysis,
        save_visual_analysis,
        visual_feedback_prompt,
    )
    from syte.visual_analysis import (
        blueprint_from_analysis,
        heuristic_analysis_from_meta,
        normalize_analysis,
        parse_analysis_json,
    )

    parsed = parse_analysis_json('```json\n{"description":"A hero","issues":["gap"],"suggestions":["pad"]}\n```')
    norm = normalize_analysis(parsed)
    assert norm["description"] == "A hero"
    assert norm["issues"] == ["gap"]

    heur = heuristic_analysis_from_meta(viewport="desktop", width=1280, height=800, route="/")
    saved = await save_visual_analysis(
        "p4",
        viewport="desktop",
        description=heur["description"],
        issues=heur["issues"],
        suggestions=heur["suggestions"],
        screenshot_id="shot1",
        screenshot_url="/api/projects/p4/agent/screenshots/shot1",
    )
    got = await get_visual_analysis(saved["id"])
    assert got is not None
    assert got["viewport"] == "desktop"
    latest = await latest_visual_analysis("p4")
    assert latest and latest["id"] == saved["id"]
    prompt = visual_feedback_prompt(got)
    assert "Visual feedback" in prompt
    assert "minimal diffs" in prompt
    bp = blueprint_from_analysis(got)
    assert "sections" in bp


@pytest.mark.asyncio
async def test_design_profile_theme(tmp_data_dir: Path) -> None:
    from syte.agent_memory import design_profile_prompt_block, get_design_profile
    from syte.database import create_project, init_db
    from syte.design_profile import apply_theme_profile, list_style_profiles

    await init_db()
    await create_project({"id": "p5", "name": "P5", "port": 3051, "start_command": ""})
    profile = await apply_theme_profile(
        "p5", theme_key="dark-tech", style_key="fintech-dark", source="test",
    )
    assert profile["theme_key"] == "dark-tech"
    assert profile["style_key"] == "fintech-dark"
    assert "--primary" in (profile.get("design_system_css") or "")
    loaded = await get_design_profile("p5")
    assert loaded and loaded["theme_key"] == "dark-tech"
    block = design_profile_prompt_block(loaded)
    assert "design system" in block.lower()
    styles = list_style_profiles()
    assert any(s["key"] == "saas-minimal" for s in styles)


def test_model_routing_heuristics() -> None:
    from syte.model_routing import suggest_model_profile

    nano = suggest_model_profile("change button text to Sign up")
    assert nano["suggested_profile"] == "syra-nano"
    # Suggestions must never auto-apply — that overrode the chat model picker.
    assert nano["auto_applied"] is False

    short = suggest_model_profile("hey", explicit_profile="syra-ultra")
    assert short["suggested_profile"] == "syra-nano"
    assert short["effective_profile"] == "syra-ultra"
    assert short["auto_applied"] is False

    havy = suggest_model_profile("build a new landing page from this screenshot")
    assert havy["suggested_profile"] == "syra-havy"

    explicit = suggest_model_profile("change button text", explicit_profile="syra-base")
    assert explicit["auto_applied"] is False
    assert explicit["effective_profile"] == "syra-base"

    thinking = suggest_model_profile("change button text", thinking_level=4)
    assert thinking["auto_applied"] is False


@pytest.mark.asyncio
async def test_api_sessions_include_memory(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import api_router
    from syte.agent_memory import save_summary, upsert_session_meta
    from syte.cloud_agent_store import ensure_session, set_turso_session_id
    from syte.database import create_project, init_db
    from syte import turso_store

    async def unconfigured():
        return "", ""

    monkeypatch.setattr(turso_store, "turso_settings", unconfigured)
    turso_store.reset_client_cache()

    await init_db()
    await create_project({"id": "p6", "name": "P6", "port": 3052, "start_command": ""})
    await ensure_session("p6", "syra-base")
    await set_turso_session_id("p6", "local-sess")
    await upsert_session_meta(
        "p6", 1, turso_session_id="local-sess", status="open",
        active_files=["app/app/page.tsx"],
    )
    await save_summary(
        "p6",
        summary_text="Story so far: homepage hero redesign",
        up_to_session_number=1,
        key_decisions=["Large CTA"],
    )

    result = await api_router.api_agent_sessions(uuid="p6", limit=50, resume=1, _token={})
    assert result["ok"] is True
    assert result["resume"] == 1
    assert result["last_work"]
    assert result["resume_session"]["turso_session_id"] == "local-sess"
    assert result["latest_summary"] is not None


@pytest.mark.asyncio
async def test_sycord_project_summary(tmp_data_dir: Path) -> None:
    from syte.database import create_project, init_db
    from syte.design_profile import apply_theme_profile
    from syte.sycord import service

    await init_db()
    await create_project({
        "id": "p7", "name": "Shop", "port": 3053, "start_command": "",
        "domain": "shop.sycord.site",
    })
    await apply_theme_profile("p7", style_key="saas-minimal")
    summary = await service.project_summary("p7")
    assert summary is not None
    assert summary["uuid"] == "p7"
    assert summary["design_tokens"] is not None
    assert summary["domain"] == "shop.sycord.site"


@pytest.mark.asyncio
async def test_maybe_summarize_session(tmp_data_dir: Path) -> None:
    from syte.agent_memory import maybe_summarize_session
    from syte.cloud_agent_store import append_message, begin_turn_session, ensure_session
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "p8", "name": "P8", "port": 3054, "start_command": ""})
    await ensure_session("p8", "syra-base")
    session = await begin_turn_session("p8", "syra-base")
    for i in range(14):
        await append_message("p8", f"req-{i}", "user", f"Please tweak item {i}", session_number=session)
        await append_message(
            "p8", f"req-{i}", "assistant",
            f"Updated components/Hero.tsx — decided on blue accent {i}",
            session_number=session,
        )
    summary = await maybe_summarize_session("p8", session, min_messages=10)
    assert summary is not None
    assert "Story so far" in summary["summary_text"]
    assert summary["up_to_session_number"] == session


@pytest.mark.asyncio
async def test_webhook_emit_no_urls(tmp_data_dir: Path) -> None:
    from syte.database import init_db
    from syte.webhooks import EVENT_AGENT_SESSION_COMPLETED, emit_webhook

    await init_db()
    result = await emit_webhook(EVENT_AGENT_SESSION_COMPLETED, {"project_id": "x"})
    assert result["skipped"] is True
    assert result["delivered"] == 0
