"""Regression tests for the Remote Servers fleet and load-balancing control plane."""
from __future__ import annotations

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
async def test_fleet_snapshot_uses_persisted_node_metrics(tmp_data_dir: Path) -> None:
    from syte.platform import store
    from syte import platform_api

    store._column_cache.clear()
    await store.init_platform_db()
    bootstrap = await store.ensure_bootstrap()
    node = await store.insert(
        "platform_servers",
        {
            "team_uuid": bootstrap["team"]["uuid"],
            "name": "beta-web-01",
            "ip": "192.0.2.44",
            "status": "ready",
            "is_reachable": True,
            "is_usable": True,
            "server_type": "micro",
            "role_websites": True,
            "role_router": True,
            "role_workers": False,
            "load_balancing_enabled": True,
            "load_balancing_weight": 125,
        },
    )
    await store.record_server_metrics(
        node["uuid"],
        {"cpu_percent": 31.5, "memory_percent": 67.2, "disk_percent": 44.0, "container_count": 3},
    )

    snapshot = await platform_api._fleet_snapshot()
    reported = next(item for item in snapshot["nodes"] if item["uuid"] == node["uuid"])

    assert reported["status"] == "online"
    assert reported["load_percent"] == 67.2
    assert reported["availability_percent"] == 32.8
    assert reported["metrics"]["container_count"] == 3
    assert snapshot["load_balancer"]["enabled"] is False
    assert snapshot["summary"]["router_nodes"] >= 1


@pytest.mark.asyncio
async def test_fleet_policy_and_enrollment_secret_round_trip(tmp_data_dir: Path) -> None:
    from syte.platform import store
    from syte import platform_api

    store._column_cache.clear()
    await store.init_platform_db()
    bootstrap = await store.ensure_bootstrap()
    policy = await platform_api._fleet_policy()
    assert policy["load_balancing_enabled"] is False

    updated = await store.update(
        "platform_fleet_policies",
        policy["uuid"],
        {"load_balancing_enabled": True, "strategy": "round-robin", "health_check_path": "/ready"},
    )
    assert updated is not None
    assert updated["load_balancing_enabled"] is True
    assert updated["strategy"] == "round-robin"

    server = await store.insert(
        "platform_servers",
        {
            "team_uuid": bootstrap["team"]["uuid"],
            "name": "worker-01",
            "ip": "192.0.2.45",
            "server_type": "build",
            "role_websites": False,
            "role_workers": True,
            "enrollment_token": "test-enrollment-token-is-long-enough",
        },
    )
    assert server["server_type"] == "build"
    assert server["role_workers"] is True
    assert (await store.get("platform_servers", server["uuid"], include_secrets=True))["enrollment_token"]
    safe_server = await store.get("platform_servers", server["uuid"])
    assert "enrollment_token" not in safe_server
    assert safe_server["enrollment_token_set"] is True


@pytest.mark.asyncio
async def test_fleet_helper_script_and_heartbeat_are_node_scoped(tmp_data_dir: Path) -> None:
    from syte.platform import store
    from syte import platform_api

    store._column_cache.clear()
    await store.init_platform_db()
    bootstrap = await store.ensure_bootstrap()
    server = await store.insert(
        "platform_servers",
        {
            "team_uuid": bootstrap["team"]["uuid"],
            "name": "edge-01",
            "ip": "192.0.2.46",
            "role_websites": True,
            "role_router": True,
            "enrollment_token": "enrollment-token-for-the-edge-node-123456",
        },
    )

    helper = await platform_api.fleet_setup_script(server["uuid"])
    assert helper["filename"] == "syte-fleet-heartbeat.sh"
    assert server["uuid"] in helper["script"]
    assert "syte-fleet-heartbeat.timer" in helper["script"]
    assert "enrollment-token-for-the-edge-node-123456" in helper["script"]

    response = await platform_api.fleet_heartbeat(
        server["uuid"],
        platform_api.FleetHeartbeatRequest(
            token="enrollment-token-for-the-edge-node-123456",
            cpu_percent=17.5,
            memory_percent=41.0,
            disk_percent=29.0,
            container_count=2,
        ),
    )
    assert response["ok"] is True
    latest = (await store.server_metrics(server["uuid"], limit=1))[-1]
    assert latest["memory_percent"] == 41.0
    refreshed = await store.get("platform_servers", server["uuid"])
    assert refreshed["is_reachable"] is True

    with pytest.raises(Exception):
        await platform_api.fleet_heartbeat(
            server["uuid"],
            platform_api.FleetHeartbeatRequest(token="x" * 24),
        )
