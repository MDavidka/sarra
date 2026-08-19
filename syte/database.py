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
    start_command TEXT NOT NULL DEFAULT '',
    env_vars TEXT DEFAULT '{}',
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS api_tokens (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    prefix TEXT NOT NULL,
    token_hash TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL,
    last_used_at TEXT
);

CREATE TABLE IF NOT EXISTS operator_accounts (
    id TEXT PRIMARY KEY,
    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    avatar_icon TEXT NOT NULL DEFAULT 'user',
    role TEXT NOT NULL DEFAULT 'operator',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_login_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_operator_accounts_email
    ON operator_accounts(email);

CREATE TABLE IF NOT EXISTS deployment_runs (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    trigger TEXT NOT NULL DEFAULT 'manual',
    status TEXT NOT NULL DEFAULT 'queued',
    commit_sha TEXT,
    commit_message TEXT,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    duration_ms INTEGER,
    log_path TEXT,
    error TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_deployment_runs_project_started
    ON deployment_runs(project_id, started_at DESC);
"""

# Preserve saved provider credentials while moving runtime configuration to the
# generic agent namespace. `INSERT OR IGNORE` makes this safe to run on every
# startup and ensures a new setting always wins over a migrated value.
AGENT_SETTING_MIGRATIONS = (
    ("agent_default_model_profile", "continue_default_model_profile"),
    ("agent_syra_nano_api_key", "continue_syra_nano_api_key"),
    ("agent_syra_havy_api_key", "continue_syra_havy_api_key"),
)


async def init_db() -> None:
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_workspaces_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=str(settings.resolved_db_path))
        await db.executescript(SCHEMA)
        await _migrate(db)
        await db.commit()


async def _migrate(db: aiosqlite.Connection) -> None:
    async with db.execute("PRAGMA table_info(projects)") as cur:
        cols = {row[1] for row in await cur.fetchall()}
    if "deploy_type" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN deploy_type TEXT DEFAULT 'shell'")
    if "dockerfile_path" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN dockerfile_path TEXT")
    if "preview_port" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN preview_port INTEGER")
    if "preview_status" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN preview_status TEXT DEFAULT 'stopped'")
    if "preview_domain" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN preview_domain TEXT")
    if "custom_tls_domain" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN custom_tls_domain TEXT")
    if "custom_tls_enabled" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN custom_tls_enabled INTEGER DEFAULT 0")
    if "agent_port" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_port INTEGER")
    if "agent_status" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_status TEXT DEFAULT 'stopped'")
    if "agent_runtime" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_runtime TEXT DEFAULT 'project'")
    if "agent_model_profile" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_model_profile TEXT")
    if "agent_last_started_at" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_last_started_at TEXT")
    if "agent_last_error" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_last_error TEXT")
    if "agent_config_path" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_config_path TEXT")
    if "agent_conversation_id" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN agent_conversation_id TEXT")
    if "preview_started_at" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN preview_started_at TEXT")
    if "healthcheck_path" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN healthcheck_path TEXT DEFAULT '/'")
    if "healthcheck_interval" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN healthcheck_interval INTEGER DEFAULT 30")
    if "auto_deploy" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN auto_deploy INTEGER DEFAULT 0")
    if "resource_memory" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN resource_memory TEXT")
    if "resource_cpus" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN resource_cpus TEXT")
    if "docker_image" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN docker_image TEXT")
    if "compose_file" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN compose_file TEXT")
    for new_key, old_key in AGENT_SETTING_MIGRATIONS:
        await db.execute(
            "INSERT OR IGNORE INTO system_settings (key, value) "
            "SELECT ?, value FROM system_settings WHERE key = ?",
            (new_key, old_key),
        )


async def get_setting(key: str, default: str = "") -> str:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT value FROM system_settings WHERE key = ?", (key,)
        ) as cursor:
            row = await cursor.fetchone()
            if not row or row["value"] is None:
                return default
            return row["value"]


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
            (id, name, git_url, branch, port, domain, start_command, env_vars,
             deploy_type, dockerfile_path, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["id"],
                data["name"],
                data.get("git_url"),
                data.get("branch", "main"),
                data["port"],
                data.get("domain"),
                data.get("start_command", ""),
                json.dumps(data.get("env_vars", {})),
                data.get("deploy_type", "shell"),
                data.get("dockerfile_path"),
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
        "start_command", "env_vars", "status", "deploy_type", "dockerfile_path",
        "preview_port", "preview_status", "preview_domain", "preview_started_at",
        "agent_port", "agent_status", "agent_runtime", "agent_model_profile",
        "agent_last_started_at", "agent_last_error", "agent_config_path",
        "agent_conversation_id",
        "custom_tls_domain", "custom_tls_enabled",
        "healthcheck_path", "healthcheck_interval", "auto_deploy",
        "resource_memory", "resource_cpus", "docker_image", "compose_file",
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


async def create_deployment_run(project_id: str, trigger: str = "manual") -> dict[str, Any]:
    import uuid
    run = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "trigger": trigger,
        "status": "queued",
        "started_at": _now(),
    }
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO deployment_runs (id, project_id, trigger, status, started_at) VALUES (?, ?, ?, ?, ?)",
            (run["id"], project_id, trigger, run["status"], run["started_at"]),
        )
        await db.commit()
    return run


async def update_deployment_run(run_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    fields = {key: value for key, value in updates.items() if key in {
        "status", "commit_sha", "commit_message", "finished_at", "duration_ms", "log_path", "error"
    }}
    if not fields:
        return await get_deployment_run(run_id)
    assignments = ", ".join(f"{key} = ?" for key in fields)
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(f"UPDATE deployment_runs SET {assignments} WHERE id = ?", [*fields.values(), run_id])
        await db.commit()
    return await get_deployment_run(run_id)


async def get_deployment_run(run_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM deployment_runs WHERE id = ?", (run_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def list_deployment_runs(project_id: str, limit: int = 20) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 100))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM deployment_runs WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
            (project_id, safe_limit),
        ) as cur:
            return [dict(row) for row in await cur.fetchall()]


async def create_api_token(name: str, prefix: str, token_hash: str) -> dict[str, Any]:
    import uuid
    token_id = uuid.uuid4().hex[:12]
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO api_tokens (id, name, prefix, token_hash, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (token_id, name, prefix, token_hash, now),
        )
        await db.commit()
    return {
        "id": token_id,
        "name": name,
        "prefix": prefix,
        "created_at": now,
    }


async def list_api_tokens() -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, prefix, created_at, last_used_at FROM api_tokens ORDER BY created_at DESC"
        ) as cur:
            return [dict(r) for r in await cur.fetchall()]


async def get_api_token_by_hash(token_hash: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM api_tokens WHERE token_hash = ?", (token_hash,)
        ) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def touch_api_token(token_id: str) -> None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "UPDATE api_tokens SET last_used_at = ? WHERE id = ?",
            (_now(), token_id),
        )
        await db.commit()


async def delete_api_token(token_id: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute("DELETE FROM api_tokens WHERE id = ?", (token_id,))
        await db.commit()
        return cursor.rowcount > 0


async def count_operator_accounts() -> int:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        async with db.execute("SELECT COUNT(*) FROM operator_accounts") as cur:
            row = await cur.fetchone()
            return int(row[0] if row else 0)


async def get_operator_account_by_email(email: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM operator_accounts WHERE email = ? COLLATE NOCASE", (email.strip(),)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def get_operator_account(account_id: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM operator_accounts WHERE id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def create_operator_account(data: dict[str, Any]) -> dict[str, Any]:
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO operator_accounts
            (id, email, password_hash, display_name, avatar_icon, role, created_at, updated_at, last_login_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data["id"], data["email"].strip().lower(), data["password_hash"], data.get("display_name", ""),
             data.get("avatar_icon", "user"), data.get("role", "operator"), now, now, None),
        )
        await db.commit()
    return (await get_operator_account(data["id"])) or data


async def update_operator_account(account_id: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {"display_name", "avatar_icon", "last_login_at", "password_hash"}
    pairs = [(key, value) for key, value in updates.items() if key in allowed]
    if not pairs:
        return await get_operator_account(account_id)
    pairs.append(("updated_at", _now()))
    columns = ", ".join(f"{key} = ?" for key, _ in pairs)
    values = [value for _, value in pairs] + [account_id]
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(f"UPDATE operator_accounts SET {columns} WHERE id = ?", values)
        await db.commit()
    return await get_operator_account(account_id)
