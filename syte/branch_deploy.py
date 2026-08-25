"""Periodic GitHub branch checks for projects that opt into automatic redeploys.

The worker is deliberately conservative: it only considers GitHub projects with an
associated connected account, compares one configured branch head at a time, and
records the observed commit before issuing a deployment so the same failed commit
does not queue a deploy repeatedly on every interval.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from syte.database import list_projects, update_project
from syte.github_oauth import GitHubOAuthError, branch_head
from syte.platform.git_sources import normalize_repo

logger = logging.getLogger("syte.branch_deploy")

BRANCH_CHECK_INTERVAL_SECONDS = 5 * 60
STARTUP_DELAY_SECONDS = 45


def _github_full_name(git_url: str) -> str:
    identity = normalize_repo(git_url)
    parts = identity.split("/")
    if len(parts) == 3 and parts[0] == "github.com":
        return f"{parts[1]}/{parts[2]}"
    return ""


async def check_project_branch(project: dict[str, Any]) -> str:
    """Check one opted-in project and return a concise operator-facing result."""
    if not bool(project.get("auto_deploy")) or not project.get("git_url"):
        return "skipped"
    account_id = str(project.get("github_account_id") or "").strip()
    repository = _github_full_name(str(project.get("git_url") or ""))
    branch = str(project.get("branch") or "main").strip()
    if not account_id or not repository:
        return "unconfigured"

    try:
        commit_sha = await branch_head(account_id, repository, branch)
    except GitHubOAuthError as exc:
        logger.info("Branch check skipped for %s: %s", project.get("id"), exc)
        return "unavailable"

    observed = str(project.get("last_seen_git_commit") or "")
    deployed = str(project.get("last_deployed_commit") or "")
    if commit_sha == observed:
        return "current"

    if commit_sha == deployed:
        await update_project(str(project["id"]), {"last_seen_git_commit": commit_sha})
        return "recorded"

    from syte.deployment import issue_deploy

    _, message = await issue_deploy(
        str(project["id"]),
        trigger=f"periodic-branch:{branch}",
        commit_sha=commit_sha,
    )
    if "already in progress" in message.lower():
        logger.info("Deferred periodic branch deployment for %s at %s: %s", project.get("id"), commit_sha[:12], message)
        return "deferred"
    await update_project(str(project["id"]), {"last_seen_git_commit": commit_sha})
    logger.info("Queued periodic branch deployment for %s at %s: %s", project.get("id"), commit_sha[:12], message)
    return "queued"


async def check_enabled_project_branches() -> dict[str, int]:
    """Run one complete bounded branch-check pass for all eligible projects."""
    results = {"checked": 0, "queued": 0, "deferred": 0, "current": 0, "unconfigured": 0, "unavailable": 0}
    for project in await list_projects():
        if not bool(project.get("auto_deploy")) or not project.get("git_url"):
            continue
        results["checked"] += 1
        try:
            result = await check_project_branch(project)
        except Exception:
            logger.exception("Unexpected branch-check failure for %s", project.get("id"))
            result = "unavailable"
        if result in results:
            results[result] += 1
    return results


async def periodic_branch_deploy_loop(stop: asyncio.Event) -> None:
    """Keep checking opted-in branches while the Sycord process is running."""
    try:
        await asyncio.wait_for(stop.wait(), timeout=STARTUP_DELAY_SECONDS)
        return
    except asyncio.TimeoutError:
        pass
    while not stop.is_set():
        try:
            await check_enabled_project_branches()
        except Exception:
            logger.exception("Periodic branch deploy pass failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=BRANCH_CHECK_INTERVAL_SECONDS)
        except asyncio.TimeoutError:
            continue
