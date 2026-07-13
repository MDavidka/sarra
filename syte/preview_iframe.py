"""Preview iframe embed readiness — headers, checklist, live probe."""

from __future__ import annotations

import urllib.error
import urllib.request
from typing import Any

from syte.domain_utils import build_https_url, normalize_domain
from syte.preview_domains import preview_frame_ancestors_csp

# Headers Caddy must strip from upstream dev servers and omit on preview responses.
PREVIEW_STRIP_HEADERS = (
    "X-Frame-Options",
    "Cross-Origin-Opener-Policy",
    "Cross-Origin-Embedder-Policy",
    "Cross-Origin-Resource-Policy",
    "Strict-Transport-Security",
    "Permissions-Policy",
    "Content-Security-Policy",
)


def expected_frame_csp(gui_domain: str = "", *, allow_any: bool = True) -> str:
    return preview_frame_ancestors_csp(gui_domain, allow_any=allow_any)


def build_iframe_checklist(
    project: dict,
    *,
    frame_csp: str,
    live_headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return checklist items for preview iframe embedding (Syte hoster view)."""
    preview_domain = normalize_domain(project.get("preview_domain") or "")
    preview_port = project.get("preview_port")
    preview_url = build_https_url(preview_domain) if preview_domain else ""
    headers = {k.lower(): v for k, v in (live_headers or {}).items()}

    def header_ok(name: str) -> bool:
        return name.lower() not in headers

    def header_missing_or_safe(name: str, bad_values: tuple[str, ...]) -> bool:
        value = headers.get(name.lower(), "")
        if not value:
            return True
        lower = value.lower()
        return not any(bad in lower for bad in bad_values)

    xfo_ok = header_ok("x-frame-options")
    coop_ok = header_missing_or_safe(
        "cross-origin-opener-policy",
        ("same-origin", "same-origin-allow-popups"),
    )
    coep_ok = header_missing_or_safe(
        "cross-origin-embedder-policy",
        ("require-corp",),
    )
    hsts_ok = header_ok("strict-transport-security")
    csp = headers.get("content-security-policy", "")
    csp_ok = "frame-ancestors" in csp and (
        "sycord.com" in csp or "*" in csp or frame_csp.split("frame-ancestors", 1)[-1].strip() in csp
    ) if csp else bool(preview_domain)

    items = [
        {
            "id": "x_frame_options",
            "label": "No X-Frame-Options (or removed)",
            "ok": xfo_ok if live_headers else preview_domain is not None,
            "configured_by_syte": True,
        },
        {
            "id": "csp_frame_ancestors",
            "label": "CSP frame-ancestors allows sycord.com parent",
            "ok": csp_ok if live_headers else preview_domain is not None,
            "configured_by_syte": True,
            "expected": frame_csp,
            "actual": csp or "(set by Caddy on first response)",
        },
        {
            "id": "no_coop",
            "label": "No Cross-Origin-Opener-Policy: same-origin",
            "ok": coop_ok if live_headers else preview_domain is not None,
            "configured_by_syte": True,
        },
        {
            "id": "no_coep",
            "label": "No Cross-Origin-Embedder-Policy: require-corp",
            "ok": coep_ok if live_headers else preview_domain is not None,
            "configured_by_syte": True,
        },
        {
            "id": "no_hsts",
            "label": "No HSTS on preview subdomain",
            "ok": hsts_ok if live_headers else preview_domain is not None,
            "configured_by_syte": True,
        },
        {
            "id": "https_443",
            "label": "Preview served on HTTPS :443 (not raw dev port)",
            "ok": bool(preview_domain and preview_url.startswith("https://")),
            "configured_by_syte": True,
        },
        {
            "id": "public_no_auth",
            "label": "Preview URL is public (no Syte login gate)",
            "ok": True,
            "configured_by_syte": True,
            "note": "Caddy proxies directly to dev server — Syte auth is not on preview routes",
        },
        {
            "id": "dev_server_running",
            "label": "Preview dev server listening",
            "ok": bool(project.get("preview_port") and project.get("preview_status") == "running"),
            "configured_by_syte": False,
        },
    ]

    return {
        "preview_domain": preview_domain or None,
        "preview_url": preview_url or None,
        "frame_csp": frame_csp,
        "live_probe": live_headers is not None,
        "all_ok": all(item["ok"] for item in items),
        "items": items,
    }


def probe_https_available(url: str, timeout: float = 4.0) -> bool:
    """True when HTTPS URL responds (TLS + HTTP). Used before advertising preview_domain_url."""
    if not url or not url.startswith("https://"):
        return False
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return 200 <= response.status < 500
    except (urllib.error.URLError, TimeoutError, OSError):
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return 200 <= response.status < 500
        except (urllib.error.URLError, TimeoutError, OSError):
            return False


def probe_preview_headers(url: str, timeout: float = 5.0) -> dict[str, str] | None:
    """HEAD request to preview URL; returns response headers or None on failure."""
    if not url:
        return None
    try:
        request = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {k: v for k, v in response.headers.items()}
    except (urllib.error.URLError, TimeoutError, OSError):
        try:
            request = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return {k: v for k, v in response.headers.items()}
        except (urllib.error.URLError, TimeoutError, OSError):
            return None
