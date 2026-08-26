"""Regression coverage for periodic branch deployment decisions and diagnostics."""
from __future__ import annotations

import pytest


@pytest.mark.asyncio
async def test_new_branch_commit_is_recorded_and_queues_one_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import branch_deploy

    updates: list[dict[str, str]] = []
    issued: list[dict[str, str]] = []

    async def fake_head(account_id: str, repository: str, branch: str) -> str:
        assert account_id == "operator-1"
        assert repository == "acme/web"
        assert branch == "main"
        return "b" * 40

    async def fake_update(project_id: str, values: dict[str, str]):
        assert project_id == "web-1"
        updates.append(values)
        return {"id": project_id, **values}

    async def fake_issue(project_id: str, *, trigger: str, commit_sha: str):
        issued.append({"project_id": project_id, "trigger": trigger, "commit_sha": commit_sha})
        return {"id": project_id}, "queued"

    monkeypatch.setattr(branch_deploy, "branch_head", fake_head)
    monkeypatch.setattr(branch_deploy, "update_project", fake_update)
    monkeypatch.setattr("syte.deployment.issue_deploy", fake_issue)

    result = await branch_deploy.check_project_branch({
        "id": "web-1", "auto_deploy": 1, "git_url": "https://github.com/acme/web.git",
        "branch": "main", "github_account_id": "operator-1", "last_seen_git_commit": "a" * 40,
        "last_deployed_commit": "a" * 40,
    })

    assert result == "queued"
    assert updates == [{"last_seen_git_commit": "b" * 40}]
    assert issued == [{"project_id": "web-1", "trigger": "periodic-branch:main", "commit_sha": "b" * 40}]


@pytest.mark.asyncio
async def test_current_branch_commit_does_not_queue_duplicate_deploy(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import branch_deploy

    async def fake_head(*_args: str) -> str:
        return "a" * 40

    monkeypatch.setattr(branch_deploy, "branch_head", fake_head)
    result = await branch_deploy.check_project_branch({
        "id": "web-1", "auto_deploy": True, "git_url": "https://github.com/acme/web.git",
        "branch": "main", "github_account_id": "operator-1", "last_seen_git_commit": "a" * 40,
        "last_deployed_commit": "a" * 40,
    })
    assert result == "current"


def test_deployment_failure_reasons_are_actionable():
    from syte.deployment import _deployment_failure_reason

    assert "start command" in _deployment_failure_reason("No start command configured.").lower()
    assert "port" in _deployment_failure_reason("bind: address already in use").lower()
    assert "source checkout" in _deployment_failure_reason("Cloning repository failed").lower()


@pytest.mark.asyncio
async def test_new_branch_commit_is_deferred_without_marking_it_seen_when_deploy_is_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    from syte import branch_deploy

    updates: list[dict[str, str]] = []

    async def fake_head(*_args: str) -> str:
        return "b" * 40

    async def fake_update(_project_id: str, values: dict[str, str]):
        updates.append(values)
        return values

    async def busy_issue(*_args, **_kwargs):
        return {"id": "web-1"}, "Deploy already in progress for web-1."

    monkeypatch.setattr(branch_deploy, "branch_head", fake_head)
    monkeypatch.setattr(branch_deploy, "update_project", fake_update)
    monkeypatch.setattr("syte.deployment.issue_deploy", busy_issue)

    result = await branch_deploy.check_project_branch({
        "id": "web-1", "auto_deploy": 1, "git_url": "https://github.com/acme/web.git",
        "branch": "main", "github_account_id": "operator-1", "last_seen_git_commit": "a" * 40,
        "last_deployed_commit": "a" * 40,
    })

    assert result == "deferred"
    assert updates == []
