"""Tests for stable preview domain assignment."""

from pathlib import Path

import pytest

from syte.config import settings
from syte.database import create_project, get_project, init_db, update_project
from syte.preview_manager import ensure_preview_address, start_preview, stop_preview_async


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_stop_preview_keeps_domain(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    await init_db()
    await create_project({
        "id": "proj-1",
        "name": "Test",
        "port": 3000,
        "start_command": "",
    })
    await update_project("proj-1", {
        "preview_domain": "previewa-test.sycord.com",
        "preview_port": 4000,
        "preview_status": "running",
    })

    async def fake_apply():
        return True, "ok"

    monkeypatch.setattr("syte.certificates.apply_proxy_config", fake_apply)
    monkeypatch.setattr("syte.preview_manager.stop_preview", lambda _id: (True, "stopped"))

    ok, _msg = await stop_preview_async("proj-1")
    assert ok is True
    project = await get_project("proj-1")
    assert project["preview_domain"] == "previewa-test.sycord.com"
    assert project["preview_status"] == "stopped"


@pytest.mark.asyncio
async def test_ensure_preview_address_assigns_once(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await init_db()
    await create_project({
        "id": "proj-2",
        "name": "My App",
        "port": 3001,
        "start_command": "",
    })

    async def fake_zone() -> str:
        return "sycord.site"

    monkeypatch.setattr("syte.preview_domains.resolve_preview_zone", fake_zone)

    project = await get_project("proj-2")
    project = await ensure_preview_address(project or {})
    first_domain = project["preview_domain"]
    assert first_domain.startswith("preview")
    assert first_domain.endswith(".sycord.site")
    assert project["preview_port"] == 4000

    project = await ensure_preview_address(project)
    assert project["preview_domain"] == first_domain
    assert project["preview_port"] == 4000


@pytest.mark.asyncio
async def test_start_preview_skips_when_already_running(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await init_db()
    await create_project({
        "id": "proj-3",
        "name": "Live",
        "port": 3002,
        "start_command": "",
    })
    await update_project("proj-3", {
        "preview_domain": "previewk-live.sycord.site",
        "preview_port": 4001,
        "preview_status": "running",
    })

    monkeypatch.setattr("syte.preview_manager.is_preview_running", lambda _id: True)
    monkeypatch.setattr("syte.preview_manager._port_listening", lambda _port: True)
    async def fake_iframe(_p):
        return {"all_ok": True, "items": []}

    monkeypatch.setattr("syte.preview_manager.preview_iframe_status", fake_iframe)
    monkeypatch.setattr(
        "syte.project_enrich.enrich_ssl",
        lambda _p: {"badge": "http"},
    )

    ok, msg, meta = await start_preview("proj-3")
    assert ok is True
    assert "already running" in msg.lower()
    assert meta["preview_domain"] == "previewk-live.sycord.site"
