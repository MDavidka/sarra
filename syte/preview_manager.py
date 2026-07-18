"""Fast dev preview servers (next dev / vite) with HMR — separate from production deploy."""

import asyncio
import json
import os
import signal
import socket
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from syte.config import settings
from syte.database import get_project, list_projects, update_project
from syte.domain_utils import normalize_domain
from syte.preview_config import build_preview_command, prepare_preview_hosts
from syte.preview_domains import (
    build_preview_urls,
    is_preview_hostname,
    preview_dns_hint,
    resolve_preview_domain,
)
from syte.nextjs_layout import is_nextjs_repo
from syte.workspace import command_exists, ensure_workspace, read_env_vars, workspace_path

PREVIEW_PORT_START = 4000
PREVIEW_PORT_END = 4999
PREVIEW_START_GRACE_SEC = 45
PREVIEW_MAX_RUNTIME_SEC = 3600
PREVIEW_NODE_MEMORY_MB = 4096
PREVIEW_THREADPOOL_SIZE = 16
PREVIEW_NICE_LEVEL = -5
PID_DIR = settings.data_dir / "pids"


def preview_pid_file(project_id: str) -> Path:
    PID_DIR.mkdir(parents=True, exist_ok=True)
    return PID_DIR / f"{project_id}.preview.pid"


def preview_log_path(project_id: str) -> Path:
    return workspace_path(project_id) / "preview.log"


def preview_process_env(project: dict, preview_port: int) -> dict[str, str]:
    """Give the isolated dev server enough CPU and memory for framework tooling."""
    env = {**os.environ, **read_env_vars(project.get("env_vars", "{}"))}
    node_options = env.get("NODE_OPTIONS", "").strip()
    if "--max-old-space-size" not in node_options:
        node_options = f"{node_options} --max-old-space-size={PREVIEW_NODE_MEMORY_MB}".strip()
    env.update({
        "PORT": str(preview_port),
        "SYTE_PREVIEW_PORT": str(preview_port),
        "HOSTNAME": "0.0.0.0",
        "NODE_ENV": "development",
        "NEXT_TELEMETRY_DISABLED": "1",
        "NODE_OPTIONS": node_options,
        "UV_THREADPOOL_SIZE": str(PREVIEW_THREADPOOL_SIZE),
        "SYTE_PREVIEW_RESOURCE_PROFILE": "expanded",
    })
    return env


def configure_preview_process() -> None:
    os.setsid()
    try:
        os.nice(PREVIEW_NICE_LEVEL)
    except OSError:
        pass


async def next_preview_port() -> int:
    projects = await list_projects()
    used = {p.get("preview_port") for p in projects if p.get("preview_port")}
    for port in range(PREVIEW_PORT_START, PREVIEW_PORT_END + 1):
        if port not in used:
            return port
    raise RuntimeError("No preview ports available (4000-4999 exhausted)")


def _port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.25)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _node_modules_ready(repo: Path) -> bool:
    """True when npm dependencies are installed enough to run dev scripts."""
    if not (repo / "package.json").exists():
        return True
    node_modules = repo / "node_modules"
    if not node_modules.is_dir():
        return False
    bin_dir = node_modules / ".bin"
    if bin_dir.is_dir():
        for name in ("vite", "next", "react-scripts", "webpack", "nuxt"):
            if (bin_dir / name).exists():
                return True
    try:
        return any(node_modules.iterdir())
    except OSError:
        return False


def ensure_preview_deps(repo: Path, log_path: Path) -> tuple[bool, str]:
    """Run npm install when package.json exists but node_modules is missing."""
    if not (repo / "package.json").exists():
        return True, "no package.json"
    if _node_modules_ready(repo):
        return True, "dependencies already installed"

    with log_path.open("a") as log_file:
        log_file.write("Running npm install (preview requires node_modules)…\n")

    from syte.workspace import run_cmd

    code, output = run_cmd(
        ["npm", "install", "--no-fund", "--no-audit"],
        cwd=repo,
    )
    with log_path.open("a") as log_file:
        if output:
            log_file.write(output + "\n")
        log_file.write(f"npm install exited {code}\n")

    if code != 0:
        tail = (output or "")[-2000:]
        return False, f"npm install failed (exit {code}).\n{tail}"

    if not _node_modules_ready(repo):
        return False, "npm install finished but node_modules is still missing."

    return True, "npm install completed"


def detect_dev_command(repo: Path) -> str | None:
    pkg = repo / "package.json"
    if not pkg.exists():
        return None
    try:
        data = json.loads(pkg.read_text())
    except (json.JSONDecodeError, OSError):
        return None

    scripts = data.get("scripts", {})
    if "dev" in scripts:
        script = scripts["dev"].lower()
        if "vite" in script:
            overlay = repo / "vite.config.syte.mjs"
            if overlay.exists():
                return "npx vite --config vite.config.syte.mjs --host 0.0.0.0 --port $SYTE_PREVIEW_PORT"
            return "npx vite --host 0.0.0.0 --port $SYTE_PREVIEW_PORT"
        if "next" in script:
            return "npm run dev -- --hostname 0.0.0.0 --port $SYTE_PREVIEW_PORT"
        return "npm run dev -- --port $SYTE_PREVIEW_PORT"

    if is_nextjs_repo(repo):
        return "npx next dev --hostname 0.0.0.0 --port $SYTE_PREVIEW_PORT"

    deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
    if "vite" in deps:
        return "npx vite --host 0.0.0.0 --port $SYTE_PREVIEW_PORT"

    return None


def is_preview_running(project_id: str) -> bool:
    pf = preview_pid_file(project_id)
    if not pf.exists():
        return False
    try:
        pid = int(pf.read_text().strip())
        os.kill(pid, 0)
        return True
    except (OSError, ValueError):
        pf.unlink(missing_ok=True)
        return False


def stop_preview(project_id: str) -> tuple[bool, str]:
    pf = preview_pid_file(project_id)
    if not pf.exists():
        return True, "Preview not running."
    try:
        pid = int(pf.read_text().strip())
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (OSError, ValueError):
        pass
    pf.unlink(missing_ok=True)
    return True, "Preview stopped."


async def ensure_preview_address(project: dict) -> dict:
    """Assign and persist a stable preview domain + port (once per project)."""
    project_id = project["id"]
    updates: dict = {}
    domain = normalize_domain(project.get("preview_domain") or "")
    if not is_preview_hostname(domain):
        domain = await resolve_preview_domain(project)
        if domain:
            updates["preview_domain"] = domain
    if not project.get("preview_port"):
        updates["preview_port"] = await next_preview_port()
    if updates:
        await update_project(project_id, updates)
        project = await get_project(project_id) or {**project, **updates}
    return project


async def stop_preview_async(project_id: str) -> tuple[bool, str]:
    """Stop preview process but keep stable preview_domain for iframe embeds."""
    stop_preview(project_id)
    await update_project(project_id, {"preview_status": "stopped", "preview_started_at": None})
    from syte.certificates import apply_proxy_config
    await apply_proxy_config()
    return True, "Preview stopped."


def get_preview_logs(project_id: str, lines: int = 200) -> str:
    log_path = preview_log_path(project_id)
    if not log_path.exists():
        return "No preview logs yet."
    content = log_path.read_text(errors="replace").splitlines()
    return "\n".join(content[-lines:])


def preview_meta(project: dict) -> dict:
    preview_port = project.get("preview_port")
    running = is_preview_running(project["id"])
    ready = running and preview_port and _port_listening(int(preview_port))
    urls = build_preview_urls(project)
    domain = urls["preview_domain"]
    base_zone = domain.split(".", 1)[-1] if domain and "." in domain else ""
    return {
        "preview_running": running,
        "preview_ready": ready,
        "preview_port": preview_port,
        "preview_status": project.get("preview_status", "stopped"),
        "preview_stream_url": f"/api/projects/{project['id']}/preview/logs/stream?live=1",
        "preview_dns_hint": preview_dns_hint(urls["preview_domain"], base_zone) if urls["preview_domain"] else "",
        **urls,
    }


async def preview_iframe_status(project: dict) -> dict:
    """Iframe embed checklist — configured headers + optional live probe."""
    from syte.database import get_setting
    from syte.domain_utils import normalize_domain
    from syte.preview_iframe import (
        build_iframe_checklist,
        expected_frame_csp,
        probe_preview_headers,
    )

    gui_domain = normalize_domain(await get_setting("gui_domain", ""))
    embed_mode = (await get_setting("preview_embed_mode", "any")).strip().lower()
    frame_csp = expected_frame_csp(gui_domain, allow_any=embed_mode != "restricted")

    live_headers = None
    urls = build_preview_urls(project)
    probe_url = urls.get("preview_fetch_url") or urls.get("preview_domain_url")
    if project.get("preview_status") == "running" and probe_url:
        live_headers = probe_preview_headers(probe_url)

    checklist = build_iframe_checklist(project, frame_csp=frame_csp, live_headers=live_headers)
    if urls.get("preview_domain_url") and not urls.get("preview_tls_ok"):
        checklist["items"].append({
            "id": "preview_tls",
            "label": "Preview HTTPS TLS reachable",
            "ok": False,
            "configured_by_syte": True,
            "note": urls.get("preview_tls_hint") or "HTTPS handshake failed for preview domain",
        })
        checklist["all_ok"] = all(item["ok"] for item in checklist["items"])
    return checklist


async def start_preview(project_id: str) -> tuple[bool, str, dict]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found", {}

    project = await ensure_preview_address(project)

    preview_port = project.get("preview_port")
    if (
        is_preview_running(project_id)
        and preview_port
        and _port_listening(int(preview_port))
    ):
        if project.get("preview_status") != "running":
            await update_project(project_id, {"preview_status": "running"})
            project = await get_project(project_id) or project
        from syte.project_enrich import enrich_ssl

        meta = preview_meta(project)
        meta["ssl"] = enrich_ssl(project)
        meta["iframe"] = await preview_iframe_status(project)
        return True, f"Preview already running on {meta['preview_url']}", meta

    stop_preview(project_id)

    repo = ensure_workspace(project_id) / "app"
    cmd_template = detect_dev_command(repo)
    if not cmd_template:
        return False, (
            "No dev server detected. Add a \"dev\" script to package.json "
            "(e.g. \"next dev\" or \"vite\") then retry start_preview."
        ), {}

    if "npm" in cmd_template and not command_exists("npm"):
        from syte.runtime import ensure_npm
        ok, msg = ensure_npm()
        if not ok:
            return False, msg, {}

    preview_port = project.get("preview_port")
    if not preview_port:
        preview_port = await next_preview_port()

    preview_domain = normalize_domain(project.get("preview_domain") or "")
    if not is_preview_hostname(preview_domain):
        preview_domain = await resolve_preview_domain(project)
    prep_actions = prepare_preview_hosts(repo, preview_domain)
    cmd_template = detect_dev_command(repo) or cmd_template
    command = build_preview_command(repo, cmd_template).replace(
        "$SYTE_PREVIEW_PORT", str(preview_port)
    )

    log_path = preview_log_path(project_id)
    with log_path.open("a") as log_file:
        log_file.write(f"\n=== Preview session (port {preview_port}) ===\n")
        log_file.write(f"Domain: {preview_domain}\n")
        if prep_actions:
            log_file.write("Config:\n" + "\n".join(f"  - {a}" for a in prep_actions) + "\n")

    ok, dep_msg = ensure_preview_deps(repo, log_path)
    if not ok:
        await update_project(project_id, {"preview_status": "stopped"})
        return False, dep_msg, {}

    with log_path.open("a") as log_file:
        if dep_msg != "dependencies already installed":
            log_file.write(f"Dependencies: {dep_msg}\n")
        log_file.write(f"$ {command}\n")

    env = preview_process_env(project, int(preview_port))

    log_file = open(log_path, "a")
    proc = subprocess.Popen(
        command,
        cwd=repo,
        shell=True,
        env=env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        preexec_fn=configure_preview_process,
    )

    ready = False
    for _ in range(80):
        await asyncio.sleep(0.25)
        if proc.poll() is not None:
            log_file.close()
            tail = "\n".join(log_path.read_text(errors="replace").splitlines()[-15:])
            await update_project(project_id, {"preview_status": "stopped"})
            return False, f"Preview process exited.\n{tail}", {}
        if _port_listening(int(preview_port)):
            ready = True
            break

    preview_pid_file(project_id).write_text(str(proc.pid))
    log_file.close()

    status = "running" if ready else "starting"
    await update_project(project_id, {
        "preview_port": int(preview_port),
        "preview_status": status,
        "preview_domain": preview_domain or None,
        "preview_started_at": datetime.now(timezone.utc).isoformat(),
    })

    from syte.certificates import apply_proxy_config
    await apply_proxy_config()

    project = await get_project(project_id) or project
    from syte.project_enrich import enrich_ssl

    meta = preview_meta(project)
    meta["ssl"] = enrich_ssl(project)
    meta["iframe"] = await preview_iframe_status(project)
    msg = f"Preview on {meta['preview_url']}"
    if meta.get("preview_domain"):
        msg += f" (domain: {meta['preview_domain']})"
    if ready:
        msg += " — ready (HMR live)"
    else:
        msg += " — starting (poll preview_status)"
    return True, msg, meta


def _preview_start_grace_elapsed(project: dict) -> bool:
    """True when enough time passed since last update to treat a dead pid as stopped."""
    raw = project.get("updated_at") or ""
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds() > PREVIEW_START_GRACE_SEC
    except (ValueError, TypeError):
        return True


async def get_preview_status(project_id: str, *, quick: bool = False) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found"
    project = await ensure_preview_address(project)
    running = is_preview_running(project_id)
    status = project.get("preview_status", "stopped")
    preview_port = project.get("preview_port")

    if running and preview_port:
        port = int(preview_port)
        new_status = "running" if _port_listening(port) else "starting"
        if status != new_status:
            await update_project(project_id, {"preview_status": new_status})
            project = await get_project(project_id) or project
    elif not running and status != "stopped":
        port_up = preview_port and _port_listening(int(preview_port))
        if port_up:
            preview_pid_file(project_id).unlink(missing_ok=True)
            if status != "running":
                await update_project(project_id, {"preview_status": "running"})
                project = await get_project(project_id) or project
        elif status == "starting" and not _preview_start_grace_elapsed(project):
            pass
        else:
            await update_project(project_id, {"preview_status": "stopped"})
            project = await get_project(project_id) or project
    from syte.project_enrich import enrich_ssl

    meta = preview_meta(project)
    meta["ssl"] = enrich_ssl(project)
    if quick:
        meta["iframe"] = project.get("iframe") or {"all_ok": None, "items": []}
    else:
        meta["iframe"] = await preview_iframe_status(project)
    return meta, "ok"


def _preview_runtime_elapsed(project: dict) -> float | None:
    raw = project.get("preview_started_at") or ""
    if not raw:
        return None
    try:
        ts = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - ts).total_seconds()
    except (ValueError, TypeError):
        return None


async def expire_stale_previews() -> None:
    """Stop previews that exceeded max runtime (5 min) or lost their process."""
    projects = await list_projects()
    for project in projects:
        project_id = project["id"]
        if project.get("preview_status") not in ("running", "starting"):
            continue
        elapsed = _preview_runtime_elapsed(project)
        if elapsed is not None and elapsed > PREVIEW_MAX_RUNTIME_SEC:
            logger = __import__("logging").getLogger("syte.preview")
            logger.info("Stopping preview for %s — max runtime %ss reached", project_id, PREVIEW_MAX_RUNTIME_SEC)
            await stop_preview_async(project_id)
            continue
        if not is_preview_running(project_id):
            port = project.get("preview_port")
            if port and _port_listening(int(port)):
                continue
            if project.get("preview_status") == "starting" and not _preview_start_grace_elapsed(project):
                continue
            await stop_preview_async(project_id)
