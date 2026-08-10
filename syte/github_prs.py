"""GitHub pull-request review, merge, and deploy support for the web GUI.

``update_source`` already resolves *which* ref a self-update should pull. This
module is the operator-facing half: it lists the open pull requests on the
install's origin repository together with everything needed to decide whether
one is safe to land — mergeability, review decision, and CI check rollup — and
then performs the merge through the GitHub REST API.

Authentication resolves in this order so the GUI can own a token without
requiring a service restart:

1. the ``github_token`` setting stored in Syte's database (set from the GUI),
2. the ``GITHUB_TOKEN`` environment variable,
3. the ``GH_TOKEN`` environment variable.

Without a token the read paths still work against public repositories (subject
to GitHub's much lower anonymous rate limit); merging always requires one.
"""

from __future__ import annotations

import os
import re
from typing import Any

import httpx

from syte.self_update import INSTALL_DIR
from syte.update_source import git_remote_repo, parse_github_repo
from syte.workspace import run_cmd

GITHUB_API = "https://api.github.com"
REQUEST_TIMEOUT = 15.0

# GitHub's three merge strategies. "merge" keeps the PR commits plus a merge
# commit; "squash" collapses them; "rebase" replays them onto the base.
MERGE_METHODS = ("merge", "squash", "rebase")

# Setting key holding an operator-supplied token.
TOKEN_SETTING = "github_token"


class GitHubError(RuntimeError):
    """A GitHub API call failed in a way the operator needs to see."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
# Auth + repo resolution
# ---------------------------------------------------------------------------


async def resolve_token() -> tuple[str, str]:
    """Return ``(token, source)`` for GitHub API calls; token may be empty."""
    try:
        from syte.database import get_setting

        stored = (await get_setting(TOKEN_SETTING, "") or "").strip()
    except Exception:  # noqa: BLE001 - DB may be unavailable during bootstrap
        stored = ""
    if stored:
        return stored, "settings"
    for env_name in ("GITHUB_TOKEN", "GH_TOKEN"):
        value = (os.environ.get(env_name) or "").strip()
        if value:
            return value, env_name
    return "", "none"


def _headers(token: str) -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


async def resolve_repo() -> str:
    """Determine the ``owner/repo`` this install tracks."""
    override = (os.environ.get("SYTE_GITHUB_REPO") or "").strip()
    if override:
        parsed = parse_github_repo(override) or override
        if "/" in parsed:
            return parsed
    try:
        from syte.database import get_setting

        stored = (await get_setting("github_repo", "") or "").strip()
    except Exception:  # noqa: BLE001
        stored = ""
    if stored:
        return parse_github_repo(stored) or stored
    from_remote = git_remote_repo(INSTALL_DIR)
    if from_remote:
        return from_remote
    return repo_from_remote_url(_origin_url())


def _origin_url() -> str:
    """The raw URL of the ``origin`` remote, if there is one."""
    code, out = run_cmd(["git", "remote", "get-url", "origin"], cwd=INSTALL_DIR)
    return out.strip() if code == 0 else ""


def repo_from_remote_url(remote_url: str) -> str:
    """Extract ``owner/repo`` from a remote URL that is not literally github.com.

    ``update_source.parse_github_repo`` only matches a ``github.com`` host, but a
    remote can legitimately point at a proxy or mirror that still serves a
    GitHub repository (CI runners and sandboxes commonly rewrite the host, e.g.
    ``https://gateway.example/github/owner/repo.git``). Falling back to the last
    two path segments recovers the identity in those cases.
    """
    url = (remote_url or "").strip().rstrip("/")
    if not url:
        return ""
    # Strip scheme/userinfo and the scp-style "git@host:owner/repo" separator.
    path = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", url)
    path = path.split("@")[-1]
    if ":" in path and "/" in path:
        host_part, _, after_colon = path.partition(":")
        if not after_colon.split("/")[0].isdigit():
            path = after_colon
        else:
            path = path.partition("/")[2]
    if path.endswith(".git"):
        path = path[: -len(".git")]
    segments = [segment for segment in path.split("/") if segment]
    if len(segments) < 2:
        return ""
    owner, repo = segments[-2], segments[-1]
    # Guard against picking up a bare hostname as the owner.
    if not owner or not repo or "." in repo:
        return ""
    return f"{owner}/{repo}"


# ---------------------------------------------------------------------------
# Local git state
# ---------------------------------------------------------------------------


def local_git_state() -> dict:
    """Describe the git checkout Syte is running from."""
    if not (INSTALL_DIR / ".git").exists():
        return {
            "is_repo": False,
            "branch": "",
            "commit": "",
            "commit_subject": "",
            "dirty": False,
            "changed_files": 0,
        }

    code, branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=INSTALL_DIR)
    branch = branch.strip() if code == 0 else ""

    code, commit = run_cmd(["git", "rev-parse", "--short", "HEAD"], cwd=INSTALL_DIR)
    commit = commit.strip() if code == 0 else ""

    code, subject = run_cmd(["git", "log", "-1", "--pretty=%s"], cwd=INSTALL_DIR)
    subject = subject.strip() if code == 0 else ""

    code, status = run_cmd(["git", "status", "--porcelain"], cwd=INSTALL_DIR)
    changed = [line for line in status.splitlines() if line.strip()] if code == 0 else []

    return {
        "is_repo": True,
        "branch": "" if branch == "HEAD" else branch,
        "commit": commit,
        "commit_subject": subject,
        "dirty": bool(changed),
        "changed_files": len(changed),
    }


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


async def _request(
    method: str,
    path: str,
    *,
    token: str,
    params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
) -> tuple[int, Any]:
    """Call the GitHub REST API, returning ``(status, parsed body)``."""
    url = f"{GITHUB_API}{path}"
    try:
        async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
            response = await client.request(
                method, url, headers=_headers(token), params=params, json=json_body
            )
    except httpx.HTTPError as error:
        raise GitHubError(f"Could not reach the GitHub API: {error}") from error
    try:
        payload = response.json() if response.content else None
    except ValueError:
        payload = None
    return response.status_code, payload


def _api_error_message(status: int, payload: Any, fallback: str) -> str:
    """Turn a GitHub error body into one readable sentence."""
    detail = ""
    if isinstance(payload, dict):
        detail = str(payload.get("message") or "")
        errors = payload.get("errors")
        if isinstance(errors, list) and errors:
            extra = "; ".join(
                str(e.get("message") or e.get("code") or e)
                for e in errors
                if isinstance(e, (dict, str))
            )
            if extra:
                detail = f"{detail} ({extra})" if detail else extra
    if status == 401:
        return "GitHub rejected the token (401). Save a valid personal access token."
    if status == 403:
        return detail or "GitHub denied the request (403) — token scope or rate limit."
    if status == 404:
        return detail or "Not found (404) — check the repository name and token access."
    return detail or f"{fallback} (HTTP {status})"


# ---------------------------------------------------------------------------
# Pull requests
# ---------------------------------------------------------------------------


def _shape_pr(item: dict[str, Any]) -> dict[str, Any]:
    """Normalise a GitHub PR object into the shape the GUI renders."""
    head = item.get("head") or {}
    base = item.get("base") or {}
    user = item.get("user") or {}
    labels = [
        str(label.get("name"))
        for label in (item.get("labels") or [])
        if isinstance(label, dict) and label.get("name")
    ]
    return {
        "number": int(item.get("number") or 0),
        "title": str(item.get("title") or "").strip(),
        "url": str(item.get("html_url") or ""),
        "author": str(user.get("login") or ""),
        "author_avatar": str(user.get("avatar_url") or ""),
        "draft": bool(item.get("draft")),
        "state": str(item.get("state") or ""),
        "head_ref": str(head.get("ref") or ""),
        "head_sha": str(head.get("sha") or ""),
        "base_ref": str(base.get("ref") or ""),
        "created_at": str(item.get("created_at") or ""),
        "updated_at": str(item.get("updated_at") or ""),
        "labels": labels,
        # Only present on the single-PR endpoint, not the list endpoint.
        "mergeable": item.get("mergeable"),
        "mergeable_state": str(item.get("mergeable_state") or ""),
        "additions": item.get("additions"),
        "deletions": item.get("deletions"),
        "changed_files": item.get("changed_files"),
        "commits": item.get("commits"),
        "merged": bool(item.get("merged")),
    }


async def _check_rollup(repo: str, sha: str, token: str) -> dict:
    """Combine the legacy commit status and the Checks API into one verdict."""
    if not sha:
        return {"state": "unknown", "total": 0, "passed": 0, "failed": 0, "pending": 0}

    passed = failed = pending = 0

    status_code, status_body = await _request(
        "GET", f"/repos/{repo}/commits/{sha}/status", token=token
    )
    if status_code < 400 and isinstance(status_body, dict):
        for ctx in status_body.get("statuses") or []:
            state = str(ctx.get("state") or "")
            if state == "success":
                passed += 1
            elif state in ("failure", "error"):
                failed += 1
            elif state == "pending":
                pending += 1

    runs_code, runs_body = await _request(
        "GET",
        f"/repos/{repo}/commits/{sha}/check-runs",
        token=token,
        params={"per_page": 100},
    )
    if runs_code < 400 and isinstance(runs_body, dict):
        for run in runs_body.get("check_runs") or []:
            status = str(run.get("status") or "")
            conclusion = str(run.get("conclusion") or "")
            if status != "completed":
                pending += 1
            elif conclusion in ("success", "neutral", "skipped"):
                passed += 1
            elif conclusion in ("failure", "timed_out", "cancelled", "action_required", "stale"):
                failed += 1

    total = passed + failed + pending
    if total == 0:
        state = "none"
    elif failed:
        state = "failing"
    elif pending:
        state = "pending"
    else:
        state = "passing"
    return {
        "state": state,
        "total": total,
        "passed": passed,
        "failed": failed,
        "pending": pending,
    }


async def _review_decision(repo: str, number: int, token: str) -> str:
    """Latest review verdict per reviewer, reduced to one decision."""
    code, body = await _request(
        "GET", f"/repos/{repo}/pulls/{number}/reviews", token=token, params={"per_page": 100}
    )
    if code >= 400 or not isinstance(body, list):
        return "none"
    latest: dict[str, str] = {}
    for review in body:
        if not isinstance(review, dict):
            continue
        login = str((review.get("user") or {}).get("login") or "")
        state = str(review.get("state") or "").upper()
        # COMMENTED reviews never supersede an approval or change request.
        if not login or state == "COMMENTED":
            continue
        latest[login] = state
    states = set(latest.values())
    if "CHANGES_REQUESTED" in states:
        return "changes_requested"
    if "APPROVED" in states:
        return "approved"
    return "none"


async def list_open_prs(repo: str | None = None, *, enrich: int = 12) -> dict:
    """List open pull requests, newest first, with merge readiness.

    The list endpoint omits ``mergeable``/``mergeable_state``, so the newest
    ``enrich`` PRs are re-fetched individually and annotated with their CI
    rollup and review decision. That bounds the request count for repositories
    carrying a long tail of stale PRs.
    """
    token, token_source = await resolve_token()
    repo = repo or await resolve_repo()
    if not repo:
        raise GitHubError(
            "Could not determine the GitHub repository. Set the git 'origin' remote "
            "or the SYTE_GITHUB_REPO environment variable."
        )

    status, body = await _request(
        "GET",
        f"/repos/{repo}/pulls",
        token=token,
        params={"state": "open", "sort": "created", "direction": "desc", "per_page": 100},
    )
    if status >= 400:
        raise GitHubError(_api_error_message(status, body, "Could not list pull requests"), status)
    if not isinstance(body, list):
        raise GitHubError("Unexpected response from GitHub when listing pull requests.")

    prs = [_shape_pr(item) for item in body if isinstance(item, dict)]
    prs.sort(key=lambda pr: pr["number"], reverse=True)

    for pr in prs[: max(0, enrich)]:
        detail_status, detail_body = await _request(
            "GET", f"/repos/{repo}/pulls/{pr['number']}", token=token
        )
        if detail_status < 400 and isinstance(detail_body, dict):
            pr.update(_shape_pr(detail_body))
        pr["checks"] = await _check_rollup(repo, pr["head_sha"], token)
        pr["review_decision"] = await _review_decision(repo, pr["number"], token)
        pr["merge_blockers"] = merge_blockers(pr)
        pr["can_merge"] = not pr["merge_blockers"] and bool(token)
        pr["enriched"] = True

    for pr in prs[max(0, enrich):]:
        pr["checks"] = {"state": "unknown", "total": 0, "passed": 0, "failed": 0, "pending": 0}
        pr["review_decision"] = "unknown"
        pr["merge_blockers"] = []
        pr["can_merge"] = False
        pr["enriched"] = False

    return {
        "repo": repo,
        "token_configured": bool(token),
        "token_source": token_source,
        "count": len(prs),
        "enriched": min(len(prs), max(0, enrich)),
        "pull_requests": prs,
    }


async def recent_mergeable_commits(repo: str | None = None, *, limit: int = 3) -> list[dict[str, Any]]:
    """Return the newest head commits from mergeable open pull requests.

    GitHub's pull-request list does not include commit messages. Reuse the
    bounded PR enrichment above, then fetch each eligible head commit. The
    result is intentionally small because it is rendered below the Update UI.
    """
    token, _token_source = await resolve_token()
    if not token:
        return []
    repo = repo or await resolve_repo()
    if not repo or limit <= 0:
        return []

    payload = await list_open_prs(repo, enrich=12)
    commits: list[dict[str, Any]] = []
    seen: set[str] = set()
    for pr in payload.get("pull_requests") or []:
        if not pr.get("can_merge") or pr.get("mergeable") is not True:
            continue
        sha = str(pr.get("head_sha") or "").strip()
        if not sha or sha in seen:
            continue
        status, body = await _request("GET", f"/repos/{repo}/commits/{sha}", token=token)
        if status >= 400 or not isinstance(body, dict):
            continue
        commit = body.get("commit") or {}
        message = str(commit.get("message") or "").splitlines()[0].strip()
        if not message:
            message = str(pr.get("title") or "Untitled commit").strip()
        author = commit.get("author") or {}
        committer = commit.get("committer") or {}
        committed_at = str(author.get("date") or committer.get("date") or "")
        commits.append({
            "sha": sha[:7],
            "message": message,
            "committed_at": committed_at,
            "commit_url": str(body.get("html_url") or f"https://github.com/{repo}/commit/{sha}"),
            "pr_number": pr.get("number"),
            "pr_title": str(pr.get("title") or ""),
            "pr_url": str(pr.get("url") or ""),
            "head_ref": str(pr.get("head_ref") or ""),
            "base_ref": str(pr.get("base_ref") or "main"),
        })
        seen.add(sha)

    commits.sort(key=lambda item: item.get("committed_at") or "", reverse=True)
    return commits[:limit]


def merge_blockers(pr: dict) -> list[str]:
    """Reasons this PR must not be merged, in operator language."""
    blockers: list[str] = []
    if pr.get("draft"):
        blockers.append("Pull request is still a draft.")
    if pr.get("merged"):
        blockers.append("Pull request is already merged.")
    if pr.get("state") and pr["state"] != "open":
        blockers.append(f"Pull request is {pr['state']}, not open.")
    # mergeable is None while GitHub is still computing the merge commit.
    if pr.get("mergeable") is False:
        blockers.append("GitHub reports merge conflicts with the base branch.")
    state = pr.get("mergeable_state") or ""
    if state == "dirty":
        blockers.append("Branch has conflicts that must be resolved first.")
    elif state == "blocked":
        blockers.append("Branch protection requires approvals or passing checks.")
    elif state == "behind":
        blockers.append("Branch is behind the base branch and must be updated.")
    checks = pr.get("checks") or {}
    if checks.get("state") == "failing":
        blockers.append(f"{checks.get('failed', 0)} CI check(s) are failing.")
    if pr.get("review_decision") == "changes_requested":
        blockers.append("A reviewer requested changes.")
    return blockers


async def get_pr(number: int, repo: str | None = None) -> dict:
    """Fetch one pull request with checks, reviews, and merge readiness."""
    token, _source = await resolve_token()
    repo = repo or await resolve_repo()
    if not repo:
        raise GitHubError("Could not determine the GitHub repository.")
    status, body = await _request("GET", f"/repos/{repo}/pulls/{number}", token=token)
    if status >= 400:
        raise GitHubError(
            _api_error_message(status, body, f"Could not load PR #{number}"), status
        )
    if not isinstance(body, dict):
        raise GitHubError(f"Unexpected response for PR #{number}.")
    pr = _shape_pr(body)
    pr["checks"] = await _check_rollup(repo, pr["head_sha"], token)
    pr["review_decision"] = await _review_decision(repo, pr["number"], token)
    pr["merge_blockers"] = merge_blockers(pr)
    pr["can_merge"] = not pr["merge_blockers"] and bool(token)
    pr["repo"] = repo
    return pr


async def merge_pr(
    number: int,
    *,
    method: str = "squash",
    repo: str | None = None,
    force: bool = False,
) -> dict:
    """Merge a pull request through the GitHub API.

    ``force`` skips Syte's own readiness checks (draft, conflicts, failing CI)
    and lets GitHub have the final say — branch protection still applies.
    """
    if method not in MERGE_METHODS:
        raise GitHubError(f"Unsupported merge method {method!r}. Use one of: {', '.join(MERGE_METHODS)}.")

    token, _source = await resolve_token()
    if not token:
        raise GitHubError(
            "Merging requires a GitHub token with 'repo' scope. Add one in Settings → Git."
        )
    repo = repo or await resolve_repo()
    if not repo:
        raise GitHubError("Could not determine the GitHub repository.")

    pr = await get_pr(number, repo=repo)
    if pr["merged"]:
        return {
            "ok": True,
            "merged": True,
            "already_merged": True,
            "number": number,
            "repo": repo,
            "message": f"PR #{number} is already merged.",
        }
    if pr["merge_blockers"] and not force:
        return {
            "ok": False,
            "merged": False,
            "number": number,
            "repo": repo,
            "blockers": pr["merge_blockers"],
            "message": "PR is not ready to merge: " + " ".join(pr["merge_blockers"]),
        }

    status, body = await _request(
        "PUT",
        f"/repos/{repo}/pulls/{number}/merge",
        token=token,
        json_body={
            "merge_method": method,
            "commit_title": f"Merge pull request #{number} from {pr['head_ref']}",
        },
    )
    if status == 200 and isinstance(body, dict) and body.get("merged"):
        return {
            "ok": True,
            "merged": True,
            "number": number,
            "repo": repo,
            "sha": str(body.get("sha") or ""),
            "method": method,
            "base_ref": pr["base_ref"],
            "message": str(body.get("message") or f"PR #{number} merged via {method}."),
        }
    if status == 405:
        return {
            "ok": False,
            "merged": False,
            "number": number,
            "repo": repo,
            "blockers": pr["merge_blockers"] or ["GitHub refused the merge (405)."],
            "message": _api_error_message(status, body, "Merge was not allowed"),
        }
    if status == 409:
        return {
            "ok": False,
            "merged": False,
            "number": number,
            "repo": repo,
            "blockers": ["The head branch moved or conflicts with the base (409)."],
            "message": _api_error_message(status, body, "Merge conflict"),
        }
    raise GitHubError(_api_error_message(status, body, f"Could not merge PR #{number}"), status)


# ---------------------------------------------------------------------------
# Deploy (pull a ref into the running install and restart)
# ---------------------------------------------------------------------------


def deploy_branch(branch: str) -> tuple[bool, str]:
    """Update the running install from a branch, then restart.

    Implemented by pointing ``self_update`` at an explicit branch for the
    duration of the call, so the update goes through exactly the same
    stash → fetch → checkout → deps → restart path as the Update button.
    """
    branch = (branch or "").strip()
    if not branch:
        return False, "No branch to deploy."
    return _deploy_with_env({"SYTE_UPDATE_BRANCH": branch})


def deploy_pr(number: int) -> tuple[bool, str]:
    """Update the running install from a pull request head, then restart."""
    if number <= 0:
        return False, "Invalid pull request number."
    return _deploy_with_env({"SYTE_UPDATE_PR": str(number)})


def _deploy_with_env(overrides: dict[str, str]) -> tuple[bool, str]:
    """Run ``update_syte`` with temporary update-source environment overrides."""
    from syte.self_update import update_syte

    # These three keys together decide resolve_update_target's answer; clear the
    # ones we are not setting so a stale value cannot redirect the deploy.
    keys = ("SYTE_UPDATE_BRANCH", "SYTE_UPDATE_PR", "SYTE_UPDATE_FROM_PR")
    previous = {key: os.environ.get(key) for key in keys}
    try:
        for key in keys:
            os.environ.pop(key, None)
        os.environ.update(overrides)
        return update_syte()
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


# ---------------------------------------------------------------------------
# Aggregate status for the GUI
# ---------------------------------------------------------------------------


async def git_status() -> dict:
    """Everything the Git panel needs on first paint."""
    from syte.self_update import get_update_info

    token, token_source = await resolve_token()
    repo = await resolve_repo()
    local = local_git_state()
    try:
        update_info = get_update_info()
    except Exception as error:  # noqa: BLE001 - never break the panel
        update_info = {"error": f"{type(error).__name__}: {error}"}

    repo_url = f"https://github.com/{repo}" if repo else ""
    description = ""
    readme_url = ""
    if repo:
        try:
            status, body = await _request("GET", f"/repos/{repo}", token=token)
            if status < 400 and isinstance(body, dict):
                description = str(body.get("description") or "")
                if body.get("default_branch"):
                    readme_url = f"https://raw.githubusercontent.com/{repo}/{body['default_branch']}/README.md"
        except Exception:
            pass

    return {
        "ok": True,
        "repo": repo,
        "repo_url": repo_url,
        "description": description,
        "readme_url": readme_url,
        "token_configured": bool(token),
        "token_source": token_source,
        "merge_methods": list(MERGE_METHODS),
        "local": local,
        "update": update_info,
    }
