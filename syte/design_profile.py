"""Build and persist per-project design systems from themes or visual blueprints."""

from __future__ import annotations

import json
from typing import Any

from syte.design_contract import DESIGN_THEMES


STYLE_PROFILES: dict[str, dict[str, Any]] = {
    "saas-minimal": {
        "label": "SaaS minimal",
        "theme_key": "minimal",
        "constraints": (
            "Prefer max-w-6xl centered layouts, large hero, strong top padding, big CTA, "
            "2–3 color palette, subtle gradients, shadcn-style cards used sparingly."
        ),
    },
    "fintech-dark": {
        "label": "Fintech dark",
        "theme_key": "dark-tech",
        "constraints": (
            "Dark backgrounds, cyan/teal accent, mono labels, dense but clear data sections, "
            "trustworthy spacing, high-contrast CTAs."
        ),
    },
    "ai-landing": {
        "label": "AI landing",
        "theme_key": "bold",
        "constraints": (
            "Expressive display type, full-bleed hero atmosphere, one dominant visual, "
            "minimal first-viewport clutter, clear single CTA."
        ),
    },
    "dashboard": {
        "label": "Dashboard",
        "theme_key": "corporate",
        "constraints": (
            "App-shell layout with sidebar or top nav, dense cards only where interactive, "
            "consistent table/form density, muted neutrals + one accent."
        ),
    },
    "ecommerce-grid": {
        "label": "E-commerce grid",
        "theme_key": "vibrant",
        "constraints": (
            "Product grid with clear cards, sticky cart/CTA patterns, strong imagery, "
            "filterable layout, mobile-first product tiles."
        ),
    },
}


def css_from_theme(theme: dict[str, Any]) -> str:
    vars_map = theme.get("css_vars") or {}
    lines = [":root {"]
    for key, value in vars_map.items():
        # Map contract HSL triples into usable custom properties plus aliases.
        lines.append(f"  {key}: {value};")
    # Friendly aliases for agent instructions.
    if "--primary" in vars_map:
        lines.append("  --color-primary: hsl(var(--primary));")
    if "--background" in vars_map:
        lines.append("  --color-background: hsl(var(--background));")
    if "--foreground" in vars_map:
        lines.append("  --color-foreground: hsl(var(--foreground));")
    if "--accent" in vars_map:
        lines.append("  --color-accent: hsl(var(--accent));")
    if "--radius" in vars_map:
        lines.append("  --radius-base: var(--radius);")
    lines.append("}")
    fonts = theme.get("fonts") or {}
    if fonts:
        lines.append("")
        lines.append("/* Font pairing */")
        lines.append(f"/* sans: {fonts.get('sans')}; display: {fonts.get('display')} */")
    return "\n".join(lines) + "\n"


def tokens_from_theme(theme_key: str, theme: dict[str, Any]) -> dict[str, Any]:
    return {
        "theme_key": theme_key,
        "label": theme.get("label"),
        "preset": theme.get("preset"),
        "accent": theme.get("accent"),
        "fonts": theme.get("fonts") or {},
        "radius": theme.get("radius"),
        "shadow": theme.get("shadow"),
        "css_vars": theme.get("css_vars") or {},
        "spacing_scale": ["0.25rem", "0.5rem", "0.75rem", "1rem", "1.5rem", "2rem", "3rem", "4rem"],
        "layout": {
            "max_width": "72rem",
            "section_y": "4rem",
            "container": "mx-auto max-w-6xl px-4",
        },
        "notes": theme.get("notes") or "",
    }


def agent_instructions_for(
    *,
    theme_key: str,
    style_key: str = "",
    constraints: str = "",
    blueprint: dict[str, Any] | None = None,
) -> str:
    style = STYLE_PROFILES.get(style_key) or {}
    parts = [
        "AI Agent Instructions — design system",
        f"1. Apply theme `{theme_key}` CSS variables before building pages.",
        "2. Use var(--color-primary) / hsl(var(--primary)) consistently; do not invent one-off hex palettes.",
        "3. Follow the spacing scale; keep section padding and container width consistent.",
        "4. Prefer shadcn/ui + Lucide; avoid generic icon-in-circle feature rows.",
        "5. First viewport: brand, one headline, one supporting sentence, one CTA group, one dominant visual.",
    ]
    constraint_text = constraints or style.get("constraints") or ""
    if constraint_text:
        parts.append(f"6. Style profile constraints: {constraint_text}")
    if blueprint:
        parts.append(
            "7. Target design blueprint (match patterns, not wording):\n"
            + json.dumps(blueprint, indent=2)[:2000]
        )
    return "\n".join(parts)


async def apply_theme_profile(
    project_id: str,
    *,
    theme_key: str | None = None,
    style_key: str | None = None,
    blueprint: dict[str, Any] | None = None,
    source: str = "theme",
) -> dict[str, Any]:
    """Persist a design profile derived from a named theme and/or style profile."""
    from syte.agent_memory import save_design_profile

    style = STYLE_PROFILES.get(style_key or "") or {}
    resolved_theme = (theme_key or style.get("theme_key") or "minimal").strip()
    if resolved_theme not in DESIGN_THEMES:
        resolved_theme = "minimal"
    theme = DESIGN_THEMES[resolved_theme]
    tokens = tokens_from_theme(resolved_theme, theme)
    css = css_from_theme(theme)
    instructions = agent_instructions_for(
        theme_key=resolved_theme,
        style_key=style_key or "",
        constraints=str(style.get("constraints") or ""),
        blueprint=blueprint,
    )
    # Also write design-tokens.json into the workspace when possible.
    await _write_workspace_design_files(project_id, tokens=tokens, css=css, instructions=instructions)
    return await save_design_profile(
        project_id,
        style_key=style_key or "",
        theme_key=resolved_theme,
        design_tokens=tokens,
        design_system_css=css,
        agent_instructions=instructions,
        reference_blueprint=blueprint or {},
        source=source,
    )


async def apply_blueprint_profile(
    project_id: str,
    blueprint: dict[str, Any],
    *,
    theme_key: str = "minimal",
    style_key: str = "saas-minimal",
) -> dict[str, Any]:
    return await apply_theme_profile(
        project_id,
        theme_key=theme_key,
        style_key=style_key,
        blueprint=blueprint,
        source="blueprint",
    )


async def _write_workspace_design_files(
    project_id: str,
    *,
    tokens: dict[str, Any],
    css: str,
    instructions: str,
) -> None:
    from syte.workspace import ensure_workspace

    root = ensure_workspace(project_id) / "data" / "design-system"
    root.mkdir(parents=True, exist_ok=True)
    (root / "design-tokens.json").write_text(
        json.dumps(tokens, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (root / "design-system.css").write_text(css, encoding="utf-8")
    (root / "design-system.md").write_text(
        "# Design system\n\n## AI Agent Instructions\n\n" + instructions + "\n",
        encoding="utf-8",
    )


def list_style_profiles() -> list[dict[str, Any]]:
    return [
        {"key": key, "label": val["label"], "theme_key": val["theme_key"], "constraints": val["constraints"]}
        for key, val in STYLE_PROFILES.items()
    ]
