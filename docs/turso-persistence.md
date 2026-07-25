# Turso persistence: request, activity, cost, subagents

Every agent turn is persisted in the configured Turso (libSQL) database. This
document describes the five tables and shows the exact write sequence the
backend performs for one request, including a delegated subagent.

Configuration lives in Settings → AI (`turso_database_url`, `turso_auth_token`).
When Turso is unset, sessions/events still work through the local SQLite
fallback in `syte.local_session_store`; the rollup tables below are remote-only.

## Tables

| Table | Purpose | Written by |
| --- | --- | --- |
| `agent_session` | One numbered chat session (turn) per user message | `open_session`, `close_session` |
| `agent_session_event` | Full activity trail (thinking, tools, screenshots, usage) | `record_event` |
| `agent_message` | Raw chat messages (user / assistant / tool) | `record_message` |
| `agent_request` | **One row per request**: the request text, its timestamp, how much activity it caused, and its final cost | `record_request`, `finalize_request` |
| `agent_subagent_task` | **One row per delegation**: task text, declared file scope, start time, outcome, cost | `record_subagent_task`, `finalize_subagent_task` |
| `agent_subagent_activity` | Activity produced *by a subagent* (its own chat tab feed) | `record_subagent_activity` |

`agent_request.cost_usd` is deliberately `NULL` until generation finishes —
token usage (and therefore price) is only known at the end of the turn.

## Write sequence for one turn

```text
1. open_session(project_id, session_number, model_profile)   -> session uuid
2. record_request(request_id, project_id, request, ...)      -> status=running, cost=NULL
3. record_event(...) xN                                      -> activity trail
   record_message(...) xN                                    -> raw messages
4. record_subagent_task(task_id, ..., files=[...])           -> status=running, started_at
5. record_subagent_activity(task_id, ...) xN                 -> subagent tab feed
6. finalize_subagent_task(task_id, ..., usage, cost)         -> status + cost
7. finalize_request(request_id, ..., usage, cost,
                    activity_count, subagent_count)          -> status + COST (end)
8. close_session(session_id, status="completed")
```

`activity_count` is the number of non-stream activity events the request
produced; it is tracked in-process by `syte.agent_activity` and read back with
`activity_count_for_request(request_id)`.

## Worked example

User asks *"Add a pricing page"* on `syra-havy`, and the agent delegates the FAQ
page to a subagent.

### 1. Request accepted — `agent_request`

```python
await turso_store.record_request(
    "req-1", "proj-42", "Add a pricing page",
    session_id="8f1c…", session_number=7, source="gui",
    model_profile="syra-havy", model="glm-4.6", provider="nvidia",
    thinking_level=3,
)
```

Row right after insert:

```json
{
  "request_id": "req-1",
  "session_id": "8f1c…",
  "project_id": "proj-42",
  "session_number": 7,
  "source": "gui",
  "model_profile": "syra-havy",
  "request": "Add a pricing page",
  "status": "running",
  "timestamp": "2026-07-25T20:36:14.910085+00:00",
  "started_at": "2026-07-25T20:36:14.910085+00:00",
  "cost_usd": null,
  "activity_count": 0,
  "ended_at": null
}
```

### 2. Delegation — `agent_subagent_task`

The main agent must declare the file scope *before* handing work over, so no two
agents can write the same file (see `delegate_task.files`).

```python
await turso_store.record_subagent_task(
    "sub-abc", "proj-42", "Build the FAQ page",
    session_id="8f1c…", session_number=7, parent_request_id="req-1",
    mode="implementation", profile="syra-subagent", model="glm-5.2",
    background=True,
    files=["app/app/faq/page.tsx", "app/components/faq.tsx"],
)
```

```json
{
  "task_id": "sub-abc",
  "parent_request_id": "req-1",
  "task": "Build the FAQ page",
  "mode": "implementation",
  "background": true,
  "files": ["app/app/faq/page.tsx", "app/components/faq.tsx"],
  "status": "running",
  "started_at": "2026-07-25T20:36:14.910931+00:00",
  "ended_at": null,
  "cost_usd": null
}
```

### 3. Subagent activity — `agent_subagent_activity`

One row per line shown in the GUI's subagent tab:

```python
await turso_store.record_subagent_activity(
    "sub-abc", "proj-42", "tool_call_finished",
    session_id="8f1c…", parent_request_id="req-1", tool="write_file",
    title="write_file", detail='{"ok": true, "message": "wrote 2140 bytes"}',
    payload={"agent": "subagent", "task_id": "sub-abc", "step": 2},
)
```

### 4. Subagent finished — cost recorded

```python
await turso_store.finalize_subagent_task(
    "sub-abc", "proj-42", status="completed",
    result="Created app/app/faq/page.tsx and the FAQ component.",
    usage={"input_tokens": 300, "output_tokens": 120, "steps": 3},
    cost={"cost_usd": 0.0009, "label": "$0.0009 · 420 tokens"},
    activity_count=6,
)
```

### 5. End of generation — request cost

```python
await turso_store.finalize_request(
    "req-1", "proj-42", status="completed", reply="Added the pricing page.",
    usage={"input_tokens": 1200, "output_tokens": 400,
           "thinking_tokens": 100, "steps": 5},
    cost={"cost_usd": 0.0123, "label": "$0.0123 · 1700 tokens"},
    activity_count=17, subagent_count=1,
)
```

Final `agent_request` row:

```json
{
  "request_id": "req-1",
  "request": "Add a pricing page",
  "reply": "Added the pricing page.",
  "status": "completed",
  "timestamp": "2026-07-25T20:36:14.910085+00:00",
  "ended_at": "2026-07-25T20:36:29.480512+00:00",
  "activity_count": 17,
  "subagent_count": 1,
  "steps": 5,
  "input_tokens": 1200,
  "output_tokens": 400,
  "thinking_tokens": 100,
  "total_tokens": 1700,
  "cost_usd": 0.0123,
  "cost_label": "$0.0123 · 1700 tokens"
}
```

`status` is one of `running`, `completed`, `failed`, `cancelled`; failed and
cancelled turns are finalized too, with whatever usage/cost accrued.

## Reading it back

```python
row   = await turso_store.get_request("req-1", "proj-42")
tasks = await turso_store.list_subagent_tasks(session_id="8f1c…")
lines = await turso_store.list_subagent_activity("sub-abc")
```

Useful queries:

```sql
-- Spend per project, last 100 requests
SELECT project_id, SUM(cost_usd) AS usd, SUM(total_tokens) AS tokens
FROM agent_request WHERE status = 'completed' GROUP BY project_id;

-- Requests that produced a lot of activity but no reply
SELECT request_id, activity_count, status FROM agent_request
WHERE status != 'completed' ORDER BY activity_count DESC LIMIT 20;

-- Which files each subagent owned
SELECT task_id, mode, files, cost_usd, started_at, ended_at
FROM agent_subagent_task WHERE session_id = ?;
```

## Reliability notes

- Every write goes through `_write_with_retry`, which retries once on a
  transient failure and treats a `UNIQUE` conflict as "already recorded"
  (idempotent replay) rather than a lost row.
- Schema init is per-statement resilient: one rejected index never disables the
  other tables. Failures are surfaced through `turso_debug_status()` (the GUI
  "brain" indicator) instead of failing silently.
- `record_event` no longer reports a committed row as unsaved when the
  cosmetic `agent_session.updated_at` touch fails.
- Save helpers never raise into the turn: a persistence problem degrades
  observability, it never fails the user's request.
- **Hot path vs Turso:** `token_delta` / `thinking_delta` are batched
  (16–32 tokens or 300–500 chars), emitted as minimal-delta SSE frames, and
  **never** written to Turso. Cold activity events mirror to Turso in a
  **background task** so a slow remote DB cannot stall streaming or first
  token. Messages use the same fire-and-forget + end-of-turn resync pattern
  (`_resync_unsynced_messages`).
- SSE endpoints negotiate `gzip` / `br` via `Accept-Encoding` so external
  pages can keep the live feed cheap while Turso remains the durable store.
