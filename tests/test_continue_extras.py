"""Tests for Continue MCP/skills helpers."""

import pytest

from syte.continue_extras import render_mcp_servers_yaml, render_rules_yaml


def test_render_mcp_servers_yaml() -> None:
    lines = render_mcp_servers_yaml([
        {"name": "sqlite", "command": "npx", "args": ["-y", "mcp-sqlite"], "env": {"FOO": "bar"}},
    ])
    text = "\n".join(lines)
    assert "mcpServers:" in text
    assert "sqlite" in text
    assert "npx" in text


def test_render_rules_yaml() -> None:
    lines = render_rules_yaml(["Always use TypeScript", "./rules/style.md"])
    assert lines[0] == "rules:"
    assert "TypeScript" in "\n".join(lines)
