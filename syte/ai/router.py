"""FastAPI routes for the Syte AI Builder agent subsystem."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from syte.ai.engine import AIAgentEngine
from syte.ai.providers import UnifiedAIClient
from syte.auth import verify_operator_session_or_token
from syte.database import (
    clear_ai_chat_history,
    get_ai_builder_settings,
    get_project,
    list_ai_chat_messages,
    save_ai_builder_settings,
)

router = APIRouter(tags=["AI Builder"])


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None


class AISettingsUpdateRequest(BaseModel):
    provider: Optional[str] = None
    model: Optional[str] = None
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    thinking_level: Optional[str] = None
    system_prompt: Optional[str] = None
    tools_enabled: Optional[str] = None
    custom_models: Optional[str] = None


class AITestConnectionRequest(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = ""
    base_url: Optional[str] = ""


@router.get("/api/projects/{project_id}/ai/settings")
async def get_project_ai_settings(project_id: str):
    """Retrieve AI Builder configuration for a project."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    settings = await get_ai_builder_settings(project_id)
    # Mask API key if present
    masked_key = ""
    if settings.get("api_key"):
        k = settings["api_key"]
        masked_key = f"{k[:4]}••••••••{k[-4:]}" if len(k) > 10 else "••••••••"
    return {
        "ok": True,
        "settings": {
            **settings,
            "api_key_masked": masked_key,
            "has_api_key": bool(settings.get("api_key")),
        },
    }


@router.put("/api/projects/{project_id}/ai/settings")
async def update_project_ai_settings(
    project_id: str,
    body: AISettingsUpdateRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Update AI Builder configuration for a project."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    data = body.model_dump(exclude_none=True)
    saved = await save_ai_builder_settings(project_id, data)
    return {"ok": True, "settings": saved}


@router.get("/api/projects/{project_id}/ai/history")
async def get_project_ai_history(project_id: str):
    """Retrieve AI chat history for a project."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    messages = await list_ai_chat_messages(project_id, limit=100)
    return {"ok": True, "project_id": project_id, "messages": messages}


@router.delete("/api/projects/{project_id}/ai/history")
async def clear_project_ai_history(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Reset and clear AI chat history for a project."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    await clear_ai_chat_history(project_id)
    return {"ok": True, "message": "AI chat history cleared"}


@router.post("/api/projects/{project_id}/ai/test-connection")
async def test_ai_provider_connection(
    project_id: str,
    body: AITestConnectionRequest,
):
    """Test connectivity to an LLM provider and model."""
    api_key = (body.api_key or "").strip()
    if not api_key:
        # Load saved key if not supplied in test payload
        current = await get_ai_builder_settings(project_id)
        api_key = current.get("api_key") or ""

    client = UnifiedAIClient(
        provider=body.provider,
        model=body.model,
        api_key=api_key,
        base_url=body.base_url or "",
    )
    result = await client.test_connection()
    return result


from syte.ai.session_manager import session_manager
from syte.ai.skills import list_available_skills


@router.get("/api/projects/{project_id}/ai/session")
async def get_project_ai_session(project_id: str):
    """Get active background agent session state, current plan, and pending questions."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    session = session_manager.get_or_create_session(project_id)
    return {"ok": True, "session": session.get_status_summary()}


@router.get("/api/projects/{project_id}/ai/events")
async def stream_project_ai_events(project_id: str):
    """Reconnect or subscribe to live AI agent SSE event stream."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    async def sse_event_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id):
                event_name = event_payload.get("event", "message")
                data_str = json.dumps(event_payload)
                yield f"event: {event_name}\ndata: {data_str}\n\n"
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_event_broadcaster(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/projects/{project_id}/ai/answer")
async def submit_project_ai_answer(
    project_id: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Submit user clarification answer or securely store an environment secret in project .env."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    res = await session_manager.handle_user_answer(project_id, body)
    return res


@router.get("/api/projects/{project_id}/ai/skills")
async def get_project_ai_skills(project_id: str):
    """List available domain skills and blueprints."""
    return {"ok": True, "skills": list_available_skills()}


@router.post("/api/projects/{project_id}/ai/chat")
async def project_ai_chat_stream(
    project_id: str,
    body: AIChatRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Initiate an autonomous AI agent turn with persistent background VM execution and SSE stream."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    overrides = body.model_dump(exclude_none=True)

    # Start or attach background task
    await session_manager.start_turn(
        project_id=project_id,
        user_message=body.message,
        settings_override=overrides,
    )

    async def sse_generator():
        try:
            async for event_payload in session_manager.subscribe(project_id):
                event_name = event_payload.get("event", "message")
                data_str = json.dumps(event_payload)
                yield f"event: {event_name}\ndata: {data_str}\n\n"
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
