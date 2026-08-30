"""Modular Skills & Capabilities Discovery Registry for Syte Autonomous AI Builder.

Provides comprehensive, structured domain guides, best practices, design systems,
and implementation blueprints that the AI agent can discover, search, browse, and load dynamically.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


# -----------------------------------------------------------------------------
# 1. Categorized Modular Capabilities Catalog
# -----------------------------------------------------------------------------
SKILLS_CATEGORIES_CATALOG: Dict[str, Dict[str, Any]] = {
    "Design & Colors": {
        "description": "Design tokens, typography scales, harmonious color palettes, Tailwind classes, spacing, and contrast standards.",
        "capabilities": {
            "get_color_palette": {
                "summary": "Return modern semantic color tokens (neutral zinc, slate, primary, accent, surface, destructive).",
                "tokens": {
                    "background": "#ffffff",
                    "card": "#ffffff",
                    "card_subtle": "#fafafa",
                    "page_bg": "#f8fafc",
                    "border": "#e4e4e7",
                    "border_subtle": "#f4f4f5",
                    "foreground": "#09090b",
                    "muted_foreground": "#71717a",
                    "primary": "#18181b",
                    "primary_foreground": "#ffffff",
                    "accent_indigo": "#6366f1",
                    "accent_emerald": "#10b981",
                    "accent_sky": "#0284c7",
                    "accent_rose": "#f43f5e",
                },
            },
            "resolve_theme_token": {
                "summary": "Map abstract CSS variables (`--primary`, `--muted`) to exact hex/rgb values for light and dark modes.",
                "guide": "Use standard Tailwind CSS variables: `bg-background text-foreground border-border ring-ring` with CSS variables defined in `@layer base`.",
            },
            "get_typography_scale": {
                "summary": "Full typography hierarchy from display headings (H1) down to small badges with font-family, sizes, weights, and tracking.",
                "scale": {
                    "display_hero": "text-4xl sm:text-5xl lg:text-6xl font-bold tracking-tight leading-tight",
                    "heading_h2": "text-2xl sm:text-3xl font-semibold tracking-tight",
                    "heading_h3": "text-lg sm:text-xl font-semibold tracking-tight",
                    "subheading_lead": "text-base sm:text-lg text-muted-foreground",
                    "body": "text-sm sm:text-base leading-relaxed text-zinc-900",
                    "caption": "text-xs font-medium tracking-wide text-zinc-500",
                },
            },
            "validate_tailwind_classes": {
                "summary": "Check for common class collisions (e.g. `p-4 px-2`), arbitrary value syntax (`bg-[#fff]`), and responsive breakpoints (`sm: md: lg:`).",
            },
            "apply_color_contrast_check": {
                "summary": "WCAG AA/AAA compliant contrast ratios (4.5:1 for normal text, 3:1 for large text).",
            },
            "get_spacing_system": {
                "summary": "Consistent 4px grid spacing scale (`p-2: 8px`, `p-4: 16px`, `p-6: 24px`, `p-8: 32px`, `gap-6: 24px`).",
            },
            "get_shadow_tokens": {
                "summary": "Elevation shadows: `shadow-xs`, `shadow-sm`, `shadow-md`, `shadow-lg`, `shadow-2xl` with subtle alpha blending (`rgba(0,0,0,0.04)`).",
            },
            "get_border_radius_scale": {
                "summary": "Modern border radii: `rounded-md: 6px`, `rounded-lg: 8px`, `rounded-xl: 12px`, `rounded-2xl: 16px`, `rounded-full: 9999px`.",
            },
        },
    },
    "Components & UI": {
        "description": "Ready-to-use component signatures, variants, Lucide icons, layout templates, and JSX/TSX syntax patterns.",
        "capabilities": {
            "get_component_signature": {
                "summary": "Standard TypeScript interfaces for buttons, modals, dropdowns, accordions, and cards.",
            },
            "list_available_components": {
                "summary": "Catalog of shadcn/ui components available to generate: Button, Card, Dialog, Dropdown, Input, Tabs, Toast, Sheet, Table, Badge, Avatar.",
            },
            "get_component_variants": {
                "summary": "Variant props mapping: `variant: 'default' | 'secondary' | 'outline' | 'ghost' | 'destructive' | 'link'` and `size: 'sm' | 'default' | 'lg' | 'icon'`.",
            },
            "get_icon_by_name": {
                "summary": "Recommended Lucide icon mappings for common UI actions (e.g. `zap`, `sparkles`, `terminal`, `rocket`, `shield`, `chevron-right`).",
            },
            "get_layout_template": {
                "summary": "Standard full-page responsive layouts: SaaS Landing Page, Admin Dashboard, Documentation, Settings Workspace, Auth Portal.",
            },
            "validate_jsx_syntax": {
                "summary": "Verify closing tags, key props in loops, className instead of class, and proper React import signatures.",
            },
            "render_component_preview": {
                "summary": "Isolated sandbox mounting guide for testing single UI components in Vite or Next.js preview.",
            },
        },
    },
    "App & Routing": {
        "description": "Site manifests, Next.js App Router & Vite route trees, page metadata, dynamic paths, and navigation schemas.",
        "capabilities": {
            "read_site_manifest": {
                "summary": "Inspect `package.json`, router config, and framework directory conventions (e.g. `app/`, `pages/`, `src/routes/`).",
            },
            "register_app_route": {
                "summary": "Create route entrypoint: `app/[route]/page.tsx` or `src/pages/[route].tsx` with layout and loading skeleton.",
            },
            "get_route_tree": {
                "summary": "Scan workspace to map active pages, dynamic segments (`[id]`, `[slug]`), and API endpoints.",
            },
            "validate_page_metadata": {
                "summary": "Ensure Next.js `metadata` export with `title`, `description`, `openGraph`, and `robots` tags.",
            },
            "get_navigation_schema": {
                "summary": "Top navigation and sidebar tree schemas with active state detection, badges, and breadcrumb trails.",
            },
        },
    },
    "Login & Auth": {
        "description": "OAuth providers, JWT/Cookie session middleware, user schemas, route protection guards, and CSRF security.",
        "capabilities": {
            "get_auth_provider_schema": {
                "summary": "OAuth2 configuration contracts for GitHub, Google, Discord, and Email magic links.",
            },
            "generate_auth_middleware": {
                "summary": "Edge/Node.js authentication middleware verifying JWT tokens or session cookies before routing.",
            },
            "get_session_user_schema": {
                "summary": "TypeScript user object schema: `{ id, email, name, role: 'admin' | 'member', avatar_url, created_at }`.",
            },
            "validate_permission_guard": {
                "summary": "Role-Based Access Control (RBAC) route and API guard logic.",
            },
            "get_protected_routes": {
                "summary": "Convention for securing `/dashboard/*`, `/settings/*`, and `/api/admin/*` behind auth barriers.",
            },
        },
    },
    "Server & Backend": {
        "description": "Server actions, REST API route generation, environment variable manifests, and payload validation.",
        "capabilities": {
            "get_server_action_contract": {
                "summary": "Next.js `'use server'` action patterns with input validation, error return objects, and revalidation.",
            },
            "list_api_endpoints": {
                "summary": "Scan workspace for `route.ts`, `api/*.py`, or Express router files and list HTTP methods.",
            },
            "get_env_variables_manifest": {
                "summary": "Generate documented `.env.example` template with key names, default fallback values, and security notes.",
            },
            "validate_request_payload": {
                "summary": "Schema validation using Zod (`z.object({...})`) or Pydantic for API route inputs.",
            },
            "generate_api_route": {
                "summary": "Generate complete API route handler with error handling, status codes, JSON serialization, and CORS headers.",
            },
        },
    },
    "Integrations & Database": {
        "description": "Database schemas (Prisma, Drizzle, SQLite, Postgres), Stripe webhooks, storage buckets, and payment events.",
        "capabilities": {
            "get_database_schema": {
                "summary": "Prisma (`schema.prisma`), Drizzle ORM, or SQL DDL templates for standard relational entities.",
            },
            "get_integration_config": {
                "summary": "Third-party SDK connection setup (Stripe, OpenAI, Resend, Supabase, AWS S3, Cloudflare R2).",
            },
            "generate_webhook_handler": {
                "summary": "Secure webhook handler verifying raw cryptographic signatures (e.g. `stripe.webhooks.constructEvent`).",
            },
            "get_storage_bucket_schema": {
                "summary": "File upload and S3/R2 presigned URL generator for secure user media uploads.",
            },
            "validate_payment_event": {
                "summary": "Handle Stripe checkout completion, subscription lifecycle, and customer billing portal redirection.",
            },
        },
    },
    "Optimization & Build": {
        "description": "TypeScript type checks, linter auto-fixes, SEO tags, bundle size impact, and cache revalidation policies.",
        "capabilities": {
            "run_typescript_check": {
                "summary": "Static TypeScript type diagnostics and missing type definition detection.",
            },
            "run_linter_fix": {
                "summary": "ESLint and Prettier code formatting patterns and syntax sanitation.",
            },
            "validate_seo_tags": {
                "summary": "Verify canonical URLs, meta descriptions, OpenGraph Twitter/Facebook preview tags, and sitemap.xml.",
            },
            "check_bundle_size_impact": {
                "summary": "Best practices for dynamic imports (`next/dynamic` or `React.lazy`), tree shaking, and lightweight dependencies.",
            },
            "get_cache_revalidation_policy": {
                "summary": "Next.js ISR caching (`revalidatePath`, `revalidateTag`, `stale-while-revalidate`) rules.",
            },
        },
    },
}


# -----------------------------------------------------------------------------
# 2. Main Full-Text Skill Blueprints
# -----------------------------------------------------------------------------
SKILLS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "website-create": {
        "name": "website-create",
        "aliases": ["design-and-colors", "components-and-ui", "shadcn-ui", "beautiful-ui", "design-system", "tailwind-ui"],
        "category": "Design & Colors",
        "description": "Comprehensive design system & UI blueprints using modern typography (Inter/Geist), shadcn/ui patterns, harmonious color palettes, sizing, and responsive components.",
        "content": """# Skill: Modern Website Creation, Design & UI Components (shadcn / Inter / Tailwind)

## 1. Typography & Hierarchy (Design & Colors)
- **Primary Font**: Inter / Geist (`font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`)
- **Monospace Font**: JetBrains Mono or SF Mono (`ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`)
- **Scale Hierarchy**:
  - `Display / Hero Title`: 40px - 56px (`text-4xl` to `text-6xl`), `font-bold` or `font-extrabold`, letter-spacing `-0.03em`, line-height `1.1`
  - `Section Heading (H2)`: 28px - 36px (`text-2xl` to `text-3xl`), `font-semibold`, letter-spacing `-0.02em`
  - `Card Heading (H3)`: 18px - 22px (`text-lg` to `text-xl`), `font-semibold`, letter-spacing `-0.01em`
  - `Subheading / Lead`: 16px - 18px (`text-base` to `text-lg`), `font-normal`, color `text-muted-foreground` (`#71717a`)
  - `Body Text`: 14px - 15px (`text-sm` to `text-base`), line-height `1.6`, color `#18181b` (light) / `#f4f4f5` (dark)
  - `Caption / Badge`: 11px - 12px (`text-xs`), `font-medium` or `font-semibold`, tracking `0.02em`

## 2. Harmonious Color Palettes & Theme Tokens
- **Neutral Palette (Zinc/Slate)**:
  - Background: `#ffffff` (Card: `#ffffff`, Sub-panel: `#fafafa`, Page BG: `#f8fafc`)
  - Border: `#e4e4e7` (Subtle: `#f4f4f5`, Hover: `#d4d4d8`, Focus: `#18181b`)
  - Foreground: `#09090b` (Muted: `#71717a`, Subtle: `#a1a1aa`)
- **Accent Palettes**:
  - `Indigo / Violet`: `#6366f1` / `#4f46e5` (Hover: `#4338ca`) — Great for SaaS, tech, AI
  - `Emerald / Forest`: `#10b981` / `#059669` (Success, health, fintech)
  - `Sky / Cyan`: `#0284c7` / `#0369a1` (Cloud, developer tools, infrastructure)
  - `Rose / Coral`: `#f43f5e` / `#e11d48` (E-commerce, creative tools)

## 3. Spacing, Borders & Shadows
- **Container Max Widths**: Content: `max-w-3xl` (768px), Dashboard: `max-w-6xl` (1152px) or `max-w-7xl` (1280px), Hero: `max-w-5xl` (1024px)
- **Component Radii**: Buttons (`rounded-lg: 8px`), Cards (`rounded-xl: 12px` or `rounded-2xl: 16px`), Badges (`rounded-full`)

## 4. Components & UI Patterns
- **Buttons**: Primary (solid dark `#18181b`), Secondary (subtle `#f4f4f5` border `#e4e4e7`), Ghost (`hover:bg-zinc-100`), Destructive (`#ef4444`).
- **Cards**: Top icon/badge, Title H3, Subtitle, Body content, Action footer with primary CTA.
- **Navbar**: Sticky top, backdrop blur (`backdrop-blur-md bg-white/80`), Brand logo, Navigation links with active indicators, CTA button.
- **Hero Section**: Eyebrow pill tag ("Introducing v2.0 ->"), H1 headline with gradient emphasis, Subtitle, Dual action buttons.
""",
    },
    "app-routing": {
        "name": "app-routing",
        "aliases": ["routing", "app-and-routing", "navigation", "manifest"],
        "category": "App & Routing",
        "description": "App structure, Next.js App Router conventions, dynamic routes, navigation schemas, and page metadata.",
        "content": """# Skill: App Architecture, Routing & Navigation

## 1. Directory Conventions
- Next.js App Router: `app/layout.tsx`, `app/page.tsx`, `app/(dashboard)/layout.tsx`, `app/api/[route]/route.ts`.
- Vite / React SPA: `src/App.tsx`, `src/pages/Home.tsx`, `src/components/Navbar.tsx`, `src/routes.tsx`.

## 2. Page Metadata & SEO
- Always export `metadata` object in root and nested layouts with `title: { default: 'App', template: '%s | App' }`.
- Include viewport settings: `width=device-width, initial-scale=1.0`.
""",
    },
    "login-auth": {
        "name": "login-auth",
        "aliases": ["auth", "login-and-auth", "jwt", "oauth", "middleware"],
        "category": "Login & Auth",
        "description": "Authentication architectures: JWT tokens, HttpOnly session cookies, OAuth2 providers, and protected route middleware.",
        "content": """# Skill: Login, Authentication & Security Middleware

## 1. Secure Session Token Handling
- Store access tokens in HttpOnly, SameSite=Lax cookies or Bearer Authorization headers.
- Never store secrets or sensitive credentials in client-side localStorage.

## 2. Route Protection Guard
- Validate user session at the edge or server component before rendering protected views (`/dashboard`, `/settings`).
- Redirect unauthenticated requests to `/login?redirect=...`.
""",
    },
    "integration": {
        "name": "integration",
        "aliases": ["server-and-backend", "integrations-and-database", "api-integration", "database", "stripe", "webhooks"],
        "category": "Integrations & Database",
        "description": "Backend API endpoints, database connectivity (SQLite, PostgreSQL, Prisma, Drizzle), Stripe checkout, and webhook verification.",
        "content": """# Skill: Server, Database & Third-Party Integrations

## 1. Environment & Secret Management
- Access runtime variables via `process.env.KEY`. Request missing secrets using `syte_ask_env_var`.

## 2. Database Connectivity & Queries
- SQLite / aiosqlite / Postgres: Always use parameterized queries (`?` or `$1`) to avoid SQL injection.
- Prisma: Store schema in `prisma/schema.prisma`. Run migrations cleanly.

## 3. Stripe & Webhook Signatures
- Verify raw webhook signatures before processing payment and subscription events.
""",
    },
    "optimization-build": {
        "name": "optimization-build",
        "aliases": ["optimization-and-build", "typescript", "lint", "performance"],
        "category": "Optimization & Build",
        "description": "TypeScript compilation validation, linting rules, bundle optimization, and caching strategies.",
        "content": """# Skill: Optimization, TypeScript Validation & Build Performance

## 1. File Generation Priority
- Focus on producing complete, verified code files that Syte will automatically compile and serve.
- Avoid spawning long-running shell scripts or blocking VM commands.

## 2. Syntax & AST Verification
- Run `syte_security_lint_scan` to verify JSX/TSX syntax and ensure clean AST parsing before delivery.
""",
    },
}


# -----------------------------------------------------------------------------
# 3. Discovery Helper Functions
# -----------------------------------------------------------------------------
def discover_skills_catalog(
    category: Optional[str] = None,
    query: Optional[str] = None,
    detailed: bool = False,
) -> Dict[str, Any]:
    """Search and browse all modular skills and capabilities categorized by domain capability."""
    results: Dict[str, Any] = {}
    q = (query or "").lower().strip()
    cat_filter = (category or "").lower().strip()

    for cat_name, cat_data in SKILLS_CATEGORIES_CATALOG.items():
        if cat_filter and cat_filter not in cat_name.lower():
            continue

        matched_capabilities = {}
        for cap_name, cap_info in cat_data.get("capabilities", {}).items():
            if not q or q in cap_name.lower() or q in str(cap_info.get("summary", "")).lower():
                if detailed:
                    matched_capabilities[cap_name] = cap_info
                else:
                    matched_capabilities[cap_name] = cap_info.get("summary", "")

        if matched_capabilities or (not q and not cat_filter):
            results[cat_name] = {
                "description": cat_data.get("description", ""),
                "capabilities_count": len(matched_capabilities),
                "capabilities": matched_capabilities,
            }

    return {
        "ok": True,
        "query": query,
        "category_filter": category,
        "categories_count": len(results),
        "catalog": results,
    }


def list_available_skills() -> List[Dict[str, Any]]:
    """Return summary list of all registered skills and high-level categories."""
    skills = []
    for key, skill in SKILLS_REGISTRY.items():
        skills.append(
            {
                "name": skill["name"],
                "aliases": skill.get("aliases", []),
                "category": skill.get("category", "General"),
                "description": skill["description"],
            }
        )
    return skills


def get_skill_content(skill_name: str) -> Optional[str]:
    """Retrieve full markdown content for a requested skill name, category, or capability."""
    query = (skill_name or "").lower().strip()
    if not query:
        return None

    # 1. Exact match in full skills registry
    if query in SKILLS_REGISTRY:
        return SKILLS_REGISTRY[query]["content"]

    # 2. Alias / substring match in full skills registry
    for key, skill in SKILLS_REGISTRY.items():
        aliases = skill.get("aliases") or []
        if query == key or (isinstance(aliases, list) and query in aliases):
            return skill.get("content")
        if isinstance(aliases, list) and any(query in alias for alias in aliases if isinstance(alias, str)):
            return skill.get("content")

    # 3. Check if query matches a capability in the modular category catalog
    for cat_name, cat_data in SKILLS_CATEGORIES_CATALOG.items():
        if query in cat_name.lower():
            caps_text = "\n".join([f"- **`{k}`**: {v.get('summary', '')}" for k, v in cat_data.get("capabilities", {}).items()])
            return f"# Domain Skill Blueprint: {cat_name}\n\n{cat_data.get('description')}\n\n## Capabilities:\n{caps_text}"

        for cap_name, cap_info in cat_data.get("capabilities", {}).items():
            if query == cap_name.lower() or query in cap_name.lower():
                info_json = json.dumps(cap_info, indent=2)
                return f"# Capability Blueprint: `{cap_name}` ({cat_name})\n\n**Summary**: {cap_info.get('summary')}\n\n```json\n{info_json}\n```"

    # Default fallback
    return SKILLS_REGISTRY["website-create"]["content"]

