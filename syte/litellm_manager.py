"""LiteLLM proxy management for Syra.

LiteLLM runs as a Docker container providing a unified API gateway to multiple
LLM providers. This module manages the container lifecycle: start, stop, status,
and configuration.

The proxy is private to the host and exposes its public OpenAI-compatible
API through Caddy at https://api.sycord.site/v1. The Syte web GUI is served at
https://api.sycord.site/ and LiteLLM administration remains loopback-only;
client traffic must use scoped virtual keys rather than the LiteLLM master key.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from syte.database import get_setting, set_setting
from syte.litellm_config import (
    LITELLM_CONTAINER_PORT,
    LITELLM_HOST_PORT,
    LITELLM_INTERNAL_ORIGIN,
    LITELLM_PUBLIC_API_URL,
)

logger = logging.getLogger(__name__)

LITELLM_CONTAINER_NAME = "syte-litellm"
# Pin the runtime image so the Prisma client and migration catalog remain
# compatible with the persistent LiteLLM PostgreSQL database.
LITELLM_IMAGE = "docker.litellm.ai/berriai/litellm:1.92.1"
LITELLM_PRISMA_BIN = "/opt/prisma/binaries/node_modules/.bin/prisma"
LITELLM_SCHEMA_PATH = "/app/litellm-proxy-extras/schema.prisma"
LITELLM_DEFAULT_PORT = LITELLM_HOST_PORT
LITELLM_DATA_DIR = "/var/lib/syte/litellm"
LITELLM_DB_CONTAINER_NAME = "syte-litellm-db"
LITELLM_DB_IMAGE = "postgres:16-alpine"
LITELLM_DB_DATA_DIR = "/var/lib/syte/litellm-postgres"
LITELLM_DB_NAME = "litellm"
LITELLM_MCP_SERVER_TABLE = "LiteLLM_MCPServerTable"
LITELLM_DB_USER = "litellm"
LITELLM_NETWORK = "syte-litellm"


def validate_litellm_database_url(value: str) -> str:
    """Validate a custom LiteLLM database URL without exposing credentials."""
    value = value.strip()
    if not value:
        return ""
    parsed = urlsplit(value)
    if parsed.scheme != "postgresql" or not parsed.netloc:
        raise ValueError(
            "LiteLLM database URL must be a PostgreSQL URL such as "
            "postgresql://user:password@host:5432/database. "
            "Turso libsql:// URLs belong in the separate Turso database field."
        )
    if (
        parsed.hostname
        and parsed.hostname.endswith(".pooler.supabase.com")
        and parsed.port == 6543
    ):
        raise ValueError(
            "Supabase transaction pooler port 6543 cannot run LiteLLM Prisma migrations. "
            "Use the direct database URL or Supabase session pooler port 5432."
        )
    return value


def _quote_postgres_identifier(value: str) -> str:
    """Quote one PostgreSQL identifier for an internally generated statement."""
    return '"' + value.replace('"', '""') + '"'


def _quote_postgres_literal(value: str) -> str:
    """Quote one PostgreSQL string literal for an internally generated statement."""
    return f"'{value.replace(chr(39), chr(39) * 2)}'"


def _litellm_prisma_schema(database_url: str) -> tuple[str, str]:
    """Return the Prisma schema and a libpq-compatible database URL.

    Prisma accepts ``?schema=name`` while libpq (and therefore psql) does not.
    Strip that Prisma-only query parameter before executing the compatibility
    statement and use it to qualify the table name instead.
    """
    parsed = urlsplit(database_url)
    schema = "public"
    libpq_query: list[tuple[str, str]] = []
    for key, value in parse_qsl(parsed.query, keep_blank_values=True):
        if key == "schema":
            if value:
                schema = value
            continue
        libpq_query.append((key, value))
    return schema, urlunsplit(parsed._replace(query=urlencode(libpq_query)))


def _litellm_mcp_instructions_compat_sql(schema: str) -> str:
    """Build an atomic MCP column repair and physical-schema verification."""
    qualified_table = (
        f"{_quote_postgres_identifier(schema)}."
        f"{_quote_postgres_identifier(LITELLM_MCP_SERVER_TABLE)}"
    )
    table_literal = _quote_postgres_literal(qualified_table)
    schema_literal = _quote_postgres_literal(schema)
    return (
        "BEGIN; "
        f"ALTER TABLE IF EXISTS {qualified_table} "
        'ADD COLUMN IF NOT EXISTS "instructions" TEXT; '
        "DO $$ BEGIN "
        f"IF to_regclass({table_literal}) IS NULL THEN "
        f"RAISE EXCEPTION 'LiteLLM MCP table is missing from schema %', {schema_literal}; "
        "END IF; "
        "IF NOT EXISTS (SELECT 1 FROM pg_attribute "
        f"WHERE attrelid = to_regclass({table_literal}) "
        "AND attname = 'instructions' AND NOT attisdropped) THEN "
        f"RAISE EXCEPTION 'LiteLLM MCP instructions column is missing from schema %', {schema_literal}; "
        "END IF; "
        "END $$; COMMIT;"
    )


LITELLM_MCP_INSTRUCTIONS_COMPAT_SQL = _litellm_mcp_instructions_compat_sql("public")


async def _run_docker(args: list[str], timeout: float = 30.0) -> tuple[int, str]:
    """Run a docker command and return (exit_code, output)."""
    cmd = ["docker", *args]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        output = stdout.decode("utf-8", errors="replace").strip()
        return proc.returncode or 0, output
    except TimeoutError:
        return 1, f"Docker command timed out: {' '.join(args)}"
    except FileNotFoundError:
        return 1, "Docker is not installed or not in PATH"
    except (OSError, RuntimeError, ValueError) as e:
        return 1, f"Docker command failed: {e}"


async def litellm_status() -> dict[str, Any]:
    """Check if LiteLLM container is running and healthy.
    
    Returns status including:
    - running: bool
    - healthy: bool
    - port: int
    - public_api_url: str
    - container_id: str
    - uptime_seconds: int
    """
    exit_code, output = await _run_docker([
        "ps",
        "-a",
        "--filter", f"name={LITELLM_CONTAINER_NAME}",
        "--format", "json",
    ])
    
    result: dict[str, Any] = {
        "running": False,
        "healthy": False,
        "port": LITELLM_DEFAULT_PORT,
        "public_api_url": LITELLM_PUBLIC_API_URL,
        "container_id": "",
        "uptime_seconds": 0,
        "message": "",
    }
    
    if exit_code != 0:
        result["message"] = output or "Failed to check container status"
        return result
    
    if not output.strip():
        result["message"] = "LiteLLM container not found"
        return result
    
    try:
        # Docker normally returns one JSON object per line, but some versions
        # return a single top-level JSON array for --format json.
        try:
            parsed: Any = json.loads(output)
        except json.JSONDecodeError:
            # Fall back to Docker's JSON-lines format.
            parsed_records: list[Any] = []
            for line in output.splitlines():
                if not line.strip():
                    continue
                line_value = json.loads(line)
                if isinstance(line_value, list):
                    parsed_records.extend(line_value)
                else:
                    parsed_records.append(line_value)
            parsed = parsed_records

        if isinstance(parsed, dict):
            containers = [parsed]
        elif isinstance(parsed, list) and all(isinstance(item, dict) for item in parsed):
            containers = parsed
        else:
            raise ValueError("Docker output must contain container objects")

        if not containers:
            result["message"] = "LiteLLM container not found"
            return result
        
        container = containers[0]
        result["container_id"] = container.get("ID", "")[:12]
        state = container.get("State", "").lower()
        status = container.get("Status", "").lower()
        
        result["running"] = state == "running"
        result["healthy"] = "healthy" in status or result["running"]
        
        # Parse uptime from status like "Up 2 hours"
        if result["running"] and "up" in status:
            result["message"] = f"Container running: {status}"
        elif state == "exited":
            result["message"] = f"Container exited: {container.get('Status', 'unknown')}"
        else:
            result["message"] = f"Container state: {state}"
            
    except (json.JSONDecodeError, ValueError) as e:
        result["message"] = f"Failed to parse docker output: {e}"
    
    return result


async def litellm_is_loopback_bound() -> bool:
    """Return whether the existing container is bound only to the loopback port."""
    exit_code, output = await _run_docker([
        "inspect",
        "--format", "{{json .HostConfig.PortBindings}}",
        LITELLM_CONTAINER_NAME,
    ])
    if exit_code != 0 or not output:
        return False
    try:
        bindings = json.loads(output)
    except json.JSONDecodeError:
        return False
    if not isinstance(bindings, dict):
        return False
    published = bindings.get(f"{LITELLM_CONTAINER_PORT}/tcp") or []
    if not isinstance(published, list):
        return False
    return bool(published) and all(
        isinstance(binding, dict)
        and binding.get("HostIp") == "127.0.0.1"
        and binding.get("HostPort") == str(LITELLM_HOST_PORT)
        for binding in published
    )


async def _repair_litellm_mcp_schema(
    database_url: str,
    database_network: str = "",
) -> tuple[bool, str]:
    """Repair the MCP schema drift that older LiteLLM databases can contain.

    LiteLLM's Prisma migration history can say this migration was applied even
    when the physical column is missing. Run the repair against the same
    database and Prisma schema before migrations so both fresh and existing
    databases are safe.
    """
    schema, repair_database_url = _litellm_prisma_schema(database_url)
    args = ["run", "--rm"]
    if database_network:
        args.extend(["--network", database_network])
    args.extend([
        "--entrypoint", "sh",
        "-e", f"DATABASE_URL={repair_database_url}",
        LITELLM_DB_IMAGE,
        "-ec",
        'psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c '
        f"'{_litellm_mcp_instructions_compat_sql(schema)}'",
    ])
    exit_code, output = await _run_docker(args, timeout=60.0)
    if exit_code != 0:
        return False, (
            "LiteLLM PostgreSQL MCP schema repair failed. The proxy was not "
            f"started; repair the database and retry Start. {output or 'no repair output'}"
        )
    return True, "LiteLLM PostgreSQL MCP schema verified."


async def _run_litellm_migrations(
    database_url: str,
    database_network: str = "",
) -> tuple[bool, str]:
    """Apply migrations and leave the MCP table compatible with the runtime.

    The repair has to happen both before and after Prisma runs.  Older
    databases can record a migration as applied without its physical column,
    while a migration can also recreate the MCP table after the initial
    repair.  Repairing the final schema prevents the proxy from starting only
    to fail its background MCP reload with a missing ``instructions`` column.
    """
    schema_ok, schema_message = await _repair_litellm_mcp_schema(
        database_url, database_network
    )
    if not schema_ok:
        return False, schema_message

    args = ["run", "--rm"]
    if database_network:
        args.extend(["--network", database_network])
    args.extend([
        "--entrypoint", LITELLM_PRISMA_BIN,
        "-e", f"DATABASE_URL={database_url}",
        LITELLM_IMAGE,
        "migrate", "deploy",
        "--schema", LITELLM_SCHEMA_PATH,
    ])
    exit_code, output = await _run_docker(args, timeout=300.0)
    if exit_code != 0:
        return False, (
            "LiteLLM PostgreSQL migrations failed. The proxy was not started; "
            f"repair the database and retry Start. {output or 'no migration output'}"
        )

    final_schema_ok, final_schema_message = await _repair_litellm_mcp_schema(
        database_url, database_network
    )
    if not final_schema_ok:
        return False, (
            "LiteLLM PostgreSQL MCP schema verification failed after migrations. "
            "The proxy was not started; repair the database and retry Start. "
            f"{final_schema_message}"
        )
    return True, "LiteLLM PostgreSQL migrations applied and MCP schema verified."


async def litellm_is_postgres_configured(expected_database_url: str = "") -> bool:
    """Return whether the running LiteLLM container uses the expected PostgreSQL URL."""
    exit_code, env_output = await _run_docker([
        "inspect",
        "--format", "{{json .Config.Env}}",
        LITELLM_CONTAINER_NAME,
    ])
    if exit_code != 0 or not env_output:
        return False
    try:
        environment = json.loads(env_output)
    except json.JSONDecodeError:
        return False
    if not isinstance(environment, list) or not any(
        isinstance(item, str) and item.startswith("DATABASE_URL=postgresql://")
        for item in environment
    ):
        return False

    database_values = [
        item.removeprefix("DATABASE_URL=")
        for item in environment
        if isinstance(item, str) and item.startswith("DATABASE_URL=")
    ]
    if not database_values or not database_values[0].startswith("postgresql://"):
        return False
    return not expected_database_url or database_values[0] == expected_database_url


async def _ensure_litellm_database() -> tuple[bool, str, str, str]:
    """Use a configured PostgreSQL URL or start the managed PostgreSQL sidecar."""
    configured_url = (await get_setting("litellm_database_url", "")).strip()
    if configured_url:
        try:
            return True, validate_litellm_database_url(configured_url), "", ""
        except ValueError as error:
            return False, "", str(error), ""

    try:
        os.makedirs(LITELLM_DB_DATA_DIR, exist_ok=True)
    except OSError as error:
        return False, "", f"Could not create LiteLLM database directory {LITELLM_DB_DATA_DIR}: {error}", ""

    database_password = (await get_setting("litellm_db_password", "")).strip()
    if not database_password:
        import secrets

        database_password = secrets.token_urlsafe(32)
        await set_setting("litellm_db_password", database_password)

    network_code, _ = await _run_docker(["network", "inspect", LITELLM_NETWORK])
    if network_code != 0:
        network_code, network_output = await _run_docker([
            "network", "create", LITELLM_NETWORK,
        ])
        if network_code != 0:
            return False, "", f"Failed to create LiteLLM Docker network: {network_output}", ""

    inspect_code, _ = await _run_docker(["inspect", LITELLM_DB_CONTAINER_NAME])
    if inspect_code != 0:
        start_code, start_output = await _run_docker([
            "run", "-d",
            "--name", LITELLM_DB_CONTAINER_NAME,
            "--network", LITELLM_NETWORK,
            "-v", f"{LITELLM_DB_DATA_DIR}:/var/lib/postgresql/data",
            "-e", f"POSTGRES_DB={LITELLM_DB_NAME}",
            "-e", f"POSTGRES_USER={LITELLM_DB_USER}",
            "-e", f"POSTGRES_PASSWORD={database_password}",
            "--restart", "unless-stopped",
            LITELLM_DB_IMAGE,
        ], timeout=60.0)
        if start_code != 0:
            return False, "", f"Failed to start LiteLLM PostgreSQL database: {start_output}", ""
    else:
        start_code, start_output = await _run_docker(["start", LITELLM_DB_CONTAINER_NAME])
        if start_code != 0 and "already running" not in start_output.lower():
            return False, "", f"Failed to start LiteLLM PostgreSQL database: {start_output}", ""

    for _ in range(30):
        ready_code, _ = await _run_docker([
            "exec", LITELLM_DB_CONTAINER_NAME,
            "pg_isready", "-U", LITELLM_DB_USER, "-d", LITELLM_DB_NAME,
        ], timeout=10.0)
        if ready_code == 0:
            database_url = (
                f"postgresql://{LITELLM_DB_USER}:{quote(database_password, safe='')}"
                f"@{LITELLM_DB_CONTAINER_NAME}:5432/{LITELLM_DB_NAME}"
            )
            return True, database_url, "", LITELLM_NETWORK
        await asyncio.sleep(1.0)

    return False, "", "LiteLLM PostgreSQL database did not become ready within 30 seconds", ""


async def _wait_for_litellm_health() -> dict[str, Any]:
    """Wait for LiteLLM's readiness endpoint after migrations and container start."""
    last_health: dict[str, Any] = {
        "ok": False,
        "healthy": False,
        "message": "LiteLLM health check has not run yet",
    }
    for _ in range(60):
        last_health = await litellm_health()
        if last_health.get("healthy"):
            return last_health
        await asyncio.sleep(2.0)
    return last_health


async def start_litellm(
    *,
    port: int | None = None,
    master_key: str | None = None,
    salt_key: str | None = None,
) -> dict[str, Any]:
    """Start the LiteLLM Docker container.
    
    Parameters:
    - port: Reserved loopback port for Caddy (default 4000)
    - master_key: Private LiteLLM administration key (not exposed to clients)
    - salt_key: Salt key for encrypting provider keys
    
    Returns status dict with ok, message, and container info.
    """
    status = await litellm_status()

    if port is None:
        port = LITELLM_DEFAULT_PORT
    if port != LITELLM_HOST_PORT:
        return {
            "ok": False,
            "message": (
                f"LiteLLM must use reserved loopback port {LITELLM_HOST_PORT} "
                "so Caddy can publish api.sycord.site."
            ),
            "running": False,
        }

    from syte.preview_manager import relocate_litellm_preview_conflicts

    preview_migration = await relocate_litellm_preview_conflicts()
    if not preview_migration["ok"]:
        return {
            "ok": False,
            "message": str(preview_migration["message"]),
            "running": False,
            "preview_migration": preview_migration,
        }

    database_ready, database_url, database_message, database_network = await _ensure_litellm_database()
    if not database_ready:
        return {
            "ok": False,
            "message": database_message,
            "running": False,
            "preview_migration": preview_migration,
        }

    rebound_legacy_container = False
    if status["running"]:
        # Do not migrate a database while the proxy is querying it. Stop and
        # remove every running instance before applying Prisma migrations.
        stop_code, stop_output = await _run_docker(["stop", LITELLM_CONTAINER_NAME])
        if stop_code != 0:
            return {
                "ok": False,
                "message": f"Failed to stop LiteLLM before PostgreSQL migrations: {stop_output}",
                "running": True,
                "preview_migration": preview_migration,
            }
        remove_code, remove_output = await _run_docker(["rm", LITELLM_CONTAINER_NAME])
        if remove_code != 0:
            return {
                "ok": False,
                "message": f"Failed to remove LiteLLM before PostgreSQL migrations: {remove_output}",
                "running": False,
                "preview_migration": preview_migration,
            }
        rebound_legacy_container = True

    migrations_ok, migrations_message = await _run_litellm_migrations(
        database_url, database_network
    )
    if not migrations_ok:
        return {
            "ok": False,
            "message": migrations_message,
            "running": False,
            "migration_message": migrations_message,
            "preview_migration": preview_migration,
        }

    # Get configuration from settings if not provided
    if master_key is None:
        master_key = (await get_setting("litellm_master_key", "")).strip()
    if salt_key is None:
        salt_key = (await get_setting("litellm_salt_key", "")).strip()
    
    # Generate defaults if not set
    if not master_key:
        import secrets
        master_key = f"sk-{secrets.token_hex(16)}"
        await set_setting("litellm_master_key", master_key)
    
    if not salt_key:
        import secrets
        salt_key = secrets.token_hex(32)
        await set_setting("litellm_salt_key", salt_key)
    
    # Ensure data directory exists
    try:
        os.makedirs(LITELLM_DATA_DIR, exist_ok=True)
    except OSError as error:
        return {
            "ok": False,
            "message": f"Could not create LiteLLM data directory {LITELLM_DATA_DIR}: {error}",
            "running": False,
            "preview_migration": preview_migration,
        }
    
    # Remove old container if exists (stopped)
    await _run_docker(["rm", "-f", LITELLM_CONTAINER_NAME])
    
    # Run the container
    args = [
        "run",
        "-d",
        "--name", LITELLM_CONTAINER_NAME,
    ]
    if database_network:
        args.extend(["--network", database_network])
    args.extend([
        "-p", f"127.0.0.1:{port}:{LITELLM_CONTAINER_PORT}",
        "-v", f"{LITELLM_DATA_DIR}:/app/litellm",
        "-e", f"LITELLM_MASTER_KEY={master_key}",
        "-e", f"LITELLM_SALT_KEY={salt_key}",
        "-e", f"DATABASE_URL={database_url}",
        "--restart", "unless-stopped",
        LITELLM_IMAGE,
    ])
    
    exit_code, output = await _run_docker(args, timeout=60.0)
    
    if exit_code != 0:
        return {
            "ok": False,
            "message": f"Failed to start LiteLLM: {output}",
            "running": False,
        }
    
    # Wait a moment for container to start
    await asyncio.sleep(2.0)
    
    # Verify it's running
    status = await litellm_status()
    if status["running"]:
        health = await _wait_for_litellm_health()
        if not health.get("healthy"):
            return {
                "ok": False,
                **status,
                "health": health,
                "message": f"Container is running but LiteLLM is not ready: {health.get('message', 'health check failed')}",
            }
        return {
            "ok": True,
            **status,
            "health": health,
            "message": (
                f"LiteLLM rebound to the private loopback port; public API: {LITELLM_PUBLIC_API_URL}; {migrations_message}"
                if rebound_legacy_container
                else f"LiteLLM started successfully; public API: {LITELLM_PUBLIC_API_URL}; {migrations_message}"
            ),
            "preview_migration": preview_migration,
            "migration_message": migrations_message,
        }
    else:
        return {
            "ok": False,
            **status,
            "message": f"Container started but not running: {status.get('message', 'unknown')}",
        }


async def stop_litellm() -> dict[str, Any]:
    """Stop the LiteLLM Docker container."""
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": True,
            "message": "LiteLLM is not running",
            **status,
        }
    
    exit_code, output = await _run_docker(["stop", LITELLM_CONTAINER_NAME])
    
    if exit_code != 0:
        return {
            "ok": False,
            "message": f"Failed to stop LiteLLM: {output}",
        }
    
    return {
        "ok": True,
        "message": "LiteLLM stopped successfully",
        "running": False,
    }


async def restart_litellm() -> dict[str, Any]:
    """Restart the LiteLLM Docker container."""
    # Stop if running
    await stop_litellm()
    
    # Start fresh
    return await start_litellm()


async def litellm_logs(lines: int = 100) -> dict[str, Any]:
    """Get logs from the LiteLLM container."""
    exit_code, output = await _run_docker([
        "logs",
        "--tail", str(lines),
        LITELLM_CONTAINER_NAME,
    ])
    
    return {
        "ok": exit_code == 0,
        "logs": output,
        "message": "" if exit_code == 0 else output,
    }


async def litellm_models() -> dict[str, Any]:
    """Get list of configured models from LiteLLM.
    
    Requires LiteLLM to be running with master key configured.
    """
    import httpx
    
    master_key = (await get_setting("litellm_master_key", "")).strip()
    if not master_key:
        return {
            "ok": False,
            "message": "LiteLLM master key not configured",
            "models": [],
        }
    
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": False,
            "message": "LiteLLM is not running",
            "models": [],
        }
    
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.get(
                f"{LITELLM_INTERNAL_ORIGIN}/models",
                headers={"Authorization": f"Bearer {master_key}"},
            )
            
            if resp.status_code == 200:
                payload = resp.json()
                if isinstance(payload, list):
                    models = payload
                elif isinstance(payload, dict):
                    models = payload.get("data", [])
                else:
                    models = []
                if not isinstance(models, list):
                    models = []
                return {
                    "ok": True,
                    "models": models,
                    "count": len(models),
                }
            else:
                return {
                    "ok": False,
                    "message": f"Failed to fetch models: HTTP {resp.status_code}",
                    "models": [],
                }
    except (httpx.HTTPError, ValueError) as e:
        return {
            "ok": False,
            "message": f"Error fetching models: {e}",
            "models": [],
        }


async def litellm_health() -> dict[str, Any]:
    """Check LiteLLM health endpoint."""
    import httpx
    
    status = await litellm_status()
    if not status["running"]:
        return {
            "ok": False,
            "healthy": False,
            "message": "LiteLLM is not running",
        }
    
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(f"{LITELLM_INTERNAL_ORIGIN}/health/readiness")
            
            if resp.status_code == 200:
                return {
                    "ok": True,
                    "healthy": True,
                    "message": "LiteLLM is healthy",
                }
            else:
                return {
                    "ok": False,
                    "healthy": False,
                    "message": f"Health check failed: HTTP {resp.status_code}",
                }
    except httpx.HTTPError as e:
        return {
            "ok": False,
            "healthy": False,
            "message": f"Health check error: {e}",
        }
