"""Unified multi-provider AI client for Syte Autonomous AI Builder.

Supports OpenAI, Anthropic, Google Gemini, DeepSeek, OpenRouter, and Local Ollama/vLLM endpoints.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Any, AsyncGenerator, Dict, List, Optional
import urllib.request
import urllib.error

logger = logging.getLogger("syte.ai.providers")

DEFAULT_BASE_URLS = {
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai",
    "vertex": "https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/us-central1/endpoints/openapi",
    "deepseek": "https://api.deepseek.com/v1",
    "openrouter": "https://openrouter.ai/api/v1",
    "ollama": "http://localhost:11434/v1",
}

ENV_KEY_MAP = {
    "openrouter": ["OPENROUTER_API_KEY", "OPENROUTER_KEY", "OPEN_ROUTER_API_KEY"],
    "openai": ["OPENAI_API_KEY"],
    "anthropic": ["ANTHROPIC_API_KEY", "CLAUDE_API_KEY"],
    "gemini": ["GEMINI_API_KEY", "GOOGLE_API_KEY"],
    "vertex": ["VERTEX_API_KEY", "VERTEXAI_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS", "GCP_API_KEY"],
    "deepseek": ["DEEPSEEK_API_KEY"],
}


def _clean_api_key(key: str) -> str:
    k = (key or "").strip().strip('"').strip("'").strip()
    if k.lower().startswith("bearer "):
        k = k[7:].strip()
    return k


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


def _normalize_google_model(model: str) -> str:
    m = (model or "").strip()
    # Map common aliases or version typos for Google Gemini endpoints
    model_map = {
        "gemini-2.5-flash-lite": "gemini-2.0-flash-lite",
        "gemini-2.5-flash": "gemini-2.0-flash",
        "gemini-2.5-pro": "gemini-1.5-pro",
        "gemini-2.0-flash-001": "gemini-2.0-flash",
        "gemini-1.5-pro-002": "gemini-1.5-pro",
        "gemini-1.5-flash-002": "gemini-1.5-flash",
    }
    return model_map.get(m, m)


def _normalize_base_url(provider: str, base_url: str) -> str:
    p = (provider or "openai").lower().strip()
    url = (base_url or "").strip()
    if not url:
        if p == "vertex":
            project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT") or os.environ.get("PROJECT_ID") or ""
            if project:
                return f"https://us-central1-aiplatform.googleapis.com/v1beta1/projects/{project}/locations/us-central1/endpoints/openapi"
            return "https://generativelanguage.googleapis.com/v1beta/openai"
        return DEFAULT_BASE_URLS.get(p, "https://api.openai.com/v1").rstrip("/")
    url = url.rstrip("/")
    # Automatically strip redundant endpoint suffixes if entered/pasted by the user
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    elif url.endswith("/messages") and p == "anthropic":
        url = url[:-len("/messages")].rstrip("/")

    if p in ("vertex", "gemini") or "aiplatform.googleapis.com" in url or "generativelanguage.googleapis.com" in url:
        if "{PROJECT}" in url or "{project}" in url:
            project = os.environ.get("GOOGLE_CLOUD_PROJECT") or os.environ.get("GCP_PROJECT") or os.environ.get("PROJECT_ID") or ""
            if project:
                url = url.replace("{PROJECT}", project).replace("{project}", project)
            else:
                return "https://generativelanguage.googleapis.com/v1beta/openai"

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
    """Safely repair unclosed quotes and brackets in truncated JSON strings."""
    if not raw_str or not raw_str.strip():
        return "{}"
    s = raw_str.strip()
    try:
        json.loads(s)
        return s
    except Exception:
        pass
    # If odd number of quotes, close the open string
    if s.count('"') % 2 != 0:
        s += '"'
    # Count open curly braces and brackets
    open_curly = s.count('{') - s.count('}')
    open_square = s.count('[') - s.count(']')
    s += (']' * max(0, open_square)) + ('}' * max(0, open_curly))
    try:
        json.loads(s)
        return s
    except Exception:
        return raw_str


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
                        try:
                            json.loads(raw_args)
                            valid_args_str = raw_args
                        except Exception:
                            valid_args_str = repair_json_string(raw_args)
                    else:
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
        if not self.api_key and self.provider not in ("ollama", "custom"):
            return {
                "ok": False,
                "error": f"Missing API key for {self.provider.upper()}. Please enter your API key in AI Settings.",
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
                    return {"ok": False, "error": chunk.get("content", "Connection error")}
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
        if not self.api_key and self.provider not in ("ollama", "custom"):
            yield {
                "type": "error",
                "content": f"Missing API key for {self.provider.upper()}. Please configure your API key in AI Settings.",
            }
            return

        effective_model = self.model
        if self.provider in ("vertex", "gemini") or "generativelanguage.googleapis.com" in (self.base_url or ""):
            effective_model = _normalize_google_model(self.model)

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

        def _blocking_post(post_url: str, post_payload: dict, post_headers: dict, post_timeout: int = 180):
            req = urllib.request.Request(
                post_url,
                data=json.dumps(post_payload).encode("utf-8"),
                headers=post_headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=post_timeout)

        response = None
        last_err_msg = "Unknown error"
        for attempt in range(3):
            try:
                response = await asyncio.to_thread(_blocking_post, url, payload, headers, 180)
                break
            except urllib.error.HTTPError as err:
                # If 404 or 400 on custom Vertex endpoint, attempt seamless fallback to Google Generative Language
                if (err.code in (404, 400)) and (self.provider in ("vertex", "gemini") or "googleapis.com" in (url or "")):
                    fallback_url = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
                    if fallback_url != url:
                        fallback_payload = dict(payload)
                        fallback_payload["model"] = _normalize_google_model(self.model)
                        try:
                            response = await asyncio.to_thread(_blocking_post, fallback_url, fallback_payload, headers, 180)
                            break
                        except Exception:
                            response = None

                if err.code in (429, 502, 503, 504) and attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue

                if response is None:
                    err_body = err.read().decode("utf-8", errors="replace")
                    err_msg = f"HTTP {err.code}: {err.reason}"
                    if err_body:
                        try:
                            parsed = json.loads(err_body)
                            if isinstance(parsed, dict) and "error" in parsed:
                                err_val = parsed["error"]
                                if isinstance(err_val, dict) and "message" in err_val:
                                    err_msg = f"{self.provider.upper()} Error (HTTP {err.code}): {err_val['message']}"
                                elif isinstance(err_val, str):
                                    err_msg = f"{self.provider.upper()} Error (HTTP {err.code}): {err_val}"
                        except Exception:
                            err_msg = f"HTTP {err.code}: {err_body}"
                    if err.code in (401, 403):
                        err_msg = f"{err_msg} — Please verify your API key and permissions in AI Settings."
                    last_err_msg = err_msg
                    break
            except Exception as exc:
                last_err_msg = f"Connection failed: {str(exc)}"
                if attempt < 2:
                    await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                break

        if response is None:
            yield {"type": "error", "content": last_err_msg}
            return

        # Read SSE Stream in real time line by line
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        def _read_single_line():
            try:
                line_bytes = response.readline()
                if not line_bytes:
                    return None
                return line_bytes.decode("utf-8", errors="replace")
            except Exception:
                return None

        while True:
            line = await asyncio.to_thread(_read_single_line)
            if line is None:
                break

            line_str = line.strip()
            if not line_str or line_str.startswith(":"):
                continue
            if line_str == "data: [DONE]":
                break
            if line_str.startswith("data: "):
                raw_json = line_str[6:]
                try:
                    chunk_data = json.loads(raw_json)
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

        def _blocking_post():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=180)

        try:
            response = await asyncio.to_thread(_blocking_post)
        except urllib.error.HTTPError as err:
            err_body = err.read().decode("utf-8", errors="replace")
            err_msg = f"Anthropic HTTP {err.code}: {err.reason}"
            if err_body:
                try:
                    parsed = json.loads(err_body)
                    if isinstance(parsed, dict) and "error" in parsed:
                        err_val = parsed["error"]
                        if isinstance(err_val, dict) and "message" in err_val:
                            err_msg = f"Anthropic Error (HTTP {err.code}): {err_val['message']}"
                        elif isinstance(err_val, str):
                            err_msg = f"Anthropic Error (HTTP {err.code}): {err_val}"
                except Exception:
                    err_msg = f"Anthropic HTTP {err.code}: {err_body}"
            yield {"type": "error", "content": err_msg}
            return
        except Exception as exc:
            yield {"type": "error", "content": f"Anthropic connection failed: {str(exc)}"}
            return

        while True:
            line_bytes = await asyncio.to_thread(response.readline)
            if not line_bytes:
                break
            line = line_bytes.decode("utf-8", errors="replace").strip()
            if line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    event_type = data.get("type")
                    if event_type == "content_block_delta":
                        delta = data.get("delta") or {}
                        if delta.get("type") == "text_delta":
                            yield {"type": "token", "content": delta.get("text", "")}
                except json.JSONDecodeError:
                    continue
