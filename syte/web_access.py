"""Full internet access for agent turns — fetch any URL, browse it in Chromium.

Before this module the agent could only *search* the web (``web_search``) and
only *fetch/inspect* URLs on the project's own preview allowlist. It could not
read a documentation page it found, and it could not open a browser against an
arbitrary origin, so "look this up and check the console" was impossible.

Two capabilities live here:

``fetch_url``
    SSRF-guarded HTTP(S) GET for any public URL, with HTML reduced to readable
    text so a page costs a bounded number of tokens.

``browse_url``
    Real headless Chromium (via CDP) against any allowed URL, returning console
    logs, page exceptions, failed requests and a page summary. Loopback and
    private addresses are permitted here on purpose so the agent can read the
    **dev server's** browser console, which is where Next.js hydration and
    client-side runtime errors actually surface.

Both honour the ``agent_internet_access`` setting (default: enabled) and never
raise — failures come back as tool-shaped dicts.
"""

from __future__ import annotations

import asyncio
import ipaddress
import logging
import re
import socket
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx

from syte.database import get_setting

logger = logging.getLogger(__name__)

USER_AGENT = "Syte-Agent/1.0 (+https://sycord.com)"
FETCH_TIMEOUT_S = 25.0
MAX_RESPONSE_BYTES = 3_000_000
DEFAULT_MAX_CHARS = 20_000
HARD_MAX_CHARS = 120_000
MAX_REDIRECTS = 5

# Hosts that must never be reachable regardless of settings: cloud instance
# metadata is the classic credential-theft target.
_BLOCKED_HOSTS = frozenset({
    "metadata.google.internal",
    "metadata.goog",
    "instance-data",
})
_BLOCKED_IPS = frozenset({"169.254.169.254", "fd00:ec2::254"})

_ALLOWED_SCHEMES = frozenset({"http", "https"})


class _TextExtractor(HTMLParser):
    """Minimal readable-text extractor (stdlib only — no bs4 dependency)."""

    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "iframe"})
    _BLOCK = frozenset({
        "p", "div", "section", "article", "header", "footer", "main", "nav",
        "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr", "br", "pre", "blockquote",
        "table", "ul", "ol", "form", "figure", "aside", "hr",
    })

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title = ""
        self.description = ""
        self.links: list[dict[str, str]] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth += 1
            return
        if tag == "title":
            self._in_title = True
        if tag == "meta":
            data = {k.lower(): (v or "") for k, v in attrs}
            name = (data.get("name") or data.get("property") or "").lower()
            if name in {"description", "og:description"} and not self.description:
                self.description = data.get("content", "")[:400]
        if tag == "a" and len(self.links) < 60:
            href = next((v or "" for k, v in attrs if k.lower() == "href"), "")
            if href and not href.startswith(("#", "javascript:")):
                self.links.append({"href": href[:400], "text": ""})
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if tag == "title":
            self._in_title = False
        if tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title += data.strip()
            return
        text = data.strip()
        if not text:
            return
        if self.links and not self.links[-1]["text"]:
            self.links[-1]["text"] = text[:120]
        self.parts.append(text)

    def text(self) -> str:
        raw = " ".join(self.parts)
        raw = re.sub(r"[ \t]+", " ", raw)
        raw = re.sub(r"\s*\n\s*", "\n", raw)
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


def html_to_text(html: str) -> dict[str, Any]:
    """Reduce an HTML document to title/description/text/links."""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # Malformed markup: fall back to a crude tag strip rather than failing.
        stripped = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        stripped = re.sub(r"<[^>]+>", " ", stripped)
        return {
            "title": "",
            "description": "",
            "text": re.sub(r"\s+", " ", stripped).strip(),
            "links": [],
        }
    return {
        "title": parser.title[:300],
        "description": parser.description,
        "text": parser.text(),
        "links": [link for link in parser.links if link["href"]][:60],
    }


async def internet_enabled() -> bool:
    """``agent_internet_access`` setting; enabled unless explicitly turned off."""
    try:
        raw = (await get_setting("agent_internet_access") or "").strip().lower()
    except Exception:
        return True
    if raw in {"0", "false", "off", "no", "disabled"}:
        return False
    return True


async def _extra_blocked_hosts() -> set[str]:
    try:
        raw = await get_setting("agent_internet_blocklist") or ""
    except Exception:
        return set()
    return {
        part.strip().lower()
        for part in re.split(r"[,\s]+", raw)
        if part.strip()
    }


def _is_private_ip(value: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _resolve_host(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        return []
    return [str(info[4][0]) for info in infos]


async def validate_url(url: str, *, allow_private: bool = False) -> dict[str, Any]:
    """Return ``{"ok": True, "url": normalized}`` or a tool-shaped error.

    Guards: scheme allowlist, credential-bearing URLs, metadata endpoints, and
    (unless ``allow_private``) every address that resolves to a private,
    loopback, link-local or reserved range. DNS is resolved here and the same
    resolution is what the caller connects to moments later; this is a
    pragmatic guard, not a rebinding-proof one.
    """
    raw = (url or "").strip()
    if not raw:
        return {"ok": False, "error": "empty_url", "message": "Provide a URL."}
    if "://" not in raw:
        raw = f"https://{raw}"
    parsed = urlparse(raw)
    scheme = (parsed.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        return {
            "ok": False,
            "error": "scheme_not_allowed",
            "message": f"Only http/https are allowed (got '{scheme or 'none'}').",
        }
    if parsed.username or parsed.password:
        return {
            "ok": False,
            "error": "credentials_in_url",
            "message": "Remove credentials from the URL and pass headers instead.",
        }
    host = (parsed.hostname or "").lower()
    if not host:
        return {"ok": False, "error": "invalid_url", "message": f"No host in URL: {url}"}
    if host in _BLOCKED_HOSTS or host in _BLOCKED_IPS:
        return {
            "ok": False,
            "error": "host_blocked",
            "message": "Cloud metadata endpoints are permanently blocked.",
        }
    blocked = await _extra_blocked_hosts()
    if host in blocked or any(host.endswith(f".{b}") for b in blocked):
        return {
            "ok": False,
            "error": "host_blocked",
            "message": f"{host} is in agent_internet_blocklist.",
        }

    addresses = await asyncio.to_thread(_resolve_host, host)
    if any(addr in _BLOCKED_IPS for addr in addresses):
        return {
            "ok": False,
            "error": "host_blocked",
            "message": "Resolves to a blocked metadata address.",
        }
    if not allow_private:
        if not addresses:
            return {
                "ok": False,
                "error": "dns_failed",
                "message": f"Could not resolve {host}.",
            }
        if all(_is_private_ip(addr) for addr in addresses):
            return {
                "ok": False,
                "error": "private_address",
                "message": (
                    f"{host} resolves to a private/loopback address. Use browse_url "
                    "(allow_local) for the project's own dev server instead."
                ),
            }
    normalized = urlunparse(
        (scheme, parsed.netloc, parsed.path or "/", parsed.params, parsed.query, "")
    )
    return {"ok": True, "url": normalized, "host": host, "addresses": addresses}


async def fetch_url(
    url: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    extract: str = "text",
    allow_private: bool = False,
) -> dict[str, Any]:
    """GET any allowed URL and return readable content.

    ``extract``: ``text`` (HTML reduced to prose, default), ``raw`` (body as
    received), or ``links`` (text plus the outbound link list).
    """
    if not await internet_enabled():
        return {
            "ok": False,
            "error": "internet_disabled",
            "message": (
                "Internet access is disabled for this deployment "
                "(setting agent_internet_access)."
            ),
        }
    max_chars = max(500, min(int(max_chars or DEFAULT_MAX_CHARS), HARD_MAX_CHARS))
    mode = (extract or "text").strip().lower()
    if mode not in {"text", "raw", "links"}:
        mode = "text"

    checked = await validate_url(url, allow_private=allow_private)
    if not checked.get("ok"):
        return checked
    target = str(checked["url"])

    try:
        async with httpx.AsyncClient(timeout=FETCH_TIMEOUT_S, follow_redirects=False) as client:
            redirects: list[str] = []
            response = await client.get(
                target,
                headers={"User-Agent": USER_AGENT, "Accept": "text/html,*/*;q=0.8"},
            )
            for _ in range(MAX_REDIRECTS):
                if response.status_code not in (301, 302, 303, 307, 308):
                    break
                location = response.headers.get("location")
                if not location:
                    break
                next_url = str(httpx.URL(target).join(location))
                hop = await validate_url(next_url, allow_private=allow_private)
                if not hop.get("ok"):
                    return {
                        **hop,
                        "error": hop.get("error") or "redirect_blocked",
                        "message": f"Redirect to {next_url} blocked: {hop.get('message')}",
                        "url": target,
                        "redirects": redirects,
                    }
                target = str(hop["url"])
                redirects.append(target)
                response = await client.get(
                    target, headers={"User-Agent": USER_AGENT},
                )

            body = response.content[:MAX_RESPONSE_BYTES]
            content_type = response.headers.get("content-type", "")
    except httpx.TimeoutException:
        return {
            "ok": False,
            "error": "fetch_timeout",
            "retryable": True,
            "message": f"{target} did not respond within {int(FETCH_TIMEOUT_S)}s.",
            "url": target,
        }
    except Exception as exc:
        return {
            "ok": False,
            "error": "fetch_failed",
            "message": str(exc) or type(exc).__name__,
            "url": target,
        }

    is_binary = not (
        content_type.startswith("text/")
        or "json" in content_type
        or "xml" in content_type
        or "javascript" in content_type
        or not content_type
    )
    if is_binary:
        return {
            "ok": response.status_code < 400,
            "url": target,
            "status_code": response.status_code,
            "content_type": content_type,
            "binary": True,
            "bytes": len(body),
            "message": "Binary response — content not returned.",
        }

    try:
        text = body.decode(response.encoding or "utf-8", errors="replace")
    except (LookupError, UnicodeDecodeError):
        text = body.decode("utf-8", errors="replace")

    payload: dict[str, Any] = {
        "ok": response.status_code < 400,
        "url": target,
        "status_code": response.status_code,
        "content_type": content_type,
        "bytes": len(body),
    }
    if redirects:
        payload["redirects"] = redirects

    if mode == "raw" or "html" not in content_type.lower():
        payload["content"] = _clip(text, max_chars, payload)
        return payload

    parsed = html_to_text(text)
    payload["title"] = parsed["title"]
    payload["description"] = parsed["description"]
    payload["content"] = _clip(parsed["text"], max_chars, payload)
    if mode == "links":
        payload["links"] = parsed["links"]
    return payload


def _clip(text: str, max_chars: int, payload: dict[str, Any]) -> str:
    if len(text) <= max_chars:
        return text
    payload["truncated"] = True
    payload["full_length"] = len(text)
    return text[:max_chars] + "\n… [truncated — request a narrower page or raise max_chars]"


async def browse_url(
    url: str,
    *,
    include_screenshot: bool = False,
    width: int = 1280,
    height: int = 800,
    allow_private: bool = True,
) -> dict[str, Any]:
    """Open ``url`` in headless Chromium and return DevTools diagnostics.

    ``allow_private`` defaults to ``True`` because the primary use is reading the
    project's own dev-server console (``http://127.0.0.1:<port>``), where client
    runtime and hydration errors appear but never reach the server log.
    """
    if not await internet_enabled():
        return {
            "ok": False,
            "error": "internet_disabled",
            "message": "Internet access is disabled (setting agent_internet_access).",
        }
    checked = await validate_url(url, allow_private=allow_private)
    if not checked.get("ok"):
        return checked
    target = str(checked["url"])

    from syte.preview_access import browser_install_hint, find_headless_browser

    browser = find_headless_browser()
    if not browser:
        return {
            "ok": False,
            "error": "no_browser",
            "url": target,
            "message": browser_install_hint(),
        }

    from syte.cdp_client import inspect_url_with_devtools

    try:
        inspected = await asyncio.to_thread(
            inspect_url_with_devtools,
            target,
            browser=browser,
            width=max(320, min(int(width or 1280), 2560)),
            height=max(320, min(int(height or 800), 2000)),
            include_screenshot=bool(include_screenshot),
        )
    except Exception as exc:
        return {
            "ok": False,
            "error": "browse_failed",
            "url": target,
            "message": str(exc) or type(exc).__name__,
        }

    public = {k: v for k, v in inspected.items() if k != "png_bytes"}
    if not include_screenshot:
        public.pop("screenshot", None)
    public["url"] = target
    public["action"] = "browse"
    return public
