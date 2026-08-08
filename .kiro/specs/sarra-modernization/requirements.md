# SARRA Performance, Stability & Modernization

## Purpose

This specification turns the SARRA/Syte modernization roadmap into an incremental implementation program. It improves the existing Python async architecture rather than replacing it with Go or Rust. Each phase must be measurable, reversible, and compatible with current GUI, token API, agent-session, SSE, preview, deployment, and Turso contracts.

## Repository baseline

The current system has:

- Per-operation `aiosqlite` connections in `syte/database.py`, with SQLite tuning helpers in `syte/sqlite_utils.py`.
- Workspace enrichment in `syte/workspace_api.py`, including bounded but still expensive per-project work.
- Structured activity persistence and SSE in `syte/agent_activity.py`, plus file-tail SSE in `syte/log_stream.py`.
- PID/process and preview behavior in `syte/process_manager.py` and `syte/preview_manager.py`.
- Deployment orchestration in `syte/deployment.py` and Docker execution in `syte/docker_deploy.py`.
- An embedded agent runtime in `syte/cloud_agent.py`, durable request admission in `syte/agent_jobs.py`, provider definitions in `syte/ai_providers.py`, and routing in `syte/model_routing.py`.
- Layered memory and a bounded workspace index in `syte/agent_memory.py`.
- Supervisory, resource, authentication, rate-limit, and lifecycle helpers in `syte/supervisor.py`, `syte/resource_monitor.py`, `syte/auth.py`, `syte/rate_limit.py`, and `syte/main.py`.

Existing API and behavior contracts are referenced by:

#[[file:docs/cloud-agent-contract.md]]
#[[file:docs/agent-streaming-api.md]]
#[[file:docs/turso-persistence.md]]
#[[file:docs/api-agent.md]]
#[[file:docs/resource-monitor.md]]

## Goals

1. Reduce latency and resource use for database, workspace, API, preview, and log operations.
2. Make long-running work explicit, cancellable, observable, restartable, and resource-bounded.
3. Improve agent context selection, model routing, memory, search, and cost control without changing the user-facing agent contract abruptly.
4. Establish durable deployment, health-check, rollback, and recovery semantics.
5. Add structured instrumentation before major rewrites so optimization follows measured bottlenecks.
6. Preserve security restrictions and strengthen isolation, redaction, quotas, and auditability.

## Non-goals

- Rewriting the backend in another language.
- Replacing SQLite/Turso without measured evidence and a migration plan.
- Removing existing SSE, polling, token API, GUI, or legacy response fields in this effort.
- Introducing a distributed dependency such as Redis or a separate worker fleet before the single-host durable-job model is proven necessary.

## Requirement 1: Database access and query performance

**User story:** As an operator, I want frequent SARRA operations to reuse database resources and expose slow queries so that normal API traffic remains responsive.

### Acceptance criteria

1. A controlled async SQLite connection manager reuses connections for high-frequency local operations and limits concurrent writers/readers; callers do not create an unbounded connection per query.
2. WAL mode, `synchronous=NORMAL`, and `busy_timeout` remain enabled and are verified at manager initialization.
3. Transactions and ownership boundaries are explicit for grouped reads/writes; related operations can execute in one transaction where consistency permits.
4. Versioned, idempotent migrations add indexes for project, agent, session, request, status, and timestamp access patterns identified by query instrumentation.
5. Settings and frequently-read project metadata have bounded in-process caches with explicit TTLs and write invalidation.
6. Query instrumentation records operation name, normalized query or query key, duration, row count where available, outcome, and a slow-query threshold without logging secrets or full payloads.
7. Turso/local persistence behavior remains compatible with current fallback and best-effort mirroring semantics; reconciliation failures are visible rather than silently discarded.

## Requirement 2: Efficient API read models and HTTP caching

**User story:** As a dashboard user, I want project, session, log, and activity pages to load quickly even when the server manages many projects.

### Acceptance criteria

1. `workspace_list()` uses a project-summary read model or batched queries and does not invoke full `workspace_get()` enrichment independently for every project.
2. Preview, agent, SSL, framework, and other expensive derived statuses are cached or fetched through bounded shared refreshes; identical polling requests do not repeat identical work during the cache window.
3. Projects, sessions, logs, and activity feeds support stable cursor or page-based pagination with documented ordering and continuation behavior.
4. Safe, mostly-static GET responses support ETag and/or Last-Modified validation and return `304 Not Modified` when appropriate.
5. Large JSON responses may be compressed when the client accepts compression; streaming responses retain flush and reconnect behavior.
6. GUI and token-facing workspace endpoints use a shared versioned snapshot/read model so fields, latency, and partial failure semantics do not diverge accidentally.
7. Request timing records route, method, status, response size, and duration, with sensitive query/body values excluded.

## Requirement 3: Shared, durable log and activity streaming

**User story:** As a dashboard client, I want several browser connections to observe the same project logs without each connection polling and reopening the source independently.

### Acceptance criteria

1. A project-scoped stream coordinator owns at most one active watcher/tailer per source where possible and broadcasts events to subscribed clients.
2. File watching uses inotify or an equivalent event-driven mechanism when available, with a bounded polling fallback for unsupported filesystems.
3. Docker logs use a long-lived or shared stream rather than repeatedly executing `docker logs` for every client or polling interval.
4. Subscriber buffers are bounded; slow or disconnected subscribers are removed and cannot retain unbounded memory.
5. SSE preserves existing event IDs, `since_id` replay, heartbeat, `retry`, compression, gap signaling, cancellation, and reconnect semantics documented in `docs/agent-streaming-api.md`.
6. Structured agent activity and deployment/preview/tool logs have a documented relationship: source, ordering, cursor, retention, and whether an event is replayable.
7. Abandoned workers and streams are cleaned up automatically after disconnect, idle timeout, or source termination.

## Requirement 4: Unified background jobs and resource scheduling

**User story:** As an operator, I want deployments, previews, builds, indexing, health checks, dependency installs, agent requests, and commands to follow one lifecycle and resource policy.

### Acceptance criteria

1. A typed `Job` model includes an ID, kind, project, priority, status, timestamps, timeout, cancellation state, retry metadata, result/error, and ownership information.
2. The job manager supports `QUEUED`, `RUNNING`/phase-specific states, `SUCCEEDED`, `FAILED`, `CANCELLED`, and `EXPIRED` transitions with durable terminal records.
3. Priority queues support at least critical, high, normal, low, and idle work; capacity limits prevent indexing or background work from starving deployments or user actions.
4. A resource manager tracks and enforces configurable limits for active agents, previews, builds, commands, deployments, CPU, memory, disk, process count, ports, and output bytes.
5. Jobs expose status, progress, logs, cancellation, and failure classification through typed internal APIs and appropriate public endpoints.
6. Job recovery on startup identifies stale leases, running processes, and recoverable states, then resumes or requeues work according to an explicit policy.
7. The first implementation remains safe for a single host; any multi-worker lease semantics must prevent duplicate execution before horizontal scaling is enabled.

## Requirement 5: Process supervision and preview lifecycle

**User story:** As an operator, I want services and previews to survive expected failures without orphaning child processes or blocking API requests during setup.

### Acceptance criteria

1. Process records track PID, process identity/start time, process group, expected state, exit code, restart count, heartbeat, failure reason, and last transition.
2. PID reuse is detected using process start identity or an equivalent verification; an unrelated process is never killed based only on a stale PID.
3. Startup crashes, repeated failures, restart limits, exponential backoff, and circuit/open states are represented explicitly and persisted where needed.
4. Shutdown sends a graceful signal to the process group, waits for a bounded timeout, then terminates and finally force-kills remaining descendants.
5. Preview dependency installation is asynchronous and reports `PREPARING`, `INSTALLING_DEPENDENCIES`, `STARTING`, and `READY`/failure progress without blocking the initial API request.
6. Preview detection recognizes the supported Node, Python, Go, Rust, and static frameworks, selects the correct package manager/lockfile command, and caches valid dependency/framework detection results.
7. Preview readiness can perform semantic HTTP checks, expected status validation, response-time checks, optional custom health endpoints, and clear failure reporting.
8. Existing preview ports, URLs, status fields, and access restrictions remain compatible during migration.

## Requirement 6: Durable deployment lifecycle, health checks, and rollback

**User story:** As an operator, I want deployments to report progress, verify health, and recover or roll back when a new version is unhealthy.

### Acceptance criteria

1. Deployments use a durable state machine: `QUEUED`, `PREPARING`, `BUILDING`, `TESTING`, `DEPLOYING`, `HEALTH_CHECK`, `READY`, `FAILED`, `ROLLING_BACK`, and `ROLLED_BACK`.
2. Each deployment records source commit/ref, build information, environment version, timestamps, logs, health results, process identity, and the prior deployable version.
3. Post-deploy validation checks process existence, listening port, HTTP response, expected status, response time, and optional custom endpoint before `READY`.
4. Health failure produces an exact classified reason and follows configured retry/restart/rollback policy rather than an unbounded retry loop.
5. Supported services can start a new version, health-check it, switch traffic, and stop the old version without an avoidable outage.
6. Deployment history supports selecting a prior healthy version for rollback; rollback is itself observable and cancellable.
7. Existing deployment endpoints continue to return compatible status and stream links while the richer state is introduced.

## Requirement 7: Agent task supervision and cancellation

**User story:** As a user, I want agent turns and subtasks to respect timeouts, cancellation, concurrency, and retry policies while preserving their durable timeline.

### Acceptance criteria

1. Every task records `task_id`, `parent_task_id`, `project_id`, `agent_id`, status, created/started/completed times, timeout, retry count, token usage, cost, error, and cancel reason.
2. Per-task, per-tool, per-agent, and global concurrency limits are enforced before work starts; queued work reports its position or reason for waiting where possible.
3. Cancellation propagates from the user request to child tasks, provider calls, tool jobs, subprocesses, and stream subscriptions.
4. Completed asyncio tasks, stale registry entries, abandoned questions, and expired task records are cleaned up automatically without deleting durable audit history.
5. Retry behavior is selected by typed error category and uses bounded exponential backoff, jitter, maximum attempts, and circuit breakers for provider failures.
6. Request deduplication is available only for explicitly idempotent operations and cannot duplicate file writes, deployments, or other mutable actions.
7. Existing request admission, idempotency, session close, and startup recovery in `syte/agent_jobs.py` remain compatible while active execution becomes more durable and observable.

## Requirement 8: Provider-aware routing, health, and cost control

**User story:** As an operator, I want SARRA to choose an appropriate healthy model and fail over predictably while controlling cost and latency.

### Acceptance criteria

1. Routing exposes stable profiles for FAST, BALANCED, POWER, LOCAL, and FAILOVER, while retaining compatibility with current named profiles.
2. Provider health tracks latency, error rate, rate-limit responses, timeout rate, token usage, estimated cost, availability, cooldown, and circuit state.
3. Routing considers task complexity, requested thinking level, provider capability, current health, configured budgets, and user/project policy.
4. 429, network, timeout, auth, invalid request, and provider outage failures have distinct fallback/retry behavior; auth and invalid configuration failures are not blindly retried.
5. Circuit breakers implement closed, open, and half-open states with bounded recovery probes and do not route requests to an unavailable provider during cooldown.
6. Cost and token usage are recorded per turn, task, subagent, project, user, day, model, and provider when available; configurable budgets can stop or degrade work.
7. Provider clients and background health tasks are closed during graceful shutdown.

## Requirement 9: Context optimization, memory, and workspace intelligence

**User story:** As an agent, I want relevant, fresh workspace context instead of the entire project so that responses are faster, cheaper, and more accurate.

### Acceptance criteria

1. A context budget manager allocates tokens among instructions, user request, relevant files/symbols, recent changes, tool results, history, and response reserve.
2. Conversation memory is separated into short-term turn, session, project, long-term preference, and ephemeral debugging layers with retention and injection rules.
3. Tool results are normalized, deduplicated, compressed, and structure-aware; compiler errors, relevant diff hunks, warning logs, matching JSON fields, and relevant directory entries are preferred over blind truncation.
4. File content caching uses path plus content hash and invalidates on hash/mtime/size changes; secrets are removed before content enters context or logs.
5. The workspace index stores path, hash, language, size, modified time, symbols, imports/exports, dependencies, tags, and optional semantic/vector data behind a pluggable interface.
6. Indexing is incremental: filesystem or action events identify candidates, hash/mtime/size determine change, changed files are parsed, and deleted files are removed without rescanning the entire project.
7. Search supports filename, text, symbol, semantic, and dependency queries and can answer relevance/impact questions using indexed data.
8. Framework/package-manager/project-manifest detection is cached and produces a machine-readable manifest for agent and preview consumers.
9. Recent changes and dependency impact are available to context retrieval and are preferred over stale or low-relevance files.

## Requirement 10: Structured timeline, tools, and replay

**User story:** As a user or operator, I want to replay and debug an agent turn from structured events rather than reconstructing it from string logs.

### Acceptance criteria

1. The durable timeline represents turn start, model request, thinking/status, tool start/result, file change, command, test, deployment/preview events, and final response with IDs, timestamps, parent relationships, and project/task/session identifiers.
2. Tool execution uses a typed `ToolJob` model containing ID, tool, arguments metadata, project, timeout, status, stdout/stderr references, exit code, timestamps, and failure category.
3. Tool arguments, results, logs, and environment values are subject to size limits and secret redaction before persistence or model reuse.
4. Timeline APIs support replay, filtering, searching, pagination, and cost/failure analysis while preserving existing event cursors and lane semantics.
5. Parallel independent read/search/inspection operations can run concurrently, while writes and dependent operations retain deterministic ordering and locks.
6. An automated verification loop can run the relevant format, type, lint, unit, build, and preview-health checks and reports which checks actually passed.
7. Failure recovery can classify a failure, retrieve relevant context, apply a bounded fix/retry loop, and stop with an actionable report when the retry budget is exhausted.

## Requirement 11: Observability and performance feedback

**User story:** As an operator, I want one coherent view of system health and bottlenecks across API, database, agents, tools, previews, Git, and deployments.

### Acceptance criteria

1. Structured logs include timestamp, level, component, event, correlation/task/project/session IDs, duration, status, and safe contextual fields.
2. Metrics cover API, DB, LLM/provider, tools, Git, dependency install, preview startup, builds, deployments, jobs, token/cost usage, memory, CPU, disk, and resource saturation.
3. `/api/metrics` and a dashboard/read model expose safe operational metrics without leaking secrets or arbitrary user content.
4. Health endpoints distinguish liveness, readiness, dependency health, and degraded states; readiness reflects database and required worker availability rather than only process existence.
5. Instrumentation can be enabled with low overhead, uses sampling or aggregation for hot paths, and has retention/rotation controls.
6. Performance changes are accepted only after before/after measurements demonstrate improvement or a documented stability benefit.

## Requirement 12: Security, limits, and data hygiene

**User story:** As an operator, I want performance and automation improvements to preserve the current security boundary and reduce leakage/resource-abuse risk.

### Acceptance criteria

1. Workspace path traversal, symlink escape, archive extraction, working-directory, and process-boundary checks remain enforced and are covered by regression tests.
2. Environment filtering and secret redaction cover API keys, tokens, passwords, authorization headers, `.env` values, private keys, provider errors, logs, tool output, agent context, and deployment output.
3. Commands and tool jobs enforce timeout, output bytes, CPU/memory/process limits where the host supports them, environment allow/deny rules, network policy where possible, and audit records.
4. Authenticated identities have configurable request, stream, job, upload, and body-size limits; rate-limit state is scoped appropriately for the deployment model.
5. API keys remain hashed at rest; project authorization is explicitly evaluated before adding project-scoped capabilities to the existing host-global token model.
6. Workspace, logs, build caches, temporary files, previews, and indexes have quotas, rotation, retention, and cleanup policies.
7. The service runs with the least practical OS privilege and documents any remaining root/systemd requirements.

## Requirement 13: Graceful shutdown and automatic recovery

**User story:** As an operator, I want restarts and host reboots to leave the system consistent and recover recoverable work without orphaning processes.

### Acceptance criteria

1. On shutdown, the service stops accepting new work, drains short operations, cancels long operations, stops previews/deployments/process groups, flushes structured logs/events, closes providers and database connections, and exits within a configured deadline.
2. On startup, persisted jobs, open sessions, stale leases, process groups, previews, ports, and deployment states are reconciled before being resumed or marked failed.
3. Recoverable jobs are requeued with an explicit attempt increment and non-recoverable jobs receive a durable failure reason.
4. Recovery detects PID reuse, missing processes, dead ports, incomplete traffic switches, and partial deployment artifacts.
5. Shutdown and recovery events are visible in the activity/timeline and operational metrics.

## Requirement 14: Compatibility, migration, and verification

**User story:** As a maintainer, I want to ship the modernization in safe slices without breaking existing clients or losing durable history.

### Acceptance criteria

1. Every schema/API/event change includes a compatibility strategy, migration path, rollback path, and data-retention impact.
2. Existing session IDs, activity cursors, SSE control frames, workspace fields, deployment status fields, preview URLs, and legacy `agent_port`-style consumers remain readable until a versioned replacement is adopted.
3. New behavior is introduced behind small internal interfaces and feature flags where failure could affect running projects.
4. Migration and recovery are idempotent and safe to rerun after interruption.
5. Each phase defines measurable latency/resource/reliability targets and a rollback trigger before implementation begins.
6. The implementation must add or update contract, integration, failure-mode, and benchmark tests for changed behavior; no phase is considered complete based only on a successful process start.

## Non-functional targets and measurement plan

Initial targets are relative to a captured baseline, not arbitrary absolute numbers:

- Reduce median and p95 workspace-list latency by eliminating per-project full enrichment.
- Reduce database connection churn and slow-query count without increasing lock contention.
- Keep SSE first-event latency and reconnect behavior at least as good as the current implementation.
- Ensure bounded memory for streams, jobs, task registries, indexes, and caches under configured limits.
- Ensure every accepted long-running request reaches a durable terminal state after success, failure, cancellation, timeout, or recovery.
- Record enough timing data to attribute latency to DB, provider, tool, Git, dependency installation, process startup, health checks, or queue wait.

## Phase exit rule

A phase is complete only when its implementation tasks, migration, compatibility checks, failure tests, instrumentation, documentation updates, and rollback procedure are complete. The next phase must not depend on undocumented behavior from an incomplete phase.
