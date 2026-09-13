# Syte Platform API Reference (v2.4.0)

This document contains the complete specification of all **113 backend API endpoints** available in the Syte deployment platform.

## Table of Contents
- [System & Health](#system--health) (4 endpoints)
- [Notifications](#notifications) (7 endpoints)
- [Auth & Operator](#auth--operator) (13 endpoints)
- [Settings & GitHub](#settings--github) (10 endpoints)
- [SSL & Certificates](#ssl--certificates) (5 endpoints)
- [Projects & Lifecycle](#projects--lifecycle) (15 endpoints)
- [Builds & Deployments](#builds--deployments) (11 endpoints)
- [Redirects & Routing](#redirects--routing) (9 endpoints)
- [Analytics & Telemetry](#analytics--telemetry) (6 endpoints)
- [Release & Previews](#release--previews) (17 endpoints)
- [Git & Workspace Files](#git--workspace-files) (16 endpoints)

---

## System & Health

### `GET` /api/health
**Health check** — Check API availability and core system uptime status.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/health
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | Current service status ("ok"). |
| `version` | `string` | Running Syte release version tag. |
| `timestamp` | `string` | ISO-8601 server timestamp. |

#### Example Response (JSON)
```json
{
  "status": "ok",
  "version": "2.4.0",
  "timestamp": "2026-09-13T12:00:00Z"
}
```

---

### `GET` /api/system
**System hardware metrics** — Retrieve real-time host VM CPU, memory, disk usage, and OS kernel information.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/system \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `cpu_percent` | `number` | Current CPU utilization percentage across all cores. |
| `memory` | `object` | RAM usage metrics in bytes (total, used, free, percent). |
| `disk` | `object` | Root filesystem storage usage stats (total, used, free). |
| `platform` | `string` | Host operating system and kernel version string. |

#### Example Response (JSON)
```json
{
  "cpu_percent": 14.2,
  "memory": {
    "total": 8589934592,
    "used": 2810183680,
    "free": 5779750912,
    "percent": 32.7
  },
  "disk": {
    "total": 53687091200,
    "used": 12884901888,
    "free": 40802189312,
    "percent": 24.0
  },
  "platform": "Linux 6.8.0-amd64"
}
```

---

### `GET` /api/system/update-info
**Check release updates** — Query upstream GitHub repository for new release versions and changelog notes.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/system/update-info \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `current_version` | `string` | Currently deployed Syte platform version. |
| `latest_version` | `string` | Latest available version tag on GitHub. |
| `update_available` | `boolean` | Whether an upgrade can be triggered. |
| `release_notes` | `string` | Changelog and release notes markdown. |

#### Example Response (JSON)
```json
{
  "current_version": "2.4.0",
  "latest_version": "2.4.1",
  "update_available": true,
  "release_notes": "Added real-time SSE streaming logs and automated certificate renewal."
}
```

---

### `POST` /api/system/update
**Trigger platform self-update** — Initiate background git pull, dependency install, and systemd service reload.

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/system/update \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | Update operation status ("in_progress"). |
| `target_version` | `string` | Target version being installed. |

#### Example Response (JSON)
```json
{
  "status": "in_progress",
  "target_version": "2.4.1",
  "message": "Self-update process spawned in background."
}
```

---

## Notifications

### `GET` /api/notifications/settings
**Get notification settings** — Fetch alert configuration including webhook endpoints, Discord/Slack hooks, and email alerts.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/notifications/settings \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `webhook_url` | `string` | HTTP webhook URL for dispatching JSON alerts. |
| `discord_webhook` | `string` | Discord channel incoming webhook URL. |
| `slack_webhook` | `string` | Slack incoming webhook URL. |
| `notify_on_deploy` | `boolean` | Send alert on successful deployment. |
| `notify_on_fail` | `boolean` | Send high-priority alert on build failure. |

#### Example Response (JSON)
```json
{
  "webhook_url": "https://hooks.example.com/alerts",
  "discord_webhook": "",
  "slack_webhook": "",
  "notify_on_deploy": true,
  "notify_on_fail": true
}
```

---

### `PUT` /api/notifications/settings
**Update notification settings** — Save webhook destinations and notification trigger policies.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `webhook_url` | `string` | No | HTTP webhook endpoint. |
| `discord_webhook` | `string` | No | Discord incoming webhook URL. |
| `slack_webhook` | `string` | No | Slack incoming webhook URL. |
| `notify_on_deploy` | `boolean` | No | Enable deployment success alerts. |
| `notify_on_fail` | `boolean` | No | Enable build failure alerts. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/notifications/settings \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"notify_on_deploy": true, "notify_on_fail": true}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | Operation confirmation ("saved"). |
| `settings` | `object` | Updated notification settings object. |

#### Example Response (JSON)
```json
{
  "status": "saved",
  "settings": {
    "notify_on_deploy": true,
    "notify_on_fail": true
  }
}
```

---

### `GET` /api/notifications
**List notifications** — Retrieve in-app notifications and alert history with read/unread flags.

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `limit` | `integer` | No | Maximum notifications to return (default: 50). |
| `unread_only` | `boolean` | No | Filter by unread notifications only. |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/notifications?unread_only=false" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `items` | `array` | Array of notification objects. |
| `unread_count` | `integer` | Total count of unread notifications. |

#### Example Response (JSON)
```json
{
  "items": [
    {
      "id": "ntf_01",
      "title": "Build Completed",
      "message": "Project syte-docs deployed successfully to production.",
      "level": "info",
      "read": false,
      "created_at": "2026-09-13T11:45:00Z"
    }
  ],
  "unread_count": 1
}
```

---

### `POST` /api/notifications/read
**Mark notifications read** — Mark one or all notifications as read to clear badge counts.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `notification_ids` | `array` | No | List of IDs to mark as read, or empty to mark all. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/notifications/read \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"notification_ids": ["ntf_01"]}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "success" |
| `marked_count` | `integer` | Number of notifications updated. |

#### Example Response (JSON)
```json
{
  "status": "success",
  "marked_count": 1
}
```

---

### `GET` /api/notifications/push/vapid-public-key
**Get VAPID public key** — Retrieve public VAPID key used for client Web Push subscription registration.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/notifications/push/vapid-public-key \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `public_key` | `string` | Base64-encoded VAPID public key string. |

#### Example Response (JSON)
```json
{
  "public_key": "BEl62iUYgUivxIkv69yViEuiBIa..."
}
```

---

### `POST` /api/notifications/push-subscriptions
**Register push subscription** — Save a browser ServiceWorker Web Push subscription payload for native push notifications.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `endpoint` | `string` | **Yes** | Browser push service endpoint URL. |
| `keys` | `object` | **Yes** | Encryption keys object containing `p256dh` and `auth` strings. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/notifications/push-subscriptions \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"endpoint": "https://fcm.googleapis.com/fcm/send/...", "keys": {"p256dh": "...", "auth": "..."}}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "subscribed" |

#### Example Response (JSON)
```json
{
  "status": "subscribed"
}
```

---

### `POST` /api/notifications/test
**Send test notification** — Trigger immediate test notification across all enabled channels (Web Push, Webhook, Discord).

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/notifications/test \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "dispatched" |
| `channels` | `array` | List of notification channels reached. |

#### Example Response (JSON)
```json
{
  "status": "dispatched",
  "channels": [
    "in_app",
    "web_push"
  ]
}
```

---

## Auth & Operator

### `GET` /api/auth/setup
**Check setup status** — Check whether root administrator account has already been initialized.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/auth/setup
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `setup_required` | `boolean` | True if no admin account exists yet. |

#### Example Response (JSON)
```json
{
  "setup_required": false
}
```

---

### `POST` /api/auth/setup
**Initialize administrator** — Create primary administrator username and master password during initial deployment.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `username` | `string` | **Yes** | Admin username (minimum 3 characters). |
| `password` | `string` | **Yes** | Secure password (minimum 8 characters). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/auth/setup \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "SuperSecretPassword123"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "initialized" |
| `user` | `object` | Created user account details. |

#### Example Response (JSON)
```json
{
  "status": "initialized",
  "user": {
    "id": "usr_01",
    "username": "admin",
    "role": "owner"
  }
}
```

---

### `POST` /api/auth/login
**User login** — Authenticate user credentials and issue session cookie or bearer token.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `username` | `string` | **Yes** | Registered username. |
| `password` | `string` | **Yes** | Account password. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "SuperSecretPassword123"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "authenticated" |
| `token` | `string` | JWT session token. |
| `user` | `object` | User profile object. |

#### Example Response (JSON)
```json
{
  "status": "authenticated",
  "token": "eyJhbGciOiJIUzI1NiIsIn...",
  "user": {
    "username": "admin",
    "role": "owner"
  }
}
```

---

### `GET` /api/auth/session
**Inspect active session** — Validate session token or cookie and return authenticated user identity.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/auth/session \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `authenticated` | `boolean` | True if session is active and valid. |
| `user` | `object` | Current authenticated user details. |

#### Example Response (JSON)
```json
{
  "authenticated": true,
  "user": {
    "id": "usr_01",
    "username": "admin",
    "role": "owner"
  }
}
```

---

### `DELETE` /api/auth/session
**Logout session** — Invalidate current session token and clear authentication cookie.

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/auth/session \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "logged_out" |

#### Example Response (JSON)
```json
{
  "status": "logged_out"
}
```

---

### `GET` /api/auth/profile
**Get user profile** — Retrieve user profile, contact info, and preferences.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/auth/profile \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `username` | `string` | Current username. |
| `email` | `string` | Registered email address. |
| `theme` | `string` | UI theme preference ("dark" / "light"). |

#### Example Response (JSON)
```json
{
  "username": "admin",
  "email": "admin@example.com",
  "theme": "dark"
}
```

---

### `PUT` /api/auth/profile
**Update user profile** — Update user password, email, and display preferences.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `email` | `string` | No | New email address. |
| `current_password` | `string` | No | Current password for verification. |
| `new_password` | `string` | No | New password to set. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/auth/profile \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"email": "ops@example.com"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |

#### Example Response (JSON)
```json
{
  "status": "updated"
}
```

---

### `GET` /api/operator/session
**Get operator session** — Check if an elevated maintenance operator session is currently active.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/operator/session \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `operator_active` | `boolean` | True if maintenance mode operator is enabled. |
| `expires_at` | `string` | ISO-8601 expiration timestamp. |

#### Example Response (JSON)
```json
{
  "operator_active": false,
  "expires_at": null
}
```

---

### `POST` /api/operator/session
**Start operator session** — Elevate current session with operator secret to bypass project quotas and access root controls.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `operator_key` | `string` | **Yes** | Host operator access secret. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/operator/session \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"operator_key": "op_sec_999"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "operator_granted" |

#### Example Response (JSON)
```json
{
  "status": "operator_granted"
}
```

---

### `DELETE` /api/operator/session
**End operator session** — Revoke elevated operator privileges and return to normal permission scope.

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/operator/session \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "operator_revoked" |

#### Example Response (JSON)
```json
{
  "status": "operator_revoked"
}
```

---

### `GET` /api/tokens
**List API tokens** — List all active programmatic API tokens with permissions and last used timestamps.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/tokens \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `tokens` | `array` | Array of API token metadata objects. |

#### Example Response (JSON)
```json
{
  "tokens": [
    {
      "id": "tok_9918",
      "name": "CI/CD Deployment Token",
      "prefix": "syt_live_...",
      "created_at": "2026-09-01T08:00:00Z",
      "last_used": "2026-09-13T10:15:20Z"
    }
  ]
}
```

---

### `POST` /api/tokens
**Create API token** — Generate a new persistent API token for CI/CD pipelines and external integrations.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | **Yes** | Descriptive token identifier (e.g. GitHub Actions). |
| `expires_in_days` | `integer` | No | Days until expiration (0 for never). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/tokens \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "GitHub Actions CI", "expires_in_days": 90}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `token` | `string` | Full plaintext secret token (only displayed once). |
| `token_id` | `string` | Token ID for future revocation. |

#### Example Response (JSON)
```json
{
  "token_id": "tok_9919",
  "token": "syt_live_a89f923b7c84192d1948",
  "name": "GitHub Actions CI"
}
```

---

### `DELETE` /api/tokens/{token_id}
**Revoke API token** — Immediately revoke and permanently delete an API token.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `token_id` | `string` | **Yes** | Unique identifier of the token to revoke. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/tokens/tok_9918 \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "revoked" |

#### Example Response (JSON)
```json
{
  "status": "revoked"
}
```

---

## Settings & GitHub

### `GET` /api/settings
**Get platform settings** — Retrieve global system settings, networking defaults, and domain configuration.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/settings \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `default_domain` | `string` | Root domain for automatic subdomain routing. |
| `telemetry_enabled` | `boolean` | Whether telemetry data collection is enabled. |
| `max_concurrent_builds` | `integer` | Max concurrent container builds. |

#### Example Response (JSON)
```json
{
  "default_domain": "sycord.site",
  "telemetry_enabled": true,
  "max_concurrent_builds": 4
}
```

---

### `PUT` /api/settings
**Save platform settings** — Update global system settings and networking defaults.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `default_domain` | `string` | No | Apex domain for routing. |
| `max_concurrent_builds` | `integer` | No | Build concurrency limit. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/settings \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"max_concurrent_builds": 4}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |

#### Example Response (JSON)
```json
{
  "status": "saved"
}
```

---

### `GET` /api/settings/cache
**Get cache metrics** — Inspect disk usage by build caches, docker layers, and temporary file artifacts.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/settings/cache \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `build_cache_size` | `integer` | Size of npm/pip/cargo build caches in bytes. |
| `docker_cache_size` | `integer` | Size of dangling container image layers. |
| `temp_files_size` | `integer` | Size of temp staging directories. |

#### Example Response (JSON)
```json
{
  "build_cache_size": 2147483648,
  "docker_cache_size": 5368709120,
  "temp_files_size": 268435456
}
```

---

### `POST` /api/settings/cache/clear
**Clear system cache** — Purge build caches, temporary zip extractions, and unused Docker layers to free disk space.

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/settings/cache/clear \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `freed_bytes` | `integer` | Total disk space recovered in bytes. |
| `status` | `string` | "cleared" |

#### Example Response (JSON)
```json
{
  "status": "cleared",
  "freed_bytes": 7784628224
}
```

---

### `GET` /api/settings/github
**Get GitHub App config** — Retrieve configured GitHub OAuth Client ID, App ID, and installation status.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/settings/github \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `client_id` | `string` | GitHub OAuth Client ID. |
| `is_configured` | `boolean` | True if Client Secret is securely stored. |

#### Example Response (JSON)
```json
{
  "client_id": "Iv1.8941829abc",
  "is_configured": true
}
```

---

### `PUT` /api/settings/github
**Update GitHub App config** — Save GitHub OAuth application credentials for repository imports and webhook triggers.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `client_id` | `string` | **Yes** | GitHub OAuth Client ID. |
| `client_secret` | `string` | **Yes** | GitHub OAuth Client Secret. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/settings/github \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "Iv1.8941829abc", "client_secret": "sec_gh_8921"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |

#### Example Response (JSON)
```json
{
  "status": "saved"
}
```

---

### `POST` /api/settings/github/test
**Test GitHub credentials** — Validate GitHub OAuth credentials against GitHub REST API.

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/settings/github/test \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `valid` | `boolean` | True if credentials successfully authenticated with GitHub. |

#### Example Response (JSON)
```json
{
  "valid": true,
  "message": "Successfully authenticated with GitHub API."
}
```

---

### `GET` /api/github/status
**Check GitHub link status** — Check if active user session is linked with a GitHub account.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/github/status \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `connected` | `boolean` | True if GitHub OAuth token is valid. |
| `github_username` | `string` | Linked GitHub account handle. |

#### Example Response (JSON)
```json
{
  "connected": true,
  "github_username": "octocat"
}
```

---

### `GET` /api/github/pulls
**List project pull requests** — Fetch open pull requests from linked GitHub repository for preview environment generation.

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `repo` | `string` | **Yes** | Full repository name (e.g. "owner/repo"). |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/github/pulls?repo=MDavidka/sarra" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `pulls` | `array` | List of open PR objects with branch info. |

#### Example Response (JSON)
```json
{
  "pulls": [
    {
      "number": 515,
      "title": "feat: mobile header and sidebar accuracy",
      "author": "MDavidka",
      "head_ref": "feat/mobile-header-and-sidebar-accuracy",
      "state": "open"
    }
  ]
}
```

---

### `POST` /api/github/pulls/{number}/merge
**Merge GitHub pull request** — Trigger automated merge of approved pull request into target production branch.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `number` | `integer` | **Yes** | Pull request number. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `merge_method` | `string` | No | "merge", "squash", or "rebase" (default: squash). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/github/pulls/515/merge \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"merge_method": "squash"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `merged` | `boolean` | True if merge succeeded. |
| `sha` | `string` | Commit SHA of the merge commit. |

#### Example Response (JSON)
```json
{
  "merged": true,
  "sha": "4a8c901e892b491a"
}
```

---

## SSL & Certificates

### `GET` /api/ssl
**Global SSL status** — Check status of ACME Let's Encrypt certificates and TLS expiration dates.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/ssl \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `certificates` | `array` | List of active TLS certificates and domains. |

#### Example Response (JSON)
```json
{
  "certificates": [
    {
      "domain": "sycord.site",
      "issuer": "Let's Encrypt",
      "valid_until": "2026-12-12T00:00:00Z",
      "auto_renew": true
    }
  ]
}
```

---

### `POST` /api/ssl/resolve
**Resolve DNS records** — Perform live DNS A and CNAME record resolution to test propagation before issuing SSL.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `domain` | `string` | **Yes** | Domain name to resolve. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/ssl/resolve \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "app.sycord.site"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `resolved` | `boolean` | True if domain points to this host IP. |
| `ip_addresses` | `array` | Resolved A/AAAA IP addresses. |

#### Example Response (JSON)
```json
{
  "resolved": true,
  "ip_addresses": [
    "185.199.108.153"
  ]
}
```

---

### `POST` /api/ssl/projects/{project_id}/custom-tls
**Upload custom TLS certificate** — Upload custom SSL certificate and private key for enterprise domain hosting.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `certificate_pem` | `string` | **Yes** | PEM-formatted certificate chain. |
| `private_key_pem` | `string` | **Yes** | PEM-formatted private key. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/ssl/projects/proj_94821a/custom-tls \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"certificate_pem": "-----BEGIN CERTIFICATE...", "private_key_pem": "-----BEGIN RSA PRIVATE KEY..."}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "installed" |
| `valid_until` | `string` | Expiration date of uploaded cert. |

#### Example Response (JSON)
```json
{
  "status": "installed",
  "valid_until": "2027-01-01T00:00:00Z"
}
```

---

### `GET` /api/certificates/guide
**Get certificate guide** — Get required DNS CNAME/A record targets and automated ACME issuance guidance.

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `domain` | `string` | **Yes** | Target custom domain name. |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/certificates/guide?domain=app.example.com" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `cname_target` | `string` | CNAME target record. |
| `a_record` | `string` | Host public IPv4 address. |

#### Example Response (JSON)
```json
{
  "cname_target": "cname.sycord.site",
  "a_record": "185.199.108.153",
  "instructions": "Create a CNAME record pointing app.example.com to cname.sycord.site"
}
```

---

### `POST` /api/certificates/issue
**Issue Let's Encrypt SSL** — Execute automated HTTP-01 or DNS-01 ACME challenge to issue Let's Encrypt SSL certificate.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `domain` | `string` | **Yes** | Fully-qualified domain name. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/certificates/issue \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "app.example.com"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "issued" |
| `domain` | `string` | Provisioned domain. |
| `expires_at` | `string` | Expiration date (90 days). |

#### Example Response (JSON)
```json
{
  "status": "issued",
  "domain": "app.example.com",
  "expires_at": "2026-12-13T12:00:00Z"
}
```

---

## Projects & Lifecycle

### `GET` /api/projects
**List all projects** — Retrieve an array of all hosted web applications and backend services.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `projects` | `array` | List of project summary objects. |

#### Example Response (JSON)
```json
{
  "projects": [
    {
      "id": "proj_94821a",
      "name": "sarra-docs",
      "status": "running",
      "port": 3000,
      "domain": "docs.sycord.site"
    }
  ]
}
```

---

### `POST` /api/projects
**Create new project** — Create and initialize a new project workspace directory and configuration.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | **Yes** | Project slug name. |
| `framework` | `string` | No | Framework type (e.g. "nextjs", "fastapi", "static"). |
| `port` | `integer` | No | Container internal port (default: 3000). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"name": "my-api", "framework": "fastapi", "port": 8000}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | `string` | Assigned unique project identifier. |
| `name` | `string` | Project name. |
| `status` | `string` | "initialized" |

#### Example Response (JSON)
```json
{
  "id": "proj_8819ab",
  "name": "my-api",
  "status": "initialized",
  "port": 8000
}
```

---

### `GET` /api/projects/{project_id}
**Get project details** — Retrieve complete runtime metadata, environment keys, domains, and health status for a project.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | `string` | Project ID. |
| `name` | `string` | Project name. |
| `status` | `string` | "running", "stopped", or "building". |
| `port` | `integer` | Container listening port. |
| `domain` | `string` | Bound custom domain. |
| `framework` | `string` | Detected runtime framework. |

#### Example Response (JSON)
```json
{
  "id": "proj_94821a",
  "name": "sarra-docs",
  "status": "running",
  "port": 3000,
  "domain": "docs.sycord.site",
  "framework": "nextjs"
}
```

---

### `PUT` /api/projects/{project_id}
**Update project settings** — Modify project configuration including assigned port, framework, and build scripts.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `name` | `string` | No | New project name. |
| `port` | `integer` | No | Updated internal container port. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"port": 8080}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |

#### Example Response (JSON)
```json
{
  "status": "updated"
}
```

---

### `DELETE` /api/projects/{project_id}
**Delete project** — Permanently stop container, wipe workspace directory, remove domains, and delete project database record.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "deleted" |

#### Example Response (JSON)
```json
{
  "status": "deleted"
}
```

---

### `POST` /api/projects/{project_id}/start
**Start project container** — Start background systemd/docker container process for project.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/start \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "started" |

#### Example Response (JSON)
```json
{
  "status": "started"
}
```

---

### `POST` /api/projects/{project_id}/stop
**Stop project container** — Gracefully terminate project container and halt process execution.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/stop \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "stopped" |

#### Example Response (JSON)
```json
{
  "status": "stopped"
}
```

---

### `POST` /api/projects/{project_id}/domain
**Bind custom domain** — Bind custom apex or subdomain with automatic SSL certificate provisioning and reverse proxy routing.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `domain` | `string` | **Yes** | Fully qualified domain name (e.g. app.example.com). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/domain \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"domain": "docs.sycord.site"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "bound" |
| `domain` | `string` | Configured domain name. |
| `ssl_status` | `string` | "active" or "pending_dns". |

#### Example Response (JSON)
```json
{
  "status": "bound",
  "domain": "docs.sycord.site",
  "ssl_status": "active"
}
```

---

### `DELETE` /api/projects/{project_id}/domain
**Unbind custom domain** — Remove custom domain binding and restore default platform subdomain routing.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/domain \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "unbound" |

#### Example Response (JSON)
```json
{
  "status": "unbound"
}
```

---

### `PUT` /api/projects/{project_id}/environment
**Upsert environment variables** — Securely set or update environment variables and secrets injected into runtime container.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `variables` | `object` | **Yes** | Key-value dictionary of environment variables. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/environment \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"variables": {"DATABASE_URL": "postgres://...", "NODE_ENV": "production"}}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |
| `keys` | `array` | List of configured variable names. |

#### Example Response (JSON)
```json
{
  "status": "saved",
  "keys": [
    "DATABASE_URL",
    "NODE_ENV"
  ]
}
```

---

### `DELETE` /api/projects/{project_id}/environment/{key}
**Delete environment variable** — Remove a specific environment variable from project configuration.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `key` | `string` | **Yes** | Variable name to delete. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/environment/DATABASE_URL \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "deleted" |

#### Example Response (JSON)
```json
{
  "status": "deleted"
}
```

---

### `GET` /api/projects/{project_id}/health
**Check project health probe** — Perform direct HTTP health probe on project listener port to check readiness.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/health \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `healthy` | `boolean` | True if HTTP 200/300 was received from local port. |
| `response_time_ms` | `number` | Probe response latency in milliseconds. |

#### Example Response (JSON)
```json
{
  "healthy": true,
  "response_time_ms": 12.4
}
```

---

### `PUT` /api/projects/{project_id}/deployment-config
**Update deployment config** — Configure build command, start script, install command, and root output directory.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `build_command` | `string` | No | Build command (e.g. "npm run build"). |
| `start_command` | `string` | No | Start command (e.g. "npm run start"). |
| `install_command` | `string` | No | Install command (e.g. "npm install"). |
| `output_directory` | `string` | No | Static output directory (e.g. "dist"). |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/deployment-config \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"build_command": "npm run build", "start_command": "npm run start"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |

#### Example Response (JSON)
```json
{
  "status": "updated"
}
```

---

### `POST` /api/projects/{project_id}/analyze
**Analyze project source** — Inspect workspace files to auto-detect framework, package manager, and required start commands.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/analyze \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `framework` | `string` | Detected framework (e.g. "nextjs", "fastapi", "astro"). |
| `package_manager` | `string` | Detected tool ("npm", "yarn", "pnpm", "pip"). |
| `suggested_port` | `integer` | Recommended default listening port. |

#### Example Response (JSON)
```json
{
  "framework": "nextjs",
  "package_manager": "pnpm",
  "suggested_port": 3000,
  "detected_scripts": [
    "build",
    "start",
    "dev"
  ]
}
```

---

### `POST` /api/projects/{project_id}/deploy-detected
**Deploy detected framework** — Automatically apply detected build configuration and trigger initial deployment pipeline.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deploy-detected \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `build_id` | `string` | Triggered build run ID. |
| `status` | `string` | "queued" |

#### Example Response (JSON)
```json
{
  "build_id": "bld_77491",
  "status": "queued"
}
```

---

## Builds & Deployments

### `GET` /api/projects/{project_id}/builds
**List project builds** — Retrieve historical build records, git commit SHAs, build duration, and pass/fail statuses.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `limit` | `integer` | No | Number of records to return (default: 20). |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `builds` | `array` | Array of build execution objects. |

#### Example Response (JSON)
```json
{
  "builds": [
    {
      "id": "bld_77491",
      "status": "success",
      "duration_seconds": 38,
      "commit_sha": "a19f201",
      "created_at": "2026-09-13T10:00:00Z"
    }
  ]
}
```

---

### `GET` /api/projects/{project_id}/builds/track
**Track active build** — Poll or track progress of currently executing build step and status.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds/track \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `active` | `boolean` | True if build is currently running. |
| `step` | `string` | Current build step ("installing", "building", "starting"). |
| `elapsed_seconds` | `integer` | Seconds elapsed since build trigger. |

#### Example Response (JSON)
```json
{
  "active": true,
  "step": "building",
  "elapsed_seconds": 18
}
```

---

### `POST` /api/projects/{project_id}/builds/trigger
**Trigger new build** — Enqueue an immediate new build and deployment execution for the project.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/builds/trigger \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `build_id` | `string` | Unique build run ID. |
| `status` | `string` | "queued" |

#### Example Response (JSON)
```json
{
  "build_id": "bld_77492",
  "status": "queued"
}
```

---

### `GET` /api/projects/{project_id}/builds/{build_id}/logs
**Get build run logs** — Retrieve complete build execution log output for a specific build ID.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `build_id` | `string` | **Yes** | Build run ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds/bld_77491/logs \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `logs` | `string` | Full stdout/stderr build text output. |

#### Example Response (JSON)
```json
{
  "logs": "[build] Installing dependencies...\n[build] Completed in 8.2s\n[build] Next.js 14 compiled successfully.\n[build] Artifact ready."
}
```

---

### `GET` /api/projects/{project_id}/deployments/{build_id}/logs
**Get deployment container logs** — Retrieve runtime stdout/stderr log output from container during specific deployment execution.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `build_id` | `string` | **Yes** | Deployment run ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/deployments/bld_77491/logs \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `logs` | `string` | Container runtime logs. |

#### Example Response (JSON)
```json
{
  "logs": "Ready in 420ms on port 3000.\nGET / 200 12ms"
}
```

---

### `POST` /api/projects/{project_id}/deploy
**Issue immediate deploy** — Trigger immediate atomic production deployment without rebuild if artifact is fresh.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deploy \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `deployment_id` | `string` | Deployment run ID. |
| `status` | `string` | "deployed" |

#### Example Response (JSON)
```json
{
  "deployment_id": "dep_19482",
  "status": "deployed"
}
```

---

### `GET` /api/projects/{project_id}/deployments
**List deployment revisions** — Retrieve deployment history list with commit tags, active production pointers, and rollback targets.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/deployments \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `deployments` | `array` | Array of deployment snapshots. |

#### Example Response (JSON)
```json
{
  "deployments": [
    {
      "id": "dep_19482",
      "is_current": true,
      "commit_sha": "a19f201",
      "created_at": "2026-09-13T10:05:00Z"
    },
    {
      "id": "dep_19480",
      "is_current": false,
      "commit_sha": "98e411b",
      "created_at": "2026-09-12T18:30:00Z"
    }
  ]
}
```

---

### `POST` /api/projects/{project_id}/deployments/{run_id}/rollback
**Rollback deployment** — Instantly switch active production traffic back to a previous healthy deployment snapshot.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `run_id` | `string` | **Yes** | Target deployment revision ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deployments/dep_19480/rollback \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "rolled_back" |
| `active_deployment_id` | `string` | ID of newly activated revision. |

#### Example Response (JSON)
```json
{
  "status": "rolled_back",
  "active_deployment_id": "dep_19480"
}
```

---

### `GET` /api/projects/{project_id}/logs
**Get container logs** — Fetch recent stdout and stderr lines from the running project container.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `lines` | `integer` | No | Number of tail lines to retrieve (default: 200). |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/logs?lines=100" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `logs` | `string` | Captured application log buffer. |

#### Example Response (JSON)
```json
{
  "logs": "2026-09-13T12:00:01Z [INFO] Application listening on 0.0.0.0:3000\n2026-09-13T12:01:23Z [INFO] GET /api/v1/users 200 OK"
}
```

---

### `GET` /api/projects/{project_id}/logs/stream
**Stream container logs (SSE)** — Open real-time Server-Sent Events (SSE) connection to stream live container logs.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -N -X GET https://sycord.site:8787/api/projects/proj_94821a/logs/stream \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `data` | `string` | Streaming log line chunk formatted as SSE message. |

#### Example Response (JSON)
```json
data: {"line": "[server] Request handled in 4ms"}

data: {"line": "[server] Cache hit for /static/bundle.js"}


```

---

### `POST` /api/projects/{project_id}/update
**Pull Git update and rebuild** — Fetch latest commits from linked Git branch, reinstall dependencies, and restart project.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/update \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |
| `commit_sha` | `string` | New head commit SHA. |

#### Example Response (JSON)
```json
{
  "status": "updated",
  "commit_sha": "d98174f"
}
```

---

## Redirects & Routing

### `GET` /api/projects/{project_id}/redirects
**List redirect rules** — Retrieve all configured HTTP redirection and reverse proxy URL rewrite rules.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/redirects \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `redirects` | `array` | Array of redirect rule objects. |

#### Example Response (JSON)
```json
{
  "redirects": [
    {
      "id": "red_01",
      "source_path": "/old-docs/:path*",
      "target_url": "/docs/:path*",
      "status_code": 301,
      "enabled": true
    }
  ]
}
```

---

### `POST` /api/projects/{project_id}/redirects
**Create redirect rule** — Add a new URL redirect or proxy rewrite rule with regex pattern matching.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `source_path` | `string` | **Yes** | Source URL pattern (e.g. "/blog/:slug"). |
| `target_url` | `string` | **Yes** | Target destination URL or path. |
| `status_code` | `integer` | No | HTTP status code (301, 302, 307, 308; default: 301). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"source_path": "/legacy", "target_url": "/new-v2", "status_code": 301}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `id` | `string` | Assigned redirect rule ID. |
| `status` | `string` | "created" |

#### Example Response (JSON)
```json
{
  "id": "red_02",
  "status": "created"
}
```

---

### `PUT` /api/projects/{project_id}/redirects/{redirect_id}
**Update redirect rule** — Update source path, destination target, or status code of an existing redirect rule.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `redirect_id` | `string` | **Yes** | Redirect rule ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `source_path` | `string` | **Yes** | Updated source pattern. |
| `target_url` | `string` | **Yes** | Updated target URL. |
| `status_code` | `integer` | No | HTTP status code. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"source_path": "/old-docs", "target_url": "/docs", "status_code": 308}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |

#### Example Response (JSON)
```json
{
  "status": "updated"
}
```

---

### `PATCH` /api/projects/{project_id}/redirects/{redirect_id}
**Toggle redirect rule status** — Enable or disable a redirect rule without modifying its configuration.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `redirect_id` | `string` | **Yes** | Redirect rule ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `enabled` | `boolean` | **Yes** | True to activate, false to pause rule. |

#### Example Request (cURL)
```bash
curl -X PATCH https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"enabled": true}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "updated" |

#### Example Response (JSON)
```json
{
  "status": "updated",
  "enabled": true
}
```

---

### `DELETE` /api/projects/{project_id}/redirects/{redirect_id}
**Delete redirect rule** — Remove a redirect rule from the edge proxy router.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `redirect_id` | `string` | **Yes** | Redirect rule ID. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "deleted" |

#### Example Response (JSON)
```json
{
  "status": "deleted"
}
```

---

### `POST` /api/projects/{project_id}/redirects/reorder
**Reorder redirect rules** — Set the sequential evaluation priority order for routing rules.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `ordered_ids` | `array` | **Yes** | Array of redirect rule IDs in desired priority order. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/reorder \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"ordered_ids": ["red_02", "red_01"]}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "reordered" |

#### Example Response (JSON)
```json
{
  "status": "reordered"
}
```

---

### `POST` /api/projects/{project_id}/redirects/bulk
**Bulk update redirect rules** — Add or replace multiple redirect rules in a single atomic transaction.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `rules` | `array` | **Yes** | Array of redirect rule objects. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/bulk \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"rules": [{"source_path": "/a", "target_url": "/b", "status_code": 301}]}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |
| `count` | `integer` | Number of rules saved. |

#### Example Response (JSON)
```json
{
  "status": "saved",
  "count": 1
}
```

---

### `POST` /api/projects/{project_id}/redirects/test
**Test redirect URL matching** — Simulate and test how a specific request URL resolves against current redirect rules.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `test_url` | `string` | **Yes** | Incoming test URL path (e.g. "/old-docs/guide"). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/test \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"test_url": "/old-docs/intro"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `matched` | `boolean` | True if a rule matched. |
| `target_url` | `string` | Computed destination redirect URL. |
| `status_code` | `integer` | Resulting HTTP redirect status. |

#### Example Response (JSON)
```json
{
  "matched": true,
  "target_url": "/docs/intro",
  "status_code": 301,
  "rule_id": "red_01"
}
```

---

### `POST` /api/projects/{project_id}/redirects/import
**Import redirects file** — Import redirect rules from a `_redirects` file, Netlify format, or JSON array.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `raw_content` | `string` | **Yes** | Raw text content of `_redirects` file or JSON. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/import \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"raw_content": "/old /new 301\n/home / 302"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `imported_count` | `integer` | Number of successfully imported rules. |

#### Example Response (JSON)
```json
{
  "imported_count": 2,
  "status": "success"
}
```

---

## Analytics & Telemetry

### `GET` /api/projects/{project_id}/stats
**Real-time project stats** — Fetch real-time CPU percentage, memory consumption in MB, and active network connections.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/stats \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `cpu_usage` | `number` | Container CPU percentage. |
| `memory_mb` | `number` | Resident RAM used in megabytes. |
| `uptime_seconds` | `integer` | Seconds elapsed since container start. |

#### Example Response (JSON)
```json
{
  "cpu_usage": 3.8,
  "memory_mb": 112.5,
  "uptime_seconds": 86400
}
```

---

### `GET` /api/projects/{project_id}/performance
**Historical performance charts** — Retrieve time-series performance data points over the last 24 hours / 7 days for graphing.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `range` | `string` | No | "1h", "24h", "7d" (default: 24h). |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/performance?range=24h" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `series` | `array` | Time-series data points with timestamps, CPU and memory values. |

#### Example Response (JSON)
```json
{
  "series": [
    {
      "timestamp": "2026-09-13T11:00:00Z",
      "cpu": 3.4,
      "memory": 110.2
    },
    {
      "timestamp": "2026-09-13T12:00:00Z",
      "cpu": 4.1,
      "memory": 112.5
    }
  ]
}
```

---

### `GET` /api/projects/{project_id}/app-logs
**Application runtime logs** — Fetch parsed application standard output and error log streams with severity levels.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/app-logs \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `lines` | `array` | Array of parsed log line objects. |

#### Example Response (JSON)
```json
{
  "lines": [
    {
      "timestamp": "2026-09-13T12:00:01Z",
      "level": "info",
      "message": "Server listening on 3000"
    }
  ]
}
```

---

### `GET` /api/projects/{project_id}/router-logs
**HTTP edge proxy access logs** — Fetch reverse proxy HTTP access logs including client IP, status code, response time, and user agent.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `limit` | `integer` | No | Max entries to fetch (default: 100). |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/router-logs \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `requests` | `array` | Array of HTTP access log entries. |

#### Example Response (JSON)
```json
{
  "requests": [
    {
      "timestamp": "2026-09-13T12:05:10Z",
      "ip": "1.2.3.4",
      "method": "GET",
      "path": "/docs",
      "status": 200,
      "duration_ms": 14
    }
  ]
}
```

---

### `GET` /api/projects/{project_id}/visitors
**Visitor geographic telemetry** — Get aggregate geographic visitor countries, unique IP counts, and referrer distribution.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/visitors \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `countries` | `object` | Country ISO codes mapped to visitor counts. |
| `unique_visitors` | `integer` | Total unique visitors in time window. |

#### Example Response (JSON)
```json
{
  "unique_visitors": 1420,
  "countries": {
    "US": 620,
    "DE": 280,
    "FR": 190,
    "GB": 150
  }
}
```

---

### `GET` /api/projects/{project_id}/analytics
**HTTP status & latency metrics** — Breakdown of HTTP 2xx, 3xx, 4xx, 5xx status codes, p95 latency, and total bandwidth transferred.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/analytics \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `total_requests` | `integer` | Total requests served. |
| `status_codes` | `object` | Status code count distribution. |
| `p95_latency_ms` | `number` | 95th percentile latency. |

#### Example Response (JSON)
```json
{
  "total_requests": 48290,
  "status_codes": {
    "200": 47100,
    "304": 950,
    "404": 210,
    "500": 30
  },
  "p95_latency_ms": 18.5
}
```

---

## Release & Previews

### `GET` /api/projects/{project_id}/release
**Get release workspace** — Retrieve deployment environments (production, staging), policies, approval workflows, and active restore points.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/release \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `environments` | `array` | List of environments (production, staging). |
| `policy` | `object` | Release approval policy object. |

#### Example Response (JSON)
```json
{
  "environments": [
    {
      "id": "env_prod",
      "name": "production",
      "auto_deploy": false
    },
    {
      "id": "env_stg",
      "name": "staging",
      "auto_deploy": true
    }
  ],
  "policy": {
    "required_approvals": 1,
    "enforce_tests": true
  }
}
```

---

### `PUT` /api/projects/{project_id}/release/environments/{environment_id}
**Update release environment** — Configure environment branch targets, auto-deploy toggles, and variable overrides.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `environment_id` | `string` | **Yes** | Environment ID (e.g. "env_prod"). |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `target_branch` | `string` | No | Target git branch name. |
| `auto_deploy` | `boolean` | No | Enable automatic deploy on push. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/release/environments/env_prod \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"target_branch": "main", "auto_deploy": false}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |

#### Example Response (JSON)
```json
{
  "status": "saved"
}
```

---

### `PUT` /api/projects/{project_id}/release/policy
**Update release policy** — Set deployment guardrails, required peer approvals, and pre-deploy smoke test requirements.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `required_approvals` | `integer` | No | Number of sign-offs needed. |
| `enforce_tests` | `boolean` | No | Require passing automated tests. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/release/policy \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"required_approvals": 1, "enforce_tests": true}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "policy_updated" |

#### Example Response (JSON)
```json
{
  "status": "policy_updated"
}
```

---

### `POST` /api/projects/{project_id}/release/team
**Upsert release team member** — Add or update team member roles and deployment approval permissions.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `user_id` | `string` | **Yes** | User ID to add. |
| `role` | `string` | **Yes** | "lead", "reviewer", or "developer". |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/team \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"user_id": "usr_david", "role": "reviewer"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "member_added" |

#### Example Response (JSON)
```json
{
  "status": "member_added",
  "user_id": "usr_david",
  "role": "reviewer"
}
```

---

### `DELETE` /api/projects/{project_id}/release/team/{member_id}
**Remove release team member** — Revoke deployment approval authority from a user.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `member_id` | `string` | **Yes** | Team member record ID. |

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/release/team/mem_81 \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "member_removed" |

#### Example Response (JSON)
```json
{
  "status": "member_removed"
}
```

---

### `POST` /api/projects/{project_id}/release/approvals
**Request release approval** — Submit a formal release deployment request to team reviewers.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `target_env` | `string` | **Yes** | "production" or "staging". |
| `commit_sha` | `string` | **Yes** | Git commit SHA to release. |
| `notes` | `string` | No | Release notes for reviewer. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/approvals \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"target_env": "production", "commit_sha": "a19f201", "notes": "Bug fixes"}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `approval_id` | `string` | Approval request ID. |
| `status` | `string` | "pending_review" |

#### Example Response (JSON)
```json
{
  "approval_id": "appr_9918",
  "status": "pending_review"
}
```

---

### `POST` /api/projects/{project_id}/release/approvals/{approval_id}/decision
**Submit approval decision** — Approve or reject a pending release deployment request.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `approval_id` | `string` | **Yes** | Approval request ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `decision` | `string` | **Yes** | "approved" or "rejected". |
| `comment` | `string` | No | Reviewer comments. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/approvals/appr_9918/decision \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"decision": "approved", "comment": "Verified and passed QA"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "approved" |

#### Example Response (JSON)
```json
{
  "status": "approved",
  "approval_id": "appr_9918"
}
```

---

### `POST` /api/projects/{project_id}/release/restore-points
**Create restore snapshot** — Create an immutable system snapshot of workspace files, database, and container image for rapid recovery.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `label` | `string` | No | Snapshot label (e.g. "Pre-v2.0 migration"). |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/restore-points \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"label": "Pre-v2.0 migration"}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `restore_point_id` | `string` | Created snapshot ID. |
| `size_bytes` | `integer` | Total snapshot archive size. |

#### Example Response (JSON)
```json
{
  "restore_point_id": "snp_94812",
  "label": "Pre-v2.0 migration",
  "size_bytes": 104857600
}
```

---

### `POST` /api/projects/{project_id}/release/restore-points/{restore_point_id}/verify
**Verify restore snapshot** — Verify checksum integrity and restore capability of a saved snapshot archive.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |
| `restore_point_id` | `string` | **Yes** | Snapshot ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/restore-points/snp_94812/verify \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `valid` | `boolean` | True if snapshot passed checksum and integrity check. |

#### Example Response (JSON)
```json
{
  "valid": true,
  "checksum": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
}
```

---

### `POST` /api/projects/{project_id}/release/preview/start
**Start release preview** — Spawn an isolated ephemeral sandbox preview for validating a proposed release.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `commit_sha` | `string` | **Yes** | Git commit SHA to spin up. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/preview/start \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"commit_sha": "a19f201"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `preview_url` | `string` | Isolated ephemeral HTTPS preview URL. |
| `port` | `integer` | Allocated temporary port. |

#### Example Response (JSON)
```json
{
  "preview_url": "https://preview-a19f201.sycord.site",
  "port": 39042,
  "status": "running"
}
```

---

### `POST` /api/projects/{project_id}/release/preview/stop
**Stop release preview** — Terminate and tear down an ephemeral preview container.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/preview/stop \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "preview_stopped" |

#### Example Response (JSON)
```json
{
  "status": "preview_stopped"
}
```

---

### `POST` /api/projects/{project_id}/release/deploy
**Execute release deploy** — Execute approved release deployment with zero downtime swap.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `approval_id` | `string` | **Yes** | Approved release ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/deploy \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"approval_id": "appr_9918"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "deployed" |
| `deployment_id` | `string` | Production deployment run ID. |

#### Example Response (JSON)
```json
{
  "status": "deployed",
  "deployment_id": "dep_19485"
}
```

---

### `POST` /api/projects/{project_id}/preview/start
**Start interactive preview** — Start interactive live preview sandbox container for immediate browser testing.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/preview/start \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "running" |
| `preview_url` | `string` | Sandbox preview iframe URL. |

#### Example Response (JSON)
```json
{
  "status": "running",
  "preview_url": "https://sycord.site:8787/preview/proj_94821a"
}
```

---

### `POST` /api/projects/{project_id}/preview/stop
**Stop interactive preview** — Halt and tear down interactive sandbox preview session.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/preview/stop \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "stopped" |

#### Example Response (JSON)
```json
{
  "status": "stopped"
}
```

---

### `GET` /api/projects/{project_id}/preview/status
**Get preview status** — Check if sandbox preview process is running, responsive, and ready for iframe rendering.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/status \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `running` | `boolean` | True if preview process is running. |
| `ready` | `boolean` | True if HTTP port is responding with 200. |

#### Example Response (JSON)
```json
{
  "running": true,
  "ready": true,
  "port": 34100
}
```

---

### `GET` /api/projects/{project_id}/preview/iframe-check
**Check preview iframe headers** — Inspect X-Frame-Options and Content-Security-Policy headers on target preview port.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/iframe-check \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `embeddable` | `boolean` | True if headers permit iframe preview rendering. |

#### Example Response (JSON)
```json
{
  "embeddable": true
}
```

---

### `GET` /api/projects/{project_id}/preview/logs/stream
**Stream preview logs (SSE)** — Real-time SSE event stream of stdout logs from sandbox preview server.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Example Request (cURL)
```bash
curl -N -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/logs/stream \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `data` | `string` | Real-time SSE preview log chunk. |

#### Example Response (JSON)
```json
data: {"preview_log": "Compiled 42 modules in 120ms"}


```

---

## Git & Workspace Files

### `GET` /api/projects/git/github/status
**GitHub connection status** — Check if user has linked their personal or organization GitHub account.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/git/github/status \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `connected` | `boolean` | True if connected to GitHub OAuth. |
| `account` | `string` | GitHub username or organization handle. |

#### Example Response (JSON)
```json
{
  "connected": true,
  "account": "MDavidka"
}
```

---

### `PUT` /api/projects/git/github/config
**Configure GitHub OAuth** — Save GitHub App Client ID and Secret for repository imports.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `client_id` | `string` | **Yes** | GitHub OAuth Client ID. |
| `client_secret` | `string` | **Yes** | GitHub OAuth Client Secret. |

#### Example Request (cURL)
```bash
curl -X PUT https://sycord.site:8787/api/projects/git/github/config \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"client_id": "Iv1.8941829abc", "client_secret": "sec_gh_8921"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |

#### Example Response (JSON)
```json
{
  "status": "saved"
}
```

---

### `GET` /api/projects/git/github/connect
**Initiate GitHub OAuth** — Generate OAuth redirect URL to authenticate with GitHub and grant repo permissions.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/git/github/connect \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `redirect_url` | `string` | GitHub authorization URL with state nonce. |

#### Example Response (JSON)
```json
{
  "redirect_url": "https://github.com/login/oauth/authorize?client_id=Iv1...&scope=repo"
}
```

---

### `GET` /api/projects/git/github/callback
**Handle GitHub callback** — Exchange temporary OAuth code for persistent user access token.

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `code` | `string` | **Yes** | OAuth code from GitHub. |
| `state` | `string` | **Yes** | State nonce for CSRF protection. |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/projects/git/github/callback?code=gh_code_8192&state=nonce_99" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "connected" |
| `username` | `string` | Authenticated GitHub user handle. |

#### Example Response (JSON)
```json
{
  "status": "connected",
  "username": "MDavidka"
}
```

---

### `DELETE` /api/projects/git/github/disconnect
**Disconnect GitHub account** — Revoke stored GitHub OAuth tokens and disconnect linked account.

#### Example Request (cURL)
```bash
curl -X DELETE https://sycord.site:8787/api/projects/git/github/disconnect \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "disconnected" |

#### Example Response (JSON)
```json
{
  "status": "disconnected"
}
```

---

### `GET` /api/projects/git/github/repositories
**List GitHub repositories** — Fetch public and private repositories accessible via linked GitHub token.

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/git/github/repositories \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `repositories` | `array` | Array of repository objects (full_name, private, default_branch). |

#### Example Response (JSON)
```json
{
  "repositories": [
    {
      "full_name": "MDavidka/sarra",
      "private": false,
      "default_branch": "main",
      "language": "Python"
    }
  ]
}
```

---

### `GET` /api/projects/git/github/repositories/{repository:path}/branches
**List repository branches** — Fetch all git branches for a specific GitHub repository.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `repository` | `string` | **Yes** | Full repository path (e.g. "MDavidka/sarra"). |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/git/github/repositories/MDavidka/sarra/branches \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `branches` | `array` | List of branch names and commit SHAs. |

#### Example Response (JSON)
```json
{
  "branches": [
    {
      "name": "main",
      "commit": {
        "sha": "a19f201"
      }
    },
    {
      "name": "feat/mobile-header-and-sidebar-accuracy",
      "commit": {
        "sha": "94812aa"
      }
    }
  ]
}
```

---

### `POST` /api/projects/import/github
**Import GitHub repository** — Clone repository from GitHub into a new project workspace and setup automatic deployment webhooks.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `repo` | `string` | **Yes** | Full repository name (e.g. "MDavidka/sarra"). |
| `branch` | `string` | No | Branch name (default: default_branch). |
| `name` | `string` | No | Optional project slug name. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/import/github \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"repo": "MDavidka/sarra", "branch": "main"}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `project_id` | `string` | Created project ID. |
| `status` | `string` | "imported" |

#### Example Response (JSON)
```json
{
  "project_id": "proj_94821a",
  "name": "sarra",
  "status": "imported"
}
```

---

### `POST` /api/projects/import/repository
**Import public Git repository** — Clone any public Git repository via HTTPS URL into a fresh workspace.

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `git_url` | `string` | **Yes** | Public Git HTTPS clone URL. |
| `branch` | `string` | No | Target branch to check out. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/import/repository \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"git_url": "https://github.com/vercel/next.js.git", "branch": "canary"}'
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `project_id` | `string` | Created project ID. |
| `status` | `string` | "cloned" |

#### Example Response (JSON)
```json
{
  "project_id": "proj_88192a",
  "status": "cloned"
}
```

---

### `POST` /api/projects/import/zip
**Upload project ZIP archive** — Upload and unpack a ZIP archive of source code directly into project workspace.

#### Request Body (`multipart/form-data`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `file` | `binary` | **Yes** | ZIP archive binary stream. |
| `name` | `string` | No | Optional project slug name. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/import/zip \
  -H "Authorization: Bearer <token>" \
  -F "file=@project-source.zip"
```

#### Response Schema (201 Created)
| Field | Type | Description |
| :--- | :--- | :--- |
| `project_id` | `string` | Created project ID. |
| `files_extracted` | `integer` | Count of extracted files. |

#### Example Response (JSON)
```json
{
  "project_id": "proj_55219a",
  "status": "extracted",
  "files_extracted": 48
}
```

---

### `GET` /api/projects/{project_id}/workspace/files
**List workspace file tree** — Retrieve hierarchical file tree and directory structure of project workspace.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `path` | `string` | No | Subdirectory path to list (default: root). |

#### Example Request (cURL)
```bash
curl -X GET https://sycord.site:8787/api/projects/proj_94821a/workspace/files \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `files` | `array` | Array of file and folder nodes with size, path, and type. |

#### Example Response (JSON)
```json
{
  "files": [
    {
      "name": "package.json",
      "type": "file",
      "size": 1024,
      "path": "package.json"
    },
    {
      "name": "src",
      "type": "directory",
      "path": "src"
    }
  ]
}
```

---

### `GET` /api/projects/{project_id}/workspace/file
**Read workspace file** — Read UTF-8 text content of a specific source code file.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `path` | `string` | **Yes** | Relative file path inside workspace (e.g. "package.json"). |

#### Example Request (cURL)
```bash
curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/workspace/file?path=package.json" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `content` | `string` | Raw text file content. |
| `size` | `integer` | File size in bytes. |

#### Example Response (JSON)
```json
{
  "path": "package.json",
  "content": "{\n  \"name\": \"sarra-app\",\n  \"version\": \"1.0.0\"\n}",
  "size": 42
}
```

---

### `POST` /api/projects/{project_id}/workspace/file
**Write workspace file** — Save or update text content of a file in the workspace.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `path` | `string` | **Yes** | Relative file path. |
| `content` | `string` | **Yes** | New file content to write. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/file \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"path": "config.json", "content": "{\"port\": 3000}"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "saved" |
| `bytes_written` | `integer` | Number of bytes written to disk. |

#### Example Response (JSON)
```json
{
  "status": "saved",
  "bytes_written": 16
}
```

---

### `POST` /api/projects/{project_id}/workspace/mkdir
**Create folder in workspace** — Create a new subdirectory directory in project workspace.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`application/json`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `path` | `string` | **Yes** | Relative directory path to create. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/mkdir \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"path": "src/components"}'
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "created" |

#### Example Response (JSON)
```json
{
  "status": "created",
  "path": "src/components"
}
```

---

### `DELETE` /api/projects/{project_id}/workspace/file
**Delete workspace file or folder** — Permanently delete a file or directory from the workspace.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Query Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `path` | `string` | **Yes** | Relative path of file or folder to delete. |

#### Example Request (cURL)
```bash
curl -X DELETE "https://sycord.site:8787/api/projects/proj_94821a/workspace/file?path=temp.log" \
  -H "Authorization: Bearer <token>"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "deleted" |

#### Example Response (JSON)
```json
{
  "status": "deleted"
}
```

---

### `POST` /api/projects/{project_id}/workspace/upload
**Upload workspace file** — Upload a binary or text file to a specific destination in project workspace.

#### Path Parameters
| Name | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `project_id` | `string` | **Yes** | Project ID. |

#### Request Body (`multipart/form-data`)
| Field | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `file` | `binary` | **Yes** | File payload multipart stream. |
| `destination_path` | `string` | **Yes** | Relative target folder path. |

#### Example Request (cURL)
```bash
curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/upload \
  -H "Authorization: Bearer <token>" \
  -F "file=@logo.png" \
  -F "destination_path=public/"
```

#### Response Schema (200 OK)
| Field | Type | Description |
| :--- | :--- | :--- |
| `status` | `string` | "uploaded" |
| `file_path` | `string` | Saved file path in workspace. |

#### Example Response (JSON)
```json
{
  "status": "uploaded",
  "file_path": "public/logo.png"
}
```

---
