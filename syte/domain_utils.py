import re

_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")
# Hostnames used in Caddyfile routes — no whitespace, braces, or quotes.
_HOSTNAME_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]{0,251}[a-z0-9])?$")


def is_valid_ip(value: str) -> bool:
    value = (value or "").strip()
    if not _IP_RE.match(value):
        return False
    try:
        return all(0 <= int(part) <= 255 for part in value.split("."))
    except ValueError:
        return False


def normalize_domain(domain: str) -> str:
    """Strip scheme/path from a domain so Caddy gets e.g. sycord.site not https://sycord.site."""
    domain = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.split("/")[0].split(":")[0].rstrip(".")


def is_safe_caddy_hostname(hostname: str) -> bool:
    """True when hostname is safe to interpolate into a Caddyfile host matcher."""
    host = normalize_domain(hostname or "")
    if not host or not _HOSTNAME_RE.match(host):
        return False
    if any(ch in host for ch in ("{", "}", '"', "'", "\n", "\r", "\t", " ", ";")):
        return False
    return True


def sanitize_caddy_label(label: str) -> str:
    """Make a project label safe for Caddyfile comments (no newlines / block closers)."""
    text = (label or "project").replace("\r", " ").replace("\n", " ")
    text = text.replace("{", "(").replace("}", ")")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:120] or "project"


def build_https_url(domain: str) -> str:
    return f"https://{normalize_domain(domain)}"


def build_direct_url(ip: str, port: int) -> str:
    clean = ip.strip()
    if not is_valid_ip(clean):
        clean = "127.0.0.1"
    return f"http://{clean}:{port}"
