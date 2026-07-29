"""Detect and fix Next.js App/Pages router layout before Docker build."""

import json
import re
import shutil
from pathlib import Path

MINIMAL_LAYOUT = """export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
"""

MINIMAL_PAGE = """export default function Home() {
  return <main><h1>Welcome</h1></main>;
}
"""

ROUTER_CANDIDATES = (
    "app",
    "pages",
    "src/app",
    "src/pages",
)

APP_ROUTER_FILES = ("page.tsx", "page.jsx", "page.ts", "page.js", "layout.tsx", "layout.jsx")
PAGES_ROUTER_FILES = ("index.tsx", "index.jsx", "index.ts", "index.js", "_app.tsx", "_app.jsx")


def is_nextjs_repo(repo: Path) -> bool:
    pkg = repo / "package.json"
    if not pkg.exists():
        return False
    try:
        data = json.loads(pkg.read_text())
        deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
        return "next" in deps
    except (json.JSONDecodeError, OSError):
        return False


def _dir_has_router_files(path: Path) -> bool:
    if not path.is_dir():
        return False
    for name in APP_ROUTER_FILES:
        if (path / name).exists():
            return True
    if path.name == "pages" or path.parts[-1] == "pages":
        for name in PAGES_ROUTER_FILES:
            if (path / name).exists():
                return True
        return any(path.glob("**/*.tsx")) or any(path.glob("**/*.jsx"))
    return any(path.rglob("page.tsx")) or any(path.rglob("page.jsx"))


def find_router_dir(repo: Path) -> Path | None:
    for rel in ROUTER_CANDIDATES:
        candidate = repo / rel
        if _dir_has_router_files(candidate):
            return candidate
    return None


def _tree_summary(repo: Path, max_depth: int = 3) -> str:
    lines: list[str] = []
    if not repo.exists():
        return "(empty)"
    for path in sorted(repo.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(repo)
        if len(rel.parts) > max_depth:
            continue
        if "node_modules" in rel.parts or ".git" in rel.parts:
            continue
        lines.append(str(rel))
    return "\n".join(lines[:40]) if lines else "(no source files)"


def fix_nextjs_layout(repo: Path) -> list[str]:
    """Auto-fix common AI scaffolding mistakes (page.tsx at wrong path)."""
    if not is_nextjs_repo(repo):
        return []

    actions: list[str] = []
    if find_router_dir(repo):
        return actions

    # Misplaced App Router files at project root (very common AI mistake).
    moved = False
    root_files = [p for name in APP_ROUTER_FILES + ("globals.css",) if (p := repo / name).exists()]
    if root_files:
        app_dir = repo / "app"
        app_dir.mkdir(exist_ok=True)
        for src in root_files:
            dest = app_dir / src.name
            if not dest.exists():
                shutil.move(str(src), str(dest))
                actions.append(f"Moved {src.name} → app/{src.name}")
                moved = True

    if moved and find_router_dir(repo):
        return actions

    # Misplaced under src/ but not src/app/
    src = repo / "src"
    if src.is_dir() and not find_router_dir(repo):
        src_root_files = [p for name in APP_ROUTER_FILES + ("globals.css",) if (p := src / name).exists()]
        if src_root_files:
            src_app = src / "app"
            src_app.mkdir(exist_ok=True)
            for src_file in src_root_files:
                dest = src_app / src_file.name
                if not dest.exists():
                    shutil.move(str(src_file), str(dest))
                    actions.append(f"Moved src/{src_file.name} → src/app/{src_file.name}")

    if find_router_dir(repo):
        return actions

    # components/ exists but no router — scaffold minimal app/
    if (repo / "components").exists() or (repo / "src" / "components").exists():
        app_dir = repo / "app"
        app_dir.mkdir(exist_ok=True)
        if not (app_dir / "layout.tsx").exists():
            (app_dir / "layout.tsx").write_text(MINIMAL_LAYOUT)
            actions.append("Created app/layout.tsx (components found but no router dir)")
        if not (app_dir / "page.tsx").exists():
            (app_dir / "page.tsx").write_text(MINIMAL_PAGE)
            actions.append("Created app/page.tsx")

    return actions


def validate_nextjs_for_docker(repo: Path) -> tuple[bool, str]:
    """Return (ok, message). Message lists workspace files if invalid."""
    if not is_nextjs_repo(repo):
        return True, ""

    router = find_router_dir(repo)
    if router:
        rel = router.relative_to(repo)
        return True, f"Next.js router found at {rel}/"

    tree = _tree_summary(repo)
    return False, (
        "Next.js project is missing app/ or pages/ directory.\n"
        "Next.js requires ONE of: app/, pages/, src/app/, or src/pages/.\n"
        "Common AI mistake: writing page.tsx at project root instead of app/page.tsx.\n"
        "Use write_file paths like app/app/page.tsx (workspace app/ + Next.js app/).\n\n"
        f"Files currently in workspace:\n{tree}"
    )


def ensure_nextjs_dockerfile(repo: Path) -> list[str]:
    """Create a working Dockerfile when missing for Next.js projects."""
    actions: list[str] = []
    dockerfile = repo / "Dockerfile"
    if dockerfile.exists():
        return actions

    if not is_nextjs_repo(repo):
        return actions

    if not find_router_dir(repo):
        return actions

    for name in ("next.config.mjs", "next.config.js", "next.config.ts"):
        cfg = repo / name
        if cfg.exists():
            break
    else:
        (repo / "next.config.mjs").write_text(
            "/** @type {import('next').NextConfig} */\n"
            "const nextConfig = { output: 'standalone' };\n"
            "export default nextConfig;\n"
        )
        actions.append("Created next.config.mjs with output: 'standalone'.")

    dockerfile.write_text(
        "# Auto-generated by Syte for Next.js\n"
        "FROM node:20-alpine AS builder\n"
        "WORKDIR /app\n"
        "COPY package*.json ./\n"
        "RUN npm install\n"
        "COPY . .\n"
        "ENV NEXT_TELEMETRY_DISABLED=1\n"
        "RUN npm run build\n"
        "\n"
        "FROM node:20-alpine AS runner\n"
        "WORKDIR /app\n"
        "ENV NODE_ENV=production\n"
        "ENV NEXT_TELEMETRY_DISABLED=1\n"
        "ENV HOSTNAME=0.0.0.0\n"
        "EXPOSE 3000\n"
        "COPY --from=builder /app/public ./public\n"
        "COPY --from=builder /app/.next/standalone ./\n"
        "COPY --from=builder /app/.next/static ./.next/static\n"
        'CMD ["node", "server.js"]\n'
    )
    actions.append("Created Dockerfile for Next.js (standalone multi-stage).")
    return actions



# ---------------------------------------------------------------------------
# Conflicting-stack cleanup
#
# Generated websites must be a single Next.js App Router + Tailwind +
# shadcn/ui + Radix project. Leftovers from other stacks (a static
# index.html, a Vite entry, a CRA public/index.html, a Tailwind CDN script)
# silently win over the real app: preview auto-detection picks the static
# file, `next build` compiles a different root, and Tailwind's CDN build
# overrides the compiled design tokens. Files are quarantined rather than
# deleted so nothing is lost.
# ---------------------------------------------------------------------------

QUARANTINE_DIR = ".syte/removed-conflicts"

# Entry points / configs that belong to a different stack than Next.js.
CONFLICTING_STACK_FILES = (
    "index.html",
    "public/index.html",
    "vite.config.ts",
    "vite.config.js",
    "vite.config.mjs",
    "vite.config.cjs",
    "src/main.tsx",
    "src/main.jsx",
    "craco.config.js",
)

# Root-level copies of App Router files that shadow the real router directory.
DUPLICATE_ROUTER_FILES = (
    "page.tsx",
    "page.jsx",
    "layout.tsx",
    "layout.jsx",
    "globals.css",
)

_TAILWIND_CDN_RE = re.compile(
    r"[ \t]*<script[^>]*src=[\"']https?://cdn\.tailwindcss\.com[^\"']*[\"'][^>]*>"
    r"(?:\s*</script>)?[ \t]*\n?",
    re.I,
)

_SKIP_DIR_PARTS = frozenset({"node_modules", ".git", ".next", ".syte", "dist", "build"})


def _skipped(rel: Path) -> bool:
    return any(part in _SKIP_DIR_PARTS for part in rel.parts)


def _quarantine(repo: Path, rel: str) -> str | None:
    """Move ``rel`` out of the build root into the quarantine dir."""
    source = repo / rel
    if not source.exists() or source.is_dir():
        return None
    target_dir = repo / QUARANTINE_DIR
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / rel.replace("/", "__")
    try:
        if target.exists():
            target.unlink()
        shutil.move(str(source), str(target))
    except OSError:
        return None
    return f"Removed conflicting {rel} (moved to {QUARANTINE_DIR}/{target.name})"


def remove_conflicting_stack_files(repo: Path) -> list[str]:
    """Quarantine non-Next.js entry points and duplicate router/style files.

    No-ops unless ``repo`` is a Next.js project, so Vite/CRA/static projects
    are never touched.
    """
    if not is_nextjs_repo(repo):
        return []

    actions: list[str] = []
    for rel in CONFLICTING_STACK_FILES:
        action = _quarantine(repo, rel)
        if action:
            actions.append(action)

    # Root duplicates only matter once a real router directory exists —
    # fix_nextjs_layout() promotes misplaced files when it does not.
    router = find_router_dir(repo)
    if router is not None and router != repo:
        for name in DUPLICATE_ROUTER_FILES:
            if (repo / name).exists() and (router / name).exists():
                action = _quarantine(repo, name)
                if action:
                    actions.append(action)
        if (router / "globals.css").exists() and (repo / "styles" / "globals.css").exists():
            action = _quarantine(repo, "styles/globals.css")
            if action:
                actions.append(action)

    actions.extend(_strip_tailwind_cdn(repo))
    return actions


def _strip_tailwind_cdn(repo: Path, *, max_files: int = 300) -> list[str]:
    """Remove Tailwind CDN script tags that override the compiled token build."""
    actions: list[str] = []
    scanned = 0
    for pattern in ("*.tsx", "*.jsx", "*.html"):
        for path in repo.rglob(pattern):
            if scanned >= max_files:
                return actions
            try:
                rel = path.relative_to(repo)
            except ValueError:
                continue
            if _skipped(rel):
                continue
            scanned += 1
            try:
                text = path.read_text(errors="replace")
            except OSError:
                continue
            if "cdn.tailwindcss.com" not in text:
                continue
            cleaned = _TAILWIND_CDN_RE.sub("", text)
            if cleaned == text:
                continue
            try:
                path.write_text(cleaned)
            except OSError:
                continue
            actions.append(
                f"Removed Tailwind CDN script from {rel} (conflicts with the compiled "
                "Tailwind/shadcn token build)"
            )
    return actions


def find_conflicting_stack_files(repo: Path) -> list[str]:
    """Read-only report of files that conflict with the Next.js/shadcn stack."""
    if not is_nextjs_repo(repo):
        return []

    found = [rel for rel in CONFLICTING_STACK_FILES if (repo / rel).is_file()]
    router = find_router_dir(repo)
    if router is not None and router != repo:
        found.extend(
            name
            for name in DUPLICATE_ROUTER_FILES
            if (repo / name).is_file() and (router / name).is_file()
        )
        if (router / "globals.css").is_file() and (repo / "styles" / "globals.css").is_file():
            found.append("styles/globals.css")

    scanned = 0
    for pattern in ("*.tsx", "*.jsx", "*.html"):
        for path in repo.rglob(pattern):
            if scanned >= 300:
                break
            try:
                rel = path.relative_to(repo)
            except ValueError:
                continue
            if _skipped(rel):
                continue
            scanned += 1
            try:
                if "cdn.tailwindcss.com" in path.read_text(errors="replace"):
                    found.append(f"{rel} (Tailwind CDN script)")
            except OSError:
                continue
    return sorted(dict.fromkeys(found))
