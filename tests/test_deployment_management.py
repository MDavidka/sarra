from pathlib import Path

import pytest

from syte.config import settings
from syte.database import (
    create_deployment_run,
    create_project,
    init_db,
    list_deployment_runs,
    update_deployment_run,
    update_project,
)


@pytest.mark.asyncio
async def test_deployment_history_and_project_limits(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    monkeypatch.setattr(settings, "workspaces_dir", tmp_path / "workspaces")
    monkeypatch.setattr(settings, "db_path", tmp_path / "syte.db")
    await init_db()
    await create_project({"id": "demo", "name": "Demo", "port": 3010})
    updated = await update_project(
        "demo",
        {
            "deploy_type": "docker",
            "resource_memory": "512m",
            "resource_cpus": "0.5",
            "healthcheck_path": "/health",
            "auto_deploy": 1,
        },
    )
    assert updated["resource_memory"] == "512m"
    assert updated["resource_cpus"] == "0.5"
    assert updated["healthcheck_path"] == "/health"
    assert updated["auto_deploy"] == 1

    run = await create_deployment_run("demo", trigger="manual")
    await update_deployment_run(run["id"], {"status": "succeeded", "duration_ms": 1200})
    rows = await list_deployment_runs("demo")
    assert rows[0]["id"] == run["id"]
    assert rows[0]["status"] == "succeeded"
    assert rows[0]["duration_ms"] == 1200
