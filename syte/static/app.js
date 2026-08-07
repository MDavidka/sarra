const API = '/api';
const API_KEY_STORAGE = 'syte_api_key';
const CONTEXT_STORAGE = 'syte_context';

let projects = [];
let logStream = null;
let previewStream = null;
let activeServiceId = null;
let deployPollTimer = null;
let previewPollTimer = null;
let lastPreviewFrameSrc = '';
let previewTabActive = false;
let agentActivityPollTimer = null;
let agentActivityEventSource = null;
let debugChatResumeSession = null;
let agentActivityPollInFlight = false;
const AGENT_ACTIVITY_POLL_INTERVAL_MS = 2000;
// SSE recovery. A dropped connection used to fall back to 2s polling forever,
// so the chat "lost signal" for the rest of the session after one blip. Now the
// stream is re-opened with exponential backoff and polling only covers the gap.
let agentActivityReconnectTimer = null;
let agentActivityReconnectAttempts = 0;
let agentActivityStreamProjectId = null;
let agentActivityStallTimer = null;
let agentActivityLastFrameAt = 0;
const AGENT_ACTIVITY_RECONNECT_BASE_MS = 1000;
const AGENT_ACTIVITY_RECONNECT_MAX_MS = 15000;
// The server sends a heartbeat every 10s, so silence well past that means the
// connection is dead even though the browser has not reported an error yet
// (common on mobile networks and after backgrounding a tab).
const AGENT_ACTIVITY_STALL_TIMEOUT_MS = 35000;
const AGENT_ACTIVITY_STALL_CHECK_MS = 5000;
let debugChatBrainPollTimer = null;
let debugChatBrainPollInFlight = false;
const DEBUG_CHAT_BRAIN_POLL_INTERVAL_MS = 3000;
let debugChatSinceId = 0;
let debugChatRenderedIds = new Set();
let debugChatAutoScroll = true;
let debugChatBusy = false;
let debugChatReplayingHistory = false;
let debugChatLoadedProjectId = null;
let debugChatLastUserMessage = '';
let debugChatStreamBuffers = new Map();
let debugChatStreamFlushFrame = null;
let debugChatThinkingBuffers = new Map();
let debugChatThinkingFlushFrame = null;
let debugChatActiveRequestId = '';
let debugChatStopping = false;
let debugChatActivityDismissTimer = null;
let debugChatRequestWatchdogTimer = null;
let debugChatRequestStartedAt = 0;
let debugChatSendInFlight = false;
let debugChatConnectionState = 'disconnected';
let debugChatTerminalRequestIds = new Set();
// Turns the user explicitly stopped, so a terminal event arriving afterwards is
// reported as "Response stopped" rather than "Response failed".
let debugChatStoppedRequestIds = new Set();
let debugChatIdleStatus = 'Agent ready';
let debugChatActivityLabel = '';
let debugChatResourceMode = '';
// Chat lanes: the Main tab shows only main-agent activity, the subagent tab
// (revealed the first time a subagent starts) shows only subagent activity.
let debugChatActiveLane = 'main';
let debugChatRenderLane = 'main';
let debugChatSubagentSeen = false;
let debugChatSubagentUnread = 0;
const debugChatAutoScrollByLane = { main: true, sub: true };
let projectFilterText = '';
let projectSortMode = 'newest';
let appContext = 'non-conected';
let statsPollTimer = null;
let activeSvcTab = 'general';
let logsAutoScroll = true;
let serverPublicIp = '';
let syraCsrfToken = '';
let operatorSessionRestorePromise = null;

const STACK_META = {
  nextjs: { label: 'next.js', icon: 'N', cls: '' },
  python: { label: 'python', icon: 'Py', cls: 'stack-python' },
  javascript: { label: 'javascript', icon: 'JS', cls: 'stack-javascript' },
  html5: { label: 'html5', icon: '5', cls: 'stack-html5' },
  shell: { label: 'shell', icon: '$', cls: 'stack-shell' },
  docker: { label: 'docker', icon: 'D', cls: '' },
};

let selectedCreateStack = 'nextjs';

function getApiKey() {
  try {
    return sessionStorage.getItem(API_KEY_STORAGE) || '';
  } catch (e) {
    return '';
  }
}

function normalizeFetchError(message) {
  const msg = (message || '').trim();
  if (!msg || msg === 'Load failed' || msg === 'Failed to fetch' || msg === 'NetworkError when attempting to fetch resource.') {
    return 'Could not reach the Syte server. Your message is still available to retry when the connection returns.';
  }
  return msg;
}

function parseApiErrorPayload(err, statusText, status) {
  // HTTP/2 responses have an empty statusText, and gateway/plain-text errors
  // have no JSON body — without the status code those become a blank failure.
  const fallback = statusText || (status ? `HTTP ${status}` : 'Request failed');
  if (!err) return fallback;
  const detail = err.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg || d).join(', ');
  if (detail && typeof detail === 'object') return detail.message || detail.error || fallback;
  return err.message || fallback;
}

function setApiKey(key) {
  try {
    if (key) sessionStorage.setItem(API_KEY_STORAGE, key);
    else sessionStorage.removeItem(API_KEY_STORAGE);
  } catch (e) { /* private session storage unavailable */ }
}

function stopLogStream() {
  if (logStream) {
    logStream.close();
    logStream = null;
  }
  if (deployPollTimer) {
    clearInterval(deployPollTimer);
    deployPollTimer = null;
  }
  setLogsLiveIndicator(false);
}

function stopPreviewStream() {
  if (previewStream) {
    previewStream.close();
    previewStream = null;
  }
  stopPreviewPoll();
  setPreviewLogsLiveIndicator(false);
}

function stopAgentActivityPollFallback() {
  if (agentActivityPollTimer) {
    clearInterval(agentActivityPollTimer);
    agentActivityPollTimer = null;
  }
}

function stopAgentActivityStream() {
  stopAgentActivityPollFallback();
  if (agentActivityReconnectTimer) {
    clearTimeout(agentActivityReconnectTimer);
    agentActivityReconnectTimer = null;
  }
  if (agentActivityStallTimer) {
    clearInterval(agentActivityStallTimer);
    agentActivityStallTimer = null;
  }
  if (agentActivityEventSource) {
    agentActivityEventSource.onerror = null;
    agentActivityEventSource.onopen = null;
    agentActivityEventSource.close();
    agentActivityEventSource = null;
  }
  agentActivityStreamProjectId = null;
  agentActivityReconnectAttempts = 0;
  agentActivityLastFrameAt = 0;
  agentActivityPollInFlight = false;
  setDebugChatConnectionState('disconnected');
  stopDebugChatBrainPoll();
}

// "Brain" indicator: green when every message in the current chat session has
// been durably saved to Turso, red when at least one has not synced (or
// Turso is unreachable), gray/dim when Turso isn't configured or status is
// not yet known. Backed by GET /projects/{id}/agent/turso_sync.
function setDebugChatBrainStatus(sync) {
  const btn = document.getElementById('debug-chat-brain');
  if (!btn) return;
  btn.classList.remove('brain-saved', 'brain-unsaved', 'brain-unconfigured');
  let label;
  if (!sync || !sync.turso_configured) {
    btn.classList.add('brain-unconfigured');
    label = 'Turso is not configured — messages are only saved locally';
  } else if (sync.all_saved) {
    btn.classList.add('brain-saved');
    label = `All ${sync.total_messages || 0} session message(s) saved to Turso`;
  } else {
    btn.classList.add('brain-unsaved');
    label = `${sync.synced_messages || 0} of ${sync.total_messages || 0} session messages saved to Turso — retrying`;
  }
  // The brain is also the failure-log trigger, so keep the sync text as the
  // prefix and let the badge/count logic append the failure hint.
  btn.dataset.syncLabel = label;
  btn.setAttribute('aria-label', `${label}. Double-click to open the session failure log.`);
  const badge = document.getElementById('debug-chat-brain-badge');
  const count = badge && !badge.classList.contains('hidden') ? badge.textContent : '';
  btn.title = count
    ? `${label} — ${count} failure(s) this session. Double-click for the failure log.`
    : `${label} — double-click for the session failure log`;
}

let debugChatBrainLastLoggedState = '';

// Fetches the live Turso connectivity/schema diagnostic and logs it to the
// browser console (grouped, so it's easy to spot in devtools) whenever the
// brain indicator is red or unconfigured. This is the "debug on web console"
// path — open devtools and look for "[Syte][turso]" groups to see exactly
// why messages aren't syncing (bad/missing credentials, an unreachable
// database, or a schema statement Turso rejected).
async function logDebugChatTursoDiagnostics(projectId, sync) {
  try {
    const debugInfo = await api(`/projects/${projectId}/agent/turso_debug`);
    // eslint-disable-next-line no-console
    console.groupCollapsed(
      `%c[Syte][turso] brain=${sync && sync.all_saved ? 'green' : 'red'} — sync status for project ${projectId}`,
      'color:#dc2626;font-weight:600;'
    );
    console.log('sync status (GET .../agent/turso_sync):', sync);
    console.log('diagnostics (GET .../agent/turso_debug):', debugInfo);
    if (debugInfo && debugInfo.configured === false) {
      console.warn('Turso is NOT configured — set turso_database_url in Settings -> AI tab.');
    } else if (debugInfo && debugInfo.reachable === false) {
      console.error('Turso is configured but NOT reachable right now:', debugInfo.error || '(no error captured)');
      if (debugInfo.hint) console.warn('hint:', debugInfo.hint);
      if (debugInfo.effective_url && debugInfo.effective_url !== debugInfo.database_url) {
        console.warn('effective_url (after libsql→https rewrite):', debugInfo.effective_url);
      }
      console.warn('database_url:', debugInfo.database_url, '| auth_token_set:', debugInfo.auth_token_set);
    } else if (debugInfo && debugInfo.schema_errors) {
      console.error('Turso is reachable, but schema setup had failing statement(s):', debugInfo.schema_errors);
      console.warn('Messages can still fail to sync until these are resolved (e.g. an index Turso rejected).');
    } else if (sync && !sync.all_saved) {
      console.warn(
        `Turso is reachable and schema is fine, but ${sync.synced_messages ?? '?'} of ` +
        `${sync.total_messages ?? '?'} messages in session ${sync.session ?? '?'} are synced. ` +
        'This usually means a transient write failure — check server logs for ' +
        '"Failed to record Turso agent message" around the time the message was sent.'
      );
    }
    console.groupEnd();
  } catch (err) {
    // eslint-disable-next-line no-console
    console.error('[Syte][turso] Failed to fetch turso_debug diagnostics:', err);
  }
}

async function pollDebugChatBrainOnce(projectId) {
  if (debugChatBrainPollInFlight) return;
  debugChatBrainPollInFlight = true;
  try {
    const res = await api(`/projects/${projectId}/agent/turso_sync`);
    if (res.ok) {
      // Refresh the failure count first so the brain's tooltip can include it.
      await loadDebugChatFailures(projectId, { render: false });
      setDebugChatBrainStatus(res);
      const state = res.turso_configured ? (res.all_saved ? 'green' : 'red') : 'unconfigured';
      // Only re-run (and re-log) the heavier diagnostic call when the
      // brain's state actually changes, so a healthy green connection
      // does not spam the console every 3 seconds.
      if (state !== 'green' && state !== debugChatBrainLastLoggedState) {
        void logDebugChatTursoDiagnostics(projectId, res);
      }
      debugChatBrainLastLoggedState = state;
    }
  } catch (err) {
    // Leave the last known state on transient errors — never claim unsaved
    // just because the status poll itself failed to reach the server.
    // eslint-disable-next-line no-console
    console.warn('[Syte][turso] agent/turso_sync poll failed (leaving last known brain state):', err);
  } finally {
    debugChatBrainPollInFlight = false;
  }
}

function startDebugChatBrainPoll(projectId) {
  stopDebugChatBrainPoll();
  debugChatBrainLastLoggedState = '';
  void pollDebugChatBrainOnce(projectId);
  debugChatBrainPollTimer = setInterval(() => {
    if (activeSvcTab !== 'debug-chat' || activeServiceId !== projectId) {
      stopDebugChatBrainPoll();
      return;
    }
    void pollDebugChatBrainOnce(projectId);
  }, DEBUG_CHAT_BRAIN_POLL_INTERVAL_MS);
}

function stopDebugChatBrainPoll() {
  if (debugChatBrainPollTimer) {
    clearInterval(debugChatBrainPollTimer);
    debugChatBrainPollTimer = null;
  }
  debugChatBrainPollInFlight = false;
}

// ---------------------------------------------------------------------------
// Session failure log — every failed task, tool, request and subagent for the
// session, opened by DOUBLE-CLICKING the brain icon. Activity events are pruned
// and replay-window limited, so this failure-only view is the reliable answer to
// "what actually went wrong?". Backed by GET/DELETE .../agent/failures.
// ---------------------------------------------------------------------------

const FAILURE_KIND_LABEL = {
  request: 'request',
  subagent: 'subagent',
  tool: 'tool',
  provider: 'provider',
  session: 'session',
  preview: 'preview',
  design: 'design',
};

function debugChatFailureScope() {
  return document.getElementById('debug-chat-failures-scope')?.value || 'last';
}

function formatFailureTime(iso) {
  if (!iso) return '';
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return String(iso);
  return date.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function renderDebugChatFailures(payload) {
  const body = document.getElementById('debug-chat-failures-body');
  if (!body) return;
  const failures = payload?.failures || [];
  const summary = payload?.summary || {};
  body.innerHTML = '';

  const subtitle = document.getElementById('debug-chat-failures-subtitle');
  if (subtitle) {
    subtitle.textContent = failures.length
      ? `${summary.total || failures.length} failure(s) — newest first`
      : 'No failures recorded for this scope.';
  }

  if (!failures.length) {
    const empty = document.createElement('div');
    empty.className = 'debug-chat-resource-empty';
    empty.textContent = 'Nothing failed here. Failed tools, requests and subagents show up automatically.';
    body.appendChild(empty);
    return;
  }

  const kinds = Object.entries(summary.by_kind || {});
  if (kinds.length) {
    const chips = document.createElement('div');
    chips.className = 'debug-chat-failure-summary';
    for (const [kind, count] of kinds.sort((a, b) => b[1] - a[1])) {
      const chip = document.createElement('span');
      chip.textContent = `${FAILURE_KIND_LABEL[kind] || kind}: ${count}`;
      chips.appendChild(chip);
    }
    body.appendChild(chips);
  }

  for (const failure of failures) {
    const row = document.createElement('div');
    row.className = `debug-chat-failure-row kind-${failure.kind || 'tool'}`;

    const head = document.createElement('div');
    head.className = 'debug-chat-failure-head';

    const kind = document.createElement('span');
    kind.className = 'debug-chat-failure-kind';
    kind.textContent = FAILURE_KIND_LABEL[failure.kind] || failure.kind || 'error';
    head.appendChild(kind);

    if (failure.agent === 'subagent') {
      const lane = document.createElement('span');
      lane.className = 'debug-chat-failure-lane';
      lane.textContent = failure.subagent_task_id
        ? `subagent ${failure.subagent_task_id}`
        : 'subagent';
      head.appendChild(lane);
    }
    if (failure.tool) {
      const tool = document.createElement('span');
      tool.className = 'debug-chat-failure-tool';
      tool.textContent = failure.tool;
      head.appendChild(tool);
    }
    const error = document.createElement('span');
    error.className = 'debug-chat-failure-error';
    error.textContent = failure.error || 'error';
    head.appendChild(error);

    if (failure.retryable) {
      const retry = document.createElement('span');
      retry.className = 'debug-chat-failure-retry';
      retry.textContent = 'retryable';
      head.appendChild(retry);
    }
    const when = document.createElement('span');
    when.className = 'debug-chat-failure-when';
    when.textContent = [
      failure.session ? `s${failure.session}` : '',
      formatFailureTime(failure.created_at),
    ].filter(Boolean).join(' · ');
    head.appendChild(when);
    row.appendChild(head);

    if (failure.message) {
      const message = document.createElement('div');
      message.className = 'debug-chat-failure-message';
      message.textContent = String(failure.message).slice(0, 2000);
      row.appendChild(message);
    }
    if (failure.target) {
      const target = document.createElement('div');
      target.className = 'debug-chat-failure-target';
      target.textContent = failure.target;
      row.appendChild(target);
    }
    body.appendChild(row);
  }
}

function updateDebugChatFailureBadge(total) {
  const badge = document.getElementById('debug-chat-brain-badge');
  if (!badge) return;
  const count = Number(total) || 0;
  badge.classList.toggle('hidden', count <= 0);
  badge.textContent = count > 99 ? '99+' : String(count);
  const btn = document.getElementById('debug-chat-brain');
  if (btn) {
    const base = btn.dataset.syncLabel || 'Turso save status';
    btn.title = count > 0
      ? `${base} — ${count} failure(s) this session. Double-click for the failure log.`
      : `${base} — double-click for the session failure log`;
  }
}

async function loadDebugChatFailures(projectId, { render = true } = {}) {
  if (!projectId) return null;
  try {
    const scope = render ? debugChatFailureScope() : 'last';
    // Badge-only refreshes ask for a single row: `summary` is computed
    // independently of `limit`, so the count is exact and the payload is tiny.
    const limit = render ? 300 : 1;
    const res = await api(
      `/projects/${projectId}/agent/failures?session=${encodeURIComponent(scope)}&limit=${limit}`,
    );
    if (!res.ok) return null;
    if (render) renderDebugChatFailures(res);
    // The badge always reflects the current session, never the "all" scope.
    if (scope === 'last') updateDebugChatFailureBadge(res.summary?.total || 0);
    return res;
  } catch (err) {
    if (render) {
      const body = document.getElementById('debug-chat-failures-body');
      if (body) {
        body.innerHTML = '';
        const error = document.createElement('div');
        error.className = 'debug-chat-resource-empty';
        error.textContent = normalizeFetchError(err.message);
        body.appendChild(error);
      }
    }
    return null;
  }
}

function closeDebugChatFailureLog() {
  document.getElementById('debug-chat-failures')?.classList.add('hidden');
}

async function openDebugChatFailureLog() {
  const panel = document.getElementById('debug-chat-failures');
  if (!panel || !activeServiceId) return;
  // The failure log and the MCP/skills resources panel share the same slot.
  document.getElementById('debug-chat-resources')?.classList.add('hidden');
  panel.classList.remove('hidden');
  const body = document.getElementById('debug-chat-failures-body');
  if (body && !body.childElementCount) {
    body.innerHTML = '<div class="debug-chat-resource-loading">Loading failure log…</div>';
  }
  await loadDebugChatFailures(activeServiceId);
}

function toggleDebugChatFailureLog() {
  const panel = document.getElementById('debug-chat-failures');
  if (!panel) return;
  if (panel.classList.contains('hidden')) void openDebugChatFailureLog();
  else closeDebugChatFailureLog();
}

// ---------------------------------------------------------------------------
// Durable subagent roster. The subagent tab used to be revealed only by
// replayed activity events, so a subagent whose events aged out of the replay
// window became invisible even though it ran. This reads the durable task list
// (GET .../agent/subagents) so the tab and its history survive a reload.
// ---------------------------------------------------------------------------

function renderDebugChatSubagentRoster(tasks) {
  const laneEl = getDebugChatMessagesEl('sub');
  if (!laneEl) return;
  document.getElementById('debug-chat-subagent-roster')?.remove();
  if (!tasks?.length) return;

  const roster = document.createElement('div');
  roster.className = 'debug-chat-subagent-roster';
  roster.id = 'debug-chat-subagent-roster';

  const head = document.createElement('div');
  head.className = 'debug-chat-subagent-roster-head';
  head.textContent = `Delegated tasks (${tasks.length})`;
  roster.appendChild(head);

  for (const task of tasks.slice(0, 12)) {
    const row = document.createElement('div');
    row.className = 'debug-chat-subagent-task';

    const rowHead = document.createElement('div');
    rowHead.className = 'debug-chat-subagent-task-head';
    const status = document.createElement('span');
    status.className = `debug-chat-subagent-status status-${task.status || 'running'}`;
    status.textContent = task.status || 'running';
    rowHead.appendChild(status);
    const id = document.createElement('span');
    id.className = 'debug-chat-subagent-mode';
    id.textContent = `${task.task_id} · ${task.mode || 'research'}${task.background ? ' · bg' : ''}`;
    rowHead.appendChild(id);
    if (task.error) {
      const error = document.createElement('span');
      error.className = 'debug-chat-failure-error';
      error.textContent = task.error;
      rowHead.appendChild(error);
    }
    row.appendChild(rowHead);

    const text = document.createElement('div');
    text.className = 'debug-chat-subagent-task-text';
    text.textContent = String(task.task || '').slice(0, 400);
    row.appendChild(text);

    if (Array.isArray(task.files) && task.files.length) {
      const files = document.createElement('div');
      files.className = 'debug-chat-subagent-task-files';
      files.textContent = task.files.join(', ');
      row.appendChild(files);
    }
    roster.appendChild(row);
  }
  laneEl.prepend(roster);
}

let debugChatSubagentRefreshTimer = null;
let debugChatFailureRefreshTimer = null;

// Subagent lifecycle events arrive in bursts (scope → started → tools → done);
// coalesce them into one roster refresh.
function scheduleDebugChatSubagentRefresh() {
  if (debugChatSubagentRefreshTimer) return;
  debugChatSubagentRefreshTimer = setTimeout(() => {
    debugChatSubagentRefreshTimer = null;
    void loadDebugChatSubagents(activeServiceId);
  }, 1200);
}

function debugChatIsFailureEvent(event) {
  const type = String(event?.event_type || '');
  if (type === 'request_failed' || type === 'subagent_failed' || type === 'tool_error') return true;
  if (type === 'tool_call_finished') return event?.payload?.ok === false;
  return false;
}

// Keep the brain badge (and an open failure panel) current without polling the
// failure endpoint on every frame.
function scheduleDebugChatFailureRefresh() {
  if (debugChatFailureRefreshTimer) return;
  debugChatFailureRefreshTimer = setTimeout(() => {
    debugChatFailureRefreshTimer = null;
    const panelOpen = !document.getElementById('debug-chat-failures')?.classList.contains('hidden');
    void loadDebugChatFailures(activeServiceId, { render: panelOpen });
  }, 1500);
}

async function loadDebugChatSubagents(projectId) {
  if (!projectId) return null;
  try {
    const res = await api(`/projects/${projectId}/agent/subagents?session=last&limit=50`);
    if (!res.ok) return null;
    const tasks = res.subagents || [];
    if (tasks.length) {
      revealDebugChatSubagentTab();
      hideDebugChatEmpty('sub');
      renderDebugChatSubagentRoster(tasks);
      updateDebugChatSubagentBadge();
    }
    return res;
  } catch (_) {
    return null;
  }
}

function setDebugChatConnectionState(state) {
  debugChatConnectionState = state;
  const dot = document.getElementById('debug-chat-live');
  const meta = {
    connected: 'Activity stream connected',
    connecting: 'Activity stream connecting',
    reconnecting: 'Activity stream reconnecting',
    disconnected: 'Activity stream disconnected',
  };
  const ariaLabel = meta[state] || meta.disconnected;
  if (dot) {
    dot.classList.toggle('live', state === 'connected');
    dot.classList.toggle('connecting', state === 'connecting' || state === 'reconnecting');
    dot.setAttribute('aria-label', ariaLabel);
  }
  if (!debugChatBusy && !debugChatSendInFlight) {
    if (state === 'connecting' || state === 'reconnecting') {
      setDebugChatActivity(state === 'connecting' ? 'Connecting…' : 'Reconnecting…');
    } else if (state === 'connected') {
      setDebugChatActivity(debugChatIdleStatus);
    }
  }
}

// Lane of the event currently being rendered. Every append/stream helper writes
// into this lane, so main and subagent feeds never interleave.
function debugChatLaneForEvent(event) {
  const payload = event?.payload || {};
  if (payload.agent === 'subagent') return 'sub';
  if (!payload.agent && payload.subagent_task_id) return 'sub';
  return 'main';
}

function debugChatLaneId(lane) {
  return lane === 'sub' ? 'debug-chat-messages-sub' : 'debug-chat-messages';
}

function getDebugChatMessagesEl(lane = debugChatRenderLane) {
  return document.getElementById(debugChatLaneId(lane));
}

function getDebugChatLaneEls() {
  return ['main', 'sub']
    .map(lane => document.getElementById(debugChatLaneId(lane)))
    .filter(Boolean);
}

function hideDebugChatEmpty(lane = debugChatRenderLane) {
  document.getElementById(lane === 'sub' ? 'debug-chat-empty-sub' : 'debug-chat-empty')
    ?.classList.add('hidden');
}

function showDebugChatEmpty(lane = debugChatRenderLane) {
  const empty = document.getElementById(
    lane === 'sub' ? 'debug-chat-empty-sub' : 'debug-chat-empty',
  );
  if (empty) empty.classList.remove('hidden');
}

function setDebugChatLane(lane) {
  const next = lane === 'sub' ? 'sub' : 'main';
  debugChatActiveLane = next;
  for (const name of ['main', 'sub']) {
    const panel = document.getElementById(debugChatLaneId(name));
    const tab = document.getElementById(name === 'sub' ? 'debug-chat-tab-sub' : 'debug-chat-tab-main');
    if (panel) panel.classList.toggle('hidden', name !== next);
    if (tab) {
      tab.classList.toggle('is-active', name === next);
      tab.setAttribute('aria-selected', name === next ? 'true' : 'false');
    }
  }
  if (next === 'sub') {
    debugChatSubagentUnread = 0;
    updateDebugChatSubagentBadge();
  }
  scrollDebugChatToBottom(true, next);
}

function updateDebugChatSubagentBadge() {
  const badge = document.getElementById('debug-chat-tab-sub-badge');
  if (!badge) return;
  const show = debugChatSubagentUnread > 0 && debugChatActiveLane !== 'sub';
  badge.classList.toggle('hidden', !show);
  badge.textContent = String(Math.min(debugChatSubagentUnread, 99));
}

// The subagent tab only exists once work has actually been delegated.
function revealDebugChatSubagentTab() {
  const tab = document.getElementById('debug-chat-tab-sub');
  if (!tab) return;
  debugChatSubagentSeen = true;
  tab.classList.remove('hidden');
}

function hideDebugChatSubagentTab() {
  const tab = document.getElementById('debug-chat-tab-sub');
  debugChatSubagentSeen = false;
  debugChatSubagentUnread = 0;
  updateDebugChatSubagentBadge();
  if (tab) tab.classList.add('hidden');
  if (debugChatActiveLane === 'sub') setDebugChatLane('main');
}

function updateDebugChatScrollState(lane) {
  const name = lane || debugChatActiveLane;
  const el = getDebugChatMessagesEl(name);
  if (!el) return;
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  debugChatAutoScrollByLane[name] = distanceFromBottom < 72;
  if (name === 'main') debugChatAutoScroll = debugChatAutoScrollByLane.main;
}

function scrollDebugChatToBottom(force = false, lane = debugChatRenderLane) {
  const el = getDebugChatMessagesEl(lane);
  if (!el) return;
  if (!force && !debugChatAutoScrollByLane[lane]) return;
  el.scrollTop = el.scrollHeight;
  if (force) {
    debugChatAutoScrollByLane[lane] = true;
    if (lane === 'main') debugChatAutoScroll = true;
  }
}

function setDebugChatActivity(label, detail = '', icon = '', active = true) {
  const bar = document.getElementById('debug-chat-activity');
  if (!bar) return;
  if (debugChatActivityDismissTimer) {
    clearTimeout(debugChatActivityDismissTimer);
    debugChatActivityDismissTimer = null;
  }
  bar.classList.remove('hidden');
  const labelEl = bar.querySelector('.debug-chat-activity-label');
  const detailEl = bar.querySelector('.debug-chat-activity-detail');
  const iconEl = bar.querySelector('.debug-chat-activity-icon');
  const modelEl = document.getElementById('debug-chat-activity-model');
  const nextLabel = active && label ? label : debugChatIdleStatus;
  const isWorking = /planning|working|writing|sending|connecting|reconnecting|stopping|capturing|waiting|reading|editing|running/i.test(nextLabel);
  const nextIcon = icon || (isWorking ? 'loader' : 'sparkles');
  debugChatActivityLabel = nextLabel;
  if (labelEl) labelEl.textContent = nextLabel;
  if (detailEl) detailEl.textContent = active ? detail : '';
  if (iconEl) {
    iconEl.innerHTML = `<i data-lucide="${esc(active ? nextIcon : 'sparkles')}"></i>`;
    iconEl.classList.toggle('debug-chat-activity-spin', active && nextIcon === 'loader');
  }
  bar.classList.toggle('is-active', Boolean(active && isWorking));
  bar.classList.toggle('is-idle', !(active && isWorking));
  bar.dataset.phase = String(nextLabel || '').toLowerCase().replace(/[^a-z]+/g, '-').replace(/-+$/, '') || 'idle';
  if (modelEl) {
    const profile = document.getElementById('debug-chat-profile')?.value || '';
    const short = ({
      auto: 'auto',
      'syra-nano': 'Go · Gemini 2.5 Flash',
      'syra-ultra': 'Air · Aliyun Qwen',
      'syra-havy': 'Metal · Claude Sonnet 4.6',
    })[profile] || profile;
    if (short && active && isWorking) {
      modelEl.hidden = false;
      modelEl.textContent = short;
    } else if (!isWorking) {
      modelEl.hidden = true;
      modelEl.textContent = '';
    }
  }
  refreshIcons();
}

function dismissDebugChatActivitySoon(delay = 2600) {
  if (debugChatActivityDismissTimer) clearTimeout(debugChatActivityDismissTimer);
  debugChatActivityDismissTimer = setTimeout(() => {
    setDebugChatActivity(debugChatIdleStatus);
  }, delay);
}

function clearDebugChatPanel({ resetCursor = false } = {}) {
  const mainEl = getDebugChatMessagesEl('main');
  if (!mainEl) return;
  if (debugChatStreamFlushFrame) {
    cancelAnimationFrame(debugChatStreamFlushFrame);
    debugChatStreamFlushFrame = null;
  }
  debugChatStreamBuffers.clear();
  if (debugChatThinkingFlushFrame) {
    cancelAnimationFrame(debugChatThinkingFlushFrame);
    debugChatThinkingFlushFrame = null;
  }
  debugChatThinkingBuffers.clear();
  const laneEmptyText = {
    main: 'What would you like to change?',
    sub: 'No subagent has been started yet.',
  };
  for (const lane of ['main', 'sub']) {
    const el = getDebugChatMessagesEl(lane);
    if (!el) continue;
    el.innerHTML = '';
    const empty = document.createElement('div');
    empty.className = 'debug-chat-empty';
    empty.id = lane === 'sub' ? 'debug-chat-empty-sub' : 'debug-chat-empty';
    empty.innerHTML = `<p>${laneEmptyText[lane]}</p>`;
    el.appendChild(empty);
    debugChatAutoScrollByLane[lane] = true;
  }
  debugChatRenderedIds.clear();
  debugChatActionGroups.main = null;
  debugChatActionGroups.sub = null;
  hideDebugChatSubagentTab();
  // Failure panel + badge belong to the project/session being cleared.
  closeDebugChatFailureLog();
  const failuresBody = document.getElementById('debug-chat-failures-body');
  if (failuresBody) failuresBody.innerHTML = '';
  updateDebugChatFailureBadge(0);
  if (resetCursor) {
    debugChatSinceId = 0;
    debugChatTerminalRequestIds.clear();
  }
  debugChatAutoScroll = true;
  setDebugChatTyping(false);
  if (!debugChatBusy) setDebugChatActivity(debugChatIdleStatus);
}

// Open/closed collapsible groups of consecutive tool activity, per lane.
const debugChatActionGroups = { main: null, sub: null };

const DEBUG_CHAT_ACTION_LABELS = {
  file_created: 'Create file',
  file_modified: 'Rewrite file',
  file_deleted: 'Delete file',
  file_read: 'Read file',
  file_search: 'Search',
  command_run: 'Run command',
  command_output: 'Command output',
  tool_call: 'Tool call',
  tool_call_started: 'Tool started',
  tool_call_finished: 'Tool finished',
  file_changed: 'File changed',
  service_action: 'Service',
  request_started: 'Request started',
  request_completed: 'Done',
  processing: 'Working',
  screenshot: 'Screenshot',
  question: 'Question',
  question_answered: 'Answer',
  agent_stopped: 'Stopped',
  session_stopped: 'Stopped',
  subagent_scope: 'Delegated files',
  subagent_started: 'Subagent started',
  subagent_completed: 'Subagent finished',
  subagent_failed: 'Subagent failed',
};

const DEBUG_CHAT_EVENT_ICONS = {
  file_created: 'file-plus-2',
  file_modified: 'file-pen-line',
  file_deleted: 'file-x-2',
  file_read: 'file-search-2',
  file_search: 'search',
  command_run: 'terminal',
  command_output: 'square-terminal',
  file_changed: 'file-check-2',
  service_action: 'wrench',
  request_started: 'message-square',
  tool_call: 'wrench',
  tool_call_started: 'loader',
  tool_call_finished: 'circle-check',
  screenshot: 'monitor-smartphone',
  question: 'circle-help',
  question_answered: 'message-circle',
  agent_stopped: 'square',
  subagent_scope: 'git-fork',
  subagent_started: 'git-fork',
  subagent_completed: 'circle-check',
  subagent_failed: 'circle-alert',
};

const DEBUG_CHAT_TOOL_META = {
  list_files: { label: 'List files', icon: 'folder-search' },
  read_file: { label: 'Read file', icon: 'file-search-2' },
  write_file: { label: 'Write file', icon: 'file-pen-line' },
  delete_file: { label: 'Delete file', icon: 'file-x-2' },
  run_command: { label: 'Run command', icon: 'terminal' },
  service: { label: 'Preview service', icon: 'wrench' },
  update_plan: { label: 'Update plan', icon: 'list-checks' },
  inspect_preview: { label: 'Inspect preview', icon: 'scan-search' },
  screenshot_preview: { label: 'Screenshot preview', icon: 'monitor-smartphone' },
  ask_question: { label: 'Ask question', icon: 'circle-help' },
  env_get: { label: 'Get env', icon: 'key-round' },
  env_set: { label: 'Set env', icon: 'key-round' },
  request_env: { label: 'Request env', icon: 'key-round' },
  list_mcp_addons: { label: 'List MCP', icon: 'plug' },
  connect_mcp: { label: 'Connect MCP', icon: 'plug' },
  call_mcp: { label: 'Call MCP', icon: 'plug' },
  delegate_task: { label: 'Delegate task', icon: 'git-fork' },
  await_subagent: { label: 'Await subagent', icon: 'timer' },
};

function debugChatActionMeta(event) {
  const tool = event.payload?.tool || (
    ['tool_call', 'tool_call_started', 'tool_call_finished'].includes(event.event_type)
      ? event.title
      : ''
  );
  const toolMeta = DEBUG_CHAT_TOOL_META[tool];
  return {
    label: toolMeta?.label || DEBUG_CHAT_ACTION_LABELS[event.event_type] || event.title || event.event_type || 'Action',
    icon: toolMeta?.icon || DEBUG_CHAT_EVENT_ICONS[event.event_type] || 'wrench',
  };
}

// --- Collapsed tool activity -------------------------------------------------
// A single openable row ("Created 1 file") replaces the long per-command lines.
// Consecutive activity of the same kind is folded into one row whose body lists
// every entry, so the transcript stays readable during heavy tool use.
const DEBUG_CHAT_ACTION_CATEGORIES = {
  write: { verb: 'Created', noun: ['file', 'files'], icon: 'file-plus-2' },
  edit: { verb: 'Edited', noun: ['file', 'files'], icon: 'file-pen-line' },
  delete: { verb: 'Deleted', noun: ['file', 'files'], icon: 'file-x-2' },
  read: { verb: 'Read', noun: ['file', 'files'], icon: 'file-search-2' },
  search: { verb: 'Searched', noun: ['time', 'times'], icon: 'search' },
  command: { verb: 'Ran', noun: ['command', 'commands'], icon: 'terminal' },
};

const DEBUG_CHAT_TOOL_CATEGORY = {
  write_file: 'write',
  delete_file: 'delete',
  read_file: 'read',
  list_files: 'read',
  search_code: 'search',
  semantic_search: 'search',
  web_search: 'search',
  run_command: 'command',
};

function debugChatActionCategory(event) {
  const type = event.event_type;
  const tool = event.payload?.tool || '';
  if (type === 'file_created') return 'write';
  if (type === 'file_modified' || type === 'file_changed') return 'edit';
  if (type === 'file_deleted') return 'delete';
  if (type === 'file_read') return 'read';
  if (type === 'file_search') return 'search';
  if (type === 'command_run' || type === 'command_output') return 'command';
  if (tool && DEBUG_CHAT_TOOL_CATEGORY[tool]) {
    // write_file on an existing path reports ok + "updated"; keep it as write/edit.
    return DEBUG_CHAT_TOOL_CATEGORY[tool];
  }
  return '';
}

function debugChatActionGroupKey(event) {
  const category = debugChatActionCategory(event);
  if (category) return `cat:${category}`;
  const tool = event.payload?.tool || '';
  if (tool) return `tool:${tool}`;
  return `type:${event.event_type}`;
}

function debugChatActionTarget(event) {
  const payload = event.payload || {};
  const args = payload.arguments && typeof payload.arguments === 'object' ? payload.arguments : {};
  const candidates = [
    payload.path, args.path, payload.command, args.command,
    payload.route, args.route, args.query, args.pattern, args.task, args.addon,
    Array.isArray(payload.files) ? payload.files[0] : '', payload.task,
  ];
  for (const candidate of candidates) {
    const text = coerceDebugChatText(candidate).trim();
    if (text) return text.replace(/\s+/g, ' ').slice(0, 160);
  }
  return '';
}

function debugChatActionLineText(event) {
  const target = debugChatActionTarget(event);
  // Keep newlines (the body is pre-wrap) but drop trailing padding.
  const detail = String(debugChatDetailText(event) || '')
    .replace(/[ \t]+/g, ' ')
    .trim()
    .slice(0, 1200);
  const label = debugChatActionTitle(event);
  const head = target ? `${label} · ${target}` : label;
  if (!detail || detail === target) return head;
  // Multi-line details (file lists, command output) are already self-describing.
  if (detail.includes('\n')) return `${label}\n${detail}`;
  return `${head}\n${detail}`;
}

function debugChatActionSummaryText(groupKey, count, firstTarget, event) {
  if (groupKey.startsWith('cat:')) {
    const meta = DEBUG_CHAT_ACTION_CATEGORIES[groupKey.slice(4)];
    if (meta) {
      const noun = meta.noun[count === 1 ? 0 : 1];
      return `${meta.verb} ${count} ${noun}`;
    }
  }
  if (groupKey.startsWith('tool:')) {
    const tool = groupKey.slice(5);
    const label = DEBUG_CHAT_TOOL_META[tool]?.label || tool;
    return count > 1 ? `${label} ×${count}` : label;
  }
  if (event?.event_type === 'subagent_scope') {
    const files = Array.isArray(event.payload?.files) ? event.payload.files : [];
    return files.length
      ? `Delegated ${files.length} ${files.length === 1 ? 'file' : 'files'} to subagent`
      : 'Delegated a research task to subagent';
  }
  const label = DEBUG_CHAT_ACTION_LABELS[event?.event_type];
  if (label) return count > 1 ? `${label} ×${count}` : label;
  return firstTarget || groupKey.replace(/^type:/, '').replace(/_/g, ' ');
}

function debugChatActionGroupIcon(groupKey, event) {
  if (groupKey.startsWith('cat:')) {
    const meta = DEBUG_CHAT_ACTION_CATEGORIES[groupKey.slice(4)];
    if (meta) return meta.icon;
  }
  return debugChatActionMeta(event).icon;
}

function appendDebugChatActionRow(event, messagesEl, lane) {
  const groupKey = debugChatActionGroupKey(event);
  const target = debugChatActionTarget(event);
  const failed = event.payload?.ok === false;
  const existing = debugChatActionGroups[lane];

  if (existing && existing.key === groupKey && existing.card.isConnected) {
    existing.count += 1;
    existing.targets.push(target);
    existing.titleEl.textContent = debugChatActionSummaryText(
      groupKey, existing.count, existing.targets[0], event,
    );
    existing.hintEl.textContent = existing.targets[0]
      ? `${existing.targets[0]}${existing.count > 1 ? ` +${existing.count - 1} more` : ''}`
      : '';
    existing.countEl.textContent = String(existing.count);
    existing.countEl.classList.toggle('hidden', existing.count < 2);
    const line = document.createElement('span');
    line.className = `debug-chat-action-line${failed ? ' debug-chat-action-line-failed' : ''}`;
    line.textContent = debugChatActionLineText(event);
    existing.bodyEl.appendChild(line);
    if (event.id != null) existing.card.dataset.lastEventId = String(event.id);
    scrollDebugChatToBottom(false, lane);
    return;
  }

  const bubble = document.createElement('div');
  bubble.className = `debug-chat-bubble debug-chat-action${
    event.event_type.startsWith('subagent') ? ' debug-chat-subagent-scope' : ''
  }`;
  if (event.id != null) bubble.dataset.eventId = String(event.id);
  const icon = debugChatActionGroupIcon(groupKey, event);
  const title = debugChatActionSummaryText(groupKey, 1, target, event);
  bubble.innerHTML = `
    <div class="debug-chat-action-card">
      <button type="button" class="debug-chat-action-summary" aria-expanded="false">
        <i data-lucide="${esc(icon)}" aria-hidden="true"></i>
        <span class="debug-chat-action-title">${esc(title)}</span>
        <span class="debug-chat-action-hint">${esc(target)}</span>
        <b class="debug-chat-action-count hidden">1</b>
        <i data-lucide="chevron-right" class="debug-chat-action-chevron" aria-hidden="true"></i>
      </button>
      <div class="debug-chat-action-body"></div>
    </div>
  `;
  const card = bubble.querySelector('.debug-chat-action-card');
  const summary = bubble.querySelector('.debug-chat-action-summary');
  const bodyEl = bubble.querySelector('.debug-chat-action-body');
  const line = document.createElement('span');
  line.className = `debug-chat-action-line${failed ? ' debug-chat-action-line-failed' : ''}`;
  line.textContent = debugChatActionLineText(event);
  bodyEl.appendChild(line);
  summary.addEventListener('click', () => {
    const open = card.classList.toggle('is-open');
    summary.setAttribute('aria-expanded', open ? 'true' : 'false');
  });
  messagesEl.appendChild(bubble);
  debugChatActionGroups[lane] = {
    key: groupKey,
    card,
    bodyEl,
    titleEl: bubble.querySelector('.debug-chat-action-title'),
    hintEl: bubble.querySelector('.debug-chat-action-hint'),
    countEl: bubble.querySelector('.debug-chat-action-count'),
    count: 1,
    targets: [target],
  };
  if (!debugChatReplayingHistory) refreshIcons(bubble);
  scrollDebugChatToBottom(false, lane);
}

function debugChatRoleForEvent(event) {
  const type = event.event_type;
  if (type === 'user_message') return 'user';
  if (type === 'assistant_message' || type === 'request_completed' || type === 'message_snapshot') return 'assistant';
  if (type === 'token_delta') return 'stream';
  if (type === 'request_failed') return 'error';
  if (type === 'thinking' || type === 'thinking_delta') return 'thinking';
  if (type === 'usage') return 'usage';
  if (type === 'screenshot') return 'screenshot';
  if (type === 'question') return 'question';
  if (type === 'question_answered') return 'user';
  if (type === 'processing') return 'processing';
  if ([
    'file_created', 'file_modified', 'file_deleted', 'file_read', 'file_search',
    'file_changed', 'tool_call', 'tool_call_started', 'tool_call_finished',
    'command_run', 'command_output', 'service_action', 'request_started',
    'agent_stopped', 'subagent_scope',
  ].includes(type)) {
    return 'action';
  }
  return 'system';
}

function debugChatActionTitle(event) {
  if (event.event_type === 'request_completed') return 'Assistant';
  if (event.event_type === 'request_started') return 'Request';
  return debugChatActionMeta(event).label;
}

function setDebugChatTyping(show) {
  document.getElementById('debug-chat-typing')?.remove();
  if (
    show
    && !debugChatReplayingHistory
    && !['Planning…', 'Working…', 'Writing…'].includes(debugChatActivityLabel)
  ) {
    setDebugChatActivity('Planning…', 'Thinking before taking action');
  }
}

function ensureStreamingAssistantBubble(requestId) {
  const rid = requestId || 'pending';
  // Token streaming always belongs to the main agent lane.
  const messagesEl = getDebugChatMessagesEl('main');
  if (!messagesEl) return null;
  const existing = document.getElementById(`debug-chat-stream-${rid}`);
  if (existing) return existing.querySelector('.debug-chat-bubble-body');

  hideDebugChatEmpty('main');
  debugChatActionGroups.main = null;
  setDebugChatTyping(false);
  const bubble = document.createElement('div');
  bubble.className = 'debug-chat-bubble debug-chat-assistant debug-chat-streaming';
  bubble.id = `debug-chat-stream-${rid}`;
  bubble.dataset.requestId = rid;
  bubble.innerHTML = `
    <div class="debug-chat-bubble-head">
      <span>Agent</span>
    </div>
    <div class="debug-chat-bubble-body"></div>
  `;
  messagesEl.appendChild(bubble);
  scrollDebugChatToBottom(false, 'main');
  return bubble.querySelector('.debug-chat-bubble-body');
}

// Human label for where a thinking block happened, from the backend payload
// (step / phase / targeted tool+file) — see _thinking_context in cloud_agent.py.
function debugChatThinkingWhere(event) {
  const payload = event?.payload || {};
  const explicit = coerceDebugChatText(payload.thinking_where).trim();
  if (explicit) return explicit;
  const bits = [];
  if (payload.step) bits.push(`step ${payload.step}`);
  const targets = Array.isArray(payload.thinking_targets) ? payload.thinking_targets : [];
  const tools = Array.isArray(payload.thinking_tools) ? payload.thinking_tools : [];
  if (targets.length) bits.push(String(targets[0]));
  else if (tools.length) bits.push(tools.slice(0, 3).join(', '));
  else if (payload.phase) bits.push(String(payload.phase).replace(/_/g, ' '));
  if (payload.subagent_task_id) bits.push(`task ${payload.subagent_task_id}`);
  return bits.join(' · ');
}

function setDebugChatThinkingWhere(bubble, event) {
  if (!bubble) return;
  const where = debugChatThinkingWhere(event);
  if (!where) return;
  const head = bubble.querySelector('.debug-chat-bubble-head');
  if (!head) return;
  let label = head.querySelector('.debug-chat-thinking-where');
  if (!label) {
    label = document.createElement('span');
    label.className = 'debug-chat-thinking-where';
    head.appendChild(label);
  }
  label.textContent = where;
}

function ensureStreamingThinkingBubble(requestId) {
  const rid = requestId || 'pending';
  // Streamed reasoning is the main agent's — subagent thinking arrives as
  // complete `thinking` events and renders in the subagent lane.
  const messagesEl = getDebugChatMessagesEl('main');
  if (!messagesEl) return null;
  const existing = document.getElementById(`debug-chat-thinking-${rid}`);
  if (existing) return existing.querySelector('.debug-chat-bubble-body');

  hideDebugChatEmpty('main');
  debugChatActionGroups.main = null;
  const bubble = document.createElement('div');
  bubble.className = 'debug-chat-bubble debug-chat-thinking debug-chat-streaming';
  bubble.id = `debug-chat-thinking-${rid}`;
  bubble.dataset.requestId = rid;
  bubble.innerHTML = `
    <div class="debug-chat-bubble-head">
      <span>Thinking</span>
    </div>
    <div class="debug-chat-bubble-body debug-chat-thinking"></div>
  `;
  messagesEl.appendChild(bubble);
  scrollDebugChatToBottom(false, 'main');
  return bubble.querySelector('.debug-chat-bubble-body');
}

function flushDebugChatThinkingBuffers() {
  debugChatThinkingFlushFrame = null;
  for (const [rid, text] of debugChatThinkingBuffers.entries()) {
    const bodyEl = ensureStreamingThinkingBubble(rid);
    if (bodyEl) bodyEl.textContent = text;
  }
  // Streaming bubbles always live in the main lane — never scroll whichever
  // lane happened to render last (a subagent event can land between frames).
  scrollDebugChatToBottom(false, 'main');
}

function queueDebugChatThinkingDelta(requestId, delta, snapshot) {
  const rid = requestId || 'pending';
  const snap = coerceDebugChatText(snapshot);
  const piece = coerceDebugChatText(delta);
  const next = snap || ((debugChatThinkingBuffers.get(rid) || '') + piece);
  debugChatThinkingBuffers.set(rid, next);
  if (!debugChatThinkingFlushFrame) {
    debugChatThinkingFlushFrame = requestAnimationFrame(flushDebugChatThinkingBuffers);
  }
}

function finalizeDebugChatThinking(requestId, finalText = '') {
  const rid = requestId || 'pending';
  if (debugChatThinkingFlushFrame) {
    cancelAnimationFrame(debugChatThinkingFlushFrame);
    debugChatThinkingFlushFrame = null;
  }
  const bufferedText = debugChatThinkingBuffers.get(rid) || '';
  debugChatThinkingBuffers.delete(rid);
  const text = coerceDebugChatText(finalText) || bufferedText;
  let bubble = document.getElementById(`debug-chat-thinking-${rid}`);
  const bodyEl = bubble?.querySelector('.debug-chat-bubble-body')
    || (text ? ensureStreamingThinkingBubble(rid) : null);
  if (bodyEl && text) bodyEl.textContent = text;
  bubble = document.getElementById(`debug-chat-thinking-${rid}`);
  if (bubble) {
    bubble.classList.remove('debug-chat-streaming');
    bubble.classList.add('debug-chat-thinking');
    setDebugChatThinkingWhere(bubble, { payload: { thinking_where: text } });
  }
}

function queueDebugChatStreamDelta(requestId, delta) {
  const rid = requestId || 'pending';
  const prev = debugChatStreamBuffers.get(rid) || '';
  debugChatStreamBuffers.set(rid, prev + coerceDebugChatText(delta));
  if (!debugChatStreamFlushFrame) {
    debugChatStreamFlushFrame = requestAnimationFrame(flushDebugChatStreamBuffers);
  }
}

function flushDebugChatStreamBuffers() {
  debugChatStreamFlushFrame = null;
  for (const [rid, text] of debugChatStreamBuffers.entries()) {
    const body = ensureStreamingAssistantBubble(rid);
    if (body) body.textContent = text;
  }
  scrollDebugChatToBottom(false, 'main');
}

function finalizeDebugChatStream(requestId, finalText = '') {
  const rid = requestId || 'pending';
  if (debugChatStreamFlushFrame) {
    cancelAnimationFrame(debugChatStreamFlushFrame);
    debugChatStreamFlushFrame = null;
  }
  const bufferedText = debugChatStreamBuffers.get(rid) || '';
  debugChatStreamBuffers.delete(rid);
  const body = ensureStreamingAssistantBubble(rid);
  if (body) body.textContent = coerceDebugChatText(finalText) || bufferedText;
}

function renderServerSwarm(sys) {
  const running = projects.filter(p => p.running).length;
  const set = (id, val) => {
    const el = document.getElementById(id);
    if (el) el.textContent = val ?? '—';
  };
  if (sys) {
    set('swarm-ip', sys.public_ip);
    set('swarm-version', 'v' + sys.version);
    set('swarm-gui-url', sys.domain_url || 'not configured');
    set('swarm-direct-url', sys.direct_url);
  }
  set('swarm-total', projects.length);
  set('swarm-running', running);
}

function renderResourceMonitor(resourceMonitor) {
  const grid = document.getElementById('swarm-stats');
  if (!grid) return;
  grid.querySelectorAll('[data-resource-monitor="1"]').forEach(node => node.remove());
  const services = Array.isArray(resourceMonitor?.services) ? resourceMonitor.services : [];
  if (!services.length) return;

  for (const service of services) {
    const label = service.label || service.name || service.service_type || 'Service';
    const cpu = Number(service.cpu_percent || 0);
    const memory = Number(service.memory_mb || 0);
    const instances = Number(service.instances || 0);
    const children = Array.isArray(service.children) ? service.children.filter(Boolean).slice(0, 4) : [];
    const card = document.createElement('div');
    card.className = 'swarm-stat full';
    card.dataset.resourceMonitor = '1';
    card.innerHTML = `
      <span class="swarm-label">${esc(label)}</span>
      <span class="swarm-value">${cpu.toFixed(1)}% CPU · ${memory.toFixed(1)} MB RAM</span>
      <span class="hint">${children.length ? esc(children.join(', ')) : `${instances} process${instances === 1 ? '' : 'es'}`}</span>
    `;
    grid.appendChild(card);
  }
}

function renderLogsList() {
  const list = document.getElementById('logs-project-list');
  const empty = document.getElementById('logs-empty');
  if (!list) return;
  list.innerHTML = '';
  const items = filteredProjects();
  if (!items.length) {
    empty?.classList.remove('hidden');
    return;
  }
  empty?.classList.add('hidden');
  for (const p of items) {
    const row = document.createElement('button');
    row.className = 'logs-row';
    row.type = 'button';
    row.innerHTML = `<span>${esc(p.name || p.id)}</span><i data-lucide="chevron-right"></i>`;
    row.onclick = () => openProject(p.id);
    list.appendChild(row);
  }
  refreshIcons(list);
}

function startStatsPoll() {
  if (statsPollTimer) clearInterval(statsPollTimer);
  statsPollTimer = setInterval(loadSystem, 10000);
}

function updateStats() {
  const running = projects.filter(p => p.running).length;
  const swarmTotal = document.getElementById('swarm-total');
  const swarmRunning = document.getElementById('swarm-running');
  if (swarmTotal) swarmTotal.textContent = projects.length;
  if (swarmRunning) swarmRunning.textContent = running;
}

function filteredProjects() {
  let list = [...projects];
  const q = projectFilterText.trim().toLowerCase();
  if (q) {
    list = list.filter(p => `${p.name || ''} ${p.id || ''} ${p.domain || ''}`.toLowerCase().includes(q));
  }
  if (projectSortMode === 'running') list.sort((a, b) => Number(b.running) - Number(a.running));
  else if (projectSortMode === 'name') list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  else list.sort((a, b) => (b.created_at || '').localeCompare(a.created_at || ''));
  return list;
}

async function loadSystem() {
  try {
    const sys = await api('/system');
    if (sys.public_ip) serverPublicIp = sys.public_ip;
    const ipInput = document.getElementById('set-ip');
    if (ipInput && !ipInput.value) ipInput.placeholder = sys.public_ip;
    const directUrl = document.getElementById('direct-url');
    if (directUrl) directUrl.textContent = sys.direct_url;
    const guiUrl = document.getElementById('gui-url');
    if (guiUrl) guiUrl.textContent = sys.domain_url || 'not configured';
    const ver = document.getElementById('syte-version');
    if (ver) ver.textContent = 'v' + sys.version;
    renderServerSwarm(sys);
    renderResourceMonitor(sys.resource_monitor);
    renderLoadStats(sys);
    if (activeServiceId) {
      const p = projects.find(x => x.id === activeServiceId);
      if (p) {
        const conn = document.getElementById('svc-conn');
        if (conn && p.domain) conn.textContent = p.domain;
      }
    }
  } catch (e) {
    // ignore
  }
}

function showView(name) {
  // ... existing code continues ...
}

// ... the rest of the file remains unchanged ...
