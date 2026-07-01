import re

_IP_RE = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


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


def build_https_url(domain: str) -> str:
    return f"https://{normalize_domain(domain)}"


def build_direct_url(ip: str, port: int) -> str:
    clean = ip.strip()
    if not is_valid_ip(clean):
        clean = "127.0.0.1"
    return f"http://{clean}:{port}"
