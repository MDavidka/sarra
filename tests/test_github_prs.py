import pytest


@pytest.mark.asyncio
async def test_recent_mergeable_commits_returns_three_newest_mergeable_heads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from syte import github_prs

    monkeypatch.setattr(
        github_prs,
        "resolve_token",
        lambda: _async_value(("gh-token", "settings")),
    )
    monkeypatch.setattr(
        github_prs,
        "resolve_repo",
        lambda: _async_value("MDavidka/sarra"),
    )
    monkeypatch.setattr(
        github_prs,
        "list_open_prs",
        lambda repo, enrich=12: _async_value({
            "pull_requests": [
                {
                    "number": 1,
                    "title": "old",
                    "url": "https://github.com/MDavidka/sarra/pull/1",
                    "head_ref": "old",
                    "base_ref": "main",
                    "head_sha": "sha-old",
                    "mergeable": True,
                    "can_merge": True,
                },
                {
                    "number": 2,
                    "title": "blocked",
                    "url": "https://github.com/MDavidka/sarra/pull/2",
                    "head_ref": "blocked",
                    "base_ref": "main",
                    "head_sha": "sha-blocked",
                    "mergeable": False,
                    "can_merge": False,
                },
                {
                    "number": 3,
                    "title": "newest",
                    "url": "https://github.com/MDavidka/sarra/pull/3",
                    "head_ref": "newest",
                    "base_ref": "main",
                    "head_sha": "sha-newest",
                    "mergeable": True,
                    "can_merge": True,
                },
                {
                    "number": 4,
                    "title": "middle",
                    "url": "https://github.com/MDavidka/sarra/pull/4",
                    "head_ref": "middle",
                    "base_ref": "main",
                    "head_sha": "sha-middle",
                    "mergeable": True,
                    "can_merge": True,
                },
                {
                    "number": 5,
                    "title": "third",
                    "url": "https://github.com/MDavidka/sarra/pull/5",
                    "head_ref": "third",
                    "base_ref": "main",
                    "head_sha": "sha-third",
                    "mergeable": True,
                    "can_merge": True,
                },
            ]
        }),
    )

    commit_dates = {
        "sha-old": "2026-08-01T00:00:00Z",
        "sha-newest": "2026-08-05T00:00:00Z",
        "sha-middle": "2026-08-03T00:00:00Z",
        "sha-third": "2026-08-02T00:00:00Z",
    }

    async def fake_request(method, path, *, token, params=None, json_body=None):
        sha = path.rsplit("/", 1)[-1]
        return 200, {
            "sha": sha,
            "html_url": f"https://github.com/MDavidka/sarra/commit/{sha}",
            "commit": {
                "message": f"message for {sha}\nbody",
                "author": {"date": commit_dates[sha]},
                "committer": {"date": commit_dates[sha]},
            },
        }

    monkeypatch.setattr(github_prs, "_request", fake_request)

    commits = await github_prs.recent_mergeable_commits(limit=3)

    assert [commit["sha"] for commit in commits] == ["sha-new", "sha-mid", "sha-thi"]
    assert [commit["pr_number"] for commit in commits] == [3, 4, 5]
    assert all("\n" not in commit["message"] for commit in commits)


async def _async_value(value):
    return value
