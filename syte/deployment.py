import uuid

from syte import process_manager
from syte.certificates import apply_proxy_config
from syte.database import create_project, delete_project, get_project, update_project
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
        "start_command": start_command or "npm start",
        "env_vars": env_vars or {},
    })

    ensure_workspace(project_id)
    if env_vars:
        write_env_file(project_id, env_vars)

    messages = [f"Workspace created at /var/lib/syte/workspaces/{project_id}"]

    if git_url:
        ok, msg = clone_or_pull(project_id, git_url, branch)
        messages.append(msg)
        if not ok:
            return project, "\n".join(messages)

        if not start_command:
            cmd = detect_start_command(project_id)
            await update_project(project_id, {"start_command": cmd})
            project = await get_project(project_id)

    ok, msg = process_manager.start_project(
        project_id,
        port,
        project["start_command"],
        project["env_vars"],
    )
    messages.append(msg)

    if ok:
        await update_project(project_id, {"status": "running"})
        project = await get_project(project_id)

    await apply_proxy_config()
    return project, "\n".join(messages)


async def update_service(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."

    messages = ["Pulling latest git version…"]
    was_running = process_manager.is_running(project_id)

    if was_running:
        _, stop_msg = process_manager.stop_project(project_id)
        messages.append(stop_msg)

    ok, msg = clone_or_pull(project_id, project.get("git_url"), project.get("branch", "main"))
    messages.append(msg)
    if not ok:
        return project, "\n".join(messages)

    if was_running:
        ok, msg = process_manager.start_project(
            project_id,
            project["port"],
            project["start_command"],
            project["env_vars"],
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
    ok, msg = process_manager.stop_project(project_id)
    await update_project(project_id, {"status": "stopped"})
    await apply_proxy_config()
    return await get_project(project_id), msg


async def start_service(project_id: str) -> tuple[dict | None, str]:
    project = await get_project(project_id)
    if not project:
        return None, "Project not found."
    ok, msg = process_manager.start_project(
        project_id,
        project["port"],
        project["start_command"],
        project["env_vars"],
    )
    status = "running" if ok else "stopped"
    await update_project(project_id, {"status": status})
    await apply_proxy_config()
    return await get_project(project_id), msg


async def remove_service(project_id: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found."
    process_manager.stop_project(project_id)
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
