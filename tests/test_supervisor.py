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

@pytest.mark.asyncio
async def test_autostart_project_agents_not_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import supervisor

    monkeypatch.setattr(supervisor, "openhands_installed", lambda: False)

    async def unexpected_list_projects():
        raise AssertionError("should not be called")

    monkeypatch.setattr(supervisor, "list_projects", unexpected_list_projects)

    await supervisor.autostart_project_agents()

@pytest.mark.asyncio
async def test_autostart_project_agents_bridge_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import supervisor

    monkeypatch.setattr(supervisor, "openhands_installed", lambda: True)

    async def fake_bridge_settings():
        raise Exception("Failed to load bridge settings")

    # The actual import is inside the function: `from syte.openhands_agent import bridge_settings`
    # We can monkeypatch `syte.supervisor.autostart_project_agents.__globals__` or similar, but
    # it's better to patch the target module directly. Wait, the code says:
    #     from syte.openhands_agent import bridge_settings
    #     ...
    #     bridge = await bridge_settings()
    # So we should monkeypatch `syte.openhands_agent.bridge_settings`

    monkeypatch.setattr("syte.openhands_agent.bridge_settings", fake_bridge_settings)

    async def unexpected_list_projects():
        raise AssertionError("should not be called")

    monkeypatch.setattr(supervisor, "list_projects", unexpected_list_projects)

    await supervisor.autostart_project_agents()

@pytest.mark.asyncio
async def test_autostart_project_agents_no_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import supervisor

    monkeypatch.setattr(supervisor, "openhands_installed", lambda: True)

    async def fake_bridge_settings():
        return {"profiles": {"default": {"api_key": ""}, "other": {"api_key": None}}}

    monkeypatch.setattr("syte.openhands_agent.bridge_settings", fake_bridge_settings)

    async def unexpected_list_projects():
        raise AssertionError("should not be called")

    monkeypatch.setattr(supervisor, "list_projects", unexpected_list_projects)

    await supervisor.autostart_project_agents()

@pytest.mark.asyncio
async def test_autostart_project_agents_warms_eligible_projects(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from syte import supervisor

    monkeypatch.setattr(supervisor, "openhands_installed", lambda: True)

    async def fake_bridge_settings():
        return {"profiles": {"default": {"api_key": "some-key"}}}

    monkeypatch.setattr("syte.openhands_agent.bridge_settings", fake_bridge_settings)

    projects = [
        {"id": "proj-1", "agent_status": "stopped"},
        {"id": "proj-2", "agent_status": "running"},
        {"id": "proj-3"},  # no agent_status, defaults to "running"
        {"id": "proj-4", "agent_status": "running"},
    ]

    async def fake_projects():
        return projects

    monkeypatch.setattr(supervisor, "list_projects", fake_projects)

    warmed = []

    async def fake_warm(pid, *, source):
        warmed.append((pid, source))
        if pid == "proj-2":
            return {"ok": True}
        elif pid == "proj-3":
            return {"ok": False, "message": "Disk full"}
        elif pid == "proj-4":
            return {"ok": False}  # message fallback test

    monkeypatch.setattr(supervisor, "warm_agent", fake_warm)

    import logging
    caplog.set_level(logging.INFO)

    await supervisor.autostart_project_agents()

    assert len(warmed) == 3
    assert ("proj-2", "startup") in warmed
    assert ("proj-3", "startup") in warmed
    assert ("proj-4", "startup") in warmed

    assert "Scheduled OpenHands warm-up for proj-2" in caplog.text
    assert "Autostart OpenHands agent failed for proj-3: Disk full" in caplog.text
    assert "Autostart OpenHands agent failed for proj-4: Agent warm-up failed" in caplog.text
