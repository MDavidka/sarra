"""Tests for the Syte MCP adapter."""

import base64
import json
import os
import subprocess
import sys


def test_screenshot_tool_result_preserves_image_content(monkeypatch):
    import syte.mcp_stdio as mcp

    png = base64.b64encode(b"png").decode("ascii")
    monkeypatch.setattr(mcp, "_call_tool", lambda _name, _args: {
        "ok": True,
        "action": "screenshot",
        "format": "png",
        "image_base64": png,
    })

    response = mcp._handle_request({
        "id": 7,
        "method": "tools/call",
        "params": {"name": "syte_access", "arguments": {"action": "screenshot"}},
    })

    content = response["result"]["content"]
    assert content[0]["type"] == "text"
    assert "image_base64" not in content[0]["text"]
    assert content[1] == {"type": "image", "data": png, "mimeType": "image/png"}


def test_access_tool_exposes_screenshot_viewport_options():
    import syte.mcp_stdio as mcp

    tool = next(item for item in mcp.TOOLS if item["name"] == "syte_access")
    properties = tool["inputSchema"]["properties"]

    assert "screenshot" in properties["action"]["description"]
    assert properties["width"]["type"] == "integer"
    assert properties["height"]["type"] == "integer"


def test_stdio_uses_newline_delimited_json_framing():
    env = {
        **os.environ,
        "SYTE_PROJECT_ID": "test",
        "SYTE_API_BASE": "http://127.0.0.1:8787",
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "syte.mcp_stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    init = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
    }
    proc.stdin.write((json.dumps(init) + "\n").encode())
    proc.stdin.flush()
    line = proc.stdout.readline().decode("utf-8")
    proc.terminate()

    assert line.startswith("{")
    assert "Content-Length" not in line
    payload = json.loads(line)
    assert payload["result"]["serverInfo"]["name"] == "syte-mcp"


def test_stdio_survives_invalid_json_line():
    env = {
        **os.environ,
        "SYTE_PROJECT_ID": "test",
        "SYTE_API_BASE": "http://127.0.0.1:8787",
        "PYTHONPATH": str(__import__("pathlib").Path(__file__).resolve().parents[1]),
    }
    proc = subprocess.Popen(
        [sys.executable, "-m", "syte.mcp_stdio"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    assert proc.stdin is not None
    assert proc.stdout is not None
    proc.stdin.write(b"not-json\n")
    proc.stdin.write(
        (
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/list",
                    "params": {},
                }
            )
            + "\n"
        ).encode()
    )
    proc.stdin.flush()
    line = proc.stdout.readline().decode("utf-8")
    proc.terminate()

    payload = json.loads(line)
    assert payload["id"] == 2
    assert payload["result"]["tools"]
