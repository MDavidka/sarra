"""Coolify-parity PaaS layer for Syte.

This package adds the resource model and deployment engine that Syte was
missing compared to Coolify: multi-server management, a
project → environment → resource hierarchy, build packs, managed databases,
one-click services, a deployment queue with rollback, scoped environment
variables, persistent storage, scheduled tasks, backups, notifications and
inbound git webhooks.

Layering (mirrors the existing ``caddy_routes`` vs ``certificates`` split — pure
render/collect functions live apart from effectful apply functions so the pure
half stays trivially testable):

    types            value objects + enums, no I/O
    store            SQLite persistence (platform_* tables)
    build_packs      source tree -> Dockerfile  (pure)
    database_catalog managed database engine definitions (pure)
    service_catalog  one-click compose templates (pure)
    compose          compose rendering + magic env vars (pure)
    proxy            resource -> Caddy route blocks (pure)
    env_vars         scoped env resolution (pure) + store reads
    health           healthcheck config -> docker args (pure) + probes
    docker_engine    docker argv builders (pure) + effectful runners
    servers          server registry + SSH/local transport
    deployments      deployment queue + log streaming
    backups          database dumps + retention + S3
    scheduled_tasks  cron parsing + scheduler loop
    notifications    channel payload builders + dispatch
    git_sources      webhook signature verification + push/PR parsing
    service          orchestration (the pipeline that uses everything above)
    api              Coolify-compatible /api/v1 router
"""

from syte.platform.types import (
    BuildPack,
    ContainerStatus,
    DatabaseType,
    DeploymentStatus,
    GitProvider,
    NotificationEvent,
    ProxyType,
    RedirectType,
    ResourceType,
    ServerStatus,
    VariableScope,
)

__all__ = [
    "BuildPack",
    "ContainerStatus",
    "DatabaseType",
    "DeploymentStatus",
    "GitProvider",
    "NotificationEvent",
    "ProxyType",
    "RedirectType",
    "ResourceType",
    "ServerStatus",
    "VariableScope",
]
