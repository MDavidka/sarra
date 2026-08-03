"""Regression coverage for publishing the combined Syte GUI/LiteLLM host."""

from typing import Any

import pytest

from syte.caddy_routes import render_litellm_api_route
from syte.litellm_config import LITELLM_PUBLIC_HOST


def test_litellm_route_uses_named_path_matcher_and_gui_fallback() -> None:
    rendered = "\n".join(
        render_litellm_api_route(
            "api.example.com",
            4000,
            use_wildcard_tls=False,
            gui_port=8787,
        )
    )

    assert "@litellm path /v1 /v1/*" in rendered
    assert "handle @litellm {\n        reverse_proxy 127.0.0.1:4000" in rendered
    assert "handle /v1 /v1/* {" not in rendered
    assert "handle {\n        reverse_proxy 127.0.0.1:8787" in rendered


@pytest.mark.asyncio
async def test_start_publishes_gui_after_host_setup_before_litellm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import host_setup, litellm_manager, main

    calls: list[str] = []

    async def fake_prepare() -> dict[str, Any]:
        calls.append("prepare")
        return {"ok": True, "message": "host ready"}

    async def fake_apply() -> tuple[bool, str]:
        calls.append("publish")
        return True, "route active"

    async def fake_start() -> dict[str, Any]:
        calls.append("start")
        return {"ok": True, "running": True, "message": "LiteLLM started"}

    monkeypatch.setattr(host_setup, "prepare_syra_host", fake_prepare)
    monkeypatch.setattr(main, "apply_proxy_config", fake_apply)
    monkeypatch.setattr(litellm_manager, "start_litellm", fake_start)

    result = await main._start_syra_stack_locked()

    assert calls == ["prepare", "publish", "start"]
    assert result["ok"] is True
    assert result["proxy_configured"] is True
    assert result["gui_published"] is True
    assert result["web_gui_url"] == f"https://{LITELLM_PUBLIC_HOST}/"


@pytest.mark.asyncio
async def test_start_failure_keeps_published_gui_result_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import host_setup, litellm_manager, main

    calls: list[str] = []

    async def fake_prepare() -> dict[str, Any]:
        calls.append("prepare")
        return {"ok": True, "message": "host ready"}

    async def fake_apply() -> tuple[bool, str]:
        calls.append("publish")
        return True, "route active"

    async def fake_start() -> dict[str, Any]:
        calls.append("start")
        return {
            "ok": False,
            "running": False,
            "message": "LiteLLM migration failed",
        }

    monkeypatch.setattr(host_setup, "prepare_syra_host", fake_prepare)
    monkeypatch.setattr(main, "apply_proxy_config", fake_apply)
    monkeypatch.setattr(litellm_manager, "start_litellm", fake_start)

    result = await main._start_syra_stack_locked()

    assert calls == ["prepare", "publish", "start"]
    assert result["ok"] is False
    assert result["message"] == "LiteLLM migration failed"
    assert result["proxy_configured"] is True
    assert result["proxy_message"] == "route active"
    assert result["gui_published"] is True
    assert result["host_setup"]["ok"] is True



@pytest.mark.asyncio
async def test_restart_host_failure_preserves_existing_running_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import host_setup, litellm_manager, main

    calls: list[str] = []

    async def fake_status() -> dict[str, Any]:
        calls.append("status")
        return {"running": True}

    async def fake_prepare() -> dict[str, Any]:
        calls.append("prepare")
        return {"ok": False, "message": "DNS preparation failed"}

    async def unexpected_apply() -> tuple[bool, str]:
        calls.append("publish")
        return True, "unexpected"

    monkeypatch.setattr(litellm_manager, "litellm_status", fake_status)
    monkeypatch.setattr(host_setup, "prepare_syra_host", fake_prepare)
    monkeypatch.setattr(main, "apply_proxy_config", unexpected_apply)

    result = await main._restart_syra_stack_locked()

    assert calls == ["status", "prepare"]
    assert result["ok"] is False
    assert result["running"] is True
    assert result["gui_published"] is False
    assert result["proxy_configured"] is False


@pytest.mark.asyncio
async def test_restart_proxy_failure_preserves_state_and_skips_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import host_setup, litellm_manager, main

    calls: list[str] = []

    async def fake_status() -> dict[str, Any]:
        calls.append("status")
        return {"running": True}

    async def fake_prepare() -> dict[str, Any]:
        calls.append("prepare")
        return {"ok": True, "message": "host ready"}

    async def fake_apply() -> tuple[bool, str]:
        calls.append("publish")
        return False, "reload failed"

    async def unexpected_restart() -> dict[str, Any]:
        calls.append("restart")
        return {"ok": True, "running": True}

    monkeypatch.setattr(litellm_manager, "litellm_status", fake_status)
    monkeypatch.setattr(litellm_manager, "restart_litellm", unexpected_restart)
    monkeypatch.setattr(host_setup, "prepare_syra_host", fake_prepare)
    monkeypatch.setattr(main, "apply_proxy_config", fake_apply)

    result = await main._restart_syra_stack_locked()

    assert calls == ["status", "prepare", "publish"]
    assert result["ok"] is False
    assert result["running"] is True
    assert result["proxy_configured"] is False
    assert result["gui_published"] is False
    assert result["proxy_message"] == "reload failed"
