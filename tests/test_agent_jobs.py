"""Tests for async agent job queue."""

import pytest
from pathlib import Path

from syte.config import settings


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    return data_dir


@pytest.mark.asyncio
async def test_submit_agent_request_returns_immediately(tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from syte.agent_jobs import submit_agent_request
    from syte.database import create_project, init_db

    await init_db()
    await create_project({
        "id": "job-proj",
        "name": "Jobs",
        "port": 3020,
        "start_command": "",
    })

    async def fake_run(*_args, **_kwargs):
        return {"ok": True, "reply": "done"}

    monkeypatch.setattr("syte.agent_jobs._run_job", fake_run)

    result = await submit_agent_request("job-proj", "hello", source="test")
    assert result["ok"] is True
    assert result["status"] == "accepted"
    assert result["request_id"].startswith("req_")
    assert "turso_session_id" in result
    assert "session_url" in result


@pytest.mark.asyncio
async def test_api_started_session_syncs_every_message_to_turso_live(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sessions started directly via the API (submit_agent_request) must sync
    every message to Turso as it happens, not only at the end of the turn."""
    from syte import turso_store
    from syte.agent_jobs import submit_agent_request
    from syte.cloud_agent import turso_message_sync_status
    from syte.database import create_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({"id": "job-live-proj", "name": "Live", "port": 3021, "start_command": ""})

    turso_db = tmp_data_dir / "turso-local-jobs.db"

    async def fake_turso_settings():
        return f"file:{turso_db}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_turso_settings)
    turso_store.reset_client_cache()

    async def fake_provider(*_args, **_kwargs):
        return {"role": "assistant", "content": "Done."}

    monkeypatch.setattr("syte.cloud_agent._provider_completion", fake_provider)

    result = await submit_agent_request("job-live-proj", "hello from api", source="api")
    assert result["ok"] is True
    session_id = result["turso_session_id"]
    # The durable Turso session is opened and its request_started activity
    # event is written synchronously during admission — before the
    # background worker (which persists the actual chat messages) even runs.
    assert session_id
    session_doc = await turso_store.get_session(session_id)
    assert session_doc is not None
    assert any(e["event_type"] == "request_started" for e in session_doc["events"])

    from syte import agent_jobs

    task = agent_jobs._running.get("job-live-proj")
    if task:
        await task

    # Once the worker has run the turn, every message it produced — the
    # admitted user message plus the assistant's reply — has been mirrored
    # to the same durable Turso session in real time (message-by-message,
    # not only once at the very end of the turn).
    messages_after = await turso_store.list_messages(session_id)
    assert [m["role"] for m in messages_after] == ["user", "assistant"]

    sync = await turso_message_sync_status("job-live-proj")
    assert sync["turso_configured"] is True
    assert sync["all_saved"] is True
    turso_store.reset_client_cache()


@pytest.mark.asyncio
async def test_run_job_converts_unexpected_exception_to_terminal_event(
    tmp_data_dir: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.agent_activity import list_agent_events
    from syte.agent_jobs import _run_job
    from syte.database import init_db

    await init_db()

    async def fail_request(*_args, **_kwargs):
        raise RuntimeError("runtime crashed")

    monkeypatch.setattr(
        "syte.cloud_agent._communicate_with_agent_impl",
        fail_request,
    )

    result = await _run_job(
        "job-error-proj",
        "req-error",
        "fix the page",
        model_profile="syra-base",
        source="test",
        auto_start=True,
    )

    failures = [
        event
        for event in await list_agent_events("job-error-proj")
        if event["event_type"] == "request_failed"
    ]
    assert result["ok"] is False
    assert result["request_id"] == "req-error"
    assert len(failures) == 1
    assert failures[0]["payload"]["request_id"] == "req-error"
    assert failures[0]["payload"]["retry_message"] == "fix the page"


@pytest.mark.asyncio
async def test_run_job_always_closes_turso_session_on_success(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import turso_store
    from syte.agent_jobs import _run_job
    from syte.database import create_project, init_db, set_setting

    await init_db()
    await set_setting("agent_syra_base_api_key", "base-key")
    await create_project({"id": "job-close-proj", "name": "Close", "port": 3022, "start_command": ""})

    turso_db = tmp_data_dir / "turso-close.db"

    async def fake_turso_settings():
        return f"file:{turso_db}", ""

    monkeypatch.setattr(turso_store, "turso_settings", fake_turso_settings)
    turso_store.reset_client_cache()

    session_id = await turso_store.open_session("job-close-proj", session_number=1)

    async def ok_request(*_args, **_kwargs):
        return {"ok": True, "reply": "done", "turso_session_id": session_id}

    monkeypatch.setattr("syte.cloud_agent._communicate_with_agent_impl", ok_request)

    result = await _run_job(
        "job-close-proj",
        "req-close",
        "hello",
        model_profile="syra-base",
        source="test",
        auto_start=True,
        session_number=1,
        turso_session_id=session_id,
    )
    assert result["ok"] is True
    session = await turso_store.get_session(session_id)
    assert session["status"] == "completed"
    assert session["ended_at"]
    turso_store.reset_client_cache()


@pytest.mark.asyncio
async def test_submit_interrupts_previous_turso_session_not_new(
    tmp_data_dir: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte.agent_jobs import submit_agent_request
    from syte.cloud_agent_store import ensure_session, set_turso_session_id
    from syte.database import create_project, init_db

    await init_db()
    await create_project({"id": "job-int-proj", "name": "Int", "port": 3023, "start_command": ""})
    await ensure_session("job-int-proj", "syra-base")
    await set_turso_session_id("job-int-proj", "old-session-id")

    interrupted: list[str | None] = []

    async def fake_interrupt(project_id, *, turso_session_id=None):
        interrupted.append(turso_session_id)
        return True, "ok"

    async def fake_open(*_args, **_kwargs):
        return "new-session-id"

    async def fake_run(*_args, **_kwargs):
        return {"ok": True, "reply": "done"}

    monkeypatch.setattr("syte.cloud_agent.interrupt_agent", fake_interrupt)
    monkeypatch.setattr("syte.agent_jobs.open_turso_session", fake_open)
    monkeypatch.setattr("syte.agent_jobs._run_job", fake_run)

    # Pretend a previous job is still running.
    import asyncio

    from syte import agent_jobs

    async def hang():
        await asyncio.sleep(3600)

    previous = asyncio.create_task(hang())
    agent_jobs._running["job-int-proj"] = previous

    result = await submit_agent_request("job-int-proj", "next", source="test")
    assert result["turso_session_id"] == "new-session-id"
    assert interrupted == ["old-session-id"]
    previous.cancel()
    try:
        await previous
    except asyncio.CancelledError:
        pass
