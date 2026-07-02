"""Sycord Design Contract — mandatory rules for AI-generated Next.js sites."""

DESIGN_CONTRACT_VERSION = "1.0"

DESIGN_CONTRACT_MARKDOWN = """# Sycord Design Contract
### Mandatory design rules for every AI-generated website (Next.js + shadcn/ui + Tailwind)

## 1. Framework & Component Rules
- Always use **shadcn/ui** components (Button, Card, Badge, Input, etc.) — never raw unstyled div buttons.
- Always use **Lucide React** (`lucide-react`) for icons. Never emoji, never inline hand-drawn SVG icons.
- Icons at natural size (`h-4 w-4` to `h-6 w-6`) — **never wrap icons in a colored circle background**.
- All interactive elements must come from `components/ui/*`.

## 2. Font Rules
- Primary font: **Inter** via `next/font/google`.
- CSS variable MUST be `--font-sans` wired into `body { font-family: var(--font-sans), system-ui, sans-serif }`.
- JetBrains Mono only for code blocks — never for body copy.

## 3. Color Pattern Rules
- Base theme from official shadcn preset (Zinc, Slate, Stone, Gray, or Neutral) + ONE accent.
- Max **2 non-neutral hues** per viewport.
- `--card` ≠ `--background` in light AND dark mode.
- `--border` visible against `--background`.
- Dark mode mandatory with toggle; default from `prefers-color-scheme`.

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
- [ ] `--font-sans` on body
- [ ] `--card` ≠ `--background` (light + dark)
- [ ] shadcn preset colors, not arbitrary HSL
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
    "Font variable is --font-sans and applied to body",
    "--card != --background in light AND dark mode",
    "Theme colors from a named shadcn preset",
    "Max 2 non-neutral hues per viewport",
    "All icons are Lucide, never in colored circles",
    "Hero has gradient transition below (opacity 0.15-0.3 minimum)",
    "All images resolve (no broken/placeholder images)",
    "Border radius consistent via --radius token",
    "Dark mode toggle present and functional",
    "WCAG AA contrast on text/background pairs",
]


def build_design_contract_spec() -> dict:
    return {
        "version": DESIGN_CONTRACT_VERSION,
        "title": "Sycord Design Contract",
        "framework": "Next.js + shadcn/ui + Tailwind CSS",
        "markdown": DESIGN_CONTRACT_MARKDOWN,
        "rules": {
            "components": "shadcn/ui only; Lucide icons; no icon-in-circle pattern",
            "fonts": "Inter via next/font/google; CSS var --font-sans on body",
            "colors": "shadcn preset base + one accent; card != background; dark mode required",
            "imagery": "real images; hero gradient below hero section",
            "layout": "avoid generic 3-col icon circles; mobile-first",
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
        + "\n\n## Deploy Rules (mandatory)\n"
        "- NEVER run `npm run build`, `yarn build`, `next build`, or similar via execute_command.\n"
        "- Use execute_command only for scaffolding, npm install, and `npm run lint` (bug testing).\n"
        "- Deploy ONLY via: POST /api/issue_deploy {\"uuid\": \"...\"}\n"
        "  This runs git pull + docker build (build happens inside Dockerfile) + restart.\n"
        "- After generating files, run POST /api/validate_design?uuid= to check the contract.\n"
    )
