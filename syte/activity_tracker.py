"""Activity Tracker and API-only log monitor for Syte.

Tracks incoming API requests and records short-format log entries:
[sycord.site/api/<route>] <time> - error log

Features:
- Only logs incoming API requests (/api/...). Ignores static files, HTML, assets.
- Redacts all environment variable names, keys, tokens, and credentials with [red].
- Rolling in-memory buffer for the latest 10 minutes.
- Background ticker refreshing system metrics every 5 seconds even when closed.
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import shutil
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Tuple

from starlette.types import ASGIApp, Receive, Scope, Send

_WINDOW_SECONDS = 600.0  # 10 minutes

BOT_PATTERNS = re.compile(
    r"(bot|crawler|spider|scraper|crawl|slurp|mediapartners|googlebot|bingbot|yandex|duckduckbot|baiduspider|curl|wget|python-requests|aiohttp|httpx|httpclient)",
    re.IGNORECASE,
)

SUSPICIOUS_PATH_PATTERNS = re.compile(
    r"(\.\./|\.\.\\|\.env|\.git|\.aws|\.ssh|wp-admin|phpmyadmin|actuator|eval\(|base64|/etc/passwd|/proc/|config\.json\.bak|\.tar\.gz|\.sql)",
    re.IGNORECASE,
)

ENV_PARAM_PATTERNS = re.compile(
    r"(?i)(key|env|env_var|val|value|secret|token|password|api_key|auth)=([^& \t\n\r]+)"
)
ENV_PATH_PATTERNS = re.compile(
    r"(?i)(/environment/|/env/|/secrets/|/tokens/|/token/)([^/?# \t\n\r]+)"
)


def sanitize_text(text: str | None) -> str:
    """Mask any sensitive keys, credentials, or environment names with [red]."""
    if not text:
        return ""
    res = ENV_PARAM_PATTERNS.sub(r"\1=[red]", text)
    res = ENV_PATH_PATTERNS.sub(r"\1[red]", res)
    res = re.sub(r"\b(?:syte|sk|ghp|gho)_[A-Za-z0-9_\-]{8,}\b", "[red]", res)
    res = re.sub(r"(?i)bearer\s+[A-Za-z0-9_\-\.]+", "Bearer [red]", res)
    return res


def sanitize_path(path: str) -> str:
    """Sanitize URL path to never leak sensitive environment variable names."""
    return ENV_PATH_PATTERNS.sub(r"\1[red]", path)


def sanitize_email(email: str) -> str:
    """Mask email for privacy in public debug logs (e.g. ad***@localhost)."""
    if not email or "@" not in email:
        return "[red]"
    user, domain = email.split("@", 1)
    if len(user) <= 2:
        masked_user = user[0] + "*"
    else:
        masked_user = user[:2] + "***"
    return f"{masked_user}@{domain}"


def is_api_path(path: str) -> bool:
    """Check if the given path is an incoming API endpoint."""
    p = path.lower()
    return p.startswith(("/api/", "/api")) or p in {"/api"}


class ApiLogEntry:
    __slots__ = (
        "timestamp",
        "time_str",
        "method",
        "route",
        "status_code",
        "duration_ms",
        "error_message",
        "short_log",
        "client_ip",
        "user_agent",
        "categories",
    )

    def __init__(
        self,
        timestamp: float,
        method: str,
        path: str,
        status_code: int,
        duration_ms: float,
        client_ip: str,
        user_agent: str,
        categories: list[str],
        error_message: str | None = None,
    ) -> None:
        self.timestamp = timestamp
        self.time_str = datetime.fromtimestamp(timestamp, timezone.utc).strftime("%H:%M:%S UTC")
        self.method = method
        self.route = sanitize_path(path)
        self.status_code = status_code
        self.duration_ms = duration_ms
        self.client_ip = client_ip
        self.user_agent = user_agent
        self.categories = categories
        self.error_message = sanitize_text(error_message) if error_message else None

        # Build short format: [sycord.site/api/<route>] <time> - error log
        # clean route format: strip double /api if present
        clean_route = self.route.lstrip("/")
        if clean_route.startswith("api/"):
            route_suffix = clean_route[4:]
        elif clean_route == "api":
            route_suffix = ""
        else:
            route_suffix = clean_route

        err_part = self.error_message if self.error_message else f"HTTP {status_code}"
        self.short_log = f"[sycord.site/api/{route_suffix}] {self.time_str} - {err_part}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "log": self.short_log,
            "route": f"sycord.site/api/{self.route.lstrip('/').removeprefix('api/').removeprefix('api')}",
            "time": self.time_str,
            "error": self.error_message or f"HTTP {self.status_code}",
            "status_code": self.status_code,
            "method": self.method,
            "duration_ms": round(self.duration_ms, 2),
            "categories": self.categories,
            "client_ip": self.client_ip,
        }


class LoginEvent:
    __slots__ = ("timestamp", "email", "success", "ip", "detail")

    def __init__(self, timestamp: float, email: str, success: bool, ip: str, detail: str = "") -> None:
        self.timestamp = timestamp
        self.email = sanitize_email(email)
        self.success = success
        self.ip = ip
        self.detail = sanitize_text(detail)

    def to_dict(self) -> dict[str, Any]:
        return {
            "time": datetime.fromtimestamp(self.timestamp, timezone.utc).strftime("%H:%M:%S UTC"),
            "email": self.email,
            "success": self.success,
            "ip": self.ip,
            "detail": self.detail,
        }


class ActivityTracker:
    def __init__(self) -> None:
        self._events: Deque[ApiLogEntry] = deque(maxlen=5000)
        self._logins: Deque[LoginEvent] = deque(maxlen=500)
        self._lock = asyncio.Lock()
        self._start_time = time.time()
        self._latest_vm_metrics: dict[str, Any] = {}
        self._last_metrics_refresh = 0.0

    def record_request(
        self,
        method: str,
        path: str,
        status_code: int,
        duration_s: float,
        client_ip: str,
        user_agent: str,
        bytes_in: int = 0,
        bytes_out: int = 0,
        error_msg: str | None = None,
    ) -> ApiLogEntry | None:
        # Only log incoming API requests. Ignore non-API things.
        if not is_api_path(path):
            return None

        now = time.time()
        categories: list[str] = []

        is_bot = bool(BOT_PATTERNS.search(user_agent))
        if is_bot:
            categories.append("bots")

        is_suspicious = bool(SUSPICIOUS_PATH_PATTERNS.search(path))
        if is_suspicious or (status_code == 401 and "login" not in path.lower() and not path.startswith("/api/auth")):
            categories.append("suspicion")

        if status_code == 422 or (status_code == 400 and "malformed" in (error_msg or "").lower()):
            categories.append("malformed")

        if status_code >= 500:
            categories.append("failed")

        if duration_s >= 5.0:
            categories.append("stale")

        if status_code == 413 or bytes_in > 1024 * 1024 or bytes_out > 1024 * 1024:
            categories.append("too many data")

        if status_code < 400 and not categories:
            categories.append("correct")
        elif status_code < 400 and "bots" in categories and len(categories) == 1:
            categories.append("correct")

        # Determine error log message if not set
        if not error_msg and status_code >= 400:
            error_msg = f"HTTP {status_code}"

        entry = ApiLogEntry(
            timestamp=now,
            method=method,
            path=path,
            status_code=status_code,
            duration_ms=duration_s * 1000.0,
            client_ip=client_ip,
            user_agent=user_agent,
            categories=categories,
            error_message=error_msg,
        )

        self._events.append(entry)
        return entry

    def record_login(self, email: str, success: bool, ip: str, detail: str = "") -> None:
        self._logins.append(LoginEvent(time.time(), email, success, ip, detail))

    def record_internal_error(self, error_type: str, message: str, path: str) -> None:
        if is_api_path(path):
            now = time.time()
            entry = ApiLogEntry(
                timestamp=now,
                method="ERROR",
                path=path,
                status_code=500,
                duration_ms=0.0,
                client_ip="internal",
                user_agent="server",
                categories=["failed"],
                error_message=f"{error_type}: {message}",
            )
            self._events.append(entry)

    def _prune(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()
        while self._logins and self._logins[0].timestamp < cutoff:
            self._logins.popleft()

    def refresh_metrics(self) -> dict[str, Any]:
        """Collect and cache real-time VM resources every 5 seconds."""
        total_ram_mb = 0.0
        free_ram_mb = 0.0
        used_ram_mb = 0.0
        ram_percent = 0.0

        try:
            page_size = os.sysconf("SC_PAGE_SIZE")
            phys_pages = os.sysconf("SC_PHYS_PAGES")
            avail_pages = os.sysconf("SC_AVPHYS_PAGES")
            total_ram_mb = round((page_size * phys_pages) / (1024 * 1024), 2)
            free_ram_mb = round((page_size * avail_pages) / (1024 * 1024), 2)
            used_ram_mb = round(total_ram_mb - free_ram_mb, 2)
            ram_percent = round((used_ram_mb / total_ram_mb) * 100.0, 2) if total_ram_mb > 0 else 0.0
        except Exception:
            pass

        cpu_count = os.cpu_count() or 1
        load_1, load_5, load_15 = (0.0, 0.0, 0.0)
        try:
            load_1, load_5, load_15 = os.getloadavg()
        except OSError:
            pass

        cpu_percent = min(100.0, round((load_1 / cpu_count) * 100.0, 2))

        disk_total_gb = 0.0
        disk_used_gb = 0.0
        disk_free_gb = 0.0
        disk_percent = 0.0
        try:
            du = shutil.disk_usage("/")
            disk_total_gb = round(du.total / (1024**3), 2)
            disk_used_gb = round(du.used / (1024**3), 2)
            disk_free_gb = round(du.free / (1024**3), 2)
            disk_percent = round((du.used / du.total) * 100.0, 2) if du.total > 0 else 0.0
        except Exception:
            pass

        uptime_seconds = 0.0
        try:
            with open("/proc/uptime", "r") as f:
                uptime_seconds = float(f.readline().split()[0])
        except Exception:
            uptime_seconds = time.time() - self._start_time

        high_resource_warnings: list[str] = []
        if cpu_percent >= 80.0:
            high_resource_warnings.append(f"High CPU: {cpu_percent}%")
        if ram_percent >= 80.0:
            high_resource_warnings.append(f"High RAM: {ram_percent}%")
        if disk_percent >= 80.0:
            high_resource_warnings.append(f"High Disk: {disk_percent}%")

        metrics = {
            "hostname": platform.node(),
            "cpu_cores": cpu_count,
            "load_1m": round(load_1, 2),
            "cpu_usage_percent": cpu_percent,
            "ram_mb": {
                "total": total_ram_mb,
                "used": used_ram_mb,
                "usage_percent": ram_percent,
            },
            "disk_gb": {
                "total": disk_total_gb,
                "used": disk_used_gb,
                "usage_percent": disk_percent,
            },
            "vm_uptime_seconds": round(uptime_seconds, 1),
            "high_resource_alert": len(high_resource_warnings) > 0,
            "resource_warnings": high_resource_warnings,
            "last_refreshed": datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
        }
        self._latest_vm_metrics = metrics
        self._last_metrics_refresh = time.time()
        return metrics

    def get_system_metrics(self) -> dict[str, Any]:
        if not self._latest_vm_metrics or (time.time() - self._last_metrics_refresh) > 5.0:
            return self.refresh_metrics()
        return self._latest_vm_metrics

    def get_debug_summary(self) -> dict[str, Any]:
        now = time.time()
        self._prune(now)

        events = list(self._events)
        log_lines = [ev.short_log for ev in events[-300:]]

        errors_only = [ev.short_log for ev in events if ev.status_code >= 400 or ev.error_message]

        vm_metrics = self.get_system_metrics()

        return {
            "status": "ok",
            "window": "latest_10_minutes",
            "refresh_interval": "5s",
            "format": "[sycord.site/api/<route>] <time> - error log",
            "summary": {
                "total_api_requests_10m": len(events),
                "error_requests_count": len(errors_only),
                "high_resource_usage": vm_metrics.get("high_resource_alert", False),
            },
            "logs": log_lines,
            "errors": errors_only[-100:],
            "entries": [ev.to_dict() for ev in events[-100:]],
            "vm_details": vm_metrics,
        }

    def get_raw_text_logs(self) -> str:
        now = time.time()
        self._prune(now)
        events = list(self._events)
        if not events:
            return "[sycord.site/api] No incoming API requests recorded in the latest 10 minutes."
        return "\n".join(ev.short_log for ev in events[-300:])


# Singleton tracker instance
tracker = ActivityTracker()


async def periodic_5s_metrics_loop(stop_event: asyncio.Event) -> None:
    """Background task: auto refreshes metrics every 5 seconds even when closed."""
    while not stop_event.is_set():
        try:
            tracker.refresh_metrics()
            tracker._prune(time.time())
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=5.0)
        except asyncio.TimeoutError:
            pass


class ActivityTrackerMiddleware:
    """ASGI Middleware to capture and log only incoming API requests."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")

        # Non-API requests (static files, GUI HTML, favicon, etc.) are not logged
        if not is_api_path(path):
            await self.app(scope, receive, send)
            return

        start_time = time.monotonic()
        method = str(scope.get("method") or "GET")
        headers = dict(scope.get("headers") or [])
        user_agent = headers.get(b"user-agent", b"").decode(errors="replace")
        
        forwarded = headers.get(b"x-forwarded-for", b"").decode(errors="replace")
        client_ip = forwarded.split(",")[0].strip() if forwarded else (scope.get("client") or ("unknown", 0))[0]

        status_code = 200
        bytes_out = 0
        error_msg: str | None = None

        async def send_wrapper(message: dict[str, Any]) -> None:
            nonlocal status_code, bytes_out
            if message["type"] == "http.response.start":
                status_code = message.get("status", 200)
            elif message["type"] == "http.response.body":
                body = message.get("body", b"")
                if body:
                    bytes_out += len(body)
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        except Exception as exc:
            status_code = 500
            error_msg = f"{type(exc).__name__}: {exc}"
            tracker.record_internal_error(type(exc).__name__, str(exc), path)
            raise
        finally:
            duration_s = time.monotonic() - start_time
            tracker.record_request(
                method=method,
                path=path,
                status_code=status_code,
                duration_s=duration_s,
                client_ip=str(client_ip),
                user_agent=user_agent,
                bytes_out=bytes_out,
                error_msg=error_msg,
            )
