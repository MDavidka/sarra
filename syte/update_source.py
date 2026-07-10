"""Resolve which git ref Syte should pull during self-update."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from syte.workspace import run_cmd

DEFAULT_FALLBACK_BRANCH = "main"


@dataclass(frozen=True)
class UpdateTarget:
    source_type: str
    branch: str
    label: str
    pr_number: int | None = None
    pr_title: str = ""
    pr_url: str = ""
    repo: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "branch": self.branch,
            "label": self.label,
            "pr_number": self.pr_number,
            "pr_title": self.pr_title,
            "pr_url": self.pr_url,
            "repo": self.repo,
        }


def _github_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    token = (os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def parse_github_repo(remote_url: str) -> str:
    url = remote_url.strip().rstrip("/")
    match = re.search(r"github\.com[:/](?P<owner>[^/]+)/(?P<repo>[^/.]+)", url)
    if not match:
        return ""
    return f"{match.group('owner')}/{match.group('repo')}"


def git_remote_repo(install_dir: Path) -> str:
    code, out = run_cmd(["git", "remote", "get-url", "origin"], cwd=install_dir)
    if code != 0:
        return ""
    return parse_github_repo(out)


def _pr_from_api_item(item: dict[str, Any], repo: str) -> UpdateTarget:
    number = int(item["number"])
    title = str(item.get("title") or "").strip()
    head_ref = str((item.get("head") or {}).get("ref") or "").strip()
    html_url = str(item.get("html_url") or "").strip()
    return UpdateTarget(
        source_type="pr",
        branch=head_ref,
        label=f"PR #{number}: {title}" if title else f"PR #{number}",
        pr_number=number,
        pr_title=title,
        pr_url=html_url,
        repo=repo,
    )


def fetch_pull_request(repo: str, pr_number: int) -> UpdateTarget | None:
    if not repo or pr_number <= 0:
        return None
    url = f"https://api.github.com/repos/{repo}/pulls/{pr_number}"
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url, headers=_github_headers())
        if response.status_code >= 400:
            return None
        return _pr_from_api_item(response.json(), repo)
    except Exception:
        return None


def fetch_latest_open_pr(repo: str) -> UpdateTarget | None:
    """Return the open PR with the highest number (newest), not merely last touched."""
    if not repo:
        return None
    url = f"https://api.github.com/repos/{repo}/pulls"
    params = {"state": "open", "sort": "created", "direction": "desc", "per_page": 100}
    try:
        with httpx.Client(timeout=12.0) as client:
            response = client.get(url, params=params, headers=_github_headers())
        if response.status_code >= 400:
            return None
        items = response.json()
        if not isinstance(items, list):
            return None

        candidates: list[dict[str, Any]] = []
        for item in items:
            if item.get("draft"):
                continue
            head_ref = str((item.get("head") or {}).get("ref") or "").strip()
            if head_ref:
                candidates.append(item)

        if not candidates:
            for item in items:
                head_ref = str((item.get("head") or {}).get("ref") or "").strip()
                if head_ref:
                    candidates.append(item)

        if not candidates:
            return None

        best = max(candidates, key=lambda item: int(item["number"]))
        return _pr_from_api_item(best, repo)
    except Exception:
        return None


def resolve_update_target(install_dir: Path) -> UpdateTarget:
    repo = git_remote_repo(install_dir)

    branch_override = (os.environ.get("SYTE_UPDATE_BRANCH") or "").strip()
    if branch_override:
        return UpdateTarget(
            source_type="branch",
            branch=branch_override,
            label=f"branch {branch_override}",
            repo=repo,
        )

    pr_override = (os.environ.get("SYTE_UPDATE_PR") or "").strip()
    if pr_override.isdigit():
        target = fetch_pull_request(repo, int(pr_override))
        if target:
            return target

    use_open_pr = (os.environ.get("SYTE_UPDATE_FROM_PR") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if use_open_pr:
        latest = fetch_latest_open_pr(repo)
        if latest and latest.branch:
            return latest

    return UpdateTarget(
        source_type="branch",
        branch=DEFAULT_FALLBACK_BRANCH,
        label=DEFAULT_FALLBACK_BRANCH,
        repo=repo,
    )
