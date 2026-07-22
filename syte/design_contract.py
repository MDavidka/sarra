"""Sycord Design Contract — mandatory rules for AI-generated Next.js sites."""

DESIGN_CONTRACT_VERSION = "1.2"

# Named visual themes the agent should offer via ask_question(choice) when
# scaffolding a new website. Each theme maps to Tailwind CSS variables + fonts.
DESIGN_THEMES: dict[str, dict] = {
    "minimal": {
        "label": "Minimal",
        "preset": "Zinc",
        "accent": "neutral black/white",
        "fonts": {"sans": "Inter", "display": "Inter"},
        "radius": "0.5rem",
        "shadow": "soft",
        "css_vars": {
            "--background": "0 0% 100%",
            "--foreground": "240 10% 3.9%",
            "--card": "0 0% 98%",
            "--primary": "240 5.9% 10%",
            "--accent": "240 4.8% 95.9%",
            "--radius": "0.5rem",
        },
        "notes": "Lots of whitespace, restrained type scale, one accent at most.",
    },
    "bold": {
        "label": "Bold",
        "preset": "Neutral",
        "accent": "orange",
        "fonts": {"sans": "Inter", "display": "Space Grotesk"},
        "radius": "0.75rem",
        "shadow": "medium",
        "css_vars": {
            "--background": "0 0% 100%",
            "--foreground": "0 0% 5%",
            "--card": "0 0% 97%",
            "--primary": "24 95% 53%",
            "--accent": "24 100% 95%",
            "--radius": "0.75rem",
        },
        "notes": "Strong headlines, high-contrast CTA, generous display type.",
    },
    "corporate": {
        "label": "Corporate",
        "preset": "Slate",
        "accent": "blue",
        "fonts": {"sans": "Inter", "display": "Inter"},
        "radius": "0.375rem",
        "shadow": "soft",
        "css_vars": {
            "--background": "210 40% 98%",
            "--foreground": "222 47% 11%",
            "--card": "0 0% 100%",
            "--primary": "221 83% 53%",
            "--accent": "214 95% 93%",
            "--radius": "0.375rem",
        },
        "notes": "Trustworthy blues, tight grid, professional density.",
    },
    "vibrant": {
        "label": "Vibrant",
        "preset": "Gray",
        "accent": "fuchsia/violet",
        "fonts": {"sans": "DM Sans", "display": "DM Sans"},
        "radius": "1rem",
        "shadow": "medium",
        "css_vars": {
            "--background": "0 0% 100%",
            "--foreground": "240 10% 4%",
            "--card": "270 50% 98%",
            "--primary": "292 84% 45%",
            "--accent": "270 100% 95%",
            "--radius": "1rem",
        },
        "notes": "Playful accent, rounded controls, energetic hero imagery.",
    },
    "dark-tech": {
        "label": "Dark Tech",
        "preset": "Zinc",
        "accent": "cyan",
        "fonts": {"sans": "Inter", "display": "JetBrains Mono"},
        "radius": "0.5rem",
        "shadow": "glow-soft",
        "css_vars": {
            "--background": "240 10% 4%",
            "--foreground": "0 0% 98%",
            "--card": "240 6% 10%",
            "--primary": "189 94% 43%",
            "--accent": "240 4% 16%",
            "--radius": "0.5rem",
        },
        "notes": "Default dark; cyan accent; mono for labels; tech product feel.",
    },
}

# Compact shadcn/ui reference so the model imports real components only.
SHADCN_COMPONENT_CATALOG: list[dict[str, str]] = [
    {"name": "Button", "import": "@/components/ui/button", "usage": "<Button variant=\"default|outline|ghost|secondary|destructive\" size=\"default|sm|lg|icon\">"},
    {"name": "Card", "import": "@/components/ui/card", "usage": "Card + CardHeader + CardTitle + CardDescription + CardContent + CardFooter"},
    {"name": "Badge", "import": "@/components/ui/badge", "usage": "<Badge variant=\"default|secondary|outline|destructive\">"},
    {"name": "Input", "import": "@/components/ui/input", "usage": "<Input type=\"text|email|password\" />"},
    {"name": "Textarea", "import": "@/components/ui/textarea", "usage": "<Textarea />"},
    {"name": "Label", "import": "@/components/ui/label", "usage": "<Label htmlFor=\"…\">"},
    {"name": "Select", "import": "@/components/ui/select", "usage": "Select + SelectTrigger + SelectValue + SelectContent + SelectItem"},
    {"name": "Checkbox", "import": "@/components/ui/checkbox", "usage": "<Checkbox />"},
    {"name": "Switch", "import": "@/components/ui/switch", "usage": "<Switch />"},
    {"name": "Slider", "import": "@/components/ui/slider", "usage": "<Slider min max step />"},
    {"name": "Dialog", "import": "@/components/ui/dialog", "usage": "Dialog + DialogTrigger + DialogContent + DialogHeader + DialogTitle"},
    {"name": "Sheet", "import": "@/components/ui/sheet", "usage": "mobile drawers / side panels"},
    {"name": "DropdownMenu", "import": "@/components/ui/dropdown-menu", "usage": "menus and overflow actions"},
    {"name": "Tabs", "import": "@/components/ui/tabs", "usage": "Tabs + TabsList + TabsTrigger + TabsContent"},
    {"name": "Accordion", "import": "@/components/ui/accordion", "usage": "FAQ / collapsible sections"},
    {"name": "Avatar", "import": "@/components/ui/avatar", "usage": "Avatar + AvatarImage + AvatarFallback"},
    {"name": "Separator", "import": "@/components/ui/separator", "usage": "<Separator />"},
    {"name": "Skeleton", "import": "@/components/ui/skeleton", "usage": "loading placeholders"},
    {"name": "Table", "import": "@/components/ui/table", "usage": "Table + TableHeader + TableBody + TableRow + TableCell"},
    {"name": "Tooltip", "import": "@/components/ui/tooltip", "usage": "TooltipProvider + Tooltip + TooltipTrigger + TooltipContent"},
    {"name": "NavigationMenu", "import": "@/components/ui/navigation-menu", "usage": "top nav"},
    {"name": "ScrollArea", "import": "@/components/ui/scroll-area", "usage": "scrollable regions"},
]

DESIGN_CONTRACT_MARKDOWN = """# Sycord Design Contract
### Mandatory design rules for every AI-generated website (Next.js + shadcn/ui + Tailwind)

## 0. Theme Selection (new projects)
- When scaffolding a **new** website, call `ask_question` with `question_type=choice` and options from the named themes: **minimal**, **bold**, **corporate**, **vibrant**, **dark-tech**.
- Apply the chosen theme's CSS variables, font pairing, radius, and shadow level before building pages.
- Do not invent a one-off palette when a named theme fits.

## 1. Framework & Component Rules
- Stack is **Next.js (App Router) + shadcn/ui + Tailwind + Lucide** only for websites.
- **FORBIDDEN UI kits (never install or import):** HeroUI, NextUI, Chakra UI, MUI / Material UI,
  Ant Design, Mantine, DaisyUI component packs, Bootstrap React. If they appear in package.json,
  remove them and migrate to shadcn/ui under `components/ui/*`.
- Always use **shadcn/ui** components (Button, Card, Badge, Input, etc.) — never raw unstyled div buttons.
- Import only components from the catalog (see JSON reference). Never invent component names.
- Always use **Lucide React** (`lucide-react`) for icons. Never emoji, never inline hand-drawn SVG icons.
- Icons at natural size (`h-4 w-4` to `h-6 w-6`) — **never wrap icons in a colored circle background**.
- All interactive elements must come from `components/ui/*`.

## 2. Font Rules
- Prefer the theme's font pairing. Default primary: **Inter** via `next/font/google`.
- CSS variable MUST be `--font-sans` wired into `body { font-family: var(--font-sans), system-ui, sans-serif }`.
- JetBrains Mono only for code blocks (or dark-tech display labels) — never for body copy.

## 3. Color Pattern Rules
- Base theme from the selected named theme (or official shadcn preset Zinc/Slate/Stone/Gray/Neutral) + ONE accent.
- Max **2 non-neutral hues** per viewport.
- `--card` ≠ `--background` in light AND dark mode.
- `--border` visible against `--background`.
- Dark mode mandatory with toggle; default from `prefers-color-scheme` (dark-tech defaults to dark).

## 4. Shape & Component Styling
- Border radius via `--radius` token (`rounded-lg`, `rounded-md`, `rounded-sm`).
- No colored left-border accent bars on cards — use elevation or neutral border.
- Soft tone-matched shadows, never pure black.

## 5. Imagery & Backgrounds
- Real stock images (Unsplash/Pexels) or AI-generated — no gray placeholders.
- Hero sections need at least one real image (except pure dashboard tools).
- **Gradient below hero** (primary/accent at 0.15–0.3 opacity):
  `background: radial-gradient(ellipse 80% 60% at 50% -10%, hsl(var(--primary) / 0.25), transparent);`
- Every `<img>` needs `alt`, `width`, `height`, `loading="lazy"`.

## 6. Layout Rules
- Avoid generic 3-column icon-in-circle as the only feature layout.
- Left-align body copy; center only short hero headlines.
- Vary section padding; mobile-first (375px + 1280px+).

## 7. Pre-Flight Checklist
- [ ] Named theme applied (or explicit user palette)
- [ ] `--font-sans` on body
- [ ] `--card` ≠ `--background` (light + dark)
- [ ] shadcn preset / theme colors, not arbitrary HSL
- [ ] Max 2 non-neutral hues per viewport
- [ ] Lucide icons, never in colored circles
- [ ] Hero gradient transition (opacity 0.15–0.3+)
- [ ] All images resolve
- [ ] `--radius` consistent
- [ ] Dark mode toggle works
- [ ] WCAG AA contrast
"""

DEPLOY_RULES = {
    "summary": "Never run npm/yarn/next build via execute_command. All builds happen inside Docker during issue_deploy.",
    "deploy_endpoint": "POST /api/issue_deploy",
    "deploy_body": {"uuid": "<project-uuid>"},
    "deploy_does": "Git pull (if configured) + docker build (includes npm run build in Dockerfile) + container start",
    "forbidden_execute_commands": [
        "npm run build",
        "yarn build",
        "pnpm build",
        "next build",
        "npx next build",
    ],
    "allowed_testing_commands": [
        "npm run lint",
        "npm install",
        "npx create-next-app@latest . --yes",
        "mkdir -p src/components",
        "ls -la",
    ],
    "workflow": [
        "1. POST /api/create_project {name} → uuid",
        "2. POST /api/write_file — scaffold Next.js + shadcn/ui per design_contract",
        "3. POST /api/execute_command — npm install, npm run lint (testing only, NOT build)",
        "4. POST /api/issue_deploy {uuid} — git pull + docker build + start",
        "5. GET /api/get_logs?uuid= — verify deploy",
        "6. POST /api/validate_design?uuid= — run design contract linter",
    ],
}

PREFLIGHT_CHECKLIST = [
    "Named theme applied (minimal|bold|corporate|vibrant|dark-tech) or explicit user palette",
    "Font variable is --font-sans and applied to body",
    "--card != --background in light AND dark mode",
    "Theme colors from a named shadcn preset / DESIGN_THEMES entry",
    "Max 2 non-neutral hues per viewport",
    "All icons are Lucide, never in colored circles",
    "Hero has gradient transition below (opacity 0.15-0.3 minimum)",
    "All images resolve (no broken/placeholder images)",
    "Border radius consistent via --radius token",
    "Dark mode toggle present and functional",
    "WCAG AA contrast on text/background pairs",
]


def shadcn_catalog_json() -> str:
    """Compact JSON for system-prompt injection."""
    import json

    return json.dumps(SHADCN_COMPONENT_CATALOG, separators=(",", ":"))


def themes_prompt_block() -> str:
    """Short theme vocabulary for the agent instruction."""
    lines = ["Named design themes (pick one via ask_question choice when scaffolding):"]
    for key, theme in DESIGN_THEMES.items():
        fonts = theme["fonts"]
        lines.append(
            f"- {key}: {theme['label']} — preset {theme['preset']}, accent {theme['accent']}, "
            f"sans={fonts['sans']}, display={fonts['display']}, radius={theme['radius']}. "
            f"{theme['notes']}"
        )
    return "\n".join(lines)


def build_design_contract_spec() -> dict:
    return {
        "version": DESIGN_CONTRACT_VERSION,
        "title": "Sycord Design Contract",
        "framework": "Next.js + shadcn/ui + Tailwind CSS",
        "markdown": DESIGN_CONTRACT_MARKDOWN,
        "themes": DESIGN_THEMES,
        "shadcn_components": SHADCN_COMPONENT_CATALOG,
        "rules": {
            "components": "shadcn/ui only (never HeroUI/NextUI/Chakra/MUI/Ant); Lucide icons; no icon-in-circle; catalog imports only",
            "fonts": "Theme pairing or Inter via next/font/google; CSS var --font-sans on body",
            "colors": "Named theme or shadcn preset base + one accent; card != background; dark mode required",
            "imagery": "real images; hero gradient below hero section",
            "layout": "avoid generic 3-col icon circles; mobile-first",
            "themes": "Ask user to pick minimal|bold|corporate|vibrant|dark-tech on new sites",
            "file_paths": "Syte workspace root is app/ — Next.js App Router files go in app/app/page.tsx, app/app/layout.tsx (double app/ is correct)",
        },
        "preflight_checklist": PREFLIGHT_CHECKLIST,
        "deploy_rules": DEPLOY_RULES,
    }


def build_system_prompt() -> str:
    """Single system-level instruction block for AI website builders."""
    return (
        "You are building a production website on Syte. Follow the Sycord Design Contract "
        "for every UI decision (Next.js + shadcn/ui + Tailwind + Lucide + Inter).\n\n"
        + DESIGN_CONTRACT_MARKDOWN
        + "\n\n## Themes\n"
        + themes_prompt_block()
        + "\n\n## shadcn/ui catalog\n"
        + shadcn_catalog_json()
        + "\n\n## Deploy Rules (mandatory)\n"
        "- NEVER run `npm run build`, `yarn build`, `next build`, or similar via execute_command.\n"
        "- Use execute_command only for scaffolding, npm install, and `npm run lint` (bug testing).\n"
        "- Deploy ONLY via: POST /api/issue_deploy {\"uuid\": \"...\"}\n"
        "  This runs git pull + docker build (build happens inside Dockerfile) + restart.\n"
        "- After generating files, run POST /api/validate_design?uuid= to check the contract.\n"
    )
