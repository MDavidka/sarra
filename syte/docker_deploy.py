import json
import re
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path

from syte import __version__
from syte.workspace import read_env_vars, run_cmd, workspace_path

DOCKERFILE_NAMES = ("Dockerfile", "dockerfile", "Dockerfile.prod", "Dockerfile.production")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def find_dockerfile(project_id: str) -> Path | None:
    """Search cloned repo for a Dockerfile (root first, then subdirs)."""
    repo = workspace_path(project_id) / "app"
    if not repo.exists():
        return None

    for name in DOCKERFILE_NAMES:
        candidate = repo / name
        if candidate.is_file():
            return candidate

    for path in sorted(repo.rglob("Dockerfile*")):
        if path.is_file() and "node_modules" not in path.parts and ".git" not in path.parts:
            return path

    for path in sorted(repo.rglob("dockerfile")):
        if path.is_file() and "node_modules" not in path.parts:
            return path

    return None


def detect_container_port(dockerfile: Path) -> int:
    port = 3000
    try:
        for line in dockerfile.read_text().splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("EXPOSE"):
                parts = stripped.split()[1:]
                if parts:
                    port = int(parts[0].split("/")[0])
    except (OSError, ValueError):
        pass
    return port


def _strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def _is_nextjs_repo(repo: Path) -> bool:
    pkg = repo / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        return "next" in deps
    except (json.JSONDecodeError, OSError):
        return False


def _runtime_env_args(repo: Path, container_port: int, env_vars_raw: str | dict) -> list[str]:
    """Env vars passed to docker run (user env + sensible defaults)."""
    env = read_env_vars(env_vars_raw)
    env.setdefault("PORT", str(container_port))
    if _is_nextjs_repo(repo):
        env.setdefault("HOSTNAME", "0.0.0.0")
        env.setdefault("NODE_ENV", "production")
    args: list[str] = []
    for key, value in env.items():
        args.extend(["-e", f"{key}={value}"])
    return args


def _container_logs(container: str, lines: int = 80) -> str:
    code, out = run_cmd(["docker", "logs", "--tail", str(lines), container])
    if code == 0 and out.strip():
        return _strip_ansi(out.strip())
    code, out = run_cmd(["docker", "logs", container])
    return _strip_ansi(out.strip()) if code == 0 and out.strip() else "No container logs."


def _container_state(container: str) -> str:
    code, out = run_cmd([
        "docker", "inspect", "-f",
        "{{.State.Status}} (exit {{.State.ExitCode}}): {{.State.Error}}",
        container,
    ])
    return out.strip() if code == 0 else "unknown"


def _image_name(project_id: str) -> str:
    safe = re.sub(r"[^a-z0-9-]", "-", project_id.lower())
    return f"syte-{safe}"


def _container_name(project_id: str) -> str:
    return _image_name(project_id)


def container_name(project_id: str) -> str:
    return _container_name(project_id)


def is_docker_running(project_id: str) -> bool:
    if not shutil.which("docker"):
        return False
    name = _container_name(project_id)
    code, out = run_cmd(
        ["docker", "inspect", "-f", "{{.State.Running}}", name]
    )
    return code == 0 and out.strip().lower() == "true"


def stop_docker(project_id: str) -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "Docker is not installed."
    name = _container_name(project_id)
    run_cmd(["docker", "stop", name])
    code, out = run_cmd(["docker", "rm", name])
    if code == 0:
        return True, f"Stopped container {name}."
    code, out = run_cmd(["docker", "inspect", name])
    if code != 0:
        return True, "Container already stopped."
    return False, out or f"Failed to stop container {name}."


def _build_log_path(project_id: str) -> Path:
    return workspace_path(project_id) / "build.log"


def _append_build_log(project_id: str, label: str, output: str) -> None:
    log_path = _build_log_path(project_id)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = _strip_ansi(output)
    with log_path.open("a") as log_file:
        log_file.write(f"\n=== {label} ===\n")
        log_file.write(cleaned)
        if cleaned and not cleaned.endswith("\n"):
            log_file.write("\n")


def deploy_docker(
    project_id: str,
    host_port: int,
    dockerfile: Path,
    env_vars_raw: str | dict,
) -> tuple[bool, str]:
    if not shutil.which("docker"):
        return False, "Docker is not installed. Install docker.io to deploy from Dockerfile."

    repo = workspace_path(project_id) / "app"
    image = _image_name(project_id)
    container = _container_name(project_id)
    container_port = detect_container_port(dockerfile)
    data_dir = workspace_path(project_id) / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    build_log = _build_log_path(project_id)
    stop_docker(project_id)
    run_cmd(["docker", "rmi", image])

    build_cmd = [
        "docker", "build",
        "-t", image,
        "-f", str(dockerfile),
        str(repo),
    ]
    build_log.write_text(
        f"Syte v{__version__} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Command: {' '.join(build_cmd)}\n"
        f"Building {image} from {dockerfile.name}\n"
    )
    code, out = run_cmd(build_cmd)
    _append_build_log(project_id, "docker build", out or "(no output)")
    if code != 0:
        tail = _strip_ansi(out or "")
        return False, f"Docker build failed (exit {code}).\n{tail[-4000:]}"

    run_cmd_list = [
        "docker", "run", "-d",
        "--name", container,
        "--restart", "unless-stopped",
        "-p", f"{host_port}:{container_port}",
        "-v", f"{data_dir}:/data",
        *_runtime_env_args(repo, container_port, env_vars_raw),
        image,
    ]
    _append_build_log(project_id, "docker run command", " ".join(run_cmd_list))
    code, out = run_cmd(run_cmd_list)
    _append_build_log(project_id, "docker run", out or "(no output)")
    if code != 0:
        return False, f"Docker run failed:\n{_strip_ansi(out or '')}"

    time.sleep(3)
    if not is_docker_running(project_id):
        logs = _container_logs(container)
        state = _container_state(container)
        _append_build_log(project_id, "container exited", f"{state}\n{logs}")
        return False, (
            f"Container exited after start — {state}\n\n"
            f"Container logs:\n{logs}\n\n"
            f"For Next.js apps ensure the Dockerfile CMD listens on 0.0.0.0 "
            f"and EXPOSE matches the app port (usually 3000)."
        )

    rel = dockerfile.relative_to(repo)
    runtime = "Next.js" if _is_nextjs_repo(repo) else "app"
    return True, (
        f"Deployed {runtime} via Docker ({rel}) on port {host_port} → "
        f"container:{container_port}. Container: {container}"
    )


def rebuild_docker(
    project_id: str,
    host_port: int,
    dockerfile: Path,
    env_vars_raw: str | dict,
) -> tuple[bool, str]:
    return deploy_docker(project_id, host_port, dockerfile, env_vars_raw)
