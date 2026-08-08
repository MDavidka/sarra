# SARRA Modernization Implementation Tasks

Tasks are ordered by dependency. Each phase should be implemented and verified before enabling the next phase broadly. Subtasks are intentionally concrete enough to become implementation tickets, but they do not prescribe a rewrite of the existing modules.

## Phase 0 — Baseline, contracts, and safety gates

- [ ] 0.1 Capture baseline measurements for API latency, workspace listing, DB connection count/lock wait, preview startup, dependency installation, log stream memory, agent/provider latency, deployment duration, and shutdown time.
- [ ] 0.2 Add correlation IDs and low-cardinality request/job/session/project timing hooks without changing response contracts.
- [ ] 0.3 Inventory every current schema, migration, endpoint, SSE event, cursor, legacy field, process PID file, and durable Turso/local record touched by the roadmap.
- [ ] 0.4 Define feature flags, operator rollback switches, retention defaults, resource defaults, and per-phase success/failure thresholds.
- [ ] 0.5 Add contract fixtures from `docs/cloud-agent-contract.md`, `docs/agent-streaming-api.md`, `docs/turso-persistence.md`, and generated specs in `syte/ai_spec.py` and `syte/sycord/spec.py`.
- [ ] 0.6 Add a shared error taxonomy and safe structured-event/redaction primitives, then migrate only new instrumentation to them initially.

## Phase 1 — Persistence, caching, and API read performance

- [ ] 1.1 Implement numbered, idempotent SQLite migrations and migration status reporting; preserve existing additive migrations during transition.
- [ ] 1.2 Implement a controlled async `DatabaseManager` around `aiosqlite`; benchmark a per-loop connection and bounded-pool strategy before selecting the default.
- [ ] 1.3 Centralize WAL, `synchronous=NORMAL`, `busy_timeout`, foreign-key, and connection health initialization.
- [ ] 1.4 Add query timing, transaction timing, lock-wait, error, and slow-query instrumentation with secret-safe query keys.
- [ ] 1.5 Add and verify indexes for project, session, request, task, status, and timestamp queries using query plans and measured workloads.
- [ ] 1.6 Add settings/project metadata cache primitives with TTL, version keys, write invalidation, and cache metrics.
- [ ] 1.7 Define the local/Turso outbox or reconciliation records and expose failed-sync diagnostics without blocking the agent hot path.
- [ ] 1.8 Add a project-summary repository and refactor `workspace_list()` to use batched/summary reads rather than full `workspace_get()` enrichment per project.
- [ ] 1.9 Consolidate GUI and token-facing workspace response construction behind a versioned read model; retain legacy fields.
- [ ] 1.10 Add stable pagination for projects, sessions, logs, and activity, plus ETag/Last-Modified behavior for safe GET responses.
- [ ] 1.11 Add response compression where safe and verify that SSE compression continues to flush each frame.
- [ ] 1.12 Compare baseline and shadow results; enable the new read path only when latency, correctness, and lock metrics meet Phase 1 thresholds.

## Phase 2 — Shared streaming and SSE connection management

- [ ] 2.1 Document the canonical relationship among durable activity events, file logs, Docker logs, polling, and SSE, including cursor and ordering semantics.
- [ ] 2.2 Implement a project/source stream coordinator with one worker per active file/Docker/activity source where possible.
- [ ] 2.3 Add inotify/event-driven file watching with a bounded polling fallback and truncation/rotation detection.
- [ ] 2.4 Replace repeated Docker log execution with shared long-lived source workers and explicit source cleanup.
- [ ] 2.5 Implement bounded per-subscriber queues, slow-consumer handling, idle cleanup, disconnect callbacks, and source worker shutdown.
- [ ] 2.6 Preserve `retry`, heartbeat, `since_id`, replay, compression, `stream_gap`, lane, and named-event behavior from `docs/agent-streaming-api.md`.
- [ ] 2.7 Add per-user and global stream limits, heartbeat timeout detection, and metrics for active connections, drops, gaps, and worker count.
- [ ] 2.8 Add reconnect, backpressure, source rotation, client cancellation, and multi-subscriber integration coverage.

## Phase 3 — Unified jobs, resources, and command execution

- [ ] 3.1 Add the unified Job schema, state transitions, priority, timeout, retry, lease, heartbeat, result, and error fields through additive migrations.
- [ ] 3.2 Implement the single-host priority scheduler with critical/high/normal/low/idle queues and fairness rules.
- [ ] 3.3 Implement job adapters for agent requests, tool commands, previews, dependency installs, builds, deployments, health checks, and indexing.
- [ ] 3.4 Implement cancellation propagation from API request to child jobs, provider calls, subprocess groups, questions, and streams.
- [ ] 3.5 Implement task/job cleanup for completed asyncio tasks, stale registry entries, abandoned questions, expired streams, and old terminal records according to retention.
- [ ] 3.6 Implement resource reservations and limits for active agents, previews, builds, commands, deployments, ports, CPU, memory, disk, processes, and output bytes.
- [ ] 3.7 Add `POST /jobs`, `GET /jobs/{id}`, cancellation, pagination, progress, and safe log/status projections where public exposure is appropriate.
- [ ] 3.8 Enforce working-directory, environment, network, timeout, output, process-count, and resource policies for long commands; retain existing path/security restrictions.
- [ ] 3.9 Add lease-expiry, duplicate-claim, cancellation-race, resource-exhaustion, restart, and graceful-drain tests.

## Phase 4 — Process supervisor and preview modernization

- [ ] 4.1 Define and persist process identity/state records: PID, start identity, group ID, expected state, exit code, restart count, heartbeat, and failure reason.
- [ ] 4.2 Launch managed processes in process groups and implement graceful shutdown → timeout → forced termination for descendants.
- [ ] 4.3 Implement restart policy data, exponential backoff with jitter, crash-window detection, maximum attempts, and cooldown/circuit states.
- [ ] 4.4 Add startup-crash and PID-reuse detection using start-time/command/workspace validation.
- [ ] 4.5 Convert preview dependency installation to a background job with progress states and cancellation.
- [ ] 4.6 Extend framework/package-manager detection for Next.js, Vite, React, Vue, Svelte, SvelteKit, Astro, Nuxt, Remix, Express, FastAPI, Flask, Django, Go, Rust, and static HTML.
- [ ] 4.7 Detect lockfiles and select safe npm/pnpm/yarn/Python environment commands; fingerprint dependency state and skip valid installs.
- [ ] 4.8 Cache framework detection and generate the project manifest consumed by preview, agent, and deployment paths.
- [ ] 4.9 Improve readiness checks with expected status, response-time, custom endpoint, process, and port validation; preserve compatible preview status fields.
- [ ] 4.10 Add preview cleanup, abandoned job recovery, process-group, dependency-cache, and failure-restart coverage.

## Phase 5 — Durable deployment lifecycle and rollback

- [ ] 5.1 Add deployment records, state transitions, source/build/environment metadata, artifact references, health results, and previous-version links.
- [ ] 5.2 Adapt `syte/deployment.py` and `syte/docker_deploy.py` to emit durable phase transitions and typed job/timeline events.
- [ ] 5.3 Implement build/test/deploy/health-check jobs with bounded logs, cancellation, retries, and exact failure categories.
- [ ] 5.4 Implement post-deployment checks for process, port, HTTP status, response time, and optional custom health endpoint.
- [ ] 5.5 Implement deployment history and selection of the previous healthy version for rollback.
- [ ] 5.6 Implement zero-downtime traffic switching for supported services, with old-version retention until the new version is healthy.
- [ ] 5.7 Add recovery handling for interrupted builds, partial switches, stale deployment leases, missing artifacts, and dead processes.
- [ ] 5.8 Preserve existing deployment endpoints/status/stream links through compatibility adapters and update deployment documentation.
- [ ] 5.9 Add state-machine transition, health failure, rollback, cancellation, restart, and no-downtime integration coverage.

## Phase 6 — Agent supervision, routing, reliability, and cost

- [ ] 6.1 Normalize agent/subagent task fields and map them to existing `agent_request`, `agent_session`, and `agent_subagent_task` records.
- [ ] 6.2 Add per-task, per-tool, per-agent, and global concurrency controls with queue visibility and resource reservations.
- [ ] 6.3 Add per-task/per-tool timeout enforcement and cancellation tokens through `cloud_agent.py`, provider calls, tools, questions, and subprocesses.
- [ ] 6.4 Add retry policy by error category with exponential backoff, jitter, maximum attempts, and no-retry categories for auth/config/user/security failures.
- [ ] 6.5 Implement provider health samples, availability, latency/error/429/timeout metrics, cooldowns, and closed/open/half-open circuit breakers.
- [ ] 6.6 Define FAST/BALANCED/POWER/LOCAL/FAILOVER routing policy while preserving current named provider profiles and explicit profile selection.
- [ ] 6.7 Add provider capability checks, healthy-provider selection, fallback events, and degradation behavior when no provider meets the request.
- [ ] 6.8 Record token usage, duration, model/provider, tool calls, estimated cost, and budget dimensions per turn, task, subagent, project, user, day, and provider.
- [ ] 6.9 Add configurable budget actions: warn, degrade model, queue, reject, or stop according to policy; ensure cost accounting is safe when providers omit usage.
- [ ] 6.10 Close provider clients and cancel worker tasks during application shutdown; add restart/recovery and provider-failure tests.

## Phase 7 — Incremental workspace intelligence and context optimization

- [ ] 7.1 Define workspace index schema/version and ownership for path metadata, hashes, language, size, mtime, symbols, imports/exports, dependencies, tags, parser version, and deletion state.
- [ ] 7.2 Refactor `agent_memory.py` scan behavior into incremental queue-driven indexing with hash/mtime/size checks and deletion reconciliation.
- [ ] 7.3 Add filesystem/action event ingestion and bounded index jobs; avoid full-project re-indexing after every agent action.
- [ ] 7.4 Implement fast filename/text search using the index plus ripgrep, with safe workspace boundaries and result limits.
- [ ] 7.5 Add symbol, dependency, impact, and semantic search interfaces; make FTS/vector/embedding backends optional and measurable.
- [ ] 7.6 Add parser failure records, freshness status, index metrics, cache invalidation, and recovery after interrupted indexing.
- [ ] 7.7 Implement context budget allocation across instructions, task, relevant files/symbols, recent changes, tool results, history, and response reserve.
- [ ] 7.8 Add layered memory retention/injection rules for turn, session, project, long-term, and ephemeral information.
- [ ] 7.9 Add file-hash content cache, duplicate tool-result detection, structure-aware result compression, secret redaction, and provenance for selected context.
- [ ] 7.10 Add recent-change prioritization, Git changed-file/diff caching, dependency graph extraction, and project manifest refresh triggers.
- [ ] 7.11 Compare selected context against current full-scan behavior for relevance, freshness, token use, and task outcome before enabling by default.

## Phase 8 — Timeline, verification loop, and multi-agent modes

- [ ] 8.1 Normalize structured timeline events for turn/model/thinking/tool/file/command/test/preview/deployment/response lifecycle stages.
- [ ] 8.2 Add typed ToolJob records and model-facing safe result projections with stdout/stderr references and output limits.
- [ ] 8.3 Add timeline filtering, search, replay, pagination, cost analysis, failure analysis, and existing SSE/polling cursor adapters.
- [ ] 8.4 Add parallel execution for independent reads/search/inspection and deterministic synchronization for dependent writes/actions.
- [ ] 8.5 Implement a verification runner using the project manifest and task mode; report format, type, lint, test, build, and preview-health results individually.
- [ ] 8.6 Implement bounded failure recovery: classify → retrieve relevant context → fix → verify → retry or report.
- [ ] 8.7 Add BUILD, DEBUG, REFACTOR, REVIEW, DEPLOY, and MAINTENANCE mode policies without forcing every request through every agent role.
- [ ] 8.8 Add optional Planner → Coder → Tester → Reviewer → Deployer orchestration with explicit task DAGs, ownership, cancellation, and cost limits.
- [ ] 8.9 Add replay and failure-recovery integration tests covering partial writes, cancelled turns, tool timeouts, failed builds, and provider fallback.

## Phase 9 — Observability, security, storage hygiene, and lifecycle

- [ ] 9.1 Replace important string-only logs with structured events using component, event, correlation IDs, duration, status, and safe context.
- [ ] 9.2 Expose `/api/metrics` and a dashboard/read model for API, DB, provider, tool, preview, Git, build, deployment, job, token/cost, CPU, RAM, disk, and queue metrics.
- [ ] 9.3 Separate liveness, readiness, dependency health, and degraded responses; document each endpoint and status transition.
- [ ] 9.4 Add centralized secret redaction for API keys, tokens, passwords, auth headers, `.env`, private keys, logs, tool output, context, and errors.
- [ ] 9.5 Add command audit records, request body/upload limits, per-identity stream/job/rate limits, archive protections, symlink checks, and workspace quotas.
- [ ] 9.6 Define and enforce log rotation/compression/retention for deploy, preview, agent, and job logs with per-project quotas.
- [ ] 9.7 Add workspace/build/temp/index cleanup based on size, age, retention, and active-reference checks.
- [ ] 9.8 Audit systemd/container privileges and document or reduce remaining root requirements.
- [ ] 9.9 Implement lifecycle coordinator ordering for startup recovery, admission stop, job drain, process cleanup, log flush, provider close, and database close.
- [ ] 9.10 Add recovery drills for SIGTERM/SIGINT, host reboot, interrupted migration, stale jobs, running previews, occupied ports, and partial deployments.

## Phase 10 — Rollout, documentation, and deprecation

- [ ] 10.1 Run each phase behind its feature flag against representative projects and capture before/after measurements.
- [ ] 10.2 Update `README.md`, API contracts, cloud-agent contract, streaming contract, Turso persistence notes, resource monitor docs, and generated specs.
- [ ] 10.3 Publish operator guidance for limits, queues, health states, rollback, retention, cache invalidation, and recovery.
- [ ] 10.4 Review compatibility telemetry for legacy fields, unpaginated clients, old SSE cursors, and named provider profiles.
- [ ] 10.5 Deprecate old paths only after a documented migration window and verified client coverage.
- [ ] 10.6 Produce a final performance/stability report showing target metrics, known limitations, residual risks, and recommended follow-up work.

## Cross-phase verification checklist

- [ ] Every schema change has an idempotent migration and rollback/back-up note.
- [ ] Every public API/event change has compatibility coverage.
- [ ] Every long-running operation has timeout, cancellation, retry, resource, and terminal-state behavior.
- [ ] Every cache has TTL, invalidation, ownership, and stale-data behavior.
- [ ] Every stream has bounded buffers, disconnect cleanup, replay/gap semantics, and connection limits.
- [ ] Every subprocess has identity validation, process-group cleanup, output limits, and failure classification.
- [ ] Every provider call has health, timeout, cost, retry, and redaction behavior.
- [ ] Every index has freshness, deletion, parser-error, and recovery behavior.
- [ ] Every phase has baseline comparison and an operator rollback switch.
- [ ] No phase claims success solely because a command exited zero; relevant checks and health signals must be recorded.
