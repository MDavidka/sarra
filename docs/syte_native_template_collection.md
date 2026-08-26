# Syte-Native Share It Template Collection

## Purpose

The Share It catalogue will expand with three additional internally hosted Node.js templates. They deliberately follow the visual grammar of Syte's own light workspace: restrained black typography, white or near-white surfaces, cyan state accents, compact operational labels, and responsive content hierarchy. They are not imported repositories and contain no third-party front-end runtime.

## Collection

| Template | Primary view | Visual character | Real project-bound behavior |
| --- | --- | --- | --- |
| Deployment Brief | Release readiness | Editorial overview with an operational side panel | Reads the bound project's scoped running status, domain, and environment count. |
| Project Compass | Project orientation | Modular light cards and a prominent project identity region | Reads the bound project's scoped identity, endpoint, status, and configuration count. |
| Service Watch | Operational pulse | Compact command-style monitor with high-contrast state treatment | Reads the bound project's scoped health state and runtime metadata. |

## Shared deployment and security contract

Each source directory will remain below `syte/share_templates`, ships a Node.js 20 Dockerfile, and is copied only into the owned Syte workspace during provisioning. The platform injects the instance identifier, scoped instance key, and internal platform base as server-only runtime variables.

The generated server exposes a public `/api/health` response and a browser-safe `/api/overview` response. Its only platform request is server-to-server, addressed to `/api/share/instances/{instanceId}/overview` with the scoped credential. Browser JavaScript communicates exclusively with the generated service's own browser-safe endpoint. It never receives a Syte key, raw environment variables, or privileged project controls.

## Validation standard

Every template must pass Node syntax validation, catalogue regression coverage, and an actual Docker deployment. A representative template must also be inspected at both desktop and 390-pixel mobile widths after the real scoped overview is live.
