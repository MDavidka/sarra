def normalize_domain(domain: str) -> str:
    """Strip scheme/path from a domain so Caddy gets e.g. sycord.site not https://sycord.site."""
    domain = domain.strip().lower()
    for prefix in ("https://", "http://"):
        if domain.startswith(prefix):
            domain = domain[len(prefix):]
    return domain.split("/")[0].rstrip(".")


def build_https_url(domain: str) -> str:
    return f"https://{normalize_domain(domain)}"


def build_direct_url(ip: str, port: int) -> str:
    return f"http://{ip}:{port}"
