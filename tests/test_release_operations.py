from __future__ import annotations

from pathlib import Path

import pytest

from syte.config import settings


@pytest.mark.asyncio
async def test_release_workspace_defaults_and_protected_approval_lifecycle(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")

    from syte import database, release_operations

    await database.init_db()
    await database.create_project({
        "id": "release-project",
        "name": "Release project",
        "git_url": "https://github.com/example/release-project.git",
        "branch": "main",
        "port": 9180,
        "domain": "app.example.test",
    })

    workspace = await release_operations.workspace("release-project")
    assert [item["kind"] for item in workspace["environments"]] == ["production", "staging", "preview"]
    production = workspace["environments"][0]
    assert production["require_approval"] is True
    assert workspace["policy"]["deployment_strategy"] == "rolling"
    assert set(workspace["host_metrics"]) == {"cpu_percent", "ram_percent", "disk_percent"}

    approval = await release_operations.request_approval("release-project", production["id"], "owner@example.test", "Ready for review")
    assert approval["status"] == "pending"
    decided = await release_operations.decide_approval("release-project", approval["id"], "owner@example.test", True)
    assert decided and decided["status"] == "approved"
    assert await release_operations.has_approved_release("release-project", production["id"])
    assert await release_operations.consume_approved_release("release-project", production["id"])
    assert not await release_operations.has_approved_release("release-project", production["id"])


@pytest.mark.asyncio
async def test_release_policy_team_recovery_and_event_records(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")

    from syte import database, release_operations

    await database.init_db()
    await database.create_project({"id": "ops-project", "name": "Ops", "port": 9181})
    policy = await release_operations.update_policy("ops-project", {
        "deployment_strategy": "canary",
        "canary_percent": 20,
        "backup_enabled": True,
        "backup_schedule": "weekly",
        "backup_retention_days": 21,
        "resource_alert_percent": 90,
    })
    assert policy["deployment_strategy"] == "canary"
    assert policy["backup_enabled"] is True
    assert policy["backup_schedule"] == "weekly"

    member = await release_operations.upsert_team_member("ops-project", "deployer@example.test", "Deployer", "deployer")
    assert member["role"] == "deployer"
    assert (await release_operations.list_team_members("ops-project"))[0]["email"] == "deployer@example.test"

    workspace = data_dir / "workspaces" / "ops-project" / "data"
    workspace.mkdir(parents=True)
    (workspace / "state.txt").write_text("durable-state", encoding="utf-8")
    point = await release_operations.create_workspace_backup("ops-project", "Before platform update")
    assert point["status"] == "available"
    assert Path(point["artifact_path"]).is_file()
    verified = await release_operations.verify_restore_point("ops-project", point["id"])
    assert verified and verified["status"] == "verified"
    events = await release_operations.list_events("ops-project")
    assert any(event["event_type"] == "recovery.restore_point_verified" for event in events)


@pytest.mark.asyncio
async def test_release_policy_rejects_unsafe_values(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "workspaces_dir", data_dir / "workspaces")
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")

    from syte import database, release_operations

    await database.init_db()
    await database.create_project({"id": "safe-project", "name": "Safe", "port": 9182})
    with pytest.raises(ValueError, match="Deployment strategy"):
        await release_operations.update_policy("safe-project", {"deployment_strategy": "unsafe"})
    with pytest.raises(ValueError, match="Role must"):
        await release_operations.upsert_team_member("safe-project", "person@example.test", "Person", "root")
