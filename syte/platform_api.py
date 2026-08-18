"""Authenticated platform resource APIs used by the Syte dashboard."""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from syte.auth import verify_operator_session_or_token
from syte.platform.backup_manager import backup_executions, execute_backup, restore_backup
from syte.platform.database_catalog import catalog, provision_defaults
from syte.platform.database_runtime import (
    database_connection,
    database_status,
    delete_database,
    start_database,
    stop_database,
)
from syte.platform.store import (
    delete,
    ensure_bootstrap,
    find,
    get,
    get_database,
    get_server,
    insert,
    list_server_resources,
    list_servers,
    server_metrics,
    update,
)
from syte.platform.types import new_uuid, utcnow

router = APIRouter(prefix="/platform", tags=["Platform resources"])


class CreateDatabaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    database_type: str
    version: str = ""
    environment_uuid: str | None = None
    public: bool = False
    public_port: int | None = Field(default=None, ge=1, le=65535)
    auto_start: bool = True


class ServerUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=500)
    ip: str | None = None
    user: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    wildcard_domain: str | None = None
    proxy_type: str | None = None
    is_build_server: bool | None = None
    concurrent_builds: int | None = Field(default=None, ge=1, le=64)
    is_swarm_manager: bool | None = None
    is_swarm_worker: bool | None = None


class ServerCommandRequest(BaseModel):
    command: str = Field(min_length=1, max_length=2000)


class CreateBackupRequest(BaseModel):
    database_uuid: str
    frequency: str = "0 0 * * *"
    save_locally: bool = True
    retention_amount_locally: int = Field(default=7, ge=1, le=365)
    s3_storage_uuid: str | None = None
    enabled: bool = True


async def _operator(_: dict[str, Any] = Depends(verify_operator_session_or_token)) -> dict[str, Any]:
    return _


@router.get("/servers", dependencies=[Depends(_operator)])
async def list_platform_servers() -> list[dict[str, Any]]:
    await ensure_bootstrap()
    return await list_servers()


@router.get("/servers/{uuid}", dependencies=[Depends(_operator)])
async def get_platform_server(uuid: str) -> dict[str, Any]:
    server = await get_server(uuid)
    if server is None:
        raise HTTPException(404, "Server not found.")
    resources = await list_server_resources(uuid)
    return {"server": server, "resources": resources, "metrics": await server_metrics(uuid, limit=60)}


@router.put("/servers/{uuid}", dependencies=[Depends(_operator)])
async def update_platform_server(uuid: str, body: ServerUpdateRequest) -> dict[str, Any]:
    if await get_server(uuid) is None:
        raise HTTPException(404, "Server not found.")
    values = body.model_dump(exclude_none=True)
    return await update("platform_servers", uuid, values) or {}


@router.get("/servers/{uuid}/resources", dependencies=[Depends(_operator)])
async def platform_server_resources(uuid: str) -> dict[str, list[dict[str, Any]]]:
    if await get_server(uuid) is None:
        raise HTTPException(404, "Server not found.")
    return await list_server_resources(uuid)


@router.get("/servers/{uuid}/metrics", dependencies=[Depends(_operator)])
async def platform_server_metrics(uuid: str, limit: int = 60) -> list[dict[str, Any]]:
    if await get_server(uuid) is None:
        raise HTTPException(404, "Server not found.")
    return await server_metrics(uuid, limit=max(1, min(limit, 240)))


@router.post("/servers/{uuid}/terminal", dependencies=[Depends(_operator)])
async def run_server_terminal(uuid: str, body: ServerCommandRequest) -> dict[str, Any]:
    server = await get_server(uuid)
    if server is None:
        raise HTTPException(404, "Server not found.")
    if not bool(server.get("is_local")):
        raise HTTPException(400, "Remote server terminal is not enabled in this local workspace.")
    from syte.workspace import run_cmd
    code, output = await asyncio.to_thread(run_cmd, ["bash", "-lc", body.command])
    return {"ok": code == 0, "exit_code": code, "output": output[-20000:]}


@router.get("/databases/catalog", dependencies=[Depends(_operator)])
async def database_catalog() -> list[dict[str, object]]:
    return catalog()


@router.get("/databases", dependencies=[Depends(_operator)])
async def list_databases() -> list[dict[str, Any]]:
    return await find("platform_databases", {}, order_by="created_at DESC")


@router.post("/databases", dependencies=[Depends(_operator)])
async def create_database(body: CreateDatabaseRequest) -> dict[str, Any]:
    try:
        defaults = provision_defaults(body.database_type, body.name, version=body.version)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    bootstrap = await ensure_bootstrap()
    environment_uuid = body.environment_uuid or bootstrap["environment"]["uuid"]
    row = {
        **defaults,
        "uuid": new_uuid(),
        "environment_uuid": environment_uuid,
        "server_uuid": bootstrap["server"]["uuid"],
        "destination_uuid": bootstrap["destination"]["uuid"],
        "is_public": body.public,
        "public_port": body.public_port if body.public else None,
        "created_at": utcnow(),
        "updated_at": utcnow(),
    }
    created = await insert("platform_databases", row)
    if body.auto_start:
        ok, message = await start_database({**created, **defaults})
        created["status"] = "running" if ok else "error"
        created["lifecycle_message"] = message
    return created


async def _database_or_404(uuid: str) -> dict[str, Any]:
    row = await get_database(uuid, include_secrets=True)
    if row is None:
        raise HTTPException(404, "Database not found.")
    return row


@router.get("/databases/{uuid}/status", dependencies=[Depends(_operator)])
async def get_database_status(uuid: str) -> dict[str, Any]:
    db = await _database_or_404(uuid)
    return {"database": db, "runtime": await database_status(db)}


@router.post("/databases/{uuid}/start", dependencies=[Depends(_operator)])
async def start_managed_database(uuid: str) -> dict[str, Any]:
    db = await _database_or_404(uuid)
    ok, message = await start_database(db)
    if not ok:
        raise HTTPException(502, message)
    return {"ok": True, "message": message, "runtime": await database_status(db)}


@router.post("/databases/{uuid}/stop", dependencies=[Depends(_operator)])
async def stop_managed_database(uuid: str) -> dict[str, Any]:
    db = await _database_or_404(uuid)
    ok, message = await stop_database(db)
    if not ok:
        raise HTTPException(502, message)
    return {"ok": True, "message": message, "runtime": await database_status(db)}


@router.get("/databases/{uuid}/connection", dependencies=[Depends(_operator)])
async def get_database_connection(uuid: str) -> dict[str, Any]:
    await _database_or_404(uuid)
    details = await database_connection(uuid)
    if details is None:
        raise HTTPException(404, "Database not found.")
    return details


@router.delete("/databases/{uuid}", dependencies=[Depends(_operator)])
async def delete_managed_database(uuid: str, delete_volume: bool = False) -> dict[str, Any]:
    db = await _database_or_404(uuid)
    ok, message = await delete_database(db, delete_volume=delete_volume)
    if not ok:
        raise HTTPException(502, message)
    await delete("platform_databases", uuid)
    return {"ok": True, "message": message}


@router.get("/backups", dependencies=[Depends(_operator)])
async def list_backups() -> list[dict[str, Any]]:
    return await find("platform_backups", {}, order_by="created_at DESC")


@router.post("/backups", dependencies=[Depends(_operator)])
async def create_backup(body: CreateBackupRequest) -> dict[str, Any]:
    database = await _database_or_404(body.database_uuid)
    row = await insert(
        "platform_backups",
        {
            "uuid": new_uuid(),
            "database_uuid": database["uuid"],
            "frequency": body.frequency,
            "save_locally": body.save_locally,
            "retention_amount_locally": body.retention_amount_locally,
            "s3_storage_uuid": body.s3_storage_uuid,
            "enabled": body.enabled,
            "created_at": utcnow(),
            "updated_at": utcnow(),
        },
    )
    return row


@router.get("/backups/{uuid}/executions", dependencies=[Depends(_operator)])
async def list_backup_executions(uuid: str) -> list[dict[str, Any]]:
    return await backup_executions(uuid)


@router.post("/backups/{uuid}/run", dependencies=[Depends(_operator)])
async def run_backup(uuid: str) -> dict[str, Any]:
    backup = await get("platform_backups", uuid, include_secrets=True)
    if backup is None:
        raise HTTPException(404, "Backup schedule not found.")
    database = await _database_or_404(str(backup["database_uuid"]))
    storage = None
    if backup.get("s3_storage_uuid"):
        storage = await get("platform_s3_storages", str(backup["s3_storage_uuid"]), include_secrets=True)
    return await execute_backup(backup, database, storage)


@router.post("/backup-executions/{uuid}/restore", dependencies=[Depends(_operator)])
async def restore_backup_execution(uuid: str) -> dict[str, Any]:
    execution = await get("platform_backup_executions", uuid)
    if execution is None:
        raise HTTPException(404, "Backup execution not found.")
    backup = await get("platform_backups", str(execution["backup_uuid"]), include_secrets=True)
    if backup is None:
        raise HTTPException(404, "Backup schedule not found.")
    database = await _database_or_404(str(backup["database_uuid"]))
    ok, message = await restore_backup(execution, database)
    if not ok:
        raise HTTPException(502, message)
    return {"ok": True, "message": message}
