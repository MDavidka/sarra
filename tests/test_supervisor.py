"""Tests for always-on OpenHands supervisor reconciliation."""

import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("process_running", "ready", "expected_warms"),
    [
        (False, False, 1),
        (True, False, 1),
        (True, True, 0),
    ],
)
async def test_maintain_rewarms_desired_agents(
    monkeypatch: pytest.MonkeyPatch,
    process_running: bool,
    ready: bool,
    expected_warms: int,
) -> None:
    from syte import supervisor

    projects = [{
        "id": "proj-always-on",
        "status": "stopped",
        "agent_status": "running",
        "agent_port": 5200,
    }]
    warmed: list[tuple[str, str]] = []

    async def fake_projects():
        return projects

    async def fake_probe(_port):
        return {"ok": ready}

    async def fake_warm(project_id, *, source):
        warmed.append((project_id, source))
        return {"ok": True, "status": "warming"}

    async def fake_expire():
        return None

    monkeypatch.setattr(supervisor, "ensure_caddy", lambda: None)
    monkeypatch.setattr(supervisor, "list_projects", fake_projects)
    monkeypatch.setattr(
        supervisor,
        "is_agent_running",
        lambda _project_id: process_running,
    )
    monkeypatch.setattr(supervisor, "probe_agent_http", fake_probe)
    monkeypatch.setattr(supervisor, "warm_agent", fake_warm)
    monkeypatch.setattr(
        "syte.preview_manager.expire_stale_previews",
        fake_expire,
    )

    await supervisor.maintain()

    assert len(warmed) == expected_warms
    if warmed:
        assert warmed[0] == ("proj-always-on", "supervisor")


@pytest.mark.asyncio
async def test_maintain_respects_explicit_agent_stop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import supervisor

    async def fake_projects():
        return [{
            "id": "proj-stopped",
            "status": "stopped",
            "agent_status": "stopped",
            "agent_port": 5201,
        }]

    async def unexpected_warm(*args, **kwargs):
        raise AssertionError("explicitly stopped agents must stay stopped")

    async def fake_expire():
        return None

    monkeypatch.setattr(supervisor, "ensure_caddy", lambda: None)
    monkeypatch.setattr(supervisor, "list_projects", fake_projects)
    monkeypatch.setattr(supervisor, "warm_agent", unexpected_warm)
    monkeypatch.setattr(
        "syte.preview_manager.expire_stale_previews",
        fake_expire,
    )

    await supervisor.maintain()
