"""Tests for the Syte MCP adapter."""

import base64


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
