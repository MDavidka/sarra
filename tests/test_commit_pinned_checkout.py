"""Regression coverage for commit-pinned Git checkouts used by automatic deploys."""
from __future__ import annotations

from pathlib import Path


def test_existing_repository_checkout_is_pinned_to_recorded_commit(monkeypatch, tmp_path: Path) -> None:
    from syte import workspace

    repo = tmp_path / "app"
    (repo / ".git").mkdir(parents=True)
    commands: list[tuple[str, ...]] = []

    def fake_workspace(_project_id: str) -> Path:
        return tmp_path

    def fake_command(command, **_kwargs):
        commands.append(tuple(command))
        return 0, "ok"

    monkeypatch.setattr(workspace, "ensure_workspace", fake_workspace)
    monkeypatch.setattr(workspace, "run_cmd", fake_command)

    ok, message = workspace.clone_or_pull(
        "project-1",
        "https://github.com/acme/web.git",
        "main",
        commit_sha="a" * 40,
    )

    assert ok is True
    assert "pinned" in message
    assert commands[0][-2:] == ("fetch", "origin")
    assert commands[1][-3:] == ("checkout", "--detach", "a" * 40)
    assert all("pull" not in command for command in commands)
