"""FastAPI routes for the Syte AI Builder agent subsystem."""

from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from syte.ai.engine import AIAgentEngine
from syte.ai.providers import UnifiedAIClient
from syte.auth import verify_operator_session_or_token
from syte.sse_core import SSE_HEADERS
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
    from syte.ai.providers import _clean_string
    for str_key in ("provider", "model", "api_key", "base_url", "system_prompt", "tools_enabled", "custom_models"):
        if str_key in data and isinstance(data[str_key], str):
            data[str_key] = _clean_string(data[str_key])

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


@router.post("/api/projects/{project_id}/ai/test-connection")
async def test_ai_provider_connection(
    project_id: str,
    body: AITestConnectionRequest,
):
    """Test connectivity to an LLM provider and model."""
    from syte.ai.providers import _clean_string
    provider = _clean_string(body.provider)
    model = _clean_string(body.model)
    api_key = _clean_string(body.api_key or "")
    base_url = _clean_string(body.base_url or "")
    gcp_project = _clean_string(body.gcp_project or "")
    gcp_location = _clean_string(body.gcp_location or "us-central1")

    if not api_key or not gcp_project:
        # Load saved settings if not supplied in test payload
        current = await get_ai_builder_settings(project_id)
        if not api_key:
            api_key = _clean_string(current.get("api_key") or "")
        if not gcp_project:
            gcp_project = _clean_string(current.get("gcp_project") or "")
        if not gcp_location or gcp_location == "us-central1":
            gcp_location = _clean_string(current.get("gcp_location") or "us-central1")

    client = UnifiedAIClient(
        provider=provider,
        model=model,
        api_key=api_key,
        base_url=base_url,
        gcp_project=gcp_project,
        gcp_location=gcp_location,
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
async def stream_project_ai_events(
    project_id: str,
    replay: bool = False,
    since_id: int = 0,
):
    """Reconnect or subscribe to live AI agent SSE event stream.

    Legacy surface for the GUI; the dedicated optimized window is
    ``GET /api/stream/projects/{project_id}/events`` (see
    ``docs/ai-chat-streaming.md``).
    """
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")

    async def sse_event_broadcaster():
        try:
            async for frame in session_manager.subscribe(project_id, replay=replay, since_id=since_id):
                yield frame
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n".encode("utf-8")

    return StreamingResponse(
        sse_event_broadcaster(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
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

    # Capture the replay cursor before starting so the stream contains
    # exactly this turn's events (no stale replay, no missed fast turns).
    session = session_manager.get_or_create_session(project_id)
    since_id = session.last_event_id

    # Start or attach background task
    await session_manager.start_turn(
        project_id=project_id,
        user_message=body.message,
        settings_override=overrides,
    )

    async def sse_generator():
        try:
            async for frame in session_manager.subscribe(project_id, replay=False, since_id=since_id):
                yield frame
        except Exception as exc:
            err_data = json.dumps({"event": "error", "error": str(exc)})
            yield f"event: error\ndata: {err_data}\n\n".encode("utf-8")

    return StreamingResponse(
        sse_generator(),
        media_type="text/event-stream",
        headers=SSE_HEADERS,
    )


@router.post("/api/projects/{project_id}/ai/stop")
async def stop_project_ai_agent(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Stop/cancel active autonomous agent execution for a project."""
    res = await session_manager.stop_session(project_id)
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


@router.post("/api/ai/upload")
@router.post("/api/agent/upload")
@router.post("/api/projects/{project_id}/ai/upload")
@router.post("/api/projects/{project_id}/upload")
@router.post("/projects/{project_id}/ai/upload")
@router.post("/projects/{project_id}/upload")
async def upload_ai_files(
    project_id: str = "global",
    files: List[UploadFile] = File(...),
    extract_to_workspace: bool = Form(False),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Handle mass file uploads (.zip, excel, word, pdf, csv, code, text) and extract structured AI understanding."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)
    else:
        from syte.config import settings
        ws_dir = settings.data_dir

    from syte.ai.file_parser import extract_zip_to_workspace, parse_uploaded_file
    from syte.database import list_project_uploaded_files, save_project_uploaded_file

    parsed_results = []
    extraction_results = []
    total_bytes = 0

    uploads_dir = Path(ws_dir) / "uploads"
    uploads_dir.mkdir(parents=True, exist_ok=True)

    for file in files:
        raw_bytes = await file.read()
        total_bytes += len(raw_bytes)
        safe_name = re.sub(r"[^\w\.-]", "_", file.filename or "uploaded_file")
        parsed = parse_uploaded_file(safe_name, raw_bytes)
        parsed_results.append(parsed)

        # 1. Save physical file to uploads directory so tools and agent can read it
        dest_path = uploads_dir / safe_name
        dest_path.write_bytes(raw_bytes)
        rel_path = f"uploads/{safe_name}"

        # 2. Persist record in project_uploaded_files table
        if project_id != "global":
            await save_project_uploaded_file(
                project_id=project_id,
                filename=safe_name,
                file_path=rel_path,
                file_size=len(raw_bytes),
                extension=parsed.get("extension") or Path(safe_name).suffix,
                summary=parsed.get("summary") or "",
                parsed_content=(parsed.get("parsed_content") or "")[:5000],
            )

        # If user requested to unpack zip directly into the workspace
        if extract_to_workspace and parsed.get("extension") == ".zip":
            ext_res = extract_zip_to_workspace(raw_bytes, ws_dir)
            extraction_results.append({"filename": file.filename, **ext_res})

    # Combine prompt context summary
    context_blocks = []
    for p in parsed_results:
        context_blocks.append(f"### [Uploaded File: {p['filename']} ({p['summary']})]\n{p['parsed_content']}\n")
    combined_prompt_context = "\n\n".join(context_blocks)

    return {
        "ok": True,
        "total_files": len(parsed_results),
        "total_bytes": total_bytes,
        "files": parsed_results,
        "combined_prompt_context": combined_prompt_context,
        "extraction_results": extraction_results if extraction_results else None,
        "message": f"Successfully parsed and saved {len(parsed_results)} file(s) to workspace uploads for AI understanding.",
    }


@router.get("/api/projects/{project_id}/ai/uploads")
@router.get("/projects/{project_id}/ai/uploads")
async def list_project_uploads_endpoint(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Retrieve all loaded files currently available to the AI agent in this project."""
    from syte.database import list_project_uploaded_files
    uploads = await list_project_uploaded_files(project_id)
    return {"ok": True, "project_id": project_id, "uploads": uploads, "count": len(uploads)}


@router.delete("/api/projects/{project_id}/ai/uploads/{file_id}")
@router.delete("/projects/{project_id}/ai/uploads/{file_id}")
async def delete_project_upload_endpoint(
    project_id: str,
    file_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Remove a previously uploaded file from the project workspace and AI context."""
    from syte.database import delete_project_uploaded_file, list_project_uploaded_files
    uploads = await list_project_uploaded_files(project_id)
    target = next((u for u in uploads if u["id"] == file_id), None)
    if target:
        project = await get_project(project_id)
        if project:
            from syte.ai.tools import _get_project_workspace_dir
            ws_dir = _get_project_workspace_dir(project)
            file_on_disk = Path(ws_dir) / target["file_path"]
            if file_on_disk.exists():
                try:
                    file_on_disk.unlink()
                except Exception:
                    pass
    deleted = await delete_project_uploaded_file(project_id, file_id)
    return {"ok": deleted, "deleted_id": file_id}


class DeepFocusUpdateRequest(BaseModel):
    custom_memory: Optional[str] = None
    framework_stack: Optional[Dict[str, Any]] = None
    architecture: Optional[Dict[str, Any]] = None


@router.get("/api/projects/{project_id}/ai/deep-focus")
@router.get("/api/projects/{project_id}/ai/memory")
async def get_project_deep_focus_endpoint(project_id: str):
    """Retrieve Deep Focus (Project Memory) model and cached index."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)
    else:
        ws_dir = None

    from syte.database import get_project_deep_focus
    from syte.ai.deep_focus import build_deep_focus_index

    stored = await get_project_deep_focus(project_id)
    custom_mem = stored.get("custom_memory", "") if stored else ""
    deep_focus = await build_deep_focus_index(project_id, ws_dir=ws_dir, custom_memory=custom_mem)

    return {
        "ok": True,
        "project_id": project_id,
        "deep_focus": deep_focus,
        "project_memory": deep_focus,
    }


@router.post("/api/projects/{project_id}/ai/deep-focus")
@router.post("/api/projects/{project_id}/ai/memory")
async def update_project_deep_focus_endpoint(
    project_id: str,
    body: DeepFocusUpdateRequest,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Update custom memory notes or Deep Focus configurations."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)
    else:
        ws_dir = None

    from syte.database import save_project_deep_focus
    from syte.ai.deep_focus import build_deep_focus_index

    saved = await save_project_deep_focus(
        project_id,
        custom_memory=body.custom_memory,
        framework_stack=body.framework_stack,
        architecture_map=body.architecture,
    )
    fresh_df = await build_deep_focus_index(project_id, ws_dir=ws_dir, custom_memory=saved.get("custom_memory", ""))

    return {
        "ok": True,
        "message": "Deep Focus / Project Memory updated successfully.",
        "deep_focus": fresh_df,
        "project_memory": fresh_df,
    }


@router.post("/api/projects/{project_id}/ai/deep-focus/rebuild")
@router.post("/api/projects/{project_id}/ai/memory/rebuild")
async def rebuild_project_deep_focus_endpoint(
    project_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Re-scan workspace and rebuild Deep Focus index from scratch."""
    if project_id != "global":
        project = await get_project(project_id)
        if not project:
            raise HTTPException(404, "Project not found")
        from syte.ai.tools import _get_project_workspace_dir
        ws_dir = _get_project_workspace_dir(project)
    else:
        ws_dir = None

    from syte.database import get_project_deep_focus, save_project_deep_focus
    from syte.ai.deep_focus import build_deep_focus_index

    stored = await get_project_deep_focus(project_id)
    custom_mem = stored.get("custom_memory", "") if stored else ""

    fresh_df = await build_deep_focus_index(project_id, ws_dir=ws_dir, custom_memory=custom_mem)
    await save_project_deep_focus(
        project_id,
        custom_memory=custom_mem,
        framework_stack=fresh_df.get("framework_stack"),
        architecture_map=fresh_df.get("architecture"),
    )

    return {
        "ok": True,
        "message": "Deep Focus index rebuilt and synchronized from workspace.",
        "deep_focus": fresh_df,
        "project_memory": fresh_df,
    }


