"""Docker-backed lifecycle operations for managed platform databases.

The catalog remains pure; this module is the effectful boundary. Database
containers join the destination's private Docker network and publish ports only
when the operator explicitly enables ``is_public``.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from syte.platform.database_catalog import (
    connection_details,
    container_command,
    container_env,
    data_volume_mount,
    engine_for,
    container_extra_args,
)
from syte.platform.store import get_database, update
from syte.platform.types import safe_name
from syte.workspace import run_cmd


def container_name(db: dict[str, Any]) -> str:
    return f"syte-db-{safe_name(str(db.get('name') or db.get('uuid') or 'database'), limit=40)}-{str(db.get('uuid') or 'db')[:8]}"


def volume_name(db: dict[str, Any]) -> str:
    return f"syte-db-data-{str(db.get('uuid') or safe_name(str(db.get('name') or 'database')))[:48]}"


def network_name(db: dict[str, Any]) -> str:
    return safe_name(str(db.get("network") or db.get("destination_network") or "syte"), fallback="syte", limit=50)


def _argv(db: dict[str, Any]) -> list[str]:
    name = container_name(db)
    network = network_name(db)
    args = [
        "docker", "run", "-d",
        "--name", name,
        "--restart", "unless-stopped",
        "--network", network,
        "--label", "io.syte.resource=database",
        "--label", f"io.syte.database={db.get('uuid', '')}",
    ]
    for key, value in container_env(db).items():
        args.extend(["-e", f"{key}={value}"])
    args.extend(container_extra_args(db))
    args.extend(["-v", f"{volume_name(db)}:{data_volume_mount(db)}"])
    if bool(db.get("is_public")) and db.get("public_port"):
        args.extend(["-p", f"{int(db['public_port'])}:{int(db.get('internal_port') or engine_for(str(db['database_type'])).default_port)}"])
    command = container_command(db)
    args.append(str(db["image"]))
    args.extend(command)
    return args


def _run(args: list[str]) -> tuple[bool, str]:
    code, out = run_cmd(args)
    return code == 0, out.strip()


async def _run_async(args: list[str]) -> tuple[bool, str]:
    return await asyncio.to_thread(_run, args)


async def ensure_network(db: dict[str, Any]) -> tuple[bool, str]:
    network = network_name(db)
    ok, out = await _run_async(["docker", "network", "inspect", network])
    if ok:
        return True, network
    ok, out = await _run_async(["docker", "network", "create", network])
    return (ok, network if ok else out)


async def start_database(db: dict[str, Any]) -> tuple[bool, str]:
    ok, message = await ensure_network(db)
    if not ok:
        return False, f"Could not create private network: {message}"
    name = container_name(db)
    exists, _ = await _run_async(["docker", "container", "inspect", name])
    if exists:
        ok, out = await _run_async(["docker", "start", name])
    else:
        await _run_async(["docker", "volume", "create", volume_name(db)])
        ok, out = await _run_async(_argv(db))
    await update("platform_databases", str(db["uuid"]), {"status": "running" if ok else "error"})
    return ok, out or (f"Started {name}." if ok else "Database start failed.")


async def stop_database(db: dict[str, Any]) -> tuple[bool, str]:
    ok, out = await _run_async(["docker", "stop", container_name(db)])
    if ok:
        await update("platform_databases", str(db["uuid"]), {"status": "stopped"})
    return ok, out or ("Stopped." if ok else "Database is not running.")


async def delete_database(db: dict[str, Any], *, delete_volume: bool = False) -> tuple[bool, str]:
    name = container_name(db)
    await _run_async(["docker", "rm", "-f", name])
    if delete_volume:
        await _run_async(["docker", "volume", "rm", volume_name(db)])
    ok, out = await _run_async(["docker", "inspect", name])
    return (not ok, "Deleted." if not ok else out)


async def database_status(db: dict[str, Any]) -> dict[str, Any]:
    ok, out = await _run_async(["docker", "inspect", container_name(db), "--format", "{{json .State}}"])
    if not ok:
        return {"status": "stopped", "container": container_name(db), "exists": False}
    try:
        state = json.loads(out)
    except json.JSONDecodeError:
        state = {"status": "unknown", "raw": out}
    return {"status": state.get("Status", "unknown"), "health": (state.get("Health") or {}).get("Status"), "container": container_name(db), "exists": True}


async def database_connection(db_uuid: str) -> dict[str, Any] | None:
    db = await get_database(db_uuid, include_secrets=True)
    if db is None:
        return None
    details = connection_details(db, internal_host=container_name(db))
    details["env"] = {
        "DATABASE_URL": details.get("internal_url", "") if str(db.get("database_type")) not in {"redis", "keydb", "dragonfly"} else "",
        "REDIS_URL": details.get("internal_url", "") if str(db.get("database_type")) in {"redis", "keydb", "dragonfly"} else "",
    }
    return details
