import json
import time
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
    in_app_notifications INTEGER NOT NULL DEFAULT 0,
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
    last_used_at TEXT,
    expires_at TEXT,
    scopes TEXT NOT NULL DEFAULT '["read","write","deploy","certificates","settings"]',
    rate_limit_per_minute INTEGER NOT NULL DEFAULT 60
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

CREATE TABLE IF NOT EXISTS github_oauth_states (
    state TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    redirect_uri TEXT NOT NULL,
    expires_at INTEGER NOT NULL,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_github_oauth_states_account
    ON github_oauth_states(account_id, expires_at DESC);

CREATE TABLE IF NOT EXISTS github_connections (
    account_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL DEFAULT 'github',
    login TEXT NOT NULL DEFAULT '',
    avatar_url TEXT NOT NULL DEFAULT '',
    token_ciphertext TEXT NOT NULL,
    scopes TEXT NOT NULL DEFAULT '',
    connected_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notification_events (
    id TEXT PRIMARY KEY,
    project_id TEXT,
    event TEXT NOT NULL,
    title TEXT NOT NULL,
    message TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}',
    is_read INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notification_events_created
    ON notification_events(created_at DESC);

CREATE TABLE IF NOT EXISTS pwa_push_subscriptions (
    endpoint TEXT PRIMARY KEY,
    account_id TEXT NOT NULL,
    subscription_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pwa_push_subscriptions_account
    ON pwa_push_subscriptions(account_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS release_environments (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    branch TEXT NOT NULL DEFAULT 'main',
    domain TEXT NOT NULL DEFAULT '',
    auto_deploy INTEGER NOT NULL DEFAULT 0,
    require_approval INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, kind),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_release_environments_project
    ON release_environments(project_id, kind);

CREATE TABLE IF NOT EXISTS release_policies (
    project_id TEXT PRIMARY KEY,
    deployment_strategy TEXT NOT NULL DEFAULT 'rolling',
    canary_percent INTEGER NOT NULL DEFAULT 10,
    preview_enabled INTEGER NOT NULL DEFAULT 1,
    preview_retention_days INTEGER NOT NULL DEFAULT 7,
    resource_alert_percent INTEGER NOT NULL DEFAULT 85,
    storage_limit_mb INTEGER NOT NULL DEFAULT 0,
    backup_enabled INTEGER NOT NULL DEFAULT 0,
    backup_schedule TEXT NOT NULL DEFAULT 'daily',
    backup_retention_days INTEGER NOT NULL DEFAULT 14,
    last_restore_check_at TEXT,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS release_approvals (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    environment_id TEXT NOT NULL,
    requested_by TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    approved_by TEXT NOT NULL DEFAULT '',
    note TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    decided_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(environment_id) REFERENCES release_environments(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_release_approvals_project_status
    ON release_approvals(project_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS project_team_members (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    email TEXT NOT NULL,
    display_name TEXT NOT NULL DEFAULT '',
    role TEXT NOT NULL DEFAULT 'viewer',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(project_id, email),
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_project_team_members_project
    ON project_team_members(project_id, role);

CREATE TABLE IF NOT EXISTS release_restore_points (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    label TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'deployment',
    status TEXT NOT NULL DEFAULT 'available',
    artifact_path TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    verified_at TEXT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS release_events (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    environment_id TEXT,
    event_type TEXT NOT NULL,
    severity TEXT NOT NULL DEFAULT 'info',
    title TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE,
    FOREIGN KEY(environment_id) REFERENCES release_environments(id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_release_events_project_created
    ON release_events(project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS share_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    summary TEXT NOT NULL,
    description TEXT NOT NULL,
    framework TEXT NOT NULL,
    runtime TEXT NOT NULL,
    source_dir TEXT NOT NULL,
    icon TEXT NOT NULL DEFAULT 'layout-template',
    is_syte_hosted INTEGER NOT NULL DEFAULT 1,
    is_available INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS share_instances (
    id TEXT PRIMARY KEY,
    template_id TEXT NOT NULL,
    project_id TEXT NOT NULL UNIQUE,
    owner_account_id TEXT NOT NULL DEFAULT '',
    instance_key_hash TEXT NOT NULL UNIQUE,
    access_password_hash TEXT NOT NULL DEFAULT '',
    access_configured_at TEXT,
    status TEXT NOT NULL DEFAULT 'provisioning',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_used_at TEXT,
    FOREIGN KEY(template_id) REFERENCES share_templates(id) ON DELETE RESTRICT,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_share_instances_owner
    ON share_instances(owner_account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS project_redirects (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_url TEXT NOT NULL,
    status_code INTEGER NOT NULL DEFAULT 301,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(project_id) REFERENCES projects(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_project_redirects_project
    ON project_redirects(project_id, created_at DESC);
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
    async with db.execute("PRAGMA table_info(api_tokens)") as cur:
        token_cols = {row[1] for row in await cur.fetchall()}
    if "expires_at" not in token_cols:
        await db.execute("ALTER TABLE api_tokens ADD COLUMN expires_at TEXT")
    if "scopes" not in token_cols:
        await db.execute("ALTER TABLE api_tokens ADD COLUMN scopes TEXT NOT NULL DEFAULT '[\"read\",\"write\",\"deploy\",\"certificates\",\"settings\"]'")
    if "rate_limit_per_minute" not in token_cols:
        await db.execute("ALTER TABLE api_tokens ADD COLUMN rate_limit_per_minute INTEGER NOT NULL DEFAULT 60")
    if "github_account_id" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN github_account_id TEXT DEFAULT ''")
    if "last_seen_git_commit" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN last_seen_git_commit TEXT DEFAULT ''")
    if "last_deployed_commit" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN last_deployed_commit TEXT DEFAULT ''")
    if "resource_memory" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN resource_memory TEXT")
    if "resource_cpus" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN resource_cpus TEXT")
    if "docker_image" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN docker_image TEXT")
    if "in_app_notifications" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN in_app_notifications INTEGER NOT NULL DEFAULT 0")
    if "compose_file" not in cols:
        await db.execute("ALTER TABLE projects ADD COLUMN compose_file TEXT")
    async with db.execute("PRAGMA table_info(share_instances)") as cur:
        share_instance_cols = {row[1] for row in await cur.fetchall()}
    if share_instance_cols and "access_password_hash" not in share_instance_cols:
        await db.execute("ALTER TABLE share_instances ADD COLUMN access_password_hash TEXT NOT NULL DEFAULT ''")
    if share_instance_cols and "access_configured_at" not in share_instance_cols:
        await db.execute("ALTER TABLE share_instances ADD COLUMN access_configured_at TEXT")
    async with db.execute("PRAGMA table_info(release_restore_points)") as cur:
        restore_cols = {row[1] for row in await cur.fetchall()}
    if restore_cols and "artifact_path" not in restore_cols:
        await db.execute("ALTER TABLE release_restore_points ADD COLUMN artifact_path TEXT NOT NULL DEFAULT ''")
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
             deploy_type, dockerfile_path, status, in_app_notifications, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
                int(bool(data.get("in_app_notifications", False))),
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
        "github_account_id", "last_seen_git_commit", "last_deployed_commit",
        "resource_memory", "resource_cpus", "docker_image", "compose_file",
        "in_app_notifications",
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


async def create_deployment_run(project_id: str, trigger: str = "manual", commit_sha: str = "") -> dict[str, Any]:
    import uuid
    run = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "trigger": trigger,
        "status": "queued",
        "commit_sha": commit_sha,
        "started_at": _now(),
    }
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            "INSERT INTO deployment_runs (id, project_id, trigger, status, commit_sha, started_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run["id"], project_id, trigger, run["status"], run["commit_sha"], run["started_at"]),
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


async def create_api_token(
    name: str,
    prefix: str,
    token_hash: str,
    *,
    expires_at: str | None = None,
    scopes: list[str] | None = None,
    rate_limit_per_minute: int = 60,
) -> dict[str, Any]:
    import uuid
    token_id = uuid.uuid4().hex[:12]
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO api_tokens (id, name, prefix, token_hash, created_at, expires_at, scopes, rate_limit_per_minute)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (token_id, name, prefix, token_hash, now, expires_at, json.dumps(scopes or ["read", "write", "deploy", "certificates", "settings"]), int(rate_limit_per_minute)),
        )
        await db.commit()
    return {
        "id": token_id,
        "name": name,
        "prefix": prefix,
        "created_at": now,
        "expires_at": expires_at,
        "scopes": scopes or ["read", "write", "deploy", "certificates", "settings"],
        "rate_limit_per_minute": int(rate_limit_per_minute),
    }


async def list_api_tokens() -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT id, name, prefix, created_at, last_used_at, expires_at, scopes, rate_limit_per_minute FROM api_tokens ORDER BY created_at DESC"
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    for row in rows:
        try:
            row["scopes"] = json.loads(row.get("scopes") or "[]")
        except (TypeError, json.JSONDecodeError):
            row["scopes"] = []
    return rows


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


async def create_github_oauth_state(state: str, account_id: str, redirect_uri: str, expires_at: int) -> None:
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute("DELETE FROM github_oauth_states WHERE expires_at <= ?", (int(time.time()),))
        await db.execute(
            "INSERT INTO github_oauth_states (state, account_id, redirect_uri, expires_at, created_at) VALUES (?, ?, ?, ?, ?)",
            (state, account_id, redirect_uri, int(expires_at), now),
        )
        await db.commit()


async def consume_github_oauth_state(state: str) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM github_oauth_states WHERE state = ?", (state,)) as cur:
            row = await cur.fetchone()
        await db.execute("DELETE FROM github_oauth_states WHERE state = ?", (state,))
        await db.execute("DELETE FROM github_oauth_states WHERE expires_at <= ?", (int(time.time()),))
        await db.commit()
    if not row:
        return None
    value = dict(row)
    return value if int(value["expires_at"]) > int(time.time()) else None


async def get_github_connection(account_id: str, *, include_token: bool = False) -> dict[str, Any] | None:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM github_connections WHERE account_id = ?", (account_id,)) as cur:
            row = await cur.fetchone()
    if not row:
        return None
    connection = dict(row)
    if not include_token:
        connection.pop("token_ciphertext", None)
    return connection


async def save_github_connection(
    account_id: str,
    *,
    login: str,
    avatar_url: str,
    token_ciphertext: str,
    scopes: str,
) -> dict[str, Any]:
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO github_connections
            (account_id, provider, login, avatar_url, token_ciphertext, scopes, connected_at, updated_at)
            VALUES (?, 'github', ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
              login = excluded.login,
              avatar_url = excluded.avatar_url,
              token_ciphertext = excluded.token_ciphertext,
              scopes = excluded.scopes,
              updated_at = excluded.updated_at""",
            (account_id, login, avatar_url, token_ciphertext, scopes, now, now),
        )
        await db.commit()
    return (await get_github_connection(account_id)) or {"account_id": account_id, "login": login}


async def delete_github_connection(account_id: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute("DELETE FROM github_connections WHERE account_id = ?", (account_id,))
        await db.commit()
        return cursor.rowcount > 0


async def create_notification_event(
    *,
    event: str,
    title: str,
    message: str,
    project_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    import uuid

    record = {
        "id": uuid.uuid4().hex[:16],
        "project_id": project_id,
        "event": event,
        "title": title,
        "message": message,
        "payload": json.dumps(payload or {}),
        "is_read": 0,
        "created_at": _now(),
    }
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO notification_events
            (id, project_id, event, title, message, payload, is_read, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            tuple(record.values()),
        )
        await db.commit()
    return {**record, "payload": payload or {}, "is_read": False}


async def list_notification_events(limit: int = 100) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 250))
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM notification_events ORDER BY created_at DESC LIMIT ?", (safe_limit,)
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for row in rows:
        try:
            row["payload"] = json.loads(row.get("payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            row["payload"] = {}
        row["is_read"] = bool(row.get("is_read"))
    return rows


async def mark_notification_events_read(event_ids: list[str] | None = None) -> int:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        if event_ids:
            placeholders = ",".join("?" for _ in event_ids)
            cursor = await db.execute(
                f"UPDATE notification_events SET is_read = 1 WHERE id IN ({placeholders})", event_ids
            )
        else:
            cursor = await db.execute("UPDATE notification_events SET is_read = 1 WHERE is_read = 0")
        await db.commit()
        return int(cursor.rowcount)


async def upsert_pwa_push_subscription(account_id: str, subscription: dict[str, Any]) -> None:
    endpoint = str(subscription.get("endpoint") or "").strip()
    if not endpoint:
        raise ValueError("A browser push subscription must include an endpoint.")
    encoded = json.dumps(subscription, separators=(",", ":"))
    now = _now()
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO pwa_push_subscriptions
            (endpoint, account_id, subscription_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(endpoint) DO UPDATE SET
              account_id = excluded.account_id,
              subscription_json = excluded.subscription_json,
              updated_at = excluded.updated_at""",
            (endpoint, account_id, encoded, now, now),
        )
        await db.commit()


async def list_pwa_push_subscriptions() -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT endpoint, subscription_json FROM pwa_push_subscriptions") as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    subscriptions: list[dict[str, Any]] = []
    for row in rows:
        try:
            subscriptions.append(json.loads(row["subscription_json"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
    return subscriptions


async def delete_pwa_push_subscription(endpoint: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute("DELETE FROM pwa_push_subscriptions WHERE endpoint = ?", (endpoint,))
        await db.commit()
        return bool(cursor.rowcount)


async def list_project_redirects(project_id: str) -> list[dict[str, Any]]:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM project_redirects WHERE project_id = ? ORDER BY created_at DESC", (project_id,)
        ) as cur:
            rows = [dict(row) for row in await cur.fetchall()]
    for r in rows:
        r["is_active"] = bool(r.get("is_active"))
    return rows


async def create_project_redirect(
    project_id: str,
    source_path: str,
    target_url: str,
    status_code: int = 301,
) -> dict[str, Any]:
    redirect_id = str(uuid.uuid4())
    now = _now()
    clean_src = "/" + source_path.strip().lstrip("/")
    clean_target = target_url.strip()
    code = 302 if int(status_code) == 302 else 301
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        await db.execute(
            """INSERT INTO project_redirects (id, project_id, source_path, target_url, status_code, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, 1, ?, ?)""",
            (redirect_id, project_id, clean_src, clean_target, code, now, now),
        )
        await db.commit()
    return {
        "id": redirect_id,
        "project_id": project_id,
        "source_path": clean_src,
        "target_url": clean_target,
        "status_code": code,
        "is_active": True,
        "created_at": now,
        "updated_at": now,
    }


async def delete_project_redirect(project_id: str, redirect_id: str) -> bool:
    async with aiosqlite.connect(settings.resolved_db_path) as db:
        cursor = await db.execute(
            "DELETE FROM project_redirects WHERE id = ? AND project_id = ?", (redirect_id, project_id)
        )
        await db.commit()
        return bool(cursor.rowcount)
