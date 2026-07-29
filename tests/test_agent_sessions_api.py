"""Tests for the Turso-backed agent session access routes."""

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


@pytest.fixture
def turso_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from syte import turso_store
    from syte.local_session_store import reset_local_session_cache

    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")

    db_path = tmp_path / "turso-local.db"

    async def fake_settings():
        return f"file:{db_path}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_settings)
    turso_store.reset_client_cache()
    reset_local_session_cache()
    yield turso_store
    turso_store.reset_client_cache()
    reset_local_session_cache()


@pytest.mark.asyncio
async def test_api_router_agent_sessions_without_turso(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import api_router
    from syte.database import create_project, init_db
    from syte import turso_store
    from syte.local_session_store import reset_local_session_cache

    async def unconfigured():
        return "", ""

    monkeypatch.setattr(turso_store, "turso_settings", unconfigured)
    turso_store.reset_client_cache()
    reset_local_session_cache()

    await init_db()
    await create_project({"id": "proj-a", "name": "A", "port": 3040, "start_command": ""})

    result = await api_router.api_agent_sessions(uuid="proj-a", limit=50, _token={})
    assert result["ok"] is True
    assert result["turso_configured"] is False
    assert result["sessions"] == []
    assert "locally" in (result.get("message") or "").lower()


@pytest.mark.asyncio
async def test_api_router_agent_session_serves_local_without_turso(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi import HTTPException

    from syte import api_router
    from syte import turso_store
    from syte.local_session_store import reset_local_session_cache

    async def unconfigured():
        return "", ""

    monkeypatch.setattr(turso_store, "turso_settings", unconfigured)
    turso_store.reset_client_cache()
    reset_local_session_cache()

    with pytest.raises(HTTPException) as exc_info:
        await api_router.api_get_agent_session(session_id="missing", since_id=0, _token={})
    assert exc_info.value.status_code == 404

    session_id = await turso_store.open_session("proj-local", session_number=1)
    await turso_store.record_event(session_id, "proj-local", "processing")
    fetched = await api_router.api_get_agent_session(session_id=session_id, since_id=0, _token={})
    assert fetched["ok"] is True
    assert fetched["id"] == session_id
    assert fetched["storage"] == "local"
    assert fetched["events"][0]["event_type"] == "processing"
    reset_local_session_cache()


@pytest.mark.asyncio
async def test_api_router_agent_sessions_and_session_with_turso(
    tmp_data_dir: Path, turso_local,
) -> None:
    from syte import api_router
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "proj-b", "name": "B", "port": 3041, "start_command": ""})

    session_id = await turso_local.open_session("proj-b", session_number=1, model_profile="syra-base")
    await turso_local.record_event(session_id, "proj-b", "request_started", detail="hello")
    await turso_local.close_session(session_id, status="completed")

    listed = await api_router.api_agent_sessions(uuid="proj-b", limit=50, _token={})
    assert listed["ok"] is True
    assert listed["turso_configured"] is True
    assert listed["sessions"][0]["id"] == session_id
    assert listed["sessions"][0]["session_url"] == f"/api/agent_session/{session_id}"

    fetched = await api_router.api_get_agent_session(session_id=session_id, since_id=0, _token={})
    assert fetched["ok"] is True
    assert fetched["id"] == session_id
    assert fetched["status"] == "completed"
    assert fetched["events"][0]["event_type"] == "request_started"


@pytest.mark.asyncio
async def test_api_router_agent_sessions_project_not_found(tmp_data_dir: Path) -> None:
    from fastapi import HTTPException

    from syte import api_router
    from syte.database import init_db

    await init_db()
    with pytest.raises(HTTPException) as exc_info:
        await api_router.api_agent_sessions(uuid="does-not-exist", limit=50, _token={})
    assert exc_info.value.status_code == 404
