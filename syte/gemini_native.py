"""Vertex AI Express Mode transport for Syra nano/havy Gemini profiles.

Syte's nano/havy profiles use **Google Cloud Vertex AI Express Mode**, not
Google AI Studio (``generativelanguage.googleapis.com``).

Express Mode authenticates with an API key on:

``https://aiplatform.googleapis.com/v1/publishers/google/models/{model}:generateContent?key=…``

OpenAI-compatible Vertex endpoints require OAuth access tokens and do not accept
Express Mode API keys, so this module always uses native ``generateContent`` and
returns OpenAI-shaped assistant messages for the existing agent loop.
"""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import quote

import httpx

# Vertex AI Express Mode (Google Cloud) — not AI Studio.
VERTEX_EXPRESS_API_BASE = "https://aiplatform.googleapis.com/v1"
# Backward-compatible aliases used by older imports/tests.
GEMINI_NATIVE_API_BASE = VERTEX_EXPRESS_API_BASE
AI_STUDIO_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def looks_like_google_auth_key(api_key: str | None) -> bool:
    """Google Auth keys (``AQ.…``) — common for AI Studio; Express keys vary."""
    return (api_key or "").strip().lower().startswith("aq.")


def looks_like_google_ai_studio_key(api_key: str | None) -> bool:
    """Legacy ``AIza…`` traffic keys or ``AQ.…`` auth keys (AI Studio shapes)."""
    key = (api_key or "").strip()
    lower = key.lower()
    return lower.startswith("aiza") or lower.startswith("aq.")


def looks_like_vertex_api_key(api_key: str | None) -> bool:
    """Heuristic: not an OpenAI/DeepSeek/Aliyun ``sk-`` key."""
    key = (api_key or "").strip()
    if not key:
        return False
    lower = key.lower()
    if lower.startswith("sk-"):
        return False
    return True


def uses_gemini_openai_compat(api_base: str | None) -> bool:
    base = (api_base or "").lower()
    return "/openai" in base and (
        "generativelanguage.googleapis.com" in base or "aiplatform.googleapis.com" in base
    )


def is_vertex_express_base(api_base: str | None) -> bool:
    base = (api_base or "").lower()
    return "aiplatform.googleapis.com" in base


def is_google_gemini_base(api_base: str | None) -> bool:
    base = (api_base or "").lower()
    return (
        "aiplatform.googleapis.com" in base
        or "generativelanguage.googleapis.com" in base
        or "vertex" in base
    )


def should_use_native_gemini(api_key: str | None, api_base: str | None = None) -> bool:
    """Vertex Express always uses native generateContent (API key via query).

    Also keep the AQ. → native path if a legacy AI Studio base is configured.
    """
    if is_vertex_express_base(api_base):
        return bool((api_key or "").strip())
    if not api_base:
        return bool((api_key or "").strip())
    if looks_like_google_auth_key(api_key) and is_google_gemini_base(api_base):
        return True
    return False


def normalize_vertex_model_id(model: str) -> str:
    model_id = (model or "").strip()
    for prefix in (
        "publishers/google/models/",
        "models/",
        "google/",
    ):
        if model_id.startswith(prefix):
            model_id = model_id[len(prefix) :]
    return model_id


def vertex_generate_content_url(model: str, api_key: str) -> str:
    model_id = normalize_vertex_model_id(model)
    path = f"publishers/google/models/{model_id}:generateContent"
    return f"{VERTEX_EXPRESS_API_BASE}/{path}?key={quote((api_key or '').strip(), safe='')}"


def vertex_model_get_url(model: str, api_key: str) -> str:
    model_id = normalize_vertex_model_id(model)
    path = f"publishers/google/models/{model_id}"
    return f"{VERTEX_EXPRESS_API_BASE}/{path}?key={quote((api_key or '').strip(), safe='')}"


def google_auth_headers(api_key: str | None = None) -> dict[str, str]:
    """Headers for Vertex Express. Key is normally passed as ``?key=`` on the URL."""
    del api_key
    return {"Content-Type": "application/json"}


# JSON Schema keys Gemini/Vertex FunctionDeclaration.parameters Schema rejects.
_GEMINI_SCHEMA_DROP = frozenset({
    "additionalProperties",
    "additional_properties",
    "$schema",
    "$id",
    "$defs",
    "definitions",
    "examples",
    "default",
    "const",
    "oneOf",
    "allOf",
    "anyOf",
    "not",
    "if",
    "then",
    "else",
    "dependentRequired",
    "dependentSchemas",
    "patternProperties",
    "unevaluatedProperties",
    "unevaluatedItems",
    "prefixItems",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "uniqueItems",
    "contentEncoding",
    "contentMediaType",
})


def sanitize_gemini_schema(schema: Any) -> Any:
    """Strip JSON Schema features Vertex function-calling Schema rejects."""
    if isinstance(schema, list):
        return [sanitize_gemini_schema(item) for item in schema]
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _GEMINI_SCHEMA_DROP:
            continue
        if key in {"properties", "defs"} and isinstance(value, dict):
            out[key] = {
                str(prop): sanitize_gemini_schema(prop_schema)
                for prop, prop_schema in value.items()
            }
            continue
        if key == "items":
            out[key] = sanitize_gemini_schema(value)
            continue
        if isinstance(value, (dict, list)):
            out[key] = sanitize_gemini_schema(value)
            continue
        out[key] = value
    return out


def openai_tools_to_gemini(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    if not tools:
        return []
    declarations: list[dict[str, Any]] = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if tool.get("type") == "function" else tool.get("function") or tool
        if not isinstance(fn, dict):
            continue
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        entry: dict[str, Any] = {
            "name": name,
            "description": str(fn.get("description") or ""),
        }
        params = fn.get("parameters")
        if isinstance(params, dict):
            cleaned = sanitize_gemini_schema(params)
            if isinstance(cleaned, dict):
                if "type" not in cleaned:
                    cleaned = {**cleaned, "type": "object"}
                entry["parameters"] = cleaned
        declarations.append(entry)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                if item.get("type") == "text" and item.get("text"):
                    parts.append(str(item["text"]))
                elif item.get("text"):
                    parts.append(str(item["text"]))
        return "\n".join(parts)
    return str(content)


def _content_to_parts(content: Any) -> list[dict[str, Any]]:
    """Convert OpenAI message content into Gemini parts (text + inline images)."""
    if content is None:
        return [{"text": ""}]
    if isinstance(content, str):
        return [{"text": content}] if content else [{"text": ""}]
    if not isinstance(content, list):
        return [{"text": str(content)}]

    parts: list[dict[str, Any]] = []
    for item in content:
        if isinstance(item, str):
            if item:
                parts.append({"text": item})
            continue
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        if kind == "text" or item.get("text"):
            text = str(item.get("text") or "")
            if text:
                parts.append({"text": text})
            continue
        if kind == "image_url":
            image_url = item.get("image_url")
            url = ""
            if isinstance(image_url, dict):
                url = str(image_url.get("url") or "")
            elif isinstance(image_url, str):
                url = image_url
            if url.startswith("data:") and ";base64," in url:
                header, _, data = url.partition(",")
                mime = "image/png"
                if header.startswith("data:") and ";base64" in header:
                    mime = header[5:].split(";", 1)[0] or mime
                parts.append({"inlineData": {"mimeType": mime, "data": data}})
            elif url:
                parts.append({"text": f"[image: {url}]"})
    return parts or [{"text": ""}]


def openai_messages_to_gemini(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return ``(system_instruction, contents)`` for generateContent."""
    system_chunks: list[str] = []
    contents: list[dict[str, Any]] = []
    call_names: dict[str, str] = {}

    for msg in messages:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role") or "")
        if role == "system":
            text = _content_to_text(msg.get("content"))
            if text.strip():
                system_chunks.append(text)
            continue

        if role == "assistant":
            parts: list[dict[str, Any]] = []
            text = _content_to_text(msg.get("content"))
            if text:
                parts.append({"text": text})
            for call in msg.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                raw_args = fn.get("arguments") or "{}"
                try:
                    args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                except (TypeError, ValueError, json.JSONDecodeError):
                    args = {"_raw": str(raw_args)}
                if not isinstance(args, dict):
                    args = {"value": args}
                if call_id and name:
                    call_names[call_id] = name
                if name:
                    parts.append({"functionCall": {"name": name, "args": args}})
            if parts:
                contents.append({"role": "model", "parts": parts})
            continue

        if role == "tool":
            call_id = str(msg.get("tool_call_id") or "")
            name = call_names.get(call_id) or str(msg.get("name") or "tool")
            raw = msg.get("content")
            try:
                response_obj: Any = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError, json.JSONDecodeError):
                response_obj = {"result": _content_to_text(raw)}
            if not isinstance(response_obj, dict):
                response_obj = {"result": response_obj}
            contents.append({
                "role": "user",
                "parts": [{
                    "functionResponse": {
                        "name": name,
                        "response": response_obj,
                    }
                }],
            })
            continue

        parts = _content_to_parts(msg.get("content"))
        contents.append({"role": "user", "parts": parts})

    system_instruction = None
    if system_chunks:
        system_instruction = {"parts": [{"text": "\n\n".join(system_chunks)}]}
    return system_instruction, contents


def gemini_response_to_openai_message(data: dict[str, Any]) -> dict[str, Any]:
    """Convert a generateContent JSON body into an OpenAI chat message dict."""
    candidates = data.get("candidates") or []
    if not candidates or not isinstance(candidates[0], dict):
        raise RuntimeError("Vertex Gemini returned no candidates")
    content = candidates[0].get("content") or {}
    parts = content.get("parts") or []
    text_bits: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        if part.get("text"):
            text_bits.append(str(part["text"]))
        fc = part.get("functionCall") or part.get("function_call")
        if isinstance(fc, dict) and fc.get("name"):
            args = fc.get("args") if isinstance(fc.get("args"), dict) else {}
            tool_calls.append({
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": str(fc["name"]),
                    "arguments": json.dumps(args, ensure_ascii=False),
                },
            })
    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(text_bits) if text_bits else (None if tool_calls else ""),
    }
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def build_generate_content_body(
    *,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    top_p: float = 0.95,
    max_tokens: int | None = None,
    thinking_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    system_instruction, contents = openai_messages_to_gemini(messages)
    body: dict[str, Any] = {"contents": contents or [{"role": "user", "parts": [{"text": ""}]}]}
    if system_instruction:
        body["systemInstruction"] = system_instruction
    gemini_tools = openai_tools_to_gemini(tools)
    if gemini_tools:
        body["tools"] = gemini_tools
    generation: dict[str, Any] = {
        "temperature": float(temperature),
        "topP": float(top_p),
    }
    if max_tokens is not None:
        generation["maxOutputTokens"] = int(max_tokens)
    cfg = thinking_config or {}
    effort = str(cfg.get("reasoning_effort") or "").strip().lower()
    if effort:
        level = {
            "none": "minimal",
            "minimal": "minimal",
            "low": "low",
            "medium": "medium",
            "high": "high",
            "xhigh": "high",
        }.get(effort, "medium")
        generation["thinkingConfig"] = {"thinkingLevel": level}
    body["generationConfig"] = generation
    return body


async def native_generate_content(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    temperature: float = 0.2,
    top_p: float = 0.95,
    max_tokens: int | None = None,
    thinking_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call Vertex Express generateContent and return an OpenAI-shaped message."""
    model_id = normalize_vertex_model_id(model)
    if not model_id:
        raise RuntimeError("Vertex Gemini model id is empty")
    url = vertex_generate_content_url(model_id, api_key)
    body = build_generate_content_body(
        messages=messages,
        tools=tools,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        thinking_config=thinking_config,
    )
    # Redact key from error URLs shown to users.
    public_url = vertex_generate_content_url(model_id, "***")
    response = await client.post(url, headers=google_auth_headers(), json=body)
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:800]
        raise RuntimeError(
            format_google_http_error(
                status_code=response.status_code,
                reason=response.reason_phrase,
                url=public_url,
                detail=detail,
            )
        )
    data = response.json()
    return gemini_response_to_openai_message(data)


async def native_list_models(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str | None = None,
) -> httpx.Response:
    """Probe Vertex by fetching the configured publisher model (Express Mode)."""
    model_id = normalize_vertex_model_id(model or "gemini-2.5-flash")
    url = vertex_model_get_url(model_id, api_key)
    return await client.get(url, headers=google_auth_headers())


async def native_probe_chat(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
) -> httpx.Response:
    url = vertex_generate_content_url(model, api_key)
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: ok"}]}],
        "generationConfig": {"maxOutputTokens": 16, "temperature": 0},
    }
    return await client.post(url, headers=google_auth_headers(), json=body)


def explain_google_api_error(detail: str | None, *, status_code: int | None = None) -> str:
    """Return an actionable hint for common Vertex Express / Gemini API errors."""
    text = detail or ""
    lower = text.lower()
    if "api keys are not supported" in lower or "credentials_missing" in lower:
        return (
            "This endpoint expects Vertex Express Mode API-key auth on "
            "aiplatform.googleapis.com/v1/publishers/google/models/…:generateContent?key=… "
            "(not OAuth OpenAI-compat, and not AI Studio generativelanguage). "
            "Create/manage the key in Google Cloud Console → APIs & Services → Credentials "
            "while in Vertex AI Express Mode."
        )
    if (
        "api_key_service_blocked" in lower
        or ("are blocked" in lower and ("aiplatform" in lower or "generativelanguage" in lower))
        or (
            status_code == 403
            and "permission_denied" in lower
            and ("aiplatform" in lower or "generativelanguage" in lower)
        )
    ):
        return (
            "Google blocked this key for the Vertex/Gemini API (API_KEY_SERVICE_BLOCKED). "
            "Use a Vertex AI Express Mode API key from Google Cloud Console → Credentials, "
            "and restrict it to the Vertex AI API (aiplatform.googleapis.com). "
            "Syte nano/havy call Vertex Express — not Google AI Studio."
        )
    if status_code == 403 and ("aiplatform" in lower or "vertex" in lower or "gemini" in lower):
        return (
            "Google returned HTTP 403 for Vertex AI. Confirm Express Mode is active and the "
            "key is allowed for aiplatform.googleapis.com."
        )
    if status_code == 404 and "model" in lower:
        return (
            "Model not found on Vertex Express. Check the model id is available in your "
            "Express Mode project (Google Cloud Agent Studio / model garden)."
        )
    return ""


def format_google_http_error(
    *,
    status_code: int,
    reason: str,
    url: str,
    detail: str,
) -> str:
    """Build a user-facing error string, appending Vertex guidance when relevant."""
    base = (
        f"Client error '{status_code} {reason}' for url '{url}'"
        + (f": {detail}" if detail else "")
    )
    hint = explain_google_api_error(detail, status_code=status_code)
    if hint:
        return f"{base}\n\n{hint}"
    return base
