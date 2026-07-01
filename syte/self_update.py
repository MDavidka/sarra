import subprocess
import sys
from pathlib import Path

from syte import __version__
from syte.config import settings
from syte.workspace import run_cmd

INSTALL_DIR = Path(__file__).resolve().parent.parent


def _venv_pip() -> str | None:
    venv_pip = INSTALL_DIR / ".venv" / "bin" / "pip"
    return str(venv_pip) if venv_pip.exists() else None


def _current_branch() -> str:
    code, out = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=INSTALL_DIR)
    return out.strip() if code == 0 else "main"


def update_syte() -> tuple[bool, str]:
    """Pull newest Syte from git, refresh dependencies, and schedule restart."""
    messages = [f"Current version: {__version__}"]

    if not (INSTALL_DIR / ".git").exists():
        return False, "Syte install is not a git repository. Cannot pull updates."

    branch = _current_branch()
    messages.append(f"Branch: {branch}")

    code, out = run_cmd(["git", "fetch", "origin"], cwd=INSTALL_DIR)
    messages.append(out or "Fetched origin.")
    if code != 0:
        return False, "\n".join(messages)

    code, out = run_cmd(
        ["git", "pull", "--ff-only", "origin", branch],
        cwd=INSTALL_DIR,
    )
    if code != 0:
        code, out = run_cmd(["git", "pull", "--ff-only"], cwd=INSTALL_DIR)
    messages.append(out or "Repository updated.")
    if code != 0:
        return False, "\n".join(messages)

    req = INSTALL_DIR / "requirements.txt"
    if req.exists():
        pip = _venv_pip()
        if pip:
            code, out = run_cmd([pip, "install", "-r", str(req), "-q"], cwd=INSTALL_DIR)
        else:
            code, out = run_cmd(
                [sys.executable, "-m", "pip", "install", "-r", str(req), "-q"],
                cwd=INSTALL_DIR,
            )
        messages.append(out or "Dependencies updated.")
        if code != 0:
            return False, "\n".join(messages)

    _schedule_restart()
    messages.append("Syte will restart shortly to apply changes.")
    return True, "\n".join(messages)


def _schedule_restart() -> None:
    data_dir = settings.data_dir
    restart_script = (
        f"sleep 2; "
        f"cd {INSTALL_DIR} && "
        f"SYTE_DATA_DIR={data_dir} ./scripts/apply-caddy.sh 2>/dev/null || true; "
        f"./scripts/restart.sh 2>/dev/null || "
        f"systemctl restart syte 2>/dev/null || "
        f"systemctl restart syte.service 2>/dev/null"
    )
    subprocess.Popen(
        ["bash", "-c", restart_script],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
