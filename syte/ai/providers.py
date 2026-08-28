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


def _normalize_base_url(provider: str, base_url: str) -> str:
    p = (provider or "openai").lower().strip()
    url = (base_url or "").strip()
    if not url:
        return DEFAULT_BASE_URLS.get(p, "https://api.openai.com/v1").rstrip("/")
    url = url.rstrip("/")
    # Automatically strip redundant endpoint suffixes if entered/pasted by the user
    if url.endswith("/chat/completions"):
        url = url[:-len("/chat/completions")].rstrip("/")
    elif url.endswith("/messages") and p == "anthropic":
        url = url[:-len("/messages")].rstrip("/")
    return url


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
        """Stream chat completions and tool calls from the configured LLM provider."""
        if self.provider == "anthropic":
            async for chunk in self._stream_anthropic(messages, tools, system_prompt):
                yield chunk
        else:
            # OpenAI, Gemini, DeepSeek, OpenRouter, Ollama, and generic OpenAI-compatible
            async for chunk in self._stream_openai_compatible(messages, tools, system_prompt):
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

        url = f"{self.base_url}/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "HTTP-Referer": "https://syte.internal",
            "X-Title": "Syte AI Builder",
            "User-Agent": "Syte-Autonomous-Agent/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        formatted_messages = []
        if system_prompt:
            formatted_messages.append({"role": "system", "content": system_prompt})
        for msg in messages:
            m = {"role": msg["role"], "content": msg.get("content") or ""}
            if msg.get("tool_calls"):
                m["tool_calls"] = msg["tool_calls"]
            if msg.get("tool_call_id"):
                m["tool_call_id"] = msg["tool_call_id"]
            if msg.get("name"):
                m["name"] = msg["name"]
            formatted_messages.append(m)

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": formatted_messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": True,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        def _blocking_post():
            req = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers=headers,
                method="POST",
            )
            return urllib.request.urlopen(req, timeout=45)

        try:
            response = await asyncio.to_thread(_blocking_post)
        except urllib.error.HTTPError as err:
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
            yield {"type": "error", "content": err_msg}
            return
        except Exception as exc:
            yield {"type": "error", "content": f"Connection failed: {str(exc)}"}
            return

        # Read SSE Stream
        tool_calls_acc: dict[int, dict[str, Any]] = {}

        def _read_lines():
            lines = []
            while True:
                line = response.readline()
                if not line:
                    break
                lines.append(line.decode("utf-8", errors="replace"))
                if len(lines) >= 8:
                    break
            return lines

        while True:
            lines = await asyncio.to_thread(_read_lines)
            if not lines:
                break

            for line in lines:
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
                            idx = tc.get("index", 0)
                            if idx not in tool_calls_acc:
                                tool_calls_acc[idx] = {
                                    "id": tc.get("id") or f"call_{idx}",
                                    "type": "function",
                                    "function": {
                                        "name": tc.get("function", {}).get("name", ""),
                                        "arguments": tc.get("function", {}).get("arguments", ""),
                                    },
                                }
                            else:
                                if tc.get("id"):
                                    tool_calls_acc[idx]["id"] = tc["id"]
                                if tc.get("function", {}).get("name"):
                                    tool_calls_acc[idx]["function"]["name"] += tc["function"]["name"]
                                if tc.get("function", {}).get("arguments"):
                                    tool_calls_acc[idx]["function"]["arguments"] += tc["function"]["arguments"]

        # Yield any accumulated tool calls
        if tool_calls_acc:
            for idx, tc in sorted(tool_calls_acc.items(), key=lambda x: x[0]):
                yield {
                    "type": "tool_call",
                    "id": tc["id"],
                    "name": tc["function"]["name"],
                    "arguments": tc["function"]["arguments"],
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
            return urllib.request.urlopen(req, timeout=45)

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
