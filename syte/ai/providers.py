"""Unified multi-provider AI client for Syte Autonomous AI Builder.

Supports Google Cloud Vertex AI, Google Gemini (AI Studio), OpenAI, Anthropic Claude,
DeepSeek, OpenRouter, and Local Ollama/vLLM endpoints.

Streaming uses a shared pooled ``httpx.AsyncClient`` (HTTP/2 + keep-alive) so
provider tokens are consumed directly on the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import time
from typing import Any, AsyncGenerator, Dict, List, Optional, Tuple

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
import httpx

logger = logging.getLogger("syte.ai.providers")

DEFAULT_BASE_URLS = {
    "vertex": "https://{LOCATION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{LOCATION}/endpoints/openapi",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

ENV_KEY_MAP = {
    "vertex": [
        "VERTEX_API_KEY",
        "VERTEXAI_API_KEY",
        "GOOGLE_APPLICATION_CREDENTIALS",
        "GCP_API_KEY",
        "GOOGLE_CLOUD_API_KEY",
        "VERTEX_SERVICE_ACCOUNT_JSON",
    ],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
    "openrouter": ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPEN_ROUTER_API_KEY"],
}


def _clean_api_key(key: str) -> str:
    k = (key or "").strip().strip('"').strip("'").strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


class VertexAuthManager:
    """Manages GCP Service Account OAuth2 tokens, Vertex Express mode API keys, and Project/Location resolution."""

    _token_cache: Dict[str, Tuple[str, float]] = {}  # cache_key -> (access_token, expiry_timestamp)

    @classmethod
    def resolve_gcp_project(cls, explicit_project: str = "", sa_info: Optional[dict] = None) -> str:
        if explicit_project and explicit_project.strip():
            return explicit_project.strip()
        if sa_info and sa_info.get("project_id"):
            return str(sa_info["project_id"]).strip()
        for env_var in (
            "VERTEX_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "PROJECT_ID",
            "CLOUDSDK_CORE_PROJECT",
        ):
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
        return ""

    @classmethod
    def resolve_gcp_location(cls, explicit_location: str = "") -> str:
        if explicit_location and explicit_location.strip():
            return explicit_location.strip()
        for env_var in (
            "VERTEX_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "GCP_REGION",
            "CLOUDSDK_COMPUTE_REGION",
        ):
            val = os.environ.get(env_var, "").strip()
            if val:
                return val
        return "us-central1"

    @classmethod
    def parse_service_account(cls, credential_str: str) -> Optional[dict]:
        """Check if credential_str is raw JSON or a path to a service account JSON file."""
        if not credential_str:
            gac = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip()
            if gac and os.path.isfile(gac):
                try:
                    with open(gac, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and data.get("type") == "service_account":
                            return data
                except Exception:
                    pass
            return None

        # Check if credential_str is a file path
        if os.path.isfile(credential_str):
            try:
                with open(credential_str, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("type") == "service_account":
                        return data
            except Exception:
                pass

        # Check if credential_str is raw JSON
        trimmed = credential_str.strip()
        if trimmed.startswith("{") and trimmed.endswith("}"):
            try:
                data = json.loads(trimmed)
                if isinstance(data, dict) and data.get("type") == "service_account":
                    return data
            except Exception:
                pass

        return None

    @classmethod
    async def get_access_token_from_service_account(cls, sa_info: dict) -> Tuple[str, Optional[str]]:
        """Mint a Google OAuth2 access token from service account RSA private key."""
        client_email = sa_info.get("client_email")
        private_key_pem = sa_info.get("private_key")
        token_uri = sa_info.get("token_uri") or "https://oauth2.googleapis.com/token"

        if not client_email or not private_key_pem:
            return "", "Service account JSON is missing 'client_email' or 'private_key'."

        cache_key = f"{client_email}:{sa_info.get('project_id', '')}"
        now = time.time()
        cached = cls._token_cache.get(cache_key)
        if cached and cached[1] > now + 60:
            return cached[0], None

        try:
            header = {"alg": "RS256", "typ": "JWT"}
            claims = {
                "iss": client_email,
                "scope": "https://www.googleapis.com/auth/cloud-platform",
                "aud": token_uri,
                "exp": int(now) + 3600,
                "iat": int(now),
            }

            def _b64url(data: bytes) -> str:
                return base64.urlsafe_b64encode(data).decode("utf-8").rstrip("=")

            header_b64 = _b64url(json.dumps(header).encode("utf-8"))
            claims_b64 = _b64url(json.dumps(claims).encode("utf-8"))
            signing_input = f"{header_b64}.{claims_b64}".encode("utf-8")

            private_key = serialization.load_pem_private_key(
                private_key_pem.encode("utf-8"),
                password=None,
            )
            signature = private_key.sign(
                signing_input,
                padding.PKCS1v15(),
                hashes.SHA256(),
            )
            jwt_assertion = f"{header_b64}.{claims_b64}.{_b64url(signature)}"

            client = _get_http_client()
            resp = await client.post(
                token_uri,
                data={
                    "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                    "assertion": jwt_assertion,
                },
                timeout=15.0,
            )
            if resp.status_code >= 400:
                err_text = resp.text
                return "", f"GCP OAuth2 Token Error (HTTP {resp.status_code}): {err_text}"

            token_data = resp.json()
            access_token = token_data.get("access_token", "")
            expires_in = int(token_data.get("expires_in", 3600))
            if not access_token:
                return "", "No access_token returned by Google OAuth2 token endpoint."

            cls._token_cache[cache_key] = (access_token, now + expires_in)
            return access_token, None
        except Exception as exc:
            logger.exception("Failed to mint Vertex AI access token from Service Account")
            return "", f"Failed to authenticate GCP Service Account: {exc}"


# ---------------------------------------------------------------------------
# Shared pooled HTTP client (HTTP/2 + keep-alive) for provider SSE streams.
# Reusing TLS connections across turns removes a full handshake (~50-300 ms)
# from every token stream start.
# ---------------------------------------------------------------------------

_STREAM_TIMEOUT = httpx.Timeout(None, connect=10.0, read=120.0, write=30.0, pool=10.0)
_http_client: Optional[httpx.AsyncClient] = None
_http_client_loop_id: Optional[int] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client, _http_client_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _http_client is None or _http_client.is_closed or _http_client_loop_id != loop_id:
        _http_client = httpx.AsyncClient(
            http2=True,
            timeout=_STREAM_TIMEOUT,
            limits=httpx.Limits(max_connections=100, max_keepalive_connections=20, keepalive_expiry=60.0),
            follow_redirects=True,
        )
        _http_client_loop_id = loop_id
    return _http_client


async def close_http_client() -> None:
    """Close the shared client (lifespan shutdown)."""
    global _http_client, _http_client_loop_id
    if _http_client is not None and not _http_client.is_closed:
        await _http_client.aclose()
    _http_client = None
    _http_client_loop_id = None


def _resolve_api_key(provider: str, explicit_key: str = "") -> str:
    k = _clean_api_key(explicit_key)
    if k:
        return k
    p = (provider or "openai").lower().strip()
    env_vars = ENV_KEY_MAP.get(p, [])
    for var in env_vars:
        val = _clean_api_key(os.environ.get(var, ""))
        if val:
            return val
    return ""


def _normalize_google_model(model: str, is_vertex: bool = False) -> str:
    m = (model or "").strip()
    # Map common aliases or version typos for Google Gemini endpoints
    model_map = {
        "gemini-2.5-flash-lite": "gemini-2.0-flash-lite",
        "gemini-2.5-flash": "gemini-2.0-flash",
        "gemini-2.5-pro": "gemini-1.5-pro",
        "gemini-2.0-flash-001": "gemini-2.0-flash",
        "gemini-1.5-pro-002": "gemini-1.5-pro-002" if is_vertex else "gemini-1.5-pro",
        "gemini-1.5-flash-002": "gemini-1.5-flash-002" if is_vertex else "gemini-1.5-flash",
    }
    return model_map.get(m, m)


def _normalize_base_url(provider: str, base_url: str) -> str:
    p = (provider or "openai").lower().strip()
    url = (base_url or "").strip()
    if not url:
        if p == "vertex":
            project = VertexAuthManager.resolve_gcp_project()
            location = VertexAuthManager.resolve_gcp_location()
            if project:
                return f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{location}/endpoints/openapi"
            return "https://generativelanguage.googleapis.com/v1beta/openai"
        return DEFAULT_BASE_URLS.get(p, "https://api.openai.com/v1").rstrip("/")
    url = url.rstrip("/")
    # Automatically strip redundant endpoint suffixes if entered/pasted by the user
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    elif url.endswith("/messages") and p == "anthropic":
        url = url[:-len("/messages")].rstrip("/")

    if p in ("vertex", "gemini") or "aiplatform.googleapis.com" in url or "generativelanguage.googleapis.com" in url:
        project = VertexAuthManager.resolve_gcp_project()
        location = VertexAuthManager.resolve_gcp_location()
        if "{PROJECT}" in url or "{project}" in url:
            if project:
                url = url.replace("{PROJECT}", project).replace("{project}", project)
            else:
                return "https://generativelanguage.googleapis.com/v1beta/openai"
        if "{LOCATION}" in url or "{location}" in url:
            url = url.replace("{LOCATION}", location).replace("{location}", location)

        # If user entered an AI Studio project (gen-lang-client-...) or locations/global, route to generativelanguage
        if "gen-lang-client-" in url or "locations/global" in url:
            return "https://generativelanguage.googleapis.com/v1beta/openai"

        # If user entered an aiplatform URL without /endpoints/openapi, format it properly
        if "aiplatform.googleapis.com" in url and not url.endswith("/endpoints/openapi"):
            if "/v1/" in url:
                url = url.replace("/v1/", "/v1beta1/")
            if not url.endswith("/endpoints/openapi"):
                url = f"{url}/endpoints/openapi"

        if "generativelanguage.googleapis.com" in url and not url.endswith("/openai"):
            if not url.endswith("/v1beta"):
                url = f"{url}/v1beta/openai"
            else:
                url = f"{url}/openai"

    return url or DEFAULT_BASE_URLS.get(p, "https://api.openai.com/v1")


def repair_json_string(raw_str: str) -> str:
    """Safely repair unclosed quotes, trailing backslashes, and unclosed braces in truncated JSON strings."""
    if not raw_str or not raw_str.strip():
        return "{}"
    s = raw_str.strip()
    try:
        json.loads(s)
        return s
    except Exception:
        pass

    # Strip trailing backslash
    if s.endswith("\\"):
        s = s[:-1]

    # Iteratively attempt closing open string literals and brackets
    for suffix in ["\"}", "\"}]}", "\"}]", "}", "}]", "\"}]}}", "\"}}}"]:
        candidate = s + suffix
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

    # If simple suffix fails, trim trailing characters back to the last valid token
    for cut in range(1, min(len(s), 300)):
        sub = s[:-cut].rstrip().rstrip("\\")
        for suffix in ["\"}", "}", "\"]}", "\"]"]:
            candidate = sub + suffix
            try:
                json.loads(candidate)
                return candidate
            except Exception:
                pass

    return json.dumps({"raw_content": raw_str[:200]})


def sanitize_openai_messages(messages: List[Dict[str, Any]], system_prompt: Optional[str] = None) -> List[Dict[str, Any]]:
    """Sanitize message sequence and tool calls to strictly adhere to OpenAI spec and prevent HTTP 400 errors."""
    sanitized: List[Dict[str, Any]] = []
    if system_prompt:
        sanitized.append({"role": "system", "content": system_prompt})

    pending_tool_ids = set()

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        m: Dict[str, Any] = {"role": role, "content": content}

        if role == "assistant":
            tool_calls = msg.get("tool_calls")
            if tool_calls and isinstance(tool_calls, list):
                valid_tcs = []
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        continue
                    t_id = tc.get("id") or f"call_{len(valid_tcs)}"
                    func = tc.get("function") or {}
                    name = func.get("name") or tc.get("name") or "syte_tool"
                    raw_args = func.get("arguments") if "arguments" in func else tc.get("arguments")

                    if isinstance(raw_args, dict):
                        valid_args_str = json.dumps(raw_args)
                    elif isinstance(raw_args, str):
                        valid_args_str = repair_json_string(raw_args)
                    else:
                        valid_args_str = "{}"

                    try:
                        json.loads(valid_args_str)
                    except Exception:
                        valid_args_str = "{}"

                    valid_tcs.append({
                        "id": t_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": valid_args_str,
                        },
                    })
                    pending_tool_ids.add(t_id)

                if valid_tcs:
                    m["tool_calls"] = valid_tcs
            sanitized.append(m)

        elif role == "tool":
            t_id = msg.get("tool_call_id") or ""
            t_name = msg.get("name") or "syte_tool"
            tool_content = content if isinstance(content, str) else json.dumps(content)
            m["tool_call_id"] = t_id
            m["name"] = t_name
            m["content"] = tool_content
            if t_id in pending_tool_ids:
                pending_tool_ids.remove(t_id)
            sanitized.append(m)

        elif role in ("user", "system"):
            sanitized.append(m)

    # Synthetic responses for any dangling tool calls
    for missing_id in list(pending_tool_ids):
        sanitized.append({
            "role": "tool",
            "tool_call_id": missing_id,
            "name": "syte_tool",
            "content": json.dumps({"ok": True, "message": "Command executed successfully."}),
        })

    # Ensure the conversation does not begin with an orphan tool message
    start_idx = 0
    if sanitized and sanitized[0]["role"] == "system":
        start_idx = 1
    while start_idx < len(sanitized) and sanitized[start_idx]["role"] == "tool":
        sanitized.pop(start_idx)

    return sanitized


class UnifiedAIClient:
    """Dispatches completions and tool calls to any supported LLM provider."""

    def __init__(
        self,
        provider: str = "openai",
        model: str = "gpt-4o",
        api_key: str = "",
        base_url: str = "",
        temperature: float = 0.7,
        max_tokens: int = 4096,
        thinking_level: str = "medium",
    ):
        self.provider = (provider or "openai").lower().strip()
        self.model = (model or "gpt-4o").strip()
        self.api_key = _resolve_api_key(self.provider, api_key)
        self.base_url = _normalize_base_url(self.provider, base_url)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_level = thinking_level

    async def test_connection(self) -> dict[str, Any]:
        """Test API connectivity and model availability."""
        has_sa = self.provider == "vertex" and VertexAuthManager.parse_service_account(self.api_key) is not None
        if not self.api_key and not has_sa and self.provider not in ("ollama", "custom"):
            return {
                "ok": False,
                "error": f"Missing API key or credentials for {self.provider.upper()}. Please enter your API key or Service Account JSON in AI Settings.",
                "model": self.model,
                "provider": self.provider,
            }

        test_messages = [{"role": "user", "content": "Respond with 'OK' and nothing else."}]
        try:
            full_reply = ""
            async for chunk in self.stream_chat(test_messages, tools=None):
                if chunk.get("type") == "token":
                    full_reply += chunk.get("content", "")
                elif chunk.get("type") == "error":
                    return {"ok": False, "error": chunk.get("content", "Connection error"), "model": self.model, "provider": self.provider}
            return {"ok": True, "reply": full_reply.strip() or "OK", "model": self.model, "provider": self.provider}
        except Exception as exc:
            return {"ok": False, "error": str(exc), "model": self.model, "provider": self.provider}

    async def stream_chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Stream assistant response with real-time SSE chunks across providers."""
        if self.provider == "anthropic":
            async for chunk in self._stream_anthropic(messages, tools=tools, system_prompt=system_prompt):
                yield chunk
        else:
            async for chunk in self._stream_openai_compatible(messages, tools=tools, system_prompt=system_prompt):
                yield chunk

    async def _stream_openai_compatible(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        # Handle Vertex AI explicit credentials and token resolution
        is_vertex = self.provider == "vertex"
        sa_info = None
        vertex_access_token = ""

        if is_vertex:
            sa_info = VertexAuthManager.parse_service_account(self.api_key)
            if sa_info:
                token, err = await VertexAuthManager.get_access_token_from_service_account(sa_info)
                if err or not token:
                    yield {
                        "type": "error",
                        "content": f"Vertex AI Service Account authentication failed: {err or 'Unable to generate access token'}",
                    }
                    return
                vertex_access_token = token
            elif self.api_key.startswith("ya29."):
                vertex_access_token = self.api_key

        if not self.api_key and not vertex_access_token and self.provider not in ("ollama", "custom"):
            yield {
                "type": "error",
                "content": f"Missing API key or credentials for {self.provider.upper()}. Please configure your API key or Service Account in AI Settings.",
            }
            return

        effective_model = self.model
        if self.provider in ("vertex", "gemini") or "generativelanguage.googleapis.com" in (self.base_url or ""):
            effective_model = _normalize_google_model(self.model, is_vertex=is_vertex)

        # Build dynamic base url for Vertex AI if needed
        base_url = self.base_url or ""
        if is_vertex and (not base_url or "aiplatform.googleapis.com" in base_url):
            project = VertexAuthManager.resolve_gcp_project(sa_info=sa_info)
            location = VertexAuthManager.resolve_gcp_location()
            if not project and not self.api_key.startswith("AIza") and not self.api_key.startswith("AQ."):
                yield {
                    "type": "error",
                    "content": "Missing Google Cloud Project ID for Vertex AI. Please configure GOOGLE_CLOUD_PROJECT in environment or provide a GCP Service Account JSON with project_id.",
                }
                return
            if project:
                base_url = f"https://{location}-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/{location}/endpoints/openapi"
            else:
                base_url = "https://generativelanguage.googleapis.com/v1beta/openai"

        url = f"{(base_url or '').rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://syte.internal",
            "X-Title": "Syte AI Builder",
            "User-Agent": "Syte-Autonomous-Agent/1.0",
        }

        if vertex_access_token:
            headers["Authorization"] = f"Bearer {vertex_access_token}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            if self.provider in ("gemini", "vertex") or self.api_key.startswith("AIza") or self.api_key.startswith("AQ."):
                headers["x-goog-api-key"] = self.api_key

        formatted_messages = sanitize_openai_messages(messages, system_prompt=system_prompt)

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        client = _get_http_client()

        async def _open_stream(request_url: str, request_payload: dict) -> tuple[Optional[httpx.Response], str, int]:
            """POST the stream request; return (response, error_message, error_status)."""
            try:
                response = await client.send(
                    client.build_request("POST", request_url, json=request_payload, headers=headers),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                return None, f"Connection failed: {exc}", 0
            except Exception as exc:
                return None, f"Connection failed: {exc}", 0
            if response.status_code < 400:
                return response, "", 0
            err_body = (await response.aread()).decode("utf-8", errors="replace")
            err_code = response.status_code
            await response.aclose()
            err_msg = f"HTTP {err_code}: {response.reason_phrase or ''}"
            if err_body:
                try:
                    parsed = json.loads(err_body)
                    if isinstance(parsed, dict) and "error" in parsed:
                        err_val = parsed["error"]
                        if isinstance(err_val, dict) and "message" in err_val:
                            err_msg = f"{self.provider.upper()} Error (HTTP {err_code}): {err_val['message']}"
                        elif isinstance(err_val, str):
                            err_msg = f"{self.provider.upper()} Error (HTTP {err_code}): {err_val}"
                except Exception:
                    err_msg = f"HTTP {err_code}: {err_body}"
            if err_code in (401, 403):
                err_msg = f"{err_msg} — Please verify your API key, permissions (Vertex AI User role), and billing status in AI Settings."
            return None, err_msg, err_code

        response: Optional[httpx.Response] = None
        last_err_msg = "Unknown error"
        for attempt in range(3):
            response, last_err_msg, err_code = await _open_stream(url, payload)
            if response is not None:
                break
            # If 404 or 400 on custom Vertex endpoint, seamless fallback to Google Generative Language
            if err_code in (404, 400) and (
                self.provider in ("vertex", "gemini") or "googleapis.com" in (url or "")
            ):
                fallback_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                if fallback_url != url:
                    fallback_payload = dict(payload)
                    fallback_payload["model"] = _normalize_google_model(self.model, is_vertex=False)
                    response, fb_err, _fb_code = await _open_stream(fallback_url, fallback_payload)
                    if response is not None:
                        break
                    last_err_msg = fb_err or last_err_msg
            if err_code in (429, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            if "Connection failed" in last_err_msg and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break

        if response is None:
            yield {"type": "error", "content": last_err_msg}
            return

        # Consume the provider SSE stream directly on the event loop
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        try:
            async for line in response.aiter_lines():
                line_str = line.strip()
                if not line_str or line_str.startswith(":"):
                    continue
                if line_str == "data: [DONE]":
                    break
                if not line_str.startswith("data: "):
                    continue
                try:
                    chunk_data = json.loads(line_str[6:])
                except json.JSONDecodeError:
                    continue

                choices = chunk_data.get("choices") or []
                if not choices:
                    continue
                delta = choices[0].get("delta") or {}

                # Thought / Reasoning delta
                thought = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought")
                if thought:
                    yield {"type": "thought", "content": thought}

                # Text token delta
                content = delta.get("content")
                if content:
                    yield {"type": "token", "content": content}

                # Tool call deltas
                raw_tool_calls = delta.get("tool_calls")
                if raw_tool_calls:
                    for tc in raw_tool_calls:
                        idx = tc.get("index")
                        if idx is None:
                            tc_id = tc.get("id") or ""
                            matching_idx = None
                            if tc_id:
                                for existing_idx, existing_tc in tool_calls_acc.items():
                                    if existing_tc.get("id") == tc_id:
                                        matching_idx = existing_idx
                                        break
                            idx = matching_idx if matching_idx is not None else len(tool_calls_acc)

                        func_delta = tc.get("function") or {}
                        f_name = func_delta.get("name") or tc.get("name") or ""
                        f_args = func_delta.get("arguments") or tc.get("arguments") or ""

                        if idx not in tool_calls_acc:
                            tool_calls_acc[idx] = {
                                "id": tc.get("id") or f"call_{idx}",
                                "type": "function",
                                "function": {
                                    "name": f_name,
                                    "arguments": f_args,
                                },
                            }
                        else:
                            if tc.get("id"):
                                tool_calls_acc[idx]["id"] = tc["id"]
                            if f_name:
                                tool_calls_acc[idx]["function"]["name"] += f_name
                            if f_args:
                                tool_calls_acc[idx]["function"]["arguments"] += f_args
        finally:
            await response.aclose()

        # Yield any accumulated tool calls with repaired JSON arguments
        if tool_calls_acc:
            for idx, tc in sorted(tool_calls_acc.items(), key=lambda x: x[0]):
                func_name = tc["function"]["name"].strip()
                raw_args = tc["function"]["arguments"]
                if not func_name:
                    continue
                repaired_args = repair_json_string(raw_args)
                yield {
                    "type": "tool_call",
                    "id": tc["id"],
                    "name": func_name,
                    "arguments": repaired_args,
                }

    async def _stream_anthropic(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self.api_key:
            yield {
                "type": "error",
                "content": "Missing API key for ANTHROPIC. Please configure your API key in AI Settings.",
            }
            return

        url = f"{self.base_url}/messages"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "anthropic-version": "2023-06-01",
            "x-api-key": self.api_key,
        }

        anthropic_messages = []
        for msg in messages:
            role = "assistant" if msg["role"] == "assistant" else "user"
            anthropic_messages.append({"role": role, "content": msg.get("content") or ""})

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": anthropic_messages,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "stream": True,
        }
        if system_prompt:
            payload["system"] = system_prompt

        def _map_anthropic_error(err_code: int, err_body: str) -> str:
            err_msg = f"Anthropic HTTP {err_code}"
            if err_body:
                try:
                    parsed = json.loads(err_body)
                    if isinstance(parsed, dict) and "error" in parsed:
                        err_val = parsed["error"]
                        if isinstance(err_val, dict) and "message" in err_val:
                            err_msg = f"Anthropic Error (HTTP {err_code}): {err_val['message']}"
                        elif isinstance(err_val, str):
                            err_msg = f"Anthropic Error (HTTP {err_code}): {err_val}"
                except Exception:
                    err_msg = f"Anthropic HTTP {err_code}: {err_body}"
            return err_msg

        client = _get_http_client()
        response: Optional[httpx.Response] = None
        last_err_msg = "Unknown error"
        for attempt in range(3):
            try:
                response = await client.send(
                    client.build_request("POST", url, json=payload, headers=headers),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                last_err_msg = f"Anthropic connection failed: {exc}"
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                last_err_msg = f"Anthropic connection failed: {exc}"
                break
            if response.status_code < 400:
                break
            err_body = (await response.aread()).decode("utf-8", errors="replace")
            err_code = response.status_code
            await response.aclose()
            response = None
            last_err_msg = _map_anthropic_error(err_code, err_body)
            if err_code in (429, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break

        if response is None:
            yield {"type": "error", "content": last_err_msg}
            return

        try:
            async for line in response.aiter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                try:
                    data = json.loads(line[5:])
                except json.JSONDecodeError:
                    continue
                event_type = data.get("type")
                if event_type == "content_block_delta":
                    delta = data.get("delta") or {}
                    delta_type = delta.get("type")
                    if delta_type == "text_delta" and delta.get("text"):
                        yield {"type": "token", "content": delta["text"]}
                    elif delta_type == "thinking_delta" and delta.get("thinking"):
                        yield {"type": "thought", "content": delta["thinking"]}
                elif event_type == "message_stop":
                    break
                elif event_type == "error":
                    err = data.get("error") or {}
                    yield {"type": "error", "content": f"Anthropic stream error: {err.get('message') or err}"}
                    break
        finally:
            await response.aclose()
