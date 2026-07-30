"""Minimal MCP stdio server exposing Syte project tools (no extra deps).

The Syte MCP stdio server is the bridge between external MCP clients (IDE
plugins, agent chat UIs, automation scripts) and the Syte project API. A client
connects by spawning this script with ``SYTE_PROJECT_ID`` and ``SYTE_API_BASE``
set; the server then forwards tool calls to the project-scoped HTTP API.

Typical client connection flow:
1. Resolve the project UUID from the Syte GUI or API.
2. Set environment variables:
   - ``SYTE_PROJECT_ID`` — the project UUID
   - ``SYTE_API_BASE`` — the Syte server base URL (e.g. ``http://127.0.0.1:8787``)
   - ``PYTHONPATH`` — path to the ``syte`` package (only needed when spawning
     ``python3 -m syte.mcp_stdio`` directly)
3. Spawn the process with stdio (JSON-RPC over stdin/stdout).
4. Call ``initialize``, then ``tools/list`` to discover available tools.
5. Call ``tools/call`` with ``name`` + ``arguments`` to invoke a tool.

The built-in ``syte`` MCP addon in the Syte GUI exposes the same two tools
(``syte_service`` and ``syte_access``) through the HTTP API at
``/api/projects/{id}/agent/mcp``. This stdio server is the equivalent for
external MCP clients.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request
from typing import Any


def _api_base() -> str:
    return (os.environ.get("SYTE_API_BASE") or "http://127.0.0.1:8787").rstrip("/")


def _project_id() -> str:
    pid = (os.environ.get("SYTE_PROJECT_ID") or "").strip()
    if not pid:
        raise RuntimeError("SYTE_PROJECT_ID not set")
    return pid


def _post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    url = f"{_api_base()}{path}"
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": "http_error", "message": raw[:2000]}


def _get(path: str) -> dict[str, Any]:
    url = f"{_api_base()}{path}"
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": "http_error", "message": raw[:2000]}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "syte_service",
        "description": (
            "Control a Syte project service and dev preview. Actions: "
            "status (project + preview status), preview_start, preview_stop, "
            "run (shell command in workspace), logs, preview_logs. "
            "Production start/stop/deploy/update are blocked for agent safety."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "preview_start", "preview_stop", "run", "logs", "preview_logs"],
                    "description": "Service action to perform.",
                },
                "command": {
                    "type": "string",
                    "description": "Shell command for action=run (e.g. 'npm run lint').",
                },
                "cwd": {
                    "type": "string",
                    "description": "Working directory relative to workspace root (default: app).",
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines for logs/preview_logs (default: 200).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "syte_access",
        "description": (
            "Access the project preview: check status, get URL, fetch page HTML/text, "
            "read page content, tail preview logs, or capture a screenshot."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "url", "fetch", "read", "logs", "screenshot"],
                    "description": "Preview access action.",
                },
                "url": {
                    "type": "string",
                    "description": "Optional full URL override (must be an allowed preview URL).",
                },
                "lines": {
                    "type": "integer",
                    "description": "Log lines for logs action (default: 200).",
                },
            },
            "required": ["action"],
        },
    },
    {
        "name": "syte_info",
        "description": (
            "Return Syte MCP server configuration: project routes, API base, "
            "project ID, and documentation links. Useful for clients that need to "
            "discover available HTTP endpoints or build direct API calls."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    pid = _project_id()
    if name == "syte_service":
        body = {"action": arguments.get("action", "status")}
        if arguments.get("command"):
            body["command"] = arguments["command"]
        if arguments.get("cwd"):
            body["cwd"] = arguments["cwd"]
        if arguments.get("lines") is not None:
            body["lines"] = arguments["lines"]
        return _post(f"/api/projects/{pid}/agent/service", body)
    if name == "syte_access":
        body = {"action": arguments.get("action", "status")}
        if arguments.get("url"):
            body["url"] = arguments["url"]
        if arguments.get("lines") is not None:
            body["lines"] = arguments["lines"]
        return _post(f"/api/projects/{pid}/agent/access", body)
    if name == "syte_info":
        return {
            "ok": True,
            "project_id": pid,
            "api_base": _api_base(),
            "project_routes": {
                "mcp_list": f"{_api_base()}/api/projects/{pid}/agent/mcp",
                "mcp_connect": f"{_api_base()}/api/projects/{pid}/agent/mcp/connect",
                "mcp_call": f"{_api_base()}/api/projects/{pid}/agent/mcp/call",
                "skills": f"{_api_base()}/api/projects/{pid}/agent/skills",
                "service": f"{_api_base()}/api/projects/{pid}/agent/service",
                "access": f"{_api_base()}/api/projects/{pid}/agent/access",
                "logs_stream": f"{_api_base()}/api/projects/{pid}/agent/logs/stream?live=1",
                "activity_stream": f"{_api_base()}/api/projects/{pid}/agent/activity/stream",
                "status": f"{_api_base()}/api/projects/{pid}/agent",
            },
            "documentation": f"{_api_base()}/api/",
            "ai_spec": f"{_api_base()}/api/ai.json",
            "mcp_stdio_command": f"SYTE_PROJECT_ID={pid} SYTE_API_BASE={_api_base()} python3 -m syte.mcp_stdio",
        }
    return {"ok": False, "error": "unknown_tool", "message": name}


def _write_message(msg: dict[str, Any]) -> None:
    payload = json.dumps(msg, ensure_ascii=False).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(payload)}\r\n\r\n".encode("ascii"))
    sys.stdout.buffer.write(payload)
    sys.stdout.buffer.flush()


def _read_message() -> dict[str, Any] | None:
    header = sys.stdin.buffer.readline()
    if not header:
        return None
    if not header.startswith(b"Content-Length:"):
        line = header.decode("utf-8", errors="replace").strip()
        if not line:
            return _read_message()
        return json.loads(line)
    length = int(header.decode("ascii").split(":", 1)[1].strip())
    while True:
        sep = sys.stdin.buffer.read(2)
        if sep == b"\r\n":
            break
    body = sys.stdin.buffer.read(length)
    return json.loads(body.decode("utf-8"))


def _handle_request(req: dict[str, Any]) -> dict[str, Any]:
    req_id = req.get("id")
    method = req.get("method", "")
    params = req.get("params") or {}

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "syte-mcp", "version": "1.0.0"},
            },
        }

    if method == "notifications/initialized":
        return {}

    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req_id, "result": {"tools": TOOLS}}

    if method == "tools/call":
        name = params.get("name", "")
        arguments = params.get("arguments") or {}
        try:
            result = _call_tool(name, arguments)
            text = json.dumps(result, ensure_ascii=False, indent=2)
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {"content": [{"type": "text", "text": text}], "isError": not result.get("ok", True)},
            }
        except Exception as exc:
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [{"type": "text", "text": str(exc)}],
                    "isError": True,
                },
            }

    if method == "ping":
        return {"jsonrpc": "2.0", "id": req_id, "result": {}}

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Method not found: {method}"},
    }


def main() -> None:
    while True:
        req = _read_message()
        if req is None:
            break
        if "method" not in req:
            continue
        resp = _handle_request(req)
        if resp:
            _write_message(resp)


if __name__ == "__main__":
    main()
