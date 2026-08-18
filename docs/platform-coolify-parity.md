# Coolify parity: the `syte.platform` layer

This document describes the PaaS layer added under `syte/platform/`, why it
exists, what has landed, and what is still outstanding.

## Why

Syte already deploys apps from git, proxies them through Caddy with automatic
TLS, and manages them from a web GUI — it is a "mini-Coolify" already. But
compared to [Coolify](https://github.com/coollabsio/coolify) it was missing most
of the platform model. The gaps that mattered:

| Gap | Before | Now |
|---|---|---|
| **Dockerfile required** | `deployment._resolve_deploy` refuses to deploy without a `Dockerfile` and tells the operator to add one | Build packs detect 12 languages and synthesise the Dockerfile |
| **No database provisioning** | Nothing creates Postgres/Redis/etc. | Catalog for all 8 Coolify engines with credentials, readiness probes and logical backups |
| **No resource hierarchy** | One flat `projects` table = one app | team → server → project → environment → resource (application / database / service) |
| **Single host only** | Every code path assumes localhost | `ServerTarget` models local *and* SSH-reachable servers |
| **No deployment history** | Status column only | Deployment queue with per-server concurrency, logs, and rollback targets |
| **No env var scoping** | One JSON blob per project | Per-resource vars plus team/project/environment shared variables, build-time vs runtime, secret masking |
| **Outbound webhooks only** | `webhooks.py` emits, never receives | Inbound push/PR model (schema + matching landed) |

## Naming: why `platform_*` tables

Coolify's vocabulary collides with Syte's. In Syte a **project** is one deployed
app; in Coolify a **project** is a grouping that contains environments, which
contain resources. Rather than redefine the existing `projects` table (and break
the agent, preview and workspace code that depends on it), the new model lives in
`platform_*` tables. The API speaks Coolify's vocabulary, the schema stays
unambiguous, and nothing existing changes behaviour.

`syte/platform/` is a package, not a single module, mirroring the existing
`syte/sycord/` precedent.

## Layering

The package follows the pure/effectful split already used by
`caddy_routes.py` (pure renderers) vs `certificates.py` (effectful appliers).
That is what makes the interesting logic testable without Docker or a network.

```
types.py             value objects + enums, zero I/O
store.py             SQLite persistence (platform_* tables)
build_packs.py       source tree -> Dockerfile          (pure + one scan fn)
database_catalog.py  managed engine definitions         (pure)
git_sources.py       repo identity + webhook auth       (pure)
```

## What landed

### `types.py` — the vocabulary

String enums throughout, so values round-trip unchanged through SQLite `TEXT`
columns and JSON payloads, and the wire format matches Coolify's own. An
operator migrating across sees `build_pack: "nixpacks"` and
`status: "cancelled-by-user"` exactly as expected.

- **Enums**: `BuildPack` (incl. `railpack`, `dockerimage`), `ResourceType`,
  `DatabaseType` (8), `DeploymentStatus`, `ContainerStatus`, `ResourceStatus`,
  `ServerStatus`, `ProxyType`, `RedirectType`, `VariableScope`, `GitProvider`,
  `GitAuthMethod`, `NotificationChannel`, `NotificationEvent`,
  `ExecutionStatus`, `VolumeKind`, `DestinationKind`, `DeploymentTrigger`.
- **Config objects**: `HealthCheckConfig` (renders both `docker run`
  `--health-*` flags and a compose `healthcheck:` block), `ResourceLimits`,
  `DomainRoute`, `PortMapping`, `VolumeMount`, `BasicAuthConfig`.
- **Deployment types**: `BuildContext`, `BuildPlan`, `DeploymentRequest`,
  `DeploymentStep`, `DeploymentPlan`, `EnvVar`, `ServerTarget`.
- **Labels**: every managed container is stamped with `syte.managed`,
  `syte.resource.uuid`, `syte.config.hash` and friends. These are the only
  reliable way to reconcile desired vs actual state after a restart, and to
  attribute a stray container during cleanup.

Design details worth calling out:

- `new_uuid()` always starts with a letter, because these identifiers end up
  inside DNS labels (generated preview subdomains) where a leading digit is
  invalid.
- `new_password()` excludes every URI-reserved character, so a generated
  credential never needs percent-encoding inside
  `postgres://user:pass@host/db`.
- `BuildContext.port` defaults to `0` meaning *unset*. A real default here would
  silently override every build pack's language-specific default.

### `store.py` — persistence

27 tables covering teams and members, private keys, servers, destinations
(docker networks), projects, environments, applications, databases, services and
their containers, env vars, shared env vars, deployments, PR previews, volumes,
scheduled tasks and executions, S3 storages, backups and executions,
notification channels, git sources, tags, SSL certificates, server metrics and
inbound webhook events.

Conventions match `syte/database.py` — `SCHEMA` string applied with
`executescript`, additive `ALTER TABLE` migrations guarded by
`PRAGMA table_info`, one short-lived connection per accessor, `Row` factory,
plain `dict` returns.

Two deliberate improvements over the existing store:

1. **Update whitelists are derived from the live schema**, not duplicated by
   hand. `syte/database.py` maintains an `allowed` set manually, so adding a
   column means touching three places and silently no-ops if you miss one.
   Reading `PRAGMA table_info` removes that failure mode while keeping the same
   "only real columns may be written" guarantee.
2. **Secrets are stripped on read by default.** `SECRET_COLUMNS` are replaced
   with a `{column}_set` boolean unless the caller passes
   `include_secrets=True`. The UI learns that a password exists without the
   value ever reaching a browser.

Safety properties, all covered by tests:

- Table names are interpolated into SQL, so they are checked against a
  whitelist derived from `SCHEMA` (`_assert_known_table`).
- `ORDER BY` is validated against the real column set and falls back to a safe
  default, so an injected clause cannot reach SQLite.
- `delete_where` refuses to run without at least one condition.
- `commit`, `trigger`, `user` and `version` are quoted in the DDL — all are
  SQLite keywords and the schema fails to create without quoting.

Higher-level helpers: `ensure_bootstrap()` (idempotently creates a default team,
a `localhost` server, a `syte` network destination, a default project and a
`production` environment — so a resource can be created with no setup wizard),
`get_resource()` (finds a resource by uuid without knowing its type),
`list_projects_with_environments()` (one shaped payload for the dashboard grid,
avoiding an N+1 from the browser), the cascade deletes, and the deployment queue
accessors (`active_deployments` FIFO, `running_deployment_count` for the
concurrency gate, `rollback_candidates` which skips untagged deployments so the
UI never offers a button that would fail).

### `build_packs.py` — no Dockerfile needed

Detects and generates for **node, bun, deno, python, go, rust, php, ruby, java,
elixir, dotnet, static**, plus framework-specific handling for Next.js, Nuxt,
Remix, SvelteKit, Astro, NestJS, Gatsby, Angular, Vite, CRA, Vue, Express,
Fastify, Koa, Hono, Django, Laravel, Rails and Phoenix.

```python
files, package_json = scan_context(repo_root, base_directory="/")
plan = resolve_build_plan(BuildPack.NIXPACKS, BuildContext(files=files, package_json=package_json))
plan.dockerfile      # generated Dockerfile text
plan.exposed_port    # 3000 for Next, 8000 for Django, 8080 for Go, 80 for static…
plan.start_command
plan.notes           # actionable hints surfaced in the deployment log
```

Behaviours that matter in practice:

- **SPA frameworks auto-route to the static pack.** A Vite/CRA/Angular/Gatsby
  project ends up behind nginx with SPA fallback (`try_files … /index.html`)
  rather than running a dev server in production. Coolify's docs tell you to
  switch build packs by hand; this detects it.
- **Every generated runtime binds `0.0.0.0` and honours `$PORT`.** Binding
  localhost inside a container is the single most common cause of the "Bad
  Gateway" reports in Coolify's own troubleshooting docs.
- **Frozen-lockfile installs only when the lockfile exists.** `npm ci`
  hard-fails without `package-lock.json`, and that failure reads like a broken
  build pack rather than a missing lockfile.
- **Corepack's `packageManager` field beats lockfile sniffing** — a stale
  `package-lock.json` alongside `packageManager: "pnpm@9"` is common after a
  migration.
- **Containers run unprivileged** wherever the base image allows.
- **Build args are emitted as both `ARG` and `ENV`**, because Vite and Next only
  read `process.env`.
- Generated Dockerfiles copy the whole app directory into the runtime stage
  instead of computing a minimal per-framework artifact set. A slightly larger
  image is a much better trade than a deploy that breaks because a framework
  moved its output directory. Go and Rust do use a slim static-binary runtime,
  where that path is reliable.

The nginx config is written with `printf` rather than a Dockerfile heredoc, so it
works without BuildKit's `dockerfile:1.4+` frontend, which is not guaranteed on a
self-managed host. Because the config lines are wrapped in shell single quotes, a
nested single quote would truncate the string and emit a broken config — so
`_nginx_spa_conf_lines` raises if any line contains one.

`scan_context` is the only impure function, bounded in depth (4) and file count
(20 000) with a skip list, so a pathological repository cannot stall a
deployment.

### `database_catalog.py` — managed databases

All eight Coolify engines: PostgreSQL, MySQL, MariaDB, MongoDB, Redis, KeyDB,
Dragonfly, ClickHouse.

```python
row = provision_defaults(DatabaseType.POSTGRESQL, "My App DB")
container_env(row)          # POSTGRES_USER/PASSWORD/DB + PGDATA
container_command(row)      # [] here; ['redis-server', '--requirepass', …] for Redis
container_extra_args(row)   # ['--shm-size', '256m']
readiness_command(row)      # pg_isready -U postgres -d my_app_db …
connection_url(row, host="syte-db-abc")
dump_command(row, output_path="/backup/db.sql")
```

The operational details this encodes are the difference between a container that
starts and one that works:

- **PostgreSQL `PGDATA` points at a subdirectory** of the mount. A fresh Docker
  volume contains `lost+found` and `initdb` refuses a non-empty directory.
- **Key-value engines take their password on the server command line**, not via
  an env var — so they need a `command` override, not just `-e`.
- **Dragonfly needs `memlock=-1`** or it exits immediately; **ClickHouse needs a
  raised `nofile` limit**.
- **MariaDB sets both `MARIADB_*` and `MYSQL_*`** so older tags keep working
  from the same row, and uses the image's shipped `healthcheck.sh` which waits
  for InnoDB recovery rather than just answering a ping.
- **MongoDB URLs include `authSource=admin`**, because the root user created by
  `MONGO_INITDB_ROOT_USERNAME` lives in `admin` — omitting it is the classic
  auth failure.
- **Readiness probes authenticate.** A Postgres still running `initdb` accepts a
  TCP connection and then rejects the query, so a port check is not enough.
- **Postgres dumps use `--clean --if-exists`** so they restore idempotently over
  an existing database; **MySQL dumps use `--single-transaction`** for a
  consistent snapshot without locking a live database.
- Engines without a logical dump tool (Redis, KeyDB, Dragonfly, ClickHouse)
  **raise** rather than silently writing an empty backup file.

### Managed database runtime and API — **done**

The first production runtime slice is now wired into the application. `syte/platform/database_runtime.py` creates or reuses a private Docker network, provisions named volumes, starts and stops database containers, reports status, and deletes containers without deleting data unless explicitly requested. Public ports are not published by default. `syte/platform_api.py` exposes catalog, list, create, start, stop, status, connection-details, and delete endpoints under `/api/platform/databases`. The main lifespan initializes the platform schema and default team/server/project/environment bootstrap records.

Touched files: `syte/platform/database_runtime.py`, `syte/platform_api.py`, `syte/main.py`, and `tests/test_database_runtime.py`.

### Backup scheduling and restore — **partial**

The platform schema and pure dump/restore command builders are present, but S3-compatible upload, retention execution, and restore orchestration remain to be connected to a host scheduler. No backup UI is exposed until that execution path is implemented safely.

Touched files currently providing the foundation: `syte/platform/store.py` and `syte/platform/database_catalog.py`.

### Generic Git deploy, templates, previews, disk hygiene, multi-server, teams, notifications — **partial**

The existing Syte deployment, preview, GitHub, resource-monitor, and platform model implementations provide foundations, but these capabilities still need effectful orchestration and UI wiring. They remain explicitly tracked here rather than being presented as complete.

### `git_sources.py` — repository identity and webhook authenticity

Two pure concerns that the inbound-webhook receiver will build on.

**Repository identity.** An operator pastes a repo URL in whatever form they
have; the provider's push payload names it in its own canonical form.
`normalize_repo()` reduces any reference — HTTPS, SSH, `git@` scp-like, with or
without `.git`, with or without embedded credentials — to `host/owner/name`, and
`repo_matches()` compares two references:

```python
normalize_repo("git@github.com:Acme/Web.git")   # 'github.com/acme/web'
repo_matches("https://github.com/acme/web", "acme/web")  # True
```

- Embedded credentials never survive normalisation.
- When **both** references are host-qualified the hosts must agree, so a push to
  `gitlab.com/acme/web` cannot deploy an application tracking
  `github.com/acme/web`.
- A reference must resolve to at least `owner/name`; without that guard an
  unparseable string would be returned verbatim and could "match" itself.
- `branch_matches()` tolerates `refs/heads/main` vs `main` — a plain equality
  check would never fire against a real push payload.
- `watch_paths_match()` implements monorepo path filters and **fails open**: no
  patterns, or a push with no reported file list, deploys. Suppressing a real
  deployment is worse than running a redundant one.

**Webhook authenticity.** Centralised so the HTTP layer cannot skip it, with
every comparison through `hmac.compare_digest`:

| Provider | Mechanism |
|---|---|
| GitHub | `X-Hub-Signature-256` HMAC-SHA256, `sha256=` prefixed |
| GitLab | `X-Gitlab-Token` verbatim shared secret |
| Gitea / Forgejo | `X-Gitea-Signature` bare HMAC-SHA256 hex |
| Bitbucket | unsigned — the secret travels in the URL, compared by the HTTP layer |
| other | falls back to a GitHub-style signature, keeping Gogs/Forgejo working |

Header lookup is case-insensitive because ASGI servers and proxies disagree
about casing. A missing secret returns an actionable message rather than a bare
`False`.

## Verification

Beyond the unit tests, the generated output was checked against real container
images:

**Static build pack** — image builds; `/` serves the page; `/health` returns
`200 ok`; `/missing` returns `404` (no SPA fallback for a plain static folder);
the generated nginx config is syntactically valid inside the image.

**Node build pack** — image builds; serves JSON; runs as `uid=1000(node)`;
`PORT=3000 HOSTNAME=0.0.0.0 NODE_ENV=production` are injected; the healthcheck
is accepted by the engine.

**PostgreSQL** — readiness probe passes; `PGDATA` subdirectory is created (the
`lost+found` workaround holds); the database and user from `container_env` exist;
write-then-read works; `dump_command` produces a valid 954-byte SQL dump
containing the expected `CREATE TABLE`.

**Redis** — readiness probe passes; `requirepass` is enforced (an
unauthenticated `PING` returns `NOAUTH Authentication required`); authenticated
`SET`/`GET` works; `appendonly` is enabled by the generated command.

### Test suite

```
python3 -m pytest tests/test_platform_*.py -q
# 299 passed, 1 skipped
```

299 new tests. The full suite is `655 passed, 5 failed`; those 5 failures
(`test_agent_chat_qol::test_cold_events_do_not_prune_every_write`,
`test_provider_quota::test_failure_metadata_surfaces_rate_limited_fields`,
`test_settings::test_agent_settings_use_cloud_namespace`, and two in
`test_thinking_levels`) are **pre-existing** — reproduced on a clean tree with
these changes stashed.

Three real bugs were caught by the tests while writing them, all fixed:

1. `delete_resource_cascade` deleted scheduled tasks *before* collecting their
   executions, orphaning every `platform_task_executions` row.
2. `BuildContext.port` defaulted to `3000`, which silently overrode the
   language-specific default port in every generator.
3. `normalize_repo` returned an unparseable slashless string verbatim, so a junk
   reference could match itself.

## Not yet landed

This is the foundation. It is self-contained and tested, but it is **not wired
into the running application yet** — there is no new HTTP route and no UI change,
so merging it cannot affect existing behaviour.

Remaining work, in dependency order:

1. `docker_engine.py` + `health.py` — `docker run`/`compose` argv builders and
   the effectful runners (build, run, stop, logs, exec, rolling swap, cleanup).
2. `env_vars.py` + `proxy.py` — scoped env resolution; FQDN parsing
   (multi-domain, path routing, port mapping) rendered to Caddy blocks with
   www/non-www redirects and basic auth.
3. `deployments.py` — the queue worker driving `DeploymentPlan` execution, log
   streaming and rollback.
4. `servers.py` — SSH transport, server validation, docker bootstrap, metrics
   collection.
5. `service_catalog.py` + `compose.py` — one-click services with Coolify's magic
   env vars (`SERVICE_FQDN_*`, `SERVICE_PASSWORD_*`, …).
6. `backups.py` + `scheduled_tasks.py` — cron parsing, dump scheduling, S3
   upload, retention.
7. `notifications.py` — Discord/Slack/Telegram/Pushover/email dispatch. The
   webhook *receiver* route, which will use the already-landed
   `git_sources.verify_webhook()` and `store.applications_watching()`.
8. `service.py` — the orchestration pipeline.
9. `api.py` — the Coolify-compatible `/api/v1` router, wired into `main.py`.
10. `static/platform/` — the Coolify-style dashboard.

There are no forward references or dead imports in the landed code: every module
imports only `syte.config`, `syte.sqlite_utils` and other `syte.platform`
modules that exist.
