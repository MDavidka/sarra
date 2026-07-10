"""Agent request logging and dashboard metrics (DPFA / MNOA)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite

from syte.ai_providers import PROFILE_ORDER
from syte.config import settings
from syte.opencode_agent import bridge_settings, is_agent_running, opencode_installed
from syte.database import get_setting, list_projects

REQUESTS_SCHEMA = """
CREATE TABLE IF NOT EXISTS agent_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'api',
    model_profile TEXT,
    message TEXT,
    status TEXT NOT NULL,
    error TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_requests_created ON agent_requests(created_at);
"""


async def ensure_agent_requests_table() -> None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.executescript(REQUESTS_SCHEMA)
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def log_agent_request(
    project_id: str,
    *,
    source: str = "api",
    model_profile: str | None = None,
    message: str | None = None,
    status: str = "ok",
    error: str = "",
) -> None:
    await ensure_agent_requests_table()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO agent_requests (project_id, source, model_profile, message, status, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (project_id, source, model_profile or "", (message or "")[:4000], status, error[:2000], _now()),
        )
        await db.commit()


async def _count_requests_since(since: str, *, status: str | None = None) -> int:
    await ensure_agent_requests_table()
    query = "SELECT COUNT(*) FROM agent_requests WHERE created_at >= ?"
    params: list = [since]
    if status:
        query += " AND status = ?"
        params.append(status)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute(query, params) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


async def max_agents_allowed() -> int:
    raw = (await get_setting("agent_max_count", "")).strip()
    if raw.isdigit():
        return max(1, int(raw))
    return settings.continue_port_end - settings.continue_port_start + 1


async def agents_online_count() -> int:
    projects = await list_projects()
    return sum(1 for p in projects if is_agent_running(p["id"]))


async def get_dashboard_metrics() -> dict:
    from syte.system_stats import get_system_stats

    await ensure_agent_requests_table()
    bridge = await bridge_settings()
    stats = get_system_stats()
    online = await agents_online_count()
    mnoa_max = await max_agents_allowed()
    since_30d = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    incoming = await _count_requests_since(since_30d)
    failed = await _count_requests_since(since_30d, status="error")

    dpfa_percent = min(100, int(stats.get("cpu_percent", 0)))
    mnoa_percent = min(100, int(round(online / max(1, mnoa_max) * 100)))

    internal_ok = bool((await get_setting("syra_internal_secret", "")).strip())
    keys_ok = any(bool(bridge["profiles"][name]["api_key"]) for name in PROFILE_ORDER)
    cli_ok = opencode_installed()

    onboarding = {
        "internal_api": internal_ok,
        "ai_models": keys_ok,
        "provider": True,
        "cli_server": cli_ok,
        "api_ready": keys_ok and internal_ok,
        "complete": internal_ok and keys_ok and cli_ok,
    }

    return {
        "agents_online": online,
        "incoming_requests_30d": incoming,
        "failed_relationships_30d": failed,
        "dpfa": {
            "label": "DPFA",
            "title": "Dedicated Performance For Agents",
            "percent": dpfa_percent,
            "detail": f"{dpfa_percent}% CPU allocated to VM workloads",
        },
        "mnoa": {
            "label": "MNOA",
            "title": "Maximum Number Of Agents",
            "percent": mnoa_percent,
            "current": online,
            "max": mnoa_max,
            "detail": f"{online} / {mnoa_max} agents running",
        },
        "onboarding": onboarding,
        "opencode_cli_installed": cli_ok,
        "continue_cli_installed": cli_ok,
        "ai_providers": [
            {
                "profile": name,
                "label": bridge["profiles"][name]["label"],
                "model": bridge["profiles"][name]["model"],
                "api_base": bridge["profiles"][name]["api_base"],
                "api_key_set": bool(bridge["profiles"][name]["api_key"]),
            }
            for name in PROFILE_ORDER
        ],
    }
