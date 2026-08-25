"""Heuristic model-profile routing for faster, cost-efficient Syra turns."""

from __future__ import annotations

import re
from typing import Any

# Short copy / tiny fix signals → syra-nano
_NANO_PATTERNS = [
    re.compile(r"\b(change|update|rename|fix|tweak|center|align)\b.{0,40}\b(text|button|label|title|copy|color|padding|margin)\b", re.I),
    re.compile(r"\b(typo|spelling|wording|headline text|button text)\b", re.I),
    re.compile(r"^(what|where|how|why|when)\b.{0,80}\?$", re.I),
    re.compile(r"\b(yes|no|ok|thanks|continue)\b", re.I),
]

_CODEGEN_PATTERNS = [
    re.compile(r"\b(implement|write|create|build|generate|refactor|patch|add)\b.{0,80}\b(code|component|feature|function|api|endpoint|test|migration)\b", re.I),
]

# Large rebuild signals → syra-havy
_HAVY_PATTERNS = [
    re.compile(r"\b(build|create|generate|redesign|remake|rebuild)\b.{0,60}\b(landing|homepage|website|page|site)\b", re.I),
    re.compile(r"\b(from screenshot|like this screenshot|reference (url|screenshot)|full (page|site) (build|redesign))\b", re.I),
    re.compile(r"\b(multi-?file refactor|entire (app|site)|new (marketing )?site)\b", re.I),
]

# Subagent research / review signals → prefer dedicated subagent (or nano fallback)
_SUBAGENT_RESEARCH_PATTERNS = [
    re.compile(r"\b(find|locate|search|inspect|review|audit|summarize|list|check|scan|read|investigate|analyze)\b", re.I),
    re.compile(r"\b(where is|what files|which file|how does|does .+ exist)\b", re.I),
]

# Subagent implementation signals → writes
_SUBAGENT_IMPL_PATTERNS = [
    re.compile(r"\b(implement|write|edit|create|fix|refactor|rename|delete|apply|patch|update file|add)\b", re.I),
]

# Cheapest → most expensive. Delegated work uses one of the public profiles.
_PROFILE_COST_RANK = {
    "syra-nano": 0,
    "syra-ultra": 1,
    "syra-havy": 2,
}

_AUTO_PROFILE_ALIASES = frozenset({"", "auto", "automatic", "route", "smart"})
_PLAN_ASSIGNEE_RE = re.compile(r"^\[(main|subagent)\]\s*(.*)$", re.I)


def normalize_explicit_profile(profile: str | None) -> str | None:
    """Treat auto/empty aliases as 'no explicit profile' so routing can apply."""
    if profile is None:
        return None
    value = str(profile).strip().lower()
    if value in _AUTO_PROFILE_ALIASES:
        return None
    return str(profile).strip() or None


def suggest_model_profile(
    message: str,
    *,
    explicit_profile: str | None = None,
    thinking_level: int | str | None = None,
    improve_from_screenshot: bool = False,
) -> dict[str, Any]:
    """Return a suggested profile + reason without overriding explicit choices.

    When the caller already set ``model_profile`` (other than ``auto``) or
    ``thinking_level``, we keep that choice and only annotate the suggestion.
    Automatic downgrade/upgrade applies only when both are omitted / auto.
    """
    text = (message or "").strip()
    suggested = "syra-nano"
    reason = "default balanced edits"
    explicit = normalize_explicit_profile(explicit_profile)

    if improve_from_screenshot and len(text) > 40:
        suggested = "syra-havy"
        reason = "screenshot-based design remake"
    elif any(p.search(text) for p in _CODEGEN_PATTERNS):
        suggested = "syra-havy"
        reason = "code generation uses the Metal coding model"
    elif any(p.search(text) for p in _HAVY_PATTERNS):
        suggested = "syra-havy"
        reason = "full page / multi-file build signal"
    elif len(text) < 120 and any(p.search(text) for p in _NANO_PATTERNS):
        suggested = "syra-nano"
        reason = "short Q&A / small copy or style tweak"
    elif len(text) < 40:
        suggested = "syra-nano"
        reason = "very short message"

    auto_applied = False
    effective = explicit
    if not explicit and thinking_level in (None, ""):
        effective = suggested
        auto_applied = True

    return {
        "suggested_profile": suggested,
        "effective_profile": effective or suggested,
        "auto_applied": auto_applied,
        "reason": reason,
    }


def infer_subagent_mode(task: str, explicit_mode: str | None = None) -> str:
    """Return ``research`` or ``implementation`` for a delegated task."""
    mode = (explicit_mode or "").strip().lower()
    if mode in {"research", "review", "readonly", "read_only", "read-only"}:
        return "research"
    if mode in {"implementation", "implement", "write", "edit", "mutate"}:
        return "implementation"
    text = (task or "").strip()
    if any(p.search(text) for p in _SUBAGENT_IMPL_PATTERNS) and not any(
        p.search(text) for p in _SUBAGENT_RESEARCH_PATTERNS
    ):
        return "implementation"
    if any(p.search(text) for p in _SUBAGENT_RESEARCH_PATTERNS):
        return "research"
    # Default research: faster, cheaper, and avoids parent/subagent write races.
    return "research"


def parse_plan_assignee(step: str) -> tuple[str, str]:
    """Return ``(assignee, text)`` from a plan step, defaulting assignee to main."""
    text = (step or "").strip()
    match = _PLAN_ASSIGNEE_RE.match(text)
    if not match:
        return "main", text
    return match.group(1).lower(), (match.group(2) or "").strip()


def normalize_plan_steps(
    steps: list[Any] | None,
    assignees: list[Any] | None = None,
) -> list[dict[str, str]]:
    """Normalize plan steps into ``{text, assignee}`` rows (main | subagent)."""
    out: list[dict[str, str]] = []
    raw_steps = steps or []
    raw_assignees = assignees or []
    for index, step in enumerate(raw_steps):
        assignee = "main"
        text = ""
        if isinstance(step, dict):
            text = str(step.get("text") or step.get("step") or step.get("title") or "").strip()
            assignee = str(step.get("assignee") or "main").strip().lower() or "main"
        else:
            text = str(step).strip()
            assignee, text = parse_plan_assignee(text)
            if assignee == "main" and index < len(raw_assignees):
                candidate = str(raw_assignees[index] or "").strip().lower()
                if candidate in {"main", "subagent"}:
                    assignee = candidate
        if assignee not in {"main", "subagent"}:
            assignee = "main"
        if text:
            out.append({"text": text, "assignee": assignee})
    return out


def _cap_profile(profile: str, parent_profile: str | None) -> str:
    """Never route a fallback subagent above the parent's cost tier."""
    parent = (parent_profile or "syra-nano").strip() or "syra-nano"
    want_rank = _PROFILE_COST_RANK.get(profile, 1)
    parent_rank = _PROFILE_COST_RANK.get(parent, 1)
    if want_rank <= parent_rank:
        return profile if profile in _PROFILE_COST_RANK else "syra-nano"
    for name, rank in sorted(_PROFILE_COST_RANK.items(), key=lambda item: -item[1]):
        if rank <= parent_rank:
            return name
    return "syra-nano"


def suggest_subagent_profile(
    task: str,
    *,
    parent_profile: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Choose a public profile for delegated work, with no hidden provider."""
    resolved_mode = infer_subagent_mode(task, mode)
    parent = (parent_profile or "syra-nano").strip() or "syra-nano"
    suggested = "syra-nano" if resolved_mode == "research" else "syra-havy"
    reason = f"public {suggested} profile for delegated {resolved_mode} work"
    fallbacks = ["syra-nano", "syra-ultra", parent]
    effective = suggested

    return {
        "mode": resolved_mode,
        "suggested_profile": suggested,
        "effective_profile": effective,
        "fallback_profiles": fallbacks,
        "parent_profile": parent,
        "reason": reason,
    }


def fallback_subagent_profile(
    preferred: str,
    *,
    parent_profile: str | None,
    mode: str,
    available_profiles: set[str] | frozenset[str],
) -> tuple[str, str]:
    """Pick the best available profile when ``preferred`` has no API key."""
    parent = (parent_profile or "syra-nano").strip() or "syra-nano"
    chain = [preferred]
    if mode == "research":
        chain.extend(["syra-nano", "syra-ultra", parent])
    else:
        chain.extend(["syra-havy", "syra-nano", "syra-ultra", parent])
    seen: set[str] = set()
    for name in chain:
        if not name or name in seen:
            continue
        seen.add(name)
        capped = _cap_profile(name, parent)
        if capped in available_profiles:
            return capped, f"fallback:{capped}"
        if name in available_profiles:
            return name, f"fallback:{name}"
    if parent in available_profiles:
        return parent, "fallback_parent"
    return parent, "fallback_parent_unverified"
