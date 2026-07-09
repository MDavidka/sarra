"""Generate per-workspace Continue CLI config for Syra cloud agent."""

from pathlib import Path

from syte.database import get_setting

SYRA_MODELS = {
    "syra-nano": "Gemini Flash (fast)",
    "syra-base": "DeepSeek Flash (default)",
    "syra-havy": "Gemini Pro (heavy)",
}

DEFAULT_MODEL = "syra-base"


async def bridge_settings() -> tuple[str, str]:
    """Return (bridge_base_url, bridge_secret)."""
    base = (await get_setting("sycord_ai_bridge_url", "")).strip()
    if not base:
        base = "https://sycord.com/api/ai/bridge/v1"
    base = base.rstrip("/")
    secret = (await get_setting("sycord_bridge_secret", "")).strip()
    return base, secret


def continue_config_dir(project_id: str, workspace_root: Path) -> Path:
    return workspace_root / ".continue"


def continue_config_path(project_id: str, workspace_root: Path) -> Path:
    return continue_config_dir(project_id, workspace_root) / "config.yaml"


def build_continue_config_yaml(
    *,
    bridge_url: str,
    bridge_secret: str,
    default_model: str = DEFAULT_MODEL,
    workspace_dir: Path,
) -> str:
    """Build config.yaml — OpenAI-compatible provider → Sycord bridge (no Continue Hub)."""
    model = default_model if default_model in SYRA_MODELS else DEFAULT_MODEL
    bridge_url = bridge_url.rstrip("/")

    model_blocks = []
    for name, desc in SYRA_MODELS.items():
        model_blocks.append(
            f"""  - name: {name}
    provider: openai
    model: {name}
    apiBase: {bridge_url}
    apiKey: ${{ secrets.SYCORD_BRIDGE_SECRET }}
    # {desc}"""
        )

    return f"""# Syte-managed Continue config — Sycord OpenAI-compatible bridge (no Continue Hub)
name: Syra Cloud Agent
version: 1.0.0
schema: v1

models:
{chr(10).join(model_blocks)}

# Default model for cn serve
defaultModel: {model}

# Agent operates in the project workspace app directory
rules:
  - Always work inside the project workspace.
  - Use issue_deploy on Syte for production builds; do not run npm run build locally unless asked.

secrets:
  SYCORD_BRIDGE_SECRET: {bridge_secret or "unset"}

# Workspace root for tools (relative to cwd when cn serve starts)
# cwd is set to workspace/app by agent_manager
"""


async def write_continue_config(
    project_id: str,
    workspace_root: Path,
    *,
    model: str | None = None,
) -> Path:
    """Write .continue/config.yaml and secrets stub for the project."""
    bridge_url, bridge_secret = await bridge_settings()
    cfg_dir = continue_config_dir(project_id, workspace_root)
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg_path = cfg_dir / "config.yaml"
    yaml_text = build_continue_config_yaml(
        bridge_url=bridge_url,
        bridge_secret=bridge_secret,
        default_model=model or DEFAULT_MODEL,
        workspace_dir=workspace_root / "app",
    )
    cfg_path.write_text(yaml_text)
    try:
        cfg_path.chmod(0o600)
    except OSError:
        pass
    return cfg_path
