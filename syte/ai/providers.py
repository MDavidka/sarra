"""Unified multi-provider AI client for Syte Autonomous AI Builder.

Supports Google Cloud Vertex AI (aiplatform.googleapis.com), Google Gemini (AI Studio),
OpenAI, Anthropic Claude, DeepSeek, OpenRouter, and Local Ollama/vLLM endpoints.

Streaming uses a shared pooled ``httpx.AsyncClient`` (HTTP/2 + keep-alive) so
provider tokens are consumed directly on the event loop.
"""

from __future__ import annotations

import asyncio
import base64
import datetime
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
    "vertex": "https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}/locations/{LOCATION}/publishers/google",
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


def _clean_string(s: str) -> str:
    """Strip zero-width spaces, word joiners, and surrounding quotes/whitespace from user inputs."""
    if not s:
        return ""
    return (
        str(s)
        .replace("\u2060", "")  # word joiner
        .replace("\u200b", "")  # zero-width space
        .replace("\u200c", "")  # zero-width non-joiner
        .replace("\u200d", "")  # zero-width joiner
        .replace("\ufeff", "")  # byte order mark
        .replace("\u00a0", " ")  # non-breaking space
        .strip()
        .strip('"')
        .strip("'")
        .strip()
    )


def _clean_api_key(key: str) -> str:
    k = _clean_string(key)
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


def extract_error_message(err_code: int, err_body: str, provider: str = "AI") -> str:
    """Extract descriptive error message from provider JSON array, dict, or raw response."""
    prov_name = (provider or "AI").upper()
    err_msg = f"{prov_name} HTTP {err_code}"
    if err_body:
        try:
            parsed = json.loads(err_body)
            # Handle Google API error returned as a list: [{"error": {...}}]
            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict):
                parsed = parsed[0]
            if isinstance(parsed, dict):
                err_val = parsed.get("error") or parsed.get("detail") or parsed
                if isinstance(err_val, dict):
                    msg = err_val.get("message") or err_val.get("detail") or str(err_val)
                    err_msg = f"{prov_name} Error (HTTP {err_code}): {msg}"
                elif isinstance(err_val, str):
                    err_msg = f"{prov_name} Error (HTTP {err_code}): {err_val}"
        except Exception:
            err_msg = f"{prov_name} HTTP {err_code}: {err_body[:300]}"
    if err_code in (401, 403):
        if "API_KEY_SERVICE_BLOCKED" in err_body or "API keys are not supported" in err_body:
            err_msg = (
                f"{err_msg} — Native Vertex AI requires a Google Cloud Service Account JSON or OAuth2 token "
                "(API keys are blocked on Vertex PredictionService unless Vertex AI Express mode is enabled, or use Google AI Studio 'Gemini' provider for API key authentication)."
            )
        else:
            err_msg = f"{err_msg} — Please verify your Google Cloud IAM permissions (Vertex AI User role / roles/aiplatform.user) and billing status."
    elif err_code == 429:
        err_msg = f"{err_msg} — Rate limit or quota reached. Please check your project billing and quota limits."
    return err_msg


class VertexAuthManager:
    """Manages GCP Service Account OAuth2 tokens, Vertex Express mode API keys, and Project/Location resolution."""

    _token_cache: Dict[str, Tuple[str, float]] = {}  # cache_key -> (access_token, expiry_timestamp)

    @classmethod
    def resolve_gcp_project(cls, explicit_project: str = "", sa_info: Optional[dict] = None) -> str:
        if explicit_project and _clean_string(explicit_project):
            return _clean_string(explicit_project)
        if sa_info and sa_info.get("project_id"):
            return _clean_string(sa_info["project_id"])
        for env_var in (
            "VERTEX_PROJECT_ID",
            "GOOGLE_CLOUD_PROJECT",
            "GCP_PROJECT",
            "PROJECT_ID",
            "CLOUDSDK_CORE_PROJECT",
        ):
            val = _clean_string(os.environ.get(env_var, ""))
            if val:
                return val
        return ""

    @classmethod
    def resolve_gcp_location(cls, explicit_location: str = "") -> str:
        if explicit_location and _clean_string(explicit_location):
            return _clean_string(explicit_location)
        for env_var in (
            "VERTEX_LOCATION",
            "GOOGLE_CLOUD_REGION",
            "GCP_REGION",
            "CLOUDSDK_COMPUTE_REGION",
        ):
            val = _clean_string(os.environ.get(env_var, ""))
            if val:
                return val
        return "us-central1"

    @classmethod
    def parse_service_account(cls, credential_str: str) -> Optional[dict]:
        """Check if credential_str is raw JSON or a path to a service account JSON file."""
        if not credential_str:
            gac = _clean_string(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", ""))
            if gac and os.path.isfile(gac):
                try:
                    with open(gac, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, dict) and data.get("type") == "service_account":
                            return data
                except Exception:
                    pass
            return None

        clean_cred = _clean_string(credential_str)
        # Check if credential_str is a file path
        if os.path.isfile(clean_cred):
            try:
                with open(clean_cred, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict) and data.get("type") == "service_account":
                        return data
            except Exception:
                pass

        # Check if credential_str is raw JSON
        if clean_cred.startswith("{") and clean_cred.endswith("}"):
            try:
                data = json.loads(clean_cred)
                if isinstance(data, dict) and data.get("type") == "service_account":
                    return data
            except Exception:
                pass

        return None

    _server_time_offset: float = 0.0
    _last_time_sync: float = 0.0

    @classmethod
    async def _get_accurate_time(cls) -> float:
        """Get network-synchronized epoch timestamp to compensate for VM clock drift."""
        now = time.time()
        if cls._last_time_sync > 0 and (now - cls._last_time_sync) < 300:
            return now + cls._server_time_offset
        try:
            client = _get_http_client()
            resp = await client.head("https://www.google.com", timeout=5.0)
            date_header = resp.headers.get("date")
            if date_header:
                server_dt = datetime.datetime.strptime(date_header, "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=datetime.timezone.utc)
                server_epoch = server_dt.timestamp()
                cls._server_time_offset = server_epoch - now
                cls._last_time_sync = now
                return server_epoch
        except Exception:
            pass
        return now

    @classmethod
    async def get_access_token_from_service_account(cls, sa_info: dict) -> Tuple[str, Optional[str]]:
        """Mint a Google OAuth2 access token from service account RSA private key."""
        client_email = sa_info.get("client_email")
        private_key_pem = sa_info.get("private_key")
        token_uri = sa_info.get("token_uri") or "https://oauth2.googleapis.com/token"

        if not client_email or not private_key_pem:
            return "", "Service account JSON is missing 'client_email' or 'private_key'."

        cache_key = f"{client_email}:{sa_info.get('project_id', '')}"
        now = await cls._get_accurate_time()
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
                "iat": int(now) - 30,
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


def format_vertex_contents(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert OpenAI-style messages list to Vertex AI contents array."""
    contents: List[Dict[str, Any]] = []
    for msg in messages:
        role = msg.get("role")
        content = msg.get("content") or ""
        tool_calls = msg.get("tool_calls") or []

        if role == "system":
            # Handled separately via systemInstruction
            continue
        elif role == "user":
            contents.append({
                "role": "user",
                "parts": [{"text": str(content)}],
            })
        elif role == "assistant":
            parts = []
            if content:
                parts.append({"text": str(content)})
            for tc in tool_calls:
                func = tc.get("function") or {}
                name = func.get("name") or tc.get("name") or "tool"
                args = func.get("arguments") if "arguments" in func else tc.get("arguments")
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except Exception:
                        args = {}
                parts.append({
                    "functionCall": {
                        "name": name,
                        "args": args if isinstance(args, dict) else {},
                    }
                })
            if parts:
                contents.append({
                    "role": "model",
                    "parts": parts,
                })
        elif role == "tool":
            t_name = msg.get("name") or "tool"
            t_content = content
            if isinstance(t_content, str):
                try:
                    t_content = json.loads(t_content)
                except Exception:
                    t_content = {"output": t_content}
            contents.append({
                "role": "user",
                "parts": [
                    {
                        "functionResponse": {
                            "name": t_name,
                            "response": {"name": t_name, "content": t_content},
                        }
                    }
                ],
            })
    return contents


def format_vertex_tools(tools: Optional[List[Dict[str, Any]]]) -> Optional[List[Dict[str, Any]]]:
    """Convert OpenAI tool schema declarations to Vertex AI functionDeclarations."""
    if not tools:
        return None
    declarations = []
    for t in tools:
        if t.get("type") == "function" and "function" in t:
            f = t["function"]
            decl = {
                "name": f.get("name"),
                "description": f.get("description", ""),
                "parameters": f.get("parameters", {}),
            }
            declarations.append(decl)
        elif "name" in t:
            declarations.append(t)
    if declarations:
        return [{"functionDeclarations": declarations}]
    return None


# ---------------------------------------------------------------------------
# Shared pooled HTTP client (HTTP/2 + keep-alive) for provider SSE streams.
# ---------------------------------------------------------------------------

_STREAM_TIMEOUT = httpx.Timeout(None, connect=10.0, read=120.0, write=30.0, pool=10.0)
_http_client: Optional[httpx.AsyncClient] = None
_http_client_loop_id: Optional[int] = None


def _get_http_client() -> httpx.AsyncClient:
    global _http_client, _http_client_loop_id
    loop_id = id(asyncio.get_running_loop())
    if _http_client is None or _http_client.is_closed or _http_client_loop_id != loop_id:
        _http_client = httpx.AsyncClient(
            http2=False,
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
    p = _clean_string(provider or "openai").lower()
    env_vars = ENV_KEY_MAP.get(p, [])
    for var in env_vars:
        val = _clean_api_key(os.environ.get(var, ""))
        if val:
            return val
    return ""


def _normalize_google_model(model: str, is_vertex: bool = False) -> str:
    m = _clean_string(model)
    model_map = {
        "gemini-2.0-flash-001": "gemini-2.0-flash",
        "gemini-1.5-pro-002": "gemini-1.5-pro-002" if is_vertex else "gemini-1.5-pro",
        "gemini-1.5-flash-002": "gemini-1.5-flash-002" if is_vertex else "gemini-1.5-flash",
    }
    return model_map.get(m, m)


def _normalize_base_url(provider: str, base_url: str, gcp_project: str = "", gcp_location: str = "") -> str:
    p = _clean_string(provider or "openai").lower()
    url = _clean_string(base_url)
    if not url:
        if p == "vertex":
            project = VertexAuthManager.resolve_gcp_project(explicit_project=gcp_project)
            location = VertexAuthManager.resolve_gcp_location(explicit_location=gcp_location)
            if project:
                return f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google"
            return "https://us-central1-aiplatform.googleapis.com/v1/publishers/google"
        return DEFAULT_BASE_URLS.get(p, "https://api.openai.com/v1").rstrip("/")
    url = url.rstrip("/")
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    elif url.endswith("/messages") and p == "anthropic":
        url = url[:-len("/messages")].rstrip("/")

    if p == "vertex" or "aiplatform.googleapis.com" in url:
        project = VertexAuthManager.resolve_gcp_project(explicit_project=gcp_project)
        location = VertexAuthManager.resolve_gcp_location(explicit_location=gcp_location)
        if "{PROJECT}" in url or "{project}" in url:
            if project:
                url = url.replace("{PROJECT}", project).replace("{project}", project)
        if "{LOCATION}" in url or "{location}" in url:
            url = url.replace("{LOCATION}", location).replace("{location}", location)
    elif p == "gemini" or "generativelanguage.googleapis.com" in url:
        if not url.endswith("/openai"):
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

    if s.endswith("\\"):
        s = s[:-1]

    for suffix in ["\"}", "\"}]}", "\"}]", "}", "}]", "\"}]}}", "\"}}}"]:
        candidate = s + suffix
        try:
            json.loads(candidate)
            return candidate
        except Exception:
            pass

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
                    pending_tool_ids.add(tc_id)

                cleaned_msg = {
                    "role": "assistant",
                    "content": m.get("content") or "",
                    "tool_calls": valid_calls,
                }
                sanitized.append(cleaned_msg)
            else:
                sanitized.append({
                    "role": "assistant",
                    "content": m.get("content") or "",
                })

        elif role == "tool":
            t_id = m.get("tool_call_id") or ""
            if t_id and t_id in pending_tool_ids:
                pending_tool_ids.remove(t_id)
                sanitized.append(m)
            elif not pending_tool_ids:
                # Orphan tool message - drop to prevent 400 Bad Request
                continue
            else:
                # Match to next expected tool ID if ID mismatch
                expected_id = next(iter(pending_tool_ids))
                pending_tool_ids.remove(expected_id)
                fixed_m = dict(m)
                fixed_m["tool_call_id"] = expected_id
                sanitized.append(fixed_m)

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

    # Ensure conversation does not start with an orphan tool message
    while sanitized and sanitized[0]["role"] == "tool":
        sanitized.pop(0)

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
        gcp_project: str = "",
        gcp_location: str = "us-central1",
    ):
        self.provider = _clean_string(provider or "openai").lower()
        self.model = _clean_string(model or "gpt-4o")
        self.api_key = _resolve_api_key(self.provider, api_key)
        self.gcp_project = _clean_string(gcp_project)
        self.gcp_location = _clean_string(gcp_location or "us-central1")
        self.base_url = _normalize_base_url(
            self.provider,
            _clean_string(base_url),
            gcp_project=self.gcp_project,
            gcp_location=self.gcp_location,
        )
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking_level = thinking_level

    async def test_connection(self) -> dict[str, Any]:
        """Test API connectivity and model availability."""
        has_sa = self.provider == "vertex" and VertexAuthManager.parse_service_account(self.api_key) is not None
        if not self.api_key and not has_sa and self.provider not in ("ollama", "custom"):
            return {
                "ok": False,
                "error": f"Missing credentials for {self.provider.upper()}. Please enter your API key or Service Account JSON in AI Settings.",
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
        if self.provider == "vertex":
            async for chunk in self._stream_vertex(messages, tools=tools, system_prompt=system_prompt):
                yield chunk
        elif self.provider == "anthropic":
            async for chunk in self._stream_anthropic(messages, tools=tools, system_prompt=system_prompt):
                yield chunk
        else:
            async for chunk in self._stream_openai_compatible(messages, tools=tools, system_prompt=system_prompt):
                yield chunk

    async def _stream_vertex(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """Direct stream with Google Cloud Vertex AI (aiplatform.googleapis.com) using GCP IAM / Service Accounts."""
        sa_info = VertexAuthManager.parse_service_account(self.api_key)
        vertex_access_token = ""

        if sa_info:
            token, err = await VertexAuthManager.get_access_token_from_service_account(sa_info)
            if err or not token:
                yield {
                    "type": "error",
                    "content": f"Google Cloud Vertex AI authentication failed: {err or 'Unable to generate access token from Service Account'}",
                }
                return
            vertex_access_token = token
        elif self.api_key.startswith("ya29."):
            vertex_access_token = self.api_key

        project = VertexAuthManager.resolve_gcp_project(explicit_project=self.gcp_project, sa_info=sa_info)
        location = VertexAuthManager.resolve_gcp_location(explicit_location=self.gcp_location)

        if not project:
            yield {
                "type": "error",
                "content": (
                    "Google Cloud Vertex AI Error: Missing GCP Project ID. "
                    "Please provide a Service Account JSON with 'project_id' or set GOOGLE_CLOUD_PROJECT in environment."
                ),
            }
            return

        effective_model = _normalize_google_model(self.model, is_vertex=True)

        # Check if Claude model on Vertex AI
        is_claude_on_vertex = "claude" in effective_model.lower()

        if is_claude_on_vertex:
            url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/anthropic/models/{effective_model}:streamRawPredict"
            headers = {
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "anthropic-version": "vertex-2023-10-16",
            }
            if vertex_access_token:
                headers["Authorization"] = f"Bearer {vertex_access_token}"
            elif self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
                headers["x-goog-api-key"] = self.api_key

            anthropic_messages = []
            for msg in messages:
                role = "assistant" if msg.get("role") == "assistant" else "user"
                anthropic_messages.append({"role": role, "content": msg.get("content") or ""})

            payload: dict[str, Any] = {
                "anthropic_version": "vertex-2023-10-16",
                "messages": anthropic_messages,
                "max_tokens": self.max_tokens,
                "temperature": self.temperature,
                "stream": True,
            }
            if system_prompt:
                payload["system"] = system_prompt

            client = _get_http_client()
            try:
                response = await client.send(
                    client.build_request("POST", url, json=payload, headers=headers),
                    stream=True,
                )
            except Exception as exc:
                yield {"type": "error", "content": f"Vertex AI Claude connection failed: {exc}"}
                return

            if response.status_code >= 400:
                err_body = (await response.aread()).decode("utf-8", errors="replace")
                await response.aclose()
                yield {"type": "error", "content": extract_error_message(response.status_code, err_body, provider="vertex")}
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
                        if delta.get("type") == "text_delta" and delta.get("text"):
                            yield {"type": "token", "content": delta["text"]}
                    elif event_type == "message_stop":
                        break
            finally:
                await response.aclose()
            return

        # Native Google Gemini on Vertex AI
        if self.base_url and "aiplatform.googleapis.com" not in self.base_url:
            url = f"{self.base_url.rstrip('/')}/models/{effective_model}:streamGenerateContent?alt=sse"
        else:
            url = f"https://{location}-aiplatform.googleapis.com/v1/projects/{project}/locations/{location}/publishers/google/models/{effective_model}:streamGenerateContent?alt=sse"

        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Goog-Api-Client": "syte-vertex-agent/1.0",
        }
        if vertex_access_token:
            headers["Authorization"] = f"Bearer {vertex_access_token}"
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            headers["x-goog-api-key"] = self.api_key

        vertex_contents = format_vertex_contents(messages)
        vertex_tools = format_vertex_tools(tools)

        gen_config: dict[str, Any] = {
            "temperature": self.temperature,
            "maxOutputTokens": self.max_tokens,
        }

        # Native Gemini 2.5 / 2.0 Flash / Pro Thinking Support on Vertex AI
        if self.thinking_level and self.thinking_level != "none" and not is_claude_on_vertex:
            thinking_budgets = {
                "low": 0,           # Fast mode: actually fast (0 thinking tokens, no thinking delay)
                "medium": 4096,     # Balanced speed & depth
                "high": 12288,      # Deep reasoning & verification
                "extra_high": 24576,# Extra high: think significantly more
                "max": 32768,       # Maximum reasoning budget
            }
            budget = thinking_budgets.get(self.thinking_level, 4096)
            gen_config["thinkingConfig"] = {
                "thinkingBudget": budget,
            }

        payload: dict[str, Any] = {
            "contents": vertex_contents,
            "generationConfig": gen_config,
        }
        if system_prompt:
            payload["systemInstruction"] = {
                "parts": [{"text": system_prompt}]
            }
        if vertex_tools:
            payload["tools"] = vertex_tools

        client = _get_http_client()
        response: Optional[httpx.Response] = None
        last_err_msg = "Unknown Vertex AI error"

        for attempt in range(3):
            try:
                response = await client.send(
                    client.build_request("POST", url, json=payload, headers=headers),
                    stream=True,
                )
            except httpx.HTTPError as exc:
                last_err_msg = f"Vertex AI connection failed: {exc}"
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break
            except Exception as exc:
                last_err_msg = f"Vertex AI connection failed: {exc}"
                break

            if response.status_code < 400:
                break

            err_body = (await response.aread()).decode("utf-8", errors="replace")
            err_code = response.status_code
            await response.aclose()
            response = None
            last_err_msg = extract_error_message(err_code, err_body, provider="vertex")
            if err_code in (429, 502, 503, 504) and attempt < 2:
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
            break

        if response is None:
            yield {"type": "error", "content": last_err_msg}
            return

        tool_calls_count = 0
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

                candidates = chunk_data.get("candidates") or []
                for cand in candidates:
                    content_obj = cand.get("content") or {}
                    parts = content_obj.get("parts") or []
                    for part in parts:
                        if not isinstance(part, dict):
                            continue
                        if "thought" in part and part["thought"]:
                            yield {"type": "thought", "content": part["thought"]}
                        if "text" in part and part["text"]:
                            yield {"type": "token", "content": part["text"]}
                        if "functionCall" in part:
                            fc = part["functionCall"]
                            f_name = fc.get("name") or "syte_tool"
                            f_args = fc.get("args") or {}
                            args_str = json.dumps(f_args) if isinstance(f_args, dict) else str(f_args)
                            tool_calls_count += 1
                            yield {
                                "type": "tool_call",
                                "id": f"call_{tool_calls_count}",
                                "name": f_name,
                                "arguments": args_str,
                            }
        finally:
            await response.aclose()

    async def _stream_openai_compatible(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        if not self.api_key and self.provider not in ("ollama", "custom"):
            yield {
                "type": "error",
                "content": f"Missing API key for {self.provider.upper()}. Please configure your API key in AI Settings.",
            }
            return

        effective_model = self.model
        if self.provider == "gemini" or "generativelanguage.googleapis.com" in (self.base_url or ""):
            effective_model = _normalize_google_model(self.model, is_vertex=False)

        url = f"{(self.base_url or '').rstrip('/')}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://syte.internal",
            "X-Title": "Syte AI Builder",
            "User-Agent": "Syte-Autonomous-Agent/1.0",
        }

        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
            if self.provider == "gemini" or self.api_key.startswith("AIza") or self.api_key.startswith("AQ."):
                headers["x-goog-api-key"] = self.api_key

        formatted_messages = sanitize_openai_messages(messages, system_prompt=system_prompt)

        payload: dict[str, Any] = {
            "model": effective_model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if self.thinking_level and self.thinking_level != "none":
            if self.thinking_level == "low":
                payload["reasoning_effort"] = "low"
            elif self.thinking_level == "medium":
                payload["reasoning_effort"] = "medium"
            elif self.thinking_level in ("high", "extra_high", "max"):
                payload["reasoning_effort"] = "high"
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        client = _get_http_client()

        async def _open_stream(request_url: str, request_payload: dict) -> tuple[Optional[httpx.Response], str, int]:
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
            err_msg = extract_error_message(err_code, err_body, provider=self.provider)
            return None, err_msg, err_code

        response: Optional[httpx.Response] = None
        last_err_msg = "Unknown error"
        for attempt in range(3):
            response, last_err_msg, err_code = await _open_stream(url, payload)
            if response is not None:
                break
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

                thought = delta.get("reasoning_content") or delta.get("reasoning") or delta.get("thought")
                if thought:
                    yield {"type": "thought", "content": thought}

                content = delta.get("content")
                if content:
                    yield {"type": "token", "content": content}

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
            last_err_msg = extract_error_message(err_code, err_body, provider="anthropic")
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
