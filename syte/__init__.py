"""Syte — workspace deployment and publishing service."""

__version__ = "0.9.17"

# The managed 9Router dashboard has its own public hostname. Apply the
# deployment default at package import time so every Syte component that imports
# caddy_routes (Router manager, certificates, and Caddy generation) uses the
# same hostname without competing with the Syte operator GUI on api.sycord.site.
from syte import caddy_routes as _caddy_routes

_caddy_routes.NINE_ROUTER_PUBLIC_HOST = "9router.sycord.site"
