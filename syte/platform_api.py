"""Authenticated platform resource APIs used by the Syte dashboard."""
from __future__ import annotations

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
    insert,
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


class CreateBackupRequest(BaseModel):
    database_uuid: str
    frequency: str = "0 0 * * *"
    save_locally: bool = True
    retention_amount_locally: int = Field(default=7, ge=1, le=365)
    s3_storage_uuid: str | None = None
    enabled: bool = True


async def _operator(_: dict[str, Any] = Depends(verify_operator_session_or_token)) -> dict[str, Any]:
    return _


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


_PLATFORM_NAV_PAGES: dict[str, dict[str, Any]] = {
    "projects": {"title": "Projects", "description": "Applications, environments, and deployment resources managed by Syte.", "tables": ["platform_projects", "platform_applications"]},
    "overview": {"title": "Overview", "description": "Live platform inventory across projects, applications, databases, and backups.", "tables": ["platform_projects", "platform_applications", "platform_databases", "platform_backups"]},
    "schedules": {"title": "Schedules", "description": "Backup and scheduled-task records currently configured on this Syte instance.", "tables": ["platform_backups", "platform_scheduled_tasks"]},
    "traefik": {"title": "Traefik File System", "description": "Proxy configuration is generated from Syte domains and certificates.", "tables": ["platform_certificates", "platform_domains"]},
    "docker": {"title": "Docker", "description": "Managed containers, databases, and deployment runtime inventory.", "tables": ["platform_databases", "platform_applications", "platform_services"]},
    "profile": {"title": "Profile", "description": "Current operator and instance identity settings.", "tables": []},
    "sessions": {"title": "Sessions", "description": "Current operator session status and session policy.", "tables": []},
    "remote-servers": {"title": "Remote Servers", "description": "Servers available for deployment and platform resource placement.", "tables": ["platform_servers"]},
    "audit-logs": {"title": "Audit Logs", "description": "Recent webhook and platform events retained by Syte.", "tables": ["platform_webhook_events"]},
    "ssh-keys": {"title": "SSH Keys", "description": "SSH key inventory used by remote deployment integrations.", "tables": ["platform_ssh_keys"]},
    "ai": {"title": "AI", "description": "Configured model providers and built-in agent capabilities.", "tables": ["model_configs"]},
    "tags": {"title": "Tags", "description": "Resource tagging inventory for organizing platform objects.", "tables": ["platform_tags"]},
    "git": {"title": "Git", "description": "Git provider and repository configuration used by deployments.", "tables": ["platform_git_sources", "platform_applications"]},
    "registry": {"title": "Registry", "description": "Container image sources and registry-backed deployments.", "tables": ["platform_registries", "platform_applications"]},
    "secrets": {"title": "Secrets", "description": "Environment and shared-secret records are shown without secret values.", "tables": ["platform_shared_env_vars", "platform_env_vars"]},
    "dns-providers": {"title": "DNS Providers", "description": "DNS provider configuration used for domain automation.", "tables": ["platform_dns_providers"]},
    "s3-destinations": {"title": "S3 Destinations", "description": "Backup destinations configured for managed database exports.", "tables": ["platform_s3_storages"]},
    "certificates": {"title": "Certificates", "description": "TLS certificate and domain records used by the proxy.", "tables": ["platform_certificates"]},
    "notifications": {"title": "Notifications", "description": "Configured notification channels for platform events.", "tables": ["platform_notification_channels"]},
    "billing": {"title": "Billing", "description": "Local billing readiness and resource usage summary.", "tables": ["platform_projects", "platform_databases"]},
    "license": {"title": "License", "description": "Syte installation version and feature availability.", "tables": []},
    "sso": {"title": "SSO", "description": "Single sign-on readiness and configured identity-provider state.", "tables": []},
    "documentation": {"title": "Documentation", "description": "API and operator documentation for the running Syte instance.", "tables": []},
    "support": {"title": "Support", "description": "Support resources, diagnostics, and recent platform events.", "tables": ["platform_webhook_events"]},
}


@router.get("/navigation/{page}", dependencies=[Depends(_operator)])
async def navigation_page(page: str) -> dict[str, Any]:
    config = _PLATFORM_NAV_PAGES.get(page)
    if config is None:
        raise HTTPException(404, "Platform page not found.")
    resources: list[dict[str, Any]] = []
    for table in config["tables"]:
        try:
            rows = await find(table, {}, order_by="created_at DESC", limit=25)
        except Exception:
            rows = []
        for row in rows:
            safe = {key: value for key, value in row.items() if key not in {"secret", "password", "token", "credentials", "private_key", "env"}}
            safe["_table"] = table
            resources.append(safe)
    return {
        "page": page,
        "title": config["title"],
        "description": config["description"],
        "resource_count": len(resources),
        "resources": resources[:100],
        "actions": [
            {"id": "refresh", "label": "Refresh data", "method": "GET"},
            {"id": "open-api", "label": "Open API reference", "href": "/api/"},
        ],
    }
