"""Workspace file operations and command execution (sandboxed)."""

import asyncio
import os
import re
import shlex
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterable

from syte.config import settings
from syte.database import get_project, list_projects, update_project
from syte.domain_utils import build_direct_url, build_https_url, normalize_domain
from syte.project_enrich import enrich_ssl
from syte.upload_limits import MAX_UPLOAD_BYTES
from syte.workspace import ensure_workspace, read_env_vars, workspace_path, write_env_file

# Block catastrophic host-wide commands. Prefer Docker deploy for isolation;
# this blocklist is defense-in-depth only and is not a complete sandbox.
BLOCKED_PATTERNS = (
    "rm -rf /",
    "rm -rf /*",
    "mkfs.",
    ":(){ :|:& };:",
    "dd if=/dev/zero of=/dev/",
    "> /dev/sda",
    "wget http",
    "curl http | sh",
    "curl http | bash",
    "| sh",
    "| bash",
    "|sh",
    "|bash",
    "| /bin/sh",
    "| /bin/bash",
    "/dev/tcp/",
    "nc -e",
    "ncat -e",
)

MAX_COMMAND_LENGTH = 8_000
COMMAND_ALLOWLIST = frozenset({
    "npm", "npx", "yarn", "pnpm", "bun", "node",
    "python", "python3", "pip", "pip3", "pipx", "uv", "uvx",
    "git", "ls", "cat", "mkdir", "rm", "cp", "mv", "touch", "chmod",
    "echo", "pwd", "which", "head", "tail", "wc", "find", "grep", "rg",
    "sed", "awk", "curl", "wget", "cargo", "rustc", "go", "make",
    "tsc", "eslint", "prettier", "vitest", "jest", "pytest", "ruff",
    "mypy", "black", "isort", "true", "false", "test", "sleep", "sort",
    "uniq", "tr", "cut", "tee", "diff", "tar", "unzip", "zip", "jq",
    "env", "printenv", "date", "uname", "basename", "dirname", "realpath",
    "sha256sum", "openssl", "xargs", "patch", "gzip", "gunzip",
    "du", "df", "stat", "file", "id", "whoami", "groups", "printf",
})


class UploadTooLargeError(ValueError):
    """Raised when a streamed upload exceeds MAX_UPLOAD_BYTES."""

# Production bundles belong to the deployment workflow, never agent preview testing.
FORBIDDEN_BUILD_PATTERNS = (
    r"\bnpm(?:\s+run)?\s+build\b",
    r"\bpnpm(?:\s+run)?\s+build\b",
    r"\byarn(?:\s+run)?\s+build\b",
    r"\bbun(?:\s+run)?\s+build\b",
    r"\bnext\s+build\b",
    r"\bvite\s+build\b",
)


def _resolve_workspace_path(project_id: str, rel_path: str = "") -> Path:
    """Resolve ``rel_path`` under the project workspace; reject traversal escapes.

    Uses ``Path.resolve()`` then ``relative_to(base)`` so symlink targets that
    leave the workspace are denied. Absolute inputs and null bytes are rejected
    before join.
    """
    base = workspace_path(project_id).resolve()
    if not base.exists():
        base = ensure_workspace(project_id).resolve()
    rel = (rel_path or "").strip().replace("\\", "/")
    if "\x00" in rel:
        raise ValueError("Path traversal denied — null byte in path")
    if re.match(r"^[A-Za-z]:", rel) or rel.startswith("//"):
        raise ValueError("Path traversal denied — absolute paths not allowed")
    rel = rel.lstrip("/")
    parts = [part for part in rel.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise ValueError("Path traversal denied — '..' segments not allowed")
    target = base.joinpath(*parts).resolve() if parts else base
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError("Path traversal denied — path must stay inside workspace") from exc
    return target


def _is_blocked(command: str) -> str | None:
    lower = command.lower().strip()
    for pattern in BLOCKED_PATTERNS:
        if pattern in lower:
            return pattern
    # Block shell expansion / substitution that can hide disallowed binaries.
    if "`" in command:
        return "backtick command substitution"
    if "$(" in command or "${" in command:
        return "shell command/parameter substitution"
    if "<(" in command or ">(" in command:
        return "process substitution"
    return None


def _is_env_assignment(token: str) -> bool:
    if "=" not in token or token.startswith("="):
        return False
    name = token.split("=", 1)[0]
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name))


def _command_segments(command: str) -> list[str]:
    lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;")
    lexer.whitespace_split = True
    segments: list[list[str]] = [[]]
    for token in lexer:
        if token in {";", "&&", "||", "|"}:
            segments.append([])
        else:
            segments[-1].append(token)
    return [" ".join(segment).strip() for segment in segments if segment]


def _primary_binary(segment: str) -> str | None:
    try:
        tokens = shlex.split(segment, posix=True)
    except ValueError:
        # Let the shell report quoting syntax errors; do not bypass validation.
        tokens = segment.split()
    while tokens and _is_env_assignment(tokens[0]):
        tokens.pop(0)
    if not tokens:
        return None
    return Path(tokens[0]).name


def _allowlist_violation(command: str) -> str | None:
    try:
        segments = _command_segments(command)
    except ValueError as exc:
        return f"invalid shell syntax: {exc}"
    if not segments:
        return "empty command"
    for segment in segments:
        binary = _primary_binary(segment)
        if not binary:
            return "missing command after environment assignment"
        if binary not in COMMAND_ALLOWLIST:
            return binary
    return None


def _is_forbidden_build(command: str) -> str | None:
    """Return matched pattern if command tries to build outside issue_deploy."""
    lower = command.lower().strip()
    for pattern in FORBIDDEN_BUILD_PATTERNS:
        if re.search(pattern, lower):
            return pattern
    return None


def _append_command_log(project_id: str, command: str, cwd: str, exit_code: int) -> None:
    log_path = workspace_path(project_id) / "commands.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    with log_path.open("a") as f:
        f.write(f"[{ts}] exit={exit_code} cwd={cwd} $ {command}\n")


async def workspace_get(project_id: str) -> dict | None:
    from syte import process_manager
    from syte.cloud_agent import ensure_agent_runtime, get_agent_status
    from syte.preview_manager import ensure_preview_address, preview_meta

    project = await get_project(project_id)
    if not project:
        return None
    project = await ensure_preview_address(project)
    project = await ensure_agent_runtime(project)
    ws = workspace_path(project_id)
    ip = settings.resolved_public_ip
    domain = project.get("domain") or ""
    url = build_https_url(domain) if domain else build_direct_url(ip, project["port"])
    return {
        "uuid": project["id"],
        "name": project["name"],
        "status": project.get("status", "stopped"),
        "running": process_manager.is_running(project_id, project.get("deploy_type", "shell")),
        "deploy_type": project.get("deploy_type", "shell"),
        "dockerfile_path": project.get("dockerfile_path"),
        "port": project["port"],
        "url": url,
        "direct_url": build_direct_url(ip, project["port"]),
        "domain": normalize_domain(domain) if domain else "",
        "domain_url": build_https_url(domain) if domain else "",
        "git_url": project.get("git_url"),
        "branch": project.get("branch", "main"),
        "start_command": project.get("start_command", ""),
        "env_vars": read_env_vars(project.get("env_vars", "{}")),
        "workspace_path": str(ws),
        "app_path": str(ws / "app"),
        "data_path": str(ws / "data"),
        "stream_url": f"/api/projects/{project_id}/logs/stream?live=1",
        **preview_meta(project),
        "agent": await get_agent_status(project_id),
        "ssl": enrich_ssl(project),
    }


async def workspace_list(*, concurrency: int = 10) -> list[dict]:
    """Load workspace details for all projects with bounded parallelism.

    ``workspace_get`` does per-project I/O (preview/agent/SSL). Gathering with a
    semaphore avoids the sequential N+1 stall without opening unbounded sockets.
    """
    projects = await list_projects()
    if not projects:
        return []
    limit = max(1, int(concurrency))
    sem = asyncio.Semaphore(limit)

    async def _one(project_id: str) -> dict | None:
        async with sem:
            return await workspace_get(project_id)

    details = await asyncio.gather(*(_one(p["id"]) for p in projects))
    return [detail for detail in details if detail]


async def list_workspace_files(project_id: str, subpath: str = "") -> list[dict]:
    project = await get_project(project_id)
    if not project:
        raise ValueError("Project not found")
    root = _resolve_workspace_path(project_id, subpath)
    if not root.exists():
        raise ValueError("Path not found")
    if root.is_file():
        return [{
            "name": root.name,
            "path": str(root.relative_to(workspace_path(project_id))),
            "type": "file",
            "size": root.stat().st_size,
        }]
    entries = []
    for item in sorted(root.iterdir()):
        if item.name.startswith(".") and item.name not in (".env", ".gitkeep"):
            continue
        rel = item.relative_to(workspace_path(project_id))
        entries.append({
            "name": item.name,
            "path": str(rel),
            "type": "directory" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
    return entries


async def read_file(project_id: str, file_path: str, max_bytes: int = 512_000) -> tuple[bool, str | bytes, str]:
    project = await get_project(project_id)
    if not project:
        return False, "", "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    if not target.exists():
        return False, "", f"File not found: {file_path}"
    if target.is_dir():
        return False, "", "Path is a directory — use list_files"
    size = target.stat().st_size
    if size > max_bytes:
        return False, "", f"File too large ({size} bytes). Max {max_bytes}."
    raw = target.read_bytes()
    try:
        return True, raw.decode("utf-8"), "text"
    except UnicodeDecodeError:
        return True, raw, "binary"


def _nextjs_path_advice(project_id: str, file_path: str) -> str:
    """Return a short advisory when a Next.js file looks misplaced.

    Purely advisory — it never blocks the write — so the model keeps legitimate
    edits while learning the correct App Router layout. The Next.js project root
    is the workspace ``app/`` folder, so App Router routes must live under
    ``app/app/`` (or ``app/pages/`` / ``app/src/app/``) to be picked up.
    """
    from pathlib import PurePosixPath

    rel = file_path.replace("\\", "/").strip("/")
    parts = PurePosixPath(rel).parts
    name = PurePosixPath(rel).name
    stem = name.rsplit(".", 1)[0]

    # Pages Router special files are silently ignored by the App Router.
    if stem in ("_document", "_app"):
        return (
            f"Note: '{name}' is a Pages Router file that the App Router ignores. "
            "Use app/app/layout.tsx (root layout) instead of _document/_app."
        )

    router_files = {"page", "layout", "template", "loading", "error", "not-found", "route", "default"}
    if stem not in router_files:
        return ""

    project_root = workspace_path(project_id) / "app"
    if not (project_root / "package.json").exists():
        return ""
    # parts[0] is the workspace app/ dir; the next segment must open a router dir.
    router_segment = parts[1] if len(parts) > 1 else ""
    if router_segment in ("app", "pages", "src"):
        return ""
    suggested = "/".join(("app", "app", *parts[1:])) if len(parts) > 1 else "app/app/page.tsx"
    return (
        f"Note: '{name}' only becomes a route when it lives under the Next.js app/ (or pages/) "
        f"directory. The project root is the workspace app/ folder, so this belongs at "
        f"'{suggested}', not '{rel}'."
    )


async def write_file(project_id: str, file_path: str, content: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    if content is None:
        return False, "Refusing to write: content was null. Send the full file body as a string."
    target = _resolve_workspace_path(project_id, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        return False, "Target path is a directory"

    existed = target.exists()
    prior_size = target.stat().st_size if existed else 0

    # Atomic write: stage in a sibling temp file, then os.replace() into place so
    # an interrupted write can never leave a half-written or empty file behind.
    tmp = target.with_name(f".{target.name}.syte-tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.replace(tmp, target)
    except OSError as exc:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        return False, f"Write failed for {file_path}: {exc}"

    # Verify by reading back from disk — never report success on a phantom write.
    try:
        on_disk = target.read_text(encoding="utf-8")
    except OSError as exc:
        return False, f"Wrote {file_path} but could not verify it on disk: {exc}"
    if on_disk != content:
        return False, (
            f"Verification failed for {file_path}: on-disk content ({len(on_disk)} chars) "
            f"does not match the {len(content)} chars sent. The file was not saved reliably."
        )

    from syte.agent_activity import record_workspace_activity

    await record_workspace_activity(
        project_id,
        "create_file" if not existed else "write_file",
        path=file_path,
        source="api",
    )

    rel = str(target.relative_to(workspace_path(project_id)))
    message = f"Wrote and verified {len(content)} chars to {rel}"
    if not content.strip():
        # Surface an accidental truncation instead of silently blanking a file.
        message += (
            " — WARNING: content is empty so the file is now blank"
            + (f" (previously {prior_size} bytes)" if prior_size else "")
            + ". If you did not intend to clear it, re-send the full file contents."
        )
    advice = _nextjs_path_advice(project_id, file_path)
    if advice:
        message += f"\n{advice}"
    return True, message


async def delete_file(project_id: str, file_path: str) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    target = _resolve_workspace_path(project_id, file_path)
    if not target.exists():
        return False, f"File not found: {file_path}"
    if target.is_dir():
        return False, "Use delete_directory or a file path"
    ws = workspace_path(project_id).resolve()
    if target == ws or target == ws / "app":
        return False, "Cannot delete workspace root"
    target.unlink()
    from syte.agent_activity import record_workspace_activity

    await record_workspace_activity(project_id, "delete_file", path=file_path, source="api")
    return True, f"Deleted {file_path}"


async def upload_file(project_id: str, file_path: str, content: bytes) -> tuple[bool, str]:
    if len(content or b"") > MAX_UPLOAD_BYTES:
        return False, f"Upload too large ({len(content)} bytes). Max {MAX_UPLOAD_BYTES}."

    async def _single_chunk() -> AsyncIterable[bytes]:
        yield content or b""

    try:
        ok, message, _written = await upload_file_stream(project_id, file_path, _single_chunk())
        return ok, message
    except UploadTooLargeError as exc:
        return False, str(exc)


async def upload_file_stream(
    project_id: str,
    file_path: str,
    chunks: AsyncIterable[bytes],
) -> tuple[bool, str, int]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found", 0
    target = _resolve_workspace_path(project_id, file_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_dir():
        return False, "Target path is a directory", 0
    tmp = target.with_name(f".{target.name}.syte-upload-tmp")
    written = 0
    try:
        with tmp.open("wb") as f:
            async for chunk in chunks:
                if not chunk:
                    continue
                written += len(chunk)
                if written > MAX_UPLOAD_BYTES:
                    raise UploadTooLargeError(
                        f"Upload too large ({written} bytes). Max {MAX_UPLOAD_BYTES}."
                    )
                f.write(chunk)
        os.replace(tmp, target)
    except UploadTooLargeError:
        tmp.unlink(missing_ok=True)
        raise
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        return False, f"Upload failed for {file_path}: {exc}", written

    from syte.agent_activity import record_workspace_activity

    await record_workspace_activity(project_id, "upload_file", path=file_path, source="api")
    return True, f"Uploaded {written} bytes to {file_path}", written


async def set_env_vars(project_id: str, env_vars: dict[str, str], merge: bool = True) -> tuple[bool, str]:
    project = await get_project(project_id)
    if not project:
        return False, "Project not found"
    current = read_env_vars(project.get("env_vars", "{}"))
    if merge:
        current.update(env_vars)
    else:
        current = dict(env_vars)
    write_env_file(project_id, current)
    await update_project(project_id, {"env_vars": current})
    return True, f"Environment updated ({len(current)} vars)"


async def execute_command(
    project_id: str,
    command: str,
    cwd: str = "app",
    timeout: int = 300,
    env: dict[str, str] | None = None,
    *,
    source: str = "api",
) -> tuple[int, str]:
    """Run a shell command inside the workspace (cwd sandboxed to workspace dir).

    Uses ``asyncio.create_subprocess_shell`` so long-running commands do not
    exhaust the default thread-pool executor (see DAV-36). Commands remain
    workspace-scoped; full process isolation requires Docker deploy.
    """
    project = await get_project(project_id)
    if not project:
        return 1, "Project not found"
    cmd = command.strip()
    if not cmd:
        return 1, "Empty command"
    if "\x00" in cmd:
        return 1, "Command blocked (null byte)"
    if len(cmd) > MAX_COMMAND_LENGTH:
        return 1, f"Command too long (max {MAX_COMMAND_LENGTH} chars)"
    blocked = _is_blocked(cmd)
    if blocked:
        return 1, f"Command blocked (host safety): {blocked}"
    disallowed = _allowlist_violation(cmd)
    if disallowed:
        return 1, f"Command blocked (unsupported binary): {disallowed}"

    build_blocked = _is_forbidden_build(cmd) if source not in ("gui", "mcp") else None
    if build_blocked:
        return 1, (
            "Build commands are not allowed via execute_command. "
            "Use POST /api/issue_deploy {\"uuid\": \"...\"} instead — "
            "that runs git pull + docker build (npm run build inside Dockerfile) + restart. "
            "For testing, use: npm run lint"
        )

    try:
        workdir = _resolve_workspace_path(project_id, cwd)
    except ValueError as exc:
        return 1, str(exc)
    if not workdir.is_dir():
        return 1, f"Working directory not found: {cwd}"

    merged_env = {**os.environ, **read_env_vars(project.get("env_vars", "{}")), **(env or {})}

    try:
        from syte.output_limits import TRUNCATION_MARKER, read_async_stream_limited

        proc = await asyncio.create_subprocess_shell(
            cmd,
            cwd=workdir,
            env=merged_env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_b, truncated = await asyncio.wait_for(
                read_async_stream_limited(proc.stdout),
                timeout=timeout,
            )
            await asyncio.wait_for(proc.wait(), timeout=5)
        except asyncio.CancelledError:
            # Propagate cancel promptly so pause/interrupt does not leave a
            # subprocess running after the agent turn is aborted (DAV-180).
            proc.kill()
            try:
                await proc.communicate()
            except Exception:
                pass
            raise
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await proc.communicate()
            except Exception:
                pass
            _append_command_log(project_id, cmd, cwd, 124)
            return 124, f"Command timed out after {timeout}s"
        out = (stdout_b or b"").decode("utf-8", errors="replace")
        if truncated and TRUNCATION_MARKER.strip() not in out:
            out = out + TRUNCATION_MARKER
        code = int(proc.returncode or 0)
        output = out.strip() or "(no output)"
        _append_command_log(project_id, cmd, cwd, code)
        from syte.agent_activity import record_workspace_activity

        await record_workspace_activity(
            project_id,
            "execute_command",
            command=cmd,
            source=source,
            detail=output[:500] if output else "",
        )
        return code, output
    except OSError as exc:
        _append_command_log(project_id, cmd, cwd, 1)
        return 1, f"Failed to start command: {exc}"


async def execute_commands(
    project_id: str,
    commands: list[dict],
    default_cwd: str = "app",
    env: dict[str, str] | None = None,
) -> list[dict]:
    """Run a sequence of custom commands; stops on first non-zero exit if stop_on_error."""
    results = []
    for item in commands:
        cmd = item.get("command", "")
        cwd = item.get("cwd", default_cwd)
        timeout = int(item.get("timeout", 300))
        stop_on_error = item.get("stop_on_error", True)
        code, output = await execute_command(project_id, cmd, cwd, timeout, env)
        entry = {"command": cmd, "cwd": cwd, "exit_code": code, "output": output, "ok": code == 0}
        results.append(entry)
        if stop_on_error and code != 0:
            break
    return results
