"""Shared LiteLLM proxy endpoint configuration.

The container only listens on loopback. Caddy is the sole public ingress and
publishes the OpenAI-compatible API at ``https://api.sycord.site/v1``.
"""

from __future__ import annotations

LITELLM_CONTAINER_PORT = 4000
LITELLM_HOST_PORT = 4000
LITELLM_INTERNAL_ORIGIN = f"http://127.0.0.1:{LITELLM_HOST_PORT}"
LITELLM_INTERNAL_API_URL = f"{LITELLM_INTERNAL_ORIGIN}/v1"
LITELLM_PUBLIC_HOST = "api.sycord.site"
LITELLM_PUBLIC_ORIGIN = f"https://{LITELLM_PUBLIC_HOST}"
LITELLM_PUBLIC_API_URL = f"{LITELLM_PUBLIC_ORIGIN}/v1"
