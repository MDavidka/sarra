import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from syte.config import settings


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "project"


def workspace_path(project_id: str) -> Path:
    return settings.resolved_workspaces_dir / project_id


def ensure_workspace(project_id: str) -> Path:
    path = workspace_path(project_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / "data").mkdir(exist_ok=True)
    (path / "app").mkdir(exist_ok=True)
    return path


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


def run_cmd(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> tuple[int, str]:
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(
        cmd,
        cwd=cwd,
        env=merged_env,
        capture_output=True,
        text=True,
    )
    output = (result.stdout or "") + (result.stderr or "")
    return result.returncode, output.strip()


def clone_or_pull(project_id: str, git_url: str | None, branch: str) -> tuple[bool, str]:
    ws = ensure_workspace(project_id)
    repo_dir = ws / "app"

    if not git_url:
        repo_dir.mkdir(parents=True, exist_ok=True)
        return True, "Workspace ready (no git repository configured)."

    if (repo_dir / ".git").exists():
        code, out = run_cmd(["git", "fetch", "origin"], cwd=repo_dir)
        if code != 0:
            return False, out
        code, out = run_cmd(["git", "checkout", branch], cwd=repo_dir)
        if code != 0:
            return False, out
        code, out = run_cmd(["git", "pull", "origin", branch], cwd=repo_dir)
        return code == 0, out or "Repository updated."

    if repo_dir.exists():
        shutil.rmtree(repo_dir)

    code, out = run_cmd(
        ["git", "clone", "--branch", branch, "--depth", "1", git_url, str(repo_dir)]
    )
    if code != 0:
        code, out = run_cmd(["git", "clone", git_url, str(repo_dir)])
        if code == 0:
            run_cmd(["git", "checkout", branch], cwd=repo_dir)
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
