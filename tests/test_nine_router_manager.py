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

    monkeypatch.setattr(manager, "_run_docker", fake_run_docker)
    monkeypatch.setattr(manager.asyncio, "sleep", fake_sleep)

    result = await manager.start_router()

    assert result["ok"] is True
    assert result["running"] is True
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
