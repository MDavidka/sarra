"""Tests for activity event catalog."""

from syte.agent_activity_catalog import ACTIVITY_EVENT_CATALOG, build_activity_api_spec


def test_catalog_includes_session_events() -> None:
    types = {item["event_type"] for item in ACTIVITY_EVENT_CATALOG}
    assert "session_started" in types
    assert "mcp_tool_call" in types
    assert "skill_invoked" in types


def test_build_activity_api_spec() -> None:
    spec = build_activity_api_spec()
    assert "catalog" in spec
    assert len(spec["event_types"]) >= 20
