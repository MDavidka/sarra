"""Planner-executor helpers for complex multi-page site builds."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

# Cap planner LLM wait so complex-site turns do not stall TTFT forever, but
# allow enough time for a real plan before the static fallback (DAV-184).
PLANNER_TIMEOUT_S = 12.0

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

_SITE_SURFACES = (
    "website",
    "web site",
    "landing page",
    "homepage",
    "home page",
    "marketing page",
    "dashboard",
    "web app",
    "web ui",
    "login page",
    "signup page",
    "pricing page",
    "hero section",
    "navbar",
)

_SUBSTANTIVE_ACTIONS = (
    "build",
    "create",
    "design",
    "redesign",
    "rework",
    "revamp",
    "scaffold",
    "implement",
    "make a",
    "add a page",
    "add a section",
)

_VISUAL_DIRECTION_SIGNALS = (
    "minimal",
    "bold",
    "corporate",
    "vibrant",
    "dark-tech",
    "dark tech",
    "editorial",
    "brutalist",
    "luxury",
    "playful",
    "professional",
    "monochrome",
    "color palette",
    "brand guide",
    "design system",
    "match this",
    "reference",
    "screenshot",
    "figma",
    "http://",
    "https://",
)


def is_complex_site_request(user_message: str) -> bool:
    text = (user_message or "").lower()
    if len(text.split()) < 12:
        return False
    return any(marker in text for marker in _COMPLEX_MARKERS)


def is_website_request(user_message: str) -> bool:
    """Return whether the request itself clearly concerns a browser UI."""
    text = " ".join((user_message or "").lower().split())
    return any(surface in text for surface in _SITE_SURFACES)


def is_substantive_site_request(user_message: str) -> bool:
    """Identify site work that deserves clarification/plan gating.

    Tiny edits such as changing one button label should stay fast. New pages,
    sections, full builds, and redesigns need an explicit design plan first.
    """
    text = " ".join((user_message or "").lower().split())
    if is_complex_site_request(text):
        return True
    return is_website_request(text) and any(action in text for action in _SUBSTANTIVE_ACTIONS)


def site_request_needs_clarification(user_message: str) -> bool:
    """Conservatively detect when auto-planning would jump ahead of a design choice.

    The main agent still makes the final judgment and can ask about audience,
    content, pages, or behavior. This guard primarily prevents the background
    planner from running before an obviously missing visual direction is chosen.
    """
    text = " ".join((user_message or "").lower().split())
    if not is_substantive_site_request(text):
        return False
    has_direction = any(signal in text for signal in _VISUAL_DIRECTION_SIGNALS)
    is_new_build = any(action in text for action in ("build", "create", "scaffold", "make a"))
    return is_new_build and not has_direction


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
    """Deterministic plan when the planner model returns non-JSON / times out."""
    intent = " ".join((user_message or "").strip().split())
    if len(intent) > 180:
        intent = intent[:177] + "…"
    intent_note = intent or "the user request"
    return [
        {
            "task": "Define the audience, content hierarchy, routes, and visual direction",
            "files": [],
            "deps": [],
        },
        {
            "task": "Audit the existing workspace, assets, design tokens, and component inventory",
            "files": ["app/app", "app/components", "app/public"],
            "deps": ["Define the audience, content hierarchy, routes, and visual direction"],
        },
        {
            "task": "Scaffold or refine the App Router shell, globals.css, fonts, and design tokens",
            "files": ["app/app/layout.tsx", "app/app/globals.css", "app/app/page.tsx"],
            "deps": ["Audit the existing workspace, assets, design tokens, and component inventory"],
        },
        {
            "task": f"Build a content-specific primary page composition for: {intent_note}",
            "files": ["app/app/page.tsx", "app/components"],
            "deps": ["Scaffold or refine the App Router shell, globals.css, fonts, and design tokens"],
        },
        {
            "task": "Compose required interactions from individual shadcn/ui components and Radix-backed behavior",
            "files": ["app/components", "app/components/ui"],
            "deps": [f"Build a content-specific primary page composition for: {intent_note}"],
        },
        {
            "task": f"Add secondary routes and real content/assets implied by: {intent_note}",
            "files": ["app/app"],
            "deps": ["Compose required interactions from individual shadcn/ui components and Radix-backed behavior"],
        },
        {
            "task": "Review accessibility, responsive states, interaction states, and anti-slop design quality",
            "files": ["app/app", "app/components"],
            "deps": [f"Add secondary routes and real content/assets implied by: {intent_note}"],
        },
        {
            "task": "Verify lint, clean browser console, and desktop plus phone previews; iterate on visual defects",
            "files": [],
            "deps": ["Review accessibility, responsive states, interaction states, and anti-slop design quality"],
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
        "You are a senior website architect and product designer. Produce a deliberate, "
        "implementation-ready plan rather than a generic page-section checklist. Cover: "
        "audience and conversion goal; information architecture and content hierarchy; "
        "visual direction and typography; real imagery/assets; mapping interactions to "
        "individual shadcn/ui components (never shadcn Blocks); responsive behavior; "
        "Radix/WAI-ARIA accessibility and complete interaction states; and desktop/phone "
        "preview verification. Reject generic AI-template defaults such as gratuitous "
        "gradient text, glowing blobs, bento grids, excessive pills, or repetitive card "
        "rows unless the brief specifically justifies them. Break the work into 7-12 "
        "concrete subtasks. For each subtask, specify: task (string), files (array of paths), "
        "deps (array of exact task names this depends on). Output ONLY a JSON array.\n\n"
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
