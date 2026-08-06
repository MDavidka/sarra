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
  return localStorage.getItem(API_KEY_STORAGE) || '';
}

function shouldAttachApiKey(path) {
  const key = getApiKey();
  if (!key) return false;
  // GUI routes are public on same-origin — a stale/revoked stored token breaks SSE and history.
  if (typeof window !== 'undefined' && window.location?.origin) {
    const guiPrefixes = [
      '/projects/',
      '/agent_dashboard',
      '/settings',
      '/system',
      '/tokens',
      '/operator/',
    ];
    if (guiPrefixes.some(prefix => path.startsWith(prefix))) return false;
  }
  return true;
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
  if (key) localStorage.setItem(API_KEY_STORAGE, key);
  else localStorage.removeItem(API_KEY_STORAGE);
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
  if (bubble) bubble.classList.remove('debug-chat-streaming');
  scrollDebugChatToBottom(false, 'main');
}

function flushDebugChatStreamBuffers() {
  debugChatStreamFlushFrame = null;
  for (const [rid, text] of debugChatStreamBuffers.entries()) {
    const bodyEl = ensureStreamingAssistantBubble(rid);
    if (bodyEl) bodyEl.textContent = text;
  }
  scrollDebugChatToBottom(false, 'main');
}

function coerceDebugChatText(value) {
  if (value == null || value === '') return '';
  if (typeof value === 'string') return value;
  if (typeof value === 'number' || typeof value === 'boolean') return String(value);
  try {
    return JSON.stringify(value);
  } catch (_) {
    return String(value);
  }
}

function queueDebugChatStreamDelta(requestId, delta, snapshot) {
  const rid = requestId || 'pending';
  const snap = coerceDebugChatText(snapshot);
  const piece = coerceDebugChatText(delta);
  const next = snap || ((debugChatStreamBuffers.get(rid) || '') + piece);
  debugChatStreamBuffers.set(rid, next);
  if (!debugChatStreamFlushFrame) {
    debugChatStreamFlushFrame = requestAnimationFrame(flushDebugChatStreamBuffers);
  }
}

function finalizeDebugChatStream(requestId, finalText = '') {
  const rid = requestId || 'pending';
  if (debugChatStreamFlushFrame) {
    cancelAnimationFrame(debugChatStreamFlushFrame);
    debugChatStreamFlushFrame = null;
  }
  const bufferedText = debugChatStreamBuffers.get(rid) || '';
  debugChatStreamBuffers.delete(rid);
  const text = coerceDebugChatText(finalText) || bufferedText;
  let bubble = document.getElementById(`debug-chat-stream-${rid}`);
  const bodyEl = bubble?.querySelector('.debug-chat-bubble-body')
    || (text ? ensureStreamingAssistantBubble(rid) : null);
  if (bodyEl && text) bodyEl.textContent = text;
  bubble = document.getElementById(`debug-chat-stream-${rid}`);
  if (bubble) bubble.classList.remove('debug-chat-streaming');
  scrollDebugChatToBottom(false, 'main');
}

// Single place that ends a turn in the UI: closes the streaming bubble, clears
// the watchdog, unlocks the composer and reports the outcome. Every terminal
// path (live event, duplicate delivery, stop, watchdog) funnels through here so
// the composer can never stay locked on a request that already finished.
function releaseDebugChatTurn(event, requestId, { finalizeStream = true } = {}) {
  const eventType = String(event?.event_type || '');
  const wasStopping = debugChatStopping
    || eventType === 'agent_stopped'
    || debugChatStoppedRequestIds.has(requestId || debugChatActiveRequestId);
  if (finalizeStream) {
    const finalText = eventType === 'request_completed' ? debugChatDetailText(event) : '';
    finalizeDebugChatStream(requestId || debugChatActiveRequestId, finalText);
  }
  setDebugChatTyping(false);
  clearDebugChatRequestWatchdog();
  setDebugChatBusy(false);
  debugChatActiveRequestId = '';
  setDebugChatActivity(
    eventType === 'request_completed'
      ? 'Response ready'
      : (wasStopping || eventType === 'agent_stopped'
        ? 'Response stopped'
        : 'Response failed'),
    '',
    eventType === 'request_completed' ? 'check-circle-2' : 'circle-alert',
  );
  dismissDebugChatActivitySoon();
  void updateDebugChatAgentStatus();
}

function finalizeAllDebugChatStreams() {
  for (const [requestId, text] of [...debugChatStreamBuffers.entries()]) {
    finalizeDebugChatStream(requestId, text);
  }
  for (const [requestId, text] of [...debugChatThinkingBuffers.entries()]) {
    finalizeDebugChatThinking(requestId, text);
  }
}

function debugChatErrorPresentation(event) {
  const code = event?.payload?.error || '';
  const errorType = event?.payload?.error_type || '';
  const retryAfter = Number(event?.payload?.retry_after_s);
  const rawError = event?.payload?.raw_error || '';
  const fallback = event?.detail || event?.payload?.message || rawError || 'The request could not be completed.';
  if (errorType === 'rate_limited' || code === 'rate_limited') {
    const waitHint = Number.isFinite(retryAfter) && retryAfter > 0
      ? ` Wait about ${Math.max(1, Math.ceil(retryAfter))}s, then retry.`
      : ' Wait a moment, then retry.';
    return {
      title: 'Rate limited',
      detail: `${fallback}${waitHint}`,
    };
  }
  const known = {
    malformed_request: {
      title: 'Malformed request',
      detail: rawError || fallback,
      settings: true,
    },
    api_key_missing: {
      title: 'Connect an AI provider',
      detail: 'Add the API key for this model profile, then retry your message.',
      settings: true,
    },
    invalid_model_profile: {
      title: 'Choose another model',
      detail: fallback,
    },
    cloud_runtime_unavailable: {
      title: 'Syte cloud runtime is unavailable',
      detail: fallback,
    },
    agent_start_failed: {
      title: 'The agent could not start',
      detail: fallback,
    },
    agent_not_ready: {
      title: 'The agent is not ready',
      detail: fallback,
    },
    agent_job_failed: {
      title: 'The agent stopped unexpectedly',
      detail: fallback,
    },
    request_timeout: {
      title: 'This response took too long',
      detail: 'The turn may still finish in the background. Reconnect to check for new activity, or retry.',
    },
    network_error: {
      title: 'Connection interrupted',
      detail: fallback,
    },
  };
  return known[code] || {
    title: event?.title || 'The request failed',
    detail: fallback,
    settings: /api key|provider key|credentials/i.test(fallback),
  };
}

function addDebugChatErrorActions(bubble, event, presentation) {
  const retryMessage = event?.payload?.retry_message || '';
  const actions = document.createElement('div');
  actions.className = 'debug-chat-error-actions';

  if (retryMessage) {
    const retry = document.createElement('button');
    retry.type = 'button';
    retry.className = 'debug-chat-error-button';
    retry.textContent = 'Retry';
    retry.addEventListener('click', () => retryDebugChatMessage(retryMessage));
    actions.appendChild(retry);
  }

  if (presentation.settings) {
    const settingsButton = document.createElement('button');
    settingsButton.type = 'button';
    settingsButton.className = 'debug-chat-error-button';
    settingsButton.textContent = 'Provider settings';
    settingsButton.addEventListener('click', openAiSettings);
    actions.appendChild(settingsButton);
  }

  if (event?.payload?.reconnect) {
    const reconnect = document.createElement('button');
    reconnect.type = 'button';
    reconnect.className = 'debug-chat-error-button';
    reconnect.textContent = 'Reconnect';
    reconnect.addEventListener('click', reconnectDebugChatStream);
    actions.appendChild(reconnect);
  }

  if (actions.childElementCount) bubble.appendChild(actions);
}

async function submitDebugChatQuestionAnswer(questionId, answer, formEl) {
  if (!activeServiceId || !questionId) return;
  const controls = formEl?.querySelectorAll('button, input, select');
  controls?.forEach((el) => { el.disabled = true; });
  try {
    const res = await api(
      `/projects/${encodeURIComponent(activeServiceId)}/agent/questions/${encodeURIComponent(questionId)}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) },
    );
    if (res && res.ok === false) {
      toast(res.message || 'Failed to send answer');
      controls?.forEach((el) => { el.disabled = false; });
      return;
    }
    const status = formEl?.querySelector('.debug-chat-question-status');
    if (status) status.textContent = 'Answer sent';
    setDebugChatActivity('Working…', 'Continuing with your answer');
  } catch (err) {
    toast(normalizeFetchError(err?.message || String(err)));
    controls?.forEach((el) => { el.disabled = false; });
  }
}

function mountDebugChatQuestionWidget(container, event) {
  if (!container || !event) return;
  const qid = event.payload?.question_id;
  const qtype = event.payload?.question_type || 'answer';
  const options = Array.isArray(event.payload?.options) ? event.payload.options : [];
  const form = document.createElement('form');
  form.className = 'debug-chat-question-form';
  form.dataset.questionId = qid || '';

  if (qtype === 'choice' || qtype === 'multi_choice') {
    const list = document.createElement('div');
    list.className = 'debug-chat-question-options';
    options.forEach((opt, idx) => {
      const id = `qopt-${qid || 'x'}-${idx}`;
      const label = document.createElement('label');
      label.className = 'debug-chat-question-option';
      const input = document.createElement('input');
      input.type = qtype === 'multi_choice' ? 'checkbox' : 'radio';
      input.name = 'option';
      input.value = String(opt);
      input.id = id;
      label.appendChild(input);
      label.appendChild(document.createTextNode(String(opt)));
      list.appendChild(label);
    });
    form.appendChild(list);
  } else if (qtype === 'slider') {
    const min = Number(event.payload?.min_value ?? 0);
    const max = Number(event.payload?.max_value ?? 100);
    const step = Number(event.payload?.step_value ?? 1);
    const def = Number(event.payload?.default_value ?? min);
    const row = document.createElement('div');
    row.className = 'debug-chat-question-slider-row';
    const range = document.createElement('input');
    range.type = 'range';
    range.min = String(min);
    range.max = String(max);
    range.step = String(step);
    range.value = String(Number.isFinite(def) ? def : min);
    const value = document.createElement('output');
    value.textContent = range.value;
    range.addEventListener('input', () => { value.textContent = range.value; });
    row.appendChild(range);
    row.appendChild(value);
    form.appendChild(row);
  } else {
    const input = document.createElement(qtype === 'answer' ? 'textarea' : 'input');
    if (input.tagName === 'INPUT') input.type = 'text';
    input.className = 'debug-chat-question-input';
    input.placeholder = qtype === 'answer' ? 'Type your answer…' : 'Enter value…';
    if (event.payload?.default_value != null) input.value = String(event.payload.default_value);
    form.appendChild(input);
  }

  const actions = document.createElement('div');
  actions.className = 'debug-chat-question-actions';
  const submit = document.createElement('button');
  submit.type = 'submit';
  submit.className = 'debug-chat-error-button';
  submit.textContent = 'Send answer';
  const status = document.createElement('span');
  status.className = 'debug-chat-question-status';
  if (event.payload?.status === 'answered') {
    status.textContent = 'Already answered';
    submit.disabled = true;
  }
  actions.appendChild(submit);
  actions.appendChild(status);
  form.appendChild(actions);

  form.addEventListener('submit', (ev) => {
    ev.preventDefault();
    if (!qid) return;
    let answer;
    if (qtype === 'choice') {
      answer = form.querySelector('input[name="option"]:checked')?.value;
      if (!answer) { toast('Pick an option', 'error'); return; }
    } else if (qtype === 'multi_choice') {
      answer = [...form.querySelectorAll('input[name="option"]:checked')].map((el) => el.value);
      if (!answer.length) { toast('Pick at least one option', 'error'); return; }
    } else if (qtype === 'slider') {
      answer = Number(form.querySelector('input[type="range"]')?.value || 0);
    } else {
      answer = form.querySelector('.debug-chat-question-input')?.value?.trim() || '';
      if (!answer) { toast('Enter an answer', 'error'); return; }
    }
    void submitDebugChatQuestionAnswer(qid, answer, form);
  });

  container.appendChild(form);
}

function debugChatDetailText(event) {
  const candidates = [
    event?.detail,
    event?.payload?.content,
    event?.payload?.reply,
  ];
  for (const raw of candidates) {
    const text = coerceDebugChatText(raw);
    if (text) return text;
  }
  return '';
}

// A long session can accumulate thousands of bubbles. On low-memory phones an
// unbounded transcript is enough on its own to crash the tab, so keep only a
// recent window in the DOM. Older turns remain available via session history.
// Kept above the 500-event history fetch limit so opening the panel never trims
// the very session it just loaded (the cursor is monotonic, so trimmed history
// would not come back on the next sync).
const DEBUG_CHAT_MAX_BUBBLES = 900;

function trimDebugChatBubbles(messagesEl) {
  if (!messagesEl) return;
  // childElementCount is O(1); only pay for a subtree query once over the cap.
  if (messagesEl.childElementCount <= DEBUG_CHAT_MAX_BUBBLES) return;
  const bubbles = messagesEl.querySelectorAll('.debug-chat-bubble');
  let excess = bubbles.length - DEBUG_CHAT_MAX_BUBBLES;
  if (excess <= 0) return;
  const heightBefore = messagesEl.scrollHeight;
  for (let i = 0; i < bubbles.length && excess > 0; i += 1) {
    const node = bubbles[i];
    // Never drop the bubble currently receiving streamed tokens.
    if (node.classList.contains('debug-chat-streaming')) continue;
    node.remove();
    excess -= 1;
  }
  // Nodes were removed above the viewport. Without compensating scrollTop the
  // content the user is reading jumps, and the smaller scrollHeight can make
  // updateDebugChatScrollState think we are at the bottom and re-enable
  // auto-scroll.
  const removedHeight = heightBefore - messagesEl.scrollHeight;
  if (removedHeight > 0) {
    messagesEl.scrollTop = Math.max(0, messagesEl.scrollTop - removedHeight);
  }
}

function appendDebugChatBubble(event) {
  const lane = event ? debugChatLaneForEvent(event) : 'main';
  debugChatRenderLane = lane;
  const messagesEl = getDebugChatMessagesEl(lane);
  if (!messagesEl || !event) return;

  const role = debugChatRoleForEvent(event);
  let detail = debugChatDetailText(event);
  const errorPresentation = role === 'error' ? debugChatErrorPresentation(event) : null;
  if (errorPresentation) detail = String(errorPresentation.detail || detail || '');
  const actionTitle = debugChatActionTitle(event);

  hideDebugChatEmpty();
  if (event.event_type === 'processing') {
    setDebugChatTyping(true);
    return;
  }
  setDebugChatTyping(false);

  if (role === 'action') {
    // Rendered as a collapsed, openable summary row instead of a long line.
    appendDebugChatActionRow(event, messagesEl, lane);
    trimDebugChatBubbles(messagesEl);
    return;
  }
  // Any non-action bubble ends the current collapsed group.
  debugChatActionGroups[lane] = null;

  if (role === 'assistant' && detail) {
    const assistants = messagesEl.querySelectorAll('.debug-chat-bubble.debug-chat-assistant:not(.debug-chat-typing)');
    const last = assistants[assistants.length - 1];
    const bodyEl = last?.querySelector('.debug-chat-bubble-body');
    const prev = bodyEl?.textContent || '';
    if (last && bodyEl && (detail.startsWith(prev) || prev.startsWith(detail)) && prev.length > 0) {
      bodyEl.textContent = detail.length >= prev.length ? detail : prev;
      if (event.id != null) last.dataset.eventId = String(event.id);
      scrollDebugChatToBottom();
      return;
    }
  }

  const bubble = document.createElement('div');
  bubble.className = `debug-chat-bubble debug-chat-${role}`;
  if (event.id != null) bubble.dataset.eventId = String(event.id);

  if (role === 'user' || role === 'assistant' || role === 'error') {
    const title = role === 'user'
      ? 'You'
      : role === 'error'
        ? errorPresentation.title
        : 'Assistant';
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <span>${esc(title)}</span>
      </div>
      <div class="debug-chat-bubble-body">${esc(detail)}</div>
    `;
  } else if (role === 'thinking') {
    // "Where it thought": step + phase + the tool/file the thought was about.
    const where = debugChatThinkingWhere(event);
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <span>${esc(event.title || 'Thinking')}</span>
        ${where ? `<span class="debug-chat-thinking-where">${esc(where)}</span>` : ''}
      </div>
      <div class="debug-chat-bubble-body debug-chat-thinking">${esc(detail)}</div>
    `;
  } else if (role === 'usage') {
    const cost = event.payload?.cost || {};
    const usage = event.payload?.usage || {};
    const label = cost.label || detail || 'usage';
    const bits = [];
    if (usage.input_tokens) bits.push(`in ${usage.input_tokens}`);
    if (usage.output_tokens) bits.push(`out ${usage.output_tokens}`);
    if (usage.thinking_tokens) bits.push(`think ${usage.thinking_tokens}`);
    bubble.innerHTML = `
      <div class="debug-chat-system-row debug-chat-usage-row">
        <span>Cost — ${esc(label)}${bits.length ? ` (${esc(bits.join(' · '))})` : ''}</span>
      </div>
    `;
  } else if (role === 'screenshot') {
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <span>${esc(event.title || 'Screenshot')}</span>
      </div>
      <div class="debug-chat-bubble-body debug-chat-screenshot-body"></div>
    `;
    const body = bubble.querySelector('.debug-chat-screenshot-body');
    const shots = event.payload?.screenshots || [];
    const grid = document.createElement('div');
    grid.className = 'debug-chat-screenshot-grid';
    shots.forEach((shot) => {
      if (!shot?.ok && !shot?.image_url && !shot?.chat_image_base64) return;
      const fig = document.createElement('figure');
      fig.className = 'debug-chat-screenshot-card';
      const img = document.createElement('img');
      img.alt = `${shot.viewport || 'preview'} screenshot`;
      img.loading = 'lazy';
      if (shot.chat_image_base64) {
        img.src = `data:image/png;base64,${shot.chat_image_base64}`;
      } else {
        img.src = shot.thumb_url || shot.image_url || '';
      }
      if (shot.image_url) {
        img.addEventListener('click', () => window.open(shot.image_url, '_blank', 'noopener'));
      }
      const cap = document.createElement('figcaption');
      cap.textContent = `${shot.viewport || 'view'} · ${shot.width || '?'}×${shot.height || '?'}`;
      fig.appendChild(img);
      fig.appendChild(cap);
      grid.appendChild(fig);
    });
    if (detail) {
      const p = document.createElement('p');
      p.className = 'debug-chat-screenshot-note';
      p.textContent = detail;
      body.appendChild(p);
    }
    body.appendChild(grid);
  } else if (role === 'question') {
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <span>${esc(event.title || 'Question')}</span>
      </div>
      <div class="debug-chat-bubble-body debug-chat-question-body">
        <p class="debug-chat-question-prompt">${esc(detail)}</p>
      </div>
    `;
    const body = bubble.querySelector('.debug-chat-question-body');
    mountDebugChatQuestionWidget(body, event);
  } else {
    bubble.innerHTML = `
      <div class="debug-chat-system-row">
        <span>${esc(event.title || event.event_type)}${detail ? ` — ${esc(detail)}` : ''}</span>
      </div>
    `;
  }

  if (role === 'error') addDebugChatErrorActions(bubble, event, errorPresentation);
  messagesEl.appendChild(bubble);
  trimDebugChatBubbles(messagesEl);
  // Full-document Lucide passes during history replay are extremely expensive
  // (hundreds of createIcons scans) and have caused mobile tab freezes/"Script error".
  if (!debugChatReplayingHistory) refreshIcons(bubble);
  scrollDebugChatToBottom();
}

function shouldSkipDebugChatEvent(event) {
  if (event.event_type === 'request_started') {
    return true;
  }
  // Turn-complete session_stopped was removed server-side; ignore any legacy
  // "completed" markers so the UI does not look idle while work continues.
  if (
    event.event_type === 'session_stopped'
    && String(event.payload?.reason || event.detail || '').toLowerCase() === 'completed'
  ) {
    if (event.id != null) {
      debugChatRenderedIds.add(event.id);
      debugChatSinceId = Math.max(debugChatSinceId, event.id);
    }
    return true;
  }
  // Post-turn health checks after the reply must not spam the transcript.
  if (event.payload?.async_post_turn && !debugChatBusy) {
    if (event.id != null) {
      debugChatRenderedIds.add(event.id);
      debugChatSinceId = Math.max(debugChatSinceId, event.id);
    }
    return true;
  }
  // "Tool started" rows duplicate the finished row and doubled the noise the
  // collapsed activity cards are meant to remove — the live status bar already
  // shows what is running right now.
  if (event.event_type === 'tool_call_started') return true;
  if (event.event_type === 'token_delta') return true;
  if (event.event_type === 'thinking_delta') return true;
  if (event.event_type === 'thinking') {
    const rid = event.payload?.request_id || debugChatActiveRequestId || 'pending';
    // Subagent thinking always gets its own bubble in the subagent lane.
    const streamBubble = debugChatLaneForEvent(event) === 'sub'
      ? null
      : document.getElementById(`debug-chat-thinking-${rid}`);
    if (streamBubble) {
      const body = streamBubble.querySelector('.debug-chat-bubble-body');
      const detail = debugChatDetailText(event);
      if (body && detail) body.textContent = detail;
      setDebugChatThinkingWhere(streamBubble, event);
      streamBubble.classList.remove('debug-chat-streaming');
      if (event.id != null) {
        debugChatRenderedIds.add(event.id);
        debugChatSinceId = Math.max(debugChatSinceId, event.id);
      }
      return true;
    }
  }
  if (event.event_type === 'message_snapshot') {
    if (!debugChatReplayingHistory) {
      finalizeDebugChatStream(event.payload?.request_id, event.payload?.content || event.detail);
    }
    if (event.id != null) {
      debugChatRenderedIds.add(event.id);
      debugChatSinceId = Math.max(debugChatSinceId, event.id);
    }
    return true;
  }
  if (event.event_type === 'request_completed') {
    if (!debugChatReplayingHistory) {
      finalizeDebugChatStream(event.payload?.request_id, event.payload?.reply || event.detail);
    }
    const messagesEl = getDebugChatMessagesEl('main');
    const assistants = messagesEl?.querySelectorAll('.debug-chat-bubble.debug-chat-assistant:not(.debug-chat-typing)');
    const last = assistants?.[assistants.length - 1];
    const body = last?.querySelector('.debug-chat-bubble-body')?.textContent || '';
    const detail = debugChatDetailText(event);
    if (body && detail && (body === detail || body.includes(detail) || detail.includes(body))) {
      if (event.id != null) {
        debugChatRenderedIds.add(event.id);
        debugChatSinceId = Math.max(debugChatSinceId, event.id);
      }
      return true;
    }
  }
  if (event.event_type === 'assistant_message') {
    const rid = event.payload?.request_id;
    // A subagent shares the parent request_id but never owns the main lane's
    // streaming bubble — its result must not be deduped against it.
    const streamBubble = rid && debugChatLaneForEvent(event) === 'main'
      ? document.getElementById(`debug-chat-stream-${rid}`)
      : null;
    if (streamBubble) {
      const body = streamBubble.querySelector('.debug-chat-bubble-body')?.textContent || '';
      const detail = debugChatDetailText(event);
      if (body && detail && (body === detail || body.includes(detail) || detail.includes(body))) {
        if (event.id != null) {
          debugChatRenderedIds.add(event.id);
          debugChatSinceId = Math.max(debugChatSinceId, event.id);
        }
        return true;
      }
    }
  }
  if (event.event_type === 'user_message') {
    const messagesEl = getDebugChatMessagesEl('main');
    const bubbles = messagesEl?.querySelectorAll('.debug-chat-bubble:not(.debug-chat-typing)');
    const last = bubbles?.[bubbles.length - 1];
    if (last?.classList.contains('debug-chat-user')) {
      const body = last.querySelector('.debug-chat-bubble-body')?.textContent;
      if (body === event.detail) {
        if (event.id != null) {
          debugChatRenderedIds.add(event.id);
          debugChatSinceId = Math.max(debugChatSinceId, event.id);
        }
        return true;
      }
    }
  }
  return false;
}

function handleDebugChatActivity(event) {
  if (!event) return;
  // Route every event to its lane before anything renders.
  const lane = debugChatLaneForEvent(event);
  debugChatRenderLane = lane;
  const isSubagentLifecycle = String(event.event_type || '').startsWith('subagent_');
  if (lane === 'sub' || isSubagentLifecycle) {
    revealDebugChatSubagentTab();
    if (!debugChatReplayingHistory) scheduleDebugChatSubagentRefresh();
  }
  if (!debugChatReplayingHistory && debugChatIsFailureEvent(event)) {
    scheduleDebugChatFailureRefresh();
  }
  const eventRequestId = event.payload?.request_id || '';
  const isTerminal = event.event_type === 'request_completed'
    || event.event_type === 'request_failed'
    || event.event_type === 'agent_stopped';
  if (isTerminal && eventRequestId) {
    if (debugChatTerminalRequestIds.has(eventRequestId)) {
      // This turn's ending was already rendered, so don't render it twice — but
      // a repeat delivery (poll after SSE, or a history replay that ran before
      // the composer was unlocked) must still release the composer. Returning
      // early here is what left finished tasks marked as "working".
      //
      // Only for the turn we are actually holding: an overlapping poll can
      // re-deliver an older turn's terminal event after a newer turn started,
      // and releasing on that would unlock the composer mid-response.
      if (
        !debugChatReplayingHistory
        && !debugChatSendInFlight
        && eventRequestId === debugChatActiveRequestId
      ) {
        releaseDebugChatTurn(event, eventRequestId, { finalizeStream: false });
      }
      return;
    }
    debugChatTerminalRequestIds.add(eventRequestId);
  }
  const eventId = event.id;
  if (eventId != null && debugChatRenderedIds.has(eventId)) return;
  if (eventId != null) {
    debugChatRenderedIds.add(eventId);
    debugChatSinceId = Math.max(debugChatSinceId, eventId);
    if (debugChatRenderedIds.size > 2000) {
      for (const oldId of [...debugChatRenderedIds].slice(0, 500)) {
        debugChatRenderedIds.delete(oldId);
      }
    }
  }

  if (event.event_type === 'request_started') {
    if (!debugChatReplayingHistory) {
      setDebugChatTyping(true);
      setDebugChatBusy(true);
      debugChatActiveRequestId = eventRequestId || debugChatActiveRequestId;
      setDebugChatActivity('Planning…', 'Model is thinking through the request');
      if (eventRequestId) {
        armDebugChatRequestWatchdog(activeServiceId, eventRequestId);
      }
    }
  }
  if (event.event_type === 'token_delta') {
    if (!debugChatReplayingHistory && debugChatActivityLabel !== 'Writing…') {
      setDebugChatActivity('Writing…', 'Streaming response');
    }
    queueDebugChatStreamDelta(
      event.payload?.request_id,
      event.payload?.delta || event.detail,
      event.payload?.snapshot,
    );
    if (event.id != null) {
      debugChatRenderedIds.add(event.id);
      debugChatSinceId = Math.max(debugChatSinceId, event.id);
    }
    return;
  }
  if (event.event_type === 'thinking_delta') {
    // During history replay, prefer the final `thinking` event so we do not
    // rebuild one bubble per streamed word.
    if (!debugChatReplayingHistory) {
      if (debugChatActivityLabel !== 'Planning…') {
        setDebugChatActivity('Planning…', 'Model is thinking');
      }
      queueDebugChatThinkingDelta(
        event.payload?.request_id,
        event.payload?.delta || event.detail,
        event.payload?.snapshot,
      );
    }
    if (event.id != null) {
      debugChatRenderedIds.add(event.id);
      debugChatSinceId = Math.max(debugChatSinceId, event.id);
    }
    return;
  }
  // Only the main agent streams reasoning; finalizing here for a subagent event
  // would write its text into the main lane's streaming thinking bubble.
  if (event.event_type === 'thinking' && !debugChatReplayingHistory && lane === 'main') {
    finalizeDebugChatThinking(
      event.payload?.request_id || debugChatActiveRequestId,
      event.detail || event.payload?.delta || '',
    );
  }
  if (
    event.event_type === 'request_completed'
    || event.event_type === 'request_failed'
    || event.event_type === 'agent_stopped'
    || (
      event.event_type === 'session_stopped'
      && String(event.payload?.reason || '').toLowerCase() !== 'completed'
    )
  ) {
    const requestId = eventRequestId || debugChatActiveRequestId;
    finalizeDebugChatThinking(requestId);
    const isActiveRequest = !debugChatActiveRequestId
      || (Boolean(eventRequestId) && eventRequestId === debugChatActiveRequestId);
    // During history replay, bubbles are rendered via appendDebugChatBubble —
    // don't create streaming "Agent" placeholders (avoids duplicate/[object Object] artifacts).
    if (!debugChatReplayingHistory) {
      if (isActiveRequest) {
        releaseDebugChatTurn(event, requestId);
      } else {
        // A terminal event for some other request id must not silently leave
        // the composer locked — ask the server whether anything is still running.
        void reconcileDebugChatBusyState(activeServiceId);
      }
    }
  }
  if (event.event_type === 'agent_started' && !debugChatReplayingHistory) {
    void updateDebugChatAgentStatus();
  }

  if (shouldSkipDebugChatEvent(event)) return;

  appendDebugChatBubble(event);
  if (lane === 'sub' && !debugChatReplayingHistory && debugChatActiveLane !== 'sub') {
    debugChatSubagentUnread += 1;
    updateDebugChatSubagentBadge();
  }
  if (!debugChatReplayingHistory && event.event_type === 'thinking') {
    const where = debugChatThinkingWhere(event);
    setDebugChatActivity(
      'Planning…',
      `${lane === 'sub' ? 'subagent · ' : ''}${where || String(event.detail || 'Preparing a plan').replace(/\s+/g, ' ')}`.slice(0, 160),
    );
  }
  if (!debugChatReplayingHistory && event.event_type === 'screenshot') {
    setDebugChatActivity('Capturing…', String(event.detail || 'Preview screenshots').slice(0, 160), 'monitor-smartphone');
  }
  if (!debugChatReplayingHistory && event.event_type === 'question') {
    setDebugChatActivity('Waiting for answer…', String(event.detail || '').slice(0, 160), 'circle-help');
  }
  if (!debugChatReplayingHistory && [
    'tool_call', 'command_run', 'file_created', 'file_modified', 'file_deleted',
    'file_read', 'file_search', 'file_changed', 'tool_call_started',
    'tool_call_finished', 'command_output', 'service_action',
  ].includes(event.event_type)) {
    const actionMeta = debugChatActionMeta(event);
    const phase = event.event_type === 'file_read' || event.event_type === 'file_search'
      ? 'Reading…'
      : (event.event_type === 'file_created' || event.event_type === 'file_modified' || event.event_type === 'file_changed')
        ? 'Editing…'
        : (event.event_type === 'command_run' || event.event_type === 'command_output')
          ? 'Running…'
          : 'Working…';
    const target = debugChatActionTarget(event);
    setDebugChatActivity(
      phase,
      `${lane === 'sub' ? 'subagent · ' : ''}${debugChatActionTitle(event)}${
        target ? ` · ${target}` : ''
      }`.slice(0, 200),
      event.event_type === 'tool_call_started' ? 'loader' : actionMeta.icon,
    );
  }
  if (!debugChatReplayingHistory && isSubagentLifecycle) {
    const label = event.event_type === 'subagent_started' ? 'Delegating…' : 'Working…';
    setDebugChatActivity(
      label,
      `${DEBUG_CHAT_ACTION_LABELS[event.event_type] || 'Subagent'}${
        event.detail ? ` · ${String(event.detail).replace(/\s+/g, ' ')}` : ''
      }`.slice(0, 200),
      'git-fork',
    );
  }
  const refreshTypes = [
    'file_created', 'file_modified', 'file_deleted', 'file_changed',
    'service_action', 'request_completed',
  ];
  if (!debugChatReplayingHistory && refreshTypes.includes(event.event_type)) {
    onDebugChatWorkspaceChange();
  }
}

async function onDebugChatWorkspaceChange() {
  if (!activeServiceId) return;
  await loadProjects({ silent: true });
  if (activeSvcTab === 'preview') {
    const p = projects.find(x => x.id === activeServiceId);
    if (p?.preview_running) renderPreviewSection(p);
  }
}

// Guards against overlapping history reads. Two reads spanning a cursor advance
// re-deliver the same terminal event, which used to unlock the composer.
let debugChatSyncInFlight = false;

async function syncDebugChatHistory(projectId) {
  if (debugChatSyncInFlight) return false;
  debugChatSyncInFlight = true;
  try {
    // `session=last` is required: without it a sync from cursor 0 (a freshly
    // opened panel) replays *every* session into the transcript.
    const res = await api(
      `/projects/${projectId}/agent/activity?since_id=${debugChatSinceId}&limit=500&session=last`,
    );
    for (const event of res.events || []) {
      handleDebugChatActivity(event);
    }
    return true;
  } catch {
    return false;
  } finally {
    debugChatSyncInFlight = false;
  }
}

// Last-resort guard against a stuck composer: ask the server whether the agent
// is actually busy and release the UI when it is not. Terminal events can be
// missed (dropped frame, request id mismatch, event without a session mark),
// and without this the chat shows "Working…" for a finished turn and the Stop
// button stays the only way out.
async function reconcileDebugChatBusyState(projectId) {
  if (!projectId || projectId !== activeServiceId) return;
  if (!debugChatBusy || debugChatSendInFlight) return;
  // Snapshot the turn being judged. A response issued for an earlier turn must
  // never unlock a newer one that started while this request was in flight.
  const judgedRequestId = debugChatActiveRequestId;
  let res;
  try {
    res = await api(`/projects/${projectId}/agent`);
  } catch {
    return;
  }
  if (projectId !== activeServiceId || debugChatSendInFlight) return;
  if (debugChatActiveRequestId !== judgedRequestId) return;
  if (res.agent_busy) return;
  finalizeAllDebugChatStreams();
  finalizeDebugChatThinking(debugChatActiveRequestId);
  clearDebugChatRequestWatchdog();
  setDebugChatTyping(false);
  setDebugChatBusy(false);
  debugChatActiveRequestId = '';
  setDebugChatActivity('Response ready', '', 'check-circle-2');
  dismissDebugChatActivitySoon();
}

async function renderDebugChatSessionHeader(metadata) {
  if (!metadata) return;
  const headerEl = document.getElementById('debug-chat-session-header');
  if (!headerEl) return;
  
  const startDate = new Date(metadata.start_time);
  const endDate = new Date(metadata.end_time);
  const duration = Math.floor((endDate - startDate) / 1000);
  const durationStr = duration > 60 ? `${Math.floor(duration / 60)}m ${duration % 60}s` : `${duration}s`;
  
  const statusColors = {
    'running': 'status-running',
    'complete': 'status-complete',
    'stopped': 'status-stopped',
    'error': 'status-error',
  };
  const statusLabel = {
    'running': 'Running',
    'complete': 'Complete',
    'stopped': 'Stopped',
    'error': 'Error',
  };
  
  const statusClass = statusColors[metadata.status] || 'status-running';
  const statusText = statusLabel[metadata.status] || 'Unknown';
  const errorBadge = metadata.error_count > 0 ? `<span class="session-error-badge">${metadata.error_count}</span>` : '';
  
  headerEl.innerHTML = `
    <div class="session-header-card">
      <div class="session-status">
        <span class="status-badge ${statusClass}">${statusText}</span>
        <span class="session-duration">${durationStr}</span>
        ${errorBadge}
      </div>
      <div class="session-time">${startDate.toLocaleTimeString()}</div>
    </div>
  `;
  headerEl.classList.remove('hidden');
}

async function loadDebugChatHistory(projectId) {
  debugChatReplayingHistory = true;
  updateDebugChatControls();
  try {
    // Load latest session metadata first
    let metadata = null;
    try {
      const metadataRes = await api(`/projects/${projectId}/agent/activity/latest`);
      metadata = metadataRes.metadata;
      renderDebugChatSessionHeader(metadata);
    } catch (e) {
      console.log('[v0] Could not load session metadata:', e.message);
    }
    
    // Only the latest [sessionN] block is loaded on open; earlier sessions are
    // already saved and never re-fetched. New live sessions arrive over the stream.
    const res = await api(`/projects/${projectId}/agent/activity?since_id=0&limit=500&session=last`);
    const pendingRequests = new Map();
    for (const event of res.events || []) {
      const requestId = event.payload?.request_id || '';
      if (event.event_type === 'request_started' && requestId) {
        pendingRequests.set(requestId, event);
      } else if (
        requestId
        && (
          event.event_type === 'request_completed'
          || event.event_type === 'request_failed'
          || event.event_type === 'agent_stopped'
        )
      ) {
        pendingRequests.delete(requestId);
      }
    }
    clearDebugChatPanel({ resetCursor: true });
    for (const event of res.events || []) {
      handleDebugChatActivity(event);
    }
    const lastId = (res.events || []).reduce((max, e) => Math.max(max, e.id || 0), 0);
    if (lastId) debugChatSinceId = Math.max(debugChatSinceId, lastId);

    // Clear busy state explicitly if there are no pending requests to fix the "stuck on generating" issue on load
    if (pendingRequests.size === 0) {
      setDebugChatBusy(false);
      debugChatSendInFlight = false;
      updateDebugChatControls();
      await updateDebugChatAgentStatus();
    }

    const pendingRequestId = [...pendingRequests.keys()].pop() || '';
    if (pendingRequestId) {
      debugChatActiveRequestId = pendingRequestId;
      setDebugChatBusy(true);
      setDebugChatActivity('Working…', 'Reconnected to the active response');
      armDebugChatRequestWatchdog(projectId, pendingRequestId);
    }
  } catch (e) {
    appendDebugChatBubble({
      event_type: 'request_failed',
      title: 'Could not load history',
      detail: normalizeFetchError(e.message),
    });
  } finally {
    debugChatReplayingHistory = false;
    finalizeAllDebugChatStreams();
    setDebugChatTyping(false);
    debugChatRenderLane = debugChatActiveLane;
    // Durable sources of truth, independent of the replay window: a subagent
    // that ran earlier in this session still reveals its tab, and the brain
    // badge shows this session's failure count immediately on open.
    await loadDebugChatSubagents(projectId);
    await loadDebugChatFailures(projectId, { render: false });
    for (const laneEl of getDebugChatLaneEls()) refreshIcons(laneEl);
    updateDebugChatSubagentBadge();
    // Replay appends with force=false, so a loaded transcript could otherwise
    // sit parked mid-scroll. Opening a chat must show the newest message.
    scrollDebugChatToBottom(true, debugChatActiveLane);
    if (!debugChatActiveRequestId && !debugChatSendInFlight) {
      setDebugChatBusy(false);
    } else {
      updateDebugChatControls();
    }
  }
}

// Prefer SSE for token-level streaming; fall back to short-interval polling of
// /agent/activity (and Turso session docs) when EventSource is unavailable.
async function pollAgentActivityOnce(projectId) {
  if (agentActivityPollInFlight) return;
  agentActivityPollInFlight = true;
  try {
    const ok = await syncDebugChatHistory(projectId);
    setDebugChatConnectionState(ok ? 'connected' : 'reconnecting');
  } finally {
    agentActivityPollInFlight = false;
  }
}

// SSE frames are emitted as `event: {event_type}` (see agent_activity.py /
// docs/agent-streaming-api.md). EventSource.onmessage only receives the default
// `message` type, so we must also bind listeners for every activity event name.
const DEBUG_CHAT_SSE_EVENT_TYPES = [
  'user_message', 'assistant_message', 'thinking', 'thinking_delta', 'usage', 'tool_call', 'command_run',
  'file_created', 'file_modified', 'file_deleted', 'file_read', 'file_search',
  'request_started', 'request_completed', 'request_failed', 'token_delta',
  'message_snapshot', 'tool_call_started', 'tool_call_finished', 'tool_error',
  'file_changed', 'command_output', 'agent_started', 'agent_stopped', 'status',
  'processing', 'service_action', 'screenshot', 'question', 'question_answered',
  'session_stopped', 'plan', 'message',
  // Subagent lane: scope hand-off (main tab) + subagent lifecycle (subagent tab).
  'subagent_scope', 'subagent_started', 'subagent_completed', 'subagent_failed',
];

function handleAgentActivitySseFrame(evt) {
  agentActivityLastFrameAt = Date.now();
  // A stream for a previously opened project can still be draining when the
  // user switches projects. Its frames must not land in the new transcript.
  if (agentActivityStreamProjectId !== activeServiceId) return;
  agentActivityReconnectAttempts = 0;
  try {
    const event = JSON.parse(evt.data || '{}');
    if (event && event.event_type) {
      applyDebugChatActivityEvent(event);
    }
  } catch (_) {
    /* ignore malformed frames */
  }
}

// Keep-alive frame: proves the connection is alive without touching the
// transcript. Used by the stall watchdog below.
function handleAgentActivityHeartbeat() {
  agentActivityLastFrameAt = Date.now();
  // Reset backoff only once the connection has proven it can carry traffic. A
  // proxy that accepts the request and immediately drops it fires onopen every
  // time, which would otherwise pin the retry delay at its minimum forever.
  agentActivityReconnectAttempts = 0;
  setDebugChatConnectionState('connected');
}

// The server dropped events for this subscriber under backpressure. Backfill
// from history so the transcript has no hole.
function handleAgentActivityStreamGap() {
  agentActivityLastFrameAt = Date.now();
  if (agentActivityStreamProjectId) {
    void syncDebugChatHistory(agentActivityStreamProjectId);
  }
}

function bindAgentActivityEventSource(es) {
  es.onmessage = handleAgentActivitySseFrame;
  for (const type of DEBUG_CHAT_SSE_EVENT_TYPES) {
    es.addEventListener(type, handleAgentActivitySseFrame);
  }
  es.addEventListener('heartbeat', handleAgentActivityHeartbeat);
  es.addEventListener('stream_gap', handleAgentActivityStreamGap);
}

function startAgentActivityPollFallback(projectId) {
  if (agentActivityPollTimer) return;
  agentActivityPollTimer = setInterval(() => {
    if (activeSvcTab !== 'debug-chat' || activeServiceId !== projectId) {
      stopAgentActivityStream();
      return;
    }
    void pollAgentActivityOnce(projectId);
  }, AGENT_ACTIVITY_POLL_INTERVAL_MS);
}

function agentActivityStreamIsCurrent(projectId) {
  return activeSvcTab === 'debug-chat'
    && activeServiceId === projectId
    && agentActivityStreamProjectId === projectId;
}

// Re-open the stream with exponential backoff. Polling runs in the meantime so
// no activity is missed while the stream is down.
function scheduleAgentActivityReconnect(projectId) {
  if (agentActivityReconnectTimer) return;
  if (!agentActivityStreamIsCurrent(projectId)) return;
  startAgentActivityPollFallback(projectId);
  const delay = Math.min(
    AGENT_ACTIVITY_RECONNECT_BASE_MS * (2 ** agentActivityReconnectAttempts),
    AGENT_ACTIVITY_RECONNECT_MAX_MS,
  );
  agentActivityReconnectAttempts += 1;
  agentActivityReconnectTimer = setTimeout(() => {
    agentActivityReconnectTimer = null;
    if (!agentActivityStreamIsCurrent(projectId)) return;
    openAgentActivityEventSource(projectId);
  }, delay);
}

function openAgentActivityEventSource(projectId) {
  if (agentActivityEventSource) {
    agentActivityEventSource.onerror = null;
    agentActivityEventSource.onopen = null;
    agentActivityEventSource.close();
    agentActivityEventSource = null;
  }
  setDebugChatConnectionState(agentActivityReconnectAttempts ? 'reconnecting' : 'connecting');
  agentActivityLastFrameAt = Date.now();
  let es;
  try {
    const url = `${API}/projects/${projectId}/agent/activity/stream`
      + `?session=last&since_id=${encodeURIComponent(debugChatSinceId || 0)}`;
    es = new EventSource(url);
  } catch (_) {
    scheduleAgentActivityReconnect(projectId);
    return;
  }
  agentActivityEventSource = es;
  const isReconnect = agentActivityReconnectAttempts > 0;
  es.onopen = () => {
    if (agentActivityEventSource !== es) return;
    agentActivityLastFrameAt = Date.now();
    setDebugChatConnectionState('connected');
    // The stream is authoritative again; polling is no longer needed.
    stopAgentActivityPollFallback();
    // Only after a drop: the stream replays its own backlog from since_id, so
    // syncing here on a first connect would be a third redundant read of the
    // same window (and overlapping reads can re-deliver terminal events).
    if (isReconnect) void syncDebugChatHistory(projectId);
  };
  bindAgentActivityEventSource(es);
  es.onerror = () => {
    if (agentActivityEventSource !== es) return;
    setDebugChatConnectionState('reconnecting');
    es.onerror = null;
    es.onopen = null;
    es.close();
    agentActivityEventSource = null;
    scheduleAgentActivityReconnect(projectId);
  };
}

// Browsers do not always fire `onerror` for a connection that has gone quiet
// (backgrounded mobile tab, dead proxy). Heartbeat silence is the reliable
// signal, so force a reconnect when frames stop arriving.
function startAgentActivityStallWatchdog(projectId) {
  if (agentActivityStallTimer) clearInterval(agentActivityStallTimer);
  agentActivityStallTimer = setInterval(() => {
    if (!agentActivityStreamIsCurrent(projectId)) {
      stopAgentActivityStream();
      return;
    }
    if (!agentActivityEventSource || agentActivityReconnectTimer) return;
    if (Date.now() - agentActivityLastFrameAt < AGENT_ACTIVITY_STALL_TIMEOUT_MS) return;
    setDebugChatConnectionState('reconnecting');
    agentActivityEventSource.onerror = null;
    agentActivityEventSource.onopen = null;
    agentActivityEventSource.close();
    agentActivityEventSource = null;
    scheduleAgentActivityReconnect(projectId);
  }, AGENT_ACTIVITY_STALL_CHECK_MS);
}

function startAgentActivityStream(projectId) {
  stopAgentActivityStream();
  agentActivityStreamProjectId = projectId;
  agentActivityReconnectAttempts = 0;
  void loadDebugChatResumeSession(projectId);
  void pollAgentActivityOnce(projectId);
  openAgentActivityEventSource(projectId);
  startAgentActivityStallWatchdog(projectId);
  startDebugChatBrainPoll(projectId);
}

async function loadDebugChatResumeSession(projectId) {
  try {
    const res = await api(`/projects/${projectId}/agent/sessions?resume=1&limit=5`);
    debugChatResumeSession = res.resume_session || res.open_session || null;
    if (res.last_work) {
      const detail = document.querySelector('#debug-chat-activity .debug-chat-activity-detail');
      if (detail && !debugChatBusy) detail.textContent = res.last_work;
    }
  } catch (_) {
    debugChatResumeSession = null;
  }
}

function applyDebugChatActivityEvent(event) {
  // Reuse the same path as history sync for a single live event.
  if (typeof handleDebugChatActivity === 'function') {
    handleDebugChatActivity(event);
    return;
  }
  if (typeof appendDebugChatBubble === 'function' && event.event_type) {
    appendDebugChatBubble(event);
  }
}

async function reconnectDebugChatStream() {
  if (!activeServiceId || activeSvcTab !== 'debug-chat') return;
  const projectId = activeServiceId;
  stopAgentActivityStream();
  setDebugChatConnectionState('connecting');
  await syncDebugChatHistory(projectId);
  startAgentActivityStream(projectId);
}

async function updateDebugChatAgentStatus() {
  if (!activeServiceId) return;
  try {
    const res = await api(`/projects/${activeServiceId}/agent`);
    if (res.agent_running && res.agent_healthy) {
      const model = res.agent_model?.profile || res.agent_model?.model || 'agent';
      debugChatIdleStatus = `Ready · ${model}`;
    } else if (res.agent_status === 'starting' || res.agent_warming) {
      debugChatIdleStatus = 'Warming agent…';
    } else if (res.agent_last_error) {
      debugChatIdleStatus = 'Agent needs attention';
    } else if (res.agent_install_ok === false) {
      debugChatIdleStatus = 'Syte cloud runtime is unavailable';
    } else if (res.agent_backend && !res.agent_backend.ok) {
      debugChatIdleStatus = 'Connect an AI provider';
    } else {
      debugChatIdleStatus = 'Ready · starts on first message';
    }
  } catch {
    debugChatIdleStatus = 'Agent status unavailable';
  }
  if (!debugChatBusy && !debugChatSendInFlight) {
    setDebugChatActivity(debugChatIdleStatus);
  }
}

function updateDebugChatControls() {
  const btn = document.getElementById('debug-chat-send');
  const input = document.getElementById('debug-chat-input');
  const cancel = document.getElementById('debug-chat-cancel');
  const profile = document.getElementById('debug-chat-profile');
  const hasMessage = Boolean(String(input?.value || '').trim());
  const controlsBusy = debugChatBusy || debugChatSendInFlight || debugChatReplayingHistory;
  if (btn) {
    btn.disabled = controlsBusy || !hasMessage;
    btn.classList.toggle('is-loading', debugChatSendInFlight);
    btn.setAttribute('aria-busy', debugChatSendInFlight ? 'true' : 'false');
    const label = btn.querySelector('span');
    if (label) label.textContent = debugChatSendInFlight ? 'Sending…' : 'Send';
  }
  if (input) {
    input.disabled = false;
    input.setAttribute('aria-busy', debugChatBusy ? 'true' : 'false');
  }
  if (profile) profile.disabled = controlsBusy;
  if (cancel) {
    cancel.classList.toggle('hidden', !debugChatBusy);
    cancel.disabled = !debugChatBusy || debugChatStopping;
    cancel.classList.toggle('is-loading', Boolean(debugChatBusy && debugChatStopping));
    const label = cancel.querySelector('span');
    if (label) label.textContent = debugChatStopping ? 'Stopping…' : 'Stop';
  }
}

function setDebugChatBusy(busy) {
  debugChatBusy = busy;
  if (!busy) debugChatStopping = false;
  updateDebugChatControls();
}

function clearDebugChatRequestWatchdog() {
  if (debugChatRequestWatchdogTimer) {
    clearTimeout(debugChatRequestWatchdogTimer);
    debugChatRequestWatchdogTimer = null;
  }
  debugChatRequestStartedAt = 0;
}

function armDebugChatRequestWatchdog(projectId, requestId) {
  if (!projectId || !requestId) return;
  clearDebugChatRequestWatchdog();
  debugChatRequestStartedAt = Date.now();

  const checkRequest = async () => {
    if (debugChatActiveRequestId !== requestId) {
      clearDebugChatRequestWatchdog();
      return;
    }

    await syncDebugChatHistory(projectId);
    if (debugChatActiveRequestId !== requestId) return;

    // If the terminal event never arrived, fall back to authoritative server
    // state so the turn cannot stay "working" indefinitely.
    if (Date.now() - debugChatRequestStartedAt > 6000) {
      await reconcileDebugChatBusyState(projectId);
      if (debugChatActiveRequestId !== requestId) return;
    }

    const delay = debugChatConnectionState === 'connected' ? 8000 : 3000;
    debugChatRequestWatchdogTimer = setTimeout(checkRequest, delay);
  };

  debugChatRequestWatchdogTimer = setTimeout(checkRequest, 4000);
}

async function retryDebugChatMessage(message) {
  if (debugChatBusy || debugChatSendInFlight) {
    toast('Wait for the current response or stop it before retrying.');
    return;
  }
  const input = document.getElementById('debug-chat-input');
  if (!input) return;
  input.value = message || debugChatLastUserMessage;
  input.dispatchEvent(new Event('input'));
  input.focus();
  await sendDebugChatMessage();
}

async function cancelDebugChatRequest() {
  if (!activeServiceId || !debugChatBusy || debugChatStopping) return;
  const cancel = document.getElementById('debug-chat-cancel');
  debugChatStopping = true;
  setDebugChatBusy(true);
  setDebugChatActivity('Stopping response', 'Interrupting the Syte cloud turn', 'square');
  const projectId = activeServiceId;
  const stoppingRequestId = debugChatActiveRequestId;
  if (stoppingRequestId) {
    debugChatStoppedRequestIds.add(stoppingRequestId);
    if (debugChatStoppedRequestIds.size > 64) {
      debugChatStoppedRequestIds = new Set([...debugChatStoppedRequestIds].slice(-32));
    }
  }
  try {
    const res = await api(`/projects/${projectId}/agent/interrupt`, { method: 'POST' });
    if (!res.ok) throw new Error(formatAgentChatError(res));
    setDebugChatTyping(false);
    // The endpoint cancels the durable job before returning, so the turn is
    // over either way. Release the composer immediately instead of waiting for
    // a terminal event that may never arrive, then backfill the transcript.
    finalizeAllDebugChatStreams();
    finalizeDebugChatThinking(debugChatActiveRequestId);
    clearDebugChatRequestWatchdog();
    setDebugChatBusy(false);
    debugChatActiveRequestId = '';
    setDebugChatActivity('Response stopped', 'Conversation history is preserved', 'square');
    dismissDebugChatActivitySoon();
    if (projectId === activeServiceId) {
      void syncDebugChatHistory(projectId);
      void updateDebugChatAgentStatus();
    }
  } catch (e) {
    debugChatStopping = false;
    toast('Could not stop response: ' + normalizeFetchError(e.message));
    if (cancel) cancel.disabled = false;
    // Never strand the user with a locked composer because Stop failed: verify
    // against server state and unlock if nothing is actually running.
    await reconcileDebugChatBusyState(projectId);
    updateDebugChatControls();
  }
}

async function getDebugChatProfile() {
  const select = document.getElementById('debug-chat-profile');
  const value = select?.value || select?.getAttribute('value') || 'auto';
  return value;
}

function setDebugChatResourceButtons(mode) {
  const mcp = document.getElementById('debug-chat-mcp');
  const skills = document.getElementById('debug-chat-skills');
  if (mcp) mcp.setAttribute('aria-expanded', mode === 'mcp' ? 'true' : 'false');
  if (skills) skills.setAttribute('aria-expanded', mode === 'skills' ? 'true' : 'false');
}

function closeDebugChatResources() {
  debugChatResourceMode = '';
  document.getElementById('debug-chat-resources')?.classList.add('hidden');
  setDebugChatResourceButtons('');
}

function renderDebugChatResources(mode, data) {
  const body = document.getElementById('debug-chat-resources-body');
  const title = document.getElementById('debug-chat-resources-title');
  const subtitle = document.getElementById('debug-chat-resources-subtitle');
  if (!body || !title || !subtitle) return;
  if (mode === 'mcp') {
    const addons = data.addons || [];
    title.textContent = 'MCP connections';
    subtitle.textContent = 'Give the agent tools for previews, files, and external services.';
    const connected = addons.filter(addon => addon.status === 'connected').length;
    const count = document.getElementById('debug-chat-mcp-count');
    if (count) count.textContent = String(connected);
    body.innerHTML = addons.length ? addons.map(addon => {
      const isConnected = addon.status === 'connected';
      const toolNames = (addon.tools || []).map(tool => tool.name).filter(Boolean).slice(0, 4);
      return `<div class="debug-chat-resource-card">
        <div class="debug-chat-resource-main">
          <div class="debug-chat-resource-name"><i data-lucide="plug"></i>${esc(addon.name)} <span class="debug-chat-resource-status ${isConnected ? 'connected' : ''}">${isConnected ? 'Connected' : 'Available'}</span></div>
          <div class="debug-chat-resource-description">${esc(addon.description || 'MCP tool provider')}</div>
          ${toolNames.length ? `<div class="debug-chat-resource-meta">${esc(toolNames.join(' · '))}${(addon.tools || []).length > 4 ? ' · …' : ''}</div>` : ''}
        </div>
        <button type="button" class="debug-chat-resource-action" onclick="${isConnected ? `disconnectDebugChatMcp('${esc(addon.id)}')` : `connectDebugChatMcp('${esc(addon.id)}')`}">${isConnected ? 'Disconnect' : 'Connect'}</button>
      </div>`;
    }).join('') : '<div class="debug-chat-resource-empty">No MCP providers registered for this project.</div>';
    body.insertAdjacentHTML('beforeend', `<div class="debug-chat-resource-form">
      <input id="debug-chat-mcp-name" placeholder="Provider name" aria-label="MCP provider name">
      <input id="debug-chat-mcp-command" placeholder="Command, e.g. npx" aria-label="MCP command">
      <button type="button" class="debug-chat-resource-action" onclick="registerDebugChatMcp()">Add</button>
    </div>`);
  } else {
    const skills = data.skills || [];
    title.textContent = 'Agent skills';
    subtitle.textContent = 'Enable built-in guidance or add custom skills for this project.';
    const active = skills.filter(skill => skill.active).length;
    const count = document.getElementById('debug-chat-skills-count');
    if (count) count.textContent = String(active);
    body.innerHTML = (skills.length ? skills.map(skill => {
      const actions = skill.custom
        ? `<div class="debug-chat-resource-actions">
            <button type="button" class="debug-chat-resource-action" onclick="${skill.active ? `disableDebugChatSkill('${esc(skill.id)}')` : `enableDebugChatSkill('${esc(skill.id)}')`}">${skill.active ? 'Disable' : 'Enable'}</button>
            <button type="button" class="debug-chat-resource-action" onclick="deleteDebugChatSkill('${esc(skill.id)}')">Delete</button>
          </div>`
        : `<button type="button" class="debug-chat-resource-action" onclick="${skill.active ? `disableDebugChatSkill('${esc(skill.id)}')` : `enableDebugChatSkill('${esc(skill.id)}')`}">${skill.active ? 'Disable' : 'Enable'}</button>`;
      return `<div class="debug-chat-resource-card">
      <div class="debug-chat-resource-main">
        <div class="debug-chat-resource-name"><i data-lucide="sparkles"></i>${esc(skill.name)} <span class="debug-chat-resource-status ${skill.active ? 'active' : ''}">${skill.active ? 'Active' : 'Off'}</span>${skill.custom ? ' <span class="debug-chat-resource-status">Custom</span>' : ''}</div>
        <div class="debug-chat-resource-description">${esc(skill.description || skill.content || '')}</div>
      </div>
      ${actions}
    </div>`;
    }).join('') : '<div class="debug-chat-resource-empty">No skills are available.</div>')
      + `<div class="debug-chat-resource-form debug-chat-resource-form-skill">
      <input id="debug-chat-skill-name" placeholder="Skill name" aria-label="Skill name">
      <input id="debug-chat-skill-description" placeholder="Short description (optional)" aria-label="Skill description">
      <textarea id="debug-chat-skill-content" placeholder="Guidance content for the agent" aria-label="Skill content" rows="3"></textarea>
      <button type="button" class="debug-chat-resource-action" onclick="addDebugChatSkill()">Add</button>
    </div>`;
  }
  refreshIcons();
}

async function openDebugChatResources(mode) {
  if (!activeServiceId) return;
  if (debugChatResourceMode === mode) {
    closeDebugChatResources();
    return;
  }
  debugChatResourceMode = mode;
  const panel = document.getElementById('debug-chat-resources');
  const body = document.getElementById('debug-chat-resources-body');
  if (!panel || !body) return;
  setDebugChatResourceButtons(mode);
  // Resources and the failure log share the same slot above the chat lanes.
  closeDebugChatFailureLog();
  panel.classList.remove('hidden');
  body.innerHTML = '<div class="debug-chat-resource-loading">Loading…</div>';
  try {
    const data = await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/${mode}`);
    if (debugChatResourceMode === mode) renderDebugChatResources(mode, data);
  } catch (error) {
    body.innerHTML = `<div class="debug-chat-resource-empty">Could not load ${mode}: ${esc(normalizeFetchError(error.message))}</div>`;
  }
}

async function refreshDebugChatResources(mode = debugChatResourceMode) {
  if (!mode || !activeServiceId) return;
  debugChatResourceMode = '';
  await openDebugChatResources(mode);
}

async function connectDebugChatMcp(addonId) {
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/mcp/connect`, {
      method: 'POST', body: JSON.stringify({ addon: addonId }),
    });
    toast('MCP connected.');
    await refreshDebugChatResources('mcp');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function disconnectDebugChatMcp(addonId) {
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/mcp/${encodeURIComponent(addonId)}`, { method: 'DELETE' });
    toast('MCP disconnected.');
    await refreshDebugChatResources('mcp');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function registerDebugChatMcp() {
  const name = document.getElementById('debug-chat-mcp-name')?.value?.trim();
  const command = document.getElementById('debug-chat-mcp-command')?.value?.trim();
  if (!name || !command) { toast('Enter an MCP name and command.'); return; }
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/mcp`, {
      method: 'POST', body: JSON.stringify({ name, command }),
    });
    toast('MCP provider registered.');
    await refreshDebugChatResources('mcp');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function enableDebugChatSkill(skillId) {
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/skills/${encodeURIComponent(skillId)}/enable`, {
      method: 'POST', body: JSON.stringify({ parameters: {} }),
    });
    toast('Skill enabled for this project.');
    await refreshDebugChatResources('skills');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function disableDebugChatSkill(skillId) {
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/skills/${encodeURIComponent(skillId)}`, { method: 'DELETE' });
    toast('Skill disabled for this project.');
    await refreshDebugChatResources('skills');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function addDebugChatSkill() {
  const name = document.getElementById('debug-chat-skill-name')?.value?.trim();
  const description = document.getElementById('debug-chat-skill-description')?.value?.trim() || '';
  const content = document.getElementById('debug-chat-skill-content')?.value?.trim();
  if (!name || !content) { toast('Skill name and content are required.'); return; }
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/skills`, {
      method: 'POST',
      body: JSON.stringify({ name, description, content, enable: true, parameters: {} }),
    });
    toast('Custom skill added.');
    await refreshDebugChatResources('skills');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

async function deleteDebugChatSkill(skillId) {
  try {
    await api(`/projects/${encodeURIComponent(activeServiceId)}/agent/skills/${encodeURIComponent(skillId)}?purge=1`, {
      method: 'DELETE',
    });
    toast('Custom skill deleted.');
    await refreshDebugChatResources('skills');
  } catch (error) { toast(normalizeFetchError(error.message)); }
}

function warmProjectAgent(projectId) {
  if (!projectId) return;
  void api(`/projects/${projectId}/agent/warm`, { method: 'POST' })
    .then((result) => {
      if (
        result.ok
        && result.status === 'warming'
        && activeServiceId === projectId
        && activeSvcTab === 'debug-chat'
        && !debugChatBusy
        && !debugChatSendInFlight
      ) {
        debugChatIdleStatus = 'Warming agent…';
        setDebugChatActivity(debugChatIdleStatus);
      }
    })
    .catch(() => {});
}

let modelOptionsLoadedAt = 0;
const MODEL_OPTIONS_TTL_MS = 300000;

// The chat picker must be usable without first visiting the Models tab.
async function ensureDebugChatModelOptions() {
  const select = document.getElementById('debug-chat-profile');
  const hasModels = Boolean(select?.querySelector('option[data-custom-model]'));
  if (hasModels && Date.now() - modelOptionsLoadedAt < MODEL_OPTIONS_TTL_MS) return;
  if (await loadAvailableModels()) modelOptionsLoadedAt = Date.now();
}

async function openDebugChatTab() {
  if (!activeServiceId) return;
  const projectId = activeServiceId;
  warmProjectAgent(projectId);
  void ensureDebugChatModelOptions();
  const projectChanged = debugChatLoadedProjectId !== activeServiceId;
  if (projectChanged) {
    clearDebugChatRequestWatchdog();
    debugChatActiveRequestId = '';
    debugChatSendInFlight = false;
    setDebugChatBusy(false);
    await loadDebugChatHistory(activeServiceId);
    debugChatLoadedProjectId = activeServiceId;
  } else {
    await syncDebugChatHistory(activeServiceId);
  }
  startAgentActivityStream(activeServiceId);
  await updateDebugChatAgentStatus();
}

function formatAgentChatError(res) {
  if (!res) return 'Unknown error';
  const parts = [res.message, res.error].filter(Boolean);
  if (res.status_code) parts.push(`HTTP ${res.status_code}`);
  return parts.join(' — ') || 'Unknown error';
}

async function sendDebugChatMessage() {
  const input = document.getElementById('debug-chat-input');
  const message = String(input?.value || '').trim();
  if (!message) return;
  if (!activeServiceId) {
    toast('Open a project before using agent chat.');
    return;
  }
  if (debugChatReplayingHistory) {
    toast('The conversation is still loading. Try again in a moment.');
    return;
  }
  if (debugChatBusy || debugChatSendInFlight) {
    toast('The agent is still working. Keep drafting, or stop the current response first.');
    input?.focus();
    return;
  }

  debugChatSendInFlight = true;
  updateDebugChatControls();
  setDebugChatActivity('Sending…');
  // A new user turn always starts in the Main lane.
  setDebugChatLane('main');
  hideDebugChatEmpty('main');
  debugChatLastUserMessage = message;
  appendDebugChatBubble({
    event_type: 'user_message',
    title: 'You',
    detail: message,
  });
  scrollDebugChatToBottom(true, 'main');
  setDebugChatTyping(true);

  const profile = await getDebugChatProfile();
  const sentMessage = message;
  if (input) {
    input.value = '';
    input.dispatchEvent(new Event('input'));
  }
  let chatOk = false;
  let acceptedAsync = false;
  try {
    const body = { message: sentMessage };
    // Omit model_profile for auto so backend heuristic routing can pick nano/base/pro.
    if (profile && profile !== 'auto') {
      body.model_profile = profile;
    }
    const res = await api(`/projects/${activeServiceId}/agent/chat`, {
      method: 'POST',
      body: JSON.stringify(body),
    });
    chatOk = !!res.ok;
    if (!res.ok) {
      appendDebugChatBubble({
        event_type: 'request_failed',
        title: 'Request failed',
        detail: formatAgentChatError(res),
        payload: {
          error: res.error || 'agent_request_failed',
          message: res.message || formatAgentChatError(res),
          retry_message: sentMessage,
        },
      });
      toast(formatAgentChatError(res));
      setDebugChatTyping(false);
      debugChatSendInFlight = false;
      setDebugChatBusy(false);
      setDebugChatActivity('Request failed', '', 'circle-alert');
      dismissDebugChatActivitySoon();
    } else if (res.request_id && (res.status === 'accepted' || !res.reply)) {
      acceptedAsync = true;
      debugChatSendInFlight = false;
      if (debugChatTerminalRequestIds.has(res.request_id)) {
        // Fast failures (for example a missing provider key) can reach the
        // activity stream before this POST response. Do not re-lock a turn
        // that the stream has already finished.
        debugChatActiveRequestId = '';
        setDebugChatTyping(false);
        setDebugChatBusy(false);
      } else {
        debugChatActiveRequestId = res.request_id;
        setDebugChatBusy(true);
        setDebugChatActivity('Working…', `${profile === 'auto' ? 'auto-routed' : profile} · thinking and building`);
        armDebugChatRequestWatchdog(activeServiceId, res.request_id);
      }
    } else if (res.reply) {
      await syncDebugChatHistory(activeServiceId);
      const messagesEl = getDebugChatMessagesEl('main');
      const assistants = messagesEl?.querySelectorAll('.debug-chat-bubble.debug-chat-assistant:not(.debug-chat-typing)');
      const lastBody = assistants?.[assistants.length - 1]?.querySelector('.debug-chat-bubble-body')?.textContent || '';
      if (!lastBody || (!lastBody.includes(res.reply) && !res.reply.includes(lastBody))) {
        appendDebugChatBubble({
          event_type: 'assistant_message',
          title: 'Assistant',
          detail: res.reply,
        });
      }
      setDebugChatTyping(false);
      debugChatSendInFlight = false;
      setDebugChatBusy(false);
      setDebugChatActivity('Response ready', '', 'check-circle-2');
      dismissDebugChatActivitySoon();
    } else {
      throw new Error('The agent accepted the connection but returned no response or request id.');
    }
  } catch (e) {
    appendDebugChatBubble({
      event_type: 'request_failed',
      title: 'Request failed',
      detail: normalizeFetchError(e.message),
      payload: {
        error: 'network_error',
        retry_message: sentMessage,
        reconnect: true,
      },
    });
    toast('Error: ' + normalizeFetchError(e.message));
    setDebugChatTyping(false);
    debugChatSendInFlight = false;
    if (debugChatActiveRequestId) {
      setDebugChatBusy(true);
      setDebugChatActivity(
        'Checking response',
        'The request may still be running; reconnecting to recover its activity',
        'wifi',
      );
    } else {
      setDebugChatBusy(false);
      setDebugChatActivity('Request failed', '', 'circle-alert');
      dismissDebugChatActivitySoon();
    }
    if (!debugChatActiveRequestId && input && !String(input.value || '').trim()) {
      input.value = sentMessage;
      input.dispatchEvent(new Event('input'));
    }
  } finally {
    debugChatSendInFlight = false;
    if (!acceptedAsync && !chatOk && !debugChatActiveRequestId) {
      setDebugChatTyping(false);
      setDebugChatBusy(false);
    }
    updateDebugChatControls();
    scrollDebugChatToBottom();
  }
}

function logLineClass(text) {
  const t = (text || '').toLowerCase();
  if (/error|failed|fatal|denied|exit code [1-9]/.test(t)) return 'log-err';
  if (/✓|success|deployed|running|complete|started/.test(t)) return 'log-ok';
  if (/warn|deprecated|notice/.test(t)) return 'log-warn';
  if (/step \d|docker|building|clone|pull|===/.test(t)) return 'log-info';
  return 'log-dim';
}

function appendLogLine(container, text, type) {
  if (!container || !text) return;
  const line = document.createElement('div');
  line.className = `log-line ${logLineClass(text)}`;
  if (type === 'build') line.classList.add('log-build');
  if (type === 'container') line.classList.add('log-container');
  line.textContent = text;
  container.appendChild(line);
  if (logsAutoScroll) container.scrollTop = container.scrollHeight;
}

function clearLogPanel(container) {
  if (container) container.innerHTML = '';
}

function renderLogText(container, text) {
  if (!container) return;
  clearLogPanel(container);
  if (!text || text === 'No logs yet.') {
    appendLogLine(container, 'No deploy logs yet.', 'log-dim');
    return;
  }
  text.split('\n').forEach(line => appendLogLine(container, line));
}

function setLogsLiveIndicator(live) {
  const dot = document.getElementById('svc-logs-live');
  if (dot) dot.classList.toggle('live', !!live);
}

function setPreviewLogsLiveIndicator(live) {
  const dot = document.getElementById('svc-preview-logs-live');
  if (dot) dot.classList.toggle('live', !!live);
}

async function loadLogSnapshot(projectId, targetEl) {
  if (!targetEl) return;
  try {
    const res = await api(`/projects/${projectId}/logs?lines=1000`);
    renderLogText(targetEl, res.logs);
  } catch (e) {
    clearLogPanel(targetEl);
    appendLogLine(targetEl, 'Could not load logs: ' + e.message, 'log-err');
  }
}

function startLogStream(projectId, targetEl, { liveOnly = true, clearFirst = false } = {}) {
  stopLogStream();
  if (!targetEl) return;
  if (clearFirst) clearLogPanel(targetEl);

  const hint = document.getElementById('svc-log-hint');
  let wasDeploying = true;

  const params = new URLSearchParams();
  if (liveOnly) params.set('live', '1');
  const qs = params.toString();
  const url = `${API}/projects/${projectId}/logs/stream${qs ? '?' + qs : ''}`;
  logStream = new EventSource(url);
  setLogsLiveIndicator(true);
  logStream.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.text) appendLogLine(targetEl, msg.text, msg.type);
    } catch { /* ping */ }
  };
  logStream.onerror = () => {
    appendLogLine(targetEl, '[stream disconnected — showing saved logs]', 'log-warn');
    setLogsLiveIndicator(false);
    stopLogStream();
    loadLogSnapshot(projectId, targetEl);
  };

  deployPollTimer = setInterval(async () => {
    await loadProjects();
    const p = projects.find(x => x.id === projectId);
    if (!p || activeServiceId !== projectId) return;
    updateServiceStatusDot(p);
    if (p.status === 'deploying') {
      wasDeploying = true;
      if (hint) hint.textContent = 'Live deployment stream';
      return;
    }
    if (wasDeploying) {
      wasDeploying = false;
      if (hint) hint.textContent = 'Deployment finished';
      await loadLogSnapshot(projectId, targetEl);
      stopLogStream();
      await loadProjects();
      const refreshed = projects.find(x => x.id === projectId);
      if (refreshed && activeServiceId === projectId) {
        renderServiceDashboard(refreshed, false);
      }
    }
  }, 2000);
}

const BREADCRUMBS = {
  dashboard: 'Projects',
  'new-service': 'Create Project',
  service: 'Project',
  sycord: 'Sycord',
  'server-swarm': 'Server Swarm',
  users: 'Users',
  logs: 'Logs',
  ai: 'AI',
  settings: 'Settings',
};

const CONTEXT_LABELS = {
  'non-conected': 'non-conected',
  xwf: 'xwf',
};

function getContext() {
  return localStorage.getItem(CONTEXT_STORAGE) || 'non-conected';
}

function setContext(ctx) {
  appContext = ctx === 'xwf' ? 'xwf' : 'non-conected';
  localStorage.setItem(CONTEXT_STORAGE, appContext);
  applyContext();
}

function applyContext() {
  const label = document.getElementById('context-label');
  const sycordNav = document.getElementById('nav-sycord');
  if (label) label.textContent = CONTEXT_LABELS[appContext] || 'non-conected';
  if (sycordNav) sycordNav.classList.toggle('hidden', appContext !== 'xwf');
  document.querySelectorAll('.context-option').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.context === appContext);
  });
  refreshIcons();
}

function renderLoadDots(filled, max = 5) {
  const el = document.getElementById('load-dots');
  if (!el) return;
  el.innerHTML = Array.from({ length: max }, (_, i) =>
    `<span class="load-dot${i < filled ? ' on' : ''}"></span>`
  ).join('');
}

function renderLoadStats(sys) {
  const statsEl = document.getElementById('load-stats');
  if (!statsEl || !sys) return;
  const cpu = typeof sys.cpu_percent === 'number' ? `${Math.round(sys.cpu_percent)}% cpu` : '— cpu';
  const ram = sys.ram_label || (sys.ram_used_mb ? `${sys.ram_used_mb}MB Ram` : '— Ram');
  statsEl.textContent = `${cpu} ${ram}`;
  renderLoadDots(sys.load_dots ?? 0, sys.load_dots_max ?? 5);
}

function toggleContextMenu(open) {
  const menu = document.getElementById('context-menu');
  const btn = document.getElementById('context-switcher-btn');
  if (!menu || !btn) return;
  const show = open ?? menu.classList.contains('hidden');
  menu.classList.toggle('hidden', !show);
  btn.setAttribute('aria-expanded', show ? 'true' : 'false');
}

function setBreadcrumb(text) {
  const el = document.getElementById('breadcrumb');
  if (el) el.textContent = text;
}

function openDrawer() {
  if (!window.matchMedia('(max-width: 768px)').matches) return;
  document.body.classList.add('drawer-open');
}

function closeDrawer() {
  document.body.classList.remove('drawer-open');
}

function refreshIcons(_root) {
  // Lucide used to load from a cross-origin CDN (@latest). Throws there were
  // masked by the browser as the useless message "Script error.". Keep the
  // call resilient even with the vendored same-origin build.
  try {
    if (!window.lucide || typeof lucide.createIcons !== 'function') return;
    lucide.createIcons();
  } catch (err) {
    console.warn('[Syte] lucide.createIcons failed:', err);
  }
}

function updateSidebarNav(viewName) {
  const isService = viewName === 'service';
  const navView = viewName === 'new-service' ? 'dashboard' : viewName;

  document.body.classList.toggle('nav-mode-service', isService);
  document.body.classList.toggle('nav-mode-home', !isService);

  document.getElementById('nav-block-home')?.classList.toggle('hidden', isService);
  document.getElementById('nav-block-service')?.classList.toggle('hidden', !isService);

  document.querySelectorAll('.nav-sublink[data-view]').forEach(el => {
    el.classList.toggle('active', !isService && el.dataset.view === navView);
  });
}

function updateServiceSidebarNav(p) {
  const title = document.getElementById('nav-service-title');
  const icon = document.getElementById('nav-service-icon');
  if (title) title.textContent = p ? displayTitle(p) : 'Service';
  if (icon) {
    const letter = ((p?.name || p?.domain || 'S').trim()[0] || 'S').toUpperCase();
    icon.textContent = letter;
  }
}

function toggleNavGroup(groupId) {
  const group = document.getElementById(groupId);
  if (!group) return;
  const expanded = group.classList.toggle('expanded');
  const toggle = group.querySelector('.nav-group-head');
  if (toggle) toggle.setAttribute('aria-expanded', expanded ? 'true' : 'false');
}

function showView(name) {
  if (name !== 'new-service' && name !== 'service') {
    stopLogStream();
    stopPreviewStream();
    stopAgentActivityStream();
  }
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');

  updateSidebarNav(name);

  if (name === 'users') loadTokens();
  if (name === 'dashboard') activeServiceId = null;
  if (name === 'server-swarm') renderServerSwarm();
  if (name === 'logs') renderLogsList();
  if (name === 'ai') { loadSettings(); loadAiDashboard(); loadAiDebug(); }
  if (name === 'models') { loadModelsTab(); }
  if (name === 'settings') loadSettings();
  const aiSettingsBtn = document.getElementById('ai-header-settings-btn');
  if (aiSettingsBtn) aiSettingsBtn.classList.toggle('hidden', name !== 'ai');
  if (name === 'sycord') refreshIcons();
  if (name === 'new-service') resetCreateForm();
  if (name === 'service') {
    const p = projects.find(x => x.id === activeServiceId);
    updateServiceSidebarNav(p);
    setBreadcrumb(p ? displayTitle(p) : 'Project');
  } else {
    setBreadcrumb(BREADCRUMBS[name] || 'Syte');
  }
  closeDrawer();
  refreshIcons();
}

let aiApiConfigured = { nano: false, havy: false, ultra: false };
let catalogModels = [];
let modelsTabData = null;
let modelsSubtab = 'models'; // 'models', 'playground', 'providers', 'add'

const MODEL_THINKING_LABELS = {
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  max: 'Max',
  xhigh: 'Xhigh',
};

// Group options by provider so long router catalogs stay navigable.
function appendModelOptionGroups(select, models) {
  const groups = new Map();
  models.forEach((model) => {
    const provider = model.provider || '9Router';
    if (!groups.has(provider)) groups.set(provider, []);
    groups.get(provider).push(model);
  });
  for (const [provider, rows] of groups) {
    const group = document.createElement('optgroup');
    group.label = provider;
    rows.forEach((model) => {
      const option = document.createElement('option');
      option.value = model.profile;
      option.textContent = model.name;
      option.dataset.customModel = '1';
      group.appendChild(option);
    });
    select.appendChild(group);
  }
}

// Always-present built-in profiles. These are resolved server-side by
// PROFILE_PROVIDERS and are not part of the 9Router catalog, so they must be
// added explicitly — otherwise a fresh install with no configured models and an
// unreachable router would offer nothing but "auto".
const STATIC_MODEL_PROFILES = [
  { profile: 'syra-nano', provider: 'Syte', name: 'Go · Gemini 2.5 Flash' },
  { profile: 'syra-ultra', provider: 'Syte', name: 'Air · Aliyun Qwen' },
  { profile: 'syra-havy', provider: 'Syte', name: 'Metal · Claude Sonnet 4.6' },
];

function syncCustomModelOptions(models) {
  const available = Array.isArray(models) ? models : [];
  const selectable = [...available];
  document.querySelectorAll('select[data-model-profile-select], #debug-chat-profile, #ai-test-profile, #agent-default-profile').forEach((select) => {
    const previous = select.value;

    // Remove everything except 'auto' (if present), including stale optgroups.
    select.querySelectorAll('option, optgroup').forEach((node) => {
      if (node.tagName === 'OPTION' && node.value === 'auto' && node.parentElement === select) return;
      node.remove();
    });

    appendModelOptionGroups(select, selectable);

    if (selectable.some((model) => model.profile === previous) || previous === 'auto') {
      select.value = previous;
    } else {
      const autoOption = select.querySelector('option[value="auto"]');
      if (autoOption) {
        select.value = 'auto';
      } else if (selectable.length > 0) {
        select.value = selectable[0].profile;
      }
    }
  });
}

// The chat model picker is populated from the Models tab response, which merges
// the curated catalog with the live 9Router /v1/models list. The <select> in
// index.html intentionally ships with only the "auto" option.
async function loadAvailableModels() {
  try {
    const data = await api('/models/available');
    syncCustomModelOptions(data.models);
    return data;
  } catch {
    /* provider setup may not be complete yet */
    return null;
  }
}

function modelThinkingSelect(selected = 'medium') {
  return `<select id="model-thinking" aria-label="Thinking setting">${Object.entries(MODEL_THINKING_LABELS).map(([value, label]) => `<option value="${value}" ${value === selected ? 'selected' : ''}>Thinking: ${label}</option>`).join('')}</select>`;
}

function updateBuiltInAgentModelOptions(models) {
  const builtInAgent = document.getElementById('new-feature-model');
  if (!builtInAgent) return;
  const previous = builtInAgent.value;
  builtInAgent.innerHTML = '<option value="" disabled selected>Select a model from the Models tab</option>';
  appendModelOptionGroups(builtInAgent, models);
  if (models.some((model) => model.profile === previous)) {
    builtInAgent.value = previous;
  }
}

function renderModelGroups() {
  const list = document.getElementById('model-catalog-list');
  if (!list) return;
  const query = (document.getElementById('model-search')?.value || '').trim().toLowerCase();
  const groups = new Map();
  catalogModels.filter((model) => {
    const text = `${model.provider || ''} ${model.name || ''}`.toLowerCase();
    return model.enabled && (!query || text.includes(query));
  }).forEach((model) => {
    const provider = model.provider || '9Router';
    if (!groups.has(provider)) groups.set(provider, []);
    groups.get(provider).push(model);
  });
  list.innerHTML = groups.size ? [...groups.entries()].map(([provider, models]) => `
    <section class="model-provider-group">
      <h3>${esc(provider)} <span>${models.length}</span></h3>
      <div class="model-list">${models.map((model) => `
        <div class="model-list-row ${model.enabled ? '' : 'is-disabled'}">
          <div><strong>${esc(model.name)}</strong><span class="hint">Thinking: ${esc(MODEL_THINKING_LABELS[model.thinking_level] || 'Medium')} · ${model.enabled ? 'Available to agents' : 'Disabled'}</span></div>
          <div class="model-row-actions">
            <button type="button" class="btn-pill ${model.enabled ? 'btn-ghost' : 'btn-primary'} model-toggle-btn" data-model-id="${esc(model.id)}">
              <i data-lucide="${model.enabled ? 'pause' : 'play'}"></i><span>${model.enabled ? 'Disable' : 'Enable'}</span>
            </button>
            <button type="button" class="btn-pill btn-ghost model-delete-btn" data-model-id="${esc(model.id)}" title="Delete model">
              <i data-lucide="trash-2"></i><span>Delete</span>
            </button>
          </div>
        </div>`).join('')}</div>
    </section>`).join('') : '<p class="hint">No models match this search.</p>';
  refreshIcons();
}

function renderModelsTab(data) {
  const content = document.getElementById('models-content');
  if (!content) return;
  const provider = data.provider || {};
  const models = Array.isArray(data.models) ? data.models : [];
  modelsTabData = data;
  catalogModels = models;
  syncCustomModelOptions(data.available_models);
  updateBuiltInAgentModelOptions(models.filter((model) => model.enabled));
  
  // Segmented tabs: Models, Playground, Providers, Add Model
  const tabBar = `<div class="models-tabs segmented-control" role="tablist" aria-label="Models">
    <button type="button" class="models-tab ${modelsSubtab === 'models' ? 'is-active' : ''}" data-models-subtab="models" role="tab" aria-selected="${modelsSubtab === 'models'}">Models</button>
    <button type="button" class="models-tab ${modelsSubtab === 'playground' ? 'is-active' : ''}" data-models-subtab="playground" role="tab" aria-selected="${modelsSubtab === 'playground'}">Playground</button>
    <button type="button" class="models-tab ${modelsSubtab === 'providers' ? 'is-active' : ''}" data-models-subtab="providers" role="tab" aria-selected="${modelsSubtab === 'providers'}">Providers</button>
    <button type="button" class="models-tab ${modelsSubtab === 'add' ? 'is-active' : ''}" data-models-subtab="add" role="tab" aria-selected="${modelsSubtab === 'add'}">Add Model</button>
  </div>`;
  
  let tabContent = '';
  
  if (modelsSubtab === 'playground') {
    const options = (data.models || []).filter((model) => model.enabled).map((model) => `<option value="${esc(model.profile)}">${esc(model.provider || '9Router')} · ${esc(model.name)}</option>`).join('');
    tabContent = `<div class="model-setup-card model-playground-card">
      <h2>Model playground</h2>
      <p class="hint block">Send a short, tool-free prompt to an enabled model without starting an agent.</p>
      <div class="form-group"><label for="playground-model">Model</label><select id="playground-model" ${options ? '' : 'disabled'}><option value="">${options ? 'Select a model' : 'Enable a model first'}</option>${options}</select></div>
      <div class="form-group"><label for="playground-prompt">Prompt</label><textarea id="playground-prompt" rows="5" placeholder="Ask this model anything…" ${options ? '' : 'disabled'}></textarea></div>
      <div class="svc-panel-actions"><button type="button" class="btn-pill btn-primary" id="run-model-playground-btn" ${options ? '' : 'disabled'}><i data-lucide="play"></i><span>Run prompt</span></button></div>
      <div id="model-playground-result" class="model-playground-result hidden" aria-live="polite"></div>
    </div>`;
  } else if (modelsSubtab === 'providers') {
    const providersList = (data.providers || []).map(p => `
      <div class="provider-config-card">
        <div class="provider-info">
          <h3>${esc(p.name)}</h3>
          <p class="hint">${p.enabled ? 'Enabled' : 'Disabled'}</p>
        </div>
        <button type="button" class="btn-pill ${p.enabled ? 'btn-ghost' : 'btn-primary'} provider-toggle-btn" data-provider-name="${esc(p.name)}">
          <i data-lucide="${p.enabled ? 'check' : 'x'}"></i><span>${p.enabled ? 'Disable' : 'Enable'}</span>
        </button>
      </div>
    `).join('');
    tabContent = `<div class="model-setup-card">
      <h2>Configured providers</h2>
      <p class="hint block">Manage which AI providers are available for model selection.</p>
      <div class="providers-list">${providersList || '<p class="hint">No providers configured yet.</p>'}</div>
    </div>`;
  } else if (modelsSubtab === 'add') {
    tabContent = `
      <div class="model-setup-card">
        <h2>Add a model</h2>
        <p class="hint block">A provider can contain a model name only once.</p>
        <div class="form-row">
          <div class="form-group"><label for="model-provider">Provider</label><input id="model-provider" placeholder="e.g. DeepSeek" autocomplete="off"></div>
          <div class="form-group"><label for="model-name">Model name</label><input id="model-name" placeholder="e.g. deepseek-r1" autocomplete="off"></div>
        </div>
        <div class="form-group"><label for="model-thinking">Default thinking</label>${modelThinkingSelect()}</div>
        <label class="model-status"><input id="model-enabled" type="checkbox" checked> Enable this model for agents</label>
        <div class="svc-panel-actions"><button type="button" class="btn-pill btn-primary" id="add-model-btn"><i data-lucide="plus"></i><span>Add model</span></button></div>
      </div>
      <div class="model-setup-card">
        <h2>Bulk add models</h2>
        <p class="hint block">Add model names for the same provider, one per line. Duplicate names are skipped.</p>
        <div class="form-group"><label for="bulk-model-provider">Provider</label><input id="bulk-model-provider" placeholder="e.g. OpenAI" autocomplete="off"></div>
        <div class="form-group"><label for="bulk-model-names">Model names</label><textarea id="bulk-model-names" rows="4" placeholder="gpt-4.1\ngpt-4.1-mini"></textarea></div>
        <div class="svc-panel-actions"><button type="button" class="btn-pill btn-ghost" id="bulk-add-models-btn"><i data-lucide="list-plus"></i><span>Bulk add</span></button></div>
      </div>`;
  } else {
    // Models tab (default)
    tabContent = `
      <div class="model-setup-card">
        <div class="model-catalog-head">
          <div><h2>Configured models</h2><p class="hint block">Enabled models are the only models available to Sarra's built-in agent and the model API.</p></div>
          <input id="model-search" type="search" placeholder="Search models or providers" aria-label="Search models">
        </div>
        <div id="model-catalog-list"></div>
      </div>`;
  }
  
  content.innerHTML = `${tabBar}${tabContent}`;
  
  if (modelsSubtab === 'models') {
    renderModelGroups();
  }
  
  refreshIcons();
}

async function loadModelsTab() {
  const content = document.getElementById('models-content');
  if (content) content.innerHTML = '<p class="hint">Loading model setup…</p>';
  try {
    renderModelsTab(await api('/models'));
  } catch (error) {
    if (content) content.innerHTML = `<p class="hint">Could not load model setup: ${esc(error.message)}</p>`;
  }
}

function aiKeySaved(id) {
  return document.getElementById(id)?.placeholder?.includes('saved');
}

function renderProviderKeyStatus(rows) {
  const el = document.getElementById('ai-provider-key-status');
  if (!el) return;
  const list = Array.isArray(rows) ? rows : [];
  if (!list.length) {
    el.innerHTML = '<div class="hint">No provider key status yet.</div>';
    return;
  }
  el.innerHTML = list.map((row) => {
    const source = row.source || 'none';
    const settingsBit = row.settings_set
      ? `settings ${esc(row.settings_hint || '••••')}`
      : 'settings —';
    const envBit = row.env_set
      ? `env ${esc(row.env_hint || '••••')}`
      : 'env —';
    const active = source === 'none'
      ? 'not set'
      : `using ${esc(source)}${row.api_key_hint ? ` · ${esc(row.api_key_hint)}` : ''}`;
    return `
      <div class="ai-env-row ai-env-row-status">
        <code>${esc(row.secret_env || '')}</code>
        <span>
          <strong>${esc(row.display_name || row.profile || '')}</strong>
          · ${esc(row.label || '')} · ${esc(row.model || '')}<br>
          <span class="hint">${settingsBit} · ${envBit} · ${active}</span>
        </span>
      </div>
    `;
  }).join('');
}

function applyAiProviderCatalog(providers) {
  const byProfile = Object.fromEntries(
    (providers || []).map((row) => [row.profile, row]),
  );
  const priceIds = {
    'syra-nano': ['agent-nano-price-in', 'agent-nano-price-out'],
    'syra-havy': ['agent-havy-price-in', 'agent-havy-price-out'],
    'syra-ultra': ['agent-ultra-price-in', 'agent-ultra-price-out'],
  };
  for (const [profile, [inId, outId]] of Object.entries(priceIds)) {
    const row = byProfile[profile];
    if (!row) continue;
    const inEl = document.getElementById(inId);
    const outEl = document.getElementById(outId);
    if (inEl && row.input_price_label) inEl.textContent = row.input_price_label;
    if (outEl && row.output_price_label) outEl.textContent = row.output_price_label;
    const card = document.querySelector(`.ai-key-card[data-profile="${profile}"]`);
    if (card) {
      const provider = card.querySelector('.ai-key-provider');
      const url = card.querySelector('.ai-key-url');
      if (provider && row.label && row.model) {
        provider.textContent = `${row.label} · ${row.model}`;
      }
      if (url && row.api_base) url.textContent = row.api_base;
    }
  }
}

function updateAiApiWarning() {
  const warn = document.getElementById('ai-api-warning');
  const profile = document.getElementById('ai-test-profile')?.value || 'syra-nano';
  const keyForProfile = {
    'syra-nano': 'agent-nano-key',
    'syra-havy': 'agent-havy-key',
    'syra-ultra': 'agent-ultra-key',
  };
  const savedForProfile = {
    'syra-nano': aiApiConfigured.nano,
    'syra-havy': aiApiConfigured.havy,
    'syra-ultra': aiApiConfigured.ultra,
  };
  const inputId = keyForProfile[profile] || '';
  const ok = savedForProfile[profile] || aiKeySaved(inputId);
  if (warn) warn.classList.toggle('hidden', ok);
  return ok;
}

function openAiSettings() {
  const sheet = document.getElementById('ai-settings-sheet');
  if (!sheet) return;
  sheet.classList.remove('hidden');
  document.body.classList.add('ai-settings-open');
  loadSettings();
  loadLegacySolarStatus();
  refreshIcons();
}

function closeAiSettings() {
  const sheet = document.getElementById('ai-settings-sheet');
  if (!sheet) return;
  sheet.classList.add('hidden');
  document.body.classList.remove('ai-settings-open');
}

function renderLegacySolarStatus(status) {
  const badge = document.getElementById('solar-status-badge');
  const message = document.getElementById('solar-status-message');
  const button = document.getElementById('delete-solar-btn');
  const state = status?.status || 'not_configured';
  if (badge) {
    badge.textContent = state.replace('_', ' ');
    badge.className = `solar-status-badge ${state}`;
  }
  if (message) message.textContent = status?.message || 'Legacy Solar is not installed.';
  if (button) {
    button.disabled = state !== 'installed';
  }
}

async function loadLegacySolarStatus() {
  try {
    const status = await api('/ai/solar/status');
    renderLegacySolarStatus(status);
  } catch (e) {
    renderLegacySolarStatus({ status: 'error', message: e.message });
  }
}

async function deleteLegacySolar() {
  const button = document.getElementById('delete-solar-btn');
  if (button) button.disabled = true;
  try {
    const status = await api('/ai/solar', { method: 'DELETE' });
    renderLegacySolarStatus(status);
    toast(status?.message || 'Legacy Solar removed from the VM');
  } catch (e) {
    renderLegacySolarStatus({ status: 'error', message: e.message });
  }
}

function setAiSettingsTab(tab) {
  if (tab !== 'providers') return;
  document.getElementById('ai-panel-providers')?.classList.remove('hidden');
  document.getElementById('ai-tab-providers')?.classList.add('is-active');
  document.getElementById('ai-tab-providers')?.setAttribute('aria-selected', 'true');
  loadLegacySolarStatus();
}

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (shouldAttachApiKey(path)) headers['X-API-Key'] = getApiKey();
  const method = (opts.method || 'GET').toUpperCase();
  const isOperatorAction = (
    (path.startsWith('/settings/syra') || path.startsWith('/tokens') || (path === '/operator/session' && method === 'DELETE'))
    && !['GET', 'HEAD', 'OPTIONS'].includes(method)
  );
  if (isOperatorAction && syraCsrfToken) headers['X-Syte-CSRF'] = syraCsrfToken;
  let res = await fetch(API + path, { credentials: 'same-origin', ...opts, headers });
  if (res.status === 401 && getApiKey()) {
    setApiKey('');
    const retryHeaders = { ...headers };
    delete retryHeaders['X-API-Key'];
    res = await fetch(API + path, { credentials: 'same-origin', ...opts, headers: retryHeaders });
  }
  if (res.status === 401 && (
    path.startsWith('/settings/syra') || path.startsWith('/tokens') || path === '/operator/session'
  )) {
    syraCsrfToken = '';
    setSyraSessionState(false);
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(normalizeFetchError(parseApiErrorPayload(err, res.statusText, res.status)));
  }
  return res.json();
}

async function restoreOperatorSession() {
  if (syraCsrfToken) return true;
  if (operatorSessionRestorePromise) return operatorSessionRestorePromise;

  let restorePromise;
  restorePromise = api('/operator/session')
    .then((session) => {
      // Do not overwrite a newly-created session if an earlier restore finishes late.
      if (operatorSessionRestorePromise !== restorePromise) return Boolean(syraCsrfToken);
      syraCsrfToken = session.authenticated ? (session.csrf_token || '') : '';
      return Boolean(syraCsrfToken);
    })
    .catch(() => {
      if (operatorSessionRestorePromise === restorePromise) syraCsrfToken = '';
      return false;
    })
    .finally(() => {
      if (operatorSessionRestorePromise === restorePromise) operatorSessionRestorePromise = null;
    });
  operatorSessionRestorePromise = restorePromise;
  return restorePromise;
}

function toast(msg) {
  const el = document.getElementById('toast');
  if (!el) return;
  const text = msg == null ? '' : String(msg);
  // Cross-origin script failures are reported as the useless "Script error."
  // Prefer a clear recovery hint over that blank message.
  el.textContent = /^script error\.?$/i.test(text.trim())
    ? 'A UI script failed while opening chat. Reload the page, then try Agent chat again.'
    : text;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 4000);
}

function parseEnv(text) {
  const env = {};
  text.split('\n').forEach(line => {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) return;
    const idx = trimmed.indexOf('=');
    if (idx > 0) env[trimmed.slice(0, idx)] = trimmed.slice(idx + 1);
  });
  return env;
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
    list = list.filter(p =>
      (p.name || '').toLowerCase().includes(q) ||
      (p.id || '').toLowerCase().includes(q) ||
      (p.domain || '').toLowerCase().includes(q)
    );
  }
  if (projectSortMode === 'name') {
    list.sort((a, b) => (a.name || '').localeCompare(b.name || ''));
  } else if (projectSortMode === 'oldest') {
    list.reverse();
  }
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
    renderLoadStats(sys);
    if (activeServiceId) {
      const p = projects.find(x => x.id === activeServiceId);
      if (p) {
        const conn = document.getElementById('svc-conn');
        if (conn) conn.textContent = hostPortLabel(p);
      }
    }
  } catch { /* offline */ }
}

function startStatsPoll() {
  if (statsPollTimer) clearInterval(statsPollTimer);
  statsPollTimer = setInterval(loadSystem, 10000);
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

function renderLogsList() {
  const list = document.getElementById('logs-project-list');
  const empty = document.getElementById('logs-empty');
  if (!list) return;
  if (!projects.length) {
    list.innerHTML = '';
    empty?.classList.remove('hidden');
    refreshIcons();
    return;
  }
  empty?.classList.add('hidden');
  list.innerHTML = projects.map(p => `
    <div class="log-row-item" onclick="openService('${p.id}')">
      <div>
        <strong>${esc(p.name)}</strong>
        <div class="hint">${esc(p.id)}</div>
      </div>
      <div class="service-meta">
        <span class="badge ${statusClass(p)}">${statusLabel(p)}</span>
        ${sslBadgeHtml(p)}
      </div>
    </div>
  `).join('');
  refreshIcons();
}

async function loadProjects(options = {}) {
  const { silent = false } = options;
  try {
    projects = await api('/projects');
    renderServices();
    updateStats();
    if (activeServiceId) {
      const p = projects.find(x => x.id === activeServiceId);
      if (p) {
        if (silent) {
          updateActiveServiceMeta(p);
        } else {
          renderServiceDashboard(p, false);
        }
      }
    }
  } catch (e) {
    console.error(e);
  }
}

function updateActiveServiceMeta(p) {
  updateServiceStatusDot(p);
  if (activeSvcTab === 'general') {
    renderQuickActions(p);
    updateServiceConnLink(p);
  } else if (activeSvcTab === 'preview') {
    renderPreviewSection(p);
  }
}

function updateServiceConnLink(p) {
  const conn = document.getElementById('svc-conn');
  if (!conn) return;
  const link = p.url || '#';
  conn.textContent = connLabel(p);
  conn.href = link;
}

function sslBadgeHtml(p) {
  const ssl = p.ssl || {};
  const badge = ssl.badge || 'http';
  const label = ssl.badge_label || 'HTTP';
  const title = [
    ssl.production?.label,
    ssl.preview?.configured ? ssl.preview.label : null,
  ].filter(Boolean).join(' · ');
  return `<span class="badge badge-ssl badge-ssl-${badge}" title="${esc(title)}">${esc(label)}</span>`;
}

function renderServices() {
  const list = document.getElementById('services-list');
  const empty = document.getElementById('empty-state');
  const visible = filteredProjects();

  if (!visible.length) {
    list.innerHTML = '';
    empty?.classList.remove('hidden');
    refreshIcons();
    return;
  }

  empty?.classList.add('hidden');
  list.innerHTML = visible.map(p => {
    const status = p.status === 'deploying' ? 'deploying' : (p.running ? 'running' : 'stopped');
    const deployLabel = p.deploy_type === 'docker' ? 'docker' : 'shell';
    return `
    <div class="project-card" onclick="openService('${p.id}')">
      <div class="project-card-head">
        <h3>${esc(p.name)}</h3>
        <span class="project-card-status ${status}" title="${status}"></span>
      </div>
      <div class="project-card-meta">
        <span class="project-card-tag">${status}</span>
        <span class="project-card-tag">${deployLabel}</span>
        ${p.port ? `<span class="project-card-tag">:${p.port}</span>` : ''}
      </div>
    </div>`;
  }).join('');
  refreshIcons();
}

function statusClass(p) {
  if (p.status === 'deploying') return 'badge-deploying';
  return p.running ? 'badge-running' : 'badge-stopped';
}

function statusLabel(p) {
  if (p.status === 'deploying') return 'deploying';
  return p.running ? 'running' : 'stopped';
}

function formatEnv(env) {
  if (!env || typeof env !== 'object') return '';
  return Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n');
}

function detectStack(p) {
  const env = p.env_vars || {};
  if (env.SYTE_STACK) return env.SYTE_STACK;
  if (p.deploy_type === 'docker') return 'nextjs';
  return 'shell';
}

function resetCreateForm() {
  selectedCreateStack = 'nextjs';
  document.querySelectorAll('.stack-card').forEach(card => {
    const on = card.dataset.stack === 'nextjs';
    card.classList.toggle('active', on);
    card.setAttribute('aria-selected', on ? 'true' : 'false');
  });
  const nameInput = document.getElementById('create-name');
  if (nameInput) nameInput.value = '';
  const startCmd = document.getElementById('create-start-cmd');
  if (startCmd) startCmd.value = '';
  const buildCmd = document.getElementById('create-build-cmd');
  if (buildCmd) buildCmd.value = '';
  document.querySelectorAll('.create-accordion-head[data-accordion]').forEach(head => {
    head.setAttribute('aria-expanded', 'false');
    const panel = document.getElementById(head.dataset.accordion);
    panel?.classList.add('hidden');
  });
  const placeholder = document.getElementById('create-log-placeholder');
  const logPanel = document.getElementById('deploy-log-panel');
  placeholder?.classList.remove('hidden');
  logPanel?.classList.add('hidden');
  if (logPanel) clearLogPanel(logPanel);
  refreshIcons();
}

function selectCreateStack(stack) {
  selectedCreateStack = stack;
  document.querySelectorAll('.stack-card').forEach(card => {
    const on = card.dataset.stack === stack;
    card.classList.toggle('active', on);
    card.setAttribute('aria-selected', on ? 'true' : 'false');
  });
}

function toggleCreateAccordion(head) {
  const panelId = head.dataset.accordion;
  if (!panelId) return;
  const panel = document.getElementById(panelId);
  if (!panel) return;
  const open = panel.classList.toggle('hidden');
  head.setAttribute('aria-expanded', open ? 'false' : 'true');
  refreshIcons();
}

function displayTitle(p) {
  return p.domain || p.name || 'service';
}

function hostPortLabel(p) {
  if (!p.domain) {
    try {
      const u = new URL(p.url);
      if (u.host) return u.host;
    } catch { /* */ }
  }
  if (serverPublicIp && p.port) return `${serverPublicIp}:${p.port}`;
  if (p.port) return `:${p.port}`;
  return '—';
}

function connLabel(p) {
  return hostPortLabel(p);
}

function switchSvcTab(tab) {
  const allowed = ['general', 'env', 'logs', 'preview', 'debug-chat'];
  if (!allowed.includes(tab)) tab = 'general';
  const prevTab = activeSvcTab;
  activeSvcTab = tab;
  document.querySelectorAll('.nav-sublink[data-svc-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.svcTab === tab);
  });
  document.querySelectorAll('.svc-tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.svcPanel === tab);
  });
  if (tab === 'debug-chat') {
    void openDebugChatTab().catch((err) => {
      console.error('[Syte][chat] Failed to open agent chat:', err);
      toast(normalizeFetchError(err?.message) || 'Could not open agent chat');
    });
  } else if (prevTab === 'debug-chat') {
    stopAgentActivityStream();
  }
  if (tab === 'preview') {
    previewTabActive = true;
    const p = projects.find(x => x.id === activeServiceId);
    if (p) renderPreviewSection(p);
  } else if (prevTab === 'preview') {
    previewTabActive = false;
    stopPreviewPoll();
    stopPreviewStream();
  }
  if (window.matchMedia('(max-width: 768px)').matches) closeDrawer();
  refreshIcons();
}

function renderQuickActions(p) {
  const el = document.getElementById('svc-quick-actions');
  if (!el) return;
  el.innerHTML = `
    <button type="button" class="svc-action-btn svc-action-deploy" onclick="serviceDeploy('${p.id}')">
      <i data-lucide="rocket"></i><span>Deploy</span>
    </button>
    ${p.running
      ? `<button type="button" class="svc-action-btn svc-action-secondary" onclick="serviceAction('${p.id}','stop')"><i data-lucide="square"></i><span>Stop server</span></button>`
      : `<button type="button" class="svc-action-btn svc-action-secondary" onclick="serviceAction('${p.id}','start')"><i data-lucide="play"></i><span>Start server</span></button>`
    }
  `;
}

function openServiceEditModal(p) {
  const modal = document.getElementById('svc-edit-modal');
  const nameInput = document.getElementById('svc-edit-name-input');
  const domainInput = document.getElementById('svc-edit-domain-input');
  if (!modal || !nameInput || !domainInput) return;
  nameInput.value = p.name || '';
  domainInput.value = p.domain || '';
  modal.classList.remove('hidden');
  modal.dataset.projectId = p.id;
  nameInput.focus();
}

function closeServiceEditModal() {
  document.getElementById('svc-edit-modal')?.classList.add('hidden');
}

function updateServiceStatusDot(p) {
  const dot = document.getElementById('svc-status-dot');
  if (!dot) return;
  dot.classList.remove('running', 'stopped', 'deploying');
  if (p.status === 'deploying') dot.classList.add('deploying');
  else if (p.running) dot.classList.add('running');
  else dot.classList.add('stopped');
  dot.title = statusLabel(p);
}

function renderStackBadge(p) {
  const stack = detectStack(p);
  const meta = STACK_META[stack] || STACK_META.docker;
  const iconEl = document.getElementById('svc-stack-icon');
  const labelEl = document.getElementById('svc-stack-label');
  if (iconEl) {
    iconEl.textContent = meta.icon;
    iconEl.className = `svc-stack-icon ${meta.cls}`.trim();
  }
  if (labelEl) labelEl.textContent = meta.label;
}

function setPreviewFrameSrc(frame, url) {
  if (!frame || !url) return;
  if (lastPreviewFrameSrc === url) return;
  lastPreviewFrameSrc = url;
  frame.src = url;
}

function renderServiceEmbed(p) {
  renderPreviewSection(p);
}

function openService(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  activeServiceId = id;
  warmProjectAgent(id);
  activeSvcTab = 'general';
  switchSvcTab('general');
  updateServiceSidebarNav(p);
  renderServiceDashboard(p, true);
  showView('service');
}

function renderServiceDashboard(p, resetLogs) {
  document.getElementById('svc-title').textContent = displayTitle(p);
  updateServiceSidebarNav(p);
  updateServiceStatusDot(p);
  updateServiceConnLink(p);

  const branchLabel = document.getElementById('svc-branch-label');
  if (branchLabel) branchLabel.textContent = p.branch || 'main';

  const uuidPill = document.getElementById('svc-uuid-pill');
  if (uuidPill) uuidPill.textContent = `UUID: ${p.id}`;

  const envInput = document.getElementById('svc-env-input');
  if (envInput) envInput.value = formatEnv(p.env_vars);

  if (activeSvcTab === 'general') {
    renderQuickActions(p);
    renderStackBadge(p);
    document.getElementById('svc-info-body').innerHTML = `
    <div class="info-cell"><span>status</span><strong>${esc(statusLabel(p))}</strong></div>
    <div class="info-cell"><span>type</span><strong>${esc(p.deploy_type || 'shell')}</strong></div>
    <div class="info-cell"><span>port</span><strong>${p.port}</strong></div>
    <div class="info-cell"><span>stack</span><strong>${esc(detectStack(p))}</strong></div>
    <div class="info-cell"><span>production ssl</span><strong>${esc(p.ssl?.production?.label || '—')}</strong></div>
    <div class="info-cell"><span>preview ssl</span><strong>${esc(p.ssl?.preview?.label || '—')}</strong></div>
    <div class="info-cell full"><span>domain</span><span>${esc(p.domain || '—')}</span></div>
    <div class="info-cell full"><span>url</span><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></div>
    <div class="info-cell full"><span>git</span><span>${esc(p.git_url || '—')}</span></div>
    <div class="info-cell"><span>branch</span><strong>${esc(p.branch || 'main')}</strong></div>
    <div class="info-cell"><span>start cmd</span><span>${esc(p.start_command || '—')}</span></div>
    <div class="info-cell full svc-danger-row">
      <button type="button" class="btn-pill btn-danger btn-sm" onclick="serviceAction('${p.id}','delete')">
        <i data-lucide="trash-2"></i><span>Remove project</span>
      </button>
    </div>
  `;
  }

  if (activeSvcTab === 'preview') {
    renderStackBadge(p);
    renderPreviewSection(p);
  }

  const logsEl = document.getElementById('svc-live-logs');
  const hint = document.getElementById('svc-log-hint');
  if (resetLogs) {
    if (p.status === 'deploying') {
      if (hint) hint.textContent = 'Live deployment stream';
      switchSvcTab('logs');
      loadLogSnapshot(p.id, logsEl).then(() => {
        startLogStream(p.id, logsEl, { liveOnly: true, clearFirst: false });
      });
    } else {
      if (hint) hint.textContent = 'Deployment log';
      stopLogStream();
      loadLogSnapshot(p.id, logsEl);
    }
  } else if (activeSvcTab === 'general') {
    updateServiceStatusDot(p);
    renderQuickActions(p);
  }

  document.getElementById('svc-env-save-btn').onclick = () => saveServiceEnv(p.id);
  document.getElementById('svc-edit-btn').onclick = () => openServiceEditModal(p);
  refreshIcons();
}

function iframeHintLine(iframe) {
  if (!iframe) return '';
  if (iframe.all_ok) return ' · iframe embed OK';
  const failed = (iframe.items || []).filter((i) => !i.ok);
  if (!failed.length) return '';
  return ` · iframe issue: ${failed[0].label}`;
}

function renderPreviewSection(p) {
  if (activeSvcTab !== 'preview') return;

  const actions = document.getElementById('svc-preview-actions');
  const frame = document.getElementById('svc-preview-frame');
  const placeholder = document.getElementById('svc-preview-placeholder');
  const hint = document.getElementById('svc-preview-hint');
  const domainEl = document.getElementById('svc-preview-domain');
  const logsEl = document.getElementById('svc-preview-logs');
  const logsWrap = document.getElementById('svc-preview-logs-wrap');
  if (!actions) return;

  if (domainEl) {
    domainEl.textContent = p.preview_domain || 'Assigning…';
  }

  const live = p.preview_running && p.preview_ready;
  const showFrame = p.preview_running && p.preview_url;
  actions.innerHTML = `
    <button class="btn-pill btn-primary" onclick="servicePreviewStart('${p.id}')">
      <i data-lucide="play"></i><span>Start preview</span>
    </button>
    <button class="btn-pill btn-ghost" onclick="servicePreviewStop('${p.id}')">
      <i data-lucide="square"></i><span>Stop</span>
    </button>
    ${p.preview_url ? `<a class="btn-pill btn-ghost" href="${esc(p.preview_url)}" target="_blank"><i data-lucide="external-link"></i><span>Open</span></a>` : ''}
    ${live ? '<span class="badge-live">live</span>' : ''}
  `;

  if (showFrame) {
    if (frame && placeholder) {
      const frameSrc = live
        ? ((p.preview_tls_ok !== false && p.preview_domain_url)
          ? p.preview_domain_url
          : (p.preview_fetch_url || p.preview_url))
        : (p.preview_fetch_url || p.preview_url);
      setPreviewFrameSrc(frame, frameSrc);
      frame.classList.remove('hidden');
      placeholder.classList.add('hidden');
    }
    const urlLabel = p.preview_domain
      ? `${p.preview_domain_url || p.preview_url}`
      : p.preview_url;
    hint.textContent = live
      ? `Live — ${urlLabel}${p.preview_domain && p.preview_tls_ok !== false ? ' (HTTPS)' : ''}${iframeHintLine(p.iframe)}`
      : `Connecting — ${urlLabel || `port ${p.preview_port || '…'}`}${iframeHintLine(p.iframe)}`;
    if (p.preview_tls_hint) {
      hint.textContent += ` — ${p.preview_tls_hint}`;
    }
    logsWrap?.classList.remove('hidden');
    if (p.preview_running && !previewStream) startPreviewLogStream(p.id, logsEl);
    if (p.preview_running && !live) startPreviewPoll(p.id);
  } else {
    lastPreviewFrameSrc = '';
    if (frame) {
      frame.classList.add('hidden');
      frame.removeAttribute('src');
    }
    placeholder?.classList.remove('hidden');
    hint.textContent = 'Fast dev server with hot reload — stays running while you use Debug Chat (auto-stops after 1 hour idle)';
    logsWrap?.classList.add('hidden');
    stopPreviewStream();
    stopPreviewPoll();
  }
  refreshIcons();
}

function startPreviewLogStream(projectId, targetEl) {
  stopPreviewStream();
  if (!targetEl) return;
  const params = new URLSearchParams({ live: '1' });
  previewStream = new EventSource(`${API}/projects/${projectId}/preview/logs/stream?${params}`);
  setPreviewLogsLiveIndicator(true);
  previewStream.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.text) appendLogLine(targetEl, msg.text, msg.type);
    } catch { /* ping */ }
  };
  previewStream.onerror = () => setPreviewLogsLiveIndicator(false);
}

function startPreviewPoll(projectId) {
  if (previewPollTimer || activeSvcTab !== 'preview') return;
  previewPollTimer = setInterval(async () => {
    if (activeSvcTab !== 'preview' || activeServiceId !== projectId) {
      stopPreviewPoll();
      return;
    }
    try {
      const st = await api(`/projects/${projectId}/preview/status?quick=1`);
      const p = projects.find(x => x.id === projectId);
      if (p && activeServiceId === projectId) {
        renderPreviewSection({ ...p, ...st, iframe: st.iframe });
        if (st.preview_ready) stopPreviewPoll();
      }
    } catch { /* */ }
  }, 2000);
}

function stopPreviewPoll() {
  if (previewPollTimer) {
    clearInterval(previewPollTimer);
    previewPollTimer = null;
  }
}

async function servicePreviewStart(id) {
  const logsEl = document.getElementById('svc-preview-logs');
  const hint = document.getElementById('svc-preview-hint');
  switchSvcTab('preview');
  hint.textContent = 'Starting preview…';
  if (logsEl) clearLogPanel(logsEl);
  try {
    const res = await api(`/projects/${id}/preview/start`, { method: 'POST' });
    toast(res.message || 'preview started');
    const idx = projects.findIndex(x => x.id === id);
    if (idx >= 0) {
      projects[idx] = { ...projects[idx], ...res };
    }
    const p = projects.find(x => x.id === id);
    if (p) renderPreviewSection({ ...p, iframe: res.iframe });
    if (logsEl) startPreviewLogStream(id, logsEl);
    startPreviewPoll(id);
  } catch (e) {
    hint.textContent = 'preview failed';
    if (logsEl) appendLogLine(logsEl, 'Error: ' + e.message, 'log-err');
    toast('Error: ' + e.message);
  }
}

async function servicePreviewStopQuiet(id) {
  try {
    await api(`/projects/${id}/preview/stop`, { method: 'POST' });
    lastPreviewFrameSrc = '';
    const idx = projects.findIndex(x => x.id === id);
    if (idx >= 0) {
      projects[idx] = { ...projects[idx], preview_running: false, preview_ready: false, preview_status: 'stopped' };
    }
    const p = projects.find(x => x.id === id);
    if (p && activeServiceId === id && activeSvcTab === 'preview') {
      renderPreviewSection(p);
    }
  } catch { /* ignore */ }
}

async function servicePreviewStop(id) {
  try {
    const res = await api(`/projects/${id}/preview/stop`, { method: 'POST' });
    toast(res.message || 'preview stopped');
    stopPreviewStream();
    await loadProjects();
    const p = projects.find(x => x.id === id);
    if (p) renderPreviewSection(p);
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

async function serviceDeploy(id) {
  const logsEl = document.getElementById('svc-live-logs');
  const hint = document.getElementById('svc-log-hint');
  switchSvcTab('logs');
  if (hint) hint.textContent = 'Live deployment stream';
  clearLogPanel(logsEl);
  appendLogLine(logsEl, 'Issuing deploy…', 'log-info');
  startLogStream(id, logsEl, { liveOnly: true, clearFirst: false });
  try {
    const res = await api(`/projects/${id}/deploy`, { method: 'POST' });
    appendLogLine(logsEl, res.message || 'Deploy started in background', 'log-info');
    toast(res.message || 'deploy started');
    await loadProjects();
  } catch (e) {
    appendLogLine(logsEl, 'Error: ' + e.message, 'log-err');
    toast('Error: ' + e.message);
    await loadLogSnapshot(id, logsEl);
  }
}

async function serviceAction(id, action) {
  try {
    if (action === 'delete') {
      if (!confirm('Remove this service? Workspace data is kept on disk.')) return;
      const res = await api(`/projects/${id}`, { method: 'DELETE' });
      toast(res.message);
      activeServiceId = null;
      showView('dashboard');
    } else if (action === 'start') {
      const logsEl = document.getElementById('svc-live-logs');
      const hint = document.getElementById('svc-log-hint');
      switchSvcTab('logs');
      if (hint) hint.textContent = 'Starting service…';
      clearLogPanel(logsEl);
      appendLogLine(logsEl, 'Starting service…', 'log-info');
      startLogStream(id, logsEl, { liveOnly: true, clearFirst: false });
      const res = await api(`/projects/${id}/start`, { method: 'POST' });
      appendLogLine(logsEl, res.message || 'Start issued', res.project?.running ? 'log-ok' : 'log-err');
      toast(res.message);
      await loadProjects();
      await loadLogSnapshot(id, logsEl);
    } else {
      const res = await api(`/projects/${id}/${action}`, { method: 'POST' });
      toast(res.message);
      await loadProjects();
    }
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

async function saveServiceEdit() {
  const modal = document.getElementById('svc-edit-modal');
  const id = modal?.dataset.projectId;
  if (!id) return;

  const name = document.getElementById('svc-edit-name-input')?.value.trim();
  let domain = document.getElementById('svc-edit-domain-input')?.value.trim() || '';
  domain = domain.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');

  if (!name) return toast('Name is required');

  try {
    await api(`/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name }),
    });
    if (domain) {
      const email = (await api('/settings')).admin_email;
      await api(`/projects/${id}/domain`, {
        method: 'POST',
        body: JSON.stringify({ domain, email: email || 'admin@localhost' }),
      });
    }
    toast('Project updated');
    closeServiceEditModal();
    await loadProjects();
    const p = projects.find(x => x.id === id);
    if (p) {
      renderServiceDashboard(p, false);
      setBreadcrumb(displayTitle(p));
    }
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

async function saveServiceEnv(id) {
  const text = document.getElementById('svc-env-input')?.value || '';
  const env_vars = parseEnv(text);
  try {
    const res = await api(`/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ env_vars }),
    });
    toast(res.message || 'Environment saved');
    await loadProjects();
    const p = projects.find(x => x.id === id);
    if (p) renderServiceDashboard(p, false);
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

document.getElementById('create-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('deploy-btn');
  const name = document.getElementById('create-name')?.value.trim();
  if (!name) return toast('Enter a project name');

  btn.disabled = true;
  btn.querySelector('span').textContent = 'Creating…';

  const startCmd = document.getElementById('create-start-cmd')?.value.trim() || null;
  const buildCmd = document.getElementById('create-build-cmd')?.value.trim() || null;
  const env_vars = {};
  if (buildCmd) env_vars.SYTE_BUILD_COMMAND = buildCmd;

  const logPanel = document.getElementById('deploy-log-panel');
  const logPlaceholder = document.getElementById('create-log-placeholder');
  logPlaceholder?.classList.add('hidden');
  logPanel?.classList.remove('hidden');
  clearLogPanel(logPanel);

  try {
    const res = await api('/projects', {
      method: 'POST',
      body: JSON.stringify({
        name,
        stack: selectedCreateStack,
        start_command: startCmd,
        env_vars,
      }),
    });
    appendLogLine(logPanel, res.message || 'Project created', 'log-info');
    toast(`Deploying: ${res.project.name}`);
    await loadProjects();
    openService(res.project.id);
    switchSvcTab('logs');
    const logsEl = document.getElementById('svc-live-logs');
    loadLogSnapshot(res.project.id, logsEl).then(() => {
      startLogStream(res.project.id, logsEl, { liveOnly: true, clearFirst: false });
    });
  } catch (err) {
    appendLogLine(logPanel, 'Error: ' + err.message, 'log-err');
    toast('Deploy failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.querySelector('span').textContent = 'Create & Deploy';
  }
});

document.getElementById('stack-picker')?.addEventListener('click', (e) => {
  const card = e.target.closest('.stack-card');
  if (!card?.dataset.stack) return;
  selectCreateStack(card.dataset.stack);
});

document.querySelectorAll('.create-accordion-head[data-accordion]').forEach(head => {
  head.addEventListener('click', () => toggleCreateAccordion(head));
});

document.getElementById('create-name-focus')?.addEventListener('click', () => {
  document.getElementById('create-name')?.focus();
});

document.getElementById('save-server-btn')?.addEventListener('click', async () => {
  try {
    const res = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({
        public_ip: document.getElementById('set-ip').value || null,
        admin_email: document.getElementById('set-email').value || null,
      }),
    });
    toast(res.messages?.join(' ') || 'saved');
    await loadSystem();
  } catch (e) {
    toast('Error: ' + e.message);
  }
});

document.getElementById('save-ai-settings-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('save-ai-settings-btn');
  const nanoKey = document.getElementById('agent-nano-key')?.value?.trim() || '';
  const havyKey = document.getElementById('agent-havy-key')?.value?.trim() || '';
  const ultraKey = document.getElementById('agent-ultra-key')?.value?.trim() || '';
  const internalSecret = document.getElementById('syra-internal-secret')?.value?.trim() || '';
  const maxRaw = document.getElementById('agent-max-count')?.value?.trim();
  const tursoDatabaseUrl = document.getElementById('turso-database-url')?.value?.trim() || '';
  const tursoAuthToken = document.getElementById('turso-auth-token')?.value?.trim() || '';
  const litellmDatabaseUrl = document.getElementById('litellm-database-url')?.value?.trim() || '';
  const body = {
    agent_default_model_profile: document.getElementById('agent-default-profile')?.value || 'syra-nano',
  };
  if (nanoKey) body.agent_syra_nano_api_key = nanoKey;
  if (havyKey) body.agent_syra_havy_api_key = havyKey;
  if (ultraKey) {
    if (ultraKey.toLowerCase().startsWith('sk-or-')) {
      return toast('syra-ultra needs an Aliyun key (sk-sp-… Token Plan or Model Studio sk-…), not OpenRouter sk-or-…');
    }
    body.agent_syra_ultra_api_key = ultraKey;
  }
  if (internalSecret) body.syra_internal_secret = internalSecret;
  if (maxRaw) body.agent_max_count = parseInt(maxRaw, 10);
  if (document.getElementById('turso-database-url')) body.turso_database_url = tursoDatabaseUrl;
  if (tursoAuthToken) body.turso_auth_token = tursoAuthToken;
  if (litellmDatabaseUrl) body.litellm_database_url = litellmDatabaseUrl;
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const res = await api('/settings', { method: 'PUT', body: JSON.stringify(body) });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'Provider settings saved');
    if (nanoKey) document.getElementById('agent-nano-key').value = '';
    if (havyKey) document.getElementById('agent-havy-key').value = '';
    if (ultraKey) document.getElementById('agent-ultra-key').value = '';
    if (internalSecret) document.getElementById('syra-internal-secret').value = '';
    if (tursoAuthToken) document.getElementById('turso-auth-token').value = '';
    if (litellmDatabaseUrl) document.getElementById('litellm-database-url').value = '';
    await loadSettings();
    await loadAiDashboard();
    closeAiSettings();
  } catch (e) {
    toast('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save provider settings';
  }
});

document.getElementById('save-domain-btn')?.addEventListener('click', async () => {
  let domain = document.getElementById('set-domain').value.trim();
  domain = domain.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  document.getElementById('set-domain').value = domain;
  const email = document.getElementById('set-email').value.trim();
  if (!domain) return toast('enter a domain for the web gui');
  if (!email || !email.includes('@') || email.endsWith('@localhost')) {
    return toast('set a valid admin email first');
  }
  const btn = document.getElementById('save-domain-btn');
  btn.disabled = true;
  btn.textContent = 'applying…';
  try {
    const res = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({ gui_domain: domain, admin_email: email }),
    });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'domain applied');
    await loadSystem();
  } catch (e) {
    toast('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'apply domain';
  }
});

document.getElementById('save-preview-domain-btn')?.addEventListener('click', async () => {
  let zone = document.getElementById('set-preview-domain').value.trim();
  zone = zone.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  document.getElementById('set-preview-domain').value = zone;
  const cfToken = document.getElementById('set-cf-token')?.value?.trim() || '';
  const btn = document.getElementById('save-preview-domain-btn');
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const body = { preview_base_domain: zone || '' };
    if (cfToken) body.cloudflare_api_token = cfToken;
    const res = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify(body),
    });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'preview settings saved');
    if (cfToken) document.getElementById('set-cf-token').value = '';
    await loadSettings();
  } catch (e) {
    toast('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save preview zone';
  }
});

document.getElementById('save-cf-token-btn')?.addEventListener('click', async () => {
  const cfToken = document.getElementById('set-cf-token')?.value?.trim() || '';
  if (!cfToken) return toast('paste your Cloudflare API token first');
  const btn = document.getElementById('save-cf-token-btn');
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const res = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({ cloudflare_api_token: cfToken }),
    });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'Cloudflare token saved');
    document.getElementById('set-cf-token').value = '';
    await loadSettings();
  } catch (e) {
    toast('Error: ' + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Save Cloudflare token';
  }
});

document.getElementById('update-syte-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('update-syte-btn');
  const box = document.getElementById('update-result');
  btn.disabled = true;
  btn.textContent = 'updating…';
  try {
    const res = await api('/system/update', { method: 'POST' });
    if (box) {
      box.textContent = `${res.message}\n\nRestarting Syte…`;
      box.classList.remove('hidden');
    }
    toast('Update complete — restarting…');
    btn.textContent = 'restarting…';
    await waitForServerRestart();
    toast('Syte is back online');
    location.reload();
  } catch (e) {
    toast('Update failed: ' + e.message);
    if (box) {
      box.textContent = e.message;
      box.classList.remove('hidden');
    }
    btn.disabled = false;
    btn.textContent = 'Update Syte';
  }
});

document.getElementById('new-feature-run-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('new-feature-run-btn');
  const input = document.getElementById('new-feature-input');
  const status = document.getElementById('new-feature-status');
  const result = document.getElementById('new-feature-result');
  const logPanel = document.getElementById('new-feature-log');
  const model = document.getElementById('new-feature-model')?.value || '';
  const apiKey = document.getElementById('new-feature-api-key')?.value || '';
  const message = input?.value.trim();
  if (!message) return toast('Enter an instruction for the agent');
  if (!model.startsWith('9router:')) return toast('Choose an enabled model from the Models tab');
  if (btn) { btn.disabled = true; btn.querySelector('span').textContent = 'Running…'; }
  if (status) { status.textContent = 'Agent starting…'; status.classList.remove('hidden'); }
  if (result) result.classList.add('hidden');
  if (logPanel) logPanel.classList.remove('hidden');
  try {
    const res = await api('/settings/new-feature/agent', {
      method: 'POST',
      body: JSON.stringify({ message, model_profile: model, request_api_key: apiKey || null }),
    });
    if (logPanel) logPanel.innerHTML = '';
    if (res.ok) {
      if (result) {
        result.innerHTML = `<strong>Agent response:</strong><br><pre>${esc(res.reply || res.response || '')}</pre>`;
        result.classList.remove('hidden');
      }
      if (status) status.textContent = `Done — current version: v${res.current_version || '?'}${res.triggered_update ? ' — auto-update triggered' : ''}`;
      toast('Agent finished' + (res.triggered_update ? ' — update started' : ''));
      if (res.triggered_update) {
        await loadUpdateInfo();
        await loadSettings();
      }
    } else {
      if (result) {
        result.innerHTML = `<strong>Error:</strong> ${esc(res.message || res.error || 'Unknown error')}`;
        result.classList.remove('hidden');
      }
      if (status) status.textContent = 'Agent failed';
      toast('Agent failed: ' + (res.message || res.error));
    }
  } catch (e) {
    if (status) status.textContent = 'Agent request failed';
    toast('Agent request failed: ' + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.querySelector('span').textContent = 'Run agent'; }
  }
});

document.getElementById('new-feature-clear-btn')?.addEventListener('click', () => {
  const input = document.getElementById('new-feature-input');
  const apiKey = document.getElementById('new-feature-api-key');
  const status = document.getElementById('new-feature-status');
  const result = document.getElementById('new-feature-result');
  const logPanel = document.getElementById('new-feature-log');
  if (input) input.value = '';
  if (apiKey) apiKey.value = '';
  if (status) { status.textContent = ''; status.classList.add('hidden'); }
  if (result) result.classList.add('hidden');
  if (logPanel) logPanel.classList.add('hidden');
});

async function waitForServerRestart(maxAttempts = 30, intervalMs = 2000) {
  await new Promise((resolve) => setTimeout(resolve, 3000));
  for (let attempt = 0; attempt < maxAttempts; attempt += 1) {
    try {
      const res = await fetch('/api/system', { cache: 'no-store' });
      if (res.ok) return;
    } catch {
      /* server still restarting */
    }
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  }
  throw new Error('Syte did not come back online after restart. Check server logs.');
}

async function loadSettings() {
  try {
    const s = await api('/settings');
    void loadLegacySolarStatus();
    const ip = document.getElementById('set-ip');
    const email = document.getElementById('set-email');
    const domain = document.getElementById('set-domain');
    const previewDomain = document.getElementById('set-preview-domain');
    const previewExample = document.getElementById('preview-host-example');
    const previewDnsHint = document.getElementById('preview-dns-hint');
    const cfToken = document.getElementById('set-cf-token');
    const cfStatus = document.getElementById('cf-token-status');
    const agentDefaultProfile = document.getElementById('agent-default-profile');
    const agentMaxCount = document.getElementById('agent-max-count');
    const agentRuntimeStatus = document.getElementById('agent-runtime-status');
    const syraInternalSecret = document.getElementById('syra-internal-secret');
    const tursoDatabaseUrl = document.getElementById('turso-database-url');
    const tursoAuthToken = document.getElementById('turso-auth-token');
    const litellmDatabaseUrl = document.getElementById('litellm-database-url');
    if (ip && s.public_ip) ip.value = s.public_ip;
    if (email && s.admin_email) email.value = s.admin_email;
    if (domain && s.gui_domain) domain.value = s.gui_domain.replace(/^https?:\/\//i, '');
    if (previewDomain) {
      previewDomain.value = (s.preview_base_domain || s.preview_zone || '').replace(/^https?:\/\//i, '');
      previewDomain.placeholder = s.preview_zone
        ? `default: ${s.preview_zone}`
        : 'e.g. sycord.site';
    }
    if (previewExample && s.preview_zone) {
      previewExample.textContent = `previewa-myapp.${s.preview_zone}`;
    }
    if (previewDnsHint && s.preview_dns_hint) {
      previewDnsHint.textContent = s.preview_dns_hint;
    }
    if (cfToken) {
      cfToken.placeholder = s.cloudflare_api_token_set
        ? 'token saved — enter new value to replace'
        : 'Zone DNS Edit token for *.sycord.site';
    }
    if (cfStatus && s.cloudflare_tls) {
      const cf = s.cloudflare_tls;
      const parts = [];
      if (cf.token_configured) parts.push('token saved');
      if (cf.wildcard_tls_enabled) parts.push('wildcard TLS on');
      if (cf.caddy_plugin_installed) parts.push('Caddy plugin OK');
      else if (cf.token_configured) parts.push('Caddy plugin needed');
      if (cf.systemd_env_configured) parts.push('systemd env OK');
      if (cf.ready) parts.push('ready');
      cfStatus.textContent = parts.length ? parts.join(' · ') : 'No Cloudflare token configured';
      cfStatus.classList.remove('hidden');
      if (cf.hints?.length) {
        cfStatus.textContent += ` — ${cf.hints.join(' ')}`;
      }
    }
    const defaultProfile = s.agent_default_model_profile || 'syra-nano';
    if (agentDefaultProfile) agentDefaultProfile.value = defaultProfile;
    if (window.customElements?.whenDefined) await customElements.whenDefined('sl-select');
    const debugChatProfile = document.getElementById('debug-chat-profile');
    // Keep chat on auto for cost-efficient routing unless the user already picked a profile.
    if (debugChatProfile && !debugChatProfile.dataset.userSelected) {
      debugChatProfile.value = 'auto';
    }
    if (agentMaxCount && s.agent_max_count) agentMaxCount.value = s.agent_max_count;
    if (agentMaxCount && !s.agent_max_count) agentMaxCount.placeholder = '50';
    const keyFields = [
      ['agent-nano-key', 'agent-nano-key-hint', s.agent_syra_nano_api_key_set, 'Gemini Go key saved', 'Gemini API key required'],
      ['agent-ultra-key', 'agent-ultra-key-hint', s.agent_syra_ultra_api_key_set, 'Aliyun Air key saved (sk-sp- Token Plan or Model Studio sk-)', 'Aliyun Token Plan sk-sp-… key required'],
      ['agent-havy-key', 'agent-havy-key-hint', s.agent_syra_havy_api_key_set, 'VyceAI Metal key saved', 'VyceAI API key required'],
    ];
    keyFields.forEach(([inputId, hintId, saved, savedText, requiredText]) => {
      const input = document.getElementById(inputId);
      const hint = document.getElementById(hintId);
      if (input) {
        input.placeholder = saved
          ? 'key saved — enter new value to replace'
          : 'required';
      }
      if (hint) hint.textContent = saved ? savedText : requiredText;
    });
    applyAiProviderCatalog(s.ai_providers || []);
    renderProviderKeyStatus(s.provider_keys || []);
    aiApiConfigured = {
      nano: Boolean(s.agent_syra_nano_api_key_set),
      havy: Boolean(s.agent_syra_havy_api_key_set),
      ultra: Boolean(s.agent_syra_ultra_api_key_set),
    };
    await loadAvailableModels();
    if (syraInternalSecret) {
      syraInternalSecret.placeholder = s.syra_internal_secret_set
        ? 'internal secret saved — enter new value to replace'
        : 'shared secret for sycord.com -> Syte';
    }
    if (tursoDatabaseUrl && s.turso_database_url) tursoDatabaseUrl.value = s.turso_database_url;
    if (tursoAuthToken) {
      tursoAuthToken.placeholder = s.turso_auth_token_set
        ? 'auth token saved — enter new value to replace'
        : 'turso auth token';
    }
    if (litellmDatabaseUrl) {
      litellmDatabaseUrl.placeholder = s.litellm_database_url_set
        ? 'custom PostgreSQL URL saved — enter new value to replace'
        : 'optional — postgresql://user:password@host:5432/database';
    }
    if (agentRuntimeStatus) {
      const parts = [];
      parts.push(`default: ${defaultProfile}`);
      parts.push(s.agent_syra_nano_api_key_set ? 'Go key saved' : 'no Go key');
      parts.push(s.agent_syra_ultra_api_key_set ? 'Air key saved' : 'no Air key');
      parts.push(s.agent_syra_havy_api_key_set ? 'Metal key saved' : 'no Metal key');
      parts.push(s.syra_internal_secret_set ? 'internal secret saved' : 'no internal secret');
      parts.push(s.turso_configured ? 'Turso configured' : 'Turso not configured');
      parts.push(s.litellm_database_url_set ? 'LiteLLM custom PostgreSQL' : 'LiteLLM managed PostgreSQL');
      agentRuntimeStatus.textContent = parts.join(' · ');
    }
    const directUrl = document.getElementById('direct-url');
    const guiUrl = document.getElementById('gui-url');
    const ver = document.getElementById('syte-version');
    if (directUrl && s.direct_url) directUrl.textContent = s.direct_url;
    if (guiUrl) guiUrl.textContent = s.domain_url || 'not configured';
    if (ver && s.version) ver.textContent = 'v' + s.version;
    const newFeatureVer = document.getElementById('new-feature-version');
    if (newFeatureVer && s.version) newFeatureVer.textContent = 'v' + s.version;
    updateAiApiWarning();
    await loadUpdateInfo();
  } catch { /* */ }
}

async function loadUpdateInfo() {
  const el = document.getElementById('syte-update-source');
  if (!el) return;
  try {
    const info = await api('/system/update-info');
    const label = info.label || info.branch || 'main';
    const prLink = info.pr_url
      ? ` — <a href="${esc(info.pr_url)}" target="_blank" rel="noopener">view PR</a>`
      : '';
    const workBranch = info.work_branch ? ` → <code>${esc(info.work_branch)}</code>` : '';
    let bootstrap = '';
    if (Array.isArray(info.bootstrap_commands) && info.bootstrap_commands.length) {
      bootstrap = `<details class="update-bootstrap"><summary>Manual upgrade (SSH)</summary><pre>${esc(info.bootstrap_commands.join('\n'))}</pre></details>`;
    }
    el.innerHTML = `Will pull <strong>${esc(label)}</strong>${workBranch}${prLink}${bootstrap}`;
  } catch {
    el.textContent = 'Will pull latest open GitHub PR (fallback: main)';
  }
}

function renderAiTestProjects() {
  const sel = document.getElementById('ai-test-project');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">Select project…</option>' +
    projects.map(p => `<option value="${esc(p.id)}">${esc(displayTitle(p))}</option>`).join('');
  if (current) sel.value = current;
}

async function loadAiDashboard() {
  renderAiTestProjects();
  try {
    const d = await api('/agent_dashboard');
    const set = (id, val) => { const el = document.getElementById(id); if (el) el.textContent = val; };
    set('ai-stat-online', d.agents_online ?? 0);
    set('ai-stat-incoming', d.incoming_requests_30d ?? 0);
    set('ai-stat-failed', d.failed_relationships_30d ?? 0);
    if (d.dpfa) {
      set('ai-dpfa-pct', `${d.dpfa.percent}%`);
      const fill = document.getElementById('ai-dpfa-fill');
      if (fill) fill.style.width = `${d.dpfa.percent}%`;
    }
    if (d.mnoa) {
      set('ai-mnoa-pct', `${d.mnoa.percent}%`);
      const fill = document.getElementById('ai-mnoa-fill');
      if (fill) fill.style.width = `${d.mnoa.percent}%`;
    }
    const onboard = d.onboarding || {};
    const doneCount = ['internal_api', 'ai_models', 'provider', 'cloud_runtime'].filter(k => onboard[k]).length;
    const badge = document.getElementById('ai-onboard-badge');
    if (badge) badge.textContent = `${doneCount}/4`;
    document.querySelectorAll('#ai-checklist li').forEach(li => {
      const step = li.dataset.step;
      li.classList.toggle('done', !!onboard[step]);
    });
    const hint = document.getElementById('ai-onboard-hint');
    const keysConfigured = [onboard.ai_models, aiApiConfigured.nano, aiApiConfigured.havy, aiApiConfigured.ultra].some(Boolean);
    if (hint) {
      hint.textContent = onboard.complete
        ? 'Ready for sycord.com agent requests'
        : keysConfigured
          ? 'Add keys for other profiles in settings if needed'
          : 'Tap settings (top right) to add model API keys';
    }
    updateAiApiWarning();
    if (!updateAiApiWarning()) openAiSettings();
  } catch { /* */ }
  refreshIcons();
}

function renderAiDebug(report) {
  const el = document.getElementById('ai-debug-content');
  if (!el) return;
  if (!report) {
    el.innerHTML = '<p class="hint">No debug data.</p>';
    return;
  }

  const steps = (report.steps || []).map(step => `
    <div class="ai-debug-step ${step.ok ? 'ok' : 'fail'}">
      <span class="ai-debug-step-icon">${step.ok ? '✓' : '✗'}</span>
      <div>
        <strong>${esc(step.label)}</strong>
        <div class="ai-debug-step-detail">${esc(step.detail || '')}</div>
      </div>
    </div>
  `).join('');

  const profiles = (report.profiles || []).map(p => {
    const probes = (p.probes || []).map(pr => `
      <tr>
        <td>${esc(pr.step)}</td>
        <td>${esc(pr.method || '')}</td>
        <td><span class="ai-debug-badge ${pr.ok ? 'ok' : 'fail'}">${pr.ok ? 'ok' : 'fail'}</span></td>
        <td>${pr.status_code ?? '—'}</td>
        <td>${pr.latency_ms ?? '—'}ms</td>
        <td>${esc(pr.error || (pr.body_preview || '').slice(0, 120))}</td>
      </tr>
    `).join('');
    const source = p.source || (p.api_key_set ? 'settings' : 'none');
    const profileHints = (p.hints || []).map(h => `<div class="ai-debug-hint">${esc(h)}</div>`).join('');
    return `
      <div class="ai-debug-block">
        <strong>${esc(p.profile)}</strong> · ${esc(p.label)} · key: ${p.api_key_set ? esc(p.api_key_hint) : 'missing'}
        <div class="hint">${esc(p.api_base)} · ${esc(p.model)} · source=${esc(source)} · env ${p.env_set ? esc(p.env_hint || 'set') : '—'}</div>
        ${profileHints}
        <table class="ai-debug-table">
          <thead><tr><th>Probe</th><th>Method</th><th>Result</th><th>HTTP</th><th>Time</th><th>Detail</th></tr></thead>
          <tbody>${probes || '<tr><td colspan="6">No probes — key not available</td></tr>'}</tbody>
        </table>
      </div>
    `;
  }).join('');

  const envs = (report.provider_envs || report.secrets?.vars_set || []).map((row) => `
    <div class="ai-debug-env-row">
      <code>${esc(row.name || '')}</code>
      <span>${row.set ? `set · ${esc(row.hint || '••••')}${row.used ? ' · in use' : ''}` : 'not set in process env'}</span>
    </div>
  `).join('');

  const hints = (report.hints || []).map(h => `<div class="ai-debug-hint">${esc(h)}</div>`).join('');
  const agent = report.agent || {};
  const config = report.config || {};

  el.innerHTML = `
    <div class="hint">Generated ${esc(report.generated_at || '')} · active profile <strong>${esc(report.active_profile || '')}</strong></div>
    <div class="ai-debug-steps">${steps || '<p class="hint">No steps recorded.</p>'}</div>
    <div class="ai-debug-block">
      <strong>Process env (provider keys)</strong>
      ${envs || '<div class="hint">No provider env status.</div>'}
    </div>
    ${hints ? `<div class="ai-debug-hints">${hints}</div>` : ''}
    <div><strong>Provider probes (all profiles)</strong>${profiles}</div>
    <div>
      <strong>Agent runtime</strong>
      <div class="hint">status ${esc(agent.agent_status || '—')} · Cloud runtime ${report.cloud_agent_runtime?.installed ? esc(report.cloud_agent_runtime.version || 'installed') : 'missing'}</div>
      ${agent.serve_command ? `<div class="hint">serve cmd: <code>${esc(agent.serve_command)}</code></div>` : ''}
      ${agent.agent_last_error ? `<div class="ai-debug-hint">${esc(agent.agent_last_error)}</div>` : ''}
    </div>
    ${config.snippet ? `<div><strong>runtime.json</strong><pre class="ai-debug-config">${esc(config.snippet)}</pre></div>` : ''}
    ${report.logs_tail ? `<div><strong>Agent logs (tail)</strong><pre class="ai-debug-logs">${esc(report.logs_tail)}</pre></div>` : ''}
  `;
}

async function loadAiDebug(report) {
  const panel = document.getElementById('ai-debug-panel');
  const content = document.getElementById('ai-debug-content');
  if (!content) return;
  if (report) {
    renderAiDebug(report);
    if (panel) panel.open = true;
    return;
  }
  const uuid = document.getElementById('ai-test-project')?.value;
  const profile = document.getElementById('ai-test-profile')?.value;
  if (!uuid) {
    content.innerHTML = '<p class="hint">Select a project to run diagnostics.</p>';
    return;
  }
  content.innerHTML = '<p class="hint">Running diagnostics…</p>';
  try {
    const q = profile ? `?profile=${encodeURIComponent(profile)}` : '';
    const res = await api(`/projects/${uuid}/agent/debug${q}`);
    renderAiDebug(res);
    if (panel) panel.open = true;
  } catch (e) {
    content.innerHTML = `<p class="hint">Debug failed: ${esc(e.message)}</p>`;
  }
}

function esc(s) {
  if (!s) return '';
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

async function loadTokens() {
  const list = document.getElementById('tokens-list');
  if (!list) return;
  if (!await restoreOperatorSession()) {
    list.innerHTML = '<p class="hint">Unlock Syra to manage API keys.</p>';
    return;
  }
  try {
    const res = await api('/tokens');
    if (!res.tokens?.length) {
      list.innerHTML = '<p class="hint">no tokens yet</p>';
      return;
    }
    list.innerHTML = res.tokens.map(t => `
      <div class="token-row">
        <div><strong>${esc(t.name)}</strong><span class="hint"> ${esc(t.prefix)}…</span></div>
        <button class="btn-pill btn-ghost btn-sm" onclick="revokeToken('${t.id}')">revoke</button>
      </div>
    `).join('');
    refreshIcons();
  } catch {
    list.innerHTML = '<p class="hint">could not load tokens</p>';
  }
}

async function revokeToken(id) {
  if (!confirm('Revoke this API token?')) return;
  if (!await restoreOperatorSession()) {
    return toast('Unlock Syra to manage API keys');
  }
  try {
    await api(`/tokens/${id}`, { method: 'DELETE' });
    toast('token revoked');
    await loadTokens();
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

document.getElementById('create-token-btn')?.addEventListener('click', async () => {
  const name = document.getElementById('token-name')?.value || 'default';
  if (!await restoreOperatorSession()) {
    return toast('Unlock Syra to manage API keys');
  }
  try {
    const res = await api('/tokens', { method: 'POST', body: JSON.stringify({ name }) });
    const box = document.getElementById('new-token-box');
    box.textContent = `Token (copy for external API use — not needed for the web GUI):\n${res.token}`;
    box.classList.remove('hidden');
    toast('token created — copy it now');
    await loadTokens();
  } catch (e) {
    toast('Error: ' + e.message);
  }
});

loadSystem();
loadProjects();
loadSettings();
loadTokens();
appContext = getContext();
applyContext();
startStatsPoll();
refreshIcons();

// Surface real errors instead of the blank cross-origin "Script error." toast/dialog.
// Same-origin lucide is vendored under /static/vendor/; remaining CDN risk is Shoelace.
window.addEventListener('error', (event) => {
  const msg = String(event?.message || event?.error?.message || '');
  if (!msg) return;
  if (/^script error\.?$/i.test(msg.trim())) {
    const src = String(event?.filename || event?.target?.src || '');
    console.error(
      '[Syte] Cross-origin script error (CDN). Details are masked by the browser.',
      src ? `src=${src}` : '(no filename)',
      event,
    );
  }
});
window.addEventListener('unhandledrejection', (event) => {
  console.error('[Syte] Unhandled promise rejection:', event?.reason);
});

document.getElementById('context-switcher-btn')?.addEventListener('click', (e) => {
  e.stopPropagation();
  const menu = document.getElementById('context-menu');
  toggleContextMenu(menu?.classList.contains('hidden'));
});

document.querySelectorAll('.context-option').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.stopPropagation();
    setContext(btn.dataset.context);
    toggleContextMenu(false);
  });
});

document.addEventListener('click', () => toggleContextMenu(false));

document.getElementById('debug-chat-mcp')?.addEventListener('click', () => openDebugChatResources('mcp'));
document.getElementById('debug-chat-skills')?.addEventListener('click', () => openDebugChatResources('skills'));
document.getElementById('debug-chat-resources-close')?.addEventListener('click', closeDebugChatResources);

document.getElementById('project-filter')?.addEventListener('input', (e) => {
  projectFilterText = e.target.value;
  renderServices();
});

document.querySelectorAll('.nav-sublink[data-view]').forEach(el => {
  if (el.tagName === 'A') return;
  el.addEventListener('click', () => showView(el.dataset.view));
});
document.getElementById('nav-group-main-toggle')?.addEventListener('click', () => toggleNavGroup('nav-group-main'));
document.getElementById('nav-service-head')?.addEventListener('click', () => showView('dashboard'));
document.getElementById('sidebar-service-tabs')?.addEventListener('click', (e) => {
  const btn = e.target.closest('.nav-sublink[data-svc-tab]');
  if (!btn?.dataset.svcTab) return;
  switchSvcTab(btn.dataset.svcTab);
});
document.getElementById('debug-chat-send')?.addEventListener('click', sendDebugChatMessage);
document.getElementById('debug-chat-cancel')?.addEventListener('click', cancelDebugChatRequest);
document.getElementById('debug-chat-messages')?.addEventListener(
  'scroll', () => updateDebugChatScrollState('main'), { passive: true },
);
document.getElementById('debug-chat-messages-sub')?.addEventListener(
  'scroll', () => updateDebugChatScrollState('sub'), { passive: true },
);
document.getElementById('debug-chat-tabs')?.addEventListener('click', (ev) => {
  const tab = ev.target.closest('.debug-chat-tab');
  if (!tab) return;
  setDebugChatLane(tab.dataset.chatLane === 'sub' ? 'sub' : 'main');
});
// Double-clicking the brain opens the session failure log (failed tasks, tools,
// requests and subagents). A single click stays a no-op so the sync indicator
// keeps behaving like an indicator.
document.getElementById('debug-chat-brain')?.addEventListener('dblclick', (ev) => {
  ev.preventDefault();
  toggleDebugChatFailureLog();
});
document.getElementById('debug-chat-failures-close')?.addEventListener('click', closeDebugChatFailureLog);
document.getElementById('debug-chat-failures-refresh')?.addEventListener('click', () => {
  void loadDebugChatFailures(activeServiceId);
});
document.getElementById('debug-chat-failures-scope')?.addEventListener('change', () => {
  void loadDebugChatFailures(activeServiceId);
});
document.getElementById('debug-chat-failures-clear')?.addEventListener('click', async () => {
  if (!activeServiceId) return;
  const scope = debugChatFailureScope();
  try {
    await api(
      `/projects/${activeServiceId}/agent/failures?session=${encodeURIComponent(scope)}`,
      { method: 'DELETE' },
    );
  } catch (err) {
    toast(normalizeFetchError(err.message));
    return;
  }
  updateDebugChatFailureBadge(0);
  await loadDebugChatFailures(activeServiceId);
});
document.getElementById('debug-chat-profile')?.addEventListener('change', () => {
  const select = document.getElementById('debug-chat-profile');
  if (select) select.dataset.userSelected = '1';
  if (debugChatBusy) {
    const modelEl = document.getElementById('debug-chat-activity-model');
    const profile = document.getElementById('debug-chat-profile')?.value || '';
    const short = ({ auto: 'auto', 'syra-nano': 'Go · Gemini 2.5 Flash', 'syra-ultra': 'Air · Aliyun Qwen', 'syra-havy': 'Metal · Claude Sonnet 4.6' })[profile] || profile;
    if (modelEl && short) {
      modelEl.hidden = false;
      modelEl.textContent = short;
    }
  }
});
function bindDebugChatComposer() {
  const input = document.getElementById('debug-chat-input');
  if (!input) return;
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      sendDebugChatMessage();
    }
  });
  input.addEventListener('input', updateDebugChatControls);
  updateDebugChatControls();
}
bindDebugChatComposer();

document.getElementById('sidebar-toggle')?.addEventListener('click', openDrawer);
document.getElementById('sidebar-backdrop')?.addEventListener('click', closeDrawer);

document.addEventListener('click', async (event) => {
  const modelsTabButton = event.target.closest('[data-models-subtab]');
  if (modelsTabButton && modelsTabData) {
    const subTab = modelsTabButton.dataset.modelsSubtab;
    if (['models', 'playground', 'providers', 'add'].includes(subTab)) {
      modelsSubtab = subTab;
      renderModelsTab(modelsTabData);
    }
    return;
  }
  const providerButton = event.target.closest('#save-model-provider-btn');
  const addButton = event.target.closest('#add-model-btn');
  const bulkButton = event.target.closest('#bulk-add-models-btn');
  const toggleButton = event.target.closest('.model-toggle-btn');
  const playgroundButton = event.target.closest('#run-model-playground-btn');
  const deleteButton = event.target.closest('.model-delete-btn');
  if (!providerButton && !addButton && !bulkButton && !toggleButton && !playgroundButton && !deleteButton) return;
  const button = providerButton || addButton || bulkButton || toggleButton || playgroundButton || deleteButton;
  button.disabled = true;
  try {
    if (providerButton) {
      const apiKey = document.getElementById('model-provider-api-key')?.value?.trim();
      if (!apiKey) throw new Error('Paste your 9Router API key first.');
      await api('/models/provider', { method: 'PUT', body: JSON.stringify({ api_key: apiKey }) });
      toast('9Router API key saved.');
    } else if (addButton) {
      const modelName = document.getElementById('model-name')?.value?.trim();
      const provider = document.getElementById('model-provider')?.value?.trim();
      const thinkingLevel = document.getElementById('model-thinking')?.value || 'medium';
      if (!modelName) throw new Error('Enter a model name.');
      if (!provider) throw new Error('Enter a provider name.');
      await api('/models', {
        method: 'POST',
        body: JSON.stringify({
          model_name: modelName,
          provider,
          thinking_level: thinkingLevel,
          enabled: Boolean(document.getElementById('model-enabled')?.checked),
        }),
      });
      toast('Model added.');
    } else if (bulkButton) {
      const names = (document.getElementById('bulk-model-names')?.value || '').split('\n').map((name) => name.trim()).filter(Boolean);
      const provider = document.getElementById('bulk-model-provider')?.value?.trim();
      if (!names.length) throw new Error('Enter one or more model names.');
      if (!provider) throw new Error('Enter a provider name.');
      const thinkingLevel = document.getElementById('model-thinking')?.value || 'medium';
      await api('/models/bulk', { method: 'POST', body: JSON.stringify({ models: names.map((model_name) => ({ model_name, provider, thinking_level: thinkingLevel, enabled: true })) }) });
      toast(`${names.length} models submitted.`);
    } else if (toggleButton) {
      const model = catalogModels.find((item) => item.id === toggleButton.dataset.modelId);
      if (!model) throw new Error('Model could not be found. Refresh and try again.');
      await api(`/models/${encodeURIComponent(model.id)}`, { method: 'PUT', body: JSON.stringify({ model_name: model.name, provider: model.provider || '9Router', thinking_levels: model.thinking_levels, thinking_level: model.thinking_level || 'medium', enabled: !model.enabled }) });
      toast(`Model ${model.enabled ? 'disabled' : 'enabled'}.`);
    } else if (deleteButton) {
      const model = catalogModels.find((item) => item.id === deleteButton.dataset.modelId);
      if (!model) throw new Error('Model could not be found. Refresh and try again.');
      const confirmed = confirm(`Delete model "${model.name}"? This cannot be undone.`);
      if (!confirmed) {
        button.disabled = false;
        return;
      }
      await api(`/models/${encodeURIComponent(model.id)}`, { method: 'DELETE' });
      toast('Model deleted.');
    } else if (playgroundButton) {
      const profile = document.getElementById('playground-model')?.value || '';
      const prompt = document.getElementById('playground-prompt')?.value?.trim() || '';
      if (!profile) throw new Error('Choose an enabled model.');
      if (!prompt) throw new Error('Enter a prompt.');
      const result = document.getElementById('model-playground-result');
      if (result) {
        result.textContent = 'Running…';
        result.classList.remove('hidden');
      }
      const response = await api('/models/playground', { method: 'POST', body: JSON.stringify({ model_profile: profile, prompt }) });
      if (result) result.textContent = response.response || 'The model returned no text.';
      toast('Playground response ready.');
      button.disabled = false;
    }
    if (!playgroundButton) {
      await loadModelsTab();
      await loadSettings();
    }
  } catch (error) {
    toast(`Error: ${error.message}`);
    button.disabled = false;
  }
});

document.addEventListener('input', (event) => {
  if (event.target.id === 'model-search') renderModelGroups();
});

document.getElementById('ai-header-settings-btn')?.addEventListener('click', openAiSettings);
document.getElementById('ai-settings-close')?.addEventListener('click', closeAiSettings);
document.getElementById('ai-settings-backdrop')?.addEventListener('click', closeAiSettings);
document.getElementById('ai-tab-providers')?.addEventListener('click', () => setAiSettingsTab('providers'));
document.getElementById('delete-solar-btn')?.addEventListener('click', deleteLegacySolar);

document.getElementById('ai-test-profile')?.addEventListener('change', () => {
  updateAiApiWarning();
  loadAiDebug();
});
document.getElementById('ai-test-project')?.addEventListener('change', () => loadAiDebug());
document.getElementById('ai-debug-refresh')?.addEventListener('click', (e) => {
  e.preventDefault();
  e.stopPropagation();
  loadAiDebug();
});

document.getElementById('ai-test-agent-btn')?.addEventListener('click', async () => {
  const uuid = document.getElementById('ai-test-project')?.value;
  const profile = document.getElementById('ai-test-profile')?.value;
  const statusEl = document.getElementById('ai-test-status');
  const btn = document.getElementById('ai-test-agent-btn');
  if (!uuid) return toast('select a project first');
  if (!updateAiApiWarning()) {
    toast('Add the API key for the selected profile first');
    openAiSettings();
    return;
  }
  if (statusEl) statusEl.textContent = 'Running agent test…';
  if (btn) btn.disabled = true;
  try {
    const res = await api(`/projects/${uuid}/agent/test`, {
      method: 'POST',
      body: JSON.stringify({ model_profile: profile }),
    });
    if (res.ok) {
      if (statusEl) statusEl.textContent = `Test passed — ${res.model || profile}: ${res.reply || 'ok'}`;
      toast('Agent test passed');
    } else {
      if (statusEl) statusEl.textContent = res.message || 'Test failed';
      toast(res.message || 'Test failed');
      if (res.debug) await loadAiDebug(res.debug);
      else await loadAiDebug();
    }
    await loadAiDashboard();
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
    toast('Error: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById('svc-logs-refresh')?.addEventListener('click', () => {
  if (!activeServiceId) return;
  const logsEl = document.getElementById('svc-live-logs');
  loadLogSnapshot(activeServiceId, logsEl);
});

document.getElementById('svc-logs-autoscroll')?.addEventListener('click', (e) => {
  const btn = e.currentTarget;
  logsAutoScroll = !logsAutoScroll;
  btn.classList.toggle('active', logsAutoScroll);
});

document.getElementById('svc-edit-cancel-btn')?.addEventListener('click', closeServiceEditModal);
document.getElementById('svc-edit-backdrop')?.addEventListener('click', closeServiceEditModal);
document.getElementById('svc-edit-save-btn')?.addEventListener('click', saveServiceEdit);

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeDrawer();
    closeServiceEditModal();
    closeAiSettings();
  }
});

window.addEventListener('resize', () => {
  if (!window.matchMedia('(max-width: 768px)').matches) closeDrawer();
});



// ---------------------------------------------------------------------------
// Syra / LiteLLM proxy management
// ---------------------------------------------------------------------------

async function loadSyraStatus() {
  const statusDot = document.getElementById('syra-status-dot');
  const statusLabel = document.getElementById('syra-status-label');
  const publicStatus = document.getElementById('syra-public-status');
  const apiUrl = document.getElementById('syra-api-url');
  const guiUrl = document.getElementById('syra-gui-url');
  const dnsHint = document.getElementById('syra-dns-hint');
  const startBtn = document.getElementById('syra-start-btn');
  const stopBtn = document.getElementById('syra-stop-btn');
  const restartBtn = document.getElementById('syra-restart-btn');

  try {
    const res = await api('/settings/syra/status');
    if (apiUrl && res.public_api_url) apiUrl.textContent = res.public_api_url;
    if (guiUrl && res.web_gui_url) guiUrl.textContent = res.web_gui_url;
    if (dnsHint && res.dns_hint) dnsHint.textContent = `${res.dns_hint} LiteLLM admin endpoints remain private.`;
    if (publicStatus) {
      const sslReady = res.ssl?.active;
      publicStatus.classList.toggle('is-ready', Boolean(sslReady));
      publicStatus.classList.toggle('is-pending', !sslReady);
      publicStatus.textContent = sslReady
        ? `${res.public_api_url} · HTTPS active`
        : `${res.public_api_url} · ${res.ssl?.label || 'SSL pending'}`;
    }

    if (res.running) {
      statusDot?.classList.remove('stopped', 'error');
      statusDot?.classList.add('running');
      if (statusLabel) statusLabel.textContent = res.health?.healthy ? 'Running (healthy)' : 'Running (unhealthy)';
      if (startBtn) startBtn.disabled = true;
      if (stopBtn) stopBtn.disabled = false;
      if (restartBtn) restartBtn.disabled = false;
    } else {
      statusDot?.classList.remove('running', 'error');
      statusDot?.classList.add('stopped');
      if (statusLabel) statusLabel.textContent = res.message || 'Stopped';
      if (startBtn) startBtn.disabled = false;
      if (stopBtn) stopBtn.disabled = true;
      if (restartBtn) restartBtn.disabled = true;
    }
  } catch (e) {
    statusDot?.classList.remove('running', 'stopped');
    statusDot?.classList.add('error');
    if (statusLabel) statusLabel.textContent = 'Error: ' + e.message;
    if (publicStatus) {
      publicStatus.classList.remove('is-ready');
      publicStatus.classList.add('is-pending');
      publicStatus.textContent = 'Unable to check the public API endpoint.';
    }
  }
}

async function loadSyraConfig() {
  try {
    const res = await api('/settings');
    const masterKey = document.getElementById('syra-master-key');
    const saltKey = document.getElementById('syra-salt-key');
    const agentKey = document.getElementById('syra-agent-api-key');

    if (masterKey) masterKey.placeholder = res.litellm_master_key_set ? 'saved — leave empty to keep' : 'sk-…';
    if (saltKey) saltKey.placeholder = res.litellm_salt_key_set ? 'saved — leave empty to keep' : '32-char hex string';
    if (agentKey) agentKey.placeholder = res.agent_litellm_api_key_set ? 'saved — leave empty to keep' : 'sk-…';
  } catch (e) {
    console.error('Failed to load Syra config:', e);
  }
}

function setSyraSessionState(unlocked) {
  const unlockPanel = document.getElementById('syra-unlock-panel');
  const unlockHint = document.getElementById('syra-unlock-hint');
  const lockButton = document.getElementById('syra-lock-btn');
  unlockPanel?.classList.toggle('hidden', unlocked);
  if (lockButton) lockButton.hidden = !unlocked;
  if (unlockHint && unlocked) unlockHint.textContent = 'Operator session active for this browser.';

  [
    'syra-start-btn', 'syra-stop-btn', 'syra-restart-btn', 'syra-save-config-btn',
    'syra-logs-refresh', 'syra-models-refresh', 'syra-master-key', 'syra-salt-key',
    'syra-agent-api-key',
  ].forEach((id) => {
    const element = document.getElementById(id);
    if (element) element.disabled = !unlocked;
  });
}

function syraSessionReady() {
  if (syraCsrfToken) return true;
  toast('Unlock Syra to continue');
  document.getElementById('syra-bootstrap-key')?.focus();
  return false;
}

async function unlockSyra() {
  if (window.location.protocol !== 'https:') {
    return toast('Syra unlock requires HTTPS. Open the configured GUI domain.');
  }
  const input = document.getElementById('syra-bootstrap-key');
  const button = document.getElementById('syra-unlock-btn');
  const bootstrapToken = input?.value.trim() || '';
  if (!bootstrapToken) return toast('Enter the system bootstrap API key');
  if (button) button.disabled = true;
  try {
    const session = await api('/operator/session', {
      method: 'POST',
      body: JSON.stringify({ bootstrap_token: bootstrapToken }),
    });
    operatorSessionRestorePromise = null;
    syraCsrfToken = session.csrf_token || '';
    if (!syraCsrfToken) throw new Error('Operator session was not created');
    if (input) input.value = '';
    toast('Syra unlocked for this browser');
    await initSyraTab();
  } catch (e) {
    toast('Unlock failed: ' + e.message);
  } finally {
    if (button) button.disabled = false;
  }
}

async function lockSyra() {
  const button = document.getElementById('syra-lock-btn');
  if (button) button.disabled = true;
  try {
    await api('/operator/session', { method: 'DELETE' });
    operatorSessionRestorePromise = null;
    syraCsrfToken = '';
    setSyraSessionState(false);
    toast('Syra session locked');
  } catch (e) {
    toast('Unable to lock Syra: ' + e.message);
  } finally {
    if (button) button.disabled = false;
  }
}

// Initialize Syra tab when view is shown. The bootstrap token is exchanged
// once for an HttpOnly session cookie and is never saved in JavaScript storage.
async function initSyraTab() {
  try {
    const unlocked = await restoreOperatorSession();
    setSyraSessionState(unlocked);
    if (!unlocked) {
      const statusLabel = document.getElementById('syra-status-label');
      const publicStatus = document.getElementById('syra-public-status');
      if (statusLabel) statusLabel.textContent = 'Unlock required';
      if (publicStatus) {
        publicStatus.classList.remove('is-ready');
        publicStatus.classList.add('is-pending');
        publicStatus.textContent = 'Unlock Syra to manage the public endpoint.';
      }
      return;
    }
    await Promise.all([loadSyraStatus(), loadSyraConfig(), loadSettings(), loadTokens()]);
  } catch (e) {
    syraCsrfToken = '';
    setSyraSessionState(false);
    toast('Unable to restore Syra session: ' + e.message);
  }
}

async function loadSyraLogs() {
  const logsEl = document.getElementById('syra-logs');
  try {
    const res = await api('/settings/syra/logs?lines=100');
    if (res.ok && logsEl) {
      logsEl.textContent = res.logs || 'No logs available';
      logsEl.scrollTop = logsEl.scrollHeight;
    }
  } catch (e) {
    if (logsEl) logsEl.textContent = 'Error loading logs: ' + e.message;
  }
}

async function loadSyraModels() {
  const modelsEl = document.getElementById('syra-models-list');
  try {
    const res = await api('/settings/syra/models');
    if (res.ok && modelsEl) {
      if (res.models && res.models.length > 0) {
        modelsEl.innerHTML = res.models.map(m => `
          <div class="syra-model-card">
            <span class="syra-model-name">${esc(String(m.id || m))}</span>
            <span class="syra-model-provider">${esc(String(m.owned_by || 'litellm'))}</span>
          </div>
        `).join('');
      } else {
        modelsEl.innerHTML = '<p class="hint">No models configured. Configure a provider and virtual key through your private LiteLLM administration access.</p>';
      }
    }
  } catch (e) {
    if (modelsEl) modelsEl.innerHTML = '<p class="hint">Error loading models: ' + e.message + '</p>';
  }
}

document.getElementById('syra-start-btn')?.addEventListener('click', async () => {
  if (!syraSessionReady()) return;
  const btn = document.getElementById('syra-start-btn');
  const statusLabel = document.getElementById('syra-status-label');
  btn.disabled = true;
  statusLabel.textContent = 'Starting…';
  try {
    const res = await api('/settings/syra/start', { method: 'POST' });
    const setupSummary = Array.isArray(res.host_setup?.steps)
      ? res.host_setup.steps.join(' ')
      : '';
    const resultMessage = [res.message, res.proxy_message, setupSummary]
      .filter(Boolean)
      .join(' ');
    if (res.ok) {
      toast(`Syra ready: ${resultMessage || 'https://api.sycord.site/'}`);
    } else {
      toast(resultMessage || 'Failed to prepare and start Syra');
    }
    await loadSyraStatus();
    await loadSyraModels();
  } catch (e) {
    toast('Error: ' + e.message);
    await loadSyraStatus();
  }
});

document.getElementById('syra-stop-btn')?.addEventListener('click', async () => {
  if (!syraSessionReady()) return;
  const btn = document.getElementById('syra-stop-btn');
  const statusLabel = document.getElementById('syra-status-label');
  btn.disabled = true;
  statusLabel.textContent = 'Stopping…';
  try {
    const res = await api('/settings/syra/stop', { method: 'POST' });
    if (res.ok) {
      toast('LiteLLM stopped');
    } else {
      toast(res.message || 'Failed to stop LiteLLM');
    }
    await loadSyraStatus();
  } catch (e) {
    toast('Error: ' + e.message);
    await loadSyraStatus();
  }
});

document.getElementById('syra-restart-btn')?.addEventListener('click', async () => {
  if (!syraSessionReady()) return;
  const btn = document.getElementById('syra-restart-btn');
  const statusLabel = document.getElementById('syra-status-label');
  btn.disabled = true;
  statusLabel.textContent = 'Restarting…';
  try {
    const res = await api('/settings/syra/restart', { method: 'POST' });
    if (res.ok) {
      toast('LiteLLM restarted');
    } else {
      toast(res.message || 'Failed to restart LiteLLM');
    }
    await loadSyraStatus();
    await loadSyraModels();
  } catch (e) {
    toast('Error: ' + e.message);
    await loadSyraStatus();
  }
});

document.getElementById('syra-save-config-btn')?.addEventListener('click', async () => {
  if (!syraSessionReady()) return;
  const btn = document.getElementById('syra-save-config-btn');
  if (btn) btn.disabled = true;
  try {
    const masterKey = document.getElementById('syra-master-key')?.value.trim() || '';
    const saltKey = document.getElementById('syra-salt-key')?.value.trim() || '';
    const agentApiKey = document.getElementById('syra-agent-api-key')?.value.trim() || '';
    const body = {};
    // Blank secret fields mean "leave the server value unchanged". Private
    // values are never returned to or prefilled in the browser.
    if (masterKey) body.master_key = masterKey;
    if (saltKey) body.salt_key = saltKey;
    if (agentApiKey) body.agent_api_key = agentApiKey;

    if (!Object.keys(body).length) {
      toast('No secret changes to save');
      return;
    }

    const res = await api('/settings/syra/secrets', {
      method: 'PUT',
      body: JSON.stringify(body),
    });

    if (res.ok) {
      document.getElementById('syra-master-key').value = '';
      document.getElementById('syra-salt-key').value = '';
      document.getElementById('syra-agent-api-key').value = '';
      toast('Server-only LiteLLM credentials saved');
      await loadSyraConfig();
    } else {
      toast(res.message || 'Failed to save configuration');
    }
  } catch (e) {
    toast('Error: ' + e.message);
  } finally {
    if (btn) btn.disabled = false;
  }
});

document.getElementById('syra-logs-refresh')?.addEventListener('click', () => {
  if (!syraSessionReady()) return;
  loadSyraLogs();
});
document.getElementById('syra-models-refresh')?.addEventListener('click', () => {
  if (!syraSessionReady()) return;
  loadSyraModels();
});
document.getElementById('syra-unlock-btn')?.addEventListener('click', unlockSyra);
document.getElementById('syra-lock-btn')?.addEventListener('click', lockSyra);
document.getElementById('syra-bootstrap-key')?.addEventListener('keydown', (event) => {
  if (event.key === 'Enter') {
    event.preventDefault();
    unlockSyra();
  }
});
