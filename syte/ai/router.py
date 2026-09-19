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
    get_omni_model,
    get_project,
    get_user_credits,
    list_ai_chat_messages,
    list_ai_usage_records,
    list_custom_providers,
    list_omni_models,
    save_ai_builder_settings,
    sync_handshake_providers,
    upsert_omni_model,
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
    gcp_project: Optional[str] = None
    gcp_location: Optional[str] = None


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
    """List available domain skills and blueprints for a project."""
    from syte.ai.skills import list_available_skills_for_project
    skills = await list_available_skills_for_project(project_id)
    return {"ok": True, "skills": skills}


@router.post("/api/projects/{project_id}/ai/skills/upload")
async def upload_project_skill(
    project_id: str,
    file: UploadFile = File(...),
    responsibility: str = Form("general"),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Upload a custom skill file (.md, .txt, .json, .py) to project skills registry."""
    from syte.database import save_project_skill
    import uuid

    raw_bytes = await file.read()
    content_str = raw_bytes.decode("utf-8", errors="replace").strip()
    skill_name = Path(file.filename or "custom-skill").stem
    skill_id = f"skill_{uuid.uuid4().hex[:8]}"

    desc = f"Uploaded skill from {file.filename}"
    first_lines = [l.strip() for l in content_str.splitlines() if l.strip()]
    if first_lines and first_lines[0].startswith("#"):
        desc = first_lines[0].lstrip("#").strip()

    saved = await save_project_skill(
        project_id=project_id,
        skill_id=skill_id,
        name=skill_name,
        content=content_str,
        responsibility=responsibility or "general",
        description=desc,
        active=True,
    )
    return {"ok": True, "skill": saved, "message": f"Skill '{skill_name}' uploaded successfully."}


@router.delete("/api/projects/{project_id}/ai/skills/{skill_id}")
async def delete_project_skill_endpoint(
    project_id: str,
    skill_id: str,
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Delete a custom uploaded skill."""
    from syte.database import delete_project_skill
    deleted = await delete_project_skill(project_id, skill_id)
    return {"ok": deleted, "deleted_id": skill_id}


@router.post("/api/projects/{project_id}/ai/skills/{skill_id}/toggle")
async def toggle_project_skill_endpoint(
    project_id: str,
    skill_id: str,
    body: Dict[str, Any],
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):
    """Toggle a custom skill active/inactive state."""
    from syte.database import get_project_skill, save_project_skill
    existing = await get_project_skill(project_id, skill_id)
    if not existing:
        raise HTTPException(404, "Skill not found")
    active_val = body.get("active", not existing.get("active", True))
    saved = await save_project_skill(
        project_id=project_id,
        skill_id=skill_id,
        name=existing["name"],
        content=existing["content"],
        responsibility=existing.get("responsibility", "general"),
        description=existing.get("description", ""),
        parameters=existing.get("parameters", {}),
        active=bool(active_val),
    )
    return {"ok": True, "skill": saved}


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


# ============================================================================
# Sycord Omni AI Router: Model Catalog, SWE Benchmark, Credits, & Handshake
# ============================================================================

class OmniSelectModelRequest(BaseModel):
    project_id: str = "global"
    model_id: str
    provider: Optional[str] = None


class HandshakeSyncRequest(BaseModel):
    token: Optional[str] = None
    providers: List[Dict[str, Any]] = []
    models: List[Dict[str, Any]] = []


@router.get("/api/ai/omni/models")
async def get_omni_models_catalog(
    search: Optional[str] = "",
    provider: Optional[str] = "",
    tag: Optional[str] = "",
    project_id: Optional[str] = "global",
):
    """Return the Omni AI Router models library with SWE benchmarks, pricing, and active status."""
    models = await list_omni_models(search=search or "", provider=provider or "", tag=tag or "")
    all_models = await list_omni_models()
    top_models = [m for m in all_models if m.get("is_top")][:6]
    if not top_models:
        top_models = sorted(all_models, key=lambda x: x.get("swe_score", 0), reverse=True)[:5]

    current_settings = await get_ai_builder_settings(project_id or "global")
    active_model = current_settings.get("model", "gemini-2.5-flash")
    active_provider = current_settings.get("provider", "vertex")

    # Mark active model in list
    for m in models:
        m["is_active"] = (m["id"] == active_model or (m["id"].endswith(active_model) and m["provider"] in (active_provider, "google" if active_provider == "vertex" else active_provider)))

    credits_data = await get_user_credits("default_user")

    return {
        "ok": True,
        "models": models,
        "top_swe_models": top_models,
        "top_models": top_models,
        "total_count": len(models),
        "active_model": active_model,
        "active_provider": active_provider,
        "credits": credits_data,
        "user_credits": credits_data,
    }


@router.post("/api/ai/omni/select-model")
async def select_omni_model(body: OmniSelectModelRequest):
    """Set active model and provider for the project from the Omni Model Library."""
    target_project_id = body.project_id or "global"
    model_info = await get_omni_model(body.model_id)

    inferred_provider = body.provider
    if not inferred_provider:
        if model_info:
            p = model_info.get("provider", "vertex").lower()
            inferred_provider = "vertex" if p in ("google", "vertex") else p
        else:
            inferred_provider = "openrouter" if ("openrouter" in body.model_id.lower() or ":free" in body.model_id.lower()) else ("vertex" if ("gemini" in body.model_id.lower() or "gemma" in body.model_id.lower()) else "openai")
    elif "openrouter" in body.model_id.lower() or ":free" in body.model_id.lower():
        inferred_provider = "openrouter"
    elif "gemini" in body.model_id.lower() or "gemma" in body.model_id.lower() or inferred_provider in ("google", "vertex"):
        inferred_provider = "vertex"

    current = await get_ai_builder_settings(target_project_id)
    saved_providers = current.get("saved_providers") or []

    # Update or insert into saved_providers
    existing_idx = next((i for i, p in enumerate(saved_providers) if p.get("provider") == inferred_provider and p.get("model") == body.model_id), -1)
    if existing_idx >= 0:
        pass
    else:
        # Check if matching provider entry exists to inherit credentials
        matching = next((p for p in saved_providers if p.get("provider") == inferred_provider), None)
        if not matching and target_project_id != "global":
            global_s = await get_ai_builder_settings("global")
            matching = next((p for p in (global_s.get("saved_providers") or []) if p.get("provider") == inferred_provider), None)

        default_base_url = "https://openrouter.ai/api/v1" if inferred_provider == "openrouter" else ""
        resolved_base_url = matching.get("base_url") if (matching and matching.get("base_url")) else default_base_url
        if inferred_provider in ("google", "vertex") and "api.b.ai" in str(resolved_base_url):
            resolved_base_url = ""

        resolved_api_key = matching.get("api_key") if matching else current.get("api_key", "")
        if inferred_provider in ("google", "vertex", "openrouter") and str(resolved_api_key).startswith("sk-1ea"):
            resolved_api_key = ""

        resolved_gcp_project = matching.get("gcp_project") if (matching and matching.get("gcp_project")) else (current.get("gcp_project") or "gen-lang-client-0678084379")
        resolved_gcp_location = matching.get("gcp_location") if (matching and matching.get("gcp_location")) else (current.get("gcp_location") or "us-central1")

        saved_providers.insert(0, {
            "id": f"omni_{int(time.time())}",
            "name": f"{inferred_provider.upper()} ({body.model_id})",
            "provider": inferred_provider,
            "model": body.model_id,
            "models_list": [body.model_id],
            "api_key": resolved_api_key,
            "base_url": resolved_base_url,
            "gcp_project": resolved_gcp_project,
            "gcp_location": resolved_gcp_location,
        })

    update_payload = {
        "provider": inferred_provider,
        "model": body.model_id,
        "base_url": resolved_base_url if "resolved_base_url" in locals() else "",
        "saved_providers": saved_providers,
    }
    if inferred_provider in ("google", "vertex"):
        update_payload["gcp_project"] = resolved_gcp_project if "resolved_gcp_project" in locals() else "gen-lang-client-0678084379"
        update_payload["gcp_location"] = resolved_gcp_location if "resolved_gcp_location" in locals() else "us-central1"
    if "resolved_api_key" in locals() and resolved_api_key:
        update_payload["api_key"] = resolved_api_key

    updated = await save_ai_builder_settings(target_project_id, update_payload)

    return {
        "ok": True,
        "message": f"Active model switched to {body.model_id} ({inferred_provider})",
        "settings": updated,
        "active_model": body.model_id,
        "model": body.model_id,
        "provider": inferred_provider,
    }


@router.get("/api/ai/user/credits")
async def get_user_credits_endpoint(user_id: str = "default_user"):
    """Get user starting credit ($5.00), current balance, and recent token usage records."""
    credits_data = await get_user_credits(user_id)
    history = await list_ai_usage_records(user_id=user_id, limit=25)
    return {
        "ok": True,
        "credits": credits_data,
        "history": history,
        "records": history,
    }


@router.post("/api/ai/handshake/sync-providers")
async def sync_providers_handshake(body: HandshakeSyncRequest):
    """Secure handshake endpoint to transfer custom & global providers to this VM instance."""
    result = await sync_handshake_providers({
        "providers": body.providers,
        "models": body.models,
    })
    total_custom = len(await list_custom_providers())
    total_models = len(await list_omni_models())
    return {
        "ok": True,
        "message": "Handshake synchronization completed successfully.",
        "synced_providers_count": result.get("imported_providers") or total_custom,
        "synced_models_count": result.get("imported_models") or total_models,
        "timestamp": int(time.time()),
        **result,
    }


@router.get("/api/ai/handshake/status")
async def get_handshake_status():
    """Check connectivity and list synced custom providers on this VM."""
    custom_provs = await list_custom_providers()
    omni_models = await list_omni_models()
    return {
        "ok": True,
        "status": "connected",
        "vm_status": "connected",
        "vm_id": "syte-local-vm",
        "custom_providers_count": len(custom_provs),
        "custom_providers": custom_provs,
        "total_omni_models": len(omni_models),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/api/ai/admin/models")
async def admin_upsert_model(model_data: Dict[str, Any]):
    """Admin endpoint to add or update models and SWE benchmark scores in the catalog."""
    saved = await upsert_omni_model(model_data)
    return {
        "ok": True,
        "message": f"Model {saved.get('id')} saved successfully.",
        "model": saved,
    }



