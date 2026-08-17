"""Tests for the platform persistence layer.

Follows the existing store-test convention (see ``tests/test_settings.py``): a
``tmp_data_dir`` fixture monkeypatches ``settings`` so every test gets its own
SQLite file, and ``syte.platform`` modules are imported at module scope only for
pure helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import pytest_asyncio

from syte.config import settings
from syte.platform.types import ResourceType


@pytest.fixture
def tmp_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "syte-data"
    monkeypatch.setattr(settings, "data_dir", data_dir)
    monkeypatch.setattr(settings, "db_path", data_dir / "syte.db")
    return data_dir


# `pytest_asyncio.fixture` is required rather than plain `pytest.fixture`: the
# repo has no conftest.py and no asyncio_mode setting, so pytest-asyncio runs in
# strict mode and would not await a bare async fixture.
@pytest_asyncio.fixture
async def bootstrapped(tmp_data_dir: Path) -> dict:
    from syte.platform import store

    # The column cache is process-global; a previous test's schema must not leak.
    store._column_cache.clear()
    await store.init_platform_db()
    return await store.ensure_bootstrap()


# --------------------------------------------------------------------------- #
# Schema and bootstrap
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_init_is_idempotent(tmp_data_dir: Path) -> None:
    from syte.platform import store

    store._column_cache.clear()
    await store.init_platform_db()
    await store.init_platform_db()  # migrations must tolerate a second pass
    assert await store.count("platform_servers") == 0


@pytest.mark.asyncio
async def test_bootstrap_creates_a_usable_default_hierarchy(bootstrapped: dict) -> None:
    """An operator must be able to create a resource with no setup wizard."""
    assert bootstrapped["team"]["personal_team"] is True
    assert bootstrapped["server"]["is_local"] is True
    assert bootstrapped["server"]["status"] == "reachable"
    assert bootstrapped["destination"]["network"] == "syte"
    assert bootstrapped["environment"]["name"] == "production"


@pytest.mark.asyncio
async def test_bootstrap_is_idempotent(bootstrapped: dict) -> None:
    from syte.platform import store

    again = await store.ensure_bootstrap()
    for key in ("team", "server", "destination", "project", "environment"):
        assert again[key]["uuid"] == bootstrapped[key]["uuid"], key
    assert await store.count("platform_servers") == 1


@pytest.mark.asyncio
async def test_sqlite_keyword_columns_round_trip(bootstrapped: dict) -> None:
    """`commit`, `trigger`, `user` and `version` are SQLite keywords."""
    from syte.platform import store

    deployment = await store.insert(
        "platform_deployments",
        {"resource_uuid": "r1", "commit": "deadbeef", "trigger": "webhook"},
    )
    assert deployment["commit"] == "deadbeef"
    assert deployment["trigger"] == "webhook"
    assert bootstrapped["server"]["user"] == "root"


# --------------------------------------------------------------------------- #
# Generic CRUD
# --------------------------------------------------------------------------- #


@pytest_asyncio.fixture
async def application(bootstrapped: dict) -> dict:
    from syte.platform import store

    return await store.insert(
        "platform_applications",
        {
            "environment_uuid": bootstrapped["environment"]["uuid"],
            "server_uuid": bootstrapped["server"]["uuid"],
            "name": "web",
            "git_repository": "https://github.com/acme/web",
            "docker_compose_domains": {"web": "https://web.test"},
            "http_basic_auth_password": "hunter2",
        },
    )


@pytest.mark.asyncio
async def test_insert_generates_uuid_and_timestamps(application: dict) -> None:
    assert application["uuid"]
    assert application["created_at"]
    assert application["updated_at"]


@pytest.mark.asyncio
async def test_integer_columns_decode_as_booleans(application: dict) -> None:
    assert application["auto_deploy"] is True
    assert application["rolling_update_enabled"] is False


@pytest.mark.asyncio
async def test_json_columns_round_trip_as_objects(application: dict) -> None:
    assert application["docker_compose_domains"] == {"web": "https://web.test"}


@pytest.mark.asyncio
async def test_secrets_are_stripped_by_default(application: dict) -> None:
    from syte.platform import store

    safe = await store.get("platform_applications", application["uuid"])
    assert "http_basic_auth_password" not in safe
    # Presence is still reported — the UI needs to know a password is set.
    assert safe["http_basic_auth_password_set"] is True

    unsafe = await store.get(
        "platform_applications", application["uuid"], include_secrets=True
    )
    assert unsafe["http_basic_auth_password"] == "hunter2"


@pytest.mark.asyncio
async def test_update_ignores_unknown_columns(application: dict) -> None:
    from syte.platform import store

    updated = await store.update(
        "platform_applications",
        application["uuid"],
        {"status": "running", "definitely_not_a_column": "x"},
    )
    assert updated["status"] == "running"
    assert "definitely_not_a_column" not in updated


@pytest.mark.asyncio
async def test_update_refuses_to_rewrite_identity_columns(application: dict) -> None:
    from syte.platform import store

    updated = await store.update(
        "platform_applications",
        application["uuid"],
        {"uuid": "hijacked", "created_at": "1999-01-01"},
    )
    assert updated["uuid"] == application["uuid"]
    assert updated["created_at"] == application["created_at"]


@pytest.mark.asyncio
async def test_update_returns_none_for_missing_row(bootstrapped: dict) -> None:
    from syte.platform import store

    assert await store.update("platform_applications", "nope", {"status": "x"}) is None


@pytest.mark.asyncio
async def test_find_supports_in_clauses_and_empty_sets(bootstrapped: dict) -> None:
    from syte.platform import store

    for status in ("queued", "finished", "failed"):
        await store.insert("platform_deployments", {"resource_uuid": "r1", "status": status})

    found = await store.find("platform_deployments", {"status": ["queued", "failed"]})
    assert {row["status"] for row in found} == {"queued", "failed"}
    # An empty IN () is a contradiction, not "match everything".
    assert await store.find("platform_deployments", {"status": []}) == []


@pytest.mark.asyncio
async def test_unknown_table_is_rejected(bootstrapped: dict) -> None:
    """Table names are interpolated into SQL, so they must be whitelisted."""
    from syte.platform import store

    with pytest.raises(ValueError, match="Unknown platform table"):
        await store.find("sqlite_master")


@pytest.mark.asyncio
async def test_order_by_injection_falls_back_to_default(bootstrapped: dict) -> None:
    from syte.platform import store

    rows = await store.find("platform_servers", order_by="name; DROP TABLE platform_servers")
    assert len(rows) == 1
    # Proves the table survived.
    assert await store.count("platform_servers") == 1


@pytest.mark.asyncio
async def test_delete_where_refuses_an_unfiltered_wipe(bootstrapped: dict) -> None:
    from syte.platform import store

    with pytest.raises(ValueError, match="at least one condition"):
        await store.delete_where("platform_servers", {})


# --------------------------------------------------------------------------- #
# Environment variables
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_env_var_upsert_replaces_by_key(application: dict) -> None:
    from syte.platform import store

    await store.bulk_upsert_env_vars(
        application["uuid"],
        ResourceType.APPLICATION,
        [
            {"key": "API_URL", "value": "one", "is_build_time": True},
            {"key": "TOKEN", "value": "secret", "is_secret": True},
            {"key": "API_URL", "value": "two"},
            {"key": "", "value": "ignored"},
        ],
    )
    variables = {row["key"]: row for row in await store.env_vars_for(application["uuid"])}
    assert set(variables) == {"API_URL", "TOKEN"}
    assert variables["API_URL"]["value"] == "two"
    # A partial upsert must not clear flags the caller did not mention.
    assert variables["API_URL"]["is_build_time"] is True
    assert variables["TOKEN"]["is_secret"] is True


@pytest.mark.asyncio
async def test_preview_env_vars_are_a_separate_namespace(application: dict) -> None:
    from syte.platform import store

    await store.upsert_env_var(
        application["uuid"], ResourceType.APPLICATION, "API_URL", "prod"
    )
    await store.upsert_env_var(
        application["uuid"], ResourceType.APPLICATION, "API_URL", "preview", is_preview=True
    )
    assert len(await store.env_vars_for(application["uuid"])) == 1
    preview = await store.env_vars_for(application["uuid"], is_preview=True)
    assert preview[0]["value"] == "preview"


# --------------------------------------------------------------------------- #
# Deployments
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_deployment_log_append_is_atomic(bootstrapped: dict) -> None:
    from syte.platform import store

    deployment = await store.insert("platform_deployments", {"resource_uuid": "r1"})
    await store.append_deployment_logs(deployment["uuid"], "first")
    await store.append_deployment_logs(deployment["uuid"], "second\n")
    await store.append_deployment_logs(deployment["uuid"], "")

    reloaded = await store.get("platform_deployments", deployment["uuid"])
    assert reloaded["logs"] == "first\nsecond\n"


@pytest.mark.asyncio
async def test_active_deployments_are_fifo(bootstrapped: dict) -> None:
    from syte.platform import store

    first = await store.insert(
        "platform_deployments", {"resource_uuid": "r1", "status": "queued"}
    )
    await store.insert("platform_deployments", {"resource_uuid": "r2", "status": "in_progress"})
    await store.insert("platform_deployments", {"resource_uuid": "r3", "status": "finished"})

    active = await store.active_deployments()
    assert len(active) == 2
    assert active[0]["uuid"] == first["uuid"]


@pytest.mark.asyncio
async def test_running_deployment_count_gates_concurrency(bootstrapped: dict) -> None:
    from syte.platform import store

    server = bootstrapped["server"]["uuid"]
    for status in ("in_progress", "in_progress", "queued", "finished"):
        await store.insert(
            "platform_deployments",
            {"resource_uuid": "r1", "server_uuid": server, "status": status},
        )
    assert await store.running_deployment_count(server) == 2


@pytest.mark.asyncio
async def test_rollback_candidates_dedupe_tags_and_skip_untagged(bootstrapped: dict) -> None:
    """A deployment with no image tag cannot be rolled back to."""
    from syte.platform import store

    for tag in ("v1", "v2", "v2", ""):
        await store.insert(
            "platform_deployments",
            {"resource_uuid": "r1", "status": "finished", "image_tag": tag},
        )
    candidates = await store.rollback_candidates("r1")
    assert [row["image_tag"] for row in candidates] == ["v2", "v1"]


@pytest.mark.asyncio
async def test_last_successful_deployment_ignores_preview_builds(bootstrapped: dict) -> None:
    from syte.platform import store

    await store.insert(
        "platform_deployments",
        {"resource_uuid": "r1", "status": "finished", "image_tag": "prod", "pull_request_id": 0},
    )
    await store.insert(
        "platform_deployments",
        {"resource_uuid": "r1", "status": "finished", "image_tag": "pr", "pull_request_id": 9},
    )
    assert (await store.last_successful_deployment("r1"))["image_tag"] == "prod"


# --------------------------------------------------------------------------- #
# Resource discovery and cascading deletes
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_get_resource_finds_type_without_being_told(application: dict) -> None:
    from syte.platform import store

    found = await store.get_resource(application["uuid"])
    assert found is not None
    resource_type, row = found
    assert resource_type is ResourceType.APPLICATION
    assert row["name"] == "web"
    assert await store.get_resource("does-not-exist") is None


@pytest.mark.asyncio
async def test_project_tree_reports_resource_counts(application: dict) -> None:
    from syte.platform import store

    tree = await store.list_projects_with_environments()
    assert len(tree) == 1
    environment = tree[0]["environments"][0]
    assert environment["counts"] == {"applications": 1, "databases": 0, "services": 0}
    assert environment["resources"][0]["name"] == "web"


@pytest.mark.asyncio
async def test_delete_resource_cascade_clears_children(application: dict) -> None:
    from syte.platform import store

    uuid = application["uuid"]
    await store.upsert_env_var(uuid, ResourceType.APPLICATION, "K", "v")
    await store.insert(
        "platform_volumes",
        {"resource_uuid": uuid, "resource_type": "application", "name": "d", "mount_path": "/d"},
    )
    task = await store.insert(
        "platform_scheduled_tasks",
        {
            "resource_uuid": uuid, "resource_type": "application",
            "name": "cron", "command": "echo", "frequency": "@daily",
        },
    )
    await store.insert("platform_task_executions", {"task_uuid": task["uuid"]})
    await store.insert("platform_deployments", {"resource_uuid": uuid})
    await store.insert("platform_previews", {"application_uuid": uuid, "pull_request_id": 3})

    assert await store.delete_resource_cascade(ResourceType.APPLICATION, uuid) is True

    assert await store.env_vars_for(uuid) == []
    assert await store.volumes_for(uuid) == []
    assert await store.scheduled_tasks_for(uuid) == []
    assert await store.count("platform_task_executions", {"task_uuid": task["uuid"]}) == 0
    assert await store.count("platform_deployments", {"resource_uuid": uuid}) == 0
    assert await store.count("platform_previews", {"application_uuid": uuid}) == 0
    assert await store.get_resource(uuid) is None


@pytest.mark.asyncio
async def test_database_cascade_clears_backup_history(bootstrapped: dict) -> None:
    from syte.platform import store

    database = await store.insert(
        "platform_databases",
        {
            "environment_uuid": bootstrapped["environment"]["uuid"],
            "server_uuid": bootstrapped["server"]["uuid"],
            "database_type": "postgresql", "name": "db",
            "image": "postgres:16-alpine", "internal_port": 5432,
        },
    )
    backup = await store.insert("platform_backups", {"database_uuid": database["uuid"]})
    await store.insert("platform_backup_executions", {"backup_uuid": backup["uuid"]})

    await store.delete_resource_cascade(ResourceType.DATABASE, database["uuid"])
    assert await store.count("platform_backups", {"database_uuid": database["uuid"]}) == 0
    assert await store.count("platform_backup_executions", {"backup_uuid": backup["uuid"]}) == 0


@pytest.mark.asyncio
async def test_environment_delete_blocked_while_not_empty(application: dict, bootstrapped: dict) -> None:
    """Matches Coolify's API contract: environments must be emptied first."""
    from syte.platform import store

    ok, message = await store.delete_environment_cascade(bootstrapped["environment"]["uuid"])
    assert ok is False
    assert "still holds 1 resource" in message

    await store.delete_resource_cascade(ResourceType.APPLICATION, application["uuid"])
    ok, message = await store.delete_environment_cascade(bootstrapped["environment"]["uuid"])
    assert ok is True


@pytest.mark.asyncio
async def test_project_delete_blocked_by_nested_resource(application: dict, bootstrapped: dict) -> None:
    from syte.platform import store

    ok, message = await store.delete_project_cascade(bootstrapped["project"]["uuid"])
    assert ok is False
    assert "production" in message

    await store.delete_resource_cascade(ResourceType.APPLICATION, application["uuid"])
    ok, _ = await store.delete_project_cascade(bootstrapped["project"]["uuid"])
    assert ok is True
    assert await store.count("platform_environments") == 0


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_metrics_are_returned_oldest_first_for_charting(bootstrapped: dict) -> None:
    from syte.platform import store

    server = bootstrapped["server"]["uuid"]
    for value in (10.0, 20.0, 30.0):
        await store.record_server_metrics(server, {"cpu_percent": value})

    samples = await store.server_metrics(server)
    assert [s["cpu_percent"] for s in samples] == [10.0, 20.0, 30.0]


@pytest.mark.asyncio
async def test_metrics_retention_trims_the_rolling_window(
    bootstrapped: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from syte.platform import store

    monkeypatch.setattr(store, "METRICS_RETENTION_ROWS", 5)
    server = bootstrapped["server"]["uuid"]
    for value in range(12):
        await store.record_server_metrics(server, {"cpu_percent": float(value)})

    samples = await store.server_metrics(server, limit=100)
    assert len(samples) == 5
    assert [s["cpu_percent"] for s in samples] == [7.0, 8.0, 9.0, 10.0, 11.0]
