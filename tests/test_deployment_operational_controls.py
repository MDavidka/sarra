"""Regression coverage for automatic branch deployment settings and rollback controls."""
from __future__ import annotations

import pytest
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_auto_deploy_binds_only_a_connected_account(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import main

    saved: list[dict[str, object]] = []

    async def fake_project(project_id: str):
        return {
            "id": project_id,
            "name": "web",
            "git_url": "https://github.com/acme/web.git",
            "github_account_id": "",
        }

    async def fake_summary(account_id: str):
        assert account_id == "operator-1"
        return {"connected": True, "login": "acme"}

    async def fake_update(project_id: str, values: dict[str, object]):
        saved.append(values)
        return {"id": project_id, **values}

    async def no_event(*_args, **_kwargs):
        return None

    monkeypatch.setattr(main, "get_project", fake_project)
    monkeypatch.setattr(main, "_enrich", lambda value: value)
    monkeypatch.setattr(main, "update_project", fake_update)
    monkeypatch.setattr("syte.github_oauth.connection_summary", fake_summary)
    monkeypatch.setattr("syte.notifications.publish_project_event", no_event)

    result = await main.api_deployment_config(
        "project-1",
        main.DeploymentConfigRequest(auto_deploy=True),
        _operator={"id": "operator-1", "auth": "account"},
    )

    assert result["ok"] is True
    assert saved == [{
        "auto_deploy": 1,
        "github_account_id": "operator-1",
        "last_seen_git_commit": "",
    }]


@pytest.mark.asyncio
async def test_auto_deploy_rejects_unbound_gui_session(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import main

    async def fake_project(project_id: str):
        return {"id": project_id, "git_url": "https://github.com/acme/web.git", "github_account_id": ""}

    monkeypatch.setattr(main, "get_project", fake_project)

    with pytest.raises(HTTPException) as error:
        await main.api_deployment_config(
            "project-1",
            main.DeploymentConfigRequest(auto_deploy=True),
            _operator={"id": "gui-session", "auth": "session"},
        )

    assert error.value.status_code == 409
    assert "Sign in" in str(error.value.detail)


@pytest.mark.asyncio
async def test_rollback_reissues_the_successful_recorded_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import main

    queued: list[dict[str, str]] = []

    async def fake_project(project_id: str):
        return {"id": project_id, "name": "web", "port": 3000}

    async def fake_run(run_id: str):
        return {"id": run_id, "project_id": "project-1", "status": "succeeded", "commit_sha": "a" * 40}

    async def fake_issue(project_id: str, *, trigger: str, commit_sha: str):
        queued.append({"project_id": project_id, "trigger": trigger, "commit_sha": commit_sha})
        return {"id": project_id, "name": "web", "port": 3000}, "Deploy issued"

    monkeypatch.setattr(main, "get_project", fake_project)
    monkeypatch.setattr(main, "_enrich", lambda value: value)
    monkeypatch.setattr(main, "get_deployment_run", fake_run)
    monkeypatch.setattr(main.deployment, "issue_deploy", fake_issue)

    result = await main.api_rollback_deployment("project-1", "run-1", _operator={"id": "operator-1"})

    assert result["ok"] is True
    assert result["commit_sha"] == "a" * 40
    assert queued == [{"project_id": "project-1", "trigger": "rollback:run-1", "commit_sha": "a" * 40}]
