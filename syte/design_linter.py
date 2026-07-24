"""Automated design contract validation for generated Next.js projects."""

import json
import re
from pathlib import Path

from syte.design_contract import PREFLIGHT_CHECKLIST
from syte.workspace import workspace_path


def _find_app_root(project_id: str) -> Path | None:
    app = workspace_path(project_id) / "app"
    if not app.exists():
        return None
    for candidate in (app, app / "src"):
        if (candidate / "package.json").exists() or (candidate / "app").exists():
            return app
    return app if any(app.iterdir()) else None


def _read_text(path: Path) -> str:
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""


def validate_design(project_id: str) -> dict:
    """Run design contract checks against project workspace. Returns pass/fail per item."""
    app = _find_app_root(project_id)
    checks: list[dict] = []

    if not app or not app.exists():
        return {
            "ok": False,
            "uuid": project_id,
            "passed": 0,
            "total": len(PREFLIGHT_CHECKLIST),
            "checks": [{"item": "Project has app/ workspace", "ok": False, "detail": "No files yet"}],
        }

    pkg_path = app / "package.json"
    if not pkg_path.exists():
        pkg_path = next(app.rglob("package.json"), None)

    pkg_text = _read_text(pkg_path) if pkg_path else ""
    pkg: dict = {}
    try:
        pkg = json.loads(pkg_text) if pkg_text else {}
    except json.JSONDecodeError:
        pass

    deps = {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}

    globals_candidates = list(app.rglob("globals.css"))
    globals_css = _read_text(globals_candidates[0]) if globals_candidates else ""

    ui_dir = app / "components" / "ui"
    if not ui_dir.exists():
        ui_dir = next(app.rglob("components/ui"), None)

    tsx_files = list(app.rglob("*.tsx"))[:100]
    tsx_sources = [(path, _read_text(path)) for path in tsx_files]
    all_tsx = "\n".join(source for _, source in tsx_sources)
    application_sources = [
        (path, source)
        for path, source in tsx_sources
        if not ui_dir or ui_dir not in path.parents
    ]
    all_application_tsx = "\n".join(source for _, source in application_sources)

    # Named theme / CSS variable tokens present (soft: pass if radius + primary exist)
    theme_ok = "--radius" in globals_css and ("--primary" in globals_css or "--background" in globals_css)
    checks.append({
        "item": PREFLIGHT_CHECKLIST[0],
        "ok": theme_ok,
        "detail": "Theme CSS variables present" if theme_ok else "Apply a named theme (minimal|bold|corporate|vibrant|dark-tech)",
    })

    # --font-sans
    font_ok = "--font-sans" in globals_css and ("font-family" in globals_css or "font-sans" in globals_css)
    checks.append({
        "item": PREFLIGHT_CHECKLIST[1],
        "ok": font_ok,
        "detail": "Found --font-sans in globals.css" if font_ok else "Missing --font-sans in globals.css",
    })

    # card != background
    card_bg_ok = "--card" in globals_css and "--background" in globals_css
    if card_bg_ok and re.search(r"--card:\s*[^;]+", globals_css) and re.search(r"--background:\s*[^;]+", globals_css):
        card_vals = re.findall(r"--card:\s*([^;]+)", globals_css)
        bg_vals = re.findall(r"--background:\s*([^;]+)", globals_css)
        card_bg_ok = bool(card_vals and bg_vals and card_vals[0].strip() != bg_vals[0].strip())
    checks.append({
        "item": PREFLIGHT_CHECKLIST[2],
        "ok": card_bg_ok,
        "detail": "card and background differ" if card_bg_ok else "Set distinct --card and --background",
    })

    # shadcn preset (tailwind + components/ui)
    shadcn_ok = bool(ui_dir and ui_dir.exists()) and ("tailwindcss" in deps or (app / "tailwind.config.ts").exists() or (app / "tailwind.config.js").exists())
    forbidden_ui = sorted(
        name for name in (
            "@heroui/react",
            "@heroui/system",
            "@nextui-org/react",
            "@chakra-ui/react",
            "@mui/material",
            "antd",
            "@mantine/core",
        )
        if name in deps
    )
    if forbidden_ui:
        shadcn_ok = False
    heroui_import = bool(re.search(
        r"""from\s+['"]@heroui/|from\s+['"]@nextui-org/|from\s+['"]@chakra-ui/|from\s+['"]@mui/""",
        all_tsx,
    ))
    if heroui_import:
        shadcn_ok = False
    detail = "components/ui + tailwind present"
    if forbidden_ui:
        detail = f"Remove forbidden UI kits: {', '.join(forbidden_ui)}; use shadcn/ui only"
    elif heroui_import:
        detail = "Remove HeroUI/NextUI/Chakra/MUI imports; use @/components/ui/* (shadcn)"
    elif not shadcn_ok:
        detail = "Add shadcn/ui (components/ui) and Tailwind"
    checks.append({
        "item": PREFLIGHT_CHECKLIST[3],
        "ok": shadcn_ok,
        "detail": detail,
    })

    # Primitive discipline: page/template Blocks are intentionally excluded,
    # and direct Radix imports belong behind local components/ui wrappers.
    block_pattern = re.compile(
        r"(?:from\s+['\"][^'\"]*(?:/blocks?/|components/blocks)|registry[^'\"]*/block)",
        re.I,
    )
    direct_radix_pattern = re.compile(
        r"from\s+['\"](?:@radix-ui/|radix-ui['\"])",
        re.I,
    )
    block_files = sorted({
        str(path.relative_to(app))
        for path, source in tsx_sources
        if block_pattern.search(source) or "blocks" in {part.lower() for part in path.parts}
    })
    radix_files = [
        str(path.relative_to(app))
        for path, source in application_sources
        if direct_radix_pattern.search(source)
    ]
    primitive_ok = not block_files and not radix_files
    primitive_details: list[str] = []
    if block_files:
        primitive_details.append("remove shadcn Block/template imports: " + ", ".join(block_files[:5]))
    if radix_files:
        primitive_details.append("move direct Radix use behind components/ui wrappers: " + ", ".join(radix_files[:5]))
    checks.append({
        "item": PREFLIGHT_CHECKLIST[11],
        "ok": primitive_ok,
        "detail": "; ".join(primitive_details) if primitive_details else "Individual shadcn primitives; Radix stays behind components/ui",
    })

    # Application code should consume shadcn controls instead of recreating
    # their behavior. Raw controls remain valid inside components/ui wrappers.
    raw_controls = sorted(set(re.findall(
        r"<(button|input|select|textarea)\b",
        all_application_tsx,
        re.I,
    )))
    if re.search(
        r"<(?:div|span)\b[^>]*(?:role\s*=\s*['\"]button['\"]|onClick\s*=)",
        all_application_tsx,
        re.I,
    ):
        raw_controls.append("clickable div/span")
    raw_controls_ok = not raw_controls
    checks.append({
        "item": PREFLIGHT_CHECKLIST[12],
        "ok": raw_controls_ok,
        "detail": (
            "Application controls use components/ui"
            if raw_controls_ok
            else "Replace raw application controls with shadcn components: " + ", ".join(raw_controls)
        ),
    })

    # lucide-react
    lucide_ok = "lucide-react" in deps or "lucide-react" in all_tsx
    circle_icon_bad = bool(re.search(r"rounded-full.*lucide|bg-primary.*h-\d.*w-\d.*flex.*items-center", all_tsx, re.I))
    icon_ok = lucide_ok and not circle_icon_bad
    checks.append({
        "item": PREFLIGHT_CHECKLIST[5],
        "ok": icon_ok,
        "detail": "lucide-react used" if icon_ok else "Use lucide-react; avoid icons in colored circles",
    })

    # dark mode
    dark_ok = bool(re.search(r"ThemeProvider|dark:|useTheme|mode-toggle|DarkMode", all_tsx + globals_css, re.I))
    checks.append({
        "item": PREFLIGHT_CHECKLIST[9],
        "ok": dark_ok,
        "detail": "Dark mode toggle/provider found" if dark_ok else "Add dark mode toggle (next-themes or similar)",
    })

    # --radius
    radius_ok = "--radius" in globals_css or "rounded-lg" in all_tsx
    checks.append({
        "item": PREFLIGHT_CHECKLIST[8],
        "ok": radius_ok,
        "detail": "--radius token or rounded-lg used" if radius_ok else "Use --radius token consistently",
    })

    # images with alt
    img_tags = re.findall(r"<img[^>]*>", all_tsx, re.I)
    img_ok = not img_tags or all("alt=" in t for t in img_tags)
    checks.append({
        "item": PREFLIGHT_CHECKLIST[7],
        "ok": img_ok,
        "detail": "All img tags have alt" if img_ok else "Add alt to every img",
    })

    passed = sum(1 for c in checks if c["ok"])
    return {
        "ok": passed == len(checks),
        "uuid": project_id,
        "passed": passed,
        "total": len(checks),
        "checks": checks,
        "preflight_checklist": PREFLIGHT_CHECKLIST,
    }
