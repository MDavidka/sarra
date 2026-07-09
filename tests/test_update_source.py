"""Tests for Syte update source resolution."""

from pathlib import Path

import pytest

from syte.update_source import (
    UpdateTarget,
    fetch_latest_open_pr,
    parse_github_repo,
    resolve_update_target,
)


def test_parse_github_repo_https() -> None:
    assert parse_github_repo("https://github.com/MDavidka/sarra.git") == "MDavidka/sarra"


def test_parse_github_repo_ssh() -> None:
    assert parse_github_repo("git@github.com:MDavidka/sarra.git") == "MDavidka/sarra"


def test_fetch_latest_open_pr_picks_newest_non_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeResponse:
        status_code = 200

        def json(self):
            return [
                {
                    "number": 9,
                    "title": "Draft work",
                    "draft": True,
                    "head": {"ref": "cursor/draft"},
                    "html_url": "https://github.com/o/r/pull/9",
                },
                {
                    "number": 10,
                    "title": "AI tab fixes",
                    "draft": False,
                    "head": {"ref": "cursor/ai-dashboard-redesign-6cbf"},
                    "html_url": "https://github.com/o/r/pull/10",
                },
            ]

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def get(self, url, params=None, headers=None):
            return FakeResponse()

    monkeypatch.setattr("syte.update_source.httpx.Client", FakeClient)
    target = fetch_latest_open_pr("MDavidka/sarra")
    assert target is not None
    assert target.pr_number == 10
    assert target.branch == "cursor/ai-dashboard-redesign-6cbf"
    assert "PR #10" in target.label


def test_resolve_update_target_uses_latest_pr(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "syte"
    install_dir.mkdir()
    (install_dir / ".git").mkdir()

    monkeypatch.delenv("SYTE_UPDATE_BRANCH", raising=False)
    monkeypatch.delenv("SYTE_UPDATE_PR", raising=False)
    monkeypatch.setattr(
        "syte.update_source.git_remote_repo",
        lambda _dir: "MDavidka/sarra",
    )
    monkeypatch.setattr(
        "syte.update_source.fetch_latest_open_pr",
        lambda _repo: UpdateTarget(
            source_type="pr",
            branch="cursor/update-from-latest-pr-6cbf",
            label="PR #11: update from PR",
            pr_number=11,
            repo="MDavidka/sarra",
        ),
    )

    target = resolve_update_target(install_dir)
    assert target.source_type == "pr"
    assert target.branch == "cursor/update-from-latest-pr-6cbf"
    assert target.pr_number == 11


def test_resolve_update_target_falls_back_to_main(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "syte"
    install_dir.mkdir()

    monkeypatch.delenv("SYTE_UPDATE_BRANCH", raising=False)
    monkeypatch.delenv("SYTE_UPDATE_PR", raising=False)
    monkeypatch.setattr("syte.update_source.git_remote_repo", lambda _dir: "MDavidka/sarra")
    monkeypatch.setattr("syte.update_source.fetch_latest_open_pr", lambda _repo: None)

    target = resolve_update_target(install_dir)
    assert target.branch == "main"
    assert "no open PRs" in target.label


def test_resolve_update_target_respects_branch_override(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_dir = tmp_path / "syte"
    install_dir.mkdir()
    monkeypatch.setenv("SYTE_UPDATE_BRANCH", "production")
    target = resolve_update_target(install_dir)
    assert target.branch == "production"
    assert target.source_type == "branch"
