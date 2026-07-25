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

# Large rebuild signals → syra-havy
_HAVY_PATTERNS = [
    re.compile(r"\b(build|create|generate|redesign|remake|rebuild)\b.{0,60}\b(landing|homepage|website|page|site)\b", re.I),
    re.compile(r"\b(from screenshot|like this screenshot|reference (url|screenshot)|full (page|site) (build|redesign))\b", re.I),
    re.compile(r"\b(multi-?file refactor|entire (app|site)|new (marketing )?site)\b", re.I),
]

# Subagent research / review signals → prefer nano
_SUBAGENT_RESEARCH_PATTERNS = [
    re.compile(r"\b(find|locate|search|inspect|review|audit|summarize|list|check|scan|read|investigate|analyze)\b", re.I),
    re.compile(r"\b(where is|what files|which file|how does|does .+ exist)\b", re.I),
]

# Subagent implementation signals → prefer parent/base (writes)
_SUBAGENT_IMPL_PATTERNS = [
    re.compile(r"\b(implement|write|edit|create|fix|refactor|rename|delete|apply|patch|update file|add)\b", re.I),
]

# Cheapest → most expensive. Subagents never upgrade above the parent.
_PROFILE_COST_RANK = {
    "syra-nano": 0,
    "syra-base": 1,
    "syra-havy": 2,
    "syra-ultra": 3,
}

_AUTO_PROFILE_ALIASES = frozenset({"", "auto", "automatic", "route", "smart"})


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
    suggested = "syra-base"
    reason = "default balanced edits"
    explicit = normalize_explicit_profile(explicit_profile)

    if improve_from_screenshot and len(text) > 40:
        suggested = "syra-havy"
        reason = "screenshot-based design remake"
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


def _cap_profile(profile: str, parent_profile: str | None) -> str:
    """Never route a subagent above the parent's cost tier."""
    parent = (parent_profile or "syra-base").strip() or "syra-base"
    want_rank = _PROFILE_COST_RANK.get(profile, 1)
    parent_rank = _PROFILE_COST_RANK.get(parent, 1)
    if want_rank <= parent_rank:
        return profile if profile in _PROFILE_COST_RANK else "syra-base"
    # Walk down to the highest allowed profile at/under parent.
    for name, rank in sorted(_PROFILE_COST_RANK.items(), key=lambda item: -item[1]):
        if rank <= parent_rank:
            return name
    return "syra-base"


def suggest_subagent_profile(
    task: str,
    *,
    parent_profile: str | None = None,
    mode: str | None = None,
) -> dict[str, Any]:
    """Pick a fast/cheap profile for a subagent without exceeding the parent tier.

    Research/review work prefers ``syra-nano``. Implementation prefers
    ``syra-base`` (or the parent if the parent is already nano/base).
    """
    resolved_mode = infer_subagent_mode(task, mode)
    parent = (parent_profile or "syra-base").strip() or "syra-base"

    if resolved_mode == "research":
        suggested = "syra-nano"
        reason = "research/review subagent → fast cheap model"
    elif parent in {"syra-nano", "syra-base"}:
        suggested = parent
        reason = "implementation subagent keeps parent profile"
    else:
        # Cap expensive parent models: sub-implementations rarely need pro/ultra.
        suggested = "syra-base"
        reason = "implementation subagent capped at syra-base for cost"

    effective = _cap_profile(suggested, parent)
    if effective != suggested:
        reason = f"{reason}; capped to parent tier ({parent})"

    return {
        "mode": resolved_mode,
        "suggested_profile": suggested,
        "effective_profile": effective,
        "parent_profile": parent,
        "reason": reason,
    }
