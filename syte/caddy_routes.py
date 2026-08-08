"""Unified Caddy route generation — production + preview with wildcard TLS."""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from syte.domain_utils import (
    is_safe_caddy_hostname,
    normalize_domain,
    sanitize_caddy_label,
)

# 9Router AI gateway upstream: TLS terminates on this Caddy instance
# (https://9router.sycord.site) and traffic is proxied to the dedicated
# gateway host/port. A separate loopback-only listener below verifies the same
# certificate/SNI path for local API clients without changing the remote upstream.
NINE_ROUTER_UPSTREAM_DEFAULT = "65.75.203.134:20128"
# Caddy also exposes a loopback-only TLS listener so the Settings tab and
# local API clients can verify the certificate/SNI path without leaving this VM.
NINE_ROUTER_LOCAL_TLS_PORT = 20128
# Host used when the managed Router tab publishes the local 9Router container.
NINE_ROUTER_PUBLIC_HOST = "9router.sycord.site"
# The official 9Router web UI is mounted at /dashboard. Keep this in the route
# layer so opening the public host lands on the real dashboard instead of the
# API root, which intentionally returns 404.
NINE_ROUTER_DASHBOARD_PATH = "/dashboard"

# Public recursive resolvers used for DNS-01 propagation checks. Without these
# Caddy asks the system resolver, which on this host points at a local/split
# view that does not yet see the freshly written _acme-challenge TXT record —
# the DNS-01 order then times out and Caddy falls back to its self-signed
# internal issuer.
ACME_DNS_RESOLVERS = "1.1.1.1 8.8.8.8"

# How long Caddy waits for the challenge TXT record to become visible on the
# authoritative nameservers before giving up on the order.
ACME_PROPAGATION_TIMEOUT = "5m"
