"""Regression: rate limits, shadcn-not-HeroUI, file targeting, browser console inspect."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.mark.asyncio
async def test_api_project_routes_use_elevated_rate_limit() -> None:
    from syte.rate_limit import RateLimitMiddleware

    seen: list[int] = []

    class _App:
        async def __call__(self, scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"ok"})

    mw = RateLimitMiddleware(_App(), requests_per_minute=5, elevated_requests_per_minute=50)

    async def _allow(key: str, limit: int):
        seen.append(limit)
        return True, 0.0

    mw._allow = _allow  # type: ignore[method-assign]

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    messages: list[dict] = []

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "path": "/api/projects/demo/agent/activity",
        "headers": [],
        "client": ("127.0.0.1", 1234),
    }
    await mw(scope, receive, send)
    assert seen == [50]

    seen.clear()
    scope["path"] = "/sycord/api/preview_status"
    await mw(scope, receive, send)
    assert seen == [50]

    seen.clear()
    scope["path"] = "/"
    await mw(scope, receive, send)
    assert seen == [5]


def test_instant_thinking_has_enough_tool_steps() -> None:
    from syte.thinking_levels import resolve_thinking_config

    instant = resolve_thinking_config(1)
    fast = resolve_thinking_config(2)
    assert instant["max_tool_steps"] >= 10
    assert fast["max_tool_steps"] >= 18


def test_design_contract_bans_heroui() -> None:
    from syte.design_contract import DESIGN_CONTRACT_MARKDOWN, build_design_contract_spec

    text = DESIGN_CONTRACT_MARKDOWN.lower()
    assert "heroui" in text
    assert "shadcn" in text
    assert "next.js" in text.lower() or "nextjs" in DESIGN_CONTRACT_MARKDOWN.lower()
    spec = build_design_contract_spec()
    assert "HeroUI" in spec["rules"]["components"] or "heroui" in spec["rules"]["components"].lower()


def test_design_linter_fails_on_heroui(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import config as config_mod
    from syte.design_linter import validate_design

    monkeypatch.setattr(config_mod.settings, "workspaces_dir", tmp_path)
    app = tmp_path / "proj" / "app"
    (app / "components" / "ui").mkdir(parents=True)
    (app / "app").mkdir(parents=True)
    (app / "package.json").write_text(
        '{"dependencies":{"@heroui/react":"2.0.0","tailwindcss":"3.4.0","next":"15.0.0"}}',
        encoding="utf-8",
    )
    (app / "app" / "globals.css").write_text(
        ":root { --radius: 0.5rem; --primary: 0 0% 0%; --background: 0 0% 100%; --card: 0 0% 98%; }\n"
        "body { font-family: var(--font-sans); --font-sans: Inter; }\n",
        encoding="utf-8",
    )
    (app / "app" / "page.tsx").write_text(
        'import { Button } from "@heroui/react";\nexport default function Page(){return <Button/>}\n',
        encoding="utf-8",
    )
    (app / "tailwind.config.js").write_text("module.exports = { content: [] }", encoding="utf-8")

    result = validate_design("proj")
    matched = [
        c for c in result["checks"]
        if (not c["ok"]) and (
            "heroui" in c["detail"].lower()
            or "forbidden" in c["detail"].lower()
        )
    ]
    assert matched, result["checks"]


def test_workspace_map_block_lists_real_paths() -> None:
    from syte.agent_memory import workspace_map_block

    block = workspace_map_block(
        [
            {"path": "app/app/page.tsx", "semantic_tags": ["page"]},
            {"path": "app/components/ui/button.tsx", "semantic_tags": ["ui", "component"]},
        ]
    )
    assert "app/app/page.tsx" in block
    assert "app/components/ui/button.tsx" in block
    assert "do not invent" in block.lower()


def test_website_enforcement_bans_heroui() -> None:
    from syte.cloud_agent import _website_enforcement_block

    text = _website_enforcement_block(is_website=True)
    assert "shadcn" in text.lower()
    assert "heroui" in text.lower()
    assert "inspect_preview" in text.lower()


@pytest.mark.asyncio
async def test_inspect_preview_captures_console_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import cloud_agent

    async def fake_access(project_id, action, **kwargs):
        if action == "status":
            return {"ok": True, "preview_url": "http://127.0.0.1:4001"}
        if action == "fetch":
            return {
                "ok": True,
                "url": "http://127.0.0.1:4001/",
                "status_code": 200,
                "content_type": "text/html",
                "content": "<html>ok</html>",
            }
        if action == "console":
            return {
                "ok": False,
                "load_ok": True,
                "title": "Demo",
                "ready_state": "complete",
                "console_logs": [{"level": "error", "text": "ReferenceError: x is not defined"}],
                "page_errors": [],
                "network_failures": [],
                "console_error_count": 1,
                "page_error_count": 0,
                "message": "console errors present",
            }
        return {"ok": False, "error": "unexpected", "message": action}

    monkeypatch.setattr(
        "syte.preview_access.run_access_action",
        fake_access,
    )
    result = await cloud_agent._tool_inspect_preview("proj", {"route": "/"}, {})
    assert result["console_error_count"] == 1
    assert result["ok"] is False
    assert result["console_logs"][0]["text"].startswith("ReferenceError")


def test_console_text_helper() -> None:
    from syte.cdp_client import _console_text

    assert "hello" in _console_text([{"type": "string", "value": "hello"}])
    assert "object" in _console_text([{"type": "object"}])


def test_preview_access_lists_console_action() -> None:
    from syte.agent_skills import default_access_config
    from syte.agent_skills import SKILL_FILES

    assert "console" in default_access_config()["preview_tools"]
    assert "console" in SKILL_FILES["preview-access.md"]
    assert "inspect_preview" in SKILL_FILES["preview-verification.md"]
    assert "HeroUI" in SKILL_FILES["website-editing.md"] or "heroui" in SKILL_FILES["website-editing.md"].lower()
