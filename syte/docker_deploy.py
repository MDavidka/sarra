import json
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from syte import __version__
from syte.nextjs_layout import (
    ensure_nextjs_dockerfile,
    find_router_dir,
    fix_nextjs_layout,
    is_nextjs_repo as _is_nextjs_repo_layout,
    validate_nextjs_for_docker,
)
from syte.workspace import read_env_vars, run_cmd, workspace_path

DOCKERFILE_NAMES = ("Dockerfile", "dockerfile", "Dockerfile.prod", "Dockerfile.production")
ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")

_UNLIMITED = frozenset({"", "0", "none", "unlimited", "off"})


def _runtime_resource_args() -> list[str]:
    """Default CPU/memory/pids caps for production containers (DAV-126)."""
    from syte.config import settings

    args: list[str] = []
    memory = str(getattr(settings, "docker_memory", "1g") or "").strip()
    cpus = str(getattr(settings, "docker_cpus", "1.0") or "").strip()
    pids = int(getattr(settings, "docker_pids_limit", 256) or 0)
    if memory.lower() not in _UNLIMITED:
        args.extend(["--memory", memory])
    if cpus.lower() not in _UNLIMITED:
        args.extend(["--cpus", cpus])
    if pids > 0:
        args.extend(["--pids-limit", str(pids)])
    return args


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


def _docker_build_hints(output: str) -> str:
    lower = output.lower()
    hints: list[str] = []
    if "copy failed" in lower and "public" in lower:
        hints.append(
            "Dockerfile copies public/ but that folder is missing from the repo. "
            "Syte can auto-create it on the next deploy, or add public/ to your git project."
        )
    if "copy failed" in lower and "standalone" in lower:
        hints.append(
            "Dockerfile expects Next.js standalone output. Add to next.config.js:\n"
            "  output: 'standalone'"
        )
    if "copy failed" in lower and ".next/static" in lower:
        hints.append(
            "Dockerfile copies .next/static — run next build first or check next.config output settings."
        )
    if "javascript heap out of memory" in lower or "enomem" in lower or "killed" in lower:
        hints.append(
            "Build ran out of memory during npm/next build. "
            "Add NODE_OPTIONS=--max-old-space-size=4096 to the Dockerfile build stage, "
            "or increase server RAM/swap."
        )
    if "couldn't find any `pages` or `app` directory" in lower or "findpagesdir" in lower:
        hints.append(
            "Next.js cannot find app/ or pages/ in the Docker build context.\n"
            "Common fix: page.tsx must be at app/page.tsx (not project root).\n"
            "With Syte workspace, use write_file path: app/app/page.tsx and app/app/layout.tsx.\n"
            "Syte auto-moves misplaced files on deploy — retry issue_deploy after update."
        )
    if "next build" in lower and ("error" in lower or "failed" in lower):
        hints.append(
            "Next.js build failed inside Docker. Scroll the build log above for the compile error — "
            "the container is only created after a successful docker build."
        )
    return "\n\n".join(hints)


def _prepare_docker_context(repo: Path, dockerfile: Path) -> list[str]:
    """Fix common Next.js Docker issues in the cloned workspace before build."""
    actions: list[str] = []

    if _is_nextjs_repo(repo) or _is_nextjs_repo_layout(repo):
        actions.extend(fix_nextjs_layout(repo))
        actions.extend(ensure_nextjs_dockerfile(repo))

    if (repo / "Dockerfile").is_file():
        dockerfile = repo / "Dockerfile"

    if not _is_nextjs_repo(repo):
        return actions

    try:
        dockerfile_text = dockerfile.read_text()
    except OSError:
        return actions

    dockerfile_lower = dockerfile_text.lower()

    if "public" in dockerfile_lower and not (repo / "public").exists():
        public = repo / "public"
        public.mkdir(parents=True)
        (public / ".gitkeep").write_text("")
        actions.append("Created missing public/ directory for Next.js Docker build.")

    if ".next/standalone" in dockerfile_lower:
        for name in ("next.config.js", "next.config.mjs", "next.config.ts"):
            path = repo / name
            if not path.exists():
                continue
            text = path.read_text()
            if re.search(r"""output\s*:\s*['"]standalone['"]""", text):
                break
            if name == "next.config.js" and "module.exports" in text:
                if re.search(r"module\.exports\s*=\s*\{", text):
                    path.write_text(
                        re.sub(
                            r"module\.exports\s*=\s*\{",
                            "module.exports = {\n  output: 'standalone',",
                            text,
                            count=1,
                        )
                    )
                    actions.append(f"Patched {name} with output: 'standalone'.")
                    break
            elif name == "next.config.mjs" and "export default" in text:
                if re.search(r"export\s+default\s*\{", text):
                    path.write_text(
                        re.sub(
                            r"export\s+default\s*\{",
                            "export default {\n  output: 'standalone',",
                            text,
                            count=1,
                        )
                    )
                    actions.append(f"Patched {name} with output: 'standalone'.")
                    break

    return actions


def _docker_build_context(repo: Path, dockerfile: Path) -> Path:
    """Docker build context must contain package.json and app/pages source."""
    df_dir = dockerfile.parent.resolve()
    repo = repo.resolve()
    if (df_dir / "package.json").exists():
        return df_dir
    if (repo / "package.json").exists():
        return repo
    return df_dir


def _workspace_file_listing(repo: Path) -> str:
    from syte.nextjs_layout import _tree_summary

    router = find_router_dir(repo)
    router_note = f"router: {router.relative_to(repo)}/" if router else "router: NOT FOUND"
    return f"{router_note}\n{_tree_summary(repo)}"


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


def docker_container_exists(project_id: str) -> bool:
    if not shutil.which("docker"):
        return False
    code, _ = run_cmd(["docker", "inspect", "-f", "{{.Id}}", _container_name(project_id)])
    return code == 0


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


def _run_build_streaming(build_cmd: list[str], project_id: str) -> tuple[int, str]:
    """Run docker build and stream stdout/stderr into build.log in real time."""
    log_path = _build_log_path(project_id)
    proc = subprocess.Popen(
        build_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    chunks: list[str] = []
    with log_path.open("a") as log_file:
        log_file.write("\n=== docker build ===\n")
        log_file.flush()
        assert proc.stdout is not None
        for line in proc.stdout:
            chunks.append(line)
            log_file.write(line)
            log_file.flush()
    code = proc.wait()
    return code, "".join(chunks).strip()


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

    prep_actions = _prepare_docker_context(repo, dockerfile)
    dockerfile = find_dockerfile(project_id) or dockerfile
    if not dockerfile or not dockerfile.is_file():
        return False, "Dockerfile not found in workspace."

    ok, validate_msg = validate_nextjs_for_docker(repo)
    if not ok:
        listing = _workspace_file_listing(repo)
        return False, f"{validate_msg}\n\nWorkspace listing:\n{listing}"

    build_context = _docker_build_context(repo, dockerfile)
    if build_context != repo:
        try:
            rel = build_context.relative_to(repo)
            prep_actions.append(f"Using docker build context: {rel}/")
        except ValueError:
            prep_actions.append(f"Using docker build context: {build_context}")

    build_cmd = [
        "docker", "build",
        "-t", image,
        "-f", str(dockerfile),
        str(build_context),
    ]
    header = (
        f"Syte v{__version__} — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
        f"Command: {' '.join(build_cmd)}\n"
        f"Building {image} from {dockerfile.name}\n"
        f"Workspace files:\n{_workspace_file_listing(repo)}\n"
    )
    if prep_actions:
        header += "Prepare:\n" + "\n".join(f"  - {a}" for a in prep_actions) + "\n"
    build_log.write_text(header)
    code, out = _run_build_streaming(build_cmd, project_id)
    if code != 0:
        tail = _strip_ansi(out or "")
        hints = _docker_build_hints(tail)
        _append_build_log(project_id, "docker build failed", f"exit code {code}")
        msg = f"Docker build failed (exit {code}).\n{tail[-6000:]}"
        if hints:
            msg += f"\n\nHint:\n{hints}"
        return False, msg

    run_cmd_list = [
        "docker", "run", "-d",
        "--name", container,
        "--restart", "unless-stopped",
        *_runtime_resource_args(),
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
