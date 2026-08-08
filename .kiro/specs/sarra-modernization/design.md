# SARRA Modernization Design

## Design principles

1. **Measure before rewriting.** Add timings and counters to the existing seams first; optimize demonstrated bottlenecks.
2. **Async I/O remains the default.** Keep FastAPI, asyncio, SQLite/Turso, and the embedded Python runtime unless profiling proves a CPU-bound component requires another approach.
3. **Durability at boundaries.** User requests, jobs, deployments, process transitions, activity events, and cost records need durable state; ephemeral caches and worker handles do not.
4. **One source of truth per concern.** Shared read models, a unified job lifecycle, one resource policy, one event envelope, and one error taxonomy prevent subsystem drift.
5. **Compatibility before cutover.** Add adapters and versioned fields before removing legacy fields or changing cursor semantics.
6. **Bound every resource.** Every queue, cache, stream, subprocess, provider call, output, and retry loop has a limit and cleanup path.
7. **Security is cross-cutting.** Redaction, authorization, path safety, process isolation, and audit records are applied at boundaries, not bolted on at the UI.

## Current-state seams

| Concern | Existing seam | Modernization seam |
|---|---|---|
| Local persistence | `syte/database.py`, `syte/sqlite_utils.py` | `DatabaseManager` with pooled/controlled connections, migrations, query metrics |
| Remote persistence | `syte/turso_store.py`, `syte/local_session_store.py` | durable outbox/reconciliation and shared record adapters |
| Workspace reads | `syte/workspace_api.py`, GUI routes in `syte/main.py` | `ProjectSummaryRepository` and versioned workspace snapshot |
| Activity | `syte/agent_activity.py` | durable event envelope + replayable stream coordinator |
| File/Docker logs | `syte/log_stream.py` | project source watchers with shared broadcasters |
| Processes | `syte/process_manager.py`, `syte/supervisor.py` | process records, groups, identity checks, restart policy |
| Previews | `syte/preview_manager.py`, `syte/preview_health.py` | asynchronous preview jobs and semantic readiness |
| Deployments | `syte/deployment.py`, `syte/docker_deploy.py` | durable deployment state machine and version history |
| Agent requests | `syte/cloud_agent.py`, `syte/agent_jobs.py` | task supervisor, typed tool jobs, cancellation, budgets |
| Providers | `syte/ai_providers.py`, `syte/model_routing.py`, `syte/provider_quota.py` | provider registry, health samples, circuit breaker, profile routing |
| Memory/index | `syte/agent_memory.py`, `syte/token_efficiency.py` | incremental workspace index and context-budget retrieval |
| Operations | `syte/agent_metrics.py`, `syte/resource_monitor.py`, `syte/main.py` | structured telemetry, health/readiness, metrics API, lifecycle coordinator |
| Security | `syte/auth.py`, `syte/rate_limit.py`, workspace command tools | centralized policy, redaction, quotas, project authorization migration |

## Target architecture

```text
                         ┌──────────────────────────┐
                         │ FastAPI / GUI / token API │
                         └────────────┬─────────────┘
                                      │
                   ┌──────────────────▼──────────────────┐
                   │ API read models + command adapters   │
                   │ ETags, pagination, auth, rate limits │
                   └───────┬─────────────┬───────────────┘
                           │             │
                ┌──────────▼───┐   ┌────▼────────────────┐
                │ Cache layers │   │ Unified Job Manager │
                │ L1/L2/L3     │   │ queues + priorities │
                └──────┬───────┘   └────┬────────────────┘
                       │                │
       ┌───────────────▼────────┐ ┌────▼───────────────────────────────┐
       │ Database manager       │ │ workers                             │
       │ SQLite/Turso/outbox    │ │ agent · tool · preview · deploy     │
       │ migrations + metrics   │ │ build · health · index · log source  │
       └───────────────┬────────┘ └────┬──────────────────────────────────┘
                       │               │
       ┌───────────────▼──────┐  ┌─────▼──────────────┐  ┌───────────────┐
       │ Durable state/events │  │ Resource manager   │  │ Process groups │
       │ jobs, sessions, cost │  │ CPU/RAM/disk/etc.  │  │ PID identity   │
       └───────────────┬──────┘  └────────────────────┘  └──────┬────────┘
                       │                                         │
       ┌───────────────▼──────────────┐                  ┌───────▼────────┐
       │ Event/timeline coordinator   │                  │ preview/deploy │
       │ replay + broadcaster + SSE   │                  │ health/rollback│
       └───────┬───────────────┬──────┘                  └────────────────┘
               │               │
      ┌────────▼──────┐ ┌──────▼──────────┐
      │ activity/SSE  │ │ file/Docker log │
      │ durable cursor│ │ shared tailers  │
      └───────────────┘ └─────────────────┘

       ┌──────────────────────────────────────────────────────────┐
       │ Context/index plane: file index → retrieval → token budget│
       │ provider registry → health/routing → cost/budget policy   │
       └──────────────────────────────────────────────────────────┘
```

## Core data models

### Job

```text
Job
- id: UUID
- kind: agent | tool | preview | dependency | build | deploy | health | index | command
- project_id: UUID?
- parent_id: UUID?
- priority: critical | high | normal | low | idle
- status: queued | preparing | running | cancelling | succeeded | failed | cancelled | expired
- phase: string?
- created_at, started_at, heartbeat_at, finished_at
- timeout_seconds
- attempt, max_attempts, next_retry_at
- owner/lease identity
- progress: safe JSON
- result/error reference
- resource reservation
```

### Agent task

Agent requests and delegated tasks remain compatible with `agent_jobs.py` and Turso records but gain normalized fields:

```text
task_id, parent_task_id, request_id, project_id, agent_id,
status, created_at, started_at, completed_at, timeout_seconds,
retry_count, token_usage, estimated_cost, error_category,
error, cancel_reason, provider, model, resource reservation
```

`agent_request`, `agent_session`, and `agent_subagent_task` remain the durable compatibility records. A mapping layer associates them with unified jobs/tasks rather than requiring an immediate table replacement.

### Event envelope

```json
{
  "event_id": "evt_…",
  "event_type": "tool_completed",
  "occurred_at": "2026-08-07T00:00:00Z",
  "project_id": "…",
  "session_id": "…",
  "request_id": "…",
  "task_id": "…",
  "parent_event_id": "…",
  "lane": "main",
  "status": "success",
  "duration_ms": 421,
  "payload": {},
  "redaction_version": 1
}
```

Existing `agent_activity` fields (`id`, `event_type`, `detail`, `payload`, `source`, `created_at`) remain readable. New event fields are additive and transport adapters continue emitting existing SSE frames.

### Tool job

```text
ToolJob
- id, task_id, project_id
- tool_name
- safe_arguments_summary
- status, timeout_seconds
- stdout_ref, stderr_ref, output_bytes
- exit_code
- created_at, started_at, finished_at
- error_category, redaction_version
```

Raw output is stored in bounded files or job records according to retention policy. The model-facing result is a compact structured projection, not an unbounded copy of stdout.

### Deployment

```text
Deployment
- id, project_id, parent/rollback_of
- source_ref, commit_sha, build_fingerprint
- state: queued | preparing | building | testing | deploying |
         health_check | ready | failed | rolling_back | rolled_back
- previous_deployment_id
- artifact/workspace references
- process/group identity
- health_check summary
- failure_category/reason
- created/started/finished timestamps
```

The current deployment endpoints are adapters over this state machine until a versioned API is ready.

## Database and cache strategy

### Local database

Introduce a `DatabaseManager` around `aiosqlite` with:

- One controlled connection per event-loop/role or a small bounded pool, chosen after benchmark comparison.
- Centralized PRAGMA initialization and health checks.
- Explicit read/write transaction helpers.
- A migration table and numbered idempotent migrations rather than scattered startup `ALTER TABLE` calls.
- Query timing hooks that use query keys rather than sensitive SQL interpolation in logs.
- Indexes derived from observed query plans and access patterns.

Do not hold a global connection across fork boundaries. The manager must reopen safely after application startup/reload and close all connections during lifespan shutdown.

### Turso and outbox

Local writes remain the source of truth for request admission and local fallback. Remote writes continue best-effort but are represented by an outbox/sync status so failures can be retried and inspected. Existing idempotency keys and UUID records are retained. Reconciliation must be bounded and must not block token streaming or the request's critical path.

### Cache layers

- **L1:** bounded in-process TTL cache for settings, project summaries, framework detection, provider health snapshots, and immutable-ish metadata.
- **L2:** durable SQLite/Turso state for persisted facts, not a blind cache of mutable commands.
- **L3:** workspace filesystem cache for hashes, Git metadata, generated manifests, indexes, and bounded logs.

Every cache entry has an owner, TTL, freshness key/version, and invalidation event. Writes invalidate affected project/settings/provider/index keys. Cache hits/misses and stale refreshes are measurable.

## API and streaming compatibility

### Read model

Create a project-summary query that returns registry data and cheap derived state. Expensive preview/agent/SSL refreshes are separate bounded jobs or cached projections. `workspace_get()` remains available for detail pages but is not used as the list primitive.

Pagination uses stable `(created_at, id)` or equivalent cursors. Existing unpaginated clients receive a compatibility default and a response metadata field before pagination becomes required.

ETags are derived from a response version or row/update timestamps, never from secrets. Last-Modified is only emitted when all included data has a trustworthy timestamp.

### Stream coordinator

A `ProjectStreamCoordinator` maintains source workers and subscriber registries:

```text
source (file/Docker/activity)
    ↓ one worker per project/source
bounded event buffer + durable cursor
    ↓ fan-out with per-subscriber bounded queues
SSE adapters / polling adapters
```

Each worker stops when its last subscriber leaves and its idle cleanup timer expires. Backpressure drops old transient frames only when safe and emits the existing `stream_gap` contract. Durable events are replayed from storage; hot token frames retain current low-latency behavior.

## Unified jobs and execution

The job manager is an in-process scheduler backed by durable state. It does not require Redis in the first phase. A worker claims a job using an owner/lease token and heartbeat. A job cannot be executed by another worker while its lease is healthy. On restart, expired leases are reconciled.

Adapters wrap existing subsystems:

- `AgentJobAdapter` around `cloud_agent.py` and `agent_jobs.py`.
- `ToolJobAdapter` around command and typed agent tools.
- `PreviewJobAdapter` around dependency installation/startup in `preview_manager.py`.
- `DeploymentJobAdapter` around `deployment.py`.
- `IndexJobAdapter` around `agent_memory.py`.
- `HealthJobAdapter` around process, port, HTTP, and custom checks.

The resource manager reserves capacity before a worker starts. If capacity is unavailable, the job remains queued rather than launching and failing unpredictably.

## Process and preview supervision

Process identity uses PID plus start time (or Linux `/proc/<pid>/stat` identity), command/workspace verification, and process-group ID. All managed child processes are launched into a group. A shutdown controller performs graceful termination, timeout, and forced cleanup on the group.

Restart policy is data, not scattered control flow:

```text
max_attempts
initial_backoff
max_backoff
jitter
crash_window
cooldown
restart_on_exit_codes
```

Dependency installation is a preview job. Detection first identifies lockfiles and package manager, then selects:

- `npm ci` for `package-lock.json`.
- `pnpm install --frozen-lockfile` for `pnpm-lock.yaml`.
- `yarn install --frozen-lockfile` for `yarn.lock`.
- A controlled Python environment/reuse path for Python projects.

Commands are still executed with the current security restrictions and new limits. A dependency fingerprint plus lockfile hash determines whether installation can be skipped.

## Deployment state machine

```text
QUEUED → PREPARING → BUILDING → TESTING → DEPLOYING → HEALTH_CHECK → READY
   └────────────── failure at any stage ───────────────→ FAILED
                                                         ↓
                                              ROLLING_BACK → ROLLED_BACK
```

A supported zero-downtime deployment reserves resources for the new version, starts it in an isolated process/group or container, performs health checks, updates the routing target, confirms traffic, and stops the old version. If any transition fails before traffic switch, the old version remains active. If it fails after switch, rollback policy decides whether to restore the previous target.

## Agent supervision and model routing

The supervisor wraps each accepted request and delegated task in a normalized task record. Child cancellation tokens propagate through provider calls, tools, subprocesses, questions, and streams. A task registry is a bounded index of active tasks; durable records remain after cleanup.

Error classification is shared across jobs and providers:

```text
TIMEOUT, RATE_LIMIT, AUTH, NETWORK, DATABASE, PROCESS,
BUILD, DEPENDENCY, CONFIG, USER_INPUT, SECURITY, RESOURCE_LIMIT
```

Retry policy maps categories to attempts/backoff. Provider circuit state is persisted only when cross-worker consistency is needed; otherwise it is an expiring shared snapshot with clear degradation semantics.

Routing separates:

1. Provider capability/profile registry.
2. Task classification and FAST/BALANCED/POWER/LOCAL selection.
3. Health/circuit policy.
4. Budget and fallback policy.
5. Request execution and usage accounting.

Current named Syra profiles map to the new stable classes. Routing decisions emit safe structured events so latency, fallback, and cost can be analyzed.

## Context and workspace intelligence

The workspace index is a local, project-scoped service with a metadata table and pluggable search backends:

```text
path, content_hash, mtime_ns, size_bytes, language,
symbols, imports, exports, dependencies, semantic_tags,
parser_version, indexed_at, deleted_at
```

An action or filesystem watcher adds paths to an index queue. The worker compares hash/mtime/size, parses only changed files, removes deleted paths, and records parser errors without failing the whole index. The initial implementation can use SQLite tables/FTS and ripgrep for text search; embeddings/vector search remain optional and must be justified by measured retrieval quality/cost.

Retrieval ranks the user task, explicit paths, recent changes, symbols, dependency edges, semantic tags, and file freshness. A context budget manager then selects content under a hard token budget. The selected context includes provenance so the agent and timeline can explain why files were included.

Project manifests are generated from the same detection/index data and include framework, language, package manager, commands, port, entrypoint, database, and deployment information. They are cacheable and invalidated by relevant lock/config changes.

## Timeline, tool results, and verification

Every job/task operation emits an event envelope. File writes, command execution, tests, model calls, deployment transitions, and health checks are correlated through parent IDs. Existing activity lanes and event types remain available through adapters.

Typed tool jobs enforce:

- Per-tool timeout and output byte limit.
- Working-directory and environment policy.
- Process/resource reservation.
- Secret redaction.
- Result projection: errors/warnings/relevant sections first.
- Durable status and cancellation.

The verification runner receives a project manifest and task mode. It selects applicable format/type/lint/unit/build/preview checks, runs independent checks concurrently where safe, and emits a final matrix rather than claiming success from one command.

## Observability and lifecycle

A structured logging helper emits the common event envelope. Metrics are aggregated by component and outcome, with cardinality controls for project/task IDs. `/api/metrics` returns safe summaries, and health routes distinguish:

- Liveness: process can answer.
- Readiness: database and essential supervisors are available.
- Dependency health: provider/Turso/Caddy/preview dependencies.
- Degraded: service can answer but one non-critical capability is unavailable.

The lifecycle coordinator owns startup and shutdown ordering:

```text
startup:
configuration → database/migrations → recovery/reconciliation → routes → workers

shutdown:
stop admission → drain short jobs → cancel long jobs → stop process groups
→ flush event/log buffers → close provider/database clients → exit
```

The current supervisor remains an adapter until responsibilities are moved into explicit worker components. Shutdown must call provider/client close hooks, including paths currently exposed but not invoked by lifespan management.

## Security and data hygiene

Central policy functions are used by API, agent tools, jobs, logs, and archive handling. Redaction occurs before persistence and before model context. Raw secrets are never included in timing labels, error strings, event payloads, cache keys, or job summaries.

Resource controls use OS facilities where available (process groups, cgroups/systemd limits, `resource`, or container limits) and degrade transparently when unavailable. The service documents the effective limit rather than implying enforcement it cannot provide.

Project-scoped authorization is a separate compatibility phase because current valid tokens are host-global. No new project-scoped endpoint should assume isolation until the token/project policy is explicitly defined and migrated.

## Migration and rollout strategy

1. Add instrumentation and compatibility adapters with no behavior change.
2. Add additive schema/migrations and backfill asynchronously.
3. Shadow new read models, routing decisions, index retrieval, and health checks against existing behavior.
4. Enable one project or feature flag at a time.
5. Compare latency, errors, resource use, and result equivalence.
6. Promote only after rollback thresholds remain clear.
7. Remove old paths only in a separate deprecation change after clients migrate.

Rollback must be possible by disabling the feature flag and preserving the old durable records. Destructive migrations, automatic deletion, traffic switching, or legacy field removal require an explicit migration task and operator confirmation.

## Risks and mitigations

| Risk | Mitigation |
|---|---|
| Connection reuse increases lock contention | benchmark pool sizes; centralize transactions; retain busy timeout; measure wait time |
| Shared log workers lose events | durable cursor/replay; gap frame; source-specific tests; bounded queue policy |
| Job recovery duplicates mutable work | leases, idempotency keys, transition guards, recovery tests |
| Process cleanup kills unrelated PID | start-time identity, command/workspace validation, process groups |
| New deployment health check blocks valid apps | configurable probes, compatibility threshold, explicit degraded state |
| Context index returns stale/wrong files | hash/version checks, deletion events, provenance, shadow comparison |
| Provider routing causes unexpected cost | budgets, allowlists, logged decisions, fail-closed cost policy |
| Redaction removes useful debugging data | typed safe summaries, secret pattern tests, operator-only references |
| Schema migration fails partway | numbered idempotent migrations, transactional where possible, backups, rollback notes |
| In-process scheduler cannot scale horizontally | single-host scope initially; durable lease model before multi-worker deployment |

## Definition of done for the full program

The roadmap is complete when all requirements have an implemented owner, durable state and migration behavior where applicable, compatibility coverage, failure/recovery tests, bounded resource behavior, structured metrics, updated contracts, and measured evidence that the resulting system is faster or more stable than the captured baseline.
