#!/usr/bin/env python3
import json
import re

# Comprehensive catalog of all 113 unique API endpoints in Syte
API_ENDPOINTS = [
    # 1. System & Health
    {
        "key": "api-health-get",
        "group": "System & Health",
        "title": "Health check",
        "summary": "Check API availability and core system uptime status.",
        "method": "GET",
        "path": "/api/health",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/health',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "Current service status (\"ok\")."},
            {"name": "version", "type": "string", "desc": "Running Syte release version tag."},
            {"name": "timestamp", "type": "string", "desc": "ISO-8601 server timestamp."}
        ],
        "responseJson": json.dumps({"status": "ok", "version": "2.4.0", "timestamp": "2026-09-13T12:00:00Z"}, indent=2)
    },
    {
        "key": "api-system-get",
        "group": "System & Health",
        "title": "System hardware metrics",
        "summary": "Retrieve real-time host VM CPU, memory, disk usage, and OS kernel information.",
        "method": "GET",
        "path": "/api/system",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/system \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "cpu_percent", "type": "number", "desc": "Current CPU utilization percentage across all cores."},
            {"name": "memory", "type": "object", "desc": "RAM usage metrics in bytes (total, used, free, percent)."},
            {"name": "disk", "type": "object", "desc": "Root filesystem storage usage stats (total, used, free)."},
            {"name": "platform", "type": "string", "desc": "Host operating system and kernel version string."}
        ],
        "responseJson": json.dumps({"cpu_percent": 14.2, "memory": {"total": 8589934592, "used": 2810183680, "free": 5779750912, "percent": 32.7}, "disk": {"total": 53687091200, "used": 12884901888, "free": 40802189312, "percent": 24.0}, "platform": "Linux 6.8.0-amd64"}, indent=2)
    },
    {
        "key": "api-system-update-info-get",
        "group": "System & Health",
        "title": "Check release updates",
        "summary": "Query upstream GitHub repository for new release versions and changelog notes.",
        "method": "GET",
        "path": "/api/system/update-info",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/system/update-info \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "current_version", "type": "string", "desc": "Currently deployed Syte platform version."},
            {"name": "latest_version", "type": "string", "desc": "Latest available version tag on GitHub."},
            {"name": "update_available", "type": "boolean", "desc": "Whether an upgrade can be triggered."},
            {"name": "release_notes", "type": "string", "desc": "Changelog and release notes markdown."}
        ],
        "responseJson": json.dumps({"current_version": "2.4.0", "latest_version": "2.4.1", "update_available": True, "release_notes": "Added real-time SSE streaming logs and automated certificate renewal."}, indent=2)
    },
    {
        "key": "api-system-update-post",
        "group": "System & Health",
        "title": "Trigger platform self-update",
        "summary": "Initiate background git pull, dependency install, and systemd service reload.",
        "method": "POST",
        "path": "/api/system/update",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/system/update \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "Update operation status (\"in_progress\")."},
            {"name": "target_version", "type": "string", "desc": "Target version being installed."}
        ],
        "responseJson": json.dumps({"status": "in_progress", "target_version": "2.4.1", "message": "Self-update process spawned in background."}, indent=2)
    },

    # 2. Notifications
    {
        "key": "api-notifications-settings-get",
        "group": "Notifications",
        "title": "Get notification settings",
        "summary": "Fetch alert configuration including webhook endpoints, Discord/Slack hooks, and email alerts.",
        "method": "GET",
        "path": "/api/notifications/settings",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/notifications/settings \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "webhook_url", "type": "string", "desc": "HTTP webhook URL for dispatching JSON alerts."},
            {"name": "discord_webhook", "type": "string", "desc": "Discord channel incoming webhook URL."},
            {"name": "slack_webhook", "type": "string", "desc": "Slack incoming webhook URL."},
            {"name": "notify_on_deploy", "type": "boolean", "desc": "Send alert on successful deployment."},
            {"name": "notify_on_fail", "type": "boolean", "desc": "Send high-priority alert on build failure."}
        ],
        "responseJson": json.dumps({"webhook_url": "https://hooks.example.com/alerts", "discord_webhook": "", "slack_webhook": "", "notify_on_deploy": True, "notify_on_fail": True}, indent=2)
    },
    {
        "key": "api-notifications-settings-put",
        "group": "Notifications",
        "title": "Update notification settings",
        "summary": "Save webhook destinations and notification trigger policies.",
        "method": "PUT",
        "path": "/api/notifications/settings",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "webhook_url", "type": "string", "required": False, "desc": "HTTP webhook endpoint."},
            {"name": "discord_webhook", "type": "string", "required": False, "desc": "Discord incoming webhook URL."},
            {"name": "slack_webhook", "type": "string", "required": False, "desc": "Slack incoming webhook URL."},
            {"name": "notify_on_deploy", "type": "boolean", "required": False, "desc": "Enable deployment success alerts."},
            {"name": "notify_on_fail", "type": "boolean", "required": False, "desc": "Enable build failure alerts."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/notifications/settings \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"notify_on_deploy": true, "notify_on_fail": true}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "Operation confirmation (\"saved\")."},
            {"name": "settings", "type": "object", "desc": "Updated notification settings object."}
        ],
        "responseJson": json.dumps({"status": "saved", "settings": {"notify_on_deploy": True, "notify_on_fail": True}}, indent=2)
    },
    {
        "key": "api-notifications-get",
        "group": "Notifications",
        "title": "List notifications",
        "summary": "Retrieve in-app notifications and alert history with read/unread flags.",
        "method": "GET",
        "path": "/api/notifications",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [
            {"name": "limit", "type": "integer", "required": False, "desc": "Maximum notifications to return (default: 50)."},
            {"name": "unread_only", "type": "boolean", "required": False, "desc": "Filter by unread notifications only."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/notifications?unread_only=false" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "items", "type": "array", "desc": "Array of notification objects."},
            {"name": "unread_count", "type": "integer", "desc": "Total count of unread notifications."}
        ],
        "responseJson": json.dumps({"items": [{"id": "ntf_01", "title": "Build Completed", "message": "Project syte-docs deployed successfully to production.", "level": "info", "read": False, "created_at": "2026-09-13T11:45:00Z"}], "unread_count": 1}, indent=2)
    },
    {
        "key": "api-notifications-read-post",
        "group": "Notifications",
        "title": "Mark notifications read",
        "summary": "Mark one or all notifications as read to clear badge counts.",
        "method": "POST",
        "path": "/api/notifications/read",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "notification_ids", "type": "array", "required": False, "desc": "List of IDs to mark as read, or empty to mark all."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/notifications/read \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"notification_ids": ["ntf_01"]}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"success\""},
            {"name": "marked_count", "type": "integer", "desc": "Number of notifications updated."}
        ],
        "responseJson": json.dumps({"status": "success", "marked_count": 1}, indent=2)
    },
    {
        "key": "api-notifications-push-vapid-public-key-get",
        "group": "Notifications",
        "title": "Get VAPID public key",
        "summary": "Retrieve public VAPID key used for client Web Push subscription registration.",
        "method": "GET",
        "path": "/api/notifications/push/vapid-public-key",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/notifications/push/vapid-public-key \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "public_key", "type": "string", "desc": "Base64-encoded VAPID public key string."}
        ],
        "responseJson": json.dumps({"public_key": "BEl62iUYgUivxIkv69yViEuiBIa..."}, indent=2)
    },
    {
        "key": "api-notifications-push-subscriptions-post",
        "group": "Notifications",
        "title": "Register push subscription",
        "summary": "Save a browser ServiceWorker Web Push subscription payload for native push notifications.",
        "method": "POST",
        "path": "/api/notifications/push-subscriptions",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "endpoint", "type": "string", "required": True, "desc": "Browser push service endpoint URL."},
            {"name": "keys", "type": "object", "required": True, "desc": "Encryption keys object containing `p256dh` and `auth` strings."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/notifications/push-subscriptions \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"endpoint": "https://fcm.googleapis.com/fcm/send/...", "keys": {"p256dh": "...", "auth": "..."}}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"subscribed\""}
        ],
        "responseJson": json.dumps({"status": "subscribed"}, indent=2)
    },
    {
        "key": "api-notifications-test-post",
        "group": "Notifications",
        "title": "Send test notification",
        "summary": "Trigger immediate test notification across all enabled channels (Web Push, Webhook, Discord).",
        "method": "POST",
        "path": "/api/notifications/test",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/notifications/test \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"dispatched\""},
            {"name": "channels", "type": "array", "desc": "List of notification channels reached."}
        ],
        "responseJson": json.dumps({"status": "dispatched", "channels": ["in_app", "web_push"]}, indent=2)
    },

    # 3. Auth & Operator
    {
        "key": "api-auth-setup-get",
        "group": "Auth & Operator",
        "title": "Check setup status",
        "summary": "Check whether root administrator account has already been initialized.",
        "method": "GET",
        "path": "/api/auth/setup",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/auth/setup',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "setup_required", "type": "boolean", "desc": "True if no admin account exists yet."}
        ],
        "responseJson": json.dumps({"setup_required": False}, indent=2)
    },
    {
        "key": "api-auth-setup-post",
        "group": "Auth & Operator",
        "title": "Initialize administrator",
        "summary": "Create primary administrator username and master password during initial deployment.",
        "method": "POST",
        "path": "/api/auth/setup",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "username", "type": "string", "required": True, "desc": "Admin username (minimum 3 characters)."},
            {"name": "password", "type": "string", "required": True, "desc": "Secure password (minimum 8 characters)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/auth/setup \\\n  -H "Content-Type: application/json" \\\n  -d \'{"username": "admin", "password": "SuperSecretPassword123"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"initialized\""},
            {"name": "user", "type": "object", "desc": "Created user account details."}
        ],
        "responseJson": json.dumps({"status": "initialized", "user": {"id": "usr_01", "username": "admin", "role": "owner"}}, indent=2)
    },
    {
        "key": "api-auth-login-post",
        "group": "Auth & Operator",
        "title": "User login",
        "summary": "Authenticate user credentials and issue session cookie or bearer token.",
        "method": "POST",
        "path": "/api/auth/login",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "username", "type": "string", "required": True, "desc": "Registered username."},
            {"name": "password", "type": "string", "required": True, "desc": "Account password."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/auth/login \\\n  -H "Content-Type: application/json" \\\n  -d \'{"username": "admin", "password": "SuperSecretPassword123"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"authenticated\""},
            {"name": "token", "type": "string", "desc": "JWT session token."},
            {"name": "user", "type": "object", "desc": "User profile object."}
        ],
        "responseJson": json.dumps({"status": "authenticated", "token": "eyJhbGciOiJIUzI1NiIsIn...", "user": {"username": "admin", "role": "owner"}}, indent=2)
    },
    {
        "key": "api-auth-session-get",
        "group": "Auth & Operator",
        "title": "Inspect active session",
        "summary": "Validate session token or cookie and return authenticated user identity.",
        "method": "GET",
        "path": "/api/auth/session",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/auth/session \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "authenticated", "type": "boolean", "desc": "True if session is active and valid."},
            {"name": "user", "type": "object", "desc": "Current authenticated user details."}
        ],
        "responseJson": json.dumps({"authenticated": True, "user": {"id": "usr_01", "username": "admin", "role": "owner"}}, indent=2)
    },
    {
        "key": "api-auth-session-delete",
        "group": "Auth & Operator",
        "title": "Logout session",
        "summary": "Invalidate current session token and clear authentication cookie.",
        "method": "DELETE",
        "path": "/api/auth/session",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/auth/session \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"logged_out\""}
        ],
        "responseJson": json.dumps({"status": "logged_out"}, indent=2)
    },
    {
        "key": "api-auth-profile-get",
        "group": "Auth & Operator",
        "title": "Get user profile",
        "summary": "Retrieve user profile, contact info, and preferences.",
        "method": "GET",
        "path": "/api/auth/profile",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/auth/profile \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "username", "type": "string", "desc": "Current username."},
            {"name": "email", "type": "string", "desc": "Registered email address."},
            {"name": "theme", "type": "string", "desc": "UI theme preference (\"dark\" / \"light\")."}
        ],
        "responseJson": json.dumps({"username": "admin", "email": "admin@example.com", "theme": "dark"}, indent=2)
    },
    {
        "key": "api-auth-profile-put",
        "group": "Auth & Operator",
        "title": "Update user profile",
        "summary": "Update user password, email, and display preferences.",
        "method": "PUT",
        "path": "/api/auth/profile",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "email", "type": "string", "required": False, "desc": "New email address."},
            {"name": "current_password", "type": "string", "required": False, "desc": "Current password for verification."},
            {"name": "new_password", "type": "string", "required": False, "desc": "New password to set."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/auth/profile \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"email": "ops@example.com"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""}
        ],
        "responseJson": json.dumps({"status": "updated"}, indent=2)
    },
    {
        "key": "api-operator-session-get",
        "group": "Auth & Operator",
        "title": "Get operator session",
        "summary": "Check if an elevated maintenance operator session is currently active.",
        "method": "GET",
        "path": "/api/operator/session",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/operator/session \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "operator_active", "type": "boolean", "desc": "True if maintenance mode operator is enabled."},
            {"name": "expires_at", "type": "string", "desc": "ISO-8601 expiration timestamp."}
        ],
        "responseJson": json.dumps({"operator_active": False, "expires_at": None}, indent=2)
    },
    {
        "key": "api-operator-session-post",
        "group": "Auth & Operator",
        "title": "Start operator session",
        "summary": "Elevate current session with operator secret to bypass project quotas and access root controls.",
        "method": "POST",
        "path": "/api/operator/session",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "operator_key", "type": "string", "required": True, "desc": "Host operator access secret."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/operator/session \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"operator_key": "op_sec_999"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"operator_granted\""}
        ],
        "responseJson": json.dumps({"status": "operator_granted"}, indent=2)
    },
    {
        "key": "api-operator-session-delete",
        "group": "Auth & Operator",
        "title": "End operator session",
        "summary": "Revoke elevated operator privileges and return to normal permission scope.",
        "method": "DELETE",
        "path": "/api/operator/session",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/operator/session \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"operator_revoked\""}
        ],
        "responseJson": json.dumps({"status": "operator_revoked"}, indent=2)
    },
    {
        "key": "api-tokens-get",
        "group": "Auth & Operator",
        "title": "List API tokens",
        "summary": "List all active programmatic API tokens with permissions and last used timestamps.",
        "method": "GET",
        "path": "/api/tokens",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/tokens \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "tokens", "type": "array", "desc": "Array of API token metadata objects."}
        ],
        "responseJson": json.dumps({"tokens": [{"id": "tok_9918", "name": "CI/CD Deployment Token", "prefix": "syt_live_...", "created_at": "2026-09-01T08:00:00Z", "last_used": "2026-09-13T10:15:20Z"}]}, indent=2)
    },
    {
        "key": "api-tokens-post",
        "group": "Auth & Operator",
        "title": "Create API token",
        "summary": "Generate a new persistent API token for CI/CD pipelines and external integrations.",
        "method": "POST",
        "path": "/api/tokens",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "name", "type": "string", "required": True, "desc": "Descriptive token identifier (e.g. GitHub Actions)."},
            {"name": "expires_in_days", "type": "integer", "required": False, "desc": "Days until expiration (0 for never)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/tokens \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"name": "GitHub Actions CI", "expires_in_days": 90}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "token", "type": "string", "desc": "Full plaintext secret token (only displayed once)."},
            {"name": "token_id", "type": "string", "desc": "Token ID for future revocation."}
        ],
        "responseJson": json.dumps({"token_id": "tok_9919", "token": "syt_live_a89f923b7c84192d1948", "name": "GitHub Actions CI"}, indent=2)
    },
    {
        "key": "api-tokens-token-id-delete",
        "group": "Auth & Operator",
        "title": "Revoke API token",
        "summary": "Immediately revoke and permanently delete an API token.",
        "method": "DELETE",
        "path": "/api/tokens/{token_id}",
        "contentType": "none",
        "pathParams": [
            {"name": "token_id", "type": "string", "required": True, "desc": "Unique identifier of the token to revoke."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/tokens/tok_9918 \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"revoked\""}
        ],
        "responseJson": json.dumps({"status": "revoked"}, indent=2)
    },

    # 4. Settings & GitHub
    {
        "key": "api-settings-get",
        "group": "Settings & GitHub",
        "title": "Get platform settings",
        "summary": "Retrieve global system settings, networking defaults, and domain configuration.",
        "method": "GET",
        "path": "/api/settings",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/settings \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "default_domain", "type": "string", "desc": "Root domain for automatic subdomain routing."},
            {"name": "telemetry_enabled", "type": "boolean", "desc": "Whether telemetry data collection is enabled."},
            {"name": "max_concurrent_builds", "type": "integer", "desc": "Max concurrent container builds."}
        ],
        "responseJson": json.dumps({"default_domain": "sycord.site", "telemetry_enabled": True, "max_concurrent_builds": 4}, indent=2)
    },
    {
        "key": "api-settings-put",
        "group": "Settings & GitHub",
        "title": "Save platform settings",
        "summary": "Update global system settings and networking defaults.",
        "method": "PUT",
        "path": "/api/settings",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "default_domain", "type": "string", "required": False, "desc": "Apex domain for routing."},
            {"name": "max_concurrent_builds", "type": "integer", "required": False, "desc": "Build concurrency limit."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/settings \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"max_concurrent_builds": 4}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""}
        ],
        "responseJson": json.dumps({"status": "saved"}, indent=2)
    },
    {
        "key": "api-settings-cache-get",
        "group": "Settings & GitHub",
        "title": "Get cache metrics",
        "summary": "Inspect disk usage by build caches, docker layers, and temporary file artifacts.",
        "method": "GET",
        "path": "/api/settings/cache",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/settings/cache \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "build_cache_size", "type": "integer", "desc": "Size of npm/pip/cargo build caches in bytes."},
            {"name": "docker_cache_size", "type": "integer", "desc": "Size of dangling container image layers."},
            {"name": "temp_files_size", "type": "integer", "desc": "Size of temp staging directories."}
        ],
        "responseJson": json.dumps({"build_cache_size": 2147483648, "docker_cache_size": 5368709120, "temp_files_size": 268435456}, indent=2)
    },
    {
        "key": "api-settings-cache-clear-post",
        "group": "Settings & GitHub",
        "title": "Clear system cache",
        "summary": "Purge build caches, temporary zip extractions, and unused Docker layers to free disk space.",
        "method": "POST",
        "path": "/api/settings/cache/clear",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/settings/cache/clear \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "freed_bytes", "type": "integer", "desc": "Total disk space recovered in bytes."},
            {"name": "status", "type": "string", "desc": "\"cleared\""}
        ],
        "responseJson": json.dumps({"status": "cleared", "freed_bytes": 7784628224}, indent=2)
    },
    {
        "key": "api-settings-github-get",
        "group": "Settings & GitHub",
        "title": "Get GitHub App config",
        "summary": "Retrieve configured GitHub OAuth Client ID, App ID, and installation status.",
        "method": "GET",
        "path": "/api/settings/github",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/settings/github \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "client_id", "type": "string", "desc": "GitHub OAuth Client ID."},
            {"name": "is_configured", "type": "boolean", "desc": "True if Client Secret is securely stored."}
        ],
        "responseJson": json.dumps({"client_id": "Iv1.8941829abc", "is_configured": True}, indent=2)
    },
    {
        "key": "api-settings-github-put",
        "group": "Settings & GitHub",
        "title": "Update GitHub App config",
        "summary": "Save GitHub OAuth application credentials for repository imports and webhook triggers.",
        "method": "PUT",
        "path": "/api/settings/github",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "client_id", "type": "string", "required": True, "desc": "GitHub OAuth Client ID."},
            {"name": "client_secret", "type": "string", "required": True, "desc": "GitHub OAuth Client Secret."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/settings/github \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"client_id": "Iv1.8941829abc", "client_secret": "sec_gh_8921"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""}
        ],
        "responseJson": json.dumps({"status": "saved"}, indent=2)
    },
    {
        "key": "api-settings-github-test-post",
        "group": "Settings & GitHub",
        "title": "Test GitHub credentials",
        "summary": "Validate GitHub OAuth credentials against GitHub REST API.",
        "method": "POST",
        "path": "/api/settings/github/test",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/settings/github/test \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "valid", "type": "boolean", "desc": "True if credentials successfully authenticated with GitHub."}
        ],
        "responseJson": json.dumps({"valid": True, "message": "Successfully authenticated with GitHub API."}, indent=2)
    },
    {
        "key": "api-github-status-get",
        "group": "Settings & GitHub",
        "title": "Check GitHub link status",
        "summary": "Check if active user session is linked with a GitHub account.",
        "method": "GET",
        "path": "/api/github/status",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/github/status \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "connected", "type": "boolean", "desc": "True if GitHub OAuth token is valid."},
            {"name": "github_username", "type": "string", "desc": "Linked GitHub account handle."}
        ],
        "responseJson": json.dumps({"connected": True, "github_username": "octocat"}, indent=2)
    },
    {
        "key": "api-github-pulls-get",
        "group": "Settings & GitHub",
        "title": "List project pull requests",
        "summary": "Fetch open pull requests from linked GitHub repository for preview environment generation.",
        "method": "GET",
        "path": "/api/github/pulls",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [
            {"name": "repo", "type": "string", "required": True, "desc": "Full repository name (e.g. \"owner/repo\")."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/github/pulls?repo=MDavidka/sarra" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "pulls", "type": "array", "desc": "List of open PR objects with branch info."}
        ],
        "responseJson": json.dumps({"pulls": [{"number": 515, "title": "feat: mobile header and sidebar accuracy", "author": "MDavidka", "head_ref": "feat/mobile-header-and-sidebar-accuracy", "state": "open"}]}, indent=2)
    },
    {
        "key": "api-github-pulls-number-merge-post",
        "group": "Settings & GitHub",
        "title": "Merge GitHub pull request",
        "summary": "Trigger automated merge of approved pull request into target production branch.",
        "method": "POST",
        "path": "/api/github/pulls/{number}/merge",
        "contentType": "application/json",
        "pathParams": [
            {"name": "number", "type": "integer", "required": True, "desc": "Pull request number."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "merge_method", "type": "string", "required": False, "desc": "\"merge\", \"squash\", or \"rebase\" (default: squash)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/github/pulls/515/merge \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"merge_method": "squash"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "merged", "type": "boolean", "desc": "True if merge succeeded."},
            {"name": "sha", "type": "string", "desc": "Commit SHA of the merge commit."}
        ],
        "responseJson": json.dumps({"merged": True, "sha": "4a8c901e892b491a"}, indent=2)
    },

    # 5. SSL & Certificates
    {
        "key": "api-ssl-get",
        "group": "SSL & Certificates",
        "title": "Global SSL status",
        "summary": "Check status of ACME Let\'s Encrypt certificates and TLS expiration dates.",
        "method": "GET",
        "path": "/api/ssl",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/ssl \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "certificates", "type": "array", "desc": "List of active TLS certificates and domains."}
        ],
        "responseJson": json.dumps({"certificates": [{"domain": "sycord.site", "issuer": "Let\'s Encrypt", "valid_until": "2026-12-12T00:00:00Z", "auto_renew": True}]}, indent=2)
    },
    {
        "key": "api-ssl-resolve-post",
        "group": "SSL & Certificates",
        "title": "Resolve DNS records",
        "summary": "Perform live DNS A and CNAME record resolution to test propagation before issuing SSL.",
        "method": "POST",
        "path": "/api/ssl/resolve",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "domain", "type": "string", "required": True, "desc": "Domain name to resolve."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/ssl/resolve \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"domain": "app.sycord.site"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "resolved", "type": "boolean", "desc": "True if domain points to this host IP."},
            {"name": "ip_addresses", "type": "array", "desc": "Resolved A/AAAA IP addresses."}
        ],
        "responseJson": json.dumps({"resolved": True, "ip_addresses": ["185.199.108.153"]}, indent=2)
    },
    {
        "key": "api-ssl-projects-custom-tls-post",
        "group": "SSL & Certificates",
        "title": "Upload custom TLS certificate",
        "summary": "Upload custom SSL certificate and private key for enterprise domain hosting.",
        "method": "POST",
        "path": "/api/ssl/projects/{project_id}/custom-tls",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "certificate_pem", "type": "string", "required": True, "desc": "PEM-formatted certificate chain."},
            {"name": "private_key_pem", "type": "string", "required": True, "desc": "PEM-formatted private key."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/ssl/projects/proj_94821a/custom-tls \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"certificate_pem": "-----BEGIN CERTIFICATE...", "private_key_pem": "-----BEGIN RSA PRIVATE KEY..."}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"installed\""},
            {"name": "valid_until", "type": "string", "desc": "Expiration date of uploaded cert."}
        ],
        "responseJson": json.dumps({"status": "installed", "valid_until": "2027-01-01T00:00:00Z"}, indent=2)
    },
    {
        "key": "api-certificates-guide-get",
        "group": "SSL & Certificates",
        "title": "Get certificate guide",
        "summary": "Get required DNS CNAME/A record targets and automated ACME issuance guidance.",
        "method": "GET",
        "path": "/api/certificates/guide",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [
            {"name": "domain", "type": "string", "required": True, "desc": "Target custom domain name."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/certificates/guide?domain=app.example.com" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "cname_target", "type": "string", "desc": "CNAME target record."},
            {"name": "a_record", "type": "string", "desc": "Host public IPv4 address."}
        ],
        "responseJson": json.dumps({"cname_target": "cname.sycord.site", "a_record": "185.199.108.153", "instructions": "Create a CNAME record pointing app.example.com to cname.sycord.site"}, indent=2)
    },
    {
        "key": "api-certificates-issue-post",
        "group": "SSL & Certificates",
        "title": "Issue Let\'s Encrypt SSL",
        "summary": "Execute automated HTTP-01 or DNS-01 ACME challenge to issue Let\'s Encrypt SSL certificate.",
        "method": "POST",
        "path": "/api/certificates/issue",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "domain", "type": "string", "required": True, "desc": "Fully-qualified domain name."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/certificates/issue \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"domain": "app.example.com"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"issued\""},
            {"name": "domain", "type": "string", "desc": "Provisioned domain."},
            {"name": "expires_at", "type": "string", "desc": "Expiration date (90 days)."}
        ],
        "responseJson": json.dumps({"status": "issued", "domain": "app.example.com", "expires_at": "2026-12-13T12:00:00Z"}, indent=2)
    },

    # 6. Projects & Core Lifecycle
    {
        "key": "api-projects-get",
        "group": "Projects & Lifecycle",
        "title": "List all projects",
        "summary": "Retrieve an array of all hosted web applications and backend services.",
        "method": "GET",
        "path": "/api/projects",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "projects", "type": "array", "desc": "List of project summary objects."}
        ],
        "responseJson": json.dumps({"projects": [{"id": "proj_94821a", "name": "sarra-docs", "status": "running", "port": 3000, "domain": "docs.sycord.site"}]}, indent=2)
    },
    {
        "key": "api-projects-post",
        "group": "Projects & Lifecycle",
        "title": "Create new project",
        "summary": "Create and initialize a new project workspace directory and configuration.",
        "method": "POST",
        "path": "/api/projects",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "name", "type": "string", "required": True, "desc": "Project slug name."},
            {"name": "framework", "type": "string", "required": False, "desc": "Framework type (e.g. \"nextjs\", \"fastapi\", \"static\")."},
            {"name": "port", "type": "integer", "required": False, "desc": "Container internal port (default: 3000)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"name": "my-api", "framework": "fastapi", "port": 8000}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "id", "type": "string", "desc": "Assigned unique project identifier."},
            {"name": "name", "type": "string", "desc": "Project name."},
            {"name": "status", "type": "string", "desc": "\"initialized\""}
        ],
        "responseJson": json.dumps({"id": "proj_8819ab", "name": "my-api", "status": "initialized", "port": 8000}, indent=2)
    },
    {
        "key": "api-projects-project-id-get",
        "group": "Projects & Lifecycle",
        "title": "Get project details",
        "summary": "Retrieve complete runtime metadata, environment keys, domains, and health status for a project.",
        "method": "GET",
        "path": "/api/projects/{project_id}",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "id", "type": "string", "desc": "Project ID."},
            {"name": "name", "type": "string", "desc": "Project name."},
            {"name": "status", "type": "string", "desc": "\"running\", \"stopped\", or \"building\"."},
            {"name": "port", "type": "integer", "desc": "Container listening port."},
            {"name": "domain", "type": "string", "desc": "Bound custom domain."},
            {"name": "framework", "type": "string", "desc": "Detected runtime framework."}
        ],
        "responseJson": json.dumps({"id": "proj_94821a", "name": "sarra-docs", "status": "running", "port": 3000, "domain": "docs.sycord.site", "framework": "nextjs"}, indent=2)
    },
    {
        "key": "api-projects-project-id-put",
        "group": "Projects & Lifecycle",
        "title": "Update project settings",
        "summary": "Modify project configuration including assigned port, framework, and build scripts.",
        "method": "PUT",
        "path": "/api/projects/{project_id}",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "name", "type": "string", "required": False, "desc": "New project name."},
            {"name": "port", "type": "integer", "required": False, "desc": "Updated internal container port."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"port": 8080}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""}
        ],
        "responseJson": json.dumps({"status": "updated"}, indent=2)
    },
    {
        "key": "api-projects-project-id-delete",
        "group": "Projects & Lifecycle",
        "title": "Delete project",
        "summary": "Permanently stop container, wipe workspace directory, remove domains, and delete project database record.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"deleted\""}
        ],
        "responseJson": json.dumps({"status": "deleted"}, indent=2)
    },
    {
        "key": "api-projects-project-id-start-post",
        "group": "Projects & Lifecycle",
        "title": "Start project container",
        "summary": "Start background systemd/docker container process for project.",
        "method": "POST",
        "path": "/api/projects/{project_id}/start",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/start \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"started\""}
        ],
        "responseJson": json.dumps({"status": "started"}, indent=2)
    },
    {
        "key": "api-projects-project-id-stop-post",
        "group": "Projects & Lifecycle",
        "title": "Stop project container",
        "summary": "Gracefully terminate project container and halt process execution.",
        "method": "POST",
        "path": "/api/projects/{project_id}/stop",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/stop \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"stopped\""}
        ],
        "responseJson": json.dumps({"status": "stopped"}, indent=2)
    },
    {
        "key": "api-projects-project-id-domain-post",
        "group": "Projects & Lifecycle",
        "title": "Bind custom domain",
        "summary": "Bind custom apex or subdomain with automatic SSL certificate provisioning and reverse proxy routing.",
        "method": "POST",
        "path": "/api/projects/{project_id}/domain",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "domain", "type": "string", "required": True, "desc": "Fully qualified domain name (e.g. app.example.com)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/domain \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"domain": "docs.sycord.site"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"bound\""},
            {"name": "domain", "type": "string", "desc": "Configured domain name."},
            {"name": "ssl_status", "type": "string", "desc": "\"active\" or \"pending_dns\"."}
        ],
        "responseJson": json.dumps({"status": "bound", "domain": "docs.sycord.site", "ssl_status": "active"}, indent=2)
    },
    {
        "key": "api-projects-project-id-domain-delete",
        "group": "Projects & Lifecycle",
        "title": "Unbind custom domain",
        "summary": "Remove custom domain binding and restore default platform subdomain routing.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}/domain",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/domain \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"unbound\""}
        ],
        "responseJson": json.dumps({"status": "unbound"}, indent=2)
    },
    {
        "key": "api-projects-project-id-environment-put",
        "group": "Projects & Lifecycle",
        "title": "Upsert environment variables",
        "summary": "Securely set or update environment variables and secrets injected into runtime container.",
        "method": "PUT",
        "path": "/api/projects/{project_id}/environment",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "variables", "type": "object", "required": True, "desc": "Key-value dictionary of environment variables."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/environment \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"variables": {"DATABASE_URL": "postgres://...", "NODE_ENV": "production"}}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""},
            {"name": "keys", "type": "array", "desc": "List of configured variable names."}
        ],
        "responseJson": json.dumps({"status": "saved", "keys": ["DATABASE_URL", "NODE_ENV"]}, indent=2)
    },
    {
        "key": "api-projects-project-id-environment-key-delete",
        "group": "Projects & Lifecycle",
        "title": "Delete environment variable",
        "summary": "Remove a specific environment variable from project configuration.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}/environment/{key}",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "key", "type": "string", "required": True, "desc": "Variable name to delete."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/environment/DATABASE_URL \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"deleted\""}
        ],
        "responseJson": json.dumps({"status": "deleted"}, indent=2)
    },
    {
        "key": "api-projects-project-id-health-get",
        "group": "Projects & Lifecycle",
        "title": "Check project health probe",
        "summary": "Perform direct HTTP health probe on project listener port to check readiness.",
        "method": "GET",
        "path": "/api/projects/{project_id}/health",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/health \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "healthy", "type": "boolean", "desc": "True if HTTP 200/300 was received from local port."},
            {"name": "response_time_ms", "type": "number", "desc": "Probe response latency in milliseconds."}
        ],
        "responseJson": json.dumps({"healthy": True, "response_time_ms": 12.4}, indent=2)
    },
    {
        "key": "api-projects-project-id-deployment-config-put",
        "group": "Projects & Lifecycle",
        "title": "Update deployment config",
        "summary": "Configure build command, start script, install command, and root output directory.",
        "method": "PUT",
        "path": "/api/projects/{project_id}/deployment-config",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "build_command", "type": "string", "required": False, "desc": "Build command (e.g. \"npm run build\")."},
            {"name": "start_command", "type": "string", "required": False, "desc": "Start command (e.g. \"npm run start\")."},
            {"name": "install_command", "type": "string", "required": False, "desc": "Install command (e.g. \"npm install\")."},
            {"name": "output_directory", "type": "string", "required": False, "desc": "Static output directory (e.g. \"dist\")."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/deployment-config \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"build_command": "npm run build", "start_command": "npm run start"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""}
        ],
        "responseJson": json.dumps({"status": "updated"}, indent=2)
    },
    {
        "key": "api-projects-project-id-analyze-post",
        "group": "Projects & Lifecycle",
        "title": "Analyze project source",
        "summary": "Inspect workspace files to auto-detect framework, package manager, and required start commands.",
        "method": "POST",
        "path": "/api/projects/{project_id}/analyze",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/analyze \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "framework", "type": "string", "desc": "Detected framework (e.g. \"nextjs\", \"fastapi\", \"astro\")."},
            {"name": "package_manager", "type": "string", "desc": "Detected tool (\"npm\", \"yarn\", \"pnpm\", \"pip\")."},
            {"name": "suggested_port", "type": "integer", "desc": "Recommended default listening port."}
        ],
        "responseJson": json.dumps({"framework": "nextjs", "package_manager": "pnpm", "suggested_port": 3000, "detected_scripts": ["build", "start", "dev"]}, indent=2)
    },
    {
        "key": "api-projects-project-id-deploy-detected-post",
        "group": "Projects & Lifecycle",
        "title": "Deploy detected framework",
        "summary": "Automatically apply detected build configuration and trigger initial deployment pipeline.",
        "method": "POST",
        "path": "/api/projects/{project_id}/deploy-detected",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deploy-detected \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "build_id", "type": "string", "desc": "Triggered build run ID."},
            {"name": "status", "type": "string", "desc": "\"queued\""}
        ],
        "responseJson": json.dumps({"build_id": "bld_77491", "status": "queued"}, indent=2)
    },

    # 7. Builds, Deployments & Logs
    {
        "key": "api-projects-project-id-builds-get",
        "group": "Builds & Deployments",
        "title": "List project builds",
        "summary": "Retrieve historical build records, git commit SHAs, build duration, and pass/fail statuses.",
        "method": "GET",
        "path": "/api/projects/{project_id}/builds",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "limit", "type": "integer", "required": False, "desc": "Number of records to return (default: 20)."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "builds", "type": "array", "desc": "Array of build execution objects."}
        ],
        "responseJson": json.dumps({"builds": [{"id": "bld_77491", "status": "success", "duration_seconds": 38, "commit_sha": "a19f201", "created_at": "2026-09-13T10:00:00Z"}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-builds-track-get",
        "group": "Builds & Deployments",
        "title": "Track active build",
        "summary": "Poll or track progress of currently executing build step and status.",
        "method": "GET",
        "path": "/api/projects/{project_id}/builds/track",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds/track \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "active", "type": "boolean", "desc": "True if build is currently running."},
            {"name": "step", "type": "string", "desc": "Current build step (\"installing\", \"building\", \"starting\")."},
            {"name": "elapsed_seconds", "type": "integer", "desc": "Seconds elapsed since build trigger."}
        ],
        "responseJson": json.dumps({"active": True, "step": "building", "elapsed_seconds": 18}, indent=2)
    },
    {
        "key": "api-projects-project-id-builds-trigger-post",
        "group": "Builds & Deployments",
        "title": "Trigger new build",
        "summary": "Enqueue an immediate new build and deployment execution for the project.",
        "method": "POST",
        "path": "/api/projects/{project_id}/builds/trigger",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/builds/trigger \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "build_id", "type": "string", "desc": "Unique build run ID."},
            {"name": "status", "type": "string", "desc": "\"queued\""}
        ],
        "responseJson": json.dumps({"build_id": "bld_77492", "status": "queued"}, indent=2)
    },
    {
        "key": "api-projects-project-id-builds-build-id-logs-get",
        "group": "Builds & Deployments",
        "title": "Get build run logs",
        "summary": "Retrieve complete build execution log output for a specific build ID.",
        "method": "GET",
        "path": "/api/projects/{project_id}/builds/{build_id}/logs",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "build_id", "type": "string", "required": True, "desc": "Build run ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/builds/bld_77491/logs \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "logs", "type": "string", "desc": "Full stdout/stderr build text output."}
        ],
        "responseJson": json.dumps({"logs": "[build] Installing dependencies...\n[build] Completed in 8.2s\n[build] Next.js 14 compiled successfully.\n[build] Artifact ready."}, indent=2)
    },
    {
        "key": "api-projects-project-id-deployments-build-id-logs-get",
        "group": "Builds & Deployments",
        "title": "Get deployment container logs",
        "summary": "Retrieve runtime stdout/stderr log output from container during specific deployment execution.",
        "method": "GET",
        "path": "/api/projects/{project_id}/deployments/{build_id}/logs",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "build_id", "type": "string", "required": True, "desc": "Deployment run ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/deployments/bld_77491/logs \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "logs", "type": "string", "desc": "Container runtime logs."}
        ],
        "responseJson": json.dumps({"logs": "Ready in 420ms on port 3000.\nGET / 200 12ms"}, indent=2)
    },
    {
        "key": "api-projects-project-id-deploy-post",
        "group": "Builds & Deployments",
        "title": "Issue immediate deploy",
        "summary": "Trigger immediate atomic production deployment without rebuild if artifact is fresh.",
        "method": "POST",
        "path": "/api/projects/{project_id}/deploy",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deploy \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "deployment_id", "type": "string", "desc": "Deployment run ID."},
            {"name": "status", "type": "string", "desc": "\"deployed\""}
        ],
        "responseJson": json.dumps({"deployment_id": "dep_19482", "status": "deployed"}, indent=2)
    },
    {
        "key": "api-projects-project-id-deployments-get",
        "group": "Builds & Deployments",
        "title": "List deployment revisions",
        "summary": "Retrieve deployment history list with commit tags, active production pointers, and rollback targets.",
        "method": "GET",
        "path": "/api/projects/{project_id}/deployments",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/deployments \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "deployments", "type": "array", "desc": "Array of deployment snapshots."}
        ],
        "responseJson": json.dumps({"deployments": [{"id": "dep_19482", "is_current": True, "commit_sha": "a19f201", "created_at": "2026-09-13T10:05:00Z"}, {"id": "dep_19480", "is_current": False, "commit_sha": "98e411b", "created_at": "2026-09-12T18:30:00Z"}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-deployments-run-id-rollback-post",
        "group": "Builds & Deployments",
        "title": "Rollback deployment",
        "summary": "Instantly switch active production traffic back to a previous healthy deployment snapshot.",
        "method": "POST",
        "path": "/api/projects/{project_id}/deployments/{run_id}/rollback",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "run_id", "type": "string", "required": True, "desc": "Target deployment revision ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/deployments/dep_19480/rollback \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"rolled_back\""},
            {"name": "active_deployment_id", "type": "string", "desc": "ID of newly activated revision."}
        ],
        "responseJson": json.dumps({"status": "rolled_back", "active_deployment_id": "dep_19480"}, indent=2)
    },
    {
        "key": "api-projects-project-id-logs-get",
        "group": "Builds & Deployments",
        "title": "Get container logs",
        "summary": "Fetch recent stdout and stderr lines from the running project container.",
        "method": "GET",
        "path": "/api/projects/{project_id}/logs",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "lines", "type": "integer", "required": False, "desc": "Number of tail lines to retrieve (default: 200)."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/logs?lines=100" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "logs", "type": "string", "desc": "Captured application log buffer."}
        ],
        "responseJson": json.dumps({"logs": "2026-09-13T12:00:01Z [INFO] Application listening on 0.0.0.0:3000\n2026-09-13T12:01:23Z [INFO] GET /api/v1/users 200 OK"}, indent=2)
    },
    {
        "key": "api-projects-project-id-logs-stream-get",
        "group": "Builds & Deployments",
        "title": "Stream container logs (SSE)",
        "summary": "Open real-time Server-Sent Events (SSE) connection to stream live container logs.",
        "method": "GET",
        "path": "/api/projects/{project_id}/logs/stream",
        "contentType": "text/event-stream",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -N -X GET https://sycord.site:8787/api/projects/proj_94821a/logs/stream \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "data", "type": "string", "desc": "Streaming log line chunk formatted as SSE message."}
        ],
        "responseJson": "data: {\"line\": \"[server] Request handled in 4ms\"}\n\ndata: {\"line\": \"[server] Cache hit for /static/bundle.js\"}\n\n"
    },
    {
        "key": "api-projects-project-id-update-post",
        "group": "Builds & Deployments",
        "title": "Pull Git update and rebuild",
        "summary": "Fetch latest commits from linked Git branch, reinstall dependencies, and restart project.",
        "method": "POST",
        "path": "/api/projects/{project_id}/update",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/update \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""},
            {"name": "commit_sha", "type": "string", "desc": "New head commit SHA."}
        ],
        "responseJson": json.dumps({"status": "updated", "commit_sha": "d98174f"}, indent=2)
    },

    # 8. Redirects & Proxy Rules
    {
        "key": "api-projects-project-id-redirects-get",
        "group": "Redirects & Routing",
        "title": "List redirect rules",
        "summary": "Retrieve all configured HTTP redirection and reverse proxy URL rewrite rules.",
        "method": "GET",
        "path": "/api/projects/{project_id}/redirects",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/redirects \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "redirects", "type": "array", "desc": "Array of redirect rule objects."}
        ],
        "responseJson": json.dumps({"redirects": [{"id": "red_01", "source_path": "/old-docs/:path*", "target_url": "/docs/:path*", "status_code": 301, "enabled": True}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-post",
        "group": "Redirects & Routing",
        "title": "Create redirect rule",
        "summary": "Add a new URL redirect or proxy rewrite rule with regex pattern matching.",
        "method": "POST",
        "path": "/api/projects/{project_id}/redirects",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "source_path", "type": "string", "required": True, "desc": "Source URL pattern (e.g. \"/blog/:slug\")."},
            {"name": "target_url", "type": "string", "required": True, "desc": "Target destination URL or path."},
            {"name": "status_code", "type": "integer", "required": False, "desc": "HTTP status code (301, 302, 307, 308; default: 301)."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"source_path": "/legacy", "target_url": "/new-v2", "status_code": 301}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "id", "type": "string", "desc": "Assigned redirect rule ID."},
            {"name": "status", "type": "string", "desc": "\"created\""}
        ],
        "responseJson": json.dumps({"id": "red_02", "status": "created"}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-redirect-id-put",
        "group": "Redirects & Routing",
        "title": "Update redirect rule",
        "summary": "Update source path, destination target, or status code of an existing redirect rule.",
        "method": "PUT",
        "path": "/api/projects/{project_id}/redirects/{redirect_id}",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "redirect_id", "type": "string", "required": True, "desc": "Redirect rule ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "source_path", "type": "string", "required": True, "desc": "Updated source pattern."},
            {"name": "target_url", "type": "string", "required": True, "desc": "Updated target URL."},
            {"name": "status_code", "type": "integer", "required": False, "desc": "HTTP status code."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"source_path": "/old-docs", "target_url": "/docs", "status_code": 308}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""}
        ],
        "responseJson": json.dumps({"status": "updated"}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-redirect-id-patch",
        "group": "Redirects & Routing",
        "title": "Toggle redirect rule status",
        "summary": "Enable or disable a redirect rule without modifying its configuration.",
        "method": "PATCH",
        "path": "/api/projects/{project_id}/redirects/{redirect_id}",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "redirect_id", "type": "string", "required": True, "desc": "Redirect rule ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "enabled", "type": "boolean", "required": True, "desc": "True to activate, false to pause rule."}
        ],
        "curlCommand": 'curl -X PATCH https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"enabled": true}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"updated\""}
        ],
        "responseJson": json.dumps({"status": "updated", "enabled": True}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-redirect-id-delete",
        "group": "Redirects & Routing",
        "title": "Delete redirect rule",
        "summary": "Remove a redirect rule from the edge proxy router.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}/redirects/{redirect_id}",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "redirect_id", "type": "string", "required": True, "desc": "Redirect rule ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/redirects/red_01 \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"deleted\""}
        ],
        "responseJson": json.dumps({"status": "deleted"}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-reorder-post",
        "group": "Redirects & Routing",
        "title": "Reorder redirect rules",
        "summary": "Set the sequential evaluation priority order for routing rules.",
        "method": "POST",
        "path": "/api/projects/{project_id}/redirects/reorder",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "ordered_ids", "type": "array", "required": True, "desc": "Array of redirect rule IDs in desired priority order."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/reorder \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"ordered_ids": ["red_02", "red_01"]}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"reordered\""}
        ],
        "responseJson": json.dumps({"status": "reordered"}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-bulk-post",
        "group": "Redirects & Routing",
        "title": "Bulk update redirect rules",
        "summary": "Add or replace multiple redirect rules in a single atomic transaction.",
        "method": "POST",
        "path": "/api/projects/{project_id}/redirects/bulk",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "rules", "type": "array", "required": True, "desc": "Array of redirect rule objects."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/bulk \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"rules": [{"source_path": "/a", "target_url": "/b", "status_code": 301}]}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""},
            {"name": "count", "type": "integer", "desc": "Number of rules saved."}
        ],
        "responseJson": json.dumps({"status": "saved", "count": 1}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-test-post",
        "group": "Redirects & Routing",
        "title": "Test redirect URL matching",
        "summary": "Simulate and test how a specific request URL resolves against current redirect rules.",
        "method": "POST",
        "path": "/api/projects/{project_id}/redirects/test",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "test_url", "type": "string", "required": True, "desc": "Incoming test URL path (e.g. \"/old-docs/guide\")."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/test \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"test_url": "/old-docs/intro"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "matched", "type": "boolean", "desc": "True if a rule matched."},
            {"name": "target_url", "type": "string", "desc": "Computed destination redirect URL."},
            {"name": "status_code", "type": "integer", "desc": "Resulting HTTP redirect status."}
        ],
        "responseJson": json.dumps({"matched": True, "target_url": "/docs/intro", "status_code": 301, "rule_id": "red_01"}, indent=2)
    },
    {
        "key": "api-projects-project-id-redirects-import-post",
        "group": "Redirects & Routing",
        "title": "Import redirects file",
        "summary": "Import redirect rules from a `_redirects` file, Netlify format, or JSON array.",
        "method": "POST",
        "path": "/api/projects/{project_id}/redirects/import",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "raw_content", "type": "string", "required": True, "desc": "Raw text content of `_redirects` file or JSON."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/redirects/import \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"raw_content": "/old /new 301\\n/home / 302"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "imported_count", "type": "integer", "desc": "Number of successfully imported rules."}
        ],
        "responseJson": json.dumps({"imported_count": 2, "status": "success"}, indent=2)
    },

    # 9. Analytics, Stats & Telemetry
    {
        "key": "api-projects-project-id-stats-get",
        "group": "Analytics & Telemetry",
        "title": "Real-time project stats",
        "summary": "Fetch real-time CPU percentage, memory consumption in MB, and active network connections.",
        "method": "GET",
        "path": "/api/projects/{project_id}/stats",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/stats \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "cpu_usage", "type": "number", "desc": "Container CPU percentage."},
            {"name": "memory_mb", "type": "number", "desc": "Resident RAM used in megabytes."},
            {"name": "uptime_seconds", "type": "integer", "desc": "Seconds elapsed since container start."}
        ],
        "responseJson": json.dumps({"cpu_usage": 3.8, "memory_mb": 112.5, "uptime_seconds": 86400}, indent=2)
    },
    {
        "key": "api-projects-project-id-performance-get",
        "group": "Analytics & Telemetry",
        "title": "Historical performance charts",
        "summary": "Retrieve time-series performance data points over the last 24 hours / 7 days for graphing.",
        "method": "GET",
        "path": "/api/projects/{project_id}/performance",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "range", "type": "string", "required": False, "desc": "\"1h\", \"24h\", \"7d\" (default: 24h)."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/performance?range=24h" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "series", "type": "array", "desc": "Time-series data points with timestamps, CPU and memory values."}
        ],
        "responseJson": json.dumps({"series": [{"timestamp": "2026-09-13T11:00:00Z", "cpu": 3.4, "memory": 110.2}, {"timestamp": "2026-09-13T12:00:00Z", "cpu": 4.1, "memory": 112.5}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-app-logs-get",
        "group": "Analytics & Telemetry",
        "title": "Application runtime logs",
        "summary": "Fetch parsed application standard output and error log streams with severity levels.",
        "method": "GET",
        "path": "/api/projects/{project_id}/app-logs",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/app-logs \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "lines", "type": "array", "desc": "Array of parsed log line objects."}
        ],
        "responseJson": json.dumps({"lines": [{"timestamp": "2026-09-13T12:00:01Z", "level": "info", "message": "Server listening on 3000"}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-router-logs-get",
        "group": "Analytics & Telemetry",
        "title": "HTTP edge proxy access logs",
        "summary": "Fetch reverse proxy HTTP access logs including client IP, status code, response time, and user agent.",
        "method": "GET",
        "path": "/api/projects/{project_id}/router-logs",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "limit", "type": "integer", "required": False, "desc": "Max entries to fetch (default: 100)."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/router-logs \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "requests", "type": "array", "desc": "Array of HTTP access log entries."}
        ],
        "responseJson": json.dumps({"requests": [{"timestamp": "2026-09-13T12:05:10Z", "ip": "1.2.3.4", "method": "GET", "path": "/docs", "status": 200, "duration_ms": 14}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-visitors-get",
        "group": "Analytics & Telemetry",
        "title": "Visitor geographic telemetry",
        "summary": "Get aggregate geographic visitor countries, unique IP counts, and referrer distribution.",
        "method": "GET",
        "path": "/api/projects/{project_id}/visitors",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/visitors \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "countries", "type": "object", "desc": "Country ISO codes mapped to visitor counts."},
            {"name": "unique_visitors", "type": "integer", "desc": "Total unique visitors in time window."}
        ],
        "responseJson": json.dumps({"unique_visitors": 1420, "countries": {"US": 620, "DE": 280, "FR": 190, "GB": 150}}, indent=2)
    },
    {
        "key": "api-projects-project-id-analytics-get",
        "group": "Analytics & Telemetry",
        "title": "HTTP status & latency metrics",
        "summary": "Breakdown of HTTP 2xx, 3xx, 4xx, 5xx status codes, p95 latency, and total bandwidth transferred.",
        "method": "GET",
        "path": "/api/projects/{project_id}/analytics",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/analytics \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "total_requests", "type": "integer", "desc": "Total requests served."},
            {"name": "status_codes", "type": "object", "desc": "Status code count distribution."},
            {"name": "p95_latency_ms", "type": "number", "desc": "95th percentile latency."}
        ],
        "responseJson": json.dumps({"total_requests": 48290, "status_codes": {"200": 47100, "304": 950, "404": 210, "500": 30}, "p95_latency_ms": 18.5}, indent=2)
    },

    # 10. Release Management & Previews
    {
        "key": "api-projects-project-id-release-get",
        "group": "Release & Previews",
        "title": "Get release workspace",
        "summary": "Retrieve deployment environments (production, staging), policies, approval workflows, and active restore points.",
        "method": "GET",
        "path": "/api/projects/{project_id}/release",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/release \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "environments", "type": "array", "desc": "List of environments (production, staging)."},
            {"name": "policy", "type": "object", "desc": "Release approval policy object."}
        ],
        "responseJson": json.dumps({"environments": [{"id": "env_prod", "name": "production", "auto_deploy": False}, {"id": "env_stg", "name": "staging", "auto_deploy": True}], "policy": {"required_approvals": 1, "enforce_tests": True}}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-environments-env-id-put",
        "group": "Release & Previews",
        "title": "Update release environment",
        "summary": "Configure environment branch targets, auto-deploy toggles, and variable overrides.",
        "method": "PUT",
        "path": "/api/projects/{project_id}/release/environments/{environment_id}",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "environment_id", "type": "string", "required": True, "desc": "Environment ID (e.g. \"env_prod\")."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "target_branch", "type": "string", "required": False, "desc": "Target git branch name."},
            {"name": "auto_deploy", "type": "boolean", "required": False, "desc": "Enable automatic deploy on push."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/release/environments/env_prod \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"target_branch": "main", "auto_deploy": false}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""}
        ],
        "responseJson": json.dumps({"status": "saved"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-policy-put",
        "group": "Release & Previews",
        "title": "Update release policy",
        "summary": "Set deployment guardrails, required peer approvals, and pre-deploy smoke test requirements.",
        "method": "PUT",
        "path": "/api/projects/{project_id}/release/policy",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "required_approvals", "type": "integer", "required": False, "desc": "Number of sign-offs needed."},
            {"name": "enforce_tests", "type": "boolean", "required": False, "desc": "Require passing automated tests."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/proj_94821a/release/policy \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"required_approvals": 1, "enforce_tests": true}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"policy_updated\""}
        ],
        "responseJson": json.dumps({"status": "policy_updated"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-team-post",
        "group": "Release & Previews",
        "title": "Upsert release team member",
        "summary": "Add or update team member roles and deployment approval permissions.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/team",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "user_id", "type": "string", "required": True, "desc": "User ID to add."},
            {"name": "role", "type": "string", "required": True, "desc": "\"lead\", \"reviewer\", or \"developer\"."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/team \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"user_id": "usr_david", "role": "reviewer"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"member_added\""}
        ],
        "responseJson": json.dumps({"status": "member_added", "user_id": "usr_david", "role": "reviewer"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-team-member-id-delete",
        "group": "Release & Previews",
        "title": "Remove release team member",
        "summary": "Revoke deployment approval authority from a user.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}/release/team/{member_id}",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "member_id", "type": "string", "required": True, "desc": "Team member record ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/proj_94821a/release/team/mem_81 \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"member_removed\""}
        ],
        "responseJson": json.dumps({"status": "member_removed"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-approvals-post",
        "group": "Release & Previews",
        "title": "Request release approval",
        "summary": "Submit a formal release deployment request to team reviewers.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/approvals",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "target_env", "type": "string", "required": True, "desc": "\"production\" or \"staging\"."},
            {"name": "commit_sha", "type": "string", "required": True, "desc": "Git commit SHA to release."},
            {"name": "notes", "type": "string", "required": False, "desc": "Release notes for reviewer."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/approvals \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"target_env": "production", "commit_sha": "a19f201", "notes": "Bug fixes"}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "approval_id", "type": "string", "desc": "Approval request ID."},
            {"name": "status", "type": "string", "desc": "\"pending_review\""}
        ],
        "responseJson": json.dumps({"approval_id": "appr_9918", "status": "pending_review"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-approvals-decision-post",
        "group": "Release & Previews",
        "title": "Submit approval decision",
        "summary": "Approve or reject a pending release deployment request.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/approvals/{approval_id}/decision",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "approval_id", "type": "string", "required": True, "desc": "Approval request ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "decision", "type": "string", "required": True, "desc": "\"approved\" or \"rejected\"."},
            {"name": "comment", "type": "string", "required": False, "desc": "Reviewer comments."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/approvals/appr_9918/decision \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"decision": "approved", "comment": "Verified and passed QA"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"approved\""}
        ],
        "responseJson": json.dumps({"status": "approved", "approval_id": "appr_9918"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-restore-points-post",
        "group": "Release & Previews",
        "title": "Create restore snapshot",
        "summary": "Create an immutable system snapshot of workspace files, database, and container image for rapid recovery.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/restore-points",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "label", "type": "string", "required": False, "desc": "Snapshot label (e.g. \"Pre-v2.0 migration\")."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/restore-points \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"label": "Pre-v2.0 migration"}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "restore_point_id", "type": "string", "desc": "Created snapshot ID."},
            {"name": "size_bytes", "type": "integer", "desc": "Total snapshot archive size."}
        ],
        "responseJson": json.dumps({"restore_point_id": "snp_94812", "label": "Pre-v2.0 migration", "size_bytes": 104857600}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-restore-points-verify-post",
        "group": "Release & Previews",
        "title": "Verify restore snapshot",
        "summary": "Verify checksum integrity and restore capability of a saved snapshot archive.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/restore-points/{restore_point_id}/verify",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."},
            {"name": "restore_point_id", "type": "string", "required": True, "desc": "Snapshot ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/restore-points/snp_94812/verify \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "valid", "type": "boolean", "desc": "True if snapshot passed checksum and integrity check."}
        ],
        "responseJson": json.dumps({"valid": True, "checksum": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-preview-start-post",
        "group": "Release & Previews",
        "title": "Start release preview",
        "summary": "Spawn an isolated ephemeral sandbox preview for validating a proposed release.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/preview/start",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "commit_sha", "type": "string", "required": True, "desc": "Git commit SHA to spin up."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/preview/start \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"commit_sha": "a19f201"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "preview_url", "type": "string", "desc": "Isolated ephemeral HTTPS preview URL."},
            {"name": "port", "type": "integer", "desc": "Allocated temporary port."}
        ],
        "responseJson": json.dumps({"preview_url": "https://preview-a19f201.sycord.site", "port": 39042, "status": "running"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-preview-stop-post",
        "group": "Release & Previews",
        "title": "Stop release preview",
        "summary": "Terminate and tear down an ephemeral preview container.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/preview/stop",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/preview/stop \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"preview_stopped\""}
        ],
        "responseJson": json.dumps({"status": "preview_stopped"}, indent=2)
    },
    {
        "key": "api-projects-project-id-release-deploy-post",
        "group": "Release & Previews",
        "title": "Execute release deploy",
        "summary": "Execute approved release deployment with zero downtime swap.",
        "method": "POST",
        "path": "/api/projects/{project_id}/release/deploy",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "approval_id", "type": "string", "required": True, "desc": "Approved release ID."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/release/deploy \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"approval_id": "appr_9918"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"deployed\""},
            {"name": "deployment_id", "type": "string", "desc": "Production deployment run ID."}
        ],
        "responseJson": json.dumps({"status": "deployed", "deployment_id": "dep_19485"}, indent=2)
    },
    {
        "key": "api-projects-project-id-preview-start-post",
        "group": "Release & Previews",
        "title": "Start interactive preview",
        "summary": "Start interactive live preview sandbox container for immediate browser testing.",
        "method": "POST",
        "path": "/api/projects/{project_id}/preview/start",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/preview/start \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"running\""},
            {"name": "preview_url", "type": "string", "desc": "Sandbox preview iframe URL."}
        ],
        "responseJson": json.dumps({"status": "running", "preview_url": "https://sycord.site:8787/preview/proj_94821a"}, indent=2)
    },
    {
        "key": "api-projects-project-id-preview-stop-post",
        "group": "Release & Previews",
        "title": "Stop interactive preview",
        "summary": "Halt and tear down interactive sandbox preview session.",
        "method": "POST",
        "path": "/api/projects/{project_id}/preview/stop",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/preview/stop \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"stopped\""}
        ],
        "responseJson": json.dumps({"status": "stopped"}, indent=2)
    },
    {
        "key": "api-projects-project-id-preview-status-get",
        "group": "Release & Previews",
        "title": "Get preview status",
        "summary": "Check if sandbox preview process is running, responsive, and ready for iframe rendering.",
        "method": "GET",
        "path": "/api/projects/{project_id}/preview/status",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/status \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "running", "type": "boolean", "desc": "True if preview process is running."},
            {"name": "ready", "type": "boolean", "desc": "True if HTTP port is responding with 200."}
        ],
        "responseJson": json.dumps({"running": True, "ready": True, "port": 34100}, indent=2)
    },
    {
        "key": "api-projects-project-id-preview-iframe-check-get",
        "group": "Release & Previews",
        "title": "Check preview iframe headers",
        "summary": "Inspect X-Frame-Options and Content-Security-Policy headers on target preview port.",
        "method": "GET",
        "path": "/api/projects/{project_id}/preview/iframe-check",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/iframe-check \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "embeddable", "type": "boolean", "desc": "True if headers permit iframe preview rendering."}
        ],
        "responseJson": json.dumps({"embeddable": True}, indent=2)
    },
    {
        "key": "api-projects-project-id-preview-logs-stream-get",
        "group": "Release & Previews",
        "title": "Stream preview logs (SSE)",
        "summary": "Real-time SSE event stream of stdout logs from sandbox preview server.",
        "method": "GET",
        "path": "/api/projects/{project_id}/preview/logs/stream",
        "contentType": "text/event-stream",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -N -X GET https://sycord.site:8787/api/projects/proj_94821a/preview/logs/stream \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "data", "type": "string", "desc": "Real-time SSE preview log chunk."}
        ],
        "responseJson": "data: {\"preview_log\": \"Compiled 42 modules in 120ms\"}\n\n"
    },

    # 11. Git & Workspace Files
    {
        "key": "api-projects-git-github-status-get",
        "group": "Git & Workspace Files",
        "title": "GitHub connection status",
        "summary": "Check if user has linked their personal or organization GitHub account.",
        "method": "GET",
        "path": "/api/projects/git/github/status",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/git/github/status \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "connected", "type": "boolean", "desc": "True if connected to GitHub OAuth."},
            {"name": "account", "type": "string", "desc": "GitHub username or organization handle."}
        ],
        "responseJson": json.dumps({"connected": True, "account": "MDavidka"}, indent=2)
    },
    {
        "key": "api-projects-git-github-config-put",
        "group": "Git & Workspace Files",
        "title": "Configure GitHub OAuth",
        "summary": "Save GitHub App Client ID and Secret for repository imports.",
        "method": "PUT",
        "path": "/api/projects/git/github/config",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "client_id", "type": "string", "required": True, "desc": "GitHub OAuth Client ID."},
            {"name": "client_secret", "type": "string", "required": True, "desc": "GitHub OAuth Client Secret."}
        ],
        "curlCommand": 'curl -X PUT https://sycord.site:8787/api/projects/git/github/config \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"client_id": "Iv1.8941829abc", "client_secret": "sec_gh_8921"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""}
        ],
        "responseJson": json.dumps({"status": "saved"}, indent=2)
    },
    {
        "key": "api-projects-git-github-connect-get",
        "group": "Git & Workspace Files",
        "title": "Initiate GitHub OAuth",
        "summary": "Generate OAuth redirect URL to authenticate with GitHub and grant repo permissions.",
        "method": "GET",
        "path": "/api/projects/git/github/connect",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/git/github/connect \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "redirect_url", "type": "string", "desc": "GitHub authorization URL with state nonce."}
        ],
        "responseJson": json.dumps({"redirect_url": "https://github.com/login/oauth/authorize?client_id=Iv1...&scope=repo"}, indent=2)
    },
    {
        "key": "api-projects-git-github-callback-get",
        "group": "Git & Workspace Files",
        "title": "Handle GitHub callback",
        "summary": "Exchange temporary OAuth code for persistent user access token.",
        "method": "GET",
        "path": "/api/projects/git/github/callback",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [
            {"name": "code", "type": "string", "required": True, "desc": "OAuth code from GitHub."},
            {"name": "state", "type": "string", "required": True, "desc": "State nonce for CSRF protection."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/projects/git/github/callback?code=gh_code_8192&state=nonce_99" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"connected\""},
            {"name": "username", "type": "string", "desc": "Authenticated GitHub user handle."}
        ],
        "responseJson": json.dumps({"status": "connected", "username": "MDavidka"}, indent=2)
    },
    {
        "key": "api-projects-git-github-disconnect-delete",
        "group": "Git & Workspace Files",
        "title": "Disconnect GitHub account",
        "summary": "Revoke stored GitHub OAuth tokens and disconnect linked account.",
        "method": "DELETE",
        "path": "/api/projects/git/github/disconnect",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE https://sycord.site:8787/api/projects/git/github/disconnect \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"disconnected\""}
        ],
        "responseJson": json.dumps({"status": "disconnected"}, indent=2)
    },
    {
        "key": "api-projects-git-github-repositories-get",
        "group": "Git & Workspace Files",
        "title": "List GitHub repositories",
        "summary": "Fetch public and private repositories accessible via linked GitHub token.",
        "method": "GET",
        "path": "/api/projects/git/github/repositories",
        "contentType": "none",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/git/github/repositories \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "repositories", "type": "array", "desc": "Array of repository objects (full_name, private, default_branch)."}
        ],
        "responseJson": json.dumps({"repositories": [{"full_name": "MDavidka/sarra", "private": False, "default_branch": "main", "language": "Python"}]}, indent=2)
    },
    {
        "key": "api-projects-git-github-repositories-branches-get",
        "group": "Git & Workspace Files",
        "title": "List repository branches",
        "summary": "Fetch all git branches for a specific GitHub repository.",
        "method": "GET",
        "path": "/api/projects/git/github/repositories/{repository:path}/branches",
        "contentType": "none",
        "pathParams": [
            {"name": "repository", "type": "string", "required": True, "desc": "Full repository path (e.g. \"MDavidka/sarra\")."}
        ],
        "queryParams": [],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/git/github/repositories/MDavidka/sarra/branches \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "branches", "type": "array", "desc": "List of branch names and commit SHAs."}
        ],
        "responseJson": json.dumps({"branches": [{"name": "main", "commit": {"sha": "a19f201"}}, {"name": "feat/mobile-header-and-sidebar-accuracy", "commit": {"sha": "94812aa"}}]}, indent=2)
    },
    {
        "key": "api-projects-import-github-post",
        "group": "Git & Workspace Files",
        "title": "Import GitHub repository",
        "summary": "Clone repository from GitHub into a new project workspace and setup automatic deployment webhooks.",
        "method": "POST",
        "path": "/api/projects/import/github",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "repo", "type": "string", "required": True, "desc": "Full repository name (e.g. \"MDavidka/sarra\")."},
            {"name": "branch", "type": "string", "required": False, "desc": "Branch name (default: default_branch)."},
            {"name": "name", "type": "string", "required": False, "desc": "Optional project slug name."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/import/github \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"repo": "MDavidka/sarra", "branch": "main"}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "project_id", "type": "string", "desc": "Created project ID."},
            {"name": "status", "type": "string", "desc": "\"imported\""}
        ],
        "responseJson": json.dumps({"project_id": "proj_94821a", "name": "sarra", "status": "imported"}, indent=2)
    },
    {
        "key": "api-projects-import-repository-post",
        "group": "Git & Workspace Files",
        "title": "Import public Git repository",
        "summary": "Clone any public Git repository via HTTPS URL into a fresh workspace.",
        "method": "POST",
        "path": "/api/projects/import/repository",
        "contentType": "application/json",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "git_url", "type": "string", "required": True, "desc": "Public Git HTTPS clone URL."},
            {"name": "branch", "type": "string", "required": False, "desc": "Target branch to check out."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/import/repository \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"git_url": "https://github.com/vercel/next.js.git", "branch": "canary"}\'',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "project_id", "type": "string", "desc": "Created project ID."},
            {"name": "status", "type": "string", "desc": "\"cloned\""}
        ],
        "responseJson": json.dumps({"project_id": "proj_88192a", "status": "cloned"}, indent=2)
    },
    {
        "key": "api-projects-import-zip-post",
        "group": "Git & Workspace Files",
        "title": "Upload project ZIP archive",
        "summary": "Upload and unpack a ZIP archive of source code directly into project workspace.",
        "method": "POST",
        "path": "/api/projects/import/zip",
        "contentType": "multipart/form-data",
        "pathParams": [],
        "queryParams": [],
        "bodyParams": [
            {"name": "file", "type": "binary", "required": True, "desc": "ZIP archive binary stream."},
            {"name": "name", "type": "string", "required": False, "desc": "Optional project slug name."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/import/zip \\\n  -H "Authorization: Bearer <token>" \\\n  -F "file=@project-source.zip"',
        "responseStatus": "201 Created",
        "responseSchema": [
            {"name": "project_id", "type": "string", "desc": "Created project ID."},
            {"name": "files_extracted", "type": "integer", "desc": "Count of extracted files."}
        ],
        "responseJson": json.dumps({"project_id": "proj_55219a", "status": "extracted", "files_extracted": 48}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-files-get",
        "group": "Git & Workspace Files",
        "title": "List workspace file tree",
        "summary": "Retrieve hierarchical file tree and directory structure of project workspace.",
        "method": "GET",
        "path": "/api/projects/{project_id}/workspace/files",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "path", "type": "string", "required": False, "desc": "Subdirectory path to list (default: root)."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET https://sycord.site:8787/api/projects/proj_94821a/workspace/files \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "files", "type": "array", "desc": "Array of file and folder nodes with size, path, and type."}
        ],
        "responseJson": json.dumps({"files": [{"name": "package.json", "type": "file", "size": 1024, "path": "package.json"}, {"name": "src", "type": "directory", "path": "src"}]}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-file-get",
        "group": "Git & Workspace Files",
        "title": "Read workspace file",
        "summary": "Read UTF-8 text content of a specific source code file.",
        "method": "GET",
        "path": "/api/projects/{project_id}/workspace/file",
        "contentType": "none",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "path", "type": "string", "required": True, "desc": "Relative file path inside workspace (e.g. \"package.json\")."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X GET "https://sycord.site:8787/api/projects/proj_94821a/workspace/file?path=package.json" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "content", "type": "string", "desc": "Raw text file content."},
            {"name": "size", "type": "integer", "desc": "File size in bytes."}
        ],
        "responseJson": json.dumps({"path": "package.json", "content": "{\n  \"name\": \"sarra-app\",\n  \"version\": \"1.0.0\"\n}", "size": 42}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-file-post",
        "group": "Git & Workspace Files",
        "title": "Write workspace file",
        "summary": "Save or update text content of a file in the workspace.",
        "method": "POST",
        "path": "/api/projects/{project_id}/workspace/file",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "path", "type": "string", "required": True, "desc": "Relative file path."},
            {"name": "content", "type": "string", "required": True, "desc": "New file content to write."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/file \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"path": "config.json", "content": "{\\"port\\": 3000}"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"saved\""},
            {"name": "bytes_written", "type": "integer", "desc": "Number of bytes written to disk."}
        ],
        "responseJson": json.dumps({"status": "saved", "bytes_written": 16}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-mkdir-post",
        "group": "Git & Workspace Files",
        "title": "Create folder in workspace",
        "summary": "Create a new subdirectory directory in project workspace.",
        "method": "POST",
        "path": "/api/projects/{project_id}/workspace/mkdir",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "path", "type": "string", "required": True, "desc": "Relative directory path to create."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/mkdir \\\n  -H "Authorization: Bearer <token>" \\\n  -H "Content-Type: application/json" \\\n  -d \'{"path": "src/components"}\'',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"created\""}
        ],
        "responseJson": json.dumps({"status": "created", "path": "src/components"}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-file-delete",
        "group": "Git & Workspace Files",
        "title": "Delete workspace file or folder",
        "summary": "Permanently delete a file or directory from the workspace.",
        "method": "DELETE",
        "path": "/api/projects/{project_id}/workspace/file",
        "contentType": "application/json",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [
            {"name": "path", "type": "string", "required": True, "desc": "Relative path of file or folder to delete."}
        ],
        "bodyParams": [],
        "curlCommand": 'curl -X DELETE "https://sycord.site:8787/api/projects/proj_94821a/workspace/file?path=temp.log" \\\n  -H "Authorization: Bearer <token>"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"deleted\""}
        ],
        "responseJson": json.dumps({"status": "deleted"}, indent=2)
    },
    {
        "key": "api-projects-project-id-workspace-upload-post",
        "group": "Git & Workspace Files",
        "title": "Upload workspace file",
        "summary": "Upload a binary or text file to a specific destination in project workspace.",
        "method": "POST",
        "path": "/api/projects/{project_id}/workspace/upload",
        "contentType": "multipart/form-data",
        "pathParams": [
            {"name": "project_id", "type": "string", "required": True, "desc": "Project ID."}
        ],
        "queryParams": [],
        "bodyParams": [
            {"name": "file", "type": "binary", "required": True, "desc": "File payload multipart stream."},
            {"name": "destination_path", "type": "string", "required": True, "desc": "Relative target folder path."}
        ],
        "curlCommand": 'curl -X POST https://sycord.site:8787/api/projects/proj_94821a/workspace/upload \\\n  -H "Authorization: Bearer <token>" \\\n  -F "file=@logo.png" \\\n  -F "destination_path=public/"',
        "responseStatus": "200 OK",
        "responseSchema": [
            {"name": "status", "type": "string", "desc": "\"uploaded\""},
            {"name": "file_path", "type": "string", "desc": "Saved file path in workspace."}
        ],
        "responseJson": json.dumps({"status": "uploaded", "file_path": "public/logo.png"}, indent=2)
    }
]

print(f"Total curated endpoints: {len(API_ENDPOINTS)}")
