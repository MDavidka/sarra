"""Simple in-memory HTTP rate limiting middleware."""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections import defaultdict, deque
from typing import Deque

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RateLimitMiddleware:
    """Sliding-window limiter for single-process Syte deployments.

    Agent polling / SSE / GUI hit many ``/api/...`` routes per minute, so API
    traffic uses the elevated budget. Anonymous HTML page traffic stays lower.
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        requests_per_minute: int = 180,
        elevated_requests_per_minute: int = 2400,
        window_seconds: float = 60.0,
    ) -> None:
        self.app = app
        self.requests_per_minute = requests_per_minute
        self.elevated_requests_per_minute = elevated_requests_per_minute
        self.window_seconds = window_seconds
        self._hits: defaultdict[str, Deque[float]] = defaultdict(deque)
        self._lock = asyncio.Lock()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "")
        if path in {"/health", "/api/health"} or path.endswith("/health"):
            await self.app(scope, receive, send)
            return

        # Elevate all API + static/OpenAPI traffic. Previously only exact "/api"
        # was elevated, so /api/projects/... agent polling hit the tiny budget.
        limit = (
            self.elevated_requests_per_minute
            if path.startswith(("/openapi", "/static", "/api/", "/sycord/"))
            or path in {"/api", "/sycord", "/sycord/"}
            else self.requests_per_minute
        )
        allowed, retry_after = await self._allow(self._client_key(scope), limit)
        if not allowed:
            response = JSONResponse(
                {"detail": "Rate limit exceeded"},
                status_code=429,
                headers={"Retry-After": str(max(1, int(retry_after)))},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)

    async def _allow(self, key: str, limit: int) -> tuple[bool, float]:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        async with self._lock:
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return False, self.window_seconds - (now - hits[0])
            hits.append(now)
            return True, 0.0

    @staticmethod
    def _client_key(scope: Scope) -> str:
        headers = {
            key.decode("latin1").lower(): value.decode("latin1")
            for key, value in scope.get("headers") or []
        }
        api_key = (headers.get("x-api-key") or "").strip()
        if api_key:
            digest = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
            return f"api:{digest}"
        client = scope.get("client")
        if client:
            return f"ip:{client[0]}"
        return "ip:unknown"
