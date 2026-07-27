"""Sycord Design Contract — mandatory rules for AI-generated Next.js sites."""

DESIGN_CONTRACT_VERSION = "2.1"

DESIGN_REFERENCE_URLS = {
    "website_builder_baseline": (
        "https://github.com/webprompts/webprompts.github.io/blob/gh-pages/v0.md"
    ),
    "shadcn_components": "https://ui.shadcn.com/docs/components",
    "shadcn_cli": "https://ui.shadcn.com/docs/cli",
    "shadcn_mcp": "https://ui.shadcn.com/docs/mcp",
    "shadcn_agent_skills": "https://ui.shadcn.com/docs/skills",
    "radix_accessibility": (
        "https://www.radix-ui.com/primitives/docs/overview/accessibility"
    ),
    "wcag_22": "https://www.w3.org/TR/WCAG22/",
}

# Documented defaults that make AI-generated UI recognisable as AI-generated.
# These are the literal statistical centre of "modern web UI" in training data,
# so an unguided model converges on them; naming them explicitly is what stops
# it. Values are stock Tailwind, i.e. nobody's deliberate brand decision.
SLOP_SIGNATURE = {
    "colors": ["#6366F1 indigo-500", "#8B5CF6 violet-500", "#A855F7 purple-500"],
    "fonts": ["Inter as the only typeface", "Roboto as the only typeface"],
    "layouts": [
        "centered hero headline + single CTA",
        "row of exactly three icon cards below the hero",
        "badge pill above the headline",
        "numbered 1-2-3 steps strip",
        "colored left-border accent card",
    ],
    "effects": [
        "purple-to-indigo gradient hero",
        "gradient text headline",
        "glassmorphism panels",
        "glowing background blobs",
        "uniform rounded-2xl + 0.1-opacity shadow on everything",
    ],
}

# A website build must resolve three separate jobs. Fusing them into one pass is
# what produces the average: the model is asked to be taste director, explorer
# and engineer simultaneously, so it answers with the safest common output.
CREATIVE_DIRECTION_JOBS = (
    "direction — what this specific product should feel like, and why (stated in words "
    "before any code, anchored to the brand/audience/content, not to adjectives)",
    "exploration — at least two genuinely different compositional approaches considered, "
    "with the chosen one justified against the rejected one",
    "specification — concrete tokens, type scale, grid, section order and component "
    "mapping recorded in the plan so the build executes a decision instead of guessing",
)

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

# The requested, pinned 57-item shadcn/ui component surface. Seven additions in
# the current docs (Attachment, Bubble, Direction, Marker, Message, Message
# Scroller, and Native Select) stay out until the contract is deliberately
# versioned again. Composite
# entries are official shadcn patterns assembled from primitive component files;
# they are not shadcn Blocks or invented imports.
SHADCN_COMPONENT_CATALOG: list[dict[str, str]] = [
    {"name": "Accordion", "import": "@/components/ui/accordion", "usage": "Accordion + AccordionItem + AccordionTrigger + AccordionContent"},
    {"name": "Alert", "import": "@/components/ui/alert", "usage": "Alert + AlertTitle + AlertDescription"},
    {"name": "AlertDialog", "import": "@/components/ui/alert-dialog", "usage": "confirmation for destructive or consequential actions"},
    {"name": "AspectRatio", "import": "@/components/ui/aspect-ratio", "usage": "stable media aspect ratios"},
    {"name": "Avatar", "import": "@/components/ui/avatar", "usage": "Avatar + AvatarImage + AvatarFallback"},
    {"name": "Badge", "import": "@/components/ui/badge", "usage": "compact status or category label"},
    {"name": "Breadcrumb", "import": "@/components/ui/breadcrumb", "usage": "hierarchical route context"},
    {"name": "Button", "import": "@/components/ui/button", "usage": "actions and links via asChild; use documented variants and sizes"},
    {"name": "ButtonGroup", "import": "@/components/ui/button-group", "usage": "visually related actions; do not use for unrelated CTAs"},
    {"name": "Calendar", "import": "@/components/ui/calendar", "usage": "date selection surface"},
    {"name": "Card", "import": "@/components/ui/card", "usage": "group content only when a bounded container is semantically useful"},
    {"name": "Carousel", "import": "@/components/ui/carousel", "usage": "optional sequential media; preserve keyboard controls"},
    {"name": "Chart", "import": "@/components/ui/chart", "usage": "ChartContainer + accessible labels/tooltips"},
    {"name": "Checkbox", "import": "@/components/ui/checkbox", "usage": "independent boolean choices with Label"},
    {"name": "Collapsible", "import": "@/components/ui/collapsible", "usage": "single disclosure region"},
    {"name": "Combobox", "import": "@/components/ui/command + @/components/ui/popover", "usage": "official shadcn composite pattern; searchable option selection", "kind": "composite"},
    {"name": "Command", "import": "@/components/ui/command", "usage": "command palette or filterable action list"},
    {"name": "ContextMenu", "import": "@/components/ui/context-menu", "usage": "contextual pointer actions with keyboard equivalent"},
    {"name": "DataTable", "import": "@/components/ui/table", "usage": "official shadcn composite pattern with sorting/filtering/pagination as needed", "kind": "composite"},
    {"name": "DatePicker", "import": "@/components/ui/calendar + @/components/ui/popover", "usage": "official shadcn composite date field pattern", "kind": "composite"},
    {"name": "Dialog", "import": "@/components/ui/dialog", "usage": "focused modal task; include title and description"},
    {"name": "Drawer", "import": "@/components/ui/drawer", "usage": "touch-friendly bottom or side surface"},
    {"name": "DropdownMenu", "import": "@/components/ui/dropdown-menu", "usage": "overflow and compact action menus"},
    {"name": "Empty", "import": "@/components/ui/empty", "usage": "purposeful zero-data state with one clear next action"},
    {"name": "Field", "import": "@/components/ui/field", "usage": "form field layout, label, description, and error grouping"},
    {"name": "HoverCard", "import": "@/components/ui/hover-card", "usage": "supplemental preview; never hide required content behind hover"},
    {"name": "Input", "import": "@/components/ui/input", "usage": "single-line data entry paired with Label or Field"},
    {"name": "InputGroup", "import": "@/components/ui/input-group", "usage": "input with a tightly related prefix, suffix, or action"},
    {"name": "InputOTP", "import": "@/components/ui/input-otp", "usage": "one-time code entry with accessible instructions"},
    {"name": "Item", "import": "@/components/ui/item", "usage": "structured list item; preserve a clear information hierarchy"},
    {"name": "Kbd", "import": "@/components/ui/kbd", "usage": "keyboard shortcut hints"},
    {"name": "Label", "import": "@/components/ui/label", "usage": "accessible label for a form control"},
    {"name": "Menubar", "import": "@/components/ui/menubar", "usage": "desktop application-style command menus only"},
    {"name": "NavigationMenu", "import": "@/components/ui/navigation-menu", "usage": "primary site navigation; keep mobile behavior explicit"},
    {"name": "Pagination", "import": "@/components/ui/pagination", "usage": "paged collections with current-page semantics"},
    {"name": "Popover", "import": "@/components/ui/popover", "usage": "non-modal contextual content"},
    {"name": "Progress", "import": "@/components/ui/progress", "usage": "determinate task progress with a text equivalent"},
    {"name": "RadioGroup", "import": "@/components/ui/radio-group", "usage": "one choice from a visible set"},
    {"name": "Resizable", "import": "@/components/ui/resizable", "usage": "user-resizable panes where the task benefits"},
    {"name": "ScrollArea", "import": "@/components/ui/scroll-area", "usage": "bounded scroll regions; avoid nested page scrolling"},
    {"name": "Select", "import": "@/components/ui/select", "usage": "compact option selection with accessible trigger/value/content"},
    {"name": "Separator", "import": "@/components/ui/separator", "usage": "semantic or decorative division"},
    {"name": "Sheet", "import": "@/components/ui/sheet", "usage": "mobile navigation, filters, or secondary side panels"},
    {"name": "Sidebar", "import": "@/components/ui/sidebar", "usage": "application navigation with responsive collapse behavior"},
    {"name": "Skeleton", "import": "@/components/ui/skeleton", "usage": "shape-matched loading placeholder"},
    {"name": "Slider", "import": "@/components/ui/slider", "usage": "bounded numeric input with visible value and label"},
    {"name": "Sonner", "import": "@/components/ui/sonner", "usage": "brief non-blocking notifications"},
    {"name": "Spinner", "import": "@/components/ui/spinner", "usage": "indeterminate progress with accessible status text"},
    {"name": "Switch", "import": "@/components/ui/switch", "usage": "immediate on/off setting with Label"},
    {"name": "Table", "import": "@/components/ui/table", "usage": "genuinely tabular data with headers"},
    {"name": "Tabs", "import": "@/components/ui/tabs", "usage": "peer views in one context; preserve arrow-key behavior"},
    {"name": "Textarea", "import": "@/components/ui/textarea", "usage": "multi-line data entry paired with Label or Field"},
    {"name": "Toast", "import": "@/components/ui/toast", "usage": "legacy project notification API; prefer Sonner for new work"},
    {"name": "Toggle", "import": "@/components/ui/toggle", "usage": "single pressed/unpressed control"},
    {"name": "ToggleGroup", "import": "@/components/ui/toggle-group", "usage": "related single- or multi-select toggles"},
    {"name": "Tooltip", "import": "@/components/ui/tooltip", "usage": "supplemental label/help; never required information"},
    {"name": "Typography", "import": "semantic HTML + Tailwind typography tokens", "usage": "official shadcn typography treatment; no registry Block", "kind": "pattern"},
]

DESIGN_CONTRACT_MARKDOWN = """# Sycord Design Contract
### Mandatory design rules for every AI-generated website (Next.js + shadcn/ui + Tailwind)

## 0. Clarify, then plan, then build
- For a new website or substantive redesign, the first tool call is `ask_question` when brand,
  audience, content, visual direction, required pages, or key behavior is materially unclear.
  Batch related decisions into one concise choice/multi-choice question. Do not ask ceremonial
  questions when the request or existing design system already answers them.
- If no clarification is needed, call `update_plan` first. After a question is answered, call
  `update_plan` before file search, commands, or edits.
- A website plan must cover information architecture, visual direction, content/assets,
  component mapping, responsive behavior, accessibility/interactions, and preview verification.
- When scaffolding a **new** website without an explicit visual direction, call `ask_question`
  with `question_type=choice` and options from: **minimal**, **bold**, **corporate**, **vibrant**,
  **dark-tech**.
- Apply the chosen theme's CSS variables, font pairing, radius, and shadow level before building pages.
- Do not invent a one-off palette when a named theme fits.

## 0.1 Creative direction before code (mandatory for new sites / redesigns)
Generic output is not a model-capability gap; it is what happens when one pass has to be
taste director, explorer, and engineer at once. Keep those three jobs separate:
1. **Direction** — before writing any file, state in the plan (or the reply) what this
   *specific* product should feel like and why, tied to its brand, audience, and real
   content. Never a bare adjective like "clean", "modern", or "premium".
2. **Exploration** — consider at least two materially different compositions (not two
   colour swaps). Name the one you chose and why the other was rejected.
3. **Specification** — record the decision as concrete tokens, type scale, grid, section
   order, and component mapping in `update_plan`, then build to that spec.
- Use `web_search` + `fetch_url` on real, current references for the product's category
  before designing. Look at how actual products in that space are composed.
- Commit to exactly one deliberate signature move per site (an editorial grid, a real data
  visualization, oversized type, a dense table-first layout, an asymmetric split, genuine
  product media). One considered decision beats five default effects.
- If you cannot say *why* a layout choice belongs to this product, it is a default. Replace it.

## 0.2 Component sourcing — verify, never recall
- `shadcn_registry` is the source of truth for component names, sub-components, props, and
  file paths. **Call it before writing UI code**, not after a build error.
- Order of operations for any new interface surface:
  `shadcn_registry(action=info)` → `shadcn_registry(action=search, query=...)` →
  `shadcn_registry(action=view, items=[...])` or `action=docs` → then write the page.
- Install real component source with `shadcn_registry(action=add, items=[...])` instead of
  hand-writing a primitive. Hand-rolled substitutes for cataloged components are a defect.
- Never invent an import path, a sub-component name, or a prop. If `view`/`docs` does not
  show it, it does not exist — search for the component that actually does the job.
- Composite patterns (Combobox, DatePicker, DataTable) are assembled from their listed
  primitives; `view` each primitive rather than guessing the composition.

## 1. Framework & Component Rules
- Stack is **Next.js (App Router) + shadcn/ui + Tailwind + Lucide** only for websites.
- **FORBIDDEN UI kits (never install or import):** HeroUI, NextUI, Chakra UI, MUI / Material UI,
  Ant Design, Mantine, DaisyUI component packs, Bootstrap React. If they appear in package.json,
  remove them and migrate to shadcn/ui under `components/ui/*`.
- Use the **57 cataloged shadcn/ui components as separate primitives/patterns**. Select only the
  components justified by the interface; do not force all 57 into one site.
- **Do not use shadcn Blocks**, block registry templates, or preassembled dashboard/landing/login
  sections. Compose original sections from `@/components/ui/*` primitives.
- All application controls use shadcn components. Do not hand-roll buttons, inputs, selects,
  dialogs, menus, tabs, tooltips, or other controls with styled `div`/raw control substitutes.
- shadcn components are Radix-backed where applicable. Preserve their ARIA semantics, keyboard
  behavior, focus management, portals, and accessible labels. If a needed behavior has no shadcn
  wrapper, wrap the Radix primitive once under `components/ui/*`; application files still import
  the local shadcn-style wrapper, never `@radix-ui/*` directly.
- Import only components from the catalog (see JSON reference). Composite catalog entries such as
  Combobox, DatePicker, and DataTable are assembled from their listed primitives; never invent an import.
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
- Use real product media, editorial imagery, illustration, or data visualization only when it adds
  meaning. Do not add a generic stock hero image merely to fill a layout; never use gray placeholders.
- Choose flat color, texture, photography, illustration, or a restrained gradient based on the
  content and visual direction. Gradients are optional and must not be the default treatment.
- Every `<img>` needs `alt`, `width`, `height`, `loading="lazy"`.

## 6. Layout Rules
- Avoid generic 3-column icon-in-circle as the only feature layout.
- Left-align body copy; center only short hero headlines.
- Vary section padding; mobile-first (375px + 1280px+).

## 7. Anti-slop quality rules
- Build a content-specific visual hierarchy, not a generic template. One clear focal point per
  viewport; deliberate grid, type scale, alignment, density, and whitespace rhythm.
- **Banned-by-default signature** (the documented "AI slop starter pack"). Each item below is
  forbidden unless the stated creative direction explicitly argues for it in the plan:
  - accent `indigo-500 / violet-500 / purple-500` (#6366F1, #8B5CF6, #A855F7) or any
    purple-to-indigo gradient
  - Inter or Roboto as the *only* typeface — pair a display face with the body face
  - centered hero headline + one CTA, with a badge pill above it
  - a row of exactly three icon cards as the primary feature layout
  - numbered 1-2-3 steps strip; colored left-border accent card
  - gradient text, glowing blobs, glassmorphism, uniform `rounded-2xl` + 0.1-opacity shadow
    applied to every surface
- Do not default to a huge centered headline, gradient text, glowing blobs, glassmorphism, bento
  grids, floating cards, excessive pills, or a three-card feature row. Use these only when the
  product and chosen direction justify them.
- Vary the section grammar: at least three structurally different section types per page
  (e.g. editorial two-column, full-bleed media, dense table/list, comparison, timeline).
  Repeating one card grid down the page is the failure mode, not the goal.
- Do not put every section inside a Card. Use semantic sections, lists, tables, or editorial
  layouts when those structures communicate better.
- Avoid repetitive section cadence and uniform card geometry. Vary composition based on content
  while keeping tokens and alignment consistent.
- Write concrete product-specific copy. Never invent customer logos, testimonials, awards,
  compliance claims, integrations, or metrics; label genuine placeholders in development only.
- Every visible control must work. Include hover, focus-visible, active, disabled, loading, empty,
  error, and success states where the flow requires them.
- Research current, relevant design conventions with `web_search` + `fetch_url` for new
  builds/redesigns, and read the actual component API with `shadcn_registry` before coding. Treat
  the v0 prompt reference as workflow inspiration, then follow official shadcn, Radix, and WCAG 2.2
  documentation as the implementation authority.
- Close the loop: after building, `inspect_preview` (and `screenshot_preview` when layout review is
  needed), then critique your own output against this section by name and fix what fails. A first
  draft is a draft, not the answer.
- Starting references:
  - Builder workflow: https://github.com/webprompts/webprompts.github.io/blob/gh-pages/v0.md
  - Components: https://ui.shadcn.com/docs/components
  - Component CLI for agents: https://ui.shadcn.com/docs/cli
  - Registry access for agents: https://ui.shadcn.com/docs/mcp
  - Primitive accessibility: https://www.radix-ui.com/primitives/docs/overview/accessibility
  - Accessibility standard: https://www.w3.org/TR/WCAG22/

## 8. Pre-Flight Checklist
- [ ] Creative direction stated in words, with a rejected alternative, before code
- [ ] Component APIs verified with `shadcn_registry` (view/docs) — nothing written from memory
- [ ] No banned-by-default slop signature (indigo/violet accent, centered-hero + 3 cards, etc.)
- [ ] At least three structurally different section types on the page
- [ ] Named theme applied (or explicit user palette)
- [ ] `--font-sans` on body
- [ ] `--card` ≠ `--background` (light + dark)
- [ ] shadcn preset / theme colors, not arbitrary HSL
- [ ] Max 2 non-neutral hues per viewport
- [ ] Lucide icons, never in colored circles
- [ ] Hero/background treatment is intentional, content-specific, and not a default effect
- [ ] All images resolve
- [ ] `--radius` consistent
- [ ] Dark mode toggle works
- [ ] WCAG AA contrast
- [ ] No shadcn Blocks or application-level direct Radix imports
- [ ] No raw hand-rolled interactive controls outside components/ui
- [ ] Layout and copy pass the anti-slop quality rules
- [ ] One stack only — no static `index.html`, Vite/CRA entry, duplicate root `page.tsx`/`globals.css`,
      or `cdn.tailwindcss.com` script next to the Next.js app
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
    "Creative direction written out (product-specific, with a rejected alternative) before code",
    "Component names/props/imports verified via shadcn_registry view/docs — none from memory",
    "No banned slop signature: indigo/violet accent, purple gradient, centered-hero + 3 icon cards",
    "At least three structurally different section types on the page",
    "Named theme applied (minimal|bold|corporate|vibrant|dark-tech) or explicit user palette",
    "Font variable is --font-sans and applied to body",
    "--card != --background in light AND dark mode",
    "Theme colors from a named shadcn preset / DESIGN_THEMES entry",
    "Max 2 non-neutral hues per viewport",
    "All icons are Lucide, never in colored circles",
    "Hero/background treatment is intentional and content-specific (no mandatory gradient)",
    "All images resolve (no broken/placeholder images)",
    "Border radius consistent via --radius token",
    "Dark mode toggle present and functional",
    "WCAG AA contrast on text/background pairs",
    "No shadcn Blocks or application-level direct Radix imports",
    "No raw hand-rolled interactive controls outside components/ui",
    "Content-specific composition passes the anti-slop quality rules",
    "Single stack: no static index.html, Vite/CRA entry, or Tailwind CDN script beside the Next.js app",
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


def slop_signature_block() -> str:
    """Explicit banned-default list for the agent instruction.

    Naming the statistical centre is what stops a model converging on it; a
    generic "be creative" instruction does not.
    """
    lines = [
        "Banned-by-default AI-slop signature (allowed ONLY if the written creative "
        "direction explicitly argues for it):",
    ]
    for group, values in SLOP_SIGNATURE.items():
        lines.append(f"- {group}: " + "; ".join(values))
    lines.append(
        "- Instead: pick tokens that mean something for this product, pair a display face "
        "with the body face, and vary section grammar (min. 3 structurally different section types)."
    )
    return "\n".join(lines)


def creative_direction_block() -> str:
    """The three separate jobs a website build must resolve, in order."""
    lines = [
        "Creative direction protocol — resolve these as SEPARATE steps, never fused into "
        "one pass (fusing them is what produces generic output):",
    ]
    for index, job in enumerate(CREATIVE_DIRECTION_JOBS, start=1):
        lines.append(f"{index}. {job}")
    lines.append(
        "Component facts are never recalled: run shadcn_registry (info → search → view/docs) "
        "before writing UI, and install real registry source with action=add instead of "
        "hand-rolling a primitive."
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
        "design_references": DESIGN_REFERENCE_URLS,
        "slop_signature": SLOP_SIGNATURE,
        "creative_direction_jobs": list(CREATIVE_DIRECTION_JOBS),
        "rules": {
            "creative_direction": "State direction, consider two compositions, then specify tokens/grid/sections before building",
            "component_sourcing": "shadcn_registry info→search→view/docs before writing UI; never recall props from memory",
            "anti_slop": "Banned by default: indigo/violet accent, purple gradient, centered hero + three icon cards, gradient text, glassmorphism",
            "components": "57 separate shadcn/ui primitives/patterns; no shadcn Blocks; Radix through local components/ui wrappers; never HeroUI/NextUI/Chakra/MUI/Ant",
            "fonts": "Theme pairing or Inter via next/font/google; CSS var --font-sans on body",
            "colors": "Named theme or shadcn preset base + one accent; card != background; dark mode required",
            "imagery": "Meaningful real media only; no placeholders or mandatory stock/gradient hero treatment",
            "layout": "avoid generic 3-col icon circles; mobile-first",
            "themes": "Ask user to pick minimal|bold|corporate|vibrant|dark-tech on new sites",
            "workflow": "For substantive website work: ask one batched clarification if needed, otherwise update_plan first; then inspect, build, and verify",
            "quality": "Content-specific hierarchy and composition; reject generic AI-template defaults and unsupported claims",
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
        + "\n\n## Creative direction\n"
        + creative_direction_block()
        + "\n\n## Anti-slop\n"
        + slop_signature_block()
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
