"""Tests for OpenCode MCP/rules extras."""

from syte.opencode_extras import render_mcp_servers_dict


def test_render_mcp_servers_dict_maps_command_and_args() -> None:
    rendered = render_mcp_servers_dict([
        {
            "name": "sqlite",
            "command": "npx",
            "args": ["-y", "mcp-sqlite"],
            "env": {"DB": "/tmp/test.db"},
        }
    ])
    assert rendered["sqlite"]["type"] == "local"
    assert rendered["sqlite"]["command"] == ["npx", "-y", "mcp-sqlite"]
    assert rendered["sqlite"]["environment"] == {"DB": "/tmp/test.db"}
    assert rendered["sqlite"]["enabled"] is True
