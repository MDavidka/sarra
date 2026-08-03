"""Persistent 9Router model catalog shared by the API and agent runtime."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from syte.database import get_setting


MODEL_CATALOG_SETTING = "agent_9router_models"
DEFAULT_MODEL_PROVIDER = "9Router"


def normalize_provider(value: str) -> str:
    """Return a comparison-safe provider name."""
    return " ".join((value or "").split()).casefold()


def inferred_provider(name: str) -> str:
    """Keep older ``provider/model`` entries grouped usefully in the UI."""
    prefix = (name or "").strip().split("/", 1)[0].strip()
    return prefix.title() if prefix else DEFAULT_MODEL_PROVIDER


def model_profile(model_id: str) -> str:
    """Return the stable agent-profile value for a configured 9Router model."""
    return f"9router:{model_id}"


def new_model_id(name: str, provider: str = "") -> str:
    """Create a stable, opaque identifier without exposing model names in IDs."""
    source = f"{normalize_provider(provider)}\0{name.strip().casefold()}"
    return hashlib.sha256(source.encode()).hexdigest()[:16]


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
                    "provider": str(item.get("provider") or inferred_provider(name)).strip(),
                    "thinking_levels": _levels(item.get("thinking_levels")),
                    "thinking_level": str(item.get("thinking_level") or "medium").strip().lower(),
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
        "provider": inferred_provider(legacy_name),
        "thinking_levels": _levels(legacy_levels),
        "thinking_level": "medium",
        "enabled": (await get_setting("agent_9router_enabled", "0")).strip() == "1",
    }]


def enabled_model_options(models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Transform configured records into safe, enabled-only agent options."""
    return [{
        "id": row["id"],
        "profile": model_profile(row["id"]),
        "name": row["name"],
        "provider": row.get("provider") or inferred_provider(row["name"]),
        "thinking_levels": list(row["thinking_levels"]),
        "thinking_level": row.get("thinking_level") or "medium",
    } for row in models if row.get("enabled")]
