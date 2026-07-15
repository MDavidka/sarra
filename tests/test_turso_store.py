"""Tests for the Turso (libSQL) durable agent-session store."""

from pathlib import Path

import pytest

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


@pytest.fixture
def turso_local(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Point turso_store at a local libSQL file so tests don't need a live Turso server."""
    from syte import turso_store

    db_path = tmp_path / "turso-local.db"

    async def fake_settings():
        return f"file:{db_path}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_settings)
    turso_store.reset_client_cache()
    yield turso_store
    turso_store.reset_client_cache()


@pytest.mark.asyncio
async def test_turso_not_configured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import turso_store

    async def empty_settings():
        return "", ""

    monkeypatch.setattr(turso_store, "turso_settings", empty_settings)
    turso_store.reset_client_cache()

    assert await turso_store.turso_configured() is False
    assert await turso_store.get_turso_client() is None
    assert await turso_store.open_session("proj-1") is None
    assert await turso_store.record_event("missing-session", "proj-1", "processing") is None
    assert await turso_store.get_session("missing-session") is None
    assert await turso_store.list_sessions_for_project("proj-1") == []


@pytest.mark.asyncio
async def test_open_session_record_events_and_fetch(turso_local) -> None:
    session_id = await turso_local.open_session("proj-1", session_number=1, model_profile="syra-base")
    assert session_id

    await turso_local.record_event(
        session_id, "proj-1", "request_started", role="user",
        title="Request", detail="Add dark mode", payload={"request_id": "req-1"},
    )
    await turso_local.record_event(
        session_id, "proj-1", "request_completed", role="assistant",
        detail="Added dark mode", payload={"reply": "Added dark mode"},
    )
    await turso_local.close_session(session_id, status="completed")

    session = await turso_local.get_session(session_id)
    assert session is not None
    assert session["id"] == session_id
    assert session["project_id"] == "proj-1"
    assert session["status"] == "completed"
    assert len(session["events"]) == 2
    assert session["events"][0]["event_type"] == "request_started"
    assert session["events"][1]["payload"]["reply"] == "Added dark mode"


@pytest.mark.asyncio
async def test_get_session_since_id_filters_events(turso_local) -> None:
    session_id = await turso_local.open_session("proj-2")
    first = await turso_local.record_event(session_id, "proj-2", "processing")
    await turso_local.record_event(session_id, "proj-2", "request_completed")

    session = await turso_local.get_session(session_id, since_id=first["id"])
    assert len(session["events"]) == 1
    assert session["events"][0]["event_type"] == "request_completed"


@pytest.mark.asyncio
async def test_list_sessions_for_project_orders_newest_first(turso_local) -> None:
    s1 = await turso_local.open_session("proj-3", session_number=1)
    s2 = await turso_local.open_session("proj-3", session_number=2)

    sessions = await turso_local.list_sessions_for_project("proj-3")
    ids = [s["id"] for s in sessions]
    assert ids[0] == s2
    assert s1 in ids

    latest = await turso_local.latest_session_id_for_project("proj-3")
    assert latest == s2


@pytest.mark.asyncio
async def test_get_session_returns_none_for_unknown_id(turso_local) -> None:
    assert await turso_local.get_session("does-not-exist") is None
