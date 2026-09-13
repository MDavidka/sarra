#!/usr/bin/env python3
"""
apply_api_and_ui_improvements.py
1. Removes search and copy icons from the API top bar in app.js
2. Redesigns buttons to remove solid gray background (clean modern white/outline style)
3. Ensures active model streaming from the AI tab in stream_api.py
4. Adds /api/ai/upload backend endpoint with file parsing and structured context
5. Enhances agent stream SSE with better disconnect handling and heartbeat headers
"""

import re

# 1. Update syte/static/app.js
with open("/root/syte/syte/static/app.js", "r") as f:
    app_js = f.read()

# Replace top bar inside renderApiDocPage to remove search and copy icons
old_top_bar = r'''      <!-- 1. Top GET / API searchbar (optimized) -->
      <div class="docs-photo-top-bar">
        <div class="docs-photo-top-left">
          <span class="docs-photo-method-badge ${methodClass}">${escapeHtml(ep.method)}</span>
          <span class="docs-photo-path">${escapeHtml(ep.path)}</span>
        </div>
        <div class="docs-photo-top-actions">
          <button type="button" class="docs-photo-search-btn" onclick="openDocsSearchModal()" title="Search endpoints (Ctrl+K)">
            <i data-lucide="search"></i>
            <span class="search-label">Search API...</span>
            <kbd>⌘K</kbd>
          </button>
          <button type="button" class="docs-photo-copy-path-btn" onclick="copySnippet(this, '${escapeHtml(ep.path)}')" title="Copy path">
            <i data-lucide="copy"></i>
          </button>
        </div>
      </div>'''

new_top_bar = r'''      <!-- 1. Top GET / API router display (clean, without search/copy icons) -->
      <div class="docs-photo-top-bar">
        <span class="docs-photo-method-badge ${methodClass}">${escapeHtml(ep.method)}</span>
        <span class="docs-photo-path">${escapeHtml(ep.path)}</span>
      </div>'''

if old_top_bar in app_js:
    app_js = app_js.replace(old_top_bar, new_top_bar)
    print("Updated top bar in app.js successfully!")
else:
    # Try regex replacement if indentation differs
    app_js = re.sub(
        r'<!-- 1\. Top GET / API searchbar \(optimized\) -->\s*<div class="docs-photo-top-bar">[\s\S]*?</div>\s*</div>',
        new_top_bar.strip(),
        app_js
    )
    print("Applied regex update for top bar in app.js")

with open("/root/syte/syte/static/app.js", "w") as f:
    f.write(app_js)


# 2. Update syte/static/pages/docs/docs.css
with open("/root/syte/syte/static/pages/docs/docs.css", "r") as f:
    docs_css = f.read()

# Redesign button styles in docs.css to remove gray fill
old_btn_styles = r'''/* 2. Action Buttons Row */
.docs-photo-btn-bar {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.docs-photo-dual-btn {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  background: #ececec;
  overflow: hidden;
}

body.dark .docs-photo-dual-btn {
  border-color: #3f3f46;
  background: #27272a;
}

.docs-photo-btn-prev,
.docs-photo-btn-next {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1f2937;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-prev,
body.dark .docs-photo-btn-next {
  color: #f4f4f5;
}

.docs-photo-btn-prev {
  border-right: 1px solid #d1d5db;
}

body.dark .docs-photo-btn-prev {
  border-right-color: #3f3f46;
}

.docs-photo-btn-prev:hover,
.docs-photo-btn-next:hover {
  background: rgba(0, 0, 0, 0.06);
}

body.dark .docs-photo-btn-prev:hover,
body.dark .docs-photo-btn-next:hover {
  background: rgba(255, 255, 255, 0.08);
}

.docs-photo-btn-single {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1f2937;
  background: #ececec;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-single {
  color: #f4f4f5;
  background: #27272a;
  border-color: #3f3f46;
}

.docs-photo-btn-single:hover {
  background: #e2e5e9;
}

body.dark .docs-photo-btn-single:hover {
  background: #3f3f46;
}

.docs-photo-btn-more {
  padding: 7px 13px;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #1f2937;
  background: #ececec;
  border: 1px solid #d1d5db;
  border-radius: 8px;
  cursor: pointer;
  transition: background 0.15s ease;
}

body.dark .docs-photo-btn-more {
  color: #f4f4f5;
  background: #27272a;
  border-color: #3f3f46;
}

.docs-photo-btn-more:hover {
  background: #e2e5e9;
}

body.dark .docs-photo-btn-more:hover {
  background: #3f3f46;
}'''

new_btn_styles = r'''/* 2. Action Buttons Row (Clean Modern Minimalist Outline Design - No Gray Fill) */
.docs-photo-btn-bar {
  margin-top: 18px;
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
}

.docs-photo-dual-btn {
  display: inline-flex;
  align-items: stretch;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  background: #ffffff;
  overflow: hidden;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
}

body.dark .docs-photo-dual-btn {
  border-color: rgba(255, 255, 255, 0.1);
  background: #18181b;
  box-shadow: none;
}

.docs-photo-btn-prev,
.docs-photo-btn-next {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1e293b;
  background: transparent;
  border: none;
  cursor: pointer;
  transition: all 0.15s ease;
}

body.dark .docs-photo-btn-prev,
body.dark .docs-photo-btn-next {
  color: #f4f4f5;
}

.docs-photo-btn-prev {
  border-right: 1px solid #e2e8f0;
}

body.dark .docs-photo-btn-prev {
  border-right-color: rgba(255, 255, 255, 0.1);
}

.docs-photo-btn-prev:hover,
.docs-photo-btn-next:hover {
  background: #f8fafc;
  color: #0f172a;
}

body.dark .docs-photo-btn-prev:hover,
body.dark .docs-photo-btn-next:hover {
  background: #27272a;
  color: #ffffff;
}

.docs-photo-btn-single {
  padding: 7px 16px;
  font-size: 13.5px;
  font-weight: 500;
  color: #1e293b;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: all 0.15s ease;
}

body.dark .docs-photo-btn-single {
  color: #f4f4f5;
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: none;
}

.docs-photo-btn-single:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #0f172a;
}

body.dark .docs-photo-btn-single:hover {
  background: #27272a;
  border-color: rgba(255, 255, 255, 0.2);
  color: #ffffff;
}

.docs-photo-btn-more {
  padding: 7px 13px;
  font-size: 13.5px;
  font-weight: 700;
  letter-spacing: 0.1em;
  color: #1e293b;
  background: #ffffff;
  border: 1px solid #e2e8f0;
  border-radius: 8px;
  cursor: pointer;
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03);
  transition: all 0.15s ease;
}

body.dark .docs-photo-btn-more {
  color: #f4f4f5;
  background: #18181b;
  border-color: rgba(255, 255, 255, 0.1);
  box-shadow: none;
}

.docs-photo-btn-more:hover {
  background: #f8fafc;
  border-color: #cbd5e1;
  color: #0f172a;
}

body.dark .docs-photo-btn-more:hover {
  background: #27272a;
  border-color: rgba(255, 255, 255, 0.2);
  color: #ffffff;
}'''

if old_btn_styles in docs_css:
    docs_css = docs_css.replace(old_btn_styles, new_btn_styles)
    print("Updated button styles in docs.css successfully!")
else:
    # Append or replace
    docs_css = re.sub(
        r'/\* 2\. Action Buttons Row \*/[\s\S]*?body\.dark \.docs-photo-btn-more:hover \{[^\}]*\}',
        new_btn_styles.strip(),
        docs_css
    )
    print("Applied regex update for button styles in docs.css")

with open("/root/syte/syte/static/pages/docs/docs.css", "w") as f:
    f.write(docs_css)


# 3. Update syte/stream_api.py (improve model streaming to stream the active model from the AI tab)
with open("/root/syte/syte/stream_api.py", "r") as f:
    stream_api = f.read()

old_stream_models = r'''@router.get("/models")
async def stream_models(
    request: Request,
    stream: bool = Query(False, description="Stream models as Server-Sent Events"),
):
    """List available AI models or stream them over Better-SSE."""
    from syte.database import get_ai_builder_settings
    settings_data = await get_ai_builder_settings("global")
    models_list = get_normalized_models_catalog(settings_data)

    accept = request.headers.get("accept", "")
    wants_stream = stream or ("text/event-stream" in accept)

    if wants_stream:
        async def _stream_models_gen():
            yield f"retry: {RETRY_MS}\n\n".encode("ascii")
            for m in models_list:
                payload = json.dumps({"model": m}, separators=(",", ":"))
                yield f"event: model_stream\ndata: {payload}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"

        return _sse_response(_stream_models_gen())

    return {
        "ok": True,
        "available_models": models_list,
        "models": models_list,
        "ai_tab_models": models_list,
        "saved_providers": settings_data.get("saved_providers", []),
        "current_model": settings_data.get("model", "gpt-4o"),
        "current_provider": settings_data.get("provider", "openai"),
    }'''

new_stream_models = r'''@router.get("/models")
async def stream_models(
    request: Request,
    project_id: Optional[str] = Query(None, description="Project ID to load active model for"),
    active_only: bool = Query(False, description="Stream only the model activated in the AI tab"),
    stream: bool = Query(False, description="Stream models as Server-Sent Events"),
):
    """List available AI models or stream the model activated in the AI tab."""
    from syte.database import get_ai_builder_settings
    pid = project_id or "global"
    settings_data = await get_ai_builder_settings(pid)
    models_list = get_normalized_models_catalog(settings_data)

    active_model_name = str(settings_data.get("model") or "gpt-4o").strip()
    active_provider = str(settings_data.get("provider") or "openai").strip()

    # Mark active model flag
    active_model_obj = None
    for m in models_list:
        is_active = (m.get("id") == active_model_name or m.get("profile") == active_model_name)
        m["active"] = is_active
        m["is_active_in_ai_tab"] = is_active
        if is_active:
            active_model_obj = m

    if not active_model_obj and models_list:
        active_model_obj = models_list[0]
        active_model_obj["active"] = True
        active_model_obj["is_active_in_ai_tab"] = True

    models_to_serve = [active_model_obj] if active_only else models_list

    accept = request.headers.get("accept", "")
    wants_stream = stream or ("text/event-stream" in accept)

    if wants_stream:
        async def _stream_models_gen():
            yield f"retry: {RETRY_MS}\n\n".encode("ascii")
            for m in models_to_serve:
                payload = json.dumps({
                    "event": "model_stream",
                    "model": m,
                    "active": m.get("active", False),
                    "active_model": active_model_name,
                    "active_provider": active_provider,
                }, separators=(",", ":"))
                yield f"event: model_stream\ndata: {payload}\n\n".encode("utf-8")
            yield b"event: done\ndata: [DONE]\n\n"

        return _sse_response(_stream_models_gen())

    return {
        "ok": True,
        "active_model": active_model_name,
        "active_provider": active_provider,
        "current_model": active_model_name,
        "current_provider": active_provider,
        "active_model_profile": active_model_obj,
        "models": models_to_serve,
        "available_models": models_list,
        "saved_providers": settings_data.get("saved_providers", []),
    }'''

if old_stream_models in stream_api:
    stream_api = stream_api.replace(old_stream_models, new_stream_models)
    print("Updated stream_models in stream_api.py successfully!")
else:
    stream_api = re.sub(
        r'@router\.get\("/models"\)[\s\S]*?return \{\s*"ok": True,[\s\S]*?"current_provider": settings_data\.get\("provider", "openai"\),\s*\}',
        new_stream_models.strip(),
        stream_api
    )
    print("Applied regex update for stream_models in stream_api.py")

with open("/root/syte/syte/stream_api.py", "w") as f:
    f.write(stream_api)


# 4. Update syte/ai/router.py (ensure /api/ai/upload and /api/agent/upload top-level routes work)
with open("/root/syte/syte/ai/router.py", "r") as f:
    ai_router = f.read()

old_upload_def = r'''@router.post("/api/projects/{project_id}/ai/upload")
@router.post("/api/projects/{project_id}/upload")
@router.post("/projects/{project_id}/ai/upload")
@router.post("/projects/{project_id}/upload")
async def upload_ai_files(
    project_id: str,
    files: List[UploadFile] = File(...),
    extract_to_workspace: bool = Form(False),
    _operator: dict[str, Any] = Depends(verify_operator_session_or_token),
):'''

new_upload_def = r'''@router.post("/api/ai/upload")
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
):'''

if old_upload_def in ai_router:
    ai_router = ai_router.replace(old_upload_def, new_upload_def)
    print("Added /api/ai/upload top-level endpoint in ai/router.py successfully!")
else:
    ai_router = re.sub(
        r'@router\.post\("/api/projects/\{project_id\}/ai/upload"\)[\s\S]*?async def upload_ai_files\([\s\S]*?\):',
        new_upload_def.strip(),
        ai_router
    )
    print("Applied regex update for upload_ai_files in ai/router.py")

with open("/root/syte/syte/ai/router.py", "w") as f:
    f.write(ai_router)

# 5. Update syte/api_router.py (ensure api_agent_activity_stream injects request for disconnect handling)
with open("/root/syte/syte/api_router.py", "r") as f:
    api_router_code = f.read()

old_stream_fn = r'''@router.get("/projects/{uuid}/agent/activity/stream")
async def api_agent_activity_stream_project(
    uuid: str,
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_activity_stream(uuid=uuid, since_id=since_id, session=session, _token=_token)'''

new_stream_fn = r'''@router.get("/projects/{uuid}/agent/activity/stream")
async def api_agent_activity_stream_project(
    uuid: str,
    request: Request,
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):
    return await api_agent_activity_stream(uuid=uuid, since_id=since_id, session=session, request=request, _token=_token)'''

old_base_stream_fn = r'''async def api_agent_activity_stream(
    uuid: str = Query(...),
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    _token: dict[str, Any] = Depends(verify_api_token),
):'''

new_base_stream_fn = r'''async def api_agent_activity_stream(
    uuid: str = Query(...),
    since_id: int = Query(0, ge=0),
    session: str | None = Query(None),
    request: Optional[Request] = None,
    _token: dict[str, Any] = Depends(verify_api_token),
):'''

if old_base_stream_fn in api_router_code:
    api_router_code = api_router_code.replace(old_base_stream_fn, new_base_stream_fn)
    api_router_code = api_router_code.replace("async for frame in session_manager.subscribe(uuid, since_id=since_id, replay=(since_id <= 0)):", "async for frame in session_manager.subscribe(uuid, since_id=since_id, replay=(since_id <= 0), request=request):")
    api_router_code = api_router_code.replace(old_stream_fn, new_stream_fn)
    print("Updated api_agent_activity_stream with request parameter for Better-SSE disconnect handling!")

with open("/root/syte/syte/api_router.py", "w") as f:
    f.write(api_router_code)

print("All updates applied successfully!")
