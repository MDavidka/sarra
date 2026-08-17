# Coolify Gap Investigation Notes

## Coolify observations

The Coolify GitHub README describes a self-hostable PaaS that manages servers, applications, and databases over SSH, with no vendor lock-in. The repository's current public description states support for static sites, databases, full-stack applications, and 280+ one-click services. The repository layout includes application code, database models, routes, Docker/bootstrap scripts, templates, OpenAPI files, and tests.

Coolify's Applications documentation lists deployment sources and types: public Git repositories, private repositories through a GitHub App or deploy key, Dockerfile, Docker Compose, and Docker images. It explains that applications run as isolated Docker containers. The page also exposes configuration concepts for commands, base/public directories, exposed and mapped ports, static sites, HTTPS, automatic deploys, preview deployments, URL templates, environment variables, persistent storage, health checks, rollbacks, resource limits, build servers, and build packs including Nixpacks, Static, Dockerfile, and Docker Compose.

## Sarra observations

The repository is Syte, a Python/FastAPI deployment service with SQLite persistence and a static JavaScript dashboard. It already supports per-project workspaces, shell and Docker deployment modules, start/stop/update/delete/deploy endpoints, logs and log streaming, domains/Caddy integration, previews, agent APIs, settings, and API tokens. Existing project fields include git URL, branch, port, domain, start command, environment variables, deploy type, Dockerfile path, preview metadata, and agent metadata.

The README currently documents a smaller PaaS surface: deploy from git or an empty workspace, start commands and environment variables, pull-and-restart, logs, and a dashboard. There is no obvious documented first-class support yet for a Coolify-like resource model covering Docker image/Compose deployments, build packs, health checks, rollbacks, persistent volumes, deployment history, automatic Git-triggered deployments, resource limits, or a dedicated server/resource management dashboard.

## Working implementation direction

Before coding, inspect the existing static dashboard and deployment/database code in detail. Prefer additive, backward-compatible APIs and UI. Implement a focused first slice with a deployment-job/history model, richer deployment configuration, lifecycle controls, health/status aggregation, and a resource-management dashboard while preserving existing endpoints and tests.
