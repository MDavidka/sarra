import os
import shutil

from syte.workspace import command_exists, run_cmd


def ensure_npm() -> tuple[bool, str]:
    """Install npm on the VM when missing (requires root)."""
    if command_exists("npm"):
        return True, "npm is available"

    if hasattr(os, "geteuid") and os.geteuid() != 0:
        return False, (
            "npm is not installed. Run on the server: "
            "sudo apt install -y nodejs npm"
        )

    messages = ["Installing nodejs and npm…"]
    run_cmd(["apt-get", "update", "-qq"])
    code, out = run_cmd(["apt-get", "install", "-y", "nodejs", "npm"])
    messages.append(out or "apt install nodejs npm")
    if code == 0 and command_exists("npm"):
        return True, "npm installed successfully."

    code, out = run_cmd(["bash", "-c", "curl -fsSL https://deb.nodesource.com/setup_20.x | bash -"])
    messages.append(out or "nodesource setup")
    code, out = run_cmd(["apt-get", "install", "-y", "nodejs"])
    messages.append(out or "apt install nodejs (nodesource)")
    if command_exists("npm"):
        return True, "\n".join(messages)

    return False, (
        "Could not install npm automatically.\n"
        + "\n".join(messages)
        + "\nRun: sudo apt install -y nodejs npm"
    )


def ensure_runtime_for_command(start_command: str) -> tuple[bool, str]:
    """Install missing runtimes needed for a shell start command."""
    cmd = (start_command or "").lower()
    if "npm" in cmd or "npx" in cmd:
        return ensure_npm()
    return True, ""
