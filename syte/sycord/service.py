"""Sycord API business logic."""

import json
import shutil
import subprocess

from syte import deployment
from syte.certificates import apply_proxy_config
from syte.database import get_project, get_setting, list_projects, update_project
from syte.deployment import issue_deploy, set_custom_domain
from syte.docker_deploy import container_name, docker_container_exists, is_docker_running
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.preview_domains import _preview_base_domain
from syte import workspace_api
from syte.workspace import slugify, write_env_file
from syte.sycord.scaffold import STACKS, scaffold_project
from syte.config import settings


def _base_zone() -> str:
    """Root zone for auto subdomains (e.g. sycord.site)."""
    return "sycord.site"


async def resolve_base_zone() -> str:
    gui = normalize_domain(await get_setting("gui_domain", ""))
    if gui:
        return _preview_base_domain(gui)
    return _base_zone()


def project_subdomain(name: str, base_zone: str) -> str:
    slug = slugify(name)[:48] or "project"
    return f"{slug}.{base_zone}"


async def project_connect(
    name: str,
    *,
    stack: str = "nextjs",
    env_vars: dict | None = None,
    project_uuid: str | None = None,
) -> tuple[dict | None, str]:
    """Create workspace, scaffold stack, assign {name}.{zone} subdomain."""
    stack = (stack or "nextjs").lower().strip()
    if stack not in STACKS:
        return None, f"Invalid stack '{stack}'. Use: {', '.join(STACKS)}"

    base_zone = await resolve_base_zone()
    subdomain = project_subdomain(name, base_zone)

    for p in await list_projects():
        if normalize_domain(p.get("domain") or "") == subdomain:
            return None, f"Subdomain already in use: {subdomain}"

    merged_env = {**(env_vars or {}), "SYTE_STACK": stack, "SYCORD_CONNECTED": "1"}
    project, message = await deployment.create_project_record(
        name=name,
        domain=subdomain,
        env_vars=merged_env,
        project_uuid=project_uuid,
        deploy_now=False,
    )
    if not project:
        return None, message

    await update_project(project["id"], {"deploy_type": "docker"})
    write_env_file(project["id"], merged_env)
    scaffolded = scaffold_project(project["id"], stack)
    await apply_proxy_config()

    project = await get_project(project["id"]) or project
    return project, message + (f" Scaffolded: {', '.join(scaffolded)}" if scaffolded else "")


async def container_get_async(project_id: str) -> dict | None:
    project = await get_project(project_id)
    if not project:
        return None
    ip = await get_setting("public_ip", settings.resolved_public_ip)
    return _container_payload(project_id, project, ip)


def _container_payload(project_id: str, project: dict, public_ip: str) -> dict:
    name = container_name(project_id)
    exists = docker_container_exists(project_id)
    running = is_docker_running(project_id) if exists else False
    state = image = started_at = ""
    if exists and shutil.which("docker"):
        code, state = _docker_inspect(name, "{{.State.Status}}")
        _, image = _docker_inspect(name, "{{.Config.Image}}")
        _, started_at = _docker_inspect(name, "{{.State.StartedAt}}")

    port = project.get("port")
    domain = normalize_domain(project.get("domain") or "")
    if domain:
        url = build_https_url(domain)
    elif port:
        url = build_direct_url(public_ip, int(port))
    else:
        url = None

    return {
        "uuid": project_id,
        "container_name": name,
        "exists": exists,
        "running": running,
        "state": state or ("running" if running else "missing"),
        "image": image,
        "started_at": started_at,
        "host_port": port,
        "domain": domain or None,
        "url": url,
        "deploy_type": project.get("deploy_type"),
        "status": project.get("status"),
    }


def _docker_inspect(container: str, fmt: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            ["docker", "inspect", "-f", fmt, container],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return proc.returncode, (proc.stdout or "").strip()
    except (OSError, subprocess.TimeoutExpired):
        return 1, ""


async def upload_file(project_id: str, path: str, content: bytes) -> tuple[bool, str]:
    return await workspace_api.upload_file(project_id, path, content)


async def set_domain(
    project_id: str,
    domain: str,
    *,
    email: str | None = None,
) -> tuple[dict | None, str]:
    from syte.config import settings

    domain = normalize_domain(domain)
    if not domain:
        return None, "domain is required"
    admin_email = email or await get_setting("admin_email", settings.admin_email)
    return await set_custom_domain(project_id, domain, admin_email)


async def issue_deployment(project_id: str) -> tuple[dict | None, str]:
    return await issue_deploy(project_id)


def project_stack(project: dict) -> str:
    raw = project.get("env_vars") or "{}"
    try:
        env = json.loads(raw) if isinstance(raw, str) else raw
    except json.JSONDecodeError:
        env = {}
    return env.get("SYTE_STACK", "nextjs")
