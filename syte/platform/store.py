"""SQLite persistence for the platform layer.

Follows the conventions in :mod:`syte.database`: a module-level ``SCHEMA``
applied with ``executescript``, hand-rolled additive migrations guarded by
``PRAGMA table_info``, one short-lived ``aiosqlite`` connection per accessor,
``Row`` factory, and plain ``dict`` returns.

Two deliberate departures:

* Every table is prefixed ``platform_`` so nothing collides with the existing
  ``projects`` table — which in Syte means "one deployed app", whereas a
  *project* in Coolify's vocabulary is a grouping that contains environments.
  The API layer speaks Coolify's vocabulary; the schema stays unambiguous.

* Update whitelists are derived from ``PRAGMA table_info`` and cached rather
  than duplicated as hand-maintained ``allowed`` sets. ``syte.database`` keeps
  those by hand, and the result is that adding a column requires touching three
  places and silently no-ops if you miss one. Reading the live schema removes
  that failure mode entirely while keeping the same "only real columns may be
  written" guarantee.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import Any

import aiosqlite

from syte.config import settings
from syte.platform.types import (
    DeploymentStatus,
    ResourceType,
    new_uuid,
    utcnow,
)

logger = logging.getLogger("syte.platform.store")


# --------------------------------------------------------------------------- #
# Schema
# --------------------------------------------------------------------------- #

SCHEMA = """
-- Teams own servers, projects and credentials. A single-operator install gets
-- one personal team created on first boot, so nothing in the API needs a
-- "team is optional" branch.
CREATE TABLE IF NOT EXISTS platform_teams (
    uuid TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    personal_team INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_team_members (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT DEFAULT '',
    role TEXT DEFAULT 'member',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_team_members_email
    ON platform_team_members (team_uuid, email);

-- SSH keys used to reach remote servers and to clone private repositories.
CREATE TABLE IF NOT EXISTS platform_private_keys (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    private_key TEXT NOT NULL,
    public_key TEXT DEFAULT '',
    fingerprint TEXT DEFAULT '',
    is_git_related INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_servers (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    ip TEXT NOT NULL DEFAULT '127.0.0.1',
    "user" TEXT NOT NULL DEFAULT 'root',
    port INTEGER NOT NULL DEFAULT 22,
    private_key_uuid TEXT,
    is_local INTEGER DEFAULT 0,
    proxy_type TEXT DEFAULT 'CADDY',
    proxy_status TEXT DEFAULT 'exited',
    status TEXT DEFAULT 'unknown',
    is_reachable INTEGER DEFAULT 0,
    is_usable INTEGER DEFAULT 0,
    is_build_server INTEGER DEFAULT 0,
    is_swarm_manager INTEGER DEFAULT 0,
    is_swarm_worker INTEGER DEFAULT 0,
    is_terminal_enabled INTEGER DEFAULT 1,
    is_metrics_enabled INTEGER DEFAULT 1,
    is_sentinel_enabled INTEGER DEFAULT 0,
    wildcard_domain TEXT DEFAULT '',
    concurrent_builds INTEGER DEFAULT 2,
    deployment_queue_limit INTEGER DEFAULT 20,
    dynamic_timeout INTEGER DEFAULT 3600,
    connection_timeout INTEGER DEFAULT 30,
    docker_version TEXT DEFAULT '',
    docker_cleanup_frequency TEXT DEFAULT '0 0 * * *',
    docker_cleanup_threshold INTEGER DEFAULT 80,
    delete_unused_volumes INTEGER DEFAULT 0,
    delete_unused_networks INTEGER DEFAULT 0,
    unreachable_count INTEGER DEFAULT 0,
    unreachable_notification_sent INTEGER DEFAULT 0,
    high_disk_usage_notification_sent INTEGER DEFAULT 0,
    validation_logs TEXT DEFAULT '',
    sentinel_token TEXT DEFAULT '',
    last_seen_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- A destination is a docker network on a server. Resources in the same
-- destination resolve each other by container name, which is how a compose
-- service talks to a managed database without publishing a port.
CREATE TABLE IF NOT EXISTS platform_destinations (
    uuid TEXT PRIMARY KEY,
    server_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    network TEXT NOT NULL,
    kind TEXT DEFAULT 'standalone',
    is_default INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_destinations_network
    ON platform_destinations (server_uuid, network);

CREATE TABLE IF NOT EXISTS platform_projects (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_environments (
    uuid TEXT PRIMARY KEY,
    project_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_environments_name
    ON platform_environments (project_uuid, name);

-- Applications are git/Dockerfile/image/compose backed resources. Column names
-- follow Coolify's Application model so an operator reading the API response
-- sees familiar keys.
CREATE TABLE IF NOT EXISTS platform_applications (
    uuid TEXT PRIMARY KEY,
    environment_uuid TEXT NOT NULL,
    server_uuid TEXT NOT NULL,
    destination_uuid TEXT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    fqdn TEXT DEFAULT '',
    build_pack TEXT DEFAULT 'nixpacks',
    static_image TEXT DEFAULT 'nginx:alpine',

    git_repository TEXT DEFAULT '',
    git_branch TEXT DEFAULT 'main',
    git_commit_sha TEXT DEFAULT '',
    git_full_url TEXT DEFAULT '',
    git_provider TEXT DEFAULT 'github',
    git_auth_method TEXT DEFAULT 'public',
    git_source_uuid TEXT,
    private_key_uuid TEXT,
    watch_paths TEXT DEFAULT '',
    auto_deploy INTEGER DEFAULT 1,
    preview_deployments_enabled INTEGER DEFAULT 0,
    preview_url_template TEXT DEFAULT 'pr-{{pr_id}}-{{app}}.{{domain}}',

    docker_registry_image_name TEXT DEFAULT '',
    docker_registry_image_tag TEXT DEFAULT '',
    docker_registry_username TEXT DEFAULT '',
    docker_registry_password TEXT DEFAULT '',

    install_command TEXT DEFAULT '',
    build_command TEXT DEFAULT '',
    start_command TEXT DEFAULT '',
    base_directory TEXT DEFAULT '/',
    publish_directory TEXT DEFAULT '',
    dockerfile TEXT DEFAULT '',
    dockerfile_location TEXT DEFAULT '/Dockerfile',
    dockerfile_target_build TEXT DEFAULT '',
    docker_compose_location TEXT DEFAULT '/docker-compose.yaml',
    docker_compose TEXT DEFAULT '',
    docker_compose_raw TEXT DEFAULT '',
    docker_compose_domains TEXT DEFAULT '{}',
    custom_docker_run_options TEXT DEFAULT '',
    custom_labels TEXT DEFAULT '',
    custom_network_aliases TEXT DEFAULT '',

    ports_exposes TEXT DEFAULT '3000',
    ports_mappings TEXT DEFAULT '',

    pre_deployment_command TEXT DEFAULT '',
    pre_deployment_command_container TEXT DEFAULT '',
    post_deployment_command TEXT DEFAULT '',
    post_deployment_command_container TEXT DEFAULT '',

    health_check_enabled INTEGER DEFAULT 1,
    health_check_path TEXT DEFAULT '/',
    health_check_port INTEGER,
    health_check_host TEXT DEFAULT '127.0.0.1',
    health_check_method TEXT DEFAULT 'GET',
    health_check_scheme TEXT DEFAULT 'http',
    health_check_return_code INTEGER DEFAULT 200,
    health_check_response_text TEXT DEFAULT '',
    health_check_interval INTEGER DEFAULT 30,
    health_check_timeout INTEGER DEFAULT 30,
    health_check_retries INTEGER DEFAULT 3,
    health_check_start_period INTEGER DEFAULT 30,
    health_check_command TEXT DEFAULT '',

    limits_memory TEXT DEFAULT '',
    limits_memory_swap TEXT DEFAULT '',
    limits_memory_swappiness INTEGER,
    limits_memory_reservation TEXT DEFAULT '',
    limits_cpus TEXT DEFAULT '',
    limits_cpuset TEXT DEFAULT '',
    limits_cpu_shares INTEGER,
    limits_pids INTEGER,

    redirect TEXT DEFAULT 'both',
    is_http_basic_auth_enabled INTEGER DEFAULT 0,
    http_basic_auth_username TEXT DEFAULT '',
    http_basic_auth_password TEXT DEFAULT '',
    is_force_https_enabled INTEGER DEFAULT 1,
    is_gzip_enabled INTEGER DEFAULT 1,
    is_strip_prefix_enabled INTEGER DEFAULT 0,
    connect_to_docker_network INTEGER DEFAULT 1,
    rolling_update_enabled INTEGER DEFAULT 0,
    max_restart_count INTEGER DEFAULT 10,

    manual_webhook_secret_github TEXT DEFAULT '',
    manual_webhook_secret_gitlab TEXT DEFAULT '',
    manual_webhook_secret_gitea TEXT DEFAULT '',
    manual_webhook_secret_bitbucket TEXT DEFAULT '',

    status TEXT DEFAULT 'stopped',
    config_hash TEXT DEFAULT '',
    current_image_tag TEXT DEFAULT '',
    previous_image_tag TEXT DEFAULT '',
    last_deployment_uuid TEXT DEFAULT '',
    last_deployed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_applications_env
    ON platform_applications (environment_uuid);

CREATE TABLE IF NOT EXISTS platform_databases (
    uuid TEXT PRIMARY KEY,
    environment_uuid TEXT NOT NULL,
    server_uuid TEXT NOT NULL,
    destination_uuid TEXT,
    database_type TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    image TEXT NOT NULL,
    "version" TEXT DEFAULT '',

    username TEXT DEFAULT '',
    password TEXT DEFAULT '',
    root_password TEXT DEFAULT '',
    database_name TEXT DEFAULT '',
    init_script TEXT DEFAULT '',
    custom_conf TEXT DEFAULT '',
    custom_docker_run_options TEXT DEFAULT '',

    internal_port INTEGER NOT NULL,
    public_port INTEGER,
    is_public INTEGER DEFAULT 0,

    limits_memory TEXT DEFAULT '',
    limits_memory_swap TEXT DEFAULT '',
    limits_memory_swappiness INTEGER,
    limits_memory_reservation TEXT DEFAULT '',
    limits_cpus TEXT DEFAULT '',
    limits_cpuset TEXT DEFAULT '',
    limits_cpu_shares INTEGER,
    limits_pids INTEGER,

    enable_ssl INTEGER DEFAULT 0,
    ssl_mode TEXT DEFAULT 'prefer',

    status TEXT DEFAULT 'stopped',
    config_hash TEXT DEFAULT '',
    last_started_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_databases_env
    ON platform_databases (environment_uuid);

CREATE TABLE IF NOT EXISTS platform_services (
    uuid TEXT PRIMARY KEY,
    environment_uuid TEXT NOT NULL,
    server_uuid TEXT NOT NULL,
    destination_uuid TEXT,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    template_key TEXT DEFAULT '',
    service_type TEXT DEFAULT 'custom',
    docker_compose_raw TEXT DEFAULT '',
    docker_compose TEXT DEFAULT '',
    connect_to_docker_network INTEGER DEFAULT 1,
    status TEXT DEFAULT 'stopped',
    config_hash TEXT DEFAULT '',
    last_deployed_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_services_env
    ON platform_services (environment_uuid);

-- One row per container inside a compose-backed service, so the dashboard can
-- show per-container status and assign a domain to just the web container.
CREATE TABLE IF NOT EXISTS platform_service_containers (
    uuid TEXT PRIMARY KEY,
    service_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    kind TEXT DEFAULT 'application',
    image TEXT DEFAULT '',
    fqdn TEXT DEFAULT '',
    exclude_from_status INTEGER DEFAULT 0,
    is_public INTEGER DEFAULT 0,
    public_port INTEGER,
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_service_containers_service
    ON platform_service_containers (service_uuid);

CREATE TABLE IF NOT EXISTS platform_env_vars (
    uuid TEXT PRIMARY KEY,
    resource_uuid TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT DEFAULT '',
    is_build_time INTEGER DEFAULT 0,
    is_runtime INTEGER DEFAULT 1,
    is_literal INTEGER DEFAULT 0,
    is_multiline INTEGER DEFAULT 0,
    is_secret INTEGER DEFAULT 0,
    is_shown_once INTEGER DEFAULT 0,
    is_preview INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_env_vars_key
    ON platform_env_vars (resource_uuid, key, is_preview);

CREATE TABLE IF NOT EXISTS platform_shared_env_vars (
    uuid TEXT PRIMARY KEY,
    scope TEXT NOT NULL,
    scope_uuid TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT DEFAULT '',
    is_secret INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_shared_env_vars_key
    ON platform_shared_env_vars (scope, scope_uuid, key);

CREATE TABLE IF NOT EXISTS platform_deployments (
    uuid TEXT PRIMARY KEY,
    resource_uuid TEXT NOT NULL,
    resource_type TEXT DEFAULT 'application',
    resource_name TEXT DEFAULT '',
    server_uuid TEXT DEFAULT '',
    server_name TEXT DEFAULT '',
    status TEXT DEFAULT 'queued',
    "trigger" TEXT DEFAULT 'manual',
    pull_request_id INTEGER DEFAULT 0,
    "commit" TEXT DEFAULT '',
    commit_message TEXT DEFAULT '',
    build_pack TEXT DEFAULT '',
    image_tag TEXT DEFAULT '',
    rollback_image_tag TEXT DEFAULT '',
    config_hash TEXT DEFAULT '',
    force_rebuild INTEGER DEFAULT 0,
    restart_only INTEGER DEFAULT 0,
    rollback INTEGER DEFAULT 0,
    is_webhook INTEGER DEFAULT 0,
    is_api INTEGER DEFAULT 0,
    requested_by TEXT DEFAULT '',
    plan TEXT DEFAULT '{}',
    logs TEXT DEFAULT '',
    error TEXT DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_deployments_resource
    ON platform_deployments (resource_uuid, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_platform_deployments_status
    ON platform_deployments (status);

CREATE TABLE IF NOT EXISTS platform_previews (
    uuid TEXT PRIMARY KEY,
    application_uuid TEXT NOT NULL,
    pull_request_id INTEGER NOT NULL,
    pull_request_title TEXT DEFAULT '',
    pull_request_html_url TEXT DEFAULT '',
    branch TEXT DEFAULT '',
    fqdn TEXT DEFAULT '',
    docker_compose_domains TEXT DEFAULT '{}',
    status TEXT DEFAULT 'stopped',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_previews_pr
    ON platform_previews (application_uuid, pull_request_id);

CREATE TABLE IF NOT EXISTS platform_volumes (
    uuid TEXT PRIMARY KEY,
    resource_uuid TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    kind TEXT DEFAULT 'volume',
    name TEXT NOT NULL,
    mount_path TEXT NOT NULL,
    host_path TEXT DEFAULT '',
    content TEXT DEFAULT '',
    is_directory INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_volumes_resource
    ON platform_volumes (resource_uuid);

CREATE TABLE IF NOT EXISTS platform_scheduled_tasks (
    uuid TEXT PRIMARY KEY,
    resource_uuid TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    name TEXT NOT NULL,
    command TEXT NOT NULL,
    container TEXT DEFAULT '',
    frequency TEXT NOT NULL,
    timeout INTEGER DEFAULT 600,
    enabled INTEGER DEFAULT 1,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_task_executions (
    uuid TEXT PRIMARY KEY,
    task_uuid TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    message TEXT DEFAULT '',
    exit_code INTEGER,
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_task_executions_task
    ON platform_task_executions (task_uuid, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_s3_storages (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    description TEXT DEFAULT '',
    endpoint TEXT NOT NULL,
    bucket TEXT NOT NULL,
    region TEXT DEFAULT 'us-east-1',
    access_key TEXT NOT NULL,
    secret_key TEXT NOT NULL,
    use_path_style INTEGER DEFAULT 1,
    is_usable INTEGER DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_backups (
    uuid TEXT PRIMARY KEY,
    database_uuid TEXT NOT NULL,
    enabled INTEGER DEFAULT 1,
    frequency TEXT DEFAULT '0 0 * * *',
    save_locally INTEGER DEFAULT 1,
    databases_to_backup TEXT DEFAULT '',
    dump_all INTEGER DEFAULT 0,
    retention_amount_locally INTEGER DEFAULT 7,
    retention_days_locally INTEGER DEFAULT 0,
    retention_amount_s3 INTEGER DEFAULT 0,
    retention_days_s3 INTEGER DEFAULT 0,
    s3_storage_uuid TEXT,
    last_run_at TEXT,
    next_run_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_backup_executions (
    uuid TEXT PRIMARY KEY,
    backup_uuid TEXT NOT NULL,
    status TEXT DEFAULT 'running',
    message TEXT DEFAULT '',
    filename TEXT DEFAULT '',
    size INTEGER DEFAULT 0,
    s3_key TEXT DEFAULT '',
    upload_status TEXT DEFAULT '',
    started_at TEXT,
    finished_at TEXT,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_backup_executions_backup
    ON platform_backup_executions (backup_uuid, created_at DESC);

CREATE TABLE IF NOT EXISTS platform_notification_channels (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT DEFAULT '',
    enabled INTEGER DEFAULT 1,
    config TEXT DEFAULT '{}',
    events TEXT DEFAULT '[]',
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_git_sources (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    api_url TEXT DEFAULT '',
    html_url TEXT DEFAULT '',
    organization TEXT DEFAULT '',
    app_id TEXT DEFAULT '',
    installation_id TEXT DEFAULT '',
    client_id TEXT DEFAULT '',
    client_secret TEXT DEFAULT '',
    access_token TEXT DEFAULT '',
    webhook_secret TEXT DEFAULT '',
    private_key_uuid TEXT,
    is_system_wide INTEGER DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS platform_tags (
    uuid TEXT PRIMARY KEY,
    team_uuid TEXT NOT NULL,
    name TEXT NOT NULL,
    color TEXT DEFAULT '#6366f1',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_tags_name
    ON platform_tags (team_uuid, name);

CREATE TABLE IF NOT EXISTS platform_resource_tags (
    uuid TEXT PRIMARY KEY,
    tag_uuid TEXT NOT NULL,
    resource_uuid TEXT NOT NULL,
    resource_type TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_platform_resource_tags_pair
    ON platform_resource_tags (tag_uuid, resource_uuid);

CREATE TABLE IF NOT EXISTS platform_ssl_certificates (
    uuid TEXT PRIMARY KEY,
    resource_uuid TEXT DEFAULT '',
    resource_type TEXT DEFAULT '',
    server_uuid TEXT DEFAULT '',
    common_name TEXT NOT NULL,
    subject_alternative_names TEXT DEFAULT '',
    certificate TEXT DEFAULT '',
    private_key TEXT DEFAULT '',
    is_ca INTEGER DEFAULT 0,
    valid_until TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- Rolling window of server metrics powering the dashboard sparklines. Trimmed
-- by retention on write so the table cannot grow without bound.
CREATE TABLE IF NOT EXISTS platform_server_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    server_uuid TEXT NOT NULL,
    cpu_percent REAL DEFAULT 0,
    memory_percent REAL DEFAULT 0,
    memory_used_mb REAL DEFAULT 0,
    memory_total_mb REAL DEFAULT 0,
    disk_percent REAL DEFAULT 0,
    disk_used_gb REAL DEFAULT 0,
    disk_total_gb REAL DEFAULT 0,
    container_count INTEGER DEFAULT 0,
    recorded_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_server_metrics_server
    ON platform_server_metrics (server_uuid, recorded_at DESC);

CREATE TABLE IF NOT EXISTS platform_webhook_events (
    uuid TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    event TEXT DEFAULT '',
    repository TEXT DEFAULT '',
    branch TEXT DEFAULT '',
    "commit" TEXT DEFAULT '',
    pull_request_id INTEGER DEFAULT 0,
    matched_resources TEXT DEFAULT '[]',
    accepted INTEGER DEFAULT 0,
    message TEXT DEFAULT '',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_platform_webhook_events_created
    ON platform_webhook_events (created_at DESC);
"""


# Additive migrations. Same hand-rolled style as ``syte.database._migrate``:
# ``(table, column, DDL type clause)``. Applied only when the column is absent,
# so this is safe to run on every startup and safe to re-order.
MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    # Added after the first release of the platform layer; keeping them here
    # rather than only in SCHEMA is what upgrades an existing install.
    ("platform_applications", "rolling_update_enabled", "INTEGER DEFAULT 0"),
    ("platform_applications", "limits_pids", "INTEGER"),
    ("platform_applications", "git_source_uuid", "TEXT"),
    ("platform_applications", "preview_url_template", "TEXT DEFAULT 'pr-{{pr_id}}-{{app}}.{{domain}}'"),
    ("platform_databases", "ssl_mode", "TEXT DEFAULT 'prefer'"),
    ("platform_servers", "docker_version", "TEXT DEFAULT ''"),
    ("platform_servers", "last_seen_at", "TEXT"),
    ("platform_deployments", "requested_by", "TEXT DEFAULT ''"),
    ("platform_deployments", "plan", "TEXT DEFAULT '{}'"),
)

# JSON-encoded TEXT columns. Values are dumped on write and parsed on read so
# callers deal in Python objects and never in strings-that-are-really-JSON.
JSON_COLUMNS: dict[str, frozenset[str]] = {
    "platform_applications": frozenset({"docker_compose_domains"}),
    "platform_deployments": frozenset({"plan"}),
    "platform_notification_channels": frozenset({"config", "events"}),
    "platform_previews": frozenset({"docker_compose_domains"}),
    "platform_webhook_events": frozenset({"matched_resources"}),
}

# Columns stored as INTEGER 0/1 that callers want as ``bool``.
BOOL_COLUMNS: dict[str, frozenset[str]] = {
    "platform_applications": frozenset({
        "auto_deploy", "preview_deployments_enabled", "health_check_enabled",
        "is_http_basic_auth_enabled", "is_force_https_enabled", "is_gzip_enabled",
        "is_strip_prefix_enabled", "connect_to_docker_network",
        "rolling_update_enabled",
    }),
    "platform_databases": frozenset({"is_public", "enable_ssl"}),
    "platform_services": frozenset({"connect_to_docker_network"}),
    "platform_service_containers": frozenset({"exclude_from_status", "is_public"}),
    "platform_env_vars": frozenset({
        "is_build_time", "is_runtime", "is_literal", "is_multiline",
        "is_secret", "is_shown_once", "is_preview",
    }),
    "platform_shared_env_vars": frozenset({"is_secret"}),
    "platform_servers": frozenset({
        "is_local", "is_reachable", "is_usable", "is_build_server",
        "is_swarm_manager", "is_swarm_worker", "is_terminal_enabled",
        "is_metrics_enabled", "is_sentinel_enabled", "delete_unused_volumes",
        "delete_unused_networks", "unreachable_notification_sent",
        "high_disk_usage_notification_sent",
    }),
    "platform_deployments": frozenset({
        "force_rebuild", "restart_only", "rollback", "is_webhook", "is_api",
    }),
    "platform_scheduled_tasks": frozenset({"enabled"}),
    "platform_backups": frozenset({"enabled", "save_locally", "dump_all"}),
    "platform_volumes": frozenset({"is_directory"}),
    "platform_destinations": frozenset({"is_default"}),
    "platform_teams": frozenset({"personal_team"}),
    "platform_private_keys": frozenset({"is_git_related"}),
    "platform_s3_storages": frozenset({"use_path_style", "is_usable"}),
    "platform_notification_channels": frozenset({"enabled"}),
    "platform_git_sources": frozenset({"is_system_wide"}),
    "platform_ssl_certificates": frozenset({"is_ca"}),
    "platform_webhook_events": frozenset({"accepted"}),
}

# Never returned by the generic read path — callers that genuinely need a
# secret (the deploy pipeline, the SSH transport) go through an explicit
# ``*_with_secrets`` accessor. This is what keeps credentials out of the API
# responses and the dashboard by default.
SECRET_COLUMNS: dict[str, frozenset[str]] = {
    "platform_private_keys": frozenset({"private_key"}),
    "platform_databases": frozenset({"password", "root_password"}),
    "platform_applications": frozenset({"docker_registry_password", "http_basic_auth_password"}),
    "platform_s3_storages": frozenset({"secret_key"}),
    "platform_git_sources": frozenset({"client_secret", "access_token", "webhook_secret"}),
    "platform_ssl_certificates": frozenset({"private_key"}),
}

# Tables keyed by ``uuid``. ``platform_server_metrics`` is the one exception
# (autoincrement id) and is handled by its own accessors.
_UUID_TABLES = frozenset(
    line.split()[5]
    for line in SCHEMA.splitlines()
    if line.startswith("CREATE TABLE IF NOT EXISTS platform_")
) - {"platform_server_metrics"}

_column_cache: dict[str, frozenset[str]] = {}


# --------------------------------------------------------------------------- #
# Connection helpers
# --------------------------------------------------------------------------- #


def _db_path() -> str:
    """Resolved at call time, never cached — tests monkeypatch ``settings``."""
    return str(settings.resolved_db_path)


async def init_platform_db() -> None:
    """Create/upgrade the platform tables. Idempotent; safe on every boot."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(_db_path()) as db:
        from syte.sqlite_utils import configure_sqlite

        await configure_sqlite(db, db_path=_db_path())
        await db.executescript(SCHEMA)
        await _migrate(db)
        await db.commit()
    _column_cache.clear()


async def _migrate(db: aiosqlite.Connection) -> None:
    """Apply additive column migrations for pre-existing installs."""
    seen: dict[str, set[str]] = {}
    for table, column, ddl in MIGRATIONS:
        if table not in seen:
            async with db.execute(f"PRAGMA table_info({table})") as cur:
                seen[table] = {row[1] for row in await cur.fetchall()}
        if not seen[table]:
            # Table does not exist yet (SCHEMA above creates it) — nothing to do.
            continue
        if column in seen[table]:
            continue
        try:
            await db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
            seen[table].add(column)
        except Exception:
            # A duplicate-column race between two workers is benign; anything
            # else is worth a log line but must not stop startup.
            logger.exception("Migration failed for %s.%s", table, column)


async def _columns(db: aiosqlite.Connection, table: str) -> frozenset[str]:
    """Live column set for ``table``, cached per process."""
    cached = _column_cache.get(table)
    if cached is not None:
        return cached
    async with db.execute(f"PRAGMA table_info({table})") as cur:
        cols = frozenset(row[1] for row in await cur.fetchall())
    if cols:
        _column_cache[table] = cols
    return cols


def _assert_known_table(table: str) -> None:
    """Guard the f-string SQL below against injection via a caller-supplied name."""
    if table not in _UUID_TABLES:
        raise ValueError(f"Unknown platform table: {table}")


# --------------------------------------------------------------------------- #
# Row encoding / decoding
# --------------------------------------------------------------------------- #


def _encode(table: str, fields: dict[str, Any]) -> dict[str, Any]:
    """Convert Python values into what SQLite should store."""
    json_cols = JSON_COLUMNS.get(table, frozenset())
    out: dict[str, Any] = {}
    for key, value in fields.items():
        if key in json_cols and not isinstance(value, str):
            out[key] = json.dumps(value if value is not None else {})
        elif isinstance(value, bool):
            out[key] = 1 if value else 0
        elif isinstance(value, (list, dict)):
            out[key] = json.dumps(value)
        else:
            out[key] = value
    return out


def decode_row(table: str, row: dict[str, Any], *, include_secrets: bool = False) -> dict[str, Any]:
    """Convert a raw row into the shape callers expect.

    Parses JSON columns, coerces 0/1 into ``bool``, and strips secret columns
    unless explicitly requested.
    """
    json_cols = JSON_COLUMNS.get(table, frozenset())
    bool_cols = BOOL_COLUMNS.get(table, frozenset())
    secret_cols = frozenset() if include_secrets else SECRET_COLUMNS.get(table, frozenset())

    out: dict[str, Any] = {}
    for key, value in row.items():
        if key in secret_cols:
            # Presence is still useful information (is a password set?) without
            # leaking the value itself.
            out[f"{key}_set"] = bool(value)
            continue
        if key in json_cols:
            if isinstance(value, str) and value.strip():
                try:
                    out[key] = json.loads(value)
                except json.JSONDecodeError:
                    out[key] = {}
            else:
                out[key] = {} if value in (None, "") else value
            continue
        if key in bool_cols:
            out[key] = bool(value)
            continue
        out[key] = value
    return out


# --------------------------------------------------------------------------- #
# Generic CRUD
# --------------------------------------------------------------------------- #


async def insert(table: str, data: dict[str, Any]) -> dict[str, Any]:
    """Insert a row, generating ``uuid``/``created_at``/``updated_at`` if absent."""
    _assert_known_table(table)
    now = utcnow()
    payload = dict(data)
    payload.setdefault("uuid", new_uuid())
    async with aiosqlite.connect(_db_path()) as db:
        cols = await _columns(db, table)
        if "created_at" in cols:
            payload.setdefault("created_at", now)
        if "updated_at" in cols:
            payload.setdefault("updated_at", now)
        fields = _encode(table, {k: v for k, v in payload.items() if k in cols})
        if not fields:
            raise ValueError(f"No writable columns supplied for {table}")
        placeholders = ", ".join("?" for _ in fields)
        column_list = ", ".join(f'"{k}"' for k in fields)
        await db.execute(
            f"INSERT INTO {table} ({column_list}) VALUES ({placeholders})",
            list(fields.values()),
        )
        await db.commit()
    return await get(table, str(payload["uuid"]), include_secrets=True) or dict(payload)


async def get(
    table: str,
    uuid: str,
    *,
    include_secrets: bool = False,
) -> dict[str, Any] | None:
    _assert_known_table(table)
    if not uuid:
        return None
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(f"SELECT * FROM {table} WHERE uuid = ?", (uuid,)) as cur:
            row = await cur.fetchone()
            if not row:
                return None
            return decode_row(table, dict(row), include_secrets=include_secrets)


async def find_one(
    table: str,
    where: dict[str, Any],
    *,
    include_secrets: bool = False,
    order_by: str = "",
) -> dict[str, Any] | None:
    rows = await find(table, where, include_secrets=include_secrets, order_by=order_by, limit=1)
    return rows[0] if rows else None


async def find(
    table: str,
    where: dict[str, Any] | None = None,
    *,
    include_secrets: bool = False,
    order_by: str = "",
    limit: int | None = None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    """Equality-only filtered select. ``None`` values become ``IS NULL``."""
    _assert_known_table(table)
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cols = await _columns(db, table)
        clauses: list[str] = []
        values: list[Any] = []
        for key, value in (where or {}).items():
            if key not in cols:
                continue
            if value is None:
                clauses.append(f'"{key}" IS NULL')
            elif isinstance(value, (list, tuple, set, frozenset)):
                items = list(value)
                if not items:
                    # An empty IN () is a contradiction — return nothing rather
                    # than emitting invalid SQL.
                    return []
                clauses.append(f'"{key}" IN ({", ".join("?" for _ in items)})')
                values.extend(1 if isinstance(i, bool) and i else 0 if isinstance(i, bool) else i for i in items)
            else:
                clauses.append(f'"{key}" = ?')
                values.append(1 if value is True else 0 if value is False else value)

        sql = f"SELECT * FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += f" ORDER BY {_safe_order(order_by, cols)}"
        if limit is not None:
            sql += " LIMIT ? OFFSET ?"
            values.extend([int(limit), int(offset)])
        async with db.execute(sql, values) as cur:
            rows = await cur.fetchall()
    return [decode_row(table, dict(r), include_secrets=include_secrets) for r in rows]


def _safe_order(order_by: str, cols: frozenset[str]) -> str:
    """Validate an ORDER BY clause against the real column set.

    ``order_by`` is interpolated into SQL, so it must never come straight from a
    request. Accepts ``"name"``, ``"created_at DESC"`` or a comma-separated
    combination and silently falls back to a safe default.
    """
    default = "created_at DESC" if "created_at" in cols else "uuid"
    if not order_by:
        return default
    parts: list[str] = []
    for chunk in order_by.split(","):
        bits = chunk.strip().split()
        if not bits or bits[0] not in cols:
            return default
        direction = bits[1].upper() if len(bits) > 1 else "ASC"
        if direction not in ("ASC", "DESC"):
            return default
        parts.append(f'"{bits[0]}" {direction}')
    return ", ".join(parts) if parts else default


async def update(table: str, uuid: str, updates: dict[str, Any]) -> dict[str, Any] | None:
    """Patch a row. Unknown keys are ignored; ``updated_at`` is stamped."""
    _assert_known_table(table)
    existing = await get(table, uuid, include_secrets=True)
    if existing is None:
        return None
    async with aiosqlite.connect(_db_path()) as db:
        cols = await _columns(db, table)
        fields = _encode(
            table,
            {k: v for k, v in updates.items() if k in cols and k not in ("uuid", "created_at")},
        )
        if not fields:
            return existing
        if "updated_at" in cols:
            fields["updated_at"] = utcnow()
        set_clause = ", ".join(f'"{k}" = ?' for k in fields)
        await db.execute(
            f"UPDATE {table} SET {set_clause} WHERE uuid = ?",
            list(fields.values()) + [uuid],
        )
        await db.commit()
    return await get(table, uuid, include_secrets=True)


async def delete(table: str, uuid: str) -> bool:
    _assert_known_table(table)
    async with aiosqlite.connect(_db_path()) as db:
        cur = await db.execute(f"DELETE FROM {table} WHERE uuid = ?", (uuid,))
        await db.commit()
        return cur.rowcount > 0


async def delete_where(table: str, where: dict[str, Any]) -> int:
    """Bulk delete used by cascading removal. Refuses an unfiltered wipe."""
    _assert_known_table(table)
    if not where:
        raise ValueError("delete_where requires at least one condition")
    async with aiosqlite.connect(_db_path()) as db:
        cols = await _columns(db, table)
        clauses: list[str] = []
        values: list[Any] = []
        for key, value in where.items():
            if key not in cols:
                continue
            clauses.append(f'"{key}" = ?')
            values.append(1 if value is True else 0 if value is False else value)
        if not clauses:
            raise ValueError("delete_where requires at least one known column")
        cur = await db.execute(
            f"DELETE FROM {table} WHERE {' AND '.join(clauses)}", values
        )
        await db.commit()
        return cur.rowcount


async def count(table: str, where: dict[str, Any] | None = None) -> int:
    _assert_known_table(table)
    async with aiosqlite.connect(_db_path()) as db:
        cols = await _columns(db, table)
        clauses: list[str] = []
        values: list[Any] = []
        for key, value in (where or {}).items():
            if key not in cols:
                continue
            clauses.append(f'"{key}" = ?')
            values.append(1 if value is True else 0 if value is False else value)
        sql = f"SELECT COUNT(*) FROM {table}"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        async with db.execute(sql, values) as cur:
            row = await cur.fetchone()
            return int(row[0]) if row else 0


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #

DEFAULT_TEAM_NAME = "Default"
DEFAULT_PROJECT_NAME = "default"
DEFAULT_ENVIRONMENT_NAME = "production"
LOCALHOST_SERVER_NAME = "localhost"
DEFAULT_NETWORK = "syte"


async def ensure_bootstrap() -> dict[str, Any]:
    """Guarantee a usable default team / server / destination / project exist.

    Coolify does this during installation; Syte does it lazily on first boot so
    an operator can create a resource immediately without a setup wizard. Every
    step is get-or-create, so calling this repeatedly is free.
    """
    team = await find_one("platform_teams", {}, order_by="created_at ASC")
    if team is None:
        team = await insert(
            "platform_teams",
            {"name": DEFAULT_TEAM_NAME, "description": "Auto-created default team", "personal_team": True},
        )

    server = await find_one("platform_servers", {"is_local": True})
    if server is None:
        server = await find_one("platform_servers", {"name": LOCALHOST_SERVER_NAME})
    if server is None:
        server = await insert(
            "platform_servers",
            {
                "team_uuid": team["uuid"],
                "name": LOCALHOST_SERVER_NAME,
                "description": "The server Syte itself runs on",
                "ip": "127.0.0.1",
                "user": "root",
                "port": 22,
                "is_local": True,
                "is_reachable": True,
                "is_usable": True,
                "status": "reachable",
                "proxy_type": "CADDY",
            },
        )

    destination = await find_one("platform_destinations", {"server_uuid": server["uuid"], "is_default": True})
    if destination is None:
        destination = await insert(
            "platform_destinations",
            {
                "server_uuid": server["uuid"],
                "name": f"{DEFAULT_NETWORK} (default)",
                "network": DEFAULT_NETWORK,
                "kind": "standalone",
                "is_default": True,
            },
        )

    project = await find_one("platform_projects", {"team_uuid": team["uuid"]}, order_by="created_at ASC")
    if project is None:
        project = await insert(
            "platform_projects",
            {"team_uuid": team["uuid"], "name": DEFAULT_PROJECT_NAME, "description": "Auto-created default project"},
        )

    environment = await find_one("platform_environments", {"project_uuid": project["uuid"]}, order_by="created_at ASC")
    if environment is None:
        environment = await insert(
            "platform_environments",
            {"project_uuid": project["uuid"], "name": DEFAULT_ENVIRONMENT_NAME},
        )

    return {
        "team": team,
        "server": server,
        "destination": destination,
        "project": project,
        "environment": environment,
    }


# --------------------------------------------------------------------------- #
# Resource lookup across the three resource tables
# --------------------------------------------------------------------------- #


async def get_resource(uuid: str, *, include_secrets: bool = False) -> tuple[ResourceType, dict[str, Any]] | None:
    """Find a resource by uuid without knowing its type.

    The API exposes ``/api/v1/deployments/{uuid}`` and webhook targets by bare
    uuid, so the type has to be discoverable. Three indexed primary-key lookups
    is cheaper than maintaining a separate resource registry table that could
    drift out of sync.
    """
    for resource_type in (ResourceType.APPLICATION, ResourceType.DATABASE, ResourceType.SERVICE):
        row = await get(resource_type.table, uuid, include_secrets=include_secrets)
        if row is not None:
            return resource_type, row
    return None


async def list_environment_resources(environment_uuid: str) -> dict[str, list[dict[str, Any]]]:
    """All resources in one environment, grouped by type."""
    return {
        "applications": await find("platform_applications", {"environment_uuid": environment_uuid}),
        "databases": await find("platform_databases", {"environment_uuid": environment_uuid}),
        "services": await find("platform_services", {"environment_uuid": environment_uuid}),
    }


async def list_server_resources(server_uuid: str) -> dict[str, list[dict[str, Any]]]:
    """All resources scheduled onto one server (drives the server detail page)."""
    return {
        "applications": await find("platform_applications", {"server_uuid": server_uuid}),
        "databases": await find("platform_databases", {"server_uuid": server_uuid}),
        "services": await find("platform_services", {"server_uuid": server_uuid}),
    }


async def list_all_resources() -> list[dict[str, Any]]:
    """Flat resource inventory used by ``GET /api/v1/resources`` and the dashboard."""
    out: list[dict[str, Any]] = []
    for resource_type in (ResourceType.APPLICATION, ResourceType.DATABASE, ResourceType.SERVICE):
        for row in await find(resource_type.table):
            out.append(
                {
                    "uuid": row["uuid"],
                    "name": row.get("name"),
                    "type": resource_type.value,
                    "status": row.get("status"),
                    "environment_uuid": row.get("environment_uuid"),
                    "server_uuid": row.get("server_uuid"),
                    "fqdn": row.get("fqdn", ""),
                    "database_type": row.get("database_type"),
                    "build_pack": row.get("build_pack"),
                    "template_key": row.get("template_key"),
                    "created_at": row.get("created_at"),
                    "updated_at": row.get("updated_at"),
                }
            )
    out.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return out


async def delete_resource_cascade(resource_type: ResourceType, uuid: str) -> bool:
    """Remove a resource and every row that hangs off it.

    SQLite foreign keys are not enabled on this connection (the existing
    ``syte.database`` schema does not use them either), so cascading is explicit.
    Doing it here rather than in the API handler means the CLI, the dashboard and
    the API cannot each forget a different child table.
    """
    row = await get(resource_type.table, uuid)
    if row is None:
        return False

    # Task executions are keyed by task_uuid, not resource_uuid, so they must be
    # collected *before* the tasks are deleted — otherwise the join column is
    # gone and the executions survive as unreachable orphans.
    for task in await find("platform_scheduled_tasks", {"resource_uuid": uuid}):
        await delete_where("platform_task_executions", {"task_uuid": task["uuid"]})

    for table in (
        "platform_env_vars",
        "platform_volumes",
        "platform_scheduled_tasks",
        "platform_resource_tags",
    ):
        try:
            await delete_where(table, {"resource_uuid": uuid})
        except Exception:
            logger.exception("Failed cascading delete of %s for %s", table, uuid)

    await delete_where("platform_deployments", {"resource_uuid": uuid})

    if resource_type is ResourceType.APPLICATION:
        await delete_where("platform_previews", {"application_uuid": uuid})
    if resource_type is ResourceType.DATABASE:
        for backup in await find("platform_backups", {"database_uuid": uuid}):
            await delete_where("platform_backup_executions", {"backup_uuid": backup["uuid"]})
        await delete_where("platform_backups", {"database_uuid": uuid})
    if resource_type is ResourceType.SERVICE:
        await delete_where("platform_service_containers", {"service_uuid": uuid})

    return await delete(resource_type.table, uuid)


async def delete_environment_cascade(environment_uuid: str) -> tuple[bool, str]:
    """Delete an environment only when empty — matches Coolify's API contract."""
    resources = await list_environment_resources(environment_uuid)
    total = sum(len(items) for items in resources.values())
    if total:
        return False, (
            f"Environment still holds {total} resource(s). "
            "Delete or move them first."
        )
    await delete_where("platform_shared_env_vars", {"scope": "environment", "scope_uuid": environment_uuid})
    return await delete("platform_environments", environment_uuid), "Environment deleted."


async def delete_project_cascade(project_uuid: str) -> tuple[bool, str]:
    """Delete a project and all of its (empty) environments."""
    environments = await find("platform_environments", {"project_uuid": project_uuid})
    for environment in environments:
        resources = await list_environment_resources(environment["uuid"])
        total = sum(len(items) for items in resources.values())
        if total:
            return False, (
                f"Environment '{environment['name']}' still holds {total} resource(s). "
                "Delete them before deleting the project."
            )
    for environment in environments:
        await delete_environment_cascade(environment["uuid"])
    await delete_where("platform_shared_env_vars", {"scope": "project", "scope_uuid": project_uuid})
    return await delete("platform_projects", project_uuid), "Project deleted."


# --------------------------------------------------------------------------- #
# Deployment-specific accessors
# --------------------------------------------------------------------------- #


async def active_deployments(server_uuid: str | None = None) -> list[dict[str, Any]]:
    """Deployments that are queued or running, oldest first.

    Ordering matters: the queue worker picks the oldest queued deployment, so
    ``created_at ASC`` is what makes the queue FIFO.
    """
    where: dict[str, Any] = {
        "status": [DeploymentStatus.QUEUED.value, DeploymentStatus.IN_PROGRESS.value]
    }
    if server_uuid:
        where["server_uuid"] = server_uuid
    return await find("platform_deployments", where, order_by="created_at ASC")


async def running_deployment_count(server_uuid: str) -> int:
    """How many deployments are mid-flight on a server (concurrency gate)."""
    rows = await find(
        "platform_deployments",
        {"server_uuid": server_uuid, "status": DeploymentStatus.IN_PROGRESS.value},
    )
    return len(rows)


async def last_successful_deployment(resource_uuid: str) -> dict[str, Any] | None:
    """Most recent finished non-preview deployment — the rollback target."""
    rows = await find(
        "platform_deployments",
        {
            "resource_uuid": resource_uuid,
            "status": DeploymentStatus.FINISHED.value,
            "pull_request_id": 0,
        },
        order_by="created_at DESC",
        limit=1,
    )
    return rows[0] if rows else None


async def deployment_history(
    resource_uuid: str,
    *,
    limit: int = 25,
    offset: int = 0,
    pull_request_id: int | None = None,
) -> list[dict[str, Any]]:
    where: dict[str, Any] = {"resource_uuid": resource_uuid}
    if pull_request_id is not None:
        where["pull_request_id"] = pull_request_id
    return await find(
        "platform_deployments",
        where,
        order_by="created_at DESC",
        limit=limit,
        offset=offset,
    )


async def append_deployment_logs(uuid: str, text: str) -> None:
    """Append to a deployment's log column.

    Done as a single SQL concat rather than read-modify-write so two concurrent
    writers (the build streamer and the orchestrator) cannot lose each other's
    lines.
    """
    if not text:
        return
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            "UPDATE platform_deployments "
            "SET logs = COALESCE(logs, '') || ?, updated_at = ? WHERE uuid = ?",
            (text if text.endswith("\n") else text + "\n", utcnow(), uuid),
        )
        await db.commit()


async def rollback_candidates(resource_uuid: str, *, limit: int = 10) -> list[dict[str, Any]]:
    """Prior successful deployments that still have a recorded image tag.

    A deployment without an ``image_tag`` cannot be rolled back to, because
    there is no image to re-run — filtering here keeps the dashboard from
    offering a button that would fail.
    """
    rows = await find(
        "platform_deployments",
        {
            "resource_uuid": resource_uuid,
            "status": DeploymentStatus.FINISHED.value,
            "pull_request_id": 0,
        },
        order_by="created_at DESC",
        limit=limit * 2,
    )
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows:
        tag = str(row.get("image_tag") or "")
        if not tag or tag in seen:
            continue
        seen.add(tag)
        out.append(row)
        if len(out) >= limit:
            break
    return out


# --------------------------------------------------------------------------- #
# Metrics
# --------------------------------------------------------------------------- #

# Keep roughly a week of 1-minute samples per server. Enough for the dashboard
# graphs without letting the table dominate the SQLite file.
METRICS_RETENTION_ROWS = 10_000


async def record_server_metrics(server_uuid: str, sample: dict[str, Any]) -> None:
    """Append one metrics sample and trim the rolling window."""
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute(
            """INSERT INTO platform_server_metrics
               (server_uuid, cpu_percent, memory_percent, memory_used_mb, memory_total_mb,
                disk_percent, disk_used_gb, disk_total_gb, container_count, recorded_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                server_uuid,
                float(sample.get("cpu_percent") or 0),
                float(sample.get("memory_percent") or 0),
                float(sample.get("memory_used_mb") or 0),
                float(sample.get("memory_total_mb") or 0),
                float(sample.get("disk_percent") or 0),
                float(sample.get("disk_used_gb") or 0),
                float(sample.get("disk_total_gb") or 0),
                int(sample.get("container_count") or 0),
                utcnow(),
            ),
        )
        await db.execute(
            """DELETE FROM platform_server_metrics
               WHERE server_uuid = ? AND id NOT IN (
                   SELECT id FROM platform_server_metrics
                   WHERE server_uuid = ? ORDER BY id DESC LIMIT ?
               )""",
            (server_uuid, server_uuid, METRICS_RETENTION_ROWS),
        )
        await db.commit()


async def server_metrics(server_uuid: str, *, limit: int = 120) -> list[dict[str, Any]]:
    """Most recent samples, oldest first so a chart can plot them directly."""
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM platform_server_metrics WHERE server_uuid = ? "
            "ORDER BY id DESC LIMIT ?",
            (server_uuid, int(limit)),
        ) as cur:
            rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    return rows


# --------------------------------------------------------------------------- #
# Convenience wrappers with meaningful names
# --------------------------------------------------------------------------- #


async def get_application(uuid: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
    return await get("platform_applications", uuid, include_secrets=include_secrets)


async def get_database(uuid: str, *, include_secrets: bool = False) -> dict[str, Any] | None:
    return await get("platform_databases", uuid, include_secrets=include_secrets)


async def get_service(uuid: str) -> dict[str, Any] | None:
    return await get("platform_services", uuid)


async def get_server(uuid: str) -> dict[str, Any] | None:
    return await get("platform_servers", uuid)


async def list_servers() -> list[dict[str, Any]]:
    return await find("platform_servers", order_by="created_at ASC")


async def list_projects_with_environments() -> list[dict[str, Any]]:
    """Projects, each with its environments and per-environment resource counts.

    One shaped payload for the dashboard's project grid — avoids the N+1 round
    trips a browser would otherwise make to render the landing page.
    """
    projects = await find("platform_projects", order_by="created_at ASC")
    out: list[dict[str, Any]] = []
    for project in projects:
        environments = await find(
            "platform_environments", {"project_uuid": project["uuid"]}, order_by="created_at ASC"
        )
        shaped_envs: list[dict[str, Any]] = []
        for environment in environments:
            resources = await list_environment_resources(environment["uuid"])
            shaped_envs.append(
                {
                    **environment,
                    "counts": {key: len(value) for key, value in resources.items()},
                    "resources": [
                        {
                            "uuid": item["uuid"],
                            "name": item.get("name"),
                            "type": key.rstrip("s"),
                            "status": item.get("status"),
                            "fqdn": item.get("fqdn", ""),
                            "database_type": item.get("database_type"),
                            "build_pack": item.get("build_pack"),
                        }
                        for key, items in resources.items()
                        for item in items
                    ],
                }
            )
        out.append({**project, "environments": shaped_envs})
    return out


async def env_vars_for(resource_uuid: str, *, is_preview: bool = False) -> list[dict[str, Any]]:
    return await find(
        "platform_env_vars",
        {"resource_uuid": resource_uuid, "is_preview": is_preview},
        order_by="key ASC",
    )


async def upsert_env_var(
    resource_uuid: str,
    resource_type: ResourceType,
    key: str,
    value: str,
    **flags: Any,
) -> dict[str, Any]:
    """Create or replace a resource env var, keyed on (resource, key, preview)."""
    is_preview = bool(flags.get("is_preview", False))
    existing = await find_one(
        "platform_env_vars",
        {"resource_uuid": resource_uuid, "key": key, "is_preview": is_preview},
    )
    payload = {
        "resource_uuid": resource_uuid,
        "resource_type": resource_type.value,
        "key": key,
        "value": value,
        **{k: v for k, v in flags.items()},
    }
    if existing:
        return await update("platform_env_vars", existing["uuid"], payload) or existing
    return await insert("platform_env_vars", payload)


async def bulk_upsert_env_vars(
    resource_uuid: str,
    resource_type: ResourceType,
    variables: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in variables:
        key = str(item.get("key") or "").strip()
        if not key:
            continue
        flags = {k: v for k, v in item.items() if k not in ("key", "value")}
        out.append(
            await upsert_env_var(
                resource_uuid,
                resource_type,
                key,
                str(item.get("value") or ""),
                **flags,
            )
        )
    return out


async def volumes_for(resource_uuid: str) -> list[dict[str, Any]]:
    return await find("platform_volumes", {"resource_uuid": resource_uuid}, order_by="mount_path ASC")


async def scheduled_tasks_for(resource_uuid: str) -> list[dict[str, Any]]:
    return await find("platform_scheduled_tasks", {"resource_uuid": resource_uuid}, order_by="name ASC")


async def enabled_scheduled_tasks() -> list[dict[str, Any]]:
    return await find("platform_scheduled_tasks", {"enabled": True}, order_by="created_at ASC")


async def enabled_backups() -> list[dict[str, Any]]:
    return await find("platform_backups", {"enabled": True}, order_by="created_at ASC")


async def notification_channels(team_uuid: str | None = None) -> list[dict[str, Any]]:
    where: dict[str, Any] = {"enabled": True}
    if team_uuid:
        where["team_uuid"] = team_uuid
    return await find("platform_notification_channels", where, order_by="created_at ASC")


async def record_webhook_event(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist an inbound webhook for the dashboard's delivery log."""
    return await insert("platform_webhook_events", payload)


async def recent_webhook_events(limit: int = 50) -> list[dict[str, Any]]:
    return await find("platform_webhook_events", order_by="created_at DESC", limit=limit)


async def applications_watching(
    repository: str,
    branch: str,
    *,
    provider: str | None = None,
) -> list[dict[str, Any]]:
    """Applications an inbound push on ``repository``/``branch`` should deploy.

    Matching is done in Python rather than SQL because ``git_repository`` is
    stored in whatever form the operator pasted (with or without ``.git``, with
    or without a scheme, SSH or HTTPS), and normalising both sides is far more
    robust than trying to make one LIKE pattern cover every case.
    """
    from syte.platform.git_sources import repo_matches

    candidates = await find("platform_applications", {"git_branch": branch})
    matches: list[dict[str, Any]] = []
    for app in candidates:
        if not app.get("auto_deploy"):
            continue
        if provider and app.get("git_provider") and app["git_provider"] != provider:
            continue
        stored = str(app.get("git_repository") or app.get("git_full_url") or "")
        if repo_matches(stored, repository):
            matches.append(app)
    return matches


__all__ = [
    "BOOL_COLUMNS",
    "DEFAULT_ENVIRONMENT_NAME",
    "DEFAULT_NETWORK",
    "DEFAULT_PROJECT_NAME",
    "DEFAULT_TEAM_NAME",
    "JSON_COLUMNS",
    "LOCALHOST_SERVER_NAME",
    "METRICS_RETENTION_ROWS",
    "MIGRATIONS",
    "SCHEMA",
    "SECRET_COLUMNS",
    "active_deployments",
    "append_deployment_logs",
    "applications_watching",
    "bulk_upsert_env_vars",
    "count",
    "decode_row",
    "delete",
    "delete_environment_cascade",
    "delete_project_cascade",
    "delete_resource_cascade",
    "delete_where",
    "deployment_history",
    "enabled_backups",
    "enabled_scheduled_tasks",
    "ensure_bootstrap",
    "env_vars_for",
    "find",
    "find_one",
    "get",
    "get_application",
    "get_database",
    "get_resource",
    "get_server",
    "get_service",
    "init_platform_db",
    "insert",
    "last_successful_deployment",
    "list_all_resources",
    "list_environment_resources",
    "list_projects_with_environments",
    "list_server_resources",
    "list_servers",
    "notification_channels",
    "recent_webhook_events",
    "record_server_metrics",
    "record_webhook_event",
    "rollback_candidates",
    "running_deployment_count",
    "scheduled_tasks_for",
    "server_metrics",
    "update",
    "upsert_env_var",
    "volumes_for",
]
