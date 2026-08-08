"""Focused tests for the managed 9Router Docker lifecycle."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.mark.asyncio
async def test_start_router_uses_persistent_data_and_official_image(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import nine_router_manager as manager
    from syte.database import get_setting, init_db

    await init_db()
    calls: list[tuple[list[str], float]] = []
    status_outputs = iter([
        "",
        json.dumps({"ID": "router-container-id", "State": "running", "Status": "Up 2 seconds"}),
    ])

    async def fake_run_docker(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
        calls.append((args, timeout))
        if args[:2] == ["ps", "-a"]:
            return 0, next(status_outputs)
        if args[0] == "run":
            return 0, "router-container-id"
        return 0, ""

    async def fake_sleep(_seconds: float) -> None:
        return None

    async def fake_probe() -> dict[str, object]:
        return {
            "web_gui_ready": True,
            "api_ready": True,
            "api_authenticated": True,
            "dashboard_status": 200,
            "api_status": 200,
            "readiness_message": "ready",
        }

    monkeypatch.setattr(manager, "_run_docker", fake_run_docker)
    monkeypatch.setattr(manager, "_probe_router_http", fake_probe)
    monkeypatch.setattr(manager.asyncio, "sleep", fake_sleep)

    result = await manager.start_router()

    assert result["ok"] is True
    assert result["running"] is True
    assert result["ready"] is True
    assert result["dashboard_url"].endswith("/dashboard")
    assert result["initial_password"]
    assert await get_setting(manager.NINE_ROUTER_PASSWORD_SETTING) == result["initial_password"]

    run_args = next(args for args, _timeout in calls if args[0] == "run")
    assert manager.NINE_ROUTER_IMAGE in run_args
    assert "--restart" in run_args
    assert run_args[run_args.index("--restart") + 1] == "unless-stopped"
    assert f"127.0.0.1:{manager.NINE_ROUTER_HOST_PORT}:{manager.NINE_ROUTER_CONTAINER_PORT}" in run_args
    assert manager.NINE_ROUTER_HOST_PORT != manager.NINE_ROUTER_CONTAINER_PORT
    assert f"{tmp_data_dir / manager.NINE_ROUTER_DATA_DIR_NAME}:/app/data" in run_args
    assert "-e" in run_args
    assert f"BASE_URL={manager.NINE_ROUTER_INTERNAL_BASE_URL}" in run_args
    assert f"NEXT_PUBLIC_BASE_URL=https://{manager.NINE_ROUTER_PUBLIC_HOST}" in run_args
    assert f"INITIAL_PASSWORD={result['initial_password']}" in run_args


@pytest.mark.asyncio
async def test_restart_router_does_not_start_after_stop_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import nine_router_manager as manager

    started = False

    async def fake_stop_router() -> dict[str, object]:
        return {"ok": False, "running": True, "message": "stop denied"}

    async def fake_start_router() -> dict[str, object]:
        nonlocal started
        started = True
        return {"ok": True, "running": True}

    monkeypatch.setattr(manager, "stop_router", fake_stop_router)
    monkeypatch.setattr(manager, "start_router", fake_start_router)

    result = await manager.restart_router()

    assert result == {"ok": False, "running": True, "message": "stop denied"}
    assert started is False



@pytest.mark.asyncio
async def test_router_password_is_revealed_only_once(
    tmp_data_dir: Path,
) -> None:
    from syte import nine_router_manager as manager
    from syte.database import init_db, set_setting

    await init_db()
    password, reveal = await manager._router_password()
    assert password
    assert reveal is True

    await set_setting(manager.NINE_ROUTER_PASSWORD_REVEALED_SETTING, "1")
    same_password, reveal_again = await manager._router_password()
    assert same_password == password
    assert reveal_again is False



@pytest.mark.asyncio
async def test_router_gui_guard_blocks_takeover_without_separate_domain(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import main
    from syte.database import init_db, set_setting
    from syte import nine_router_manager as manager

    await init_db()

    async def fake_status() -> dict[str, object]:
        return {"ok": True, "running": False, "enabled": False}

    monkeypatch.setattr(manager, "router_status", fake_status)
    await set_setting("gui_domain", "api.sycord.site")
    blocked = await main._router_gui_guard()
    assert blocked and blocked["ok"] is False
    assert "separate GUI domain" in str(blocked["message"])

    await set_setting("gui_domain", "console.sycord.site")
    assert await main._router_gui_guard() is None

    await set_setting("gui_domain", "")
    assert await main._router_gui_guard() is None


@pytest.mark.asyncio
async def test_router_public_state_rolls_back_after_caddy_failure(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import main
    from syte.database import get_setting, init_db

    await init_db()
    calls = 0

    async def fake_apply() -> tuple[bool, str]:
        nonlocal calls
        calls += 1
        return (False, "reload failed") if calls == 1 else (True, "restored")

    monkeypatch.setattr(main, "apply_proxy_config", fake_apply)
    ok, message = await main._set_router_public_state(True, force=True)

    assert ok is False
    assert "restored" in message
    assert calls == 2
    assert await get_setting("nine_router_public_enabled", "0") == "0"



@pytest.mark.asyncio
async def test_router_api_base_follows_managed_public_state(
    tmp_data_dir: Path,
) -> None:
    from syte.ai_providers import (
        NINE_ROUTER_API_BASE,
        NINE_ROUTER_MANAGED_API_BASE,
        resolved_nine_router_api_base,
    )
    from syte.database import init_db, set_setting

    await init_db()
    assert await resolved_nine_router_api_base() == NINE_ROUTER_API_BASE

    await set_setting("nine_router_public_enabled", "1")
    assert await resolved_nine_router_api_base() == NINE_ROUTER_MANAGED_API_BASE



@pytest.mark.asyncio
async def test_router_status_does_not_call_running_container_ready_without_http_probe(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import nine_router_manager as manager
    from syte.database import init_db

    await init_db()

    async def fake_run_docker(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
        if args[:2] == ["ps", "-a"]:
            return 0, json.dumps({"ID": "router-container-id", "State": "running", "Status": "Up 1 second"})
        return 0, ""

    async def fake_probe() -> dict[str, object]:
        return {
            "web_gui_ready": False,
            "api_ready": False,
            "api_authenticated": False,
            "dashboard_status": 503,
            "api_status": None,
            "readiness_message": "9Router dashboard is not ready (HTTP 503).",
        }

    monkeypatch.setattr(manager, "_run_docker", fake_run_docker)
    monkeypatch.setattr(manager, "_probe_router_http", fake_probe)

    result = await manager.router_status()

    assert result["running"] is True
    assert result["ready"] is False
    assert result["healthy"] is False
    assert "dashboard is not ready" in result["message"]


@pytest.mark.asyncio
async def test_router_probe_marks_dashboard_auth_errors_as_ready(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import nine_router_manager as manager
    from syte.database import init_db

    await init_db()

    async def fake_run_docker(args: list[str], timeout: float = 60.0) -> tuple[int, str]:
        if args[:2] == ["ps", "-a"]:
            return 0, json.dumps({"ID": "router-container-id", "State": "running", "Status": "Up 1 second"})
        return 0, ""

    async def fake_probe() -> dict[str, object]:
        return {
            "web_gui_ready": True,
            "api_ready": True,
            "api_authenticated": False,
            "dashboard_status": 401,
            "api_status": 401,
            "readiness_message": "9Router dashboard and API are ready; save an API key before making authenticated API calls.",
        }

    monkeypatch.setattr(manager, "_run_docker", fake_run_docker)
    monkeypatch.setattr(manager, "_probe_router_http", fake_probe)

    result = await manager.router_status()

    assert result["running"] is True
    assert result["ready"] is True
    assert result["healthy"] is True
    assert "ready" in result["message"]



@pytest.mark.asyncio
async def test_router_start_restores_fallback_before_cleanup_when_public_route_fails(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import main
    from syte.database import init_db
    from syte import nine_router_manager as manager

    await init_db()
    cleanup_called = False
    route_states: list[bool] = []

    async def fake_guard() -> None:
        return None

    async def fake_status() -> dict[str, object]:
        return {"ok": True, "running": False, "enabled": False}

    async def fake_start() -> dict[str, object]:
        return {"ok": True, "running": True, "ready": True, "message": "ready"}

    async def fake_stop() -> dict[str, object]:
        nonlocal cleanup_called
        cleanup_called = True
        return {"ok": True, "running": False}

    async def fake_set(enabled: bool, *, force: bool = False) -> tuple[bool, str]:
        route_states.append(enabled)
        return (False, "managed reload failed") if enabled else (True, "fallback restored")

    monkeypatch.setattr(main, "_router_gui_guard", fake_guard)
    monkeypatch.setattr(main, "_set_router_public_state", fake_set)
    monkeypatch.setattr(manager, "router_status", fake_status)
    monkeypatch.setattr(manager, "start_router", fake_start)
    monkeypatch.setattr(manager, "stop_router", fake_stop)

    result = await main._router_start()

    assert result["ok"] is False
    assert result["fallback_configured"] is True
    assert route_states == [True, False]
    assert cleanup_called is True
