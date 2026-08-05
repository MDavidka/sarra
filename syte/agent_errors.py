"""Structured agent errors and provider circuit breakers."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any


class AgentError(Exception):
    """Base for all agent errors."""

    def __init__(
        self,
        message: str,
        error_type: str,
        *,
        retryable: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.error_type = error_type
        self.retryable = retryable
        self.detail = detail or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error": self.error_type,
            "message": self.message,
            "retryable": self.retryable,
            **({k: v for k, v in self.detail.items() if k not in {"ok", "error", "message"}}),
        }


class ToolExecutionError(AgentError):
    """Tool failed (file not found, command timeout, etc.)."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "tool_failed",
        retryable: bool = False,
        hint: str = "",
        detail: dict[str, Any] | None = None,
    ) -> None:
        payload = dict(detail or {})
        if hint:
            payload["hint"] = hint
        super().__init__(message, error_type, retryable=retryable, detail=payload)


class ProviderError(AgentError):
    """LLM provider error (rate limit, timeout, circuit open)."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "provider_error",
        retryable: bool = True,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_type, retryable=retryable, detail=detail)


class WorkspaceError(AgentError):
    """Workspace issue (disk full, permissions, missing path)."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "workspace_error",
        retryable: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_type, retryable=retryable, detail=detail)


class MalformedRequestError(AgentError):
    """Client sent a malformed request (invalid model id, bad parameters)."""

    def __init__(
        self,
        message: str,
        *,
        error_type: str = "malformed_request",
        retryable: bool = False,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, error_type, retryable=retryable, detail=detail)


# Error messages emitted by external agent providers, mapped to a structured
# error type + a user-facing message. Covers the phrases providers commonly
# return verbatim: invalid model, rate limiting, invalid project, capacity.
_PROVIDER_ERROR_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (
        ("invalid model", "model not found", "unknown model", "model id not found", "no such model"),
        "malformed_request",
        "The selected model is not valid or no longer available on the provider. "
        "Pick a supported model in AI provider settings, then retry.",
    ),
    (
        ("invalid project", "project not found", "no such project", "unknown project"),
        "malformed_request",
        "The provider rejected the request because the project is invalid or not found. "
        "Check the configured project/region in AI provider settings.",
    ),
    (
        ("model is on capacity", "at capacity", "overloaded", "capacity", "server is busy", "insufficient capacity"),
        "provider_error",
        "The model is currently at capacity on the provider. This is transient — retry shortly.",
    ),
    (
        ("rate limited", "rate limit", "too many requests", "slow down", "quota exceeded", "resource exhausted"),
        "rate_limited",
        "You are being rate limited by the provider. Slow down and retry in a moment.",
    ),
)

# Superset used to gate ``is_retryable``: which phrases mark a request as
# retryable (false for permanent config errors like a bad key).
_NON_RETRYABLE_PROVIDER_MARKERS = (
    "invalid model",
    "model not found",
    "unknown model",
    "invalid project",
    "project not found",
    "invalid or deactivated api key",
)


def classify_provider_error(detail: str | None, status_code: int | None = None) -> dict[str, Any]:
    """Categorize a raw external-agent provider error body.

    Returns ``{"matched": bool, "error_type": str, "message": str}``. When the
    provider message matches a known phrase, ``message`` is a friendly summary
    and ``error_type`` is the structured type (``rate_limited``,
    ``malformed_request``, ``provider_error``). Unmatched details degrade to a
    generic provider error so callers never parse raw strings.
    """
    lower = (detail or "").lower()
    for markers, error_type, hint in _PROVIDER_ERROR_HINTS:
        if any(marker in lower for marker in markers):
            return {"matched": True, "error_type": error_type, "message": hint}
    if status_code == 429:
        return {
            "matched": True,
            "error_type": "rate_limited",
            "message": (
                "You are being rate limited by the provider (HTTP 429). "
                "Slow down and retry in a moment."
            ),
        }
    return {
        "matched": False,
        "error_type": "provider_error",
        "message": "",
    }


def is_retryable_provider_detail(status_code: int | None, detail: str | None) -> bool:
    """True when a provider error is transient and worth retrying."""
    lower = (detail or "").lower()
    if status_code is not None and status_code not in {408, 429, 500, 502, 503, 504}:
        return False
    if any(marker in lower for marker in _NON_RETRYABLE_PROVIDER_MARKERS):
        return False
    return True


_circuit_breakers: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "failures": 0,
        "last_failure": None,
        "state": "closed",  # closed | open | half_open
    }
)

_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN = timedelta(minutes=5)


def circuit_breaker_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip()}:{(model or '').strip()}"


def circuit_breaker_status(provider: str, model: str) -> dict[str, Any]:
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    return {
        "key": key,
        "state": breaker["state"],
        "failures": int(breaker["failures"] or 0),
        "last_failure": breaker["last_failure"].isoformat()
        if isinstance(breaker.get("last_failure"), datetime)
        else breaker.get("last_failure"),
    }


def reset_circuit_breaker(provider: str | None = None, model: str | None = None) -> None:
    """Reset one breaker or all (tests / recovery)."""
    if provider is None and model is None:
        _circuit_breakers.clear()
        return
    key = circuit_breaker_key(provider or "", model or "")
    _circuit_breakers.pop(key, None)


def check_circuit_breaker(provider: str, model: str) -> None:
    """Raise ProviderError when the circuit is open and cool-down has not elapsed."""
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    if breaker["state"] != "open":
        return
    last = breaker.get("last_failure")
    now = datetime.now(timezone.utc)
    if isinstance(last, datetime) and (now - last) > _CIRCUIT_COOLDOWN:
        breaker["state"] = "half_open"
        return
    raise ProviderError(
        f"Circuit breaker open for {key}",
        error_type="circuit_open",
        retryable=False,
        detail={"provider": provider, "model": model},
    )


def record_circuit_success(provider: str, model: str) -> None:
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    breaker["failures"] = 0
    breaker["state"] = "closed"
    breaker["last_failure"] = None


def record_circuit_failure(provider: str, model: str) -> None:
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    breaker["failures"] = int(breaker["failures"] or 0) + 1
    breaker["last_failure"] = datetime.now(timezone.utc)
    if breaker["failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
        breaker["state"] = "open"
