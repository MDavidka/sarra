"""Persistent 9Router model catalog shared by the API and agent runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from syte.database import get_setting


MODEL_CATALOG_SETTING = "agent_9router_models"


def model_profile(model_id: str) -> str:
    """Return the stable agent-profile value for a configured 9Router model."""
    return f"9router:{model_id}"


def new_model_id(name: str) -> str:
    """Create a stable, opaque identifier without exposing model names in IDs."""
    return hashlib.sha256(name.strip().encode()).hexdigest()[:16]


def _levels(value: Any) -> list[int]:
    if not isinstance(value, list):
        return [1, 2, 3, 4, 5]
    levels = sorted({int(level) for level in value if str(level).isdigit() and 1 <= int(level) <= 5})
    return levels or [1, 2, 3, 4, 5]


async def configured_models() -> list[dict[str, Any]]:
    """Return configured models, reading the former single-model settings too."""
    raw = (await get_setting(MODEL_CATALOG_SETTING, "")).strip()
    try:
        saved = json.loads(raw) if raw else []
    except json.JSONDecodeError:
        saved = []
    rows: list[dict[str, Any]] = []
    if isinstance(saved, list):
        for item in saved:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            model_id = str(item.get("id") or "").strip()
            if name and model_id:
                rows.append({
                    "id": model_id,
                    "name": name,
                    "thinking_levels": _levels(item.get("thinking_levels")),
                    "enabled": bool(item.get("enabled")),
                })
    if rows:
        return rows

    # Backward compatibility for the initial single-model implementation.
    legacy_name = (await get_setting("agent_9router_model_name", "")).strip()
    if not legacy_name:
        return []
    legacy_levels = (await get_setting("agent_9router_thinking_levels", "1,2,3,4,5")).split(",")
    return [{
        "id": "legacy",
        "name": legacy_name,
        "thinking_levels": _levels(legacy_levels),
        "enabled": (await get_setting("agent_9router_enabled", "0")).strip() == "1",
    }]


def enabled_model_options(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform configured records into safe, enabled-only agent options."""
    return [{
        "id": row["id"],
        "profile": model_profile(row["id"]),
        "name": row["name"],
        "thinking_levels": list(row["thinking_levels"]),
    } for row in models if row.get("enabled")]
