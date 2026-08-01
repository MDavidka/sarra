"""System-level agent for the 'new feature?' settings tab.

Uses the existing Syte agent infrastructure (communicate_with_agent)
so the agent has full access to tools, file system, and the selected model.
After the agent finishes, an auto-update is triggered.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from syte import __version__
from syte.cloud_agent import communicate_with_agent
from syte.config import settings
from syte.database import create_project, get_project
from syte.self_update import update_syte
from syte.workspace import ensure_workspace

INSTALL_DIR = Path(__file__).resolve().parent.parent
SYSTEM_PROJECT_ID = "system-new-feature-agent"


def get_current_version() -> str:
    return __version__


def get_update_target_info() -> dict[str, Any]:
    from syte.update_source import resolve_update_target

    target = resolve_update_target(INSTALL_DIR)
    return {
        "source_type": target.source_type,
        "branch": target.branch,
        "label": target.label,
        "pr_number": target.pr_number,
        "pr_url": target.pr_url,
        "repo": target.repo,
    }


async def _ensure_system_project() -> str:
    project = await get_project(SYSTEM_PROJECT_ID)
    if project:
        return SYSTEM_PROJECT_ID
    now = __import__("datetime").now(__import__("datetime").timezone.utc).isoformat()
    await create_project({
        "id": SYSTEM_PROJECT_ID,
        "name": "System Agent",
        "git_url": None,
        "branch": "main",
        "port": 5200,
        "domain": None,
        "start_command": "",
        "env_vars": {},
        "deploy_type": "shell",
        "dockerfile_path": None,
        "status": "stopped",
        "created_at": now,
        "updated_at": now,
    })
    ensure_workspace(SYSTEM_PROJECT_ID)
    return SYSTEM_PROJECT_ID


async def run_new_feature_agent(
    message: str,
    model_profile: str | None = None,
) -> dict[str, Any]:
    """Run the new-feature agent using Syte's general agent API.

    Uses communicate_with_agent so the agent has full access to
    system file tools and the selected model. After the agent finishes,
    an auto-update is triggered automatically.
    """
    if not message.strip():
        return {
            "ok": False,
            "error": "empty_message",
            "message": "Please provide a message for the system agent.",
        }

    project_id = await _ensure_system_project()

    result = await communicate_with_agent(
        project_id,
        message,
        model_profile=model_profile,
        thinking_level=3,
        source="settings_new_feature",
        auto_start=True,
    )

    if not result.get("ok"):
        return result

    current_version = get_current_version()
    update_info = get_update_target_info()

    return {
        "ok": True,
        "profile": result.get("model_profile") or "auto",
        "model": result.get("model") or "",
        "provider": result.get("provider") or "",
        "reply": result.get("reply") or result.get("message") or "",
        "current_version": current_version,
        "update_target": update_info,
        "triggered_update": False,
    }