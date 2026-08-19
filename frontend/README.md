# Syte Next.js Frontend

This directory contains the staged Next.js migration of Syte's operator UI. It intentionally keeps FastAPI as the system of record for authentication, deployments, managed databases, backups, 9Router, and agent APIs.

## Development

Start the FastAPI backend first on port `8787`, then run the Next.js UI:

```bash
cd frontend
pnpm install
SYTE_API_ORIGIN=http://127.0.0.1:8787 pnpm dev
```

The development server proxies `/api/*` requests to FastAPI. The restored primary navigation includes **Home**, **Agent**, **9Router**, and **Settings**, with generic platform routes using the existing `/api/platform/navigation/{page}` contracts.

## Migration boundary

The legacy static frontend remains available during migration. New React pages call the existing API surface directly; no platform data model or backend lifecycle behavior is duplicated in Next.js.
