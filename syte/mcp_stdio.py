"""Minimal MCP stdio server exposing Syte project tools (no extra deps)."""

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


TOOLS: list[dict[str, Any]] = [
    {
        "name": "syte_service",
        "description": "Control Syte project services: start/stop/deploy/preview/run/logs",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "status|start|stop|deploy|preview_start|preview_stop|update|run|logs|preview_logs",
                },
                "command": {"type": "string", "description": "Shell command for action=run"},
                "cwd": {"type": "string", "description": "Working dir relative to workspace (default app)"},
                "lines": {"type": "integer", "description": "Log lines for logs actions"},
            },
            "required": ["action"],
        },
    },
    {
        "name": "syte_access",
        "description": "Preview URL access: fetch HTML, logs, screenshot, status",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "description": "status|url|fetch|read|logs|screenshot"},
                "url": {"type": "string"},
                "lines": {"type": "integer"},
            },
            "required": ["action"],
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
