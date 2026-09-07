"""FastAPI routes for the Syte AI Builder agent subsystem with Project and Session UUID support."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
import uuid
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from syte.ai.engine import AIAgentEngine
from syte.ai.providers import UnifiedAIClient
from syte.ai.session_manager import session_manager
from syte.ai.skills import list_available_skills
from syte.auth import verify_operator_session_or_token
from syte.database import (
    clear_ai_chat_history,
    delete_ai_chat_message,
    get_ai_builder_settings,
    get_project,
    list_ai_chat_messages,
    save_ai_builder_settings,
)

router = APIRouter(tags=["AI Builder"])


class AIChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=50000)
    session_id: Optional[str] = Field(None, description="Optional custom session UUID to isolate stream/context.")
    provider: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    system_prompt: Optional[str] = None


class AIAgentChatStreamRequest(BaseModel):
    """External API request payload for streaming agent messages directly with session UUID support."""
    message: str = Field(..., min_length=1, max_length=50000, description="The prompt or instruction for the AI agent.")
    session_id: Optional[str] = Field(None, description="Optional unique Session UUID to isolate separate concurrent streams.")
    provider: Optional[str] = Field(None, description="Target LLM provider (openai, anthropic, gemini, deepseek, openrouter, ollama).")
    model: Optional[str] = Field(None, description="Model ID to execute (e.g., gpt-4o, claude-3-5-sonnet-20241022, deepseek-chat).")
    temperature: Optional[float] = Field(None, ge=0.0, le=2.0, description="Sampling temperature.")
    max_tokens: Optional[int] = Field(None, ge=1, le=128000, description="Maximum tokens for generation.")
    system_prompt: Optional[str] = Field(None, description="Custom system instructions overriding defaults.")
    stream_tokens_only: Optional[bool] = Field(False, description="When True, SSE only yields assistant token deltas.")
    execution_speed: Optional[str] = Field(None, description="Execution speed profile: ultra_fast, balanced, or deep_reasoning.")
    thinking_level: Optional[str] = Field(None, description="Thinking level: low, medium, high, extra_high, max.")


class SessionThinkingUpdateRequest(BaseModel):
    thinking_level: str = Field(..., description="Thinking budget or speed: low, medium, high, extra_high, max.")
    execution_speed: Optional[str] = Field(None, description="Speed preset: ultra_fast, balanced, deep_reasoning.")


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
    execution_speed: Optional[str] = None
    intelligence_level: Optional[str] = None
    plan_approval_mode: Optional[str] = None
    saved_providers: Optional[Any] = None


class AITestConnectionRequest(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = ""
    base_url: Optional[str] = ""


@router.get("/api/ai/settings")
@router.get("/api/projects/{project_id}/ai/settings")
async def get_project_ai_settings(
    project_id: str = "global",
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Retrieve AI Builder configuration for a specific project or platform global."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    settings = await get_ai_builder_settings(project_id)
    raw_key = settings.get("api_key") or ""
    masked_key = raw_key[:6] + "••••••••" + raw_key[-4:] if len(raw_key) > 10 else ("••••••••" if raw_key else "")
    res = dict(settings)
    res["api_key_masked"] = masked_key
    res["has_api_key"] = bool(raw_key)
    return {"ok": True, "settings": res}


@router.put("/api/ai/settings")
@router.put("/api/projects/{project_id}/ai/settings")
async def update_project_ai_settings(
    body: AISettingsUpdateRequest,
    project_id: str = "global",
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Update AI Builder configuration, switch model, or set agent settings."""
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
    """Reset and clear AI chat history and in-memory session for a project."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    await clear_ai_chat_history(project_id)
    session_manager.clear_session(project_id)
    return {"ok": True, "message": "AI chat history cleared"}


@router.delete("/api/projects/{project_id}/ai/history/{message_id}")
async def delete_single_ai_message(
    project_id: str,
    message_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Delete an individual AI chat message from history."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    await delete_ai_chat_message(project_id, message_id)
    return {"ok": True, "message_id": message_id, "message": "Message deleted"}


@router.post("/api/projects/{project_id}/ai/providers/activate")
async def activate_saved_provider(
    project_id: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Switch active model or saved provider configuration."""
    current = await get_ai_builder_settings(project_id)
    provider_id = body.get("provider_id")
    model = body.get("model")
    saved_list = current.get("saved_providers") or []

    target = None
    if provider_id:
        for p in saved_list:
            if p.get("id") == provider_id or p.get("name") == provider_id:
                target = p
                break
    elif model:
        for p in saved_list:
            if p.get("model") == model:
                target = p
                break

    if target:
        provider_name = target.get("provider") or current.get("provider")
        api_key = target.get("api_key") or current.get("api_key") or ""
        if not api_key:
            for p in saved_list:
                if (p.get("provider") == provider_name or not provider_name) and p.get("api_key"):
                    api_key = p.get("api_key")
                    break
        updates = {
            "provider": provider_name,
            "model": target.get("model") or model or current.get("model"),
            "api_key": api_key,
            "base_url": target.get("base_url") if target.get("base_url") is not None else current.get("base_url"),
        }
        saved = await save_ai_builder_settings(project_id, updates)
        return {"ok": True, "settings": saved}
    elif model:
        updates = {"model": model}
        saved = await save_ai_builder_settings(project_id, updates)
        return {"ok": True, "settings": saved}

    return {"ok": False, "error": "Saved provider not found"}


@router.get("/api/ai/models")
@router.get("/api/projects/{project_id}/ai/models")
@router.get("/api/models")
@router.get("/models")
async def list_ai_models(
    project_id: str = "global",
    provider: Optional[str] = None,
    request: Request = None,
):
    """Request and list available AI models for the configured or requested provider and project UUID."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, detail={"error": "project_not_found", "message": f"Project UUID '{project_id}' not found."})

    settings = await get_ai_builder_settings(project_id)
    target_provider = (provider or settings.get("provider") or "openai").lower().strip()
    api_key = settings.get("api_key") or ""
    base_url = settings.get("base_url") or ""
    saved_providers = settings.get("saved_providers") or []
    current_model = settings.get("model") or "gpt-4o"

    # Tab preset catalog (all models available from the AI tab)
    ai_tab_presets: Dict[str, List[str]] = {
        "vertex": [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-pro-002",
            "claude-3-5-sonnet@20241022",
            "meta/llama-3.3-70b-instruct-maas",
        ],
        "gemini": [
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-1.5-pro",
            "gemini-1.5-flash",
        ],
        "openai": [
            "gpt-4o",
            "gpt-4o-mini",
            "o3-mini",
            "o1",
            "o1-preview",
        ],
        "anthropic": [
            "claude-3-7-sonnet",
            "claude-3-5-sonnet-20241022",
            "claude-3-5-haiku-20241022",
            "claude-3-opus-20240229",
        ],
        "deepseek": [
            "deepseek-chat",
            "deepseek-reasoner",
            "deepseek-coder",
        ],
        "openrouter": [
            "z-ai/glm-5.2:free",
            "openai/gpt-4o",
            "deepseek/deepseek-r1",
            "anthropic/claude-3.5-sonnet",
            "meta-llama/llama-3.3-70b-instruct",
            "qwen/qwen-2.5-coder-32b-instruct",
        ],
        "ollama": [
            "qwen2.5-coder:32b",
            "llama3.3:70b",
            "deepseek-r1:14b",
            "qwen2.5-coder:latest",
            "llama3.2:latest",
        ],
        "custom": [
            "gpt-4o",
            "claude-3-5-sonnet-20241022",
            "deepseek-chat",
        ],
    }

    client = UnifiedAIClient(
        provider=target_provider,
        model=current_model,
        api_key=api_key,
        base_url=base_url,
    )
    models = await client.list_available_models()

    # If no saved providers exist in settings, do NOT display synthetic/preset models — keep list empty
    enabled_saved_providers = [sp for sp in saved_providers if sp.get("active") is not False]
    all_ai_tab_models = []
    seen_model_ids = set()

    for sp in enabled_saved_providers:
        sp_m = sp.get("model")
        if sp_m and sp_m not in seen_model_ids:
            seen_model_ids.add(sp_m)
            all_ai_tab_models.append({
                "id": sp_m,
                "name": sp.get("name") or sp_m,
                "provider": sp.get("provider") or target_provider,
                "custom_saved": True,
                "active": sp.get("active", True),
            })

    # If streaming is requested (via query param stream=true or Accept: text/event-stream)
    wants_stream = False
    if request:
        if request.query_params.get("stream") in ("true", "1", "yes"):
            wants_stream = True
        elif "text/event-stream" in (request.headers.get("accept") or ""):
            wants_stream = True

    if wants_stream:
        async def sse_models_generator():
            for m in all_ai_tab_models:
                yield f"data: {json.dumps(m)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(sse_models_generator(), media_type="text/event-stream")

    return {
        "ok": True,
        "status": "ok",
        "project_id": project_id,
        "uuid": project_id,
        "provider": target_provider,
        "current_model": current_model,
        "models": all_ai_tab_models,
        "ai_tab_models": all_ai_tab_models,
        "saved_providers": saved_providers,
        "enabled_saved_providers": enabled_saved_providers,
        "count": len(all_ai_tab_models),
        "total_available_models": len(all_ai_tab_models),
    }


# =========================================================================
# AI STREAMING ROUTE WITH PROJECT & SESSION UUID SUPPORT
# =========================================================================
@router.post("/api/ai/stream")
@router.post("/api/projects/{project_id}/ai/stream")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/stream")
async def stream_agent_messages_to_external(
    body: AIAgentChatStreamRequest,
    project_id: str = "global",
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Stream AI agent thoughts, tokens, and tool events directly to external apps via Server-Sent Events (SSE).
    Uses project_id and optional session_id UUID to identify exactly which session to stream.
    """
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    target_session_id = session_id or body.session_id or str(uuid.uuid4())
    overrides = body.model_dump(exclude_none=True)
    stream_tokens_only = bool(body.stream_tokens_only)

    active_sess = await session_manager.start_turn(
        project_id=project_id,
        user_message=body.message,
        session_id=target_session_id,
        settings_override=overrides,
    )

    async def sse_external_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id, session_id=target_session_id, replay=False):
                evt_name = event_payload.get("event", "message")
                
                # If caller only requested token deltas
                if stream_tokens_only:
                    if evt_name in ("token", "token_delta"):
                        yield f"data: {event_payload.get('delta') or event_payload.get('token', '')}\n\n"
                    elif evt_name == "done":
                        yield "data: [DONE]\n\n"
                        break
                    elif evt_name == "error":
                        yield f"event: error\ndata: {json.dumps(event_payload)}\n\n"
                        break
                    continue

                # Standard full-structured agent stream
                data_str = json.dumps(event_payload)
                yield f"event: {evt_name}\ndata: {data_str}\n\n"
                if evt_name in ("done", "stopped"):
                    break
        except Exception as exc:
            err_data = json.dumps({"event": "error", "session_id": target_session_id, "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_external_broadcaster(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Syte-Session-ID": target_session_id,
            "X-Syte-Project-ID": project_id,
        },
    )


@router.post("/api/ai/cancel")
@router.post("/api/projects/{project_id}/ai/cancel")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/cancel")
async def cancel_agent_answer(
    project_id: str = "global",
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Cancel and terminate the active agent answer and background turn immediately."""
    res = await session_manager.stop_session(project_id, session_id=session_id)
    return res


@router.post("/api/ai/sessions/{session_id}/thinking")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/thinking")
async def update_session_thinking_level(
    session_id: str,
    body: SessionThinkingUpdateRequest,
    project_id: str = "global",
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Dynamically configure thinking budget / reasoning speed session-wide (low, medium, high, extra_high, max)."""
    session = session_manager.get_or_create_session(project_id, session_id=session_id)
    session.thinking_level = str(body.thinking_level).strip().lower()
    if body.execution_speed:
        session.execution_speed = str(body.execution_speed).strip().lower()
    session.add_event({
        "event": "thinking_level_updated",
        "session_id": session.session_id,
        "thinking_level": session.thinking_level,
        "execution_speed": session.execution_speed,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })
    return {
        "ok": True,
        "session_id": session.session_id,
        "project_id": project_id,
        "thinking_level": session.thinking_level,
        "execution_speed": session.execution_speed,
    }


@router.post("/api/projects/{project_id}/ai/test-connection")
async def test_ai_provider_connection(
    project_id: str,
    body: AITestConnectionRequest,
):
    """Test connectivity to an LLM provider and model."""
    api_key = (body.api_key or "").strip()
    if not api_key:
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


@router.get("/api/projects/{project_id}/ai/session")
@router.get("/api/projects/{project_id}/ai/sessions/{session_id}")
async def get_project_ai_session(
    project_id: str,
    session_id: Optional[str] = None,
):
    """Get active background agent session state, current plan, and pending questions by project UUID and optional session UUID."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
    session = session_manager.get_or_create_session(project_id, session_id=session_id)
    return {"ok": True, "session": session.get_status_summary()}


@router.get("/api/projects/{project_id}/ai/events")
@router.get("/api/projects/{project_id}/ai/sessions/{session_id}/events")
async def stream_project_ai_events(
    project_id: str,
    session_id: Optional[str] = None,
    replay: bool = False,
):
    """Reconnect or subscribe to live AI agent SSE event stream for a specific session UUID."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    target_session_id = session_id

    async def sse_event_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id, session_id=target_session_id, replay=replay):
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
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/answer")
async def submit_project_ai_answer(
    project_id: str,
    body: Dict[str, Any],
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Submit user clarification answer or securely store an environment secret in project .env."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    target_session_id = session_id or body.get("session_id")
    res = await session_manager.handle_user_answer(project_id, body, session_id=target_session_id)
    return res


@router.post("/api/projects/{project_id}/ai/plan/decision")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/plan/decision")
async def submit_project_ai_plan_decision(
    project_id: str,
    body: Dict[str, Any],
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Submit user decision on active plan (accept, start now, pause, revise)."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    target_session_id = session_id or body.get("session_id")
    res = await session_manager.handle_user_plan_decision(project_id, body, session_id=target_session_id)
    return res


@router.get("/api/projects/{project_id}/ai/skills")
async def get_project_ai_skills(project_id: str):
    """List available domain skills and blueprints."""
    return {"ok": True, "skills": list_available_skills()}


@router.post("/api/projects/{project_id}/ai/chat")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/chat")
async def project_ai_chat_stream(
    project_id: str,
    body: AIChatRequest,
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Initiate an autonomous AI agent turn with persistent background VM execution and SSE stream."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    target_session_id = session_id or body.session_id or str(uuid.uuid4())
    overrides = body.model_dump(exclude_none=True)

    # Start or attach background task
    await session_manager.start_turn(
        project_id=project_id,
        user_message=body.message,
        session_id=target_session_id,
        settings_override=overrides,
    )

    async def sse_generator():
        try:
            async for event_payload in session_manager.subscribe(project_id, session_id=target_session_id, replay=False):
                event_name = event_payload.get("event", "message")
                data_str = json.dumps(event_payload)
                yield f"event: {event_name}\ndata: {data_str}\n\n"
        except Exception as exc:
            err_data = json.dumps({"event": "error", "session_id": target_session_id, "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n"

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "X-Syte-Session-ID": target_session_id,
            "X-Syte-Project-ID": project_id,
        },
    )


@router.post("/api/projects/{project_id}/ai/stop")
@router.post("/api/projects/{project_id}/ai/sessions/{session_id}/stop")
async def stop_project_ai_agent(
    project_id: str,
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Stop/cancel active autonomous agent execution for a project or session UUID."""
    res = await session_manager.stop_session(project_id, session_id=session_id)
    return res


@router.get("/api/projects/{project_id}/ai/file")
async def get_project_workspace_file_content(
    project_id: str,
    path: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Retrieve content of a specific workspace file for code viewing / inspection."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)
    else:
        from syte.config import settings
        ws_dir = settings.data_dir

    rel_path = path.lstrip("/\\").strip()
    if not rel_path or rel_path in (".", "/"):
        raise HTTPException(400, "Invalid file path")

    file_path = ws_dir / rel_path
    if not file_path.resolve().is_relative_to(ws_dir.resolve()):
        raise HTTPException(403, "Access denied: Path escapes workspace")

    if not file_path.exists() or file_path.is_dir():
        raise HTTPException(404, f"File '{rel_path}' not found")

    content = file_path.read_text(encoding="utf-8", errors="replace")
    return {
        "ok": True,
        "path": rel_path,
        "size_bytes": len(content),
        "lines_count": len(content.splitlines()),
        "content": content,
    }


@router.get("/api/projects/{project_id}/ai/diagnostics")
async def export_project_ai_diagnostics(
    project_id: str,
    session_id: Optional[str] = None,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Export a comprehensive diagnostic bundle JSON including VM response, session events, DB history, errors, and system stats."""
    now_iso = datetime.now(timezone.utc).isoformat()
    project_meta = {}
    ws_dir = ""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = str(_get_project_workspace_dir(project))
        project_meta = {
            "id": project.get("id"),
            "name": project.get("name"),
            "domain": project.get("domain"),
            "branch": project.get("branch"),
            "running": bool(project.get("running")),
            "port": project.get("port"),
            "git_url": project.get("git_url"),
            "workspace_dir": ws_dir,
        }
    else:
        project_meta = {"id": "global", "name": "Global Platform"}

    ai_settings = await get_ai_builder_settings(project_id)
    sanitized_settings = dict(ai_settings)
    if sanitized_settings.get("api_key"):
        raw_k = str(sanitized_settings["api_key"])
        sanitized_settings["api_key"] = raw_k[:6] + "..." + raw_k[-4:] if len(raw_k) > 10 else "***"
    if isinstance(sanitized_settings.get("saved_providers"), list):
        san_provs = []
        for p in sanitized_settings["saved_providers"]:
            sp = dict(p)
            if sp.get("api_key"):
                rk = str(sp["api_key"])
                sp["api_key"] = rk[:6] + "..." + rk[-4:] if len(rk) > 10 else "***"
            san_provs.append(sp)
        sanitized_settings["saved_providers"] = san_provs

    session = session_manager.get_or_create_session(project_id, session_id=session_id)
    session_summary = session.get_status_summary()
    session_events = list(session.event_buffer)

    db_messages = await list_ai_chat_messages(project_id, limit=200)

    sys_stats = {}
    try:
        from syte.system_stats import get_system_stats
        sys_stats = get_system_stats()
    except Exception as e:
        sys_stats = {"error": str(e)}

    recent_logs = []
    if project_id != "global":
        try:
            from syte.process_manager import get_logs
            raw_logs = get_logs(project_id, lines=80)
            recent_logs = [line for line in raw_logs.splitlines() if line.strip()]
        except Exception as e:
            recent_logs = [f"Log retrieval error: {e}"]

    errors_detected = []
    for evt in session_events:
        if evt.get("event") == "error" or "error" in evt:
            errors_detected.append({"source": "session_event", "event": evt})
        elif evt.get("event") == "tool_call_result":
            res = evt.get("result") or {}
            if res.get("ok") is False or "error" in res or res.get("syntax_errors"):
                errors_detected.append({"source": "tool_failure", "tool_name": evt.get("tool_name"), "result": res})

    for msg in db_messages:
        if msg.get("role") == "tool":
            try:
                parsed_c = json.loads(msg.get("content") or "{}")
                if parsed_c.get("ok") is False or "error" in parsed_c or parsed_c.get("syntax_errors"):
                    errors_detected.append({"source": "database_tool_message", "tool": msg.get("name"), "content": parsed_c})
            except Exception:
                pass

    diagnostic_bundle = {
        "export_timestamp": now_iso,
        "syte_version": "2.0.0",
        "project": project_meta,
        "ai_settings": sanitized_settings,
        "session": {
            "summary": session_summary,
            "event_buffer_count": len(session_events),
            "event_buffer": session_events,
        },
        "chat_history": {
            "message_count": len(db_messages),
            "messages": db_messages,
        },
        "errors_and_failures": {
            "error_count": len(errors_detected),
            "errors": errors_detected,
        },
        "vm_diagnostics": {
            "system_stats": sys_stats,
            "recent_process_logs": recent_logs,
        },
    }

    filename = f"syte-ai-diagnostics-{project_id}-{session.session_id[:8]}-{int(time.time())}.json"
    return JSONResponse(
        content=diagnostic_bundle,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
