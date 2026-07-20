"""Heuristic model-profile routing for faster Syra turns."""

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


def suggest_model_profile(
    message: str,
    *,
    explicit_profile: str | None = None,
    thinking_level: int | str | None = None,
    improve_from_screenshot: bool = False,
) -> dict[str, Any]:
    """Return a suggested profile + reason without overriding explicit choices.

    When the caller already set ``model_profile`` or ``thinking_level``, we keep
    that choice and only annotate the suggestion. Automatic downgrade/upgrade
    applies only when both are omitted.
    """
    text = (message or "").strip()
    suggested = "syra-base"
    reason = "default balanced edits"

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
    effective = explicit_profile
    if not explicit_profile and thinking_level in (None, ""):
        effective = suggested
        auto_applied = True

    return {
        "suggested_profile": suggested,
        "effective_profile": effective or suggested,
        "auto_applied": auto_applied,
        "reason": reason,
    }
