"""Machine-readable catalog of agent activity event types (Continue.dev aligned)."""

from __future__ import annotations

from typing import Any

# Continue CLI / cn serve aligned activity vocabulary for sycord.com UIs.
ACTIVITY_EVENT_CATALOG: list[dict[str, Any]] = [
    {
        "event_type": "session_started",
        "role": "system",
        "title": "Session started",
        "description": "Continue cn serve session opened for this project.",
        "continue_aligned": "agent loop start",
    },
    {
        "event_type": "session_finished",
        "role": "system",
        "title": "Session finished",
        "description": "Agent finished processing and returned to idle.",
        "continue_aligned": "isProcessing=false",
    },
    {
        "event_type": "agent_started",
        "role": "system",
        "description": "cn serve process started on allocated localhost port.",
    },
    {
        "event_type": "agent_stopped",
        "role": "system",
        "description": "cn serve process stopped.",
    },
    {
        "event_type": "agent_restarted",
        "role": "system",
        "description": "Agent stopped and started again (config reload).",
    },
    {
        "event_type": "request_started",
        "role": "user",
        "description": "User/API message sent via POST /message or agent/change.",
        "continue_aligned": "POST /message",
    },
    {
        "event_type": "request_completed",
        "role": "assistant",
        "description": "Agent finished request; reply available.",
    },
    {
        "event_type": "request_failed",
        "role": "system",
        "description": "Request failed (HTTP error, timeout, provider error).",
    },
    {
        "event_type": "processing",
        "role": "system",
        "description": "Agent is working (isProcessing=true). Ephemeral in SSE; may not persist.",
        "continue_aligned": "GET /state isProcessing",
    },
    {
        "event_type": "user_message",
        "role": "user",
        "description": "User message from session.history.",
    },
    {
        "event_type": "assistant_message",
        "role": "assistant",
        "description": "Assistant text from session.history.",
    },
    {
        "event_type": "thinking",
        "role": "assistant",
        "description": "Model reasoning / redacted thinking blocks.",
        "continue_aligned": "thinking content parts",
    },
    {
        "event_type": "plan",
        "role": "assistant",
        "description": "Agent planning steps before tool use.",
        "continue_aligned": "plan / todo tools",
    },
    {
        "event_type": "asking_user",
        "role": "assistant",
        "description": "Agent asks a clarifying question.",
    },
    {
        "event_type": "tool_call",
        "role": "assistant",
        "description": "Generic tool invocation.",
        "continue_aligned": "tool_use / function_call",
    },
    {
        "event_type": "tool_result",
        "role": "tool",
        "description": "Result returned from a tool execution.",
        "continue_aligned": "role=tool messages",
    },
    {
        "event_type": "mcp_tool_call",
        "role": "assistant",
        "description": "MCP server tool invoked (from mcpServers in config.yaml).",
        "continue_aligned": "mcpServers tools",
    },
    {
        "event_type": "skill_invoked",
        "role": "assistant",
        "description": "Continue Skills tool read a SKILL.md file.",
        "continue_aligned": ".continue/skills/SKILL.md",
    },
    {
        "event_type": "command_run",
        "role": "assistant",
        "description": "Shell/terminal command executed.",
    },
    {
        "event_type": "file_created",
        "role": "assistant",
        "description": "File created in workspace.",
    },
    {
        "event_type": "file_modified",
        "role": "assistant",
        "description": "File edited in workspace.",
    },
    {
        "event_type": "file_deleted",
        "role": "assistant",
        "description": "File deleted from workspace.",
    },
    {
        "event_type": "file_read",
        "role": "assistant",
        "description": "File read by agent.",
    },
    {
        "event_type": "status",
        "role": "system",
        "description": "Informational status line.",
    },
]


def activity_event_types() -> list[str]:
    return [item["event_type"] for item in ACTIVITY_EVENT_CATALOG]


def build_activity_api_spec() -> dict[str, Any]:
    return {
        "description": (
            "Real-time Cursor-like feed of Continue cn serve activity. "
            "Parse session.history from GET /state and Syte workspace actions."
        ),
        "continue_reference": "https://docs.continue.dev/guides/cli",
        "snapshot": "GET /api/agent_activity?uuid=&since_id=0",
        "stream": "GET /api/projects/{uuid}/agent/activity/stream?live=1&since_id=0",
        "internal_stream": "GET /api/internal/projects/{uuid}/agent/activity/stream?live=1",
        "sse_types": {
            "activity": "Structured event object (persisted)",
            "processing": "Ephemeral working indicator while isProcessing=true",
            "session": "Stream connected",
            "ping": "Keepalive with since_id",
        },
        "event_fields": ["id", "project_id", "event_type", "role", "title", "detail", "payload", "source", "created_at"],
        "event_types": activity_event_types(),
        "catalog": ACTIVITY_EVENT_CATALOG,
        "sources": ["agent", "api", "sycord", "gui", "internal"],
    }
