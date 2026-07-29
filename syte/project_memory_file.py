"""Persistent ``syra/memory.md`` project brief for the coding agent.

Keeps a short, human-editable description of the project so the agent does not
re-scan the whole workspace on every turn. Stored under the workspace root as
``syra/memory.md`` (next to the app tree).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from syte.workspace import workspace_path

MEMORY_REL_PATH = "syra/memory.md"

_MEMORY_TEMPLATE = """# Project memory

> Maintained by Syte's coding agent. Keep this short and current.
> Update after meaningful architecture / stack / UI decisions.

## Summary
- (what this project is)

## Stack
- (framework, UI kit, package manager, deploy notes)

## Key paths
- (important files the agent should edit first)

## Conventions
- (naming, layout, design tokens, do/don't)

## Open issues
- (known bugs or TODOs the agent should not rediscover)
"""


def memory_file_path(project_id: str) -> Path:
    return workspace_path(project_id) / MEMORY_REL_PATH


def read_project_memory_md(project_id: str, *, max_chars: int = 6000) -> str:
    path = memory_file_path(project_id)
    if not path.is_file():
        return ""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    text = text.strip()
    if not text:
        return ""
    if len(text) > max_chars:
        return text[: max_chars - 20].rstrip() + "\n… [truncated]"
    return text


def ensure_project_memory_md(project_id: str) -> dict[str, Any]:
    """Create ``syra/memory.md`` with a template if missing."""
    path = memory_file_path(project_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not path.exists():
        path.write_text(_MEMORY_TEMPLATE, encoding="utf-8")
        created = True
    return {
        "ok": True,
        "path": MEMORY_REL_PATH,
        "created": created,
        "exists": path.is_file(),
        "chars": len(path.read_text(encoding="utf-8", errors="replace")) if path.is_file() else 0,
    }


def project_memory_md_prompt_block(project_id: str) -> str:
    text = read_project_memory_md(project_id)
    if not text:
        return (
            f"## Project memory file\n"
            f"No `{MEMORY_REL_PATH}` yet. After you understand the project, create/update "
            f"`{MEMORY_REL_PATH}` with a short summary, stack, key paths, and conventions "
            f"so future turns do not re-discover basics."
        )
    return (
        f"## Project memory (`{MEMORY_REL_PATH}`)\n"
        f"Authoritative short brief — prefer this over re-scanning the whole repo:\n\n"
        f"{text}\n\n"
        f"When you learn lasting facts (stack, key paths, design decisions), update "
        f"`{MEMORY_REL_PATH}` with write_file."
    )
