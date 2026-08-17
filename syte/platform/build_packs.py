"""Build packs: turn a source tree into a Docker image recipe.

Sarra previously *required* a Dockerfile — ``syte.deployment._resolve_deploy``
refuses to deploy without one and tells the operator to add one. That is the
single biggest gap against Coolify, which detects the language and synthesises
the Dockerfile for you (Nixpacks/Railpack), or wraps a build output in nginx
(Static), or reads your Dockerfile, or orchestrates a compose file.

This module implements all of those. It is **pure**: the detection and
generation functions take a :class:`~syte.platform.types.BuildContext` (a set of
file paths plus a parsed ``package.json``) and return a
:class:`~syte.platform.types.BuildPlan`. The single I/O function,
:func:`scan_context`, is kept at the bottom and clearly separated — same
pure/effectful split as ``caddy_routes`` vs ``certificates``.

Design choices worth knowing:

* Generated Dockerfiles copy the whole application directory into the runtime
  stage rather than trying to compute a minimal artifact set per framework.
  A slightly larger image is a much better trade than a deployment that fails
  because a framework moved its output directory. Where a framework has a
  reliable slim path (Go, Rust static binaries) we do use it.
* Every generated runtime honours ``$PORT`` and binds ``0.0.0.0``. Binding
  localhost inside a container is the most common cause of the "Bad Gateway"
  reports in Coolify's own troubleshooting docs, so the generators never leave
  it to chance.
* Nothing here shells out or writes files. The caller decides where the
  Dockerfile lands, which is what makes the whole matrix unit-testable.
"""

from __future__ import annotations

import json
import re
import shlex
from pathlib import Path

from syte.platform.types import (
    BuildContext,
    BuildPack,
    BuildPlan,
)

# --------------------------------------------------------------------------- #
# Base images
# --------------------------------------------------------------------------- #

# Pinned to Alpine variants where the ecosystem tolerates musl. Node, Python
# and Ruby native extensions frequently do not, so those get a build toolchain
# installed in the build stage instead of switching to a Debian base (which
# would roughly triple image size).
DEFAULT_NODE_VERSION = "20"
DEFAULT_PYTHON_VERSION = "3.12"
DEFAULT_GO_VERSION = "1.23"
DEFAULT_RUST_VERSION = "1.83"
DEFAULT_PHP_VERSION = "8.3"
DEFAULT_RUBY_VERSION = "3.3"
DEFAULT_JAVA_VERSION = "21"
DEFAULT_ELIXIR_VERSION = "1.17"
DEFAULT_DOTNET_VERSION = "8.0"
DEFAULT_BUN_VERSION = "1"
DEFAULT_DENO_VERSION = "2"
DEFAULT_STATIC_IMAGE = "nginx:alpine"

# Default port per language. Overridden by an explicit ``ports_exposes`` on the
# application, and by a framework-specific hint where one exists.
DEFAULT_PORTS: dict[str, int] = {
    "node": 3000,
    "bun": 3000,
    "deno": 8000,
    "python": 8000,
    "go": 8080,
    "rust": 8080,
    "php": 80,
    "ruby": 3000,
    "java": 8080,
    "elixir": 4000,
    "dotnet": 8080,
    "static": 80,
}

# Node package managers, in the order they are probed. pnpm and yarn lockfiles
# are checked before npm because a repo can contain a stale package-lock.json
# alongside the lockfile actually in use.
_NODE_PACKAGE_MANAGERS: tuple[tuple[str, str], ...] = (
    ("pnpm-lock.yaml", "pnpm"),
    ("yarn.lock", "yarn"),
    ("bun.lockb", "bun"),
    ("bun.lock", "bun"),
    ("package-lock.json", "npm"),
    ("npm-shrinkwrap.json", "npm"),
)

# Framework detection for Node: dependency name -> (framework, default port,
# build script needed, start command). Ordered because a Next.js app also
# depends on react, and Nuxt also depends on vue.
_NODE_FRAMEWORKS: tuple[tuple[str, str, int], ...] = (
    ("next", "nextjs", 3000),
    ("nuxt", "nuxt", 3000),
    ("@remix-run/serve", "remix", 3000),
    ("@remix-run/node", "remix", 3000),
    ("@sveltejs/kit", "sveltekit", 3000),
    ("astro", "astro", 4321),
    ("@nestjs/core", "nestjs", 3000),
    ("gatsby", "gatsby", 8000),
    ("@angular/core", "angular", 80),
    ("nitropack", "nitro", 3000),
    ("vite", "vite", 80),
    ("react-scripts", "cra", 80),
    ("vue", "vue", 80),
    ("express", "express", 3000),
    ("fastify", "fastify", 3000),
    ("koa", "koa", 3000),
    ("hono", "hono", 3000),
)

# Frameworks whose production output is a directory of static files. These get
# routed to the static build pack automatically when the operator picks
# Nixpacks, matching what Coolify's docs recommend doing by hand.
_STATIC_FRAMEWORKS = frozenset({"vite", "cra", "angular", "gatsby", "astro", "vue"})

# Default publish directory per static framework.
_STATIC_PUBLISH_DIRS: dict[str, str] = {
    "vite": "dist",
    "vue": "dist",
    "astro": "dist",
    "cra": "build",
    "angular": "dist",
    "gatsby": "public",
    "sveltekit": "build",
    "nuxt": "dist",
}

# Python dependency managers, probed in order.
_PYTHON_MANAGERS: tuple[tuple[str, str], ...] = (
    ("uv.lock", "uv"),
    ("poetry.lock", "poetry"),
    ("Pipfile.lock", "pipenv"),
    ("Pipfile", "pipenv"),
    ("pdm.lock", "pdm"),
    ("requirements.txt", "pip"),
    ("pyproject.toml", "pip-project"),
    ("setup.py", "pip-project"),
)

STATIC_FILE_MARKERS = ("index.html", "index.htm", "public/index.html", "dist/index.html")


class BuildPackError(ValueError):
    """Raised when no build pack can handle the source tree.

    Carries an actionable message because it is surfaced verbatim in the
    deployment log — the same convention the rest of Syte follows.
    """


# --------------------------------------------------------------------------- #
# Detection
# --------------------------------------------------------------------------- #


def detect_language(ctx: BuildContext) -> str:
    """Best-effort language detection from the file inventory.

    Order matters: a Next.js repo may vendor a ``go.mod`` in a tools directory,
    and a Rails app has a ``package.json`` for its asset pipeline, so the
    manifest that defines the *deployable* unit is probed first.
    """
    if ctx.has("package.json"):
        return "node"
    if ctx.has("bun.lockb", "bunfig.toml", "bun.lock"):
        return "bun"
    if ctx.has("deno.json", "deno.jsonc", "deno.lock"):
        return "deno"
    if ctx.has("Gemfile"):
        return "ruby"
    if ctx.has("mix.exs"):
        return "elixir"
    if ctx.has("composer.json", "artisan"):
        return "php"
    if ctx.has("go.mod"):
        return "go"
    if ctx.has("Cargo.toml"):
        return "rust"
    if ctx.has("pom.xml", "build.gradle", "build.gradle.kts"):
        return "java"
    if any(name.endswith((".csproj", ".sln")) for name in ctx.files):
        return "dotnet"
    if any(ctx.has(marker) for marker, _ in _PYTHON_MANAGERS):
        return "python"
    if any(name.endswith(".py") for name in ctx.files):
        return "python"
    if any(ctx.has(marker) for marker in STATIC_FILE_MARKERS):
        return "static"
    raise BuildPackError(
        "Could not detect the application type. No package.json, requirements.txt, "
        "go.mod, Cargo.toml, Gemfile, composer.json, pom.xml, mix.exs or index.html "
        "was found in the build directory.\n"
        "Fix by one of: set the Base Directory if your app lives in a subfolder, "
        "add a Dockerfile and switch the build pack to 'dockerfile', or set the "
        "build pack to 'static' and point Publish Directory at your output folder."
    )


def detect_node_package_manager(ctx: BuildContext) -> str:
    """Which package manager to use. Falls back to npm (always available)."""
    engines = ctx.package_json.get("packageManager")
    if isinstance(engines, str):
        # Corepack's `packageManager: "pnpm@9.1.0"` is the most explicit signal
        # a repo can give, so it beats lockfile sniffing.
        name = engines.split("@", 1)[0].strip().lower()
        if name in {"npm", "yarn", "pnpm", "bun"}:
            return name
    for lockfile, manager in _NODE_PACKAGE_MANAGERS:
        if ctx.has(lockfile):
            return manager
    return "npm"


def detect_node_framework(ctx: BuildContext) -> tuple[str, int]:
    """``(framework, default_port)`` from package.json dependencies."""
    deps: dict[str, object] = {}
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = ctx.package_json.get(key)
        if isinstance(section, dict):
            deps.update(section)
    for dependency, framework, port in _NODE_FRAMEWORKS:
        if dependency in deps:
            return framework, port
    return "node", DEFAULT_PORTS["node"]


def detect_python_manager(ctx: BuildContext) -> str:
    for marker, manager in _PYTHON_MANAGERS:
        if ctx.has(marker):
            return manager
    return "pip"


def node_scripts(ctx: BuildContext) -> dict[str, str]:
    scripts = ctx.package_json.get("scripts")
    if isinstance(scripts, dict):
        return {str(k): str(v) for k, v in scripts.items()}
    return {}


def is_static_output(ctx: BuildContext) -> bool:
    """True when a Node project builds to a directory of static assets.

    Used to auto-route SPA frameworks to the static build pack so they end up
    behind nginx instead of running a dev server in production.
    """
    if not ctx.has("package.json"):
        return bool(any(ctx.has(marker) for marker in STATIC_FILE_MARKERS))
    framework, _ = detect_node_framework(ctx)
    if framework not in _STATIC_FRAMEWORKS:
        return False
    # A framework that ships a `start` script expects to run a server (e.g. an
    # Astro SSR build), so do not force it behind nginx.
    scripts = node_scripts(ctx)
    return not (framework == "astro" and "start" in scripts)


# --------------------------------------------------------------------------- #
# Command inference
# --------------------------------------------------------------------------- #


def node_install_command(manager: str, ctx: BuildContext) -> str:
    """Install command honouring lockfile-frozen installs where available.

    Frozen/CI installs are used only when the matching lockfile exists —
    ``npm ci`` hard-fails without ``package-lock.json``, and that failure looks
    like a broken build pack rather than a missing lockfile.
    """
    if manager == "pnpm":
        return "pnpm install --frozen-lockfile" if ctx.has("pnpm-lock.yaml") else "pnpm install"
    if manager == "yarn":
        return "yarn install --frozen-lockfile" if ctx.has("yarn.lock") else "yarn install"
    if manager == "bun":
        return "bun install --frozen-lockfile" if ctx.has("bun.lockb", "bun.lock") else "bun install"
    if ctx.has("package-lock.json", "npm-shrinkwrap.json"):
        return "npm ci"
    return "npm install"


def node_build_command(manager: str, ctx: BuildContext) -> str:
    scripts = node_scripts(ctx)
    if "build" not in scripts:
        return ""
    return "bun run build" if manager == "bun" else f"{manager} run build"


def node_start_command(manager: str, ctx: BuildContext, framework: str) -> str:
    """Start command, preferring an explicit script over a framework guess."""
    scripts = node_scripts(ctx)
    runner = "bun" if manager == "bun" else manager

    if "start" in scripts:
        return "bun run start" if manager == "bun" else f"{runner} run start"

    # Framework fallbacks for repos that only define `dev` and `build`.
    framework_starts = {
        "nextjs": f"{runner} run next start -p ${{PORT:-3000}}" if manager != "npm" else "npx next start -p ${PORT:-3000}",
        "nuxt": "node .output/server/index.mjs",
        "remix": "npx remix-serve ./build/server/index.js",
        "sveltekit": "node build/index.js",
        "nestjs": "node dist/main.js",
        "astro": "node ./dist/server/entry.mjs",
    }
    if framework in framework_starts:
        return framework_starts[framework]

    main = ctx.package_json.get("main")
    if isinstance(main, str) and main.strip():
        return f"node {shlex.quote(main.strip())}"
    for candidate in ("server.js", "index.js", "app.js", "src/index.js", "dist/index.js", "main.js"):
        if ctx.has(candidate):
            return f"node {candidate}"
    return "npm start"


def python_install_command(manager: str) -> str:
    """Dependency install for the detected Python tool.

    ``--no-cache-dir`` everywhere: the pip/uv cache is pure build-time bloat in
    a layer that is never reused at runtime.
    """
    return {
        "uv": "uv sync --frozen --no-dev",
        "poetry": (
            "pip install --no-cache-dir poetry "
            "&& poetry config virtualenvs.create false "
            "&& poetry install --no-interaction --no-ansi --without dev --no-root"
        ),
        "pipenv": "pip install --no-cache-dir pipenv && pipenv install --deploy --system",
        "pdm": "pip install --no-cache-dir pdm && pdm install --prod --no-lock --no-editable",
        "pip": "pip install --no-cache-dir -r requirements.txt",
        "pip-project": "pip install --no-cache-dir .",
    }.get(manager, "pip install --no-cache-dir -r requirements.txt")


def python_start_command(ctx: BuildContext) -> str:
    """Detect the WSGI/ASGI entrypoint, else a plain script.

    Web frameworks are probed first because a Django project also has a
    ``main.py`` in some layouts, and running that instead of gunicorn would
    start nothing useful.
    """
    if ctx.has("manage.py"):
        # Django: find the package holding wsgi.py so gunicorn gets a real target.
        for name in sorted(ctx.files):
            if name.endswith("/wsgi.py"):
                module = name[: -len("/wsgi.py")].replace("/", ".")
                return f"gunicorn {module}.wsgi:application --bind 0.0.0.0:${{PORT:-8000}}"
        return "python manage.py runserver 0.0.0.0:${PORT:-8000}"

    asgi_candidates = (
        ("main.py", "main:app"),
        ("app.py", "app:app"),
        ("app/main.py", "app.main:app"),
        ("src/main.py", "src.main:app"),
        ("api/main.py", "api.main:app"),
        ("asgi.py", "asgi:app"),
    )
    for filename, target in asgi_candidates:
        if ctx.has(filename):
            return f"uvicorn {target} --host 0.0.0.0 --port ${{PORT:-8000}}"

    for candidate in ("main.py", "app.py", "run.py", "server.py", "bot.py", "__main__.py"):
        if ctx.has(candidate):
            return f"python {candidate}"
    return "python main.py"


# --------------------------------------------------------------------------- #
# Dockerfile generators
# --------------------------------------------------------------------------- #


def _nginx_spa_conf_lines(*, spa_fallback: bool = True) -> list[str]:
    """nginx config written inline by the static runtime stage.

    Generated with ``printf`` rather than a Dockerfile heredoc so the output
    works on any Docker version — heredocs need BuildKit's dockerfile:1.4+
    frontend, which is not guaranteed on a self-managed host.

    ``try_files ... /index.html`` is what makes client-side routing work; a
    plain nginx image returns 404 for ``/dashboard`` on a React Router app.

    Every nginx string literal below uses double quotes. The config lines are
    wrapped in shell single quotes, and a nested single quote would terminate
    the shell string early and emit a syntactically broken nginx config —
    which surfaces as a container that exits immediately on start.
    """
    try_files = "try_files $uri $uri/ /index.html;" if spa_fallback else "try_files $uri $uri/ =404;"
    conf = [
        "server {",
        "    listen 80;",
        "    listen [::]:80;",
        "    server_name _;",
        "    root /usr/share/nginx/html;",
        "    index index.html index.htm;",
        "    absolute_redirect off;",
        "    gzip on;",
        (
            "    gzip_types text/plain text/css application/json application/javascript "
            "text/xml application/xml image/svg+xml;"
        ),
        '    location = /health { access_log off; default_type text/plain; return 200 "ok"; }',
        "    location ~* \\.(?:css|js|woff2?|ttf|eot|svg|png|jpg|jpeg|gif|webp|avif|ico)$ {",
        "        expires 30d;",
        '        add_header Cache-Control "public, immutable";',
        "    }",
        "    location / {",
        f"        {try_files}",
        "    }",
        "}",
    ]
    for line in conf:
        if "'" in line:  # pragma: no cover — guards the invariant documented above
            raise BuildPackError(f"nginx config line must not contain a single quote: {line}")
    quoted = " ".join(f"'{line}'" for line in conf)
    return [f"RUN printf '%s\\n' {quoted} > /etc/nginx/conf.d/default.conf"]


def _cmd_line(command: str) -> str:
    """Emit CMD in exec form when safe, shell form when the command needs it.

    Shell form is required whenever the command contains ``$PORT`` expansion,
    a pipe, or ``&&`` — using exec form there passes the literal text to the
    binary and the container exits immediately.
    """
    needs_shell = any(token in command for token in ("$", "&&", "||", "|", ">", "<", ";", "*"))
    if needs_shell:
        return f'CMD ["sh", "-c", {json.dumps(command)}]'
    parts = shlex.split(command)
    return "CMD " + json.dumps(parts)


def _env_lines(port: int, extra: dict[str, str] | None = None) -> list[str]:
    lines = [
        f"ENV PORT={port}",
        "ENV HOST=0.0.0.0",
        "ENV HOSTNAME=0.0.0.0",
    ]
    for key, value in (extra or {}).items():
        lines.append(f"ENV {key}={value}")
    return lines


def _build_arg_lines(ctx: BuildContext) -> list[str]:
    """Expose build-time env vars as ARG+ENV so the build can read them.

    Both are needed: ``ARG`` receives the ``--build-arg`` value, and the
    matching ``ENV`` is what makes it visible to the framework's build step
    (Vite and Next only read ``process.env``).
    """
    lines: list[str] = []
    for key, _value in ctx.build_args:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            lines.append(f"ARG {key}")
            lines.append(f"ENV {key}=${key}")
    return lines


def generate_node_dockerfile(ctx: BuildContext) -> BuildPlan:
    manager = detect_node_package_manager(ctx)
    framework, framework_port = detect_node_framework(ctx)
    node_version = ctx.node_version or DEFAULT_NODE_VERSION
    port = ctx.port or framework_port
    install = ctx.install_command or node_install_command(manager, ctx)
    build = ctx.build_command if ctx.build_command else node_build_command(manager, ctx)
    start = ctx.start_command or node_start_command(manager, ctx, framework)
    notes: list[str] = [f"Detected Node.js ({framework}) using {manager}."]

    base = f"node:{node_version}-alpine"
    lines = [
        f"# Generated by Syte build packs — Node.js / {framework} / {manager}",
        f"FROM {base} AS build",
        "WORKDIR /app",
        # libc6-compat covers prebuilt glibc binaries (esbuild, sharp); the
        # toolchain covers packages that compile on install (bcrypt, sqlite3).
        "RUN apk add --no-cache libc6-compat python3 make g++",
    ]
    if manager == "pnpm":
        lines.append("RUN corepack enable && corepack prepare pnpm@latest --activate")
    elif manager == "yarn":
        lines.append("RUN corepack enable")
    elif manager == "bun":
        lines.append("RUN npm install -g bun")

    lines += _build_arg_lines(ctx)
    lines += [
        "COPY . .",
        f"RUN {install}",
    ]
    if build:
        lines.append(f"RUN {build}")

    lines += [
        "",
        f"FROM {base} AS runtime",
        "ENV NODE_ENV=production",
        *_env_lines(port),
        "WORKDIR /app",
        "RUN apk add --no-cache curl libc6-compat",
    ]
    if manager == "pnpm":
        lines.append("RUN corepack enable && corepack prepare pnpm@latest --activate")
    elif manager == "yarn":
        lines.append("RUN corepack enable")
    elif manager == "bun":
        lines.append("RUN npm install -g bun")

    lines += [
        "COPY --from=build /app ./",
        # The node images ship an unprivileged `node` user; using it means a
        # container escape does not land on root.
        "RUN chown -R node:node /app",
        "USER node",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]

    if framework == "nextjs":
        notes.append(
            "Next.js detected. Adding `output: 'standalone'` to next.config.js and "
            "switching the build pack to 'dockerfile' produces a much smaller image."
        )

    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="node",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        build_command=build,
        start_command=start,
        notes=tuple(notes),
    )


def generate_bun_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["bun"]
    install = ctx.install_command or "bun install --frozen-lockfile"
    build = ctx.build_command
    start = ctx.start_command or "bun run start"
    base = f"oven/bun:{DEFAULT_BUN_VERSION}"
    lines = [
        "# Generated by Syte build packs — Bun",
        f"FROM {base} AS build",
        "WORKDIR /app",
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {install}",
    ]
    if build:
        lines.append(f"RUN {build}")
    lines += [
        "",
        f"FROM {base} AS runtime",
        "ENV NODE_ENV=production",
        *_env_lines(port),
        "WORKDIR /app",
        "COPY --from=build /app ./",
        "USER bun",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="bun",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        build_command=build,
        start_command=start,
        notes=("Detected Bun runtime.",),
    )


def generate_deno_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["deno"]
    entry = "main.ts"
    for candidate in ("main.ts", "mod.ts", "server.ts", "src/main.ts", "index.ts"):
        if ctx.has(candidate):
            entry = candidate
            break
    install = ctx.install_command or f"deno cache {entry}"
    start = ctx.start_command or f"deno run -A {entry}"
    lines = [
        "# Generated by Syte build packs — Deno",
        f"FROM denoland/deno:{DEFAULT_DENO_VERSION}",
        "WORKDIR /app",
        *_env_lines(port),
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {install}",
        "USER deno",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="deno",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        start_command=start,
        notes=(f"Detected Deno, entrypoint {entry}.",),
    )


def generate_python_dockerfile(ctx: BuildContext) -> BuildPlan:
    manager = detect_python_manager(ctx)
    version = ctx.python_version or DEFAULT_PYTHON_VERSION
    port = ctx.port or DEFAULT_PORTS["python"]
    install = ctx.install_command or python_install_command(manager)
    build = ctx.build_command
    start = ctx.start_command or python_start_command(ctx)
    base = f"python:{version}-slim"

    needs_uvicorn = "uvicorn" in start and not ctx.has("requirements.txt")
    needs_gunicorn = "gunicorn" in start

    lines = [
        f"# Generated by Syte build packs — Python / {manager}",
        f"FROM {base} AS runtime",
        # Unbuffered output is what makes `docker logs` show tracebacks live
        # instead of holding them in a pipe buffer until the process dies.
        "ENV PYTHONUNBUFFERED=1",
        "ENV PYTHONDONTWRITEBYTECODE=1",
        "ENV PIP_DISABLE_PIP_VERSION_CHECK=1",
        *_env_lines(port),
        "WORKDIR /app",
        (
            "RUN apt-get update "
            "&& apt-get install -y --no-install-recommends build-essential curl libpq-dev "
            "&& rm -rf /var/lib/apt/lists/*"
        ),
    ]
    if manager == "uv":
        lines.append("RUN pip install --no-cache-dir uv")
    lines += _build_arg_lines(ctx)
    lines += [
        "COPY . .",
        f"RUN {install}",
    ]
    extra_packages = [pkg for pkg, needed in (("uvicorn[standard]", needs_uvicorn), ("gunicorn", needs_gunicorn)) if needed]
    if extra_packages:
        # The detected start command references a server the project may not
        # list as a dependency; installing it here beats failing at runtime with
        # "gunicorn: not found".
        lines.append(f"RUN pip install --no-cache-dir {' '.join(extra_packages)}")
    if build:
        lines.append(f"RUN {build}")
    if ctx.has("manage.py"):
        # collectstatic is a no-op without STATIC_ROOT, so tolerate failure
        # rather than breaking every Django deploy that serves no static files.
        lines.append("RUN python manage.py collectstatic --noinput || true")
    lines += [
        "RUN useradd --create-home --shell /bin/bash app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    notes = [f"Detected Python ({manager})."]
    if ctx.has("manage.py"):
        notes.append("Django project detected — running collectstatic during build.")
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="python",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        build_command=build,
        start_command=start,
        notes=tuple(notes),
    )


def generate_go_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["go"]
    build = ctx.build_command or "go build -ldflags='-s -w' -o /out/app ./..."
    start = ctx.start_command or "/app/app"
    lines = [
        "# Generated by Syte build packs — Go",
        f"FROM golang:{DEFAULT_GO_VERSION}-alpine AS build",
        "WORKDIR /src",
        "RUN apk add --no-cache git ca-certificates",
        "COPY go.mod go.sum* ./",
        # Dependency download is its own layer so a source-only change does not
        # re-download the module cache.
        "RUN go mod download || true",
        *_build_arg_lines(ctx),
        "COPY . .",
        "ENV CGO_ENABLED=0",
        f"RUN {build}",
        "",
        # Go produces a static binary, so the runtime needs nothing but certs —
        # this is where the slim path is genuinely worth taking.
        "FROM alpine:3.20 AS runtime",
        "RUN apk add --no-cache ca-certificates curl",
        *_env_lines(port),
        "WORKDIR /app",
        "COPY --from=build /out/app /app/app",
        "RUN adduser -D -H app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="go",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        build_command=build,
        start_command=start,
        notes=("Detected Go — building a static binary on a minimal Alpine runtime.",),
    )


def generate_rust_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["rust"]
    binary = _rust_binary_name(ctx)
    build = ctx.build_command or "cargo build --release --locked"
    start = ctx.start_command or f"/app/{binary}"
    lines = [
        "# Generated by Syte build packs — Rust",
        f"FROM rust:{DEFAULT_RUST_VERSION}-slim AS build",
        "WORKDIR /src",
        (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "pkg-config libssl-dev ca-certificates && rm -rf /var/lib/apt/lists/*"
        ),
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {build}",
        "",
        "FROM debian:bookworm-slim AS runtime",
        (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "ca-certificates curl libssl3 && rm -rf /var/lib/apt/lists/*"
        ),
        *_env_lines(port),
        "WORKDIR /app",
        f"COPY --from=build /src/target/release/{binary} /app/{binary}",
        "RUN useradd --create-home app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="rust",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        build_command=build,
        start_command=start,
        notes=(f"Detected Rust — release binary '{binary}'.",),
    )


def _rust_binary_name(ctx: BuildContext) -> str:
    """Crate name from Cargo.toml, used as the release binary path.

    ``BuildContext`` intentionally does not carry parsed TOML, so fall back to a
    conventional name when the caller has not supplied a start command.
    """
    name = ctx.package_json.get("cargo_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return "app"


def generate_php_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["php"]
    is_laravel = ctx.has("artisan")
    install = ctx.install_command or (
        "composer install --no-dev --optimize-autoloader --no-interaction --prefer-dist"
        if ctx.has("composer.json")
        else ""
    )
    doc_root = "/var/www/html/public" if (is_laravel or ctx.has("public/index.php")) else "/var/www/html"
    lines = [
        "# Generated by Syte build packs — PHP" + (" / Laravel" if is_laravel else ""),
        f"FROM php:{DEFAULT_PHP_VERSION}-apache AS runtime",
        (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "git unzip curl libzip-dev libpng-dev libonig-dev libxml2-dev libpq-dev "
            "&& docker-php-ext-install pdo pdo_mysql pdo_pgsql zip gd opcache "
            "&& rm -rf /var/lib/apt/lists/*"
        ),
        "COPY --from=composer:2 /usr/bin/composer /usr/bin/composer",
        "RUN a2enmod rewrite",
        # Apache's default vhost points at /var/www/html; Laravel serves from
        # public/ and exposing the project root instead would leak .env.
        (
            f"RUN sed -ri 's!/var/www/html!{doc_root}!g' /etc/apache2/sites-available/000-default.conf "
            "/etc/apache2/apache2.conf"
        ),
        "WORKDIR /var/www/html",
        *_build_arg_lines(ctx),
        "COPY . .",
    ]
    if install:
        lines.append(f"RUN {install}")
    if is_laravel:
        lines += [
            "RUN php artisan config:cache || true",
            "RUN php artisan route:cache || true",
            "RUN chown -R www-data:www-data storage bootstrap/cache || true",
        ]
    lines += [
        "RUN chown -R www-data:www-data /var/www/html",
        f"EXPOSE {port}",
        'CMD ["apache2-foreground"]',
    ]
    notes = ["Detected PHP with Apache."]
    if is_laravel:
        notes.append("Laravel detected — document root set to public/ and caches warmed.")
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="php",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        start_command="apache2-foreground",
        notes=tuple(notes),
    )


def generate_ruby_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["ruby"]
    is_rails = ctx.has("config/application.rb", "bin/rails")
    install = ctx.install_command or "bundle install --jobs 4 --retry 3 --without development test"
    start = ctx.start_command or (
        "bundle exec rails server -b 0.0.0.0 -p ${PORT:-3000}"
        if is_rails
        else "bundle exec ruby app.rb -o 0.0.0.0 -p ${PORT:-3000}"
    )
    lines = [
        "# Generated by Syte build packs — Ruby" + (" / Rails" if is_rails else ""),
        f"FROM ruby:{DEFAULT_RUBY_VERSION}-slim AS runtime",
        "ENV RAILS_ENV=production",
        "ENV RACK_ENV=production",
        # Rails 7 defaults to writing logs to a file; this makes it log to
        # stdout so `docker logs` and the dashboard log viewer see anything.
        "ENV RAILS_LOG_TO_STDOUT=1",
        "ENV RAILS_SERVE_STATIC_FILES=1",
        "ENV BUNDLE_WITHOUT=development:test",
        *_env_lines(port),
        (
            "RUN apt-get update && apt-get install -y --no-install-recommends "
            "build-essential git curl libpq-dev libyaml-dev pkg-config nodejs "
            "&& rm -rf /var/lib/apt/lists/*"
        ),
        "WORKDIR /app",
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {install}",
    ]
    if is_rails:
        lines.append(
            "RUN SECRET_KEY_BASE=dummy bundle exec rails assets:precompile || true"
        )
    lines += [
        "RUN useradd --create-home app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    notes = ["Detected Ruby."]
    if is_rails:
        notes.append("Rails detected — assets precompiled and logging to stdout.")
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="ruby",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        start_command=start,
        notes=tuple(notes),
    )


def generate_java_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["java"]
    is_maven = ctx.has("pom.xml")
    if is_maven:
        build = ctx.build_command or "mvn -B -DskipTests package"
        build_image = f"maven:3.9-eclipse-temurin-{DEFAULT_JAVA_VERSION}"
        artifact_glob = "/src/target/*.jar"
    else:
        build = ctx.build_command or "./gradlew --no-daemon build -x test"
        build_image = f"gradle:8-jdk{DEFAULT_JAVA_VERSION}"
        artifact_glob = "/src/build/libs/*.jar"
    start = ctx.start_command or "java -jar /app/app.jar"
    lines = [
        "# Generated by Syte build packs — Java " + ("(Maven)" if is_maven else "(Gradle)"),
        f"FROM {build_image} AS build",
        "WORKDIR /src",
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {build}",
        # Gradle emits a -plain.jar alongside the real boot jar; picking the
        # largest file reliably selects the executable one.
        f"RUN mkdir -p /out && cp $(ls -S {artifact_glob} | head -n1) /out/app.jar",
        "",
        f"FROM eclipse-temurin:{DEFAULT_JAVA_VERSION}-jre-alpine AS runtime",
        "RUN apk add --no-cache curl",
        *_env_lines(port),
        # Without this the JVM sizes its heap from the host's RAM and gets
        # OOM-killed as soon as a memory limit is applied to the container.
        "ENV JAVA_OPTS=\"-XX:MaxRAMPercentage=75\"",
        "WORKDIR /app",
        "COPY --from=build /out/app.jar /app/app.jar",
        "RUN adduser -D -H app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="java",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        build_command=build,
        start_command=start,
        notes=(f"Detected Java ({'Maven' if is_maven else 'Gradle'}).",),
    )


def generate_elixir_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["elixir"]
    is_phoenix = ctx.has("assets/package.json") or ctx.has("config/runtime.exs")
    install = ctx.install_command or "mix deps.get --only prod"
    build = ctx.build_command or "mix release"
    start = ctx.start_command or "/app/bin/server"
    lines = [
        "# Generated by Syte build packs — Elixir" + (" / Phoenix" if is_phoenix else ""),
        f"FROM elixir:{DEFAULT_ELIXIR_VERSION}-alpine AS build",
        "RUN apk add --no-cache build-base git nodejs npm",
        "ENV MIX_ENV=prod",
        "WORKDIR /src",
        "RUN mix local.hex --force && mix local.rebar --force",
        *_build_arg_lines(ctx),
        "COPY . .",
        f"RUN {install}",
        "RUN mix compile",
    ]
    if is_phoenix:
        lines += [
            "RUN mix assets.deploy || true",
        ]
    lines += [
        f"RUN {build}",
        "",
        "FROM alpine:3.20 AS runtime",
        "RUN apk add --no-cache libstdc++ ncurses-libs openssl curl",
        "ENV MIX_ENV=prod",
        *_env_lines(port),
        "WORKDIR /app",
        "COPY --from=build /src/_build/prod/rel/ /app/",
        # A release lands under _build/prod/rel/<name>/; flatten whichever name
        # the project used so the CMD path is predictable.
        "RUN set -e; dir=$(ls -d /app/*/ | head -n1); cp -r \"$dir\". /app/ && rm -rf \"$dir\"",
        "RUN adduser -D -H app && chown -R app:app /app",
        "USER app",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="elixir",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        build_command=build,
        start_command=start,
        notes=("Detected Elixir release build.",),
    )


def generate_dotnet_dockerfile(ctx: BuildContext) -> BuildPlan:
    port = ctx.port or DEFAULT_PORTS["dotnet"]
    build = ctx.build_command or "dotnet publish -c Release -o /out"
    start = ctx.start_command or "dotnet /app/app.dll"
    lines = [
        "# Generated by Syte build packs — .NET",
        f"FROM mcr.microsoft.com/dotnet/sdk:{DEFAULT_DOTNET_VERSION} AS build",
        "WORKDIR /src",
        *_build_arg_lines(ctx),
        "COPY . .",
        "RUN dotnet restore",
        f"RUN {build}",
        "",
        f"FROM mcr.microsoft.com/dotnet/aspnet:{DEFAULT_DOTNET_VERSION} AS runtime",
        *_env_lines(port),
        # ASP.NET listens on 8080/localhost by default in container images;
        # ASPNETCORE_URLS is the only way to make it bind all interfaces.
        f"ENV ASPNETCORE_URLS=http://0.0.0.0:{port}",
        "WORKDIR /app",
        "COPY --from=build /out ./",
        "RUN set -e; dll=$(ls *.dll | head -n1); ln -sf \"$dll\" app.dll",
        f"EXPOSE {port}",
        _cmd_line(start),
    ]
    return BuildPlan(
        build_pack=BuildPack.NIXPACKS,
        language="dotnet",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        build_command=build,
        start_command=start,
        notes=("Detected .NET — publishing a Release build onto the ASP.NET runtime image.",),
    )


def generate_static_dockerfile(ctx: BuildContext) -> BuildPlan:
    """Static build pack: optionally build, then serve behind nginx.

    Two shapes: a plain folder of HTML (no build stage at all) and a JS project
    that compiles to a ``dist``/``build`` directory. Detecting which avoids
    dragging a whole Node toolchain into an image that only serves HTML.
    """
    image = ctx.static_image or DEFAULT_STATIC_IMAGE
    port = 80
    has_node_build = ctx.has("package.json")
    framework, _ = detect_node_framework(ctx) if has_node_build else ("static", 80)
    publish = (ctx.publish_directory or _STATIC_PUBLISH_DIRS.get(framework, "dist")).strip("/") or "dist"
    notes: list[str] = []

    if not has_node_build:
        # Straight file copy. `.` is safe as the source because the build
        # context is already scoped to base_directory.
        source = ctx.publish_directory.strip("/") or "."
        lines = [
            "# Generated by Syte build packs — Static (no build step)",
            f"FROM {image} AS runtime",
            (
                "RUN command -v curl >/dev/null 2>&1 || "
                "(apk add --no-cache curl 2>/dev/null || true)"
            ),
            *_nginx_spa_conf_lines(spa_fallback=False),
            f"COPY {source} /usr/share/nginx/html",
            "EXPOSE 80",
            'CMD ["nginx", "-g", "daemon off;"]',
        ]
        notes.append(f"Serving static files from '{source}' with {image}.")
        return BuildPlan(
            build_pack=BuildPack.STATIC,
            language="static",
            dockerfile="\n".join(lines) + "\n",
            exposed_port=port,
            start_command="nginx -g 'daemon off;'",
            notes=tuple(notes),
        )

    manager = detect_node_package_manager(ctx)
    install = ctx.install_command or node_install_command(manager, ctx)
    build = ctx.build_command or node_build_command(manager, ctx) or f"{manager} run build"
    node_version = ctx.node_version or DEFAULT_NODE_VERSION
    lines = [
        f"# Generated by Syte build packs — Static / {framework} / {manager}",
        f"FROM node:{node_version}-alpine AS build",
        "WORKDIR /app",
        "RUN apk add --no-cache libc6-compat python3 make g++",
    ]
    if manager == "pnpm":
        lines.append("RUN corepack enable && corepack prepare pnpm@latest --activate")
    elif manager == "yarn":
        lines.append("RUN corepack enable")
    elif manager == "bun":
        lines.append("RUN npm install -g bun")
    lines += _build_arg_lines(ctx)
    lines += [
        "COPY . .",
        f"RUN {install}",
        f"RUN {build}",
        "",
        f"FROM {image} AS runtime",
        (
            "RUN command -v curl >/dev/null 2>&1 || "
            "(apk add --no-cache curl 2>/dev/null || true)"
        ),
        *_nginx_spa_conf_lines(spa_fallback=True),
        f"COPY --from=build /app/{publish} /usr/share/nginx/html",
        "EXPOSE 80",
        'CMD ["nginx", "-g", "daemon off;"]',
    ]
    notes.append(f"Building with {manager} and serving '{publish}' from {image}.")
    notes.append("SPA fallback enabled: unknown paths resolve to /index.html.")
    return BuildPlan(
        build_pack=BuildPack.STATIC,
        language="static",
        dockerfile="\n".join(lines) + "\n",
        exposed_port=port,
        install_command=install,
        build_command=build,
        start_command="nginx -g 'daemon off;'",
        notes=tuple(notes),
    )


# Dispatch table for generated build packs.
_GENERATORS = {
    "node": generate_node_dockerfile,
    "bun": generate_bun_dockerfile,
    "deno": generate_deno_dockerfile,
    "python": generate_python_dockerfile,
    "go": generate_go_dockerfile,
    "rust": generate_rust_dockerfile,
    "php": generate_php_dockerfile,
    "ruby": generate_ruby_dockerfile,
    "java": generate_java_dockerfile,
    "elixir": generate_elixir_dockerfile,
    "dotnet": generate_dotnet_dockerfile,
    "static": generate_static_dockerfile,
}

SUPPORTED_LANGUAGES = tuple(sorted(_GENERATORS))


# --------------------------------------------------------------------------- #
# Top-level resolution
# --------------------------------------------------------------------------- #


def resolve_build_plan(build_pack: BuildPack, ctx: BuildContext) -> BuildPlan:
    """Produce the :class:`BuildPlan` for a build pack + source tree.

    ``nixpacks``/``railpack`` detect and generate. ``static`` forces the nginx
    path. ``dockerfile``, ``dockercompose`` and ``dockerimage`` do not generate
    anything — they describe where to find an existing recipe — but still return
    a plan so callers have one uniform shape to work with.
    """
    if build_pack is BuildPack.DOCKERFILE:
        location = (ctx.package_json.get("dockerfile_location") or "/Dockerfile")
        location = str(location).lstrip("/") or "Dockerfile"
        return BuildPlan(
            build_pack=BuildPack.DOCKERFILE,
            language="dockerfile",
            dockerfile="",
            dockerfile_path=location,
            exposed_port=ctx.port or 3000,
            start_command=ctx.start_command,
            notes=(f"Using the repository Dockerfile at /{location}.",),
            generated=False,
        )

    if build_pack is BuildPack.DOCKERCOMPOSE:
        return BuildPlan(
            build_pack=BuildPack.DOCKERCOMPOSE,
            language="compose",
            dockerfile="",
            dockerfile_path="",
            exposed_port=ctx.port or 3000,
            notes=(
                (
                    "Compose deployment: images are built or pulled per service by "
                    "docker compose. Rolling updates are not available for compose stacks."
                ),
            ),
            generated=False,
        )

    if build_pack is BuildPack.DOCKERIMAGE:
        return BuildPlan(
            build_pack=BuildPack.DOCKERIMAGE,
            language="image",
            dockerfile="",
            dockerfile_path="",
            exposed_port=ctx.port or 3000,
            start_command=ctx.start_command,
            notes=("Deploying a prebuilt image — no build step runs.",),
            generated=False,
        )

    if build_pack is BuildPack.STATIC:
        return generate_static_dockerfile(ctx)

    # Nixpacks / Railpack: detect, then route SPA frameworks to static.
    language = detect_language(ctx)
    if language in ("node", "static") and is_static_output(ctx):
        plan = generate_static_dockerfile(ctx)
        return BuildPlan(
            build_pack=plan.build_pack,
            language=plan.language,
            dockerfile=plan.dockerfile,
            context_directory=plan.context_directory,
            dockerfile_path=plan.dockerfile_path,
            exposed_port=plan.exposed_port,
            install_command=plan.install_command,
            build_command=plan.build_command,
            start_command=plan.start_command,
            notes=plan.notes
            + (
                (
                    "Detected a static-output frontend and switched to the static "
                    "build pack automatically."
                ),
            ),
        )

    generator = _GENERATORS.get(language)
    if generator is None:
        raise BuildPackError(
            f"No build pack generator for detected language '{language}'. "
            f"Supported: {', '.join(SUPPORTED_LANGUAGES)}. "
            "Add a Dockerfile and switch the build pack to 'dockerfile'."
        )
    plan = generator(ctx)
    if build_pack is BuildPack.RAILPACK:
        # Railpack is Nixpacks' successor; Syte's generator is shared, so record
        # the pack the operator selected rather than silently rewriting it.
        return BuildPlan(
            build_pack=BuildPack.RAILPACK,
            language=plan.language,
            dockerfile=plan.dockerfile,
            context_directory=plan.context_directory,
            dockerfile_path=plan.dockerfile_path,
            exposed_port=plan.exposed_port,
            install_command=plan.install_command,
            build_command=plan.build_command,
            start_command=plan.start_command,
            notes=plan.notes,
        )
    return plan


def build_context_from_application(
    app: dict[str, object],
    *,
    files: frozenset[str] | None = None,
    package_json: dict[str, object] | None = None,
    build_args: tuple[tuple[str, str], ...] = (),
) -> BuildContext:
    """Assemble a :class:`BuildContext` from a ``platform_applications`` row.

    Explicit operator overrides (install/build/start command, publish directory,
    exposed port) always take precedence over detection — that is the whole
    point of exposing them in the UI.
    """
    ports_exposes = str(app.get("ports_exposes") or "").strip()
    port = 0
    if ports_exposes:
        first = ports_exposes.split(",")[0].strip()
        try:
            port = int(first)
        except ValueError:
            port = 0

    extra: dict[str, object] = {}
    if app.get("dockerfile_location"):
        extra["dockerfile_location"] = app["dockerfile_location"]

    return BuildContext(
        files=files or frozenset(),
        package_json={**(package_json or {}), **extra},
        base_directory=str(app.get("base_directory") or "/"),
        publish_directory=str(app.get("publish_directory") or ""),
        install_command=str(app.get("install_command") or ""),
        build_command=str(app.get("build_command") or ""),
        start_command=str(app.get("start_command") or ""),
        static_image=str(app.get("static_image") or DEFAULT_STATIC_IMAGE),
        build_args=build_args,
        port=port,
    )


# --------------------------------------------------------------------------- #
# Filesystem scan — the only impure function in this module
# --------------------------------------------------------------------------- #

# Directories never worth walking. node_modules alone can hold hundreds of
# thousands of entries and would dominate the scan time.
_SKIP_DIRS = frozenset({
    ".git", "node_modules", ".next", ".nuxt", "dist", "build", "target",
    "vendor", "__pycache__", ".venv", "venv", ".gradle", ".mvn", "_build",
    "deps", ".terraform", ".cache", ".pytest_cache", "coverage", ".svelte-kit",
})

# Deep trees add scan cost without improving detection — every manifest that
# matters lives near the root.
_SCAN_MAX_DEPTH = 4
_SCAN_MAX_FILES = 20_000


def scan_context(
    root: Path,
    *,
    base_directory: str = "/",
    max_depth: int = _SCAN_MAX_DEPTH,
) -> tuple[frozenset[str], dict[str, object]]:
    """Walk a source tree and return ``(relative file paths, package.json)``.

    Bounded in both depth and file count so a pathological repository cannot
    stall a deployment. Returns paths with forward slashes regardless of
    platform so the pure detection functions can compare them literally.
    """
    base = (base_directory or "/").strip("/")
    scan_root = (root / base) if base else root
    if not scan_root.is_dir():
        return frozenset(), {}

    names: set[str] = set()
    stack: list[tuple[Path, int]] = [(scan_root, 0)]
    while stack and len(names) < _SCAN_MAX_FILES:
        current, depth = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            if entry.name in _SKIP_DIRS:
                continue
            try:
                rel = entry.relative_to(scan_root).as_posix()
            except ValueError:
                continue
            if entry.is_dir():
                names.add(rel)
                if depth + 1 <= max_depth:
                    stack.append((entry, depth + 1))
            else:
                names.add(rel)
            if len(names) >= _SCAN_MAX_FILES:
                break

    package_json: dict[str, object] = {}
    package_path = scan_root / "package.json"
    if package_path.is_file():
        try:
            loaded = json.loads(package_path.read_text(encoding="utf-8", errors="replace"))
            if isinstance(loaded, dict):
                package_json = loaded
        except (OSError, json.JSONDecodeError):
            # A malformed package.json should degrade to "Node detected, no
            # framework" rather than failing the whole deployment.
            package_json = {}

    cargo_path = scan_root / "Cargo.toml"
    if cargo_path.is_file():
        match = re.search(
            r'^\s*name\s*=\s*"([^"]+)"', cargo_path.read_text(errors="replace"), re.MULTILINE
        )
        if match:
            package_json["cargo_name"] = match.group(1)

    return frozenset(names), package_json


def detect_dockerfile(root: Path, *, base_directory: str = "/", location: str = "") -> Path | None:
    """Locate the Dockerfile for the ``dockerfile`` build pack.

    An explicit ``location`` is honoured first (that is the operator's stated
    intent); only then do we fall back to the conventional names.
    """
    base = (base_directory or "/").strip("/")
    scan_root = (root / base) if base else root
    if location:
        candidate = scan_root / location.lstrip("/")
        if candidate.is_file():
            return candidate
    for name in ("Dockerfile", "dockerfile", "Dockerfile.prod", "Dockerfile.production"):
        candidate = scan_root / name
        if candidate.is_file():
            return candidate
    return None


def detect_compose_file(root: Path, *, base_directory: str = "/", location: str = "") -> Path | None:
    """Locate the compose file for the ``dockercompose`` build pack."""
    base = (base_directory or "/").strip("/")
    scan_root = (root / base) if base else root
    if location:
        candidate = scan_root / location.lstrip("/")
        if candidate.is_file():
            return candidate
    for name in (
        "docker-compose.yaml",
        "docker-compose.yml",
        "compose.yaml",
        "compose.yml",
        "docker-compose.prod.yaml",
        "docker-compose.prod.yml",
    ):
        candidate = scan_root / name
        if candidate.is_file():
            return candidate
    return None


__all__ = [
    "DEFAULT_NODE_VERSION",
    "DEFAULT_PORTS",
    "DEFAULT_PYTHON_VERSION",
    "DEFAULT_STATIC_IMAGE",
    "SUPPORTED_LANGUAGES",
    "BuildPackError",
    "build_context_from_application",
    "detect_compose_file",
    "detect_dockerfile",
    "detect_language",
    "detect_node_framework",
    "detect_node_package_manager",
    "detect_python_manager",
    "generate_bun_dockerfile",
    "generate_deno_dockerfile",
    "generate_dotnet_dockerfile",
    "generate_elixir_dockerfile",
    "generate_go_dockerfile",
    "generate_java_dockerfile",
    "generate_node_dockerfile",
    "generate_php_dockerfile",
    "generate_python_dockerfile",
    "generate_ruby_dockerfile",
    "generate_rust_dockerfile",
    "generate_static_dockerfile",
    "is_static_output",
    "node_build_command",
    "node_install_command",
    "node_scripts",
    "node_start_command",
    "python_install_command",
    "python_start_command",
    "resolve_build_plan",
    "scan_context",
]
