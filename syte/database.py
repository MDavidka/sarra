import json
from datetime import datetime, timezone
from typing import Any

import aiosqlite

from syte.config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    git_url TEXT,
    branch TEXT DEFAULT 'main',
    port INTEGER NOT NULL,
    domain TEXT,
    start_command TEXT NOT NULL DEFAULT 'npm start',
    env_vars TEXT DEFAULT '{}',
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


async def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_workspaces_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.executescript(SCHEMA)
        await db.commit()


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            return row["value"] if row else default


async def set_setting(key: str, value: str) -> None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO system_settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        await db.commit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def list_projects() -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM projects ORDER BY created_at DESC") as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def get_project(project_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM projects WHERE id = ?", (project_id,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_project(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO projects
            (id, name, git_url, branch, port, domain, start_command, env_vars, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["name"],
                data.get("git_url"),
                data.get("branch", "main"),
                data["port"],
                data.get("domain"),
                data.get("start_command", "npm start"),
                json.dumps(data.get("env_vars", {})),
                "stopped",
                now,
                now,
            ),
        )
        await db.commit()
    return (await get_project(data["id"])) or data


async def update_project(project_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    project = await get_project(project_id)
    if not project:
        return None

    allowed = {
        "name", "git_url", "branch", "port", "domain",
        "start_command", "env_vars", "status",
    }
    fields = {k: v for k, v in updates.items() if k in allowed}
    if "env_vars" in fields and isinstance(fields["env_vars"], dict):
        fields["env_vars"] = json.dumps(fields["env_vars"])

    if not fields:
        return project

    fields["updated_at"] = _now()
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    values = list(fields.values()) + [project_id]

    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            f"UPDATE projects SET {set_clause} WHERE id = ?", values
        )
        await db.commit()
    return await get_project(project_id)


async def delete_project(project_id: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
        return cursor.rowcount > 0
