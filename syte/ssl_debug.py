"""Live per-endpoint SSL debug: is each HTTPS endpoint actually serving?

The SSL dashboard's certificate summary only says whether Caddy *holds* a
certificate. It does not tell the operator whether a specific hostname is
reachable over HTTPS — which is what matters when e.g. ``9router.sycord.site``
is served by this Caddy instance and a cert file exists but nothing answers.
"""

from __future__ import annotations

import asyncio
import socket
import ssl
import time
import urllib.error
import urllib.request

from syte.domain_utils import build_https_url, is_safe_caddy_hostname, normalize_domain


def _probe(url: str, timeout: float = 3.0):
    """Probe an HTTPS URL, distinguishing a browser-trustable TLS result from a
    handshake that succeeds only on a self-signed / mismatched certificate.

    Python's default SSL context verifies the certificate trust chain and
    hostname, so a certificate a browser would reject surfaces here as a
    verification error rather than a successful request.
    """
    if not url or not url.startswith("https://"):
        return False, None
    start = time.monotonic()
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return True, time.monotonic() - start
    except urllib.error.HTTPError as error:
        # A real HTTP reply (even 4xx/5xx) proves TLS + routing with a
        # trustable certificate are working; Caddy may 404 a non-route.
        return True, time.monotonic() - start
    except urllib.error.URLError as error:
        reason = getattr(error, "reason", "")
        reason_str = str(reason).lower()
        latency = time.monotonic() - start
        # A certificate the browser would reject. Phrase it for the operator.
        if isinstance(reason, ssl.SSLCertVerificationError):
            return ("invalid-cert", str(reason.verify_message or "certificate not trusted"), latency)
        if "certificate" in reason_str or "ssl" in reason_str or "tls" in reason_str:
            return "cert-error", latency
        return False, latency
    except (TimeoutError, OSError):
        return False, None


async def live_probe_https(url: str, timeout: float = 3.0):
    """Probe an HTTPS URL off the event loop.

    Returns a dict describing reachability, suitable for the SSL debug view.
    """
    if not url:
        return {"reachable": False, "state": "not-configured", "detail": "no endpoint", "latency_ms": None}
    result = await asyncio.to_thread(_probe, url, timeout)
    if isinstance(result, tuple) and result and result[0] == "invalid-cert":
        return {
            "reachable": False,
            "state": "invalid-cert",
            "detail": f"certificate rejected: {result[1]}",
            "latency_ms": round(result[2] * 1000) if result[2] is not None else None,
        }
    if isinstance(result, tuple) and result and result[0] == "cert-error":
        return {
            "reachable": False,
            "state": "cert-error",
            "detail": "TLS/certificate failure",
            "latency_ms": round(result[1] * 1000) if result[1] is not None else None,
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
    live_state = live.get("state") if live else None
    if live_state in ("invalid-cert", "cert-error"):
        base["state"] = live_state
        base["reachable"] = False
        base["detail"] = live.get("detail", "certificate not trusted by browsers")
        return base
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


def _probe_tls_target(
    connect_host: str,
    port: int,
    *,
    server_hostname: str,
    host_header: str,
    timeout: float = 3.0,
):
    """Probe TLS on a socket while keeping public hostname/SNI semantics.

    ``urllib`` would use 127.0.0.1 as SNI when connecting to the loopback
    address. The local Caddy route needs the real 9Router hostname instead, so
    this small HTTP/1.1 probe connects to loopback but sends the public name in
    both TLS SNI and the Host header.
    """
    start = time.monotonic()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((connect_host, port), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=server_hostname) as tls:
                request = (
                    f"HEAD / HTTP/1.1\r\n"
                    f"Host: {host_header}\r\n"
                    "Connection: close\r\n\r\n"
                ).encode("ascii")
                tls.sendall(request)
                response = tls.makefile("rb")
                try:
                    status_line = response.readline(4096).decode("iso-8859-1", "replace")
                finally:
                    response.close()
        parts = status_line.split()
        if len(parts) < 2 or not parts[1].isdigit():
            return "bad-response", status_line.strip(), time.monotonic() - start
        return "http", int(parts[1]), time.monotonic() - start
    except ssl.SSLCertVerificationError as error:
        return "invalid-cert", str(error.verify_message or error), time.monotonic() - start
    except ssl.SSLError as error:
        return "cert-error", str(error), time.monotonic() - start
    except (TimeoutError, OSError) as error:
        return "unreachable", str(error), time.monotonic() - start


async def local_caddy_tls_status(
    *,
    hostname: str = "9router.sycord.site",
    port: int = 20128,
    timeout: float = 3.0,
) -> dict:
    """Verify the loopback-only Caddy TLS listener for the 9Router hostname."""
    host = normalize_domain(hostname or "")
    if not host or not is_safe_caddy_hostname(host):
        return {
            "configured": False,
            "serving": False,
            "state": "malformed",
            "hostname": host or None,
            "target": f"127.0.0.1:{port}",
            "detail": "invalid 9Router hostname",
        }

    result = await asyncio.to_thread(
        _probe_tls_target,
        "127.0.0.1",
        port,
        server_hostname=host,
        host_header=host,
        timeout=timeout,
    )
    state, value, latency = result
    base = {
        "configured": True,
        "serving": False,
        "hostname": host,
        "target": f"127.0.0.1:{port}",
        "latency_ms": round(latency * 1000),
        "http_status": None,
    }
    if state == "http":
        base.update(
            serving=True,
            state="serving",
            http_status=value,
            detail=f"Caddy TLS served {host} on 127.0.0.1:{port} (HTTP {value})",
        )
    elif state == "invalid-cert":
        base.update(
            state="invalid-cert",
            detail=f"certificate rejected for {host}: {value}",
        )
    elif state == "cert-error":
        base.update(state="cert-error", detail=f"TLS handshake failed: {value}")
    elif state == "bad-response":
        base.update(
            state="bad-response",
            detail=(
                "TLS connected but Caddy returned no HTTP status: "
                f"{value}"
            ),
        )
    else:
        base.update(
            state="caddy-down",
            detail=f"127.0.0.1:{port} is not accepting TLS: {value}",
        )
    return base
