"""Live per-endpoint SSL debug: is each HTTPS endpoint actually serving?

The SSL dashboard's certificate summary only says whether Caddy *holds* a
certificate. It does not tell the operator whether a specific hostname is
reachable over HTTPS — which is what matters when e.g. ``9router.sycord.site``
is referenced as an API base but is not proxied by this Caddy instance.
"""

from __future__ import annotations

import asyncio
import time
import urllib.error
import urllib.request

from syte.domain_utils import build_https_url, is_safe_caddy_hostname, normalize_domain


def _probe(url: str, timeout: float = 3.0) -> tuple[bool, float | None]:
    if not url or not url.startswith("https://"):
        return False, None
    start = time.monotonic()
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            ok = 200 <= response.status < 600
            return ok, time.monotonic() - start
    except urllib.error.HTTPError as error:
        # An HTTP error still proves TLS + routing are working.
        return True, time.monotonic() - start
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", "")
        reason_str = str(reason).lower()
        # Certificate / TLS handshake failures are the diagnostic we care about.
        if "certificate" in reason_str or "ssl" in reason_str or "tls" in reason_str:
            return "cert-error", time.monotonic() - start
        return False, time.monotonic() - start
    except (TimeoutError, OSError):
        return False, None


async def live_probe_https(url: str, timeout: float = 3.0):
    """Probe an HTTPS URL off the event loop.

    Returns a dict describing reachability, suitable for the SSL debug view.
    """
    if not url:
        return {"reachable": False, "state": "not-configured", "detail": "no endpoint", "latency_ms": None}
    result = await asyncio.to_thread(_probe, url, timeout)
    if result == "cert-error":
        return {
            "reachable": False,
            "state": "cert-error",
            "detail": "TLS/certificate error",
            "latency_ms": result[1],
        }
    reachable, latency = result
    return {
        "reachable": bool(reachable),
        "state": "serving" if reachable else "down",
        "detail": f"HTTPS{' OK' if reachable else ' unresponsive'}",
        "latency_ms": round(latency * 1000) if latency is not None else None,
    }


def malformed_host(domain: str) -> str | None:
    """Return a reason string when a domain cannot be used as a hostname, else None."""
    host = normalize_domain(domain or "")
    if not host:
        return "empty domain"
    if not is_safe_caddy_hostname(host):
        return f"malformed hostname: {domain!r}"
    return None


def classify_endpoint(configured: bool, cert_active: bool, live) -> dict:
    """Combine configured/cert/live probe into one operator-facing status row."""
    if not configured:
        return {
            "configured": False,
            "cert": False,
            "state": "not-configured",
            "reachable": False,
            "detail": "no domain configured",
        }
    suspicious = live.get("state") if live else None
    if suspicious == "malformed":
        return {
            "configured": True,
            "cert": cert_active,
            "state": "malformed",
            "reachable": False,
            "detail": live.get("detail", "malformed hostname"),
        }
    base = {
        "configured": True,
        "cert": cert_active,
        "reachable": bool(live and live.get("reachable")),
        "latency_ms": live.get("latency_ms") if live else None,
    }
    if live and live.get("reachable"):
        base["state"] = "serving"
        base["detail"] = "SSL serving"
    elif cert_active:
        base["state"] = "down"
        base["detail"] = "cert installed but endpoint not responding"
    else:
        base["state"] = "pending"
        base["detail"] = "certificate pending"
    return base


async def debug_endpoint(
    *,
    name: str,
    domain: str,
    configured: bool,
    cert_active: bool,
    extra: str | None = None,
) -> dict:
    """Produce a full debug row for one named endpoint."""
    host = normalize_domain(domain or "")
    malformed = malformed_host(host) if host else None
    if malformed or not configured:
        live = None
    else:
        live = await live_probe_https(build_https_url(host))
    row = classify_endpoint(configured, cert_active, live)
    row["name"] = name
    row["domain"] = host or None
    if row.get("state") == "malformed":
        row["detail"] = malformed or row.get("detail")
    if extra:
        row["note"] = extra
    return row
