import uuid
import asyncio
from pathlib import Path

from syte import process_manager
from syte.certificates import apply_proxy_config
from syte.database import create_project, delete_project, get_project, update_project
from syte.docker_deploy import find_dockerfile
from syte.runtime import ensure_runtime_for_command
from syte.workspace import (
    clone_or_pull,
    detect_start_command,
    ensure_workspace,
    slugify,
    write_env_file,
)


def _next_port(existing: list[dict]) -> int:
    used = {p["port"] for p in existing}
    port = 3000
    while port in used:
        port += 1
    return port


def _resolve_deploy(
    project_id: str,
    start_command: str | None,
    *,
    prefer_docker: bool = True,
) -> tuple[dict, str | None]:
    """After git clone, prefer Docker deploy; fall back to shell only with explicit command."""
    dockerfile = find_dockerfile(project_id)
    if dockerfile:
        app_root = ensure_workspace(project_id) / "app"
        try:
            rel = dockerfile.relative_to(app_root)
        except ValueError:
            rel = Path("Dockerfile")
        return {
            "deploy_type": "docker",
            "dockerfile_path": str(rel),
            "start_command": f"docker:{rel}",
        }, None

    if start_command:
        return {
            "deploy_type": "shell",
            "dockerfile_path": None,
            "start_command": start_command,
        }, None

    if prefer_docker:
        return {
            "deploy_type": "docker",
            "dockerfile_path": None,
            "start_command": "",
        }, (
            "No Dockerfile found in repository. "
            "Add a Dockerfile for docker deployment (recommended), "
            "or provide a start command for shell deployment."
        )

    cmd, err = detect_start_command(project_id)
    if err:
        return {
            "deploy_type": "shell",
            "dockerfile_path": None,
            "start_command": "",
        }, err

    return {
        "deploy_type": "shell",
        "dockerfile_path": None,
        "start_command": cmd or "",
    }, None


async def _ensure_deploy_info(project: dict) -> dict:
    """Re-detect Dockerfile for projects created before docker detection worked."""
    project_id = project["id"]
    if project.get("deploy_type") == "docker":
        return project
    if not project.get("git_url"):
        return project
    dockerfile = find_dockerfile(project_id)
    if not dockerfile:
        return project
    deploy_info, _ = _resolve_deploy(project_id, project.get("start_command") or None)
    if deploy_info["deploy_type"] != "docker":
        return project
    await update_project(project_id, deploy_info)
    return await get_project(project_id) or project


async def run_deploy_job(project_id: str, start_command: str | None = None) -> str:
    """Execute deploy steps for an existing project (async, streamable logs)."""
    project = await get_project(project_id)
    if not project:
        return "Project not found"

    await update_project(project_id, {"status": "deploying"})
    port = project["port"]
    git_url = project.get("git_url")
    branch = project.get("branch", "main")
    messages = [f"Deploying {project_id}…"]

    if git_url:
        messages.append("Cloning/updating git repository…")
        ok, msg = clone_or_pull(project_id, git_url, branch)
        messages.append(msg)
        if not ok:
            await update_project(project_id, {"status": "stopped"})
            return "\n".join(messages)

        deploy_info, deploy_err = _resolve_deploy(project_id, start_command or project.get("start_command"))
        await update_project(project_id, deploy_info)
        project = await get_project(project_id)
        if not project:
            return "\n".join(messages)

        if deploy_err:
            messages.append(deploy_err)
            await update_project(project_id, {"status": "stopped"})
            return "\n".join(messages)

        if deploy_info["deploy_type"] == "docker":
            messages.append(f"Dockerfile: {deploy_info['dockerfile_path']}")
    elif not project.get("start_command") and not start_command:
        cmd, err = detect_start_command(project_id)
        if err:
            messages.append(
                "Nothing to deploy yet. Add files (write_file/upload_file), "
                "run execute_command to scaffold, add a Dockerfile, or set start_command — "
                "then call issue_deploy again."
            )
            await update_project(project_id, {"status": "created"})
            return "\n".join(messages)
        if cmd:
            await update_project(project_id, {"start_command": cmd})
            project = await get_project(project_id)

    if not project.get("start_command") and project.get("deploy_type") != "docker":
        messages.append("No start command configured.")
        await update_project(project_id, {"status": "stopped"})
        return "\n".join(messages)

    ok, msg = await asyncio.to_thread(
        process_manager.start_project,
        project_id,
        port,
        project["start_command"],
        project["env_vars"],
        project.get("deploy_type", "shell"),
        project.get("dockerfile_path"),
    )
    messages.append(msg)
    status = "running" if ok else "stopped"
    await update_project(project_id, {"status": status})
    await apply_proxy_config()
    return "\n".join(messages)


async def create_project_record(
    name: str,
    git_url: str | None = None,
    branch: str = "main",
    start_command: str | None = None,
    env_vars: dict | None = None,
    domain: str | None = None,
    git_provider: str | None = None,
    project_uuid: str | None = None,
    deploy_now: bool = False,
) -> tuple[dict | None, str]:
    """Create an empty project workspace. Git and files are optional — add anytime, deploy anytime."""
    from syte.database import list_projects

    projects = await list_projects()
    if project_uuid:
        project_id = project_uuid
    else:
        project_id = slugify(name) + "-" + uuid.uuid4().hex[:6]

    if await get_project(project_id):
        return None, f"Project UUID already exists: {project_id}"

    port = _next_port(projects)
    resolved_git = git_url
    if git_provider and git_url and not git_url.startswith("http"):
        resolved_git = f"https://{git_provider}/{git_url.lstrip('/')}"

    project = await create_project({
        "id": project_id,
        "name": name,
        "git_url": resolved_git,
        "branch": branch,
        "port": port,
        "domain": domain,
        "start_command": start_command or "",
        "env_vars": env_vars or {},
        "deploy_type": "docker",
    })

    ensure_workspace(project_id)
    if env_vars:
        write_env_file(project_id, env_vars)

    await update_project(project_id, {"status": "created"})
    project = await get_project(project_id)

    if deploy_now:
        asyncio.create_task(run_deploy_job(project_id, start_command))
        return project, f"Project {project_id} created. Deploy started in background."

    return project, (
        f"Empty project {project_id} created. "
        f"No git or files required — add anytime via write_file/execute_command, "
        f"then POST /api/issue_deploy when ready."
    )


async def begin_deploy_service(
    name: str,
    git_url: str | None = None,
    branch: str = "main",
    start_command: str | None = None,
    env_vars: dict | None = None,
    domain: str | None = None,
    git_provider: str | None = None,
    project_uuid: str | None = None,
) -> tuple[dict | None, str]:
    """Create project and immediately start deploy (GUI flow)."""
    return await create_project_record(
        name=name,
        git_url=git_url,
        branch=branch,
        start_command=start_command,
        env_vars=env_vars,
        domain=domain,
        git_provider=git_provider,
        project_uuid=project_uuid,
        deploy_now=True,
    )


async def issue_deploy(project_id: str) -> tuple[dict | None, str]:
    """Re-run deploy for an existing project (background)."""
    project = await get_project(project_id)
    if not project:
        return None, "Project not found"
    asyncio.create_task(run_deploy_job(project_id))
    return project, (
        f"Deploy issued for {project_id}. "
        f"Stream logs: GET /api/projects/{project_id}/logs/stream"
    )


async def deploy_service(
    name: str,
    git_url: str | None = None,
    branch: str = "main",
    start_command: str | None = None,
    env_vars: dict | None = None,
    domain: str | None = None,
) -> tuple[dict | None, str]:
    from syte.database import list_projects

    projects = await list_projects()
    project_id = slugify(name) + "-" + uuid.uuid4().hex[:6]
    port = _next_port(projects)

    project = await create_project({
        "id": project_id,
        "name": name,
        "git_url": git_url,
        "branch": branch,
        "port": port,
        "domain": domain,
        "start_command": start_command or "",
        "env_vars": env_vars or {},
        "deploy_type": "docker" if git_url else "shell",
    })

    ensure_workspace(project_id)
    if env_vars:
        write_env_file(project_id, env_vars)

    messages = [f"Workspace created at /var/lib/syte/workspaces/{project_id}"]

    if git_url:
        messages.append("Cloning git repository…")
        ok, msg = clone_or_pull(project_id, git_url, branch)
        messages.append(msg)
        if not ok:
            return project, "\n".join(messages)

        deploy_info, deploy_err = _resolve_deploy(project_id, start_command)
        await update_project(project_id, deploy_info)
        project = await get_project(project_id)

        if deploy_err:
            messages.append(deploy_err)
            return project, "\n".join(messages)

        if deploy_info["deploy_type"] == "docker":
            messages.append(f"Dockerfile found: {deploy_info['dockerfile_path']}")
        elif start_command:
            messages.append("No Dockerfile — using shell deployment (start command provided).")
        else:
            messages.append("No Dockerfile — docker deployment requires a Dockerfile.")
    elif not start_command:
        cmd, err = detect_start_command(project_id)
        if err:
            messages.append(err)
            return project, "\n".join(messages)
        if cmd:
            await update_project(project_id, {"start_command": cmd})
            project = await get_project(project_id)

    if not project.get("start_command") and project.get("deploy_type") != "docker":
        messages.append("No start command configured.")
        return project, "\n".join(messages)

    ok, msg = process_manager.start_project(
        project_id,
        port,
        project["start_command"],
        project["env_vars"],
        project.get("deploy_type", "shell"),
        project.get("dockerfile_path"),
    )
    messages.append(msg)

    if ok:
        await update_project(project_id, {"status": "running"})
        project = await get_project(project_id)
    else:
        await update_project(project_id, {"status": "stopped"})
        project = await get_project(project_id)

    await apply_proxy_config()
    return project, "\n".join(messages)


async def update_service(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."

    deploy_type = project.get("deploy_type", "shell")
    messages = ["Pulling latest git version…"]
    was_running = process_manager.is_running(project_id, deploy_type)

    if was_running:
        _, stop_msg = process_manager.stop_project(project_id, deploy_type)
        messages.append(stop_msg)

    ok, msg = clone_or_pull(project_id, project.get("git_url"), project.get("branch", "main"))
    messages.append(msg)
    if not ok:
        return project, "\n".join(messages)

    if project.get("git_url"):
        deploy_info, deploy_err = _resolve_deploy(project_id, None)
        await update_project(project_id, deploy_info)
        project = await get_project(project_id)
        deploy_type = project.get("deploy_type", "shell")
        if deploy_err:
            messages.append(deploy_err)
            return project, "\n".join(messages)
        if deploy_info["deploy_type"] == "docker":
            messages.append(f"Dockerfile: {deploy_info['dockerfile_path']}")

    if was_running:
        if deploy_type == "docker":
            ok, msg = process_manager.restart_docker_project(
                project_id,
                project["port"],
                project["env_vars"],
                project.get("dockerfile_path"),
            )
        else:
            ok, msg = process_manager.start_project(
                project_id,
                project["port"],
                project["start_command"],
                project["env_vars"],
                deploy_type,
            )
        messages.append(msg)
        status = "running" if ok else "stopped"
        await update_project(project_id, {"status": status})
        project = await get_project(project_id)

    await apply_proxy_config()
    messages.append("Data preserved in workspace /data directory.")
    return project, "\n".join(messages)


async def stop_service(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."
    ok, msg = process_manager.stop_project(project_id, project.get("deploy_type", "shell"))
    await update_project(project_id, {"status": "stopped"})
    await apply_proxy_config()
    return await get_project(project_id), msg


async def start_service(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."
    project = await _ensure_deploy_info(project)
    ok, msg = process_manager.start_project(
        project_id,
        project["port"],
        project["start_command"],
        project["env_vars"],
        project.get("deploy_type", "shell"),
        project.get("dockerfile_path"),
    )
    status = "running" if ok else "stopped"
    await update_project(project_id, {"status": status})
    await apply_proxy_config()
    return await get_project(project_id), msg


async def remove_service(project_id: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found."
    process_manager.stop_project(project_id, project.get("deploy_type", "shell"))
    await delete_project(project_id)
    await apply_proxy_config()
    return True, f"Service '{project['name']}' removed. Workspace data retained on disk."


async def set_custom_domain(project_id: str, domain: str, email: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."

    await update_project(project_id, {"domain": domain})
    ok, proxy_msg = await apply_proxy_config()

    project = await get_project(project_id)
    return project, (
        f"Domain set to {domain}. "
        f"Caddy will issue a TLS certificate once DNS points to this server.\n"
        f"{proxy_msg}"
    )
