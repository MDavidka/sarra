"""FastAPI routes for the Syte AI Builder agent subsystem."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import time
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
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
    from syte.database import delete_ai_chat_message
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
        updates = {
            "provider": target.get("provider") or current.get("provider"),
            "model": target.get("model") or model or current.get("model"),
            "api_key": target.get("api_key") or current.get("api_key"),
            "base_url": target.get("base_url") if target.get("base_url") is not None else current.get("base_url"),
        }
        saved = await save_ai_builder_settings(project_id, updates)
        return {"ok": True, "settings": saved}
    elif model:
        updates = {"model": model}
        saved = await save_ai_builder_settings(project_id, updates)
        return {"ok": True, "settings": saved}

    return {"ok": False, "error": "Saved provider not found"}


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
async def stream_project_ai_events(project_id: str, replay: bool = False):
    """Reconnect or subscribe to live AI agent SSE event stream."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    async def sse_event_broadcaster():
        try:
            async for event_payload in session_manager.subscribe(project_id, replay=replay):
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
            async for event_payload in session_manager.subscribe(project_id, replay=True):
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


@router.post("/api/projects/{project_id}/ai/stop")
async def stop_project_ai_agent(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Stop/cancel active autonomous agent execution for a project."""
    res = await session_manager.stop_session(project_id)
    return res


@router.get("/api/projects/{project_id}/ai/diagnostics")
async def export_project_ai_diagnostics(
    project_id: str,
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

    # 1. AI Settings
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

    # 2. Session state & in-memory event buffer
    session = session_manager.get_or_create_session(project_id)
    session_summary = session.get_status_summary()
    session_events = list(session.event_buffer)

    # 3. Database chat history
    db_messages = await list_ai_chat_messages(project_id, limit=200)

    # 4. System & VM Diagnostics
    sys_stats = {}
    try:
        from syte.system_stats import get_system_stats
        sys_stats = get_system_stats()
    except Exception as e:
        sys_stats = {"error": str(e)}

    # 5. Process / deployment logs
    recent_logs = []
    if project_id != "global":
        try:
            from syte.process_manager import get_logs
            raw_logs = get_logs(project_id, lines=80)
            recent_logs = [line for line in raw_logs.splitlines() if line.strip()]
        except Exception as e:
            recent_logs = [f"Log retrieval error: {e}"]

    # 6. Extract all errors encountered
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

    filename = f"syte-ai-diagnostics-{project_id}-{int(time.time())}.json"
    return JSONResponse(
        content=diagnostic_bundle,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )

