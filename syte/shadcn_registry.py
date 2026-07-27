"""Live shadcn/ui registry access — so the agent stops guessing component code.

The design contract already *names* the 57 allowed components, but naming is not
knowing: without the real registry the model writes props and sub-component
names from memory, which is exactly where hallucinated APIs and generic markup
come from. shadcn's CLI v4 is explicitly built for coding agents and exposes the
commands we need:

* ``info --json``   — this project's framework, aliases, base library, icon
                      library and which components are already installed
* ``search @reg``   — scored search across configured registries
* ``view <items>``  — the real source of a registry item before installing it
* ``docs <c>``      — the actual API reference for a component
* ``add <items>``   — install real component source (supports ``--dry-run``)
* ``apply <preset>``— apply a preset's theme/fonts

Every action degrades gracefully: when the CLI is unavailable or offline we fall
back to the public registry JSON / docs pages, and finally to the pinned local
catalog, so the tool never dead-ends a turn.

Reference: shadcn CLI docs (https://ui.shadcn.com/docs/cli),
shadcn MCP server (https://ui.shadcn.com/docs/mcp),
shadcn agent skills (https://ui.shadcn.com/docs/skills).
Content was rephrased for compliance with licensing restrictions.
"""

from __future__ import annotations

import json
import logging
import shlex
import time
from typing import Any

from syte.workspace import workspace_path

logger = logging.getLogger(__name__)

CLI = "npx --yes shadcn@latest"
CLI_TIMEOUT_S = 150
DOCS_URL = "https://ui.shadcn.com/docs/components/{slug}"
# Public registry item URLs, tried in order. The bare `/r/{name}.json` form is
# what custom registries publish; ui.shadcn.com serves items under a style path.
REGISTRY_ITEM_URLS = (
    "https://ui.shadcn.com/r/styles/new-york/{name}.json",
    "https://ui.shadcn.com/r/styles/default/{name}.json",
    "https://ui.shadcn.com/r/{name}.json",
)
MAX_OUTPUT_CHARS = 24_000

ACTIONS = ("info", "search", "view", "docs", "add", "apply_preset", "presets")

# Cache CLI/network answers per project for one turn's worth of time. Registry
# content is stable and each `npx` cold start costs seconds.
_CACHE_TTL_S = 900.0
_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_MAX_CACHE_ENTRIES = 256


def _cache_get(key: str) -> dict[str, Any] | None:
    hit = _cache.get(key)
    if not hit:
        return None
    stamped, value = hit
    if (time.monotonic() - stamped) > _CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: dict[str, Any]) -> dict[str, Any]:
    if value.get("ok"):
        if len(_cache) >= _MAX_CACHE_ENTRIES:
            for stale in list(_cache.keys())[: _MAX_CACHE_ENTRIES // 4]:
                _cache.pop(stale, None)
        _cache[key] = (time.monotonic(), value)
    return value


def clear_cache() -> None:
    _cache.clear()


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + "\n… [truncated]"


def _app_dir(project_id: str) -> str:
    """Directory that holds components.json (``app/`` in a Syte workspace)."""
    root = workspace_path(project_id)
    if (root / "app" / "components.json").exists():
        return "app"
    if (root / "components.json").exists():
        return "."
    return "app"


def project_components_json(project_id: str) -> dict[str, Any]:
    """Read components.json without shelling out (offline fallback for ``info``)."""
    root = workspace_path(project_id)
    for candidate in (root / "app" / "components.json", root / "components.json"):
        try:
            if candidate.exists():
                return json.loads(candidate.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
    return {}


def installed_components(project_id: str) -> list[str]:
    """List component files already present under ``components/ui``."""
    root = workspace_path(project_id)
    for base in (root / "app", root):
        for rel in ("components/ui", "src/components/ui"):
            directory = base / rel
            if not directory.is_dir():
                continue
            try:
                return sorted(
                    path.stem
                    for path in directory.iterdir()
                    if path.is_file() and path.suffix in {".tsx", ".ts", ".jsx", ".js"}
                )
            except OSError:
                continue
    return []


async def _run_cli(project_id: str, args: str, *, timeout: int = CLI_TIMEOUT_S) -> tuple[int, str]:
    from syte.workspace_api import execute_command

    return await execute_command(
        project_id,
        f"{CLI} {args}",
        cwd=_app_dir(project_id),
        timeout=timeout,
        source="agent",
    )


def _parse_json_output(output: str) -> Any:
    """Extract the JSON document from CLI output that may include log noise."""
    text = (output or "").strip()
    if not text:
        return None
    for opener, closer in (("{", "}"), ("[", "]")):
        start = text.find(opener)
        end = text.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
    return None


async def _registry_item(name: str) -> dict[str, Any] | None:
    """Fetch one registry item's JSON straight from the public registry."""
    from syte.web_access import fetch_url

    slug = name.strip().lstrip("@").split("/")[-1]
    for template in REGISTRY_ITEM_URLS:
        result = await fetch_url(
            template.format(name=slug), extract="raw", max_chars=60_000
        )
        if not result.get("ok"):
            continue
        parsed = _parse_json_output(str(result.get("content") or ""))
        if isinstance(parsed, dict) and parsed.get("name"):
            return parsed
    return None


def _catalog_entries(query: str = "") -> list[dict[str, str]]:
    from syte.design_contract import SHADCN_COMPONENT_CATALOG

    needle = (query or "").strip().lower()
    if not needle:
        return list(SHADCN_COMPONENT_CATALOG)
    return [
        entry
        for entry in SHADCN_COMPONENT_CATALOG
        if needle in entry["name"].lower() or needle in entry.get("usage", "").lower()
    ]


async def info(project_id: str) -> dict[str, Any]:
    """Project-aware shadcn context: framework, aliases, base, installed items."""
    key = f"info:{project_id}"
    cached = _cache_get(key)
    if cached:
        return cached

    code, output = await _run_cli(project_id, "info --json", timeout=90)
    parsed = _parse_json_output(output) if code == 0 else None
    local = project_components_json(project_id)
    payload: dict[str, Any] = {
        "ok": True,
        "action": "info",
        "installed_components": installed_components(project_id),
        "components_json": local,
        "source": "cli" if isinstance(parsed, dict) else "components.json",
    }
    if isinstance(parsed, dict):
        payload["info"] = parsed
    else:
        payload["cli_available"] = False
        payload["cli_output"] = _clip(output, 2000)
        payload["note"] = (
            "shadcn CLI info unavailable — using components.json + on-disk component "
            "files. Run `npx shadcn@latest init` if this project has no components.json."
        )
    return _cache_put(key, payload)


async def search(
    project_id: str,
    query: str,
    *,
    registries: list[str] | None = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Search configured registries for components matching ``query``."""
    regs = [r.strip() for r in (registries or ["@shadcn"]) if str(r).strip()]
    regs = [r if r.startswith("@") or "://" in r else f"@{r}" for r in regs] or ["@shadcn"]
    limit = max(1, min(int(limit or 25), 100))
    key = f"search:{project_id}:{'|'.join(regs)}:{query.strip().lower()}:{limit}"
    cached = _cache_get(key)
    if cached:
        return cached

    args = " ".join(shlex.quote(r) for r in regs)
    cli_args = f"search {args} --limit {limit}"
    if query.strip():
        cli_args += f" --query {shlex.quote(query.strip())}"
    code, output = await _run_cli(project_id, cli_args)
    if code == 0 and output.strip():
        return _cache_put(key, {
            "ok": True,
            "action": "search",
            "query": query,
            "registries": regs,
            "source": "cli",
            "results": _clip(output),
            "next": "Call action=view on the promising item names before writing any JSX.",
        })

    entries = _catalog_entries(query)[:limit]
    return {
        "ok": True,
        "action": "search",
        "query": query,
        "registries": regs,
        "source": "local_catalog",
        "cli_output": _clip(output, 1500),
        "results": entries,
        "note": (
            "Registry search unavailable (offline or CLI missing) — returned the pinned "
            "contract catalog. Use action=view to pull real source when connectivity returns."
        ),
    }


async def view(project_id: str, items: list[str]) -> dict[str, Any]:
    """Show the real source/metadata of registry items before installing them."""
    names = [str(i).strip() for i in (items or []) if str(i).strip()][:8]
    if not names:
        return {
            "ok": False,
            "error": "missing_items",
            "message": "Pass items, e.g. items=['button','navigation-menu'] or ['@acme/hero'].",
        }
    key = f"view:{project_id}:{','.join(sorted(names))}"
    cached = _cache_get(key)
    if cached:
        return cached

    args = " ".join(shlex.quote(name) for name in names)
    code, output = await _run_cli(project_id, f"view {args}")
    if code == 0 and output.strip():
        return _cache_put(key, {
            "ok": True,
            "action": "view",
            "items": names,
            "source": "cli",
            "content": _clip(output),
        })

    fetched: dict[str, Any] = {}
    for name in names:
        item = await _registry_item(name)
        if item:
            fetched[name] = {
                "name": item.get("name"),
                "type": item.get("type"),
                "dependencies": item.get("dependencies") or [],
                "registryDependencies": item.get("registryDependencies") or [],
                "files": [
                    {
                        "path": entry.get("path"),
                        "type": entry.get("type"),
                        "content": _clip(str(entry.get("content") or ""), 8000),
                    }
                    for entry in (item.get("files") or [])[:4]
                ],
            }
    if fetched:
        return _cache_put(key, {
            "ok": True,
            "action": "view",
            "items": names,
            "source": "registry_http",
            "items_detail": fetched,
        })
    return {
        "ok": False,
        "error": "view_failed",
        "action": "view",
        "items": names,
        "message": "Could not read these items from the CLI or the public registry.",
        "cli_output": _clip(output, 2000),
    }


async def docs(project_id: str, component: str) -> dict[str, Any]:
    """Fetch the real API reference for a component."""
    name = str(component or "").strip()
    if not name:
        return {
            "ok": False,
            "error": "missing_component",
            "message": "Pass component, e.g. component='navigation-menu'.",
        }
    key = f"docs:{project_id}:{name.lower()}"
    cached = _cache_get(key)
    if cached:
        return cached

    code, output = await _run_cli(project_id, f"docs {shlex.quote(name)} --json")
    parsed = _parse_json_output(output) if code == 0 else None
    if parsed:
        return _cache_put(key, {
            "ok": True, "action": "docs", "component": name,
            "source": "cli", "docs": parsed,
        })
    if code == 0 and output.strip():
        return _cache_put(key, {
            "ok": True, "action": "docs", "component": name,
            "source": "cli", "docs": _clip(output),
        })

    from syte.web_access import fetch_url

    slug = name.lower().replace(" ", "-").replace("_", "-")
    page = await fetch_url(DOCS_URL.format(slug=slug), extract="text", max_chars=24_000)
    if page.get("ok"):
        return _cache_put(key, {
            "ok": True,
            "action": "docs",
            "component": name,
            "source": "docs_site",
            "url": page.get("url"),
            "docs": page.get("content"),
        })
    return {
        "ok": False,
        "error": "docs_failed",
        "action": "docs",
        "component": name,
        "message": "Component docs unavailable from the CLI or the docs site.",
        "cli_output": _clip(output, 1500),
    }


async def add(
    project_id: str,
    items: list[str],
    *,
    dry_run: bool = False,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Install real registry component source into ``components/ui``."""
    names = [str(i).strip() for i in (items or []) if str(i).strip()][:20]
    if not names:
        return {
            "ok": False,
            "error": "missing_items",
            "message": "Pass items, e.g. items=['button','card'].",
        }
    args = " ".join(shlex.quote(name) for name in names)
    flags = "--yes"
    if dry_run:
        flags += " --dry-run"
    if overwrite:
        flags += " --overwrite"
    code, output = await _run_cli(project_id, f"add {args} {flags}", timeout=300)
    ok = code == 0
    result: dict[str, Any] = {
        "ok": ok,
        "action": "add",
        "items": names,
        "dry_run": bool(dry_run),
        "exit_code": code,
        "output": _clip(output),
    }
    if not ok:
        result["error"] = "add_failed"
        result["message"] = (
            "shadcn add failed. Confirm components.json exists (run init first) and that "
            "the item names came from action=search / action=view."
        )
    else:
        clear_cache()
        result["installed_components"] = installed_components(project_id)
        result["next"] = (
            "Import from @/components/ui/* and compose original sections — do not paste "
            "a registry block wholesale."
        )
    return result


async def apply_preset(
    project_id: str, preset: str, *, only: str = ""
) -> dict[str, Any]:
    """Apply a shadcn preset (optionally only its ``theme`` or ``font``)."""
    code_name = str(preset or "").strip()
    if not code_name:
        return {
            "ok": False,
            "error": "missing_preset",
            "message": "Pass preset (a preset name or code).",
        }
    args = f"apply {shlex.quote(code_name)} --yes"
    scope = (only or "").strip().lower()
    if scope in {"theme", "font", "theme,font", "font,theme"}:
        args += f" --only {shlex.quote(scope)}"
    code, output = await _run_cli(project_id, args, timeout=240)
    return {
        "ok": code == 0,
        "action": "apply_preset",
        "preset": code_name,
        "only": scope or None,
        "exit_code": code,
        "output": _clip(output),
        **({} if code == 0 else {
            "error": "apply_failed",
            "message": "Preset apply failed — check the preset code with action=presets.",
        }),
    }


async def presets(project_id: str) -> dict[str, Any]:
    """Resolve the preset currently applied to this project."""
    code, output = await _run_cli(project_id, "preset resolve --json", timeout=90)
    parsed = _parse_json_output(output) if code == 0 else None
    from syte.design_contract import DESIGN_THEMES

    return {
        "ok": True,
        "action": "presets",
        "resolved": parsed if parsed is not None else None,
        "cli_output": None if parsed is not None else _clip(output, 2000),
        "syte_themes": {
            key: {
                "label": theme["label"],
                "preset": theme["preset"],
                "accent": theme["accent"],
                "fonts": theme["fonts"],
                "radius": theme["radius"],
            }
            for key, theme in DESIGN_THEMES.items()
        },
    }


async def run_action(project_id: str, args: dict[str, Any]) -> dict[str, Any]:
    """Dispatch the ``shadcn_registry`` tool."""
    action = str(args.get("action") or "").strip().lower()
    if action not in ACTIONS:
        return {
            "ok": False,
            "error": "unknown_action",
            "message": f"action must be one of: {', '.join(ACTIONS)}",
        }
    items = args.get("items")
    if isinstance(items, str):
        items = [part.strip() for part in items.replace(",", " ").split() if part.strip()]
    if not isinstance(items, list):
        items = []

    if action == "info":
        return await info(project_id)
    if action == "search":
        registries = args.get("registries")
        if isinstance(registries, str):
            registries = [registries]
        return await search(
            project_id,
            str(args.get("query") or ""),
            registries=registries if isinstance(registries, list) else None,
            limit=int(args.get("limit") or 25),
        )
    if action == "view":
        return await view(project_id, items)
    if action == "docs":
        return await docs(project_id, str(args.get("component") or (items[0] if items else "")))
    if action == "add":
        return await add(
            project_id,
            items,
            dry_run=bool(args.get("dry_run")),
            overwrite=bool(args.get("overwrite")),
        )
    if action == "apply_preset":
        return await apply_preset(
            project_id,
            str(args.get("preset") or ""),
            only=str(args.get("only") or ""),
        )
    return await presets(project_id)
