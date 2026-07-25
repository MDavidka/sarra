"""Native Gemini API transport for Google AI Studio / Vertex Express keys.

Google AI Studio now issues Auth keys that start with ``AQ.`` instead of the
legacy ``AIza…`` traffic keys. Those ``AQ.`` keys work on the native Gemini
REST API (``x-goog-api-key``) but currently fail on the OpenAI-compatible
``/v1beta/openai`` endpoint when sent as ``Authorization: Bearer``.

Syte labels nano/havy as "Vertex AI" but talks to
``generativelanguage.googleapis.com``. This module routes ``AQ.`` keys through
native ``generateContent`` and returns OpenAI-shaped assistant messages so the
existing agent loop does not need a second tool protocol.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import httpx

GEMINI_NATIVE_API_BASE = "https://generativelanguage.googleapis.com/v1beta"


def looks_like_google_auth_key(api_key: str | None) -> bool:
    """New Google AI Studio Auth keys (``AQ.…``)."""
    return (api_key or "").strip().lower().startswith("aq.")


def looks_like_google_ai_studio_key(api_key: str | None) -> bool:
    """Legacy ``AIza…`` traffic keys or new ``AQ.…`` auth keys."""
    key = (api_key or "").strip()
    lower = key.lower()
    return lower.startswith("aiza") or lower.startswith("aq.")


def uses_gemini_openai_compat(api_base: str | None) -> bool:
    base = (api_base or "").lower()
    return "generativelanguage.googleapis.com" in base and "/openai" in base


def should_use_native_gemini(api_key: str | None, api_base: str | None = None) -> bool:
    """Prefer native Gemini when the key is an Auth key (``AQ.``).

    Legacy ``AIza…`` keys keep using the OpenAI-compat Bearer path. When an
    ``api_base`` is provided, only switch for Gemini / Vertex-shaped hosts so
    accidental AQ.-looking keys on other providers are left alone.
    """
    if not looks_like_google_auth_key(api_key):
        return False
    if not api_base:
        return True
    base = (api_base or "").lower()
    return (
        "generativelanguage.googleapis.com" in base
        or "aiplatform.googleapis.com" in base
        or "vertex" in base
    )


def google_auth_headers(api_key: str) -> dict[str, str]:
    return {
        "x-goog-api-key": (api_key or "").strip(),
        "Content-Type": "application/json",
    }


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


# JSON Schema keys Gemini's FunctionDeclaration.parameters Schema rejects.
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
    """Strip JSON Schema features Gemini's function-calling Schema rejects.

    OpenAI-style tool parameters often include ``additionalProperties: false``
    (and nested ``additionalProperties`` maps). Gemini returns HTTP 400
    ``Unknown name "additionalProperties"`` for those fields.
    """
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
                # Gemini expects an object schema; ensure type is present.
                if "type" not in cleaned:
                    cleaned = {**cleaned, "type": "object"}
                entry["parameters"] = cleaned
        declarations.append(entry)
    if not declarations:
        return []
    return [{"functionDeclarations": declarations}]


def openai_messages_to_gemini(
    messages: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Return ``(system_instruction, contents)`` for generateContent."""
    system_chunks: list[str] = []
    contents: list[dict[str, Any]] = []
    # Map tool_call_id → function name for functionResponse turns.
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

        # user / other
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
        raise RuntimeError("Gemini returned no candidates")
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
    # Map OpenAI-style reasoning_effort onto Gemini thinkingConfig when present.
    cfg = thinking_config or {}
    effort = str(cfg.get("reasoning_effort") or "").strip().lower()
    if effort:
        # Gemini 3.x accepts thinkingConfig.thinkingLevel for flash/lite families.
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
    """Call native generateContent and return an OpenAI-shaped assistant message."""
    model_id = (model or "").strip().removeprefix("models/")
    if not model_id:
        raise RuntimeError("Gemini model id is empty")
    url = f"{GEMINI_NATIVE_API_BASE}/models/{model_id}:generateContent"
    body = build_generate_content_body(
        messages=messages,
        tools=tools,
        temperature=temperature,
        top_p=top_p,
        max_tokens=max_tokens,
        thinking_config=thinking_config,
    )
    response = await client.post(url, headers=google_auth_headers(api_key), json=body)
    if response.status_code >= 400:
        detail = (response.text or "").strip()[:800]
        raise RuntimeError(
            f"Client error '{response.status_code} {response.reason_phrase}' for url '{url}'"
            + (f": {detail}" if detail else "")
        )
    data = response.json()
    return gemini_response_to_openai_message(data)


async def native_list_models(*, client: httpx.AsyncClient, api_key: str) -> httpx.Response:
    url = f"{GEMINI_NATIVE_API_BASE}/models"
    return await client.get(url, headers=google_auth_headers(api_key))


async def native_probe_chat(
    *,
    client: httpx.AsyncClient,
    api_key: str,
    model: str,
) -> httpx.Response:
    model_id = (model or "").strip().removeprefix("models/")
    url = f"{GEMINI_NATIVE_API_BASE}/models/{model_id}:generateContent"
    body = {
        "contents": [{"role": "user", "parts": [{"text": "Reply with exactly: ok"}]}],
        "generationConfig": {"maxOutputTokens": 16, "temperature": 0},
    }
    return await client.post(url, headers=google_auth_headers(api_key), json=body)
