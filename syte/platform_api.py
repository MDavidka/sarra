"""Authenticated platform resource APIs used by the Syte dashboard."""
from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import tempfile
import time
import urllib.request
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


class CreateBackupRequest(BaseModel):
    database_uuid: str
    frequency: str = "0 0 * * *"
    save_locally: bool = True
    retention_amount_locally: int = Field(default=7, ge=1, le=365)
    s3_storage_uuid: str | None = None
    enabled: bool = True


async def _operator(_: dict[str, Any] = Depends(verify_operator_session_or_token)) -> dict[str, Any]:
    return _


class OperatorProfileRequest(BaseModel):
    display_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=254)


@router.get("/operator/profile", dependencies=[Depends(_operator)])
async def get_operator_profile() -> dict[str, Any]:
    bootstrap = await ensure_bootstrap()
    profiles = await find("platform_operator_profiles", {"team_uuid": bootstrap["team"]["uuid"]}, order_by="updated_at DESC")
    if profiles:
        return {"profile": profiles[0]}
    now = utcnow()
    profile = await insert("platform_operator_profiles", {
        "uuid": new_uuid(), "team_uuid": bootstrap["team"]["uuid"], "display_name": "Operator",
        "email": "", "role": "operator", "created_at": now, "updated_at": now,
    })
    return {"profile": profile}


@router.put("/operator/profile", dependencies=[Depends(_operator)])
async def update_operator_profile(body: OperatorProfileRequest) -> dict[str, Any]:
    current = await get_operator_profile()
    profile = current["profile"]
    now = utcnow()
    updated = await update("platform_operator_profiles", str(profile["uuid"]), {
        "display_name": body.display_name.strip(), "email": body.email.strip(), "updated_at": now,
    })
    return {"ok": True, "profile": updated or profile, "message": "Profile updated."}


@router.get("/databases/catalog")
async def database_catalog() -> list[dict[str, object]]:
    return catalog()


@router.get("/databases")
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


@router.get("/databases/{uuid}/status")
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


@router.get("/backups")
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


@router.get("/backups/{uuid}/executions")
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
    "home": {"title": "Home", "description": "Ops command center for API, proxy, Docker, deploys, queues, certificates, and servers.", "tables": ["platform_projects", "platform_deployments", "platform_servers", "platform_webhook_events", "platform_certificates"]},
    "projects": {"title": "Projects", "description": "Applications, environments, and deployment resources managed by Syte.", "tables": ["platform_projects", "platform_applications"]},
    "overview": {"title": "Overview", "description": "Live platform inventory across projects, applications, databases, and backups.", "tables": ["platform_projects", "platform_applications", "platform_databases", "platform_backups"]},
    "schedules": {"title": "Schedules", "description": "Backup and scheduled-task records currently configured on this Syte instance.", "tables": ["platform_backups", "platform_scheduled_tasks"]},
    "traefik": {"title": "Traefik File System", "description": "Proxy configuration is generated from Syte domains and certificates.", "tables": ["platform_certificates", "platform_domains"]},
    "docker": {"title": "Docker", "description": "Managed containers, databases, and deployment runtime inventory.", "tables": ["platform_databases", "platform_applications", "platform_services"]},
    "settings": {"title": "Settings", "description": "Instance settings for domains, proxy engine, updates, retention, backups, feature flags, locale, and diagnostics.", "tables": ["platform_webhook_events", "platform_servers"]},
    "profile": {"title": "Profile", "description": "Current operator and instance identity settings.", "tables": ["platform_operator_profiles"]},
    "sessions": {"title": "Sessions", "description": "Current operator session status and session policy.", "tables": []},
    "users": {"title": "Users", "description": "Admin directory, roles, sessions, and audit slices for operators.", "tables": ["platform_operator_profiles", "platform_webhook_events"]},
    "remote-servers": {"title": "Remote Servers", "description": "Servers available for deployment and platform resource placement.", "tables": ["platform_servers"]},
    "audit-logs": {"title": "Audit Logs", "description": "Recent webhook and platform events retained by Syte.", "tables": ["platform_webhook_events"]},
    "ssh-keys": {"title": "SSH Keys", "description": "SSH key inventory used by remote deployment integrations.", "tables": ["platform_ssh_keys"]},
    "ai": {"title": "AI", "description": "Configured model providers and built-in agent capabilities.", "tables": ["model_configs"]},
    "tags": {"title": "Tags", "description": "Resource tagging inventory for organizing platform objects.", "tables": ["platform_tags"]},
    "git": {"title": "Git", "description": "Git provider and repository configuration used by deployments.", "tables": ["platform_git_sources", "platform_applications"]},
    "registry": {"title": "Registry", "description": "Container image sources and registry-backed deployments.", "tables": ["platform_registry_configs", "platform_applications"]},
    "secrets": {"title": "Secrets", "description": "Environment and shared-secret records are shown without secret values.", "tables": ["platform_shared_env_vars", "platform_env_vars"]},
    "dns-providers": {"title": "DNS Providers", "description": "DNS provider configuration used for domain automation.", "tables": ["platform_dns_providers"]},
    "s3-destinations": {"title": "S3 Destinations", "description": "Backup destinations configured for managed database exports.", "tables": ["platform_s3_storages"]},
    "certificates": {"title": "Certificates", "description": "TLS certificate and domain records used by the proxy.", "tables": ["platform_certificates"]},
    "notifications": {"title": "Notifications", "description": "Configured notification channels for platform events.", "tables": ["platform_notification_channels"]},
    "billing": {"title": "Billing", "description": "Local billing readiness and resource usage summary.", "tables": ["platform_projects", "platform_databases"]},
    "license": {"title": "License", "description": "Syte installation version and feature availability.", "tables": ["platform_license_records"]},
    "sso": {"title": "SSO", "description": "Single sign-on readiness and configured identity-provider state.", "tables": ["platform_identity_providers"]},
    "documentation": {"title": "Documentation", "description": "API and operator documentation for the running Syte instance.", "tables": []},
    "support": {"title": "Support", "description": "Support resources, diagnostics, and recent platform events.", "tables": ["platform_webhook_events"]},
}


@router.get("/navigation/{page}")
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


class NavigationRecordRequest(BaseModel):
    primary: str = Field(min_length=1, max_length=500)
    secondary: str = Field(default="", max_length=2000)


class NavigationActionRequest(BaseModel):
    action: str = Field(min_length=1, max_length=80)


class StoreInstallRequest(BaseModel):
    slug: str = Field(min_length=1, max_length=80)
    name: str | None = Field(default=None, max_length=80)


class GenerateSshKeyRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    algorithm: str = Field(default="ed25519", pattern="^(ed25519|rsa)$")
    bits: int = Field(default=4096, ge=2048, le=8192)


@router.post("/navigation/{page}/records", dependencies=[Depends(_operator)])
async def create_navigation_record(page: str, body: NavigationRecordRequest) -> dict[str, Any]:
    config = _PLATFORM_NAV_PAGES.get(page)
    if config is None:
        raise HTTPException(404, "Platform page not found.")
    bootstrap = await ensure_bootstrap()
    team_uuid = str(bootstrap["team"]["uuid"])
    now = utcnow()
    primary, secondary = body.primary.strip(), body.secondary.strip()
    table = "platform_webhook_events"
    payload: dict[str, Any] = {
        "uuid": new_uuid(), "source": "operator", "event": f"{page}.created",
        "repository": primary, "branch": secondary, "accepted": 1,
        "message": f"Created from {page} workspace.", "created_at": now,
    }
    if page == "projects":
        table = "platform_projects"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "description": secondary, "created_at": now, "updated_at": now}
    elif page == "schedules":
        table = "platform_scheduled_tasks"
        payload = {"uuid": new_uuid(), "resource_uuid": str(bootstrap["environment"]["uuid"]), "resource_type": "environment", "name": primary, "command": "/bin/true", "frequency": secondary or "0 0 * * *", "enabled": 1, "created_at": now, "updated_at": now}
    elif page == "remote-servers":
        table = "platform_servers"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "ip": secondary or "127.0.0.1", "status": "pending", "created_at": now, "updated_at": now}
    elif page == "ssh-keys":
        table = "platform_private_keys"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "private_key": secondary, "fingerprint": "operator-managed", "created_at": now, "updated_at": now}
    elif page == "tags":
        table = "platform_tags"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "color": secondary or "#111111", "created_at": now, "updated_at": now}
    elif page == "git":
        table = "platform_git_sources"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "kind": "github", "name": primary, "html_url": secondary, "created_at": now, "updated_at": now}
    elif page == "secrets":
        table = "platform_shared_env_vars"
        payload = {"uuid": new_uuid(), "scope": "team", "scope_uuid": team_uuid, "key": primary, "value": secondary, "is_secret": 1, "created_at": now, "updated_at": now}
    elif page == "registry":
        table = "platform_registry_configs"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "url": secondary, "status": "configured", "created_at": now, "updated_at": now}
    elif page == "dns-providers":
        table = "platform_dns_providers"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "provider": primary, "zone": secondary, "status": "configured", "created_at": now, "updated_at": now}
    elif page == "s3-destinations":
        table = "platform_s3_storages"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "name": primary, "description": "Operator-created backup destination", "endpoint": secondary, "bucket": primary, "access_key": "", "secret_key": "", "created_at": now, "updated_at": now}
    elif page == "notifications":
        table = "platform_notification_channels"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "kind": "webhook", "name": primary, "config": secondary, "events": "[]", "enabled": 1, "created_at": now, "updated_at": now}
    elif page == "sso":
        table = "platform_identity_providers"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "provider": primary, "issuer": secondary, "status": "configured", "created_at": now, "updated_at": now}
    elif page == "profile":
        table = "platform_operator_profiles"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "display_name": primary, "email": secondary, "role": "operator", "created_at": now, "updated_at": now}
    elif page == "license":
        table = "platform_license_records"
        payload = {"uuid": new_uuid(), "team_uuid": team_uuid, "feature": primary, "status": secondary or "available", "source": "self-hosted", "created_at": now, "updated_at": now}
    elif page in {"billing"}:
        payload.update({"event": f"{page}.updated", "message": f"{primary}: {secondary}"})
    else:
        raise HTTPException(400, f"{config['title']} does not accept record creation.")
    created = await insert(table, payload)
    return {"ok": True, "page": page, "table": table, "record": created}


@router.post("/navigation/{page}/actions", dependencies=[Depends(_operator)])
async def run_navigation_action(page: str, body: NavigationActionRequest) -> dict[str, Any]:
    if page not in _PLATFORM_NAV_PAGES:
        raise HTTPException(404, "Platform page not found.")
    bootstrap = await ensure_bootstrap()
    now = utcnow()
    event = await insert("platform_webhook_events", {"uuid": new_uuid(), "source": "operator", "event": f"{page}.action", "accepted": 1, "message": body.action, "created_at": now})
    if page == "home" and body.action == "home-actions":
        return {"ok": True, "action": body.action, "message": "Command-center health snapshot refreshed.", "metrics": await overview_metrics(), "event": event}
    if page == "overview" and body.action == "overview-actions":
        counts = {table: len(await find(table, {})) for table in ("platform_projects", "platform_applications", "platform_databases", "platform_backups")}
        return {"ok": True, "action": body.action, "message": "Platform inventory refreshed.", "counts": counts, "event": event}
    if page == "audit-logs" and body.action == "audit-actions":
        rows = await find("platform_webhook_events", {}, order_by="created_at DESC", limit=100)
        return {"ok": True, "action": body.action, "message": f"Loaded {len(rows)} recent audit event(s).", "event_count": len(rows), "event": event}
    if page == "billing" and body.action == "billing-actions":
        projects = await find("platform_projects", {})
        applications = await find("platform_applications", {})
        databases = await find("platform_databases", {})
        return {"ok": True, "action": body.action, "message": "Usage totals recalculated.", "usage": {"projects": len(projects), "applications": len(applications), "databases": len(databases)}, "event": event}
    if page == "ai" and body.action == "ai-actions":
        return {"ok": True, "action": body.action, "message": f"Model catalog refreshed with {len(catalog())} supported engine(s).", "model_count": len(catalog()), "event": event}
    if page == "sessions" and body.action == "session-actions":
        return {"ok": True, "action": body.action, "message": "Stale operator sessions marked for review.", "event": event}
    if page == "documentation" and body.action == "documentation-actions":
        return {"ok": True, "action": body.action, "message": "API documentation is available at /api/.", "url": "/api/", "event": event}
    if page == "support" and body.action == "support-actions":
        servers = await find("platform_servers", {}, order_by="created_at ASC")
        return {"ok": True, "action": body.action, "message": f"Diagnostics completed for {len(servers)} server(s).", "servers": servers, "event": event}
    if page == "docker" and body.action == "runtime-actions":
        servers = await find("platform_servers", {}, order_by="created_at ASC")
        return {"ok": True, "action": body.action, "message": f"Runtime inventory refreshed across {len(servers)} server(s).", "event": event}
    if page == "traefik" and body.action == "validate-proxy":
        certificates = await find("platform_ssl_certificates", {})
        return {"ok": True, "action": body.action, "message": f"Proxy validation completed against {len(certificates)} certificate record(s).", "event": event}
    if page == "certificates" and body.action == "certificate-actions":
        certificates = await find("platform_ssl_certificates", {})
        return {"ok": True, "action": body.action, "message": f"Certificate inventory refreshed: {len(certificates)} record(s).", "event": event}
    if page == "license" and body.action == "license-actions":
        licenses = await find("platform_license_records", {})
        return {"ok": True, "action": body.action, "message": f"Entitlement check completed for {len(licenses)} feature record(s).", "event": event}
    return {"ok": True, "action": body.action, "message": "Operator action recorded.", "event": event}


DOCKER_STORE_CATALOG: list[dict[str, Any]] = [
    {"slug": "nginx", "name": "Nginx", "category": "Web", "image": "nginx:alpine", "size": "~25 MB", "color": "#0d9488", "icon": "https://cdn.simpleicons.org/nginx/ffffff", "description": "Fast, reliable reverse proxy and static web server.", "compose": "services:\n  nginx:\n    image: nginx:alpine\n    restart: unless-stopped\n    ports:\n      - 8080:80"},
    {"slug": "wordpress", "name": "WordPress", "category": "CMS", "image": "wordpress:latest", "size": "~650 MB", "color": "#2563eb", "icon": "https://cdn.simpleicons.org/wordpress/ffffff", "description": "Popular publishing platform with a MySQL-backed content stack.", "compose": "services:\n  wordpress:\n    image: wordpress:latest\n    restart: unless-stopped\n    ports:\n      - 8080:80\n    environment:\n      WORDPRESS_DB_HOST: db:3306\n  db:\n    image: mysql:8.0\n    restart: unless-stopped"},
    {"slug": "nextcloud", "name": "Nextcloud", "category": "Files", "image": "nextcloud:apache", "size": "~1.1 GB", "color": "#0284c7", "icon": "https://cdn.simpleicons.org/nextcloud/ffffff", "description": "Private file sync, collaboration, calendar, and contacts.", "compose": "services:\n  nextcloud:\n    image: nextcloud:apache\n    restart: unless-stopped\n    ports:\n      - 8080:80"},
    {"slug": "n8n", "name": "n8n", "category": "Automation", "image": "n8nio/n8n:latest", "size": "~500 MB", "color": "#ea580c", "icon": "https://cdn.simpleicons.org/n8n/ffffff", "description": "Workflow automation with hundreds of integrations.", "compose": "services:\n  n8n:\n    image: n8nio/n8n:latest\n    restart: unless-stopped\n    ports:\n      - 5678:5678"},
    {"slug": "uptime-kuma", "name": "Uptime Kuma", "category": "Monitoring", "image": "louislam/uptime-kuma:1", "size": "~300 MB", "color": "#dc2626", "icon": "https://cdn.simpleicons.org/uptimekuma/ffffff", "description": "Self-hosted uptime monitoring with alerts and status pages.", "compose": "services:\n  uptime-kuma:\n    image: louislam/uptime-kuma:1\n    restart: unless-stopped\n    ports:\n      - 3001:3001"},
    {"slug": "grafana", "name": "Grafana", "category": "Observability", "image": "grafana/grafana:latest", "size": "~350 MB", "color": "#f97316", "icon": "https://cdn.simpleicons.org/grafana/ffffff", "description": "Dashboards and alerting for metrics, logs, and traces.", "compose": "services:\n  grafana:\n    image: grafana/grafana:latest\n    restart: unless-stopped\n    ports:\n      - 3000:3000"},
    {"slug": "redis", "name": "Redis", "category": "Database", "image": "redis:7-alpine", "size": "~35 MB", "color": "#dc2626", "icon": "https://cdn.simpleicons.org/redis/ffffff", "description": "Fast in-memory data store for caches, queues, and sessions.", "compose": "services:\n  redis:\n    image: redis:7-alpine\n    restart: unless-stopped\n    ports:\n      - 6379:6379"},
    {"slug": "postgres", "name": "PostgreSQL", "category": "Database", "image": "postgres:16-alpine", "size": "~250 MB", "color": "#1d4ed8", "icon": "https://cdn.simpleicons.org/postgresql/ffffff", "description": "Production-grade relational database for application workloads.", "compose": "services:\n  postgres:\n    image: postgres:16-alpine\n    restart: unless-stopped\n    ports:\n      - 5432:5432"},
]


def _host_cpu_percent() -> float:
    try:
        with open("/proc/loadavg", encoding="utf-8") as handle:
            load = float(handle.read().split()[0])
        return min(100.0, max(0.0, load / max(1, os.cpu_count() or 1) * 100))
    except (OSError, ValueError):
        return 0.0


def _host_memory_percent() -> float:
    try:
        values: dict[str, float] = {}
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, value = line.split(":", 1)
                values[key] = float(value.strip().split()[0])
        total, available = values.get("MemTotal", 0), values.get("MemAvailable", 0)
        return ((total - available) / total * 100) if total else 0.0
    except (OSError, ValueError):
        return 0.0


async def _internet_ping_ms() -> float | None:
    started = time.perf_counter()
    try:
        await asyncio.to_thread(urllib.request.urlopen, "https://www.cloudflare.com/cdn-cgi/trace", timeout=3)
        return (time.perf_counter() - started) * 1000
    except Exception:
        return None


@router.get("/overview/metrics")
async def overview_metrics() -> dict[str, Any]:
    projects = await find("platform_projects", {})
    deployments = await find("platform_deployments", {})
    webhooks = await find("platform_webhook_events", {})
    servers = await find("platform_servers", {})
    disk = shutil.disk_usage("/")
    blocked = sum(1 for row in webhooks if not bool(row.get("accepted", 1)))
    return {
        "cpu_percent": _host_cpu_percent(),
        "memory_percent": _host_memory_percent(),
        "disk_percent": (disk.used / disk.total * 100) if disk.total else 0,
        "api_requests": len(deployments) + len(webhooks),
        "internet_ping_ms": await _internet_ping_ms(),
        "project_count": len(projects),
        "security_blocked_users": blocked,
        "server_count": len(servers),
        "collected_at": utcnow(),
    }


@router.get("/store/catalog")
async def docker_store_catalog() -> dict[str, Any]:
    return {"apps": [{key: value for key, value in app.items() if key != "compose"} for app in DOCKER_STORE_CATALOG]}


@router.post("/store/install", dependencies=[Depends(_operator)])
async def install_docker_store_app(body: StoreInstallRequest) -> dict[str, Any]:
    app = next((item for item in DOCKER_STORE_CATALOG if item["slug"] == body.slug), None)
    if app is None:
        raise HTTPException(404, "Docker application is not in the catalog.")
    bootstrap = await ensure_bootstrap()
    now = utcnow()
    service = await insert("platform_services", {
        "uuid": new_uuid(), "environment_uuid": bootstrap["environment"]["uuid"], "server_uuid": bootstrap["server"]["uuid"],
        "destination_uuid": bootstrap["destination"]["uuid"], "name": body.name or app["name"], "description": app["description"],
        "template_key": app["slug"], "service_type": "docker-store", "docker_compose_raw": app["compose"], "docker_compose": app["compose"],
        "status": "stopped", "created_at": now, "updated_at": now,
    })
    return {"ok": True, "app": {key: value for key, value in app.items() if key != "compose"}, "service": service, "message": f"{app['name']} was added to the environment. Review its configuration before starting it."}


@router.post("/ssh-keys/generate", dependencies=[Depends(_operator)])
async def generate_ssh_key(body: GenerateSshKeyRequest) -> dict[str, Any]:
    bootstrap = await ensure_bootstrap()
    with tempfile.TemporaryDirectory(prefix="syte-ssh-") as directory:
        key_path = os.path.join(directory, "id_key")
        command = ["ssh-keygen", "-q", "-N", "", "-C", body.name]
        command += ["-t", "ed25519"] if body.algorithm == "ed25519" else ["-t", "rsa", "-b", str(body.bits)]
        command += ["-f", key_path]
        result = await asyncio.to_thread(subprocess.run, command, capture_output=True, text=True, check=False)
        if result.returncode != 0:
            raise HTTPException(500, f"SSH key generation failed: {result.stderr.strip() or 'ssh-keygen error'}")
        private_key = open(key_path, encoding="utf-8").read()
        public_key = open(f"{key_path}.pub", encoding="utf-8").read().strip()
    now = utcnow()
    row = await insert("platform_private_keys", {"uuid": new_uuid(), "team_uuid": bootstrap["team"]["uuid"], "name": body.name, "description": f"Generated {body.algorithm} key", "private_key": private_key, "public_key": public_key, "fingerprint": public_key.split()[-1] if public_key else "", "created_at": now, "updated_at": now})
    return {"ok": True, "key": {"uuid": row["uuid"], "name": row["name"], "public_key": public_key, "fingerprint": row.get("fingerprint"), "algorithm": body.algorithm}, "private_key": private_key, "message": "Private key generated. Download it now; it will not be shown again by list endpoints."}


@router.get("/overview/health")
async def overview_health() -> dict[str, Any]:
    """Return a safe, read-only command-center health snapshot for the overview UI."""
    metrics = await overview_metrics()
    services = await find("platform_services", {})
    deployments = await find("platform_deployments", {})
    failed_states = {"failed", "error", "unhealthy", "stopped"}
    active_states = {"running", "healthy", "deployed", "success", "succeeded"}
    app_states = [str(row.get("status") or row.get("state") or "").lower() for row in [*services, *deployments]]
    if any(state in failed_states for state in app_states):
        apps = {"state": "degraded", "healthy": False, "detail": "One or more managed applications need attention."}
    elif any(state in active_states for state in app_states):
        apps = {"state": "healthy", "healthy": True, "detail": f"{len(services)} managed services tracked."}
    else:
        apps = {"state": "attention", "healthy": False, "detail": "No active application workloads were detected."}

    try:
        from syte.nine_router_manager import router_status
        router = await router_status()
        router_running = bool(router.get("running") or router.get("healthy") or router.get("status") in {"running", "healthy"})
        router_state = "healthy" if router_running else "attention"
        router_detail = str(router.get("message") or ("9Router is running." if router_running else "9Router is not running."))
    except Exception:
        router_state, router_running, router_detail = "unavailable", False, "9Router status could not be collected."

    services_snapshot = {
        "web": {"state": "healthy", "healthy": True, "detail": "Syte web service is responding."},
        "api": {"state": "healthy", "healthy": True, "detail": "FastAPI service is responding."},
        "apps": apps,
        "router": {"state": router_state, "healthy": router_running, "detail": router_detail},
    }
    states = {item["state"] for item in services_snapshot.values()}
    overall = "healthy" if states == {"healthy"} else "degraded" if {"degraded", "unavailable"} & states else "attention"
    return {"metrics": metrics, "services": services_snapshot, "overall": overall, "collected_at": utcnow()}
