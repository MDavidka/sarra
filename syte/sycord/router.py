"""Sycord API routes — /sycord/api/* for external Sycord website integration."""

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel, Field

from syte.auth import verify_api_token
from syte.database import get_project
from syte.domain_utils import build_https_url, normalize_domain
from syte.sycord import service
from syte.sycord.scaffold import STACKS
from syte.sycord.integration_guide import build_backend_integration
from syte.sycord.spec import build_sycord_spec
from syte.upload_limits import UPLOAD_CHUNK_BYTES
from syte import workspace_api
from syte.workspace import workspace_path

router = APIRouter(tags=["Sycord API"])


def _err(status: int, code: str, message: str):
    raise HTTPException(status_code=status, detail={"error": code, "message": message})


class ProjectConnectRequest(BaseModel):
    name: str = Field(..., description="Project name — used for subdomain slug")
    stack: str = Field("nextjs", description="nextjs | python | javascript | html5")
    uuid: str | None = Field(None, description="Optional custom Syte project UUID — save if you provide your own")
    env_vars: dict[str, str] = Field(default_factory=dict)


class UuidBody(BaseModel):
    uuid: str = Field(..., description="Syte project UUID from project_connect")


class DomainBody(BaseModel):
    uuid: str = Field(..., description="Syte project UUID from project_connect")
    domain: str = Field(..., description="Production hostname e.g. myapp.sycord.site")


class AgentChangeBody(BaseModel):
    uuid: str = Field(..., description="Syte project UUID from project_connect")
    message: str = Field(..., description="User change request for the workspace agent")
    model_profile: str | None = Field(None, description="syra-nano | syra-base | syra-havy")
    model_name: str | None = Field(None, description="Alias for model_profile")
    thinking_level: int | None = Field(
        None, ge=1, le=5, description="1 Instant … 5 Max — per-request depth"
    )
    wait: bool = Field(False, description="If true, block until agent completes (legacy sync mode)")
    improve_from_screenshot: bool = Field(
        False, description="Inject latest (or specified) visual_analysis into the agent prompt"
    )
    visual_analysis_id: str | None = Field(
        None, description="Optional visual_analyses document id to use as design critique source"
    )


class DesignProfileBody(BaseModel):
    uuid: str
    theme_key: str | None = Field(None, description="minimal|bold|corporate|vibrant|dark-tech")
    style_key: str | None = Field(
        None, description="saas-minimal|fintech-dark|ai-landing|dashboard|ecommerce-grid"
    )


class VisualAnalyzeBody(BaseModel):
    uuid: str
    screenshot_id: str | None = None
    route: str = "/"
    viewports: list[str] = Field(default_factory=lambda: ["desktop", "phone"])
    capture: bool = Field(True, description="Capture live preview screenshots when true")


class ImproveFromScreenshotBody(BaseModel):
    uuid: str
    message: str = Field(
        "Improve the UI from the latest screenshot analysis. Fix listed issues with minimal diffs."
    )
    visual_analysis_id: str | None = None
    model_profile: str | None = None
    thinking_level: int | None = Field(None, ge=1, le=5)
    wait: bool = False


def _project_record(project: dict) -> dict:
    domain = normalize_domain(project.get("domain") or "")
    return {
        "uuid": project["id"],
        "name": project["name"],
        "domain": domain or None,
        "url": build_https_url(domain) if domain else None,
        "stack": service.project_stack(project),
        "workspace_path": str(workspace_path(project["id"])),
        "app_path": str(workspace_path(project["id"]) / "app"),
        "status": project.get("status"),
        "port": project.get("port"),
        "created_at": project.get("created_at"),
        "updated_at": project.get("updated_at"),
    }


def _persist_block(project_id: str) -> dict:
    return {
        "save_uuid": True,
        "uuid": project_id,
        "instruction": (
            "Save uuid in your application database before any other Sycord API call. "
            "Required for upload, issue_deployment, container_get, and domain."
        ),
        "endpoints_using_uuid": [
            "POST /sycord/api/upload — form field uuid",
            "POST /sycord/api/issue_deployment — JSON body.uuid",
            "GET /sycord/api/container_get?uuid=",
            "POST /sycord/api/domain — JSON body.uuid",
            "POST /sycord/api/preview_start — JSON body.uuid",
            "GET /sycord/api/preview_status?uuid=",
            "POST /sycord/api/preview_stop — JSON body.uuid",
            "GET /sycord/api/agent_status?uuid=",
            "POST /sycord/api/agent_change — JSON body.uuid + message",
            "GET /sycord/api/agent_activity?uuid=&since_id=",
            "GET /sycord/api/agent_sessions?uuid= — list durable Turso session ids",
            "GET /sycord/api/agent_session/{session_id} — fetch one durable session",
            "GET /sycord/api/agent_turso_sync?uuid= — all-messages-saved status (brain indicator)",
        ],
    }


def _project_urls(project: dict) -> dict:
    return _project_record(project)


@router.get("/spec.json", include_in_schema=False)
async def sycord_spec():
    return build_sycord_spec()


@router.get("/integration.json", include_in_schema=False)
async def sycord_integration(request: Request):
    """Step-by-step backend integration: what to call, what JSON you get, what to save."""
    base = str(request.base_url).rstrip("/")
    return build_backend_integration(base)


@router.post("/project_connect")
async def api_project_connect(
    body: ProjectConnectRequest,
    _token: dict = Depends(verify_api_token),
):
    """
    Connect a Sycord project to Syte: create workspace, scaffold stack, assign subdomain.
    Example subdomain: testproject.sycord.site
    """
    if body.stack.lower() not in STACKS:
        _err(400, "invalid_stack", f"stack must be one of: {', '.join(STACKS)}")
    project, message = await service.project_connect(
        body.name,
        stack=body.stack,
        env_vars=body.env_vars,
        project_uuid=body.uuid,
    )
    if not project:
        _err(400, "connect_failed", message)
    base_zone = await service.resolve_base_zone()
    record = _project_record(project)
    project_id = record["uuid"]
    return {
        "ok": True,
        "uuid": project_id,
        "message": message,
        "persist": _persist_block(project_id),
        "project": record,
        "subdomain_pattern": f"{{slug}}.{base_zone}",
        "next_steps": {
            "save_uuid": project_id,
            "upload": "POST /sycord/api/upload",
            "preview": f"POST /sycord/api/preview_start — body {{\"uuid\": \"{project_id}\"}}",
            "deploy": "POST /sycord/api/issue_deployment",
            "container": f"GET /sycord/api/container_get?uuid={project_id}",
            "agent": f"POST /sycord/api/agent_change — body {{\"uuid\": \"{project_id}\", \"message\": \"…\"}}",
            "agent_sessions": f"GET /sycord/api/agent_sessions?uuid={project_id}",
        },
    }


@router.get("/agent_status")
async def api_agent_status(
    request: Request,
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Continuous workspace agent status — hot cn serve per project."""
    base = str(request.base_url).rstrip("/")
    payload = await service.agent_status(uuid, request_base=base)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.get("/agent_activity")
async def api_agent_activity(
    uuid: str = Query(..., description="Project UUID"),
    since_id: int = Query(0, description="Last event id for incremental fetch"),
    limit: int = Query(200, ge=1, le=2000),
    session: str = Query(
        "",
        description="Optional: last | session number — return only that chat session",
    ),
    _token: dict = Depends(verify_api_token),
):
    """Agent activity snapshot — for durable per-turn records use agent_sessions."""
    payload = await service.agent_activity(
        uuid, since_id=since_id, limit=limit, session=session or None,
    )
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.get("/agent_turso_sync")
async def api_agent_turso_sync(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Aggregate 'all messages saved to Turso' status for the brain indicator."""
    payload = await service.agent_turso_sync(uuid)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.get("/agent_turso_debug")
async def api_agent_turso_debug(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Diagnose why the brain indicator is red — live Turso connectivity + schema check."""
    payload = await service.agent_turso_debug(uuid)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.get("/agent_session/{session_id}")
async def api_agent_session(
    session_id: str,
    since_id: int = Query(0, ge=0),
    uuid: str | None = None,
    project_id: str | None = None,
    _token: dict = Depends(verify_api_token),
):
    """Turso access route — fetch a durable agent activity session by UUID.

    API tokens are host-global in this single-tenant service; pass ``uuid`` or
    ``project_id`` to additionally verify the session belongs to that project.
    """
    payload = await service.agent_session(session_id, since_id=since_id)
    if not payload:
        _err(404, "not_found", "Agent session not found (or Turso is not configured)")
    expected_project_id = project_id or uuid
    if expected_project_id and str(payload.get("project_id") or "") != expected_project_id:
        _err(403, "forbidden", "Agent session does not belong to the requested project")
    return {"ok": True, **payload}


@router.get("/agent_sessions")
async def api_agent_sessions(
    uuid: str = Query(..., description="Project UUID"),
    limit: int = Query(50, ge=1, le=500),
    resume: int = Query(0, ge=0, le=1, description="When 1, emphasize resume_session + memory"),
    _token: dict = Depends(verify_api_token),
):
    """List durable Turso sessions plus layered memory (summary, active files, design)."""
    payload = await service.agent_sessions(uuid, limit=limit)
    if not payload:
        _err(404, "not_found", "Project not found")
    if resume:
        payload = {
            **payload,
            "resume": 1,
            "hint": (
                "Reuse resume_session.session_url for follow-up polls; prefer continuing "
                "the open turn over starting a cold session when possible."
            ),
        }
    return {"ok": True, **payload}


@router.get("/project_summary")
async def api_project_summary(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Read-only external summary: meta, deployment URL, design tokens, last agent summary."""
    payload = await service.project_summary(uuid)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.get("/agent_activity/stream")
async def api_agent_activity_stream(
    request: Request,
    uuid: str = Query(..., description="Project UUID"),
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict = Depends(verify_api_token),
):
    """Optional SSE activity stream (token deltas + tool events) alongside Turso polling."""
    from fastapi.responses import StreamingResponse

    from syte.agent_activity import activity_sse_generator

    project = await get_project(uuid)
    if not project:
        _err(404, "not_found", "Project not found")

    async def _gen():
        async for frame in activity_sse_generator(uuid, since_id=since_id, session=session):
            if await request.is_disconnected():
                break
            yield frame

    return StreamingResponse(
        _gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/visual_analyses")
async def api_list_visual_analyses(
    uuid: str = Query(...),
    limit: int = Query(20, ge=1, le=100),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_memory import list_visual_analyses

    if not await get_project(uuid):
        _err(404, "not_found", "Project not found")
    return {"ok": True, "uuid": uuid, "analyses": await list_visual_analyses(uuid, limit=limit)}


@router.post("/visual_analyze")
async def api_visual_analyze(body: VisualAnalyzeBody, _token: dict = Depends(verify_api_token)):
    """Capture preview screenshots (optional) and store structured visual_analyses."""
    from syte.cloud_agent import selected_model_metadata
    from syte.visual_analysis import analyze_and_store

    project = await get_project(body.uuid)
    if not project:
        _err(404, "not_found", "Project not found")

    analyses: list[dict] = []
    if body.capture:
        from syte.cloud_agent import _tool_screenshot_preview

        model = await selected_model_metadata(project)
        result = await _tool_screenshot_preview(
            body.uuid,
            {"route": body.route, "viewports": body.viewports},
            {"session_number": 0, "model": model},
        )
        for shot in result.get("screenshots") or []:
            if shot.get("visual_analysis_id"):
                from syte.agent_memory import get_visual_analysis

                row = await get_visual_analysis(str(shot["visual_analysis_id"]))
                if row:
                    analyses.append(row)
        if not analyses and not result.get("ok"):
            _err(400, "capture_failed", result.get("message") or "Screenshot capture failed")
    elif body.screenshot_id:
        import base64
        from pathlib import Path

        from syte.agent_artifacts import get_screenshot

        record = await get_screenshot(body.uuid, body.screenshot_id)
        if not record:
            _err(404, "not_found", "Screenshot not found")
        image_b64 = ""
        path = Path(str(record.get("path") or ""))
        if path.is_file():
            image_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
        model = await selected_model_metadata(project)
        analysis = await analyze_and_store(
            body.uuid,
            screenshot_id=body.screenshot_id,
            image_base64=image_b64,
            viewport=str(record.get("viewport") or "desktop"),
            width=int(record.get("width") or 0),
            height=int(record.get("height") or 0),
            route=str(record.get("route") or body.route),
            screenshot_url=f"/api/projects/{body.uuid}/agent/screenshots/{body.screenshot_id}",
            model=model,
        )
        analyses.append(analysis)
    else:
        _err(400, "invalid_request", "Provide capture=true or screenshot_id")

    return {"ok": True, "uuid": body.uuid, "analyses": analyses}


@router.post("/improve_from_screenshot")
async def api_improve_from_screenshot(
    body: ImproveFromScreenshotBody, _token: dict = Depends(verify_api_token),
):
    result = await service.agent_change(
        body.uuid,
        body.message,
        model_profile=body.model_profile,
        thinking_level=body.thinking_level,
        wait=body.wait,
        improve_from_screenshot=True,
        visual_analysis_id=body.visual_analysis_id,
    )
    if not result.get("ok"):
        _err(400, result.get("error") or "improve_failed", result.get("message") or "Failed")
    return {"ok": True, **result}


@router.get("/design_profile")
async def api_get_design_profile(
    uuid: str = Query(...),
    _token: dict = Depends(verify_api_token),
):
    from syte.agent_memory import get_design_profile
    from syte.design_profile import list_style_profiles

    if not await get_project(uuid):
        _err(404, "not_found", "Project not found")
    profile = await get_design_profile(uuid)
    return {
        "ok": True,
        "uuid": uuid,
        "profile": profile,
        "style_profiles": list_style_profiles(),
    }


@router.post("/design_profile")
async def api_set_design_profile(body: DesignProfileBody, _token: dict = Depends(verify_api_token)):
    from syte.design_profile import apply_theme_profile

    if not await get_project(body.uuid):
        _err(404, "not_found", "Project not found")
    profile = await apply_theme_profile(
        body.uuid,
        theme_key=body.theme_key,
        style_key=body.style_key,
        source="api",
    )
    return {"ok": True, "uuid": body.uuid, "profile": profile}


@router.post("/agent_change")
async def api_agent_change(body: AgentChangeBody, _token: dict = Depends(verify_api_token)):
    """
    Request a code change via the workspace agent.
    Returns immediately with request_id — subscribe to activity stream for live updates.
    """
    profile = body.model_profile or body.model_name
    result = await service.agent_change(
        body.uuid,
        body.message,
        model_profile=profile,
        thinking_level=body.thinking_level,
        wait=body.wait,
        improve_from_screenshot=body.improve_from_screenshot,
        visual_analysis_id=body.visual_analysis_id,
    )
    if not result.get("ok"):
        _err(400, result.get("error") or "agent_change_failed", result.get("message") or "Change request failed")
    return {
        **result,
        "uuid": body.uuid,
        "change_applied": bool(result.get("reply")) if body.wait else None,
    }


@router.get("/container_get")
async def api_container_get(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Docker container status and URLs for a connected project."""
    payload = await service.container_get_async(uuid)
    if not payload:
        _err(404, "not_found", "Project not found")
    return {"ok": True, **payload}


@router.post("/upload")
async def api_upload(
    uuid: str = Form(...),
    path: str = Form(..., description="Relative path under workspace, e.g. app/src/page.tsx"),
    file: UploadFile = File(...),
    _token: dict = Depends(verify_api_token),
):
    """Upload a file into the project workspace (multipart)."""
    async def chunks():
        while True:
            chunk = await file.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            yield chunk

    try:
        ok, message, written = await workspace_api.upload_file_stream(uuid, path, chunks())
    except workspace_api.UploadTooLargeError as exc:
        _err(413, "upload_too_large", str(exc))
    except ValueError as exc:
        _err(400, "invalid_path", str(exc))
    if not ok:
        _err(400, "upload_failed", message)
    return {"ok": True, "uuid": uuid, "path": path, "bytes": written, "message": message}


@router.post("/domain")
async def api_domain(body: DomainBody, _token: dict = Depends(verify_api_token)):
    """Set or update production HTTPS domain (Caddy auto TLS)."""
    project, message = await service.set_domain(body.uuid, body.domain)
    if not project:
        _err(404, "not_found", message)
    return {
        "ok": True,
        "message": message,
        "uuid": body.uuid,
        "project": _project_record(project),
    }


@router.post("/issue_deployment")
async def api_issue_deployment(body: UuidBody, _token: dict = Depends(verify_api_token)):
    """Build and deploy project (docker build + container start)."""
    project, message = await service.issue_deployment(body.uuid)
    if not project:
        _err(404, "not_found", message)
    return {
        "ok": True,
        "message": message,
        "uuid": body.uuid,
        "stream_url": f"/api/projects/{body.uuid}/logs/stream?live=1",
        "status": project.get("status"),
    }


@router.post("/preview_start")
async def api_preview_start(body: UuidBody, _token: dict = Depends(verify_api_token)):
    """Start fast dev preview (next dev / vite) with HMR — seconds, not minutes."""
    ok, message, meta = await service.preview_start(body.uuid)
    if not ok:
        _err(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}


@router.get("/preview_status")
async def api_preview_status(
    uuid: str = Query(..., description="Project UUID"),
    _token: dict = Depends(verify_api_token),
):
    """Preview dev server status — poll until preview_ready=true."""
    meta, message = await service.preview_status(uuid)
    if not meta:
        _err(404, "not_found", message)
    return {"ok": True, "uuid": uuid, **meta}


@router.post("/preview_stop")
async def api_preview_stop(body: UuidBody, _token: dict = Depends(verify_api_token)):
    """Stop preview dev server."""
    ok, message, meta = await service.preview_stop(body.uuid)
    if not ok:
        _err(400, "preview_failed", message)
    return {"ok": True, "uuid": body.uuid, "message": message, **meta}
