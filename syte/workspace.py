import json
import os
import re
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from syte.config import settings

_GIT_SAFE_CONFIG = [
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "protocol.file.allow=never",
]

# Project IDs are used as filesystem path segments — keep them UUID/slug-safe.
_PROJECT_ID_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def assert_safe_project_id(project_id: str) -> str:
    """Reject path-traversal and otherwise unsafe project_id values."""
    value = (project_id or "").strip()
    if not value or not _PROJECT_ID_RE.match(value):
        raise ValueError("Invalid project_id — must be alphanumeric with ._- only")
    if ".." in value or "/" in value or "\\" in value:
        raise ValueError("Invalid project_id — path traversal denied")
    return value


def workspace_path(project_id: str) -> Path:
    safe_id = assert_safe_project_id(project_id)
    base = settings.resolved_workspaces_dir.resolve()
    path = (base / safe_id).resolve()
    if path != base and base not in path.parents:
        raise ValueError("Invalid project_id — path traversal denied")
    return path


def ensure_workspace(project_id: str) -> Path:
    path = workspace_path(project_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(exist_ok=True)
    (path / "app").mkdir(exist_ok=True)
    return path


def deploy_log_path(project_id: str) -> Path:
    return workspace_path(project_id) / "deploy.log"


def begin_deploy_log_session(project_id: str) -> None:
    ensure_workspace(project_id)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with deploy_log_path(project_id).open("a") as f:
        f.write(f"\n=== Deploy session {ts} ===\n")


def append_deploy_log(project_id: str, line: str) -> None:
    ensure_workspace(project_id)
    with deploy_log_path(project_id).open("a") as f:
        for part in line.splitlines() or [""]:
            f.write(part + "\n")


def write_env_file(project_id: str, env_vars: dict[str, str]) -> None:
    ws = ensure_workspace(project_id)
    env_path = ws / ".env"
    lines = [f"{k}={v}" for k, v in env_vars.items()]
    env_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def read_env_vars(raw: str | dict) -> dict[str, str]:
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}


def command_exists(cmd: str) -> bool:
    return shutil.which(cmd) is not None


def git_cmd(*args: str) -> list[str]:
    """Build a git argv with repository-controlled hooks/protocols disabled."""
    return ["git", *_GIT_SAFE_CONFIG, *args]


def _harden_git_argv(cmd: list[str]) -> tuple[list[str], bool]:
    if not cmd or Path(cmd[0]).name != "git":
        return cmd, False
    if cmd[1:1 + len(_GIT_SAFE_CONFIG)] == _GIT_SAFE_CONFIG:
        return cmd, True
    return [cmd[0], *_GIT_SAFE_CONFIG, *cmd[1:]], True


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    from syte.output_limits import TRUNCATION_MARKER, read_binary_stream_limited

    merged_env = {**os.environ, **(env or {})}
    cmd, is_git = _harden_git_argv(cmd)
    if is_git:
        merged_env["GIT_TERMINAL_PROMPT"] = "0"
    proc = subprocess.Popen(
        cmd,
        cwd=cwd,
        env=merged_env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert proc.stdout is not None
    raw, truncated = read_binary_stream_limited(proc.stdout)
    code = int(proc.wait() or 0)
    output = raw.decode("utf-8", errors="replace").strip()
    if truncated and TRUNCATION_MARKER.strip() not in output:
        output = (output + TRUNCATION_MARKER).strip()
    return code, output


def clone_or_pull(project_id: str, git_url: str | None, branch: str) -> tuple[bool, str]:
    ws = ensure_workspace(project_id)
    repo_dir = ws / "app"

    if not git_url:
        repo_dir.mkdir(parents=True, exist_ok=True)
        return True, "Workspace ready (no git repository configured)."

    if (repo_dir / ".git").exists():
        code, out = run_cmd(git_cmd("fetch", "origin"), cwd=repo_dir)
        if code != 0:
            return False, out
        code, out = run_cmd(git_cmd("checkout", branch), cwd=repo_dir)
        if code != 0:
            return False, out
        code, out = run_cmd(git_cmd("pull", "origin", branch), cwd=repo_dir)
        return code == 0, out or "Repository updated."

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    code, out = run_cmd(
        git_cmd("clone", "--branch", branch, "--depth", "1", git_url, str(repo_dir))
    )
    if code != 0:
        code, out = run_cmd(git_cmd("clone", git_url, str(repo_dir)))
        if code == 0:
            run_cmd(git_cmd("checkout", branch), cwd=repo_dir)
    return code == 0, out or "Repository cloned."


def _node_start_command(repo: Path) -> str | None:
    if not (repo / "package.json").exists():
        return None
    if command_exists("npm"):
        return "npm install && npm start"
    if command_exists("yarn"):
        return "yarn install && yarn start"
    if command_exists("pnpm"):
        return "pnpm install && pnpm start"
    if command_exists("node"):
        pkg = json.loads((repo / "package.json").read_text())
        main = pkg.get("main", "index.js")
        return f"node {main}"
    return None


def detect_start_command(project_id: str) -> tuple[str | None, str | None]:
    """Return (command, error_message). error is set when detection fails."""
    repo = workspace_path(project_id) / "app"

    if (repo / "package.json").exists() and not command_exists("npm"):
        from syte.runtime import ensure_npm
        ok, msg = ensure_npm()
        if not ok:
            return None, msg

    node_cmd = _node_start_command(repo)
    if node_cmd:
        return node_cmd, None
    if (repo / "package.json").exists():
        return None, (
            "Node.js project detected but npm/yarn/pnpm is not installed. "
            "Run sudo ./scripts/install.sh to install nodejs, or add a Dockerfile."
        )

    if (repo / "requirements.txt").exists():
        if command_exists("python3"):
            return (
                "python3 -m venv .venv && . .venv/bin/activate && "
                "pip install -r requirements.txt && python main.py"
            ), None
        return None, "Python project detected but python3 is not installed."

    if (repo / "go.mod").exists():
        if command_exists("go"):
            return "go build -o app . && ./app", None
        return None, "Go project detected but go is not installed."

    if (repo / "Cargo.toml").exists():
        if command_exists("cargo"):
            return "cargo build --release && ./target/release/$(basename $(pwd))", None
        return None, "Rust project detected but cargo is not installed."

    if repo.exists() and any(repo.iterdir()):
        return None, "Could not detect start command. Add a Dockerfile or set one manually."

    return "python3 -m http.server ${PORT:-3000}", None
