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
import httpx

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

    async def list_available_models(self) -> List[Dict[str, Any]]:
        """Request and retrieve available models from the provider or return curated lists."""
        curated_defaults: Dict[str, List[Dict[str, Any]]] = {
            "openai": [
                {"id": "gpt-4o", "name": "GPT-4o (Omni)", "context_window": 128000, "recommended": True},
                {"id": "gpt-4o-mini", "name": "GPT-4o Mini", "context_window": 128000, "recommended": True},
                {"id": "o1-preview", "name": "o1 (Reasoning)", "context_window": 128000},
                {"id": "o3-mini", "name": "o3-mini", "context_window": 200000},
            ],
            "anthropic": [
                {"id": "claude-3-5-sonnet-20241022", "name": "Claude 3.5 Sonnet", "context_window": 200000, "recommended": True},
                {"id": "claude-3-5-haiku-20241022", "name": "Claude 3.5 Haiku", "context_window": 200000},
                {"id": "claude-3-opus-20240229", "name": "Claude 3 Opus", "context_window": 200000},
            ],
            "gemini": [
                {"id": "gemini-2.0-flash", "name": "Gemini 2.0 Flash", "context_window": 1048576, "recommended": True},
                {"id": "gemini-1.5-pro", "name": "Gemini 1.5 Pro", "context_window": 2097152},
                {"id": "gemini-1.5-flash", "name": "Gemini 1.5 Flash", "context_window": 1048576},
            ],
            "deepseek": [
                {"id": "deepseek-chat", "name": "DeepSeek V3", "context_window": 64000, "recommended": True},
                {"id": "deepseek-coder", "name": "DeepSeek Coder", "context_window": 64000},
                {"id": "deepseek-reasoner", "name": "DeepSeek R1", "context_window": 64000, "recommended": True},
            ],
            "openrouter": [
                {"id": "anthropic/claude-3.5-sonnet", "name": "Anthropic: Claude 3.5 Sonnet", "context_window": 200000, "recommended": True},
                {"id": "openai/gpt-4o", "name": "OpenAI: GPT-4o", "context_window": 128000},
                {"id": "deepseek/deepseek-chat", "name": "DeepSeek V3", "context_window": 64000},
                {"id": "meta-llama/llama-3.3-70b-instruct", "name": "Meta Llama 3.3 70B", "context_window": 131072},
                {"id": "qwen/qwen-2.5-coder-32b-instruct", "name": "Qwen 2.5 Coder 32B", "context_window": 32768},
            ],
            "ollama": [
                {"id": "qwen2.5-coder:latest", "name": "Qwen 2.5 Coder", "context_window": 32768},
                {"id": "llama3.2:latest", "name": "Llama 3.2", "context_window": 8192},
                {"id": "deepseek-coder-v2:latest", "name": "DeepSeek Coder V2", "context_window": 64000},
            ],
        }

        # Attempt remote live query if provider supports /models
        if self.provider in ("openai", "openrouter", "deepseek", "ollama", "gemini"):
            url = f"{self.base_url}/models"
            if self.provider == "ollama":
                url = f"{self.base_url.replace('/v1', '')}/api/tags" if self.base_url.endswith('/v1') else f"{self.base_url}/api/tags"

            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"

            def _fetch_remote():
                req = urllib.request.Request(url, headers=headers, method="GET")
                with urllib.request.urlopen(req, timeout=5) as res:
                    return json.loads(res.read().decode("utf-8", errors="replace"))

            try:
                data = await asyncio.to_thread(_fetch_remote)
                if isinstance(data, dict):
                    if "data" in data and isinstance(data["data"], list):
                        remote_models = []
                        for m in data["data"]:
                            m_id = m.get("id") or m.get("name")
                            if m_id:
                                remote_models.append({
                                    "id": m_id,
                                    "name": m.get("name") or m_id,
                                    "context_window": m.get("context_length") or m.get("context_window"),
                                    "owned_by": m.get("owned_by"),
                                })
                        if remote_models:
                            return remote_models[:100]
                    elif "models" in data and isinstance(data["models"], list):
                        return [{"id": m.get("name"), "name": m.get("name")} for m in data["models"] if m.get("name")]
            except Exception as e:
                logger.debug(f"Remote models fetch for {self.provider} failed, falling back to curated: {e}")

        return curated_defaults.get(self.provider, [
            {"id": self.model, "name": self.model, "context_window": 32000}
        ])

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
        effort_map = {
            "low": "low",
            "medium": "medium",
            "high": "high",
            "extra_high": "high",
            "max": "high",
        }
        effort = effort_map.get(str(self.thinking_level).lower(), "medium")
        if any(k in effective_model.lower() for k in ("o1", "o3", "reasoner", "r1", "thinking")):
            payload["reasoning_effort"] = effort
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        tool_calls_acc: dict[int, dict[str, Any]] = {}
        timeout = httpx.Timeout(180.0, connect=15.0)

        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")
                        err_msg = f"{self.provider.upper()} Error (HTTP {resp.status_code}): {err_body[:200]}"
                        try:
                            parsed = json.loads(err_body)
                            if isinstance(parsed, dict) and "error" in parsed:
                                err_val = parsed["error"]
                                if isinstance(err_val, dict) and "message" in err_val:
                                    err_msg = f"{self.provider.upper()} Error (HTTP {resp.status_code}): {err_val['message']}"
                                elif isinstance(err_val, str):
                                    err_msg = f"{self.provider.upper()} Error (HTTP {resp.status_code}): {err_val}"
                        except Exception:
                            pass
                        if resp.status_code in (401, 403):
                            err_msg = f"{err_msg} — Please verify your API key and permissions in AI Settings."
                        yield {"type": "error", "content": err_msg}
                        return

                    async for line in resp.aiter_lines():
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
        except Exception as exc:
            yield {"type": "error", "content": f"Connection to {self.provider} failed: {str(exc)}"}
            return

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
        budget_map = {
            "low": 1024,
            "medium": 2048,
            "high": 4096,
            "extra_high": 8192,
            "max": 16384,
        }
        if any(k in self.model.lower() for k in ("claude-3-7", "sonnet-3-7", "thinking")):
            payload["thinking"] = {
                "type": "enabled",
                "budget_tokens": budget_map.get(str(self.thinking_level).lower(), 2048),
            }
        if system_prompt:
            payload["system"] = system_prompt

        timeout = httpx.Timeout(180.0, connect=15.0)
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, headers=headers, json=payload) as resp:
                    if resp.status_code != 200:
                        err_body = (await resp.aread()).decode("utf-8", errors="replace")
                        yield {"type": "error", "content": f"Anthropic HTTP {resp.status_code}: {err_body[:200]}"}
                        return
                    async for line in resp.aiter_lines():
                        line_str = line.strip()
                        if line_str.startswith("data: "):
                            try:
                                data = json.loads(line_str[6:])
                                event_type = data.get("type")
                                if event_type == "content_block_delta":
                                    delta = data.get("delta") or {}
                                    if delta.get("type") == "text_delta":
                                        yield {"type": "token", "content": delta.get("text", "")}
                                    elif delta.get("type") == "thinking_delta":
                                        yield {"type": "thought", "content": delta.get("thinking", "")}
                            except json.JSONDecodeError:
                                continue
        except Exception as exc:
            yield {"type": "error", "content": f"Anthropic connection failed: {str(exc)}"}
            return
