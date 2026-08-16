"""Tests for git repository identity matching and webhook verification."""

from __future__ import annotations

import hashlib
import hmac

import pytest

from syte.platform.git_sources import (
    branch_matches,
    normalize_repo,
    provider_for_repo,
    repo_matches,
    verify_gitea_signature,
    verify_github_signature,
    verify_gitlab_token,
    verify_webhook,
    watch_paths_match,
)
from syte.platform.types import GitProvider

# --------------------------------------------------------------------------- #
# normalize_repo
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://github.com/Acme/Web", "github.com/acme/web"),
        ("https://github.com/acme/web/", "github.com/acme/web"),
        ("https://github.com/acme/web.git", "github.com/acme/web"),
        ("http://github.com/acme/web", "github.com/acme/web"),
        ("git@github.com:Acme/Web.git", "github.com/acme/web"),
        ("ssh://git@github.com/acme/web.git", "github.com/acme/web"),
        ("git@gitlab.example.com:group/sub/proj.git", "gitlab.example.com/group/sub/proj"),
        ("acme/web", "acme/web"),
        ("Acme/Web.git", "acme/web"),
        # Embedded credentials must not survive normalisation.
        ("https://user:token@github.com/acme/web.git", "github.com/acme/web"),
    ],
)
def test_normalize_repo(raw: str, expected: str) -> None:
    assert normalize_repo(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "https://github.com/", "git@github.com:"])
def test_normalize_repo_returns_empty_for_unusable_input(raw: str) -> None:
    assert normalize_repo(raw) == ""


# --------------------------------------------------------------------------- #
# repo_matches
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("stored", "incoming"),
    [
        ("https://github.com/acme/web", "acme/web"),
        ("git@github.com:acme/web.git", "https://github.com/acme/web"),
        ("https://github.com/Acme/Web.git", "github.com/acme/web"),
        ("acme/web", "acme/web"),
        ("https://github.com/acme/web", "https://github.com/acme/web.git"),
    ],
)
def test_repo_matches_across_url_forms(stored: str, incoming: str) -> None:
    assert repo_matches(stored, incoming)


def test_repo_matches_requires_host_agreement_when_both_qualified() -> None:
    """A GitLab push must not deploy an app tracking the GitHub repo."""
    assert not repo_matches(
        "https://github.com/acme/web", "https://gitlab.com/acme/web"
    )


def test_repo_matches_rejects_different_repositories() -> None:
    assert not repo_matches("acme/web", "acme/api")
    assert not repo_matches("acme/web", "other/web")


@pytest.mark.parametrize(("stored", "incoming"), [("", "acme/web"), ("acme/web", ""), ("", "")])
def test_repo_matches_is_false_for_missing_input(stored: str, incoming: str) -> None:
    assert not repo_matches(stored, incoming)


def test_provider_for_repo_classifies_known_hosts() -> None:
    assert provider_for_repo("https://github.com/a/b") is GitProvider.GITHUB
    assert provider_for_repo("git@gitlab.com:a/b.git") is GitProvider.GITLAB
    assert provider_for_repo("https://bitbucket.org/a/b") is GitProvider.BITBUCKET
    assert provider_for_repo("https://codeberg.org/a/b") is GitProvider.GITEA
    assert provider_for_repo("https://git.internal/a/b") is GitProvider.OTHER


# --------------------------------------------------------------------------- #
# branch_matches
# --------------------------------------------------------------------------- #


def test_branch_matches_strips_refs_prefix() -> None:
    """Push payloads carry refs/heads/main while the app stores main."""
    assert branch_matches("main", "refs/heads/main")
    assert branch_matches("refs/heads/main", "main")
    assert branch_matches("release/v2", "refs/heads/release/v2")


def test_branch_matches_rejects_other_branches() -> None:
    assert not branch_matches("main", "refs/heads/develop")
    assert not branch_matches("main", "")
    assert not branch_matches("", "")


# --------------------------------------------------------------------------- #
# watch_paths_match
# --------------------------------------------------------------------------- #


def test_no_watch_paths_means_watch_everything() -> None:
    assert watch_paths_match("", ("anything.txt",))
    assert watch_paths_match("   ", ("anything.txt",))


def test_watch_paths_fails_open_when_no_file_list_reported() -> None:
    """Suppressing a real deployment is worse than a redundant one."""
    assert watch_paths_match("apps/web/**", ())


@pytest.mark.parametrize(
    ("patterns", "files", "expected"),
    [
        ("apps/web/**", ("apps/web/src/index.ts",), True),
        ("apps/web", ("apps/web/src/index.ts",), True),
        ("apps/web/**", ("apps/api/src/index.ts",), False),
        ("*.md", ("README.md",), True),
        ("*.md", ("src/main.go",), False),
        ("apps/web/**\napps/shared/**", ("apps/shared/util.ts",), True),
        ("apps/web/**,apps/shared/**", ("apps/shared/util.ts",), True),
        ("apps/web/**", ("./apps/web/x.ts",), True),
    ],
)
def test_watch_paths_match(patterns: str, files: tuple[str, ...], expected: bool) -> None:
    assert watch_paths_match(patterns, files) is expected


# --------------------------------------------------------------------------- #
# Signature verification
# --------------------------------------------------------------------------- #

SECRET = "s3cr3t"
BODY = b'{"ref":"refs/heads/main"}'


def github_header(secret: str = SECRET, body: bytes = BODY) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_github_signature_accepts_valid_and_rejects_tampered() -> None:
    assert verify_github_signature(SECRET, BODY, github_header())
    assert not verify_github_signature(SECRET, BODY + b" ", github_header())
    assert not verify_github_signature("wrong", BODY, github_header())


@pytest.mark.parametrize(
    "header",
    ["", "sha1=abc", "abc", "sha256=", "sha256"],
)
def test_github_signature_rejects_malformed_headers(header: str) -> None:
    assert not verify_github_signature(SECRET, BODY, header)


def test_github_signature_requires_a_secret() -> None:
    assert not verify_github_signature("", BODY, github_header())


def test_gitlab_token_is_compared_verbatim() -> None:
    assert verify_gitlab_token(SECRET, SECRET)
    assert verify_gitlab_token(SECRET, f"  {SECRET}  ")
    assert not verify_gitlab_token(SECRET, "nope")
    assert not verify_gitlab_token("", SECRET)


def test_gitea_signature_is_a_bare_hex_digest() -> None:
    digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    assert verify_gitea_signature(SECRET, BODY, digest)
    assert not verify_gitea_signature(SECRET, BODY, "sha256=" + digest)


# --------------------------------------------------------------------------- #
# verify_webhook dispatch
# --------------------------------------------------------------------------- #


def test_verify_webhook_dispatches_per_provider() -> None:
    ok, _ = verify_webhook(GitProvider.GITHUB, SECRET, BODY, {"X-Hub-Signature-256": github_header()})
    assert ok

    ok, _ = verify_webhook(GitProvider.GITLAB, SECRET, BODY, {"X-Gitlab-Token": SECRET})
    assert ok

    digest = hmac.new(SECRET.encode(), BODY, hashlib.sha256).hexdigest()
    ok, _ = verify_webhook(GitProvider.GITEA, SECRET, BODY, {"X-Gitea-Signature": digest})
    assert ok


def test_verify_webhook_header_lookup_is_case_insensitive() -> None:
    """ASGI servers and proxies disagree about header casing."""
    ok, _ = verify_webhook(
        GitProvider.GITHUB, SECRET, BODY, {"x-hub-signature-256": github_header()}
    )
    assert ok


def test_verify_webhook_without_secret_explains_the_fix() -> None:
    ok, message = verify_webhook(GitProvider.GITHUB, "", BODY, {})
    assert not ok
    assert "Webhooks tab" in message


def test_verify_webhook_reports_the_wrong_signature() -> None:
    ok, message = verify_webhook(GitProvider.GITHUB, SECRET, BODY, {"X-Hub-Signature-256": "sha256=bad"})
    assert not ok
    assert "X-Hub-Signature-256" in message


def test_bitbucket_is_accepted_because_it_does_not_sign() -> None:
    ok, message = verify_webhook(GitProvider.BITBUCKET, SECRET, BODY, {})
    assert ok
    assert "does not sign" in message


def test_unknown_provider_accepts_github_style_signature() -> None:
    """Keeps a Forgejo/Gogs instance working."""
    ok, _ = verify_webhook("something-else", SECRET, BODY, {"X-Hub-Signature-256": github_header()})
    assert ok
    ok, message = verify_webhook("something-else", SECRET, BODY, {})
    assert not ok
    assert "No recognised signature header" in message
