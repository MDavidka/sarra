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


_circuit_breakers: dict[str, dict[str, Any]] = defaultdict(
    lambda: {
        "failures": 0,
        "last_failure": None,
        "state": "closed",  # closed | open | half_open
    }
)

_CIRCUIT_FAILURE_THRESHOLD = 3
_CIRCUIT_COOLDOWN = timedelta(minutes=5)

# Failures that are config/auth problems — do not open the breaker (user must
# fix the key/settings; retrying only extends lockouts).
_PERMANENT_FAILURE_MARKERS = (
    "api_key_service_blocked",
    "invalid api key",
    "invalid or deactivated api key",
    "api key you provided is invalid",
    "incorrect api key",
    "authentication failed",
    "permission_denied",
    "unauthorized",
    "are blocked",
)


def is_permanent_provider_failure(error: str | None) -> bool:
    text = (error or "").lower()
    if not text:
        return False
    return any(marker in text for marker in _PERMANENT_FAILURE_MARKERS)


def circuit_breaker_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip()}:{(model or '').strip()}"


def circuit_breaker_status(provider: str, model: str) -> dict[str, Any]:
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    remaining_s = 0
    last = breaker.get("last_failure")
    if breaker.get("state") == "open" and isinstance(last, datetime):
        elapsed = datetime.now(timezone.utc) - last
        remaining = _CIRCUIT_COOLDOWN - elapsed
        remaining_s = max(0, int(remaining.total_seconds()))
    return {
        "key": key,
        "state": breaker["state"],
        "failures": int(breaker["failures"] or 0),
        "cooldown_remaining_s": remaining_s,
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


def reset_profile_circuit_breakers(*profiles: str) -> list[str]:
    """Clear breakers for the given Syra profiles (or all profiles if empty)."""
    from syte.ai_providers import PROFILE_ORDER, PROFILE_PROVIDERS

    names = profiles or PROFILE_ORDER
    cleared: list[str] = []
    for name in names:
        spec = PROFILE_PROVIDERS.get(name)
        if not spec:
            continue
        provider = str(spec.get("provider") or "openai")
        model = str(spec.get("model") or "")
        key = circuit_breaker_key(provider, model)
        reset_circuit_breaker(provider, model)
        cleared.append(key)
    return cleared


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
    remaining_s = 0
    if isinstance(last, datetime):
        remaining_s = max(0, int((_CIRCUIT_COOLDOWN - (now - last)).total_seconds()))
    raise ProviderError(
        (
            f"Circuit breaker open for {key} "
            f"(~{remaining_s}s left). Wait for cool-down, re-save the profile API key "
            f"in Settings → AI, or restart Syte after fixing provider auth."
        ),
        error_type="circuit_open",
        retryable=remaining_s > 0,
        detail={
            "provider": provider,
            "model": model,
            "cooldown_remaining_s": remaining_s,
        },
    )


def record_circuit_success(provider: str, model: str) -> None:
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    breaker["failures"] = 0
    breaker["state"] = "closed"
    breaker["last_failure"] = None


def record_circuit_failure(
    provider: str,
    model: str,
    *,
    error: str | None = None,
) -> bool:
    """Record a provider failure. Returns False when the failure is permanent and ignored."""
    if is_permanent_provider_failure(error):
        return False
    key = circuit_breaker_key(provider, model)
    breaker = _circuit_breakers[key]
    breaker["failures"] = int(breaker["failures"] or 0) + 1
    breaker["last_failure"] = datetime.now(timezone.utc)
    if breaker["failures"] >= _CIRCUIT_FAILURE_THRESHOLD:
        breaker["state"] = "open"
    return True
