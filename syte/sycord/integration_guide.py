"""Backend integration guide — how your Sycord server uses each Sycord API call."""


def build_backend_integration(base_url: str = "") -> dict:
    base = base_url.rstrip("/") or "https://sycord.site"
    api = f"{base}/sycord/api"
    return {
        "title": "Sycord backend integration guide",
        "audience": "Developers building sycord.site or any app that talks to Syte deployer",
        "content_type": "application/json for all POST bodies; multipart for upload",
        "prerequisites": {
            "syte_api_key": "Create in Syte GUI → Users → Create token, or POST /api/tokens",
            "env_var": "SYTE_API_KEY=syte_xxxxxxxx (server-side only, never expose to browser)",
            "dns": "Wildcard *.sycord.site → Syte server IP (for auto subdomains)",
        },
        "your_database": {
            "description": "Minimum columns to add to your projects table",
            "schema_example": {
                "id": "uuid — your internal primary key",
                "user_id": "who owns the project",
                "name": "display name from your UI",
                "stack": "nextjs | python | javascript | html5",
                "syte_uuid": "STRING — from Syte project_connect response.uuid (REQUIRED)",
                "syte_domain": "STRING — e.g. myapp.sycord.site",
                "syte_url": "STRING — https://myapp.sycord.site",
                "deploy_status": "created | deploying | running | stopped — mirror Syte status",
            },
        },
        "steps": [
            _step_connect(api),
            _step_upload(api),
            _step_preview_start(api),
            _step_preview_poll(api),
            _step_deploy(api),
            _step_container_poll(api),
            _step_domain(api),
        ],
        "quick_reference": [
            {
                "when": "User creates a project in your app",
                "call": f"POST {api}/project_connect",
                "you_send": '{"name":"…","stack":"nextjs"}',
                "you_save": "response.uuid → syte_uuid column",
                "you_show_user": "response.project.url as preview/deploy link",
            },
            {
                "when": "User uploads or you push generated files",
                "call": f"POST {api}/upload",
                "you_send": "multipart: uuid, path, file",
                "you_save": "nothing (optional log bytes)",
                "you_show_user": "upload success toast",
            },
            {
                "when": "User wants live dev preview (fast, HMR)",
                "call": f"POST {api}/preview_start",
                "you_send": '{"uuid":"<syte_uuid>"}',
                "you_save": "preview_url from response (optional cache)",
                "you_show_user": "preview_url in iframe or new tab; poll preview_status",
            },
            {
                "when": "Polling preview (every 1–2s)",
                "call": f"GET {api}/preview_status?uuid=<syte_uuid>",
                "you_send": "query param uuid only",
                "you_save": "preview_url when preview_ready=true",
                "you_show_user": "Open preview_url when ready",
            },
            {
                "when": "User clicks Deploy",
                "call": f"POST {api}/issue_deployment",
                "you_send": '{"uuid":"<syte_uuid>"}',
                "you_save": "deploy_status=deploying",
                "you_show_user": "deploying spinner; poll container_get",
            },
            {
                "when": "Polling after deploy (every 3–5s)",
                "call": f"GET {api}/container_get?uuid=<syte_uuid>",
                "you_send": "query param uuid only",
                "you_save": "deploy_status from response.status; syte_url from response.url",
                "you_show_user": "Open response.url when running=true",
            },
            {
                "when": "User sets custom domain",
                "call": f"POST {api}/domain",
                "you_send": '{"uuid":"…","domain":"custom.sycord.site"}',
                "you_save": "syte_domain, syte_url from response.project",
                "you_show_user": "updated HTTPS link",
            },
            {
                "when": "User stops dev preview",
                "call": f"POST {api}/preview_stop",
                "you_send": '{"uuid":"<syte_uuid>"}',
                "you_save": "clear cached preview_url",
                "you_show_user": "preview stopped state",
            },
        ],
    }


def _step_connect(api: str) -> dict:
    return {
        "step": 1,
        "name": "Connect project to Syte",
        "endpoint": f"POST {api}/project_connect",
        "when_to_call": "User creates a new project in your Sycord app (or first time linking to deployer).",
        "request": {
            "method": "POST",
            "headers": {
                "Content-Type": "application/json",
                "X-API-Key": "syte_YOUR_TOKEN",
            },
            "body_json": {
                "name": "myapp",
                "stack": "nextjs",
                "uuid": None,
                "env_vars": {},
            },
            "body_fields": {
                "name": {"type": "string", "required": True, "description": "Project name; becomes subdomain slug"},
                "stack": {"type": "string", "required": False, "default": "nextjs", "enum": ["nextjs", "python", "javascript", "html5"]},
                "uuid": {"type": "string", "required": False, "description": "Your own Syte id if you need a fixed mapping"},
                "env_vars": {"type": "object", "required": False, "description": "Extra env vars for container"},
            },
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "message": "Empty project myapp-a1b2c3 created. Scaffolded…",
                "persist": {"save_uuid": True, "uuid": "myapp-a1b2c3"},
                "project": {
                    "uuid": "myapp-a1b2c3",
                    "name": "myapp",
                    "domain": "myapp.sycord.site",
                    "url": "https://myapp.sycord.site",
                    "stack": "nextjs",
                    "status": "created",
                    "port": 3010,
                },
            },
            "fields_to_save": {
                "uuid": {
                    "required": True,
                    "your_column": "syte_uuid",
                    "description": "Primary link to Syte — use in every future API call",
                },
                "project.domain": {
                    "required": False,
                    "your_column": "syte_domain",
                    "description": "Auto-assigned hostname",
                },
                "project.url": {
                    "required": False,
                    "your_column": "syte_url",
                    "description": "HTTPS URL to show user once deployed",
                },
                "project.status": {
                    "required": False,
                    "your_column": "deploy_status",
                    "description": "Usually 'created' until first deploy",
                },
            },
        },
        "backend_pseudocode": (
            "async function onUserCreatesProject(name, stack) {\n"
            "  const res = await fetch(SYTE_URL + '/sycord/api/project_connect', {\n"
            "    method: 'POST',\n"
            "    headers: { 'Content-Type': 'application/json', 'X-API-Key': SYTE_API_KEY },\n"
            "    body: JSON.stringify({ name, stack }),\n"
            "  });\n"
            "  const data = await res.json();\n"
            "  if (!data.ok) throw new Error(data.detail?.message);\n"
            "  await db.insert({ name, syte_uuid: data.uuid, syte_domain: data.project.domain,\n"
            "    syte_url: data.project.url, deploy_status: data.project.status });\n"
            "  return { internalId: row.id, syteUuid: data.uuid, url: data.project.url };\n"
            "}"
        ),
    }


def _step_upload(api: str) -> dict:
    return {
        "step": 2,
        "name": "Upload files (optional)",
        "endpoint": f"POST {api}/upload",
        "when_to_call": "After connect, if you need to push files beyond the scaffold (custom code, assets). Skip if scaffold is enough.",
        "request": {
            "method": "POST",
            "headers": {"X-API-Key": "syte_YOUR_TOKEN"},
            "content_type": "multipart/form-data",
            "fields": {
                "uuid": {"type": "string", "required": True, "source": "your syte_uuid column"},
                "path": {"type": "string", "required": True, "example": "app/app/page.tsx", "note": "Relative to Syte workspace root"},
                "file": {"type": "binary", "required": True},
            },
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "path": "app/app/page.tsx",
                "bytes": 2048,
                "message": "Uploaded 2048 bytes to app/app/page.tsx",
            },
            "fields_to_use": {
                "ok": "true if upload succeeded",
                "bytes": "optional — log or show in UI",
            },
        },
        "backend_pseudocode": (
            "const form = new FormData();\n"
            "form.append('uuid', project.syte_uuid);\n"
            "form.append('path', 'app/app/page.tsx');\n"
            "form.append('file', fileBlob);\n"
            "await fetch(SYTE_URL + '/sycord/api/upload', { method: 'POST', headers: { 'X-API-Key': KEY }, body: form });"
        ),
    }


def _step_preview_start(api: str) -> dict:
    return {
        "step": 3,
        "name": "Start dev preview (fast HMR)",
        "endpoint": f"POST {api}/preview_start",
        "when_to_call": (
            "After connect (and optional upload), when user wants a live dev preview "
            "without a full Docker deploy — typically ~5 seconds for nextjs/vite."
        ),
        "request": {
            "method": "POST",
            "headers": {"Content-Type": "application/json", "X-API-Key": "syte_YOUR_TOKEN"},
            "body_json": {"uuid": "myapp-a1b2c3"},
            "body_fields": {
                "uuid": {"type": "string", "required": True, "source": "your syte_uuid column"},
            },
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "message": "Preview on https://previewk-myapp.sycord.site — ready (HMR live)",
                "preview_url": "https://previewk-myapp.sycord.site",
                "preview_domain": "previewk-myapp.sycord.site",
                "preview_ready": True,
                "preview_running": True,
                "preview_port": 4001,
                "preview_status": "running",
                "preview_stream_url": "/api/projects/myapp-a1b2c3/preview/logs/stream?live=1",
            },
            "fields_to_use": {
                "preview_url": "Show to user — HTTPS preview link (wildcard *.sycord.site)",
                "preview_ready": "true when dev server is accepting connections",
                "preview_running": "true while preview process is alive",
                "preview_stream_url": "Optional — append to SYTE_URL for live preview logs (SSE)",
            },
            "iframe_embedding": {
                "note": (
                    "Embed preview_url in an iframe on any site (sycord.com, your app, etc.). "
                    "Syte sets frame-ancestors * on preview domains by default."
                ),
                "example_html": '<iframe src="{preview_url}" style="width:100%;height:100%;border:0" referrerpolicy="no-referrer-when-downgrade"></iframe>',
                "avoid": "Do not use sandbox without allow-scripts — page will stay blank",
                "setting": "preview_embed_mode=restricted in Syte settings limits embed to sycord.com + GUI domain only",
            },
        },
        "backend_pseudocode": (
            "const res = await postJson('/sycord/api/preview_start', { uuid: project.syte_uuid });\n"
            "startPollingPreview(project.syte_uuid, res.preview_url);"
        ),
    }


def _step_preview_poll(api: str) -> dict:
    return {
        "step": 4,
        "name": "Poll preview status",
        "endpoint": f"GET {api}/preview_status?uuid=<syte_uuid>",
        "when_to_call": "Every 1–2 seconds after preview_start until preview_ready=true.",
        "request": {
            "method": "GET",
            "headers": {"X-API-Key": "syte_YOUR_TOKEN"},
            "query": {"uuid": "myapp-a1b2c3"},
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "preview_url": "https://previewk-myapp.sycord.site",
                "preview_ready": True,
                "preview_running": True,
                "preview_status": "running",
            },
            "fields_to_use": {
                "preview_ready": "true → embed or link preview_url for user",
                "preview_status": "starting | running | stopped",
            },
        },
        "backend_pseudocode": (
            "const st = await getJson('/sycord/api/preview_status?uuid=' + syte_uuid);\n"
            "if (st.preview_ready) showPreview(st.preview_url);\n"
            "else if (st.preview_running) { /* keep polling */ }"
        ),
    }


def _step_deploy(api: str) -> dict:
    return {
        "step": 5,
        "name": "Start deployment",
        "endpoint": f"POST {api}/issue_deployment",
        "when_to_call": "User clicks Deploy in your app, or after file uploads are complete.",
        "request": {
            "method": "POST",
            "headers": {"Content-Type": "application/json", "X-API-Key": "syte_YOUR_TOKEN"},
            "body_json": {"uuid": "myapp-a1b2c3"},
            "body_fields": {
                "uuid": {"type": "string", "required": True, "source": "your syte_uuid column"},
            },
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "message": "Deploy issued for myapp-a1b2c3. Stream logs: GET /api/projects/…/logs/stream",
                "stream_url": "/api/projects/myapp-a1b2c3/logs/stream?live=1",
                "status": "deploying",
            },
            "fields_to_use": {
                "status": "Set deploy_status='deploying' in your DB",
                "stream_url": "Optional — append to SYTE_URL for live build logs (SSE)",
            },
        },
        "backend_pseudocode": (
            "const res = await postJson('/sycord/api/issue_deployment', { uuid: project.syte_uuid });\n"
            "await db.update(project.id, { deploy_status: res.status });\n"
            "startPollingContainer(project.syte_uuid);"
        ),
    }


def _step_container_poll(api: str) -> dict:
    return {
        "step": 6,
        "name": "Poll container status",
        "endpoint": f"GET {api}/container_get?uuid=<syte_uuid>",
        "when_to_call": "Every 3–5 seconds after issue_deployment until running=true or failed.",
        "request": {
            "method": "GET",
            "headers": {"X-API-Key": "syte_YOUR_TOKEN"},
            "query": {"uuid": "myapp-a1b2c3"},
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "container_name": "syte-myapp-a1b2c3",
                "exists": True,
                "running": True,
                "state": "running",
                "url": "https://myapp.sycord.site",
                "domain": "myapp.sycord.site",
                "host_port": 3010,
                "status": "running",
            },
            "fields_to_use": {
                "running": "true → site is live; show url to user",
                "url": "Update syte_url; use as href in your dashboard",
                "status": "Mirror to deploy_status (deploying | running | stopped)",
                "state": "Docker state string for debugging",
            },
        },
        "backend_pseudocode": (
            "const st = await getJson('/sycord/api/container_get?uuid=' + syte_uuid);\n"
            "if (st.running) {\n"
            "  await db.update(id, { deploy_status: 'running', syte_url: st.url });\n"
            "  notifyUser('Live at ' + st.url);\n"
            "} else if (st.status === 'deploying') { /* keep polling */ }"
        ),
    }


def _step_domain(api: str) -> dict:
    return {
        "step": 7,
        "name": "Custom domain (optional)",
        "endpoint": f"POST {api}/domain",
        "when_to_call": "User configures a custom hostname instead of the auto subdomain.",
        "request": {
            "method": "POST",
            "headers": {"Content-Type": "application/json", "X-API-Key": "syte_YOUR_TOKEN"},
            "body_json": {"uuid": "myapp-a1b2c3", "domain": "shop.example.com"},
        },
        "response": {
            "content_type": "application/json",
            "example": {
                "ok": True,
                "uuid": "myapp-a1b2c3",
                "message": "Domain set to shop.example.com…",
                "project": {
                    "uuid": "myapp-a1b2c3",
                    "domain": "shop.example.com",
                    "url": "https://shop.example.com",
                },
            },
            "fields_to_use": {
                "project.domain": "Update syte_domain",
                "project.url": "Update syte_url shown in UI",
            },
        },
    }
