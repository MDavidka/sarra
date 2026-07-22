"""Planner-executor helpers for complex multi-page site builds."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

# Cap planner LLM wait so complex-site turns do not stall TTFT.
PLANNER_TIMEOUT_S = 2.5

_COMPLEX_MARKERS = (
    "build a site",
    "create a website",
    "build a website",
    "multi-page",
    "multipage",
    "landing page with",
    "dashboard with",
    "full website",
    "complete website",
    "marketing site",
)


def is_complex_site_request(user_message: str) -> bool:
    text = (user_message or "").lower()
    if len(text.split()) < 12:
        return False
    return any(marker in text for marker in _COMPLEX_MARKERS)


def _extract_json_array(text: str) -> list[dict[str, Any]] | None:
    raw = (text or "").strip()
    if not raw:
        return None
    # Prefer fenced JSON
    fence = re.search(r"```(?:json)?\s*(\[[\s\S]*?\])\s*```", raw, re.IGNORECASE)
    candidate = fence.group(1) if fence else raw
    if not candidate.lstrip().startswith("["):
        start = candidate.find("[")
        end = candidate.rfind("]")
        if start >= 0 and end > start:
            candidate = candidate[start : end + 1]
        else:
            return None
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None
    out: list[dict[str, Any]] = []
    for item in parsed:
        if isinstance(item, dict) and item.get("task"):
            out.append({
                "task": str(item.get("task") or "").strip(),
                "files": [str(f) for f in (item.get("files") or []) if f][:12],
                "deps": [str(d) for d in (item.get("deps") or []) if d][:12],
            })
        elif isinstance(item, str) and item.strip():
            out.append({"task": item.strip(), "files": [], "deps": []})
    return out or None


def fallback_site_plan(user_message: str) -> list[dict[str, Any]]:
    """Deterministic plan when the planner model returns non-JSON."""
    return [
        {
            "task": "Scaffold Next.js App Router layout, globals.css, and design tokens",
            "files": ["app/app/layout.tsx", "app/app/globals.css", "app/app/page.tsx"],
            "deps": [],
        },
        {
            "task": "Build the primary home / landing page sections from the user request",
            "files": ["app/app/page.tsx", "app/components"],
            "deps": ["Scaffold Next.js App Router layout, globals.css, and design tokens"],
        },
        {
            "task": "Add secondary routes/pages implied by the request",
            "files": ["app/app"],
            "deps": ["Build the primary home / landing page sections from the user request"],
        },
        {
            "task": "Wire shadcn/ui components and verify desktop + mobile preview",
            "files": ["app/components/ui"],
            "deps": ["Add secondary routes/pages implied by the request"],
        },
    ]


async def plan_complex_site(
    project_id: str,
    user_message: str,
    *,
    provider_completion,
    model: dict[str, str],
    timeout_s: float = PLANNER_TIMEOUT_S,
) -> dict[str, Any]:
    """Use a cheap planner call to decompose a site build into subtasks.

    Falls back to :func:`fallback_site_plan` on timeout, parse failure, or errors
    so the main streamed turn is not blocked for a full extra LLM round-trip.
    """
    planner_prompt = (
        "You are a website architect. Break this request into 5-10 concrete subtasks. "
        "For each subtask, specify: task (string), files (array of paths), deps (array of "
        "task names this depends on). Output ONLY a JSON array.\n\n"
        f"User request: {user_message}"
    )
    try:
        resp = await asyncio.wait_for(
            provider_completion(
                model,
                [{"role": "user", "content": planner_prompt}],
                tools=[],
                temperature=0.2,
            ),
            timeout=max(0.5, float(timeout_s)),
        )
        content = str((resp or {}).get("content") or "")
        subtasks = _extract_json_array(content)
        if not subtasks:
            subtasks = fallback_site_plan(user_message)
            return {
                "ok": True,
                "subtasks": subtasks,
                "planner": "fallback",
                "raw": content[:2000],
            }
        return {"ok": True, "subtasks": subtasks, "planner": "llm", "raw": content[:2000]}
    except asyncio.TimeoutError:
        return {
            "ok": True,
            "subtasks": fallback_site_plan(user_message),
            "planner": "fallback_timeout",
        }
    except Exception as exc:
        return {
            "ok": True,
            "subtasks": fallback_site_plan(user_message),
            "planner": "fallback",
            "message": str(exc),
        }


def order_subtasks(subtasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Topological-ish order: ready deps first; append unresolved at the end."""
    remaining = list(subtasks)
    completed: set[str] = set()
    ordered: list[dict[str, Any]] = []
    guard = 0
    while remaining and guard < len(subtasks) * 3:
        guard += 1
        progressed = False
        next_remaining: list[dict[str, Any]] = []
        for item in remaining:
            deps = item.get("deps") or []
            if all(dep in completed for dep in deps):
                ordered.append(item)
                completed.add(str(item.get("task") or ""))
                progressed = True
            else:
                next_remaining.append(item)
        remaining = next_remaining
        if not progressed:
            ordered.extend(remaining)
            break
    return ordered
