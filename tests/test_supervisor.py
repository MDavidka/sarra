"""Tests for cloud request recovery during VM startup."""

import pytest


@pytest.mark.asyncio
async def test_startup_resumes_durable_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import supervisor

    resumed = []

    async def noop():
        return None

    async def fake_resume():
        resumed.append(True)
        return 2

    monkeypatch.setattr(supervisor, "apply_proxy_config", noop)
    monkeypatch.setattr(supervisor, "ensure_caddy", lambda: None)
    monkeypatch.setattr(supervisor, "maintain", noop)
    monkeypatch.setattr("syte.agent_jobs.resume_pending_requests", fake_resume)
    await supervisor.startup()
    assert resumed == [True]
