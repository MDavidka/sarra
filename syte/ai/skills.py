"""Modular Skills Registry for Syte Autonomous AI Builder.

Provides comprehensive, structured domain guides, best practices, design systems,
and implementation blueprints that the AI agent can discover and load dynamically.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

SKILLS_REGISTRY: Dict[str, Dict[str, Any]] = {
    "website-create": {
        "name": "website-create",
        "aliases": ["shadcn-ui", "beautiful-ui", "design-system", "tailwind-ui"],
        "category": "Frontend & Design",
        "description": "Comprehensive design system & UI blueprints using modern typography (Inter/Geist), shadcn/ui patterns, harmonious color palettes, sizing, and responsive components.",
        "content": """# Skill: Modern Website Creation & Beautiful UI (shadcn / Inter / Tailwind)

## 1. Typography & Font Hierarchy
- **Primary Font**: Inter (`font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif`)
- **Monospace Font**: JetBrains Mono or SF Mono (`ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace`)
- **Scale Hierarchy**:
  - `Display / Hero Title`: 40px - 56px (`text-4xl` to `text-6xl`), `font-bold` or `font-extrabold`, letter-spacing `-0.03em`, line-height `1.1`
  - `Section Heading (H2)`: 28px - 36px (`text-2xl` to `text-3xl`), `font-semibold`, letter-spacing `-0.02em`
  - `Card Heading (H3)`: 18px - 22px (`text-lg` to `text-xl`), `font-semibold`, letter-spacing `-0.01em`
  - `Subheading / Lead`: 16px - 18px (`text-base` to `text-lg`), `font-normal`, color `text-muted-foreground` (`#71717a`)
  - `Body Text`: 14px - 15px (`text-sm` to `text-base`), line-height `1.6`, color `#18181b` (light) / `#f4f4f5` (dark)
  - `Caption / Badge`: 11px - 12px (`text-xs`), `font-medium` or `font-semibold`, tracking `0.02em`

## 2. Harmonious Color Palettes
- **Neutral Palette (Zinc/Slate)**:
  - Background: `#ffffff` (Card: `#ffffff`, Sub-panel: `#fafafa`, Page BG: `#f8fafc`)
  - Border: `#e4e4e7` (Subtle: `#f4f4f5`, Hover: `#d4d4d8`, Focus: `#18181b`)
  - Foreground: `#09090b` (Muted: `#71717a`, Subtle: `#a1a1aa`)
- **Accent Palettes**:
  - `Indigo / Violet`: `#6366f1` / `#4f46e5` (Hover: `#4338ca`) — Great for SaaS, tech, AI
  - `Emerald / Forest`: `#10b981` / `#059669` (Success, health, fintech)
  - `Sky / Cyan`: `#0284c7` / `#0369a1` (Cloud, developer tools, infrastructure)
  - `Rose / Coral`: `#f43f5e` / `#e11d48` (E-commerce, creative tools)

## 3. Sizing & Spacing Rules
- **Container Max Widths**:
  - Content / Article: `max-w-3xl` (768px)
  - Main App / Dashboard: `max-w-6xl` (1152px) or `max-w-7xl` (1280px)
  - Hero Section: `max-w-5xl` (1024px)
- **Component Padding & Radii**:
  - Buttons: `h-10 px-4 py-2` (Small: `h-8 px-3 text-xs`, Large: `h-12 px-6 text-base`), `rounded-lg` (8px) or `rounded-full` (pills)
  - Cards: `p-6` (Mobile: `p-4`), `rounded-xl` (12px) or `rounded-2xl` (16px), border `1px solid #e4e4e7`, shadow `0 1px 3px rgba(0,0,0,0.05)`
  - Inputs: `h-10 px-3.5 py-2`, `rounded-lg`, border `1px solid #d4d4d8`, focus `ring-2 ring-zinc-900 ring-offset-2`

## 4. Essential shadcn/ui Component Patterns
- **Buttons**: Primary (solid dark `#18181b`), Secondary (subtle `#f4f4f5` border `#e4e4e7`), Ghost (transparent hover `#f4f4f5`), Destructive (`#ef4444`).
- **Cards**: Top badge/icon, Title H3, Description paragraph, Body content, Action footer with primary CTA.
- **Navbar**: Sticky top, backdrop blur (`backdrop-blur-md bg-white/80`), Brand logo, Navigation links with active indicators, CTA button, Mobile hamburger sheet.
- **Hero Section**: Eyebrow pill tag ("Introducing v2.0 ->"), H1 headline with gradient emphasis, Subtitle, Dual action buttons (Primary CTA + Secondary demo/doc button), Floating interactive preview/mockup card with subtle drop shadow.
- **Feature Grid**: 3-column responsive grid (`grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6`), with icon box, title, description, and interactive hover states.

## 5. Responsive Design Standards
- Mobile-first layout: Ensure all touch targets are at least `44px x 44px`.
- Use `gap-4 sm:gap-6 lg:gap-8` for dynamic scaling.
- Prevent horizontal overflows (`overflow-x-hidden`, `max-w-full`, `box-border`).
""",
    },
    "integration": {
        "name": "integration",
        "aliases": ["api-integration", "auth", "database", "stripe", "webhooks"],
        "category": "Backend & Architecture",
        "description": "Enterprise API integrations, OAuth/JWT authentication, secure webhook processing, Stripe billing, and database connectivity (SQLite, PostgreSQL, Prisma, Drizzle).",
        "content": """# Skill: Robust Backend Integrations & APIs

## 1. Environment & Secret Management
- Always access secrets via environment variables (`process.env.KEY` in Node, `os.environ.get('KEY')` in Python).
- When a new secret is needed, request it using `syte_ask_env_var` so it is stored directly in server `.env` without exposing secrets in chat context.

## 2. Authentication Patterns
- **JWT Authentication**: Store access token in HttpOnly SameSite cookies or Bearer Authorization headers. Always verify token expiry (`exp`) and signature.
- **Session Tokens**: Use secure random tokens (`crypto.randomBytes(32).toString('hex')` / `secrets.token_hex(32)`) with database lookup and expiration timestamps.

## 3. Database Connectivity
- **SQLite / aiosqlite**: Use connection context managers and parameterized queries (`?` in SQLite, `%s` / `$1` in Postgres) to prevent SQL injection.
- **Prisma / Drizzle**: Maintain schema files in `prisma/schema.prisma` or `src/db/schema.ts`. Always run migrations via terminal commands (`npx prisma migrate dev` / `npx drizzle-kit push`).

## 4. Stripe & Payment Processing
- **Checkout Sessions**: Create server-side session with line items, success_url, and cancel_url.
- **Webhook Endpoint**: ALWAYS verify the webhook signature using `stripe.webhooks.constructEvent(payload, sig, endpointSecret)` before processing event types (`checkout.session.completed`, `customer.subscription.updated`).

## 5. Webhook Handlers & Background Jobs
- Return HTTP 200 immediately upon valid payload receipt, and process long-running tasks asynchronously.
- Implement idempotency keys to avoid duplicate transaction processing.
""",
    },
    "providers": {
        "name": "providers",
        "aliases": ["llm-providers", "vertex-ai", "openai", "anthropic", "gemini", "openrouter"],
        "category": "AI & LLMs",
        "description": "Multi-provider LLM configurations, endpoints, streaming SSE parsers, tool calling schemas, and error handling for OpenAI, Anthropic, Google Cloud Vertex AI, Google Gemini, DeepSeek, and OpenRouter.",
        "content": """# Skill: LLM Provider Configuration & Multi-Provider Architecture

## 1. Provider Endpoint Map
- **OpenAI**: `https://api.openai.com/v1/chat/completions` (Headers: `Authorization: Bearer <KEY>`)
- **Anthropic**: `https://api.anthropic.com/v1/messages` (Headers: `x-api-key: <KEY>`, `anthropic-version: 2023-06-01`)
- **Google Gemini**: `https://generativelanguage.googleapis.com/v1beta/openai/chat/completions` (Headers: `x-goog-api-key: <KEY>`, `Authorization: Bearer <KEY>`)
- **Google Cloud Vertex AI**: `https://{REGION}-aiplatform.googleapis.com/v1beta1/projects/{PROJECT}/locations/{REGION}/endpoints/openapi/chat/completions`
- **DeepSeek**: `https://api.deepseek.com/v1/chat/completions`
- **OpenRouter**: `https://openrouter.ai/api/v1/chat/completions` (Headers: `HTTP-Referer`, `X-Title`)

## 2. Google Vertex AI & Gemini Best Practices
- Dual Header Authentication: Google endpoints accept `x-goog-api-key` for API keys (`AIzaSy...`) and `Authorization: Bearer` for OAuth2 access tokens (`ya29...`).
- When project ID is not specified or endpoint returns 404, fallback to `generativelanguage.googleapis.com/v1beta/openai`.
- Model aliases: `gemini-2.0-flash`, `gemini-2.0-flash-lite`, `gemini-1.5-pro`, `gemini-1.5-flash`.

## 3. Tool Calling Format
- OpenAI / OpenAI-Compatible: `tools: [{type: 'function', function: {name, description, parameters}}]`, `tool_choice: 'auto'`.
- SSE Stream chunks: Accumulate `delta.tool_calls` by index until complete JSON argument string is parsed.
""",
    },
    "cloud-code": {
        "name": "cloud-code",
        "aliases": ["devops", "deployments", "docker", "containers", "vm-runtime"],
        "category": "Infrastructure & DevOps",
        "description": "Zero-downtime production deployments, optimized Dockerfile multi-stage builds, process management, health checking, and reverse proxy routing on Syte host VM.",
        "content": """# Skill: Cloud-Code DevOps, Containerization & Zero-Downtime Deployments

## 1. Project Runtime Management on Syte
- Node.js apps: Build with `npm run build` or `pnpm build`, serve on assigned `$PORT`.
- Python apps: Use `uvicorn` / `gunicorn` or FastAPI entrypoints binding to `0.0.0.0:$PORT`.
- Static apps: Serve via internal Syte static file server or nginx.

## 2. Dockerfile Optimization
- Use multi-stage builds:
  - Stage 1 (Builder): Install full dependencies and compile assets (`node:20-alpine` or `python:3.12-slim`).
  - Stage 2 (Runner): Copy only build artifacts and production node_modules / virtualenv.
- Ensure non-root user execution (`USER node` / `USER appuser`).

## 3. Health Checks & Process Verification
- Implement `/health` or `/api/health` endpoint returning `{"status": "ok"}`.
- Check live listening ports using `syte_run_command` with `ss -tulpn | grep :PORT` or `curl -I http://localhost:PORT`.

## 4. Deployment Diagnostics
- When a build fails, inspect stdout/stderr logs with `syte_get_deployment_logs`.
- Common failure vectors: Missing npm dependencies, TypeScript compilation errors (`tsc`), missing environment variables, port conflicts.
""",
    },
}


def list_available_skills() -> List[Dict[str, Any]]:
    """Return summary list of all registered skills."""
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
    """Retrieve full markdown content for a requested skill name or alias."""
    query = (skill_name or "").lower().strip()
    # Exact match
    if query in SKILLS_REGISTRY:
        return SKILLS_REGISTRY[query]["content"]

    # Alias / substring match
    for key, skill in SKILLS_REGISTRY.items():
        aliases = skill.get("aliases") or []
        if query == key or (isinstance(aliases, list) and query in aliases):
            return skill.get("content")
        if isinstance(aliases, list) and any(query in alias for alias in aliases if isinstance(alias, str)):
            return skill.get("content")

    return None

