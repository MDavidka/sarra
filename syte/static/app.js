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
let liveSystemMetrics = null;
const overviewMetricHistory = {ram: [], cpu: [], disk: []};
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
let highLoadNetworkErrorCount = 0;

function getApiKey() {
  try {
    return sessionStorage.getItem(API_KEY_STORAGE) || '';
  } catch (e) {
    return '';
  }
}

function showCrashScreen(info = {}) {
  const overlay = document.getElementById('crash-screen');
  if (!overlay) return;

  const titleEl = document.getElementById('crash-title');
  const subtitleEl = document.getElementById('crash-subtitle');
  const msgEl = document.getElementById('crash-message');
  const detailsEl = document.getElementById('crash-details');

  if (titleEl) titleEl.textContent = info.title || 'Application Error';
  if (subtitleEl) subtitleEl.textContent = info.subtitle || 'Syte encountered an issue under high server load or network disruption.';

  const msgText = typeof info === 'string' ? info : (info.message || info.error?.message || String(info));
  if (msgEl) msgEl.textContent = msgText || 'An unexpected application error occurred.';

  const detailsText = info.details || info.stack || (info.error && info.error.stack) || '';
  if (detailsEl) {
    if (detailsText && detailsText !== msgText) {
      detailsEl.textContent = detailsText;
      detailsEl.classList.remove('hidden');
    } else {
      detailsEl.classList.add('hidden');
    }
  }

  overlay.classList.remove('hidden');
  if (typeof refreshIcons === 'function') {
    try { refreshIcons(); } catch (_) {}
  }
}

function hideCrashScreen() {
  const overlay = document.getElementById('crash-screen');
  if (overlay) overlay.classList.add('hidden');
}

function setupCrashScreenHandlers() {
  document.getElementById('crash-reload-btn')?.addEventListener('click', () => {
    window.location.reload();
  });

  document.getElementById('crash-retry-btn')?.addEventListener('click', async () => {
    hideCrashScreen();
    highLoadNetworkErrorCount = 0;
    if (typeof showToast === 'function') showToast('Retrying server connection…');
    if (typeof loadSystem === 'function') loadSystem();
    if (typeof loadProjects === 'function') loadProjects();
  });

  document.getElementById('crash-dismiss-btn')?.addEventListener('click', () => {
    hideCrashScreen();
  });
}

function normalizeFetchError(message) {
  const msg = (message || '').trim();
  if (!msg || msg === 'Load failed' || msg === 'Failed to fetch' || msg === 'NetworkError when attempting to fetch resource.') {
    return 'Could not reach the Syte server. Server may be down or under high load.';
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

function isDebugChatWorkspaceActive() {
  return activeSvcTab === 'debug-chat' || document.getElementById('view-ai')?.classList.contains('active');
}

function startDebugChatBrainPoll(projectId) {
  stopDebugChatBrainPoll();
  debugChatBrainLastLoggedState = '';
  void pollDebugChatBrainOnce(projectId);
  debugChatBrainPollTimer = setInterval(() => {
    if (!isDebugChatWorkspaceActive() || activeServiceId !== projectId) {
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
    main: 'No messages in this project yet.',
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
    'agent_stopped',
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
    settingsButton.textContent = 'Models & providers';
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
      `${where || String(event.detail || 'Preparing a plan').replace(/\s+/g, ' ')}`.slice(0, 160),
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
      `${debugChatActionTitle(event)}${
        target ? ` · ${target}` : ''
      }`.slice(0, 200),
      event.event_type === 'tool_call_started' ? 'loader' : actionMeta.icon,
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
    if (!isDebugChatWorkspaceActive() || activeServiceId !== projectId) {
      stopAgentActivityStream();
      return;
    }
    void pollAgentActivityOnce(projectId);
  }, AGENT_ACTIVITY_POLL_INTERVAL_MS);
}

function agentActivityStreamIsCurrent(projectId) {
  return isDebugChatWorkspaceActive()
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
  if (!activeServiceId || !isDebugChatWorkspaceActive()) return;
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
    const activeProfile = res.agent_model?.profile || '';
    if (activeProfile && activeProfile !== 'auto') {
      const picker = document.getElementById('debug-chat-profile');
      if (picker && [...picker.options].some((option) => option.value === activeProfile)) {
        syncGlobalAiModelSelection(activeProfile);
      }
    }
    if (res.agent_running && res.agent_healthy) {
      const model = activeProfile || res.agent_model?.model || 'agent';
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
  for (const id of [
    'debug-chat-thinking-level', 'debug-chat-context-window', 'debug-chat-stream-limit',
    'debug-chat-memory-depth', 'debug-chat-plan-mode', 'debug-chat-agent-mode',
    'debug-chat-max-steps', 'debug-chat-deployment-readiness',
  ]) {
    const control = document.getElementById(id);
    if (control) control.disabled = controlsBusy;
  }
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

function getDebugChatTurnControls() {
  return {
  thinking_level: Number(document.getElementById('debug-chat-thinking-level')?.value || 3),
  memory_depth: document.getElementById('debug-chat-memory-depth')?.value || 'balanced',
  plan_mode: document.getElementById('debug-chat-plan-mode')?.value || 'auto',
  agent_mode: document.getElementById('debug-chat-agent-mode')?.value || 'build',
  deployment_readiness: Boolean(document.getElementById('debug-chat-deployment-readiness')?.checked),
  };
}

function updateDebugChatContextSummary() {
  const controls = getDebugChatTurnControls();
  const windowLabel = document.getElementById('debug-chat-context-window-label');
  const streamLabel = document.getElementById('debug-chat-stream-limit-label');
  const memoryLabel = document.getElementById('debug-chat-memory-depth-label');
  if (windowLabel) windowLabel.textContent = `${Math.round(controls.context_window_tokens / 1000)}k`;
  if (streamLabel) streamLabel.textContent = `${Math.round(controls.stream_max_tokens / 1000)}k`;
  if (memoryLabel) memoryLabel.textContent = controls.memory_depth[0].toUpperCase() + controls.memory_depth.slice(1);
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
        && isDebugChatWorkspaceActive()
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
  const turnControls = getDebugChatTurnControls();
  const sentMessage = message;
  if (input) {
    input.value = '';
    input.dispatchEvent(new Event('input'));
  }
  let chatOk = false;
  let acceptedAsync = false;
  try {
    const body = { message: sentMessage, ...turnControls };
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
  users: 'API',
  logs: 'Logs',
  ai: 'AI',
  models: 'Models',
  router: 'Router',
  ssl: 'SSL',
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
    const isPlatformLink = el.dataset.view === 'platform';
    const matchesPlatformPage = viewName === 'platform' && isPlatformLink && el.dataset.platformPage === activePlatformPage;
    const matchesView = viewName !== 'platform' && !isPlatformLink && el.dataset.view === navView;
    el.classList.toggle('active', !isService && (matchesPlatformPage || matchesView));
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

function setSettingsMiniTab(tab = 'general') {
  const allowed = ['general', 'git', 'advanced'];
  const next = allowed.includes(tab) ? tab : 'general';
  const descriptions = {
    general: 'Server, domains, and preview access',
    git: 'Track the install branch and review open pull requests',
    advanced: 'AI and feature controls',
  };
  document.querySelectorAll('[data-settings-tab]').forEach((button) => {
    const active = button.dataset.settingsTab === next;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.querySelectorAll('[data-settings-panel]').forEach((panel) => {
    panel.classList.toggle('hidden', panel.dataset.settingsPanel !== next);
  });
  const hint = document.getElementById('settings-tab-hint');
  if (hint) hint.textContent = descriptions[next];
  try { localStorage.setItem('syte_settings_tab', next); } catch { /* private browsing */ }
  if (next === 'git') void loadGitTracking();
  refreshIcons();
}

async function loadGitTracking() {
  const summary = document.getElementById('github-tracking-summary');
  const list = document.getElementById('github-pr-list');
  if (!summary || !list) return;
  if (!await operatorAuthenticated()) {
    summary.innerHTML = '<p class="hint">Sign in to load branch and pull request status.</p>';
    list.innerHTML = '<button type="button" class="btn-pill btn-ghost" id="github-sign-in-btn">Sign in to GitHub tracking</button>';
    document.getElementById('github-sign-in-btn')?.addEventListener('click', () => showLoginScreen('settings'));
    return;
  }
  summary.innerHTML = '<p class="hint">Loading local branch and update status…</p>';
  list.innerHTML = '<p class="hint">Loading pull requests…</p>';
  try {
    const status = await api('/github/status');
    const local = status.local || {};
    const update = status.update || {};
    const repo = status.repo || 'repository not configured';
    const repoLink = status.repo_url
      ? `<a class="link" href="${esc(status.repo_url)}" target="_blank" rel="noopener">${esc(repo)}</a>`
      : esc(repo);
    summary.innerHTML = `
      <div class="git-state-item"><span class="git-state-label">repository</span><span class="git-state-value">${repoLink}</span></div>
      <div class="git-state-item"><span class="git-state-label">current branch</span><span class="git-state-value">${esc(local.branch || 'detached')}</span></div>
      <div class="git-state-item"><span class="git-state-label">commit</span><span class="git-state-value">${esc(local.commit || '—')}${local.dirty ? ' · uncommitted changes' : ''}</span></div>
      <div class="git-state-item"><span class="git-state-label">update target</span><span class="git-state-value">${esc(update.label || update.branch || 'main')}</span></div>
      <div class="git-state-item"><span class="git-state-label">token</span><span class="git-state-value">${status.token_configured ? `configured · ${esc(status.token_source || 'unknown')}` : 'not configured'}</span></div>
      <div class="git-state-item"><span class="git-state-label">working tree</span><span class="git-state-value">${local.dirty ? `${local.changed_files || 0} changed file(s)` : 'clean'}</span></div>
    `;
    const prs = await api('/github/pulls');
    renderGitHubPullRequests(prs);
  } catch (error) {
    summary.innerHTML = `<p class="hint">GitHub tracking unavailable: ${esc(error.message)}</p>`;
    list.innerHTML = '<p class="hint">Check the repository name, token permissions, or GitHub rate limit.</p>';
  }
  refreshIcons();
}

function renderGitHubPullRequests(payload) {
  const list = document.getElementById('github-pr-list');
  if (!list) return;
  const prs = payload?.pull_requests || [];
  if (!prs.length) {
    list.innerHTML = '<p class="hint">No open pull requests found for this repository.</p>';
    return;
  }
  list.innerHTML = prs.map((pr) => {
    const blockers = pr.merge_blockers || [];
    const checks = pr.checks || {};
    const ready = Boolean(pr.can_merge && !blockers.length);
    const status = ready
      ? '<span class="github-pr-status ready">ready to merge</span>'
      : `<span class="github-pr-status blocked">${esc(blockers[0] || (pr.enriched ? `checks: ${checks.state || 'unknown'}` : 'details loading'))}</span>`;
    const mergeButton = ready
      ? `<button type="button" class="btn-pill btn-primary btn-sm github-pr-merge" data-merge-pr="${pr.number}">Merge squash</button>`
      : '';
    return `
      <div class="github-pr-row">
        <div class="github-pr-main">
          <strong>#${pr.number} · ${esc(pr.title || 'Untitled pull request')}</strong>
          <div class="github-pr-meta">
            <a href="${esc(pr.url || '#')}" target="_blank" rel="noopener">${esc(pr.head_ref || 'unknown')}</a>
            → ${esc(pr.base_ref || 'main')} · ${esc(pr.author || 'unknown')} · ${checks.passed || 0}/${checks.total || 0} checks passed
          </div>
          ${mergeButton}
        </div>
        ${status}
      </div>
    `;
  }).join('');
  list.querySelectorAll('[data-merge-pr]').forEach((button) => {
    button.addEventListener('click', () => mergeTrackedPullRequest(Number(button.dataset.mergePr)));
  });
  refreshIcons();
}

async function mergeTrackedPullRequest(number) {
  if (!number || !confirm(`Merge pull request #${number} with squash?`)) return;
  try {
    const result = await api(`/github/pulls/${number}/merge`, {
      method: 'POST',
      body: JSON.stringify({ method: 'squash', force: false }),
    });
    toast(result.message || `PR #${number} merged`);
    await loadGitTracking();
  } catch (error) {
    toast(`Merge failed: ${error.message}`);
  }
}

function restoreSettingsMiniTab() {
  let tab = 'general';
  try { tab = localStorage.getItem('syte_settings_tab') || 'general'; } catch { /* private browsing */ }
  setSettingsMiniTab(tab);
}

let activePlatformPage = 'overview';

const PLATFORM_PAGE_LABELS = {
  projects: 'Projects', overview: 'Overview', schedules: 'Schedules', traefik: 'Traefik File System', docker: 'Docker',
  profile: 'Profile', sessions: 'Sessions', 'remote-servers': 'Remote Servers', 'audit-logs': 'Audit Logs', 'ssh-keys': 'SSH Keys',
  ai: 'AI', tags: 'Tags', git: 'Git', registry: 'Registry', secrets: 'Secrets', 'dns-providers': 'DNS Providers',
  's3-destinations': 'S3 Destinations', certificates: 'Certificates', notifications: 'Notifications', billing: 'Billing',
  license: 'License', sso: 'SSO', documentation: 'Documentation', support: 'Support',
};

const PLATFORM_PAGE_BLUEPRINTS = {
  projects: {heading:'Applications and environments', control:'create-project', columns:['name','status','git_repository','git_branch']},
  overview: {heading:'Platform inventory', control:'overview-actions', columns:['name','status','_table']},
  schedules: {heading:'Backup and task schedules', control:'create-schedule', columns:['name','frequency','enabled','last_run_at']},
  traefik: {heading:'Proxy configuration', control:'validate-proxy', columns:['name','status','domain','certificate']},
  docker: {heading:'Runtime containers', control:'runtime-actions', columns:['name','status','database_type','server_uuid']},
  profile: {heading:'Operator profile', control:'profile-form', columns:['email','name','role']},
  sessions: {heading:'Authenticated sessions', control:'session-actions', columns:['created_at','last_seen_at','user_agent','status']},
  'remote-servers': {heading:'Deployment nodes', control:'server-form', columns:['name','status','ip','proxy']},
  'audit-logs': {heading:'Recent audit events', control:'audit-actions', columns:['created_at','event','source','status']},
  'ssh-keys': {heading:'Deployment credentials', control:'key-form', columns:['name','fingerprint','created_at']},
  ai: {heading:'Model providers', control:'ai-actions', columns:['provider','model','enabled','updated_at']},
  tags: {heading:'Resource tags', control:'tag-form', columns:['name','color','resource_count']},
  git: {heading:'Git providers and repositories', control:'git-form', columns:['name','provider','url','status']},
  registry: {heading:'Container registries', control:'registry-form', columns:['name','url','username','status']},
  secrets: {heading:'Environment variables', control:'secret-form', columns:['key','scope','resource_uuid','updated_at']},
  'dns-providers': {heading:'DNS automation providers', control:'dns-form', columns:['name','provider','zone','status']},
  's3-destinations': {heading:'Backup destinations', control:'s3-form', columns:['name','endpoint','bucket','region','status']},
  certificates: {heading:'TLS certificates', control:'certificate-actions', columns:['domain','issuer','status','expires_at']},
  notifications: {heading:'Notification channels', control:'notification-form', columns:['name','type','enabled','last_delivery_at']},
  billing: {heading:'Usage and entitlement', control:'billing-actions', columns:['name','resource_count','status']},
  license: {heading:'Installation entitlement', control:'license-actions', columns:['feature','status','source']},
  sso: {heading:'Identity provider configuration', control:'sso-form', columns:['provider','issuer','status','updated_at']},
  documentation: {heading:'Operator references', control:'documentation-actions', columns:['name','url','method','status']},
  support: {heading:'Diagnostics and support', control:'support-actions', columns:['created_at','event','source','status']},
};

function renderPlatformControls(page, data) {
  const blueprint = PLATFORM_PAGE_BLUEPRINTS[page] || {};
  const controls = document.getElementById('platform-page-controls');
  if (!controls) return;
  const forms = {
    'create-project':['Project name','Repository URL'], 'create-schedule':['Schedule name','Cron expression'], 'profile-form':['Display name','Email'],
    'server-form':['Server name','Host/IP'], 'key-form':['Key name','Algorithm (ed25519 or rsa)'], 'tag-form':['Tag name','Color'], 'git-form':['Provider name','Repository URL'],
    'registry-form':['Registry name','Registry URL'], 'secret-form':['Variable name','Value'], 'dns-form':['Provider name','Zone'], 's3-form':['Destination name','Bucket'],
    'notification-form':['Channel name','Webhook URL'], 'sso-form':['Provider','Issuer URL'],
  };
  const actionLabels = {
    'overview-actions':'Refresh inventory', 'validate-proxy':'Validate proxy configuration', 'runtime-actions':'Refresh runtime status', 'session-actions':'Revoke stale sessions',
    'ai-actions':'Refresh model catalog', 'certificate-actions':'Renew certificate inventory', 'billing-actions':'Recalculate usage', 'license-actions':'Check entitlement',
    'documentation-actions':'Open API documentation', 'support-actions':'Run diagnostics', 'audit-actions':'Refresh audit log',
  };
  const fields = forms[blueprint.control];
  if (fields) {
    controls.innerHTML = `<form class="platform-inline-form" data-platform-form="${esc(blueprint.control)}"><div><label>${esc(fields[0])}</label><input name="primary" required></div><div><label>${esc(fields[1])}</label><input name="secondary" required></div><button class="btn-create" type="submit"><i data-lucide="plus"></i><span>Create</span></button></form>`;
  } else if (blueprint.control) {
    controls.innerHTML = `<div class="platform-control-toolbar"><span>Operational tools</span><button type="button" class="btn-pill btn-ghost" data-platform-operation="${esc(blueprint.control)}"><i data-lucide="play"></i><span>${esc(actionLabels[blueprint.control] || 'Run operation')}</span></button></div>`;
  } else controls.innerHTML = '';
  controls.querySelector('form')?.addEventListener('submit', async (event) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      const endpoint = blueprint.control === 'key-form' ? '/platform/ssh-keys/generate' : `/platform/navigation/${encodeURIComponent(page)}/records`;
      const body = blueprint.control === 'key-form' ? {name: form.get('primary'), algorithm: form.get('secondary') || 'ed25519'} : {primary: form.get('primary'), secondary: form.get('secondary')};
      const result = await api(endpoint, {method:'POST', body: JSON.stringify(body)});
      if (blueprint.control === 'key-form') {
        const message = document.getElementById('platform-page-message');
        if (message) message.textContent = `${result.message} Fingerprint: ${result.key?.fingerprint || 'generated'}. Private key is available in the response for immediate download.`;
      }
      await loadPlatformPage(page);
    } catch (error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; }
  });
  controls.querySelector('[data-platform-operation]')?.addEventListener('click', async () => {
    try { await api(`/platform/navigation/${encodeURIComponent(page)}/actions`, {method:'POST', body: JSON.stringify({action: blueprint.control})}); await loadPlatformPage(page); }
    catch (error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; }
  });
}

function renderDashboardMetrics(data) {
  renderMonitorMetrics(data);
  const homeTarget = document.getElementById('home-dashboard-metrics');
  if (!homeTarget) return;

  const ramLabel = data.ram_used_mb && data.ram_total_mb
    ? `${Math.round(data.ram_percent || 0)}% (${(data.ram_used_mb / 1024).toFixed(1)}GB / ${(data.ram_total_mb / 1024).toFixed(1)}GB)`
    : `${Math.round(data.ram_percent || data.memory_percent || 0)}%`;

  const diskLabel = data.disk_used_gb != null && data.disk_total_gb
    ? `${Math.round(data.disk_percent || 0)}% (${data.disk_used_gb}GB / ${data.disk_total_gb}GB)`
    : `${Math.round(data.disk_percent || 0)}%`;

  const pingVal = data.ping_ms ?? data.internet_ping_ms;
  const pingLabel = pingVal != null ? `${Math.round(pingVal)} ms` : '—';

  const cards = [
    ['RAM', ramLabel, 'memory-stick'],
    ['CPU', `${Math.round(data.cpu_percent || 0)}%`, 'cpu'],
    ['Disk', diskLabel, 'hard-drive'],
    ['Ping', pingLabel, 'wifi'],
    ['API Requests (7d)', String(data.api_requests_7d ?? '0'), 'activity'],
    ['API Requests (30d)', String(data.api_requests_30d ?? data.api_requests ?? '0'), 'bar-chart-2'],
    ['Projects', String(data.project_count ?? projects.length ?? 0), 'layers'],
  ];

  homeTarget.innerHTML = cards.map(([label, value, icon]) => `
    <div class="monitor-metric-card">
      <i data-lucide="${icon}"></i>
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
    </div>
  `).join('');
  refreshIcons();
}

function renderMonitorMetrics(data) {
  const target = document.getElementById('overview-monitor-grid');
  if (!target) return;

  const ramLabel = data.ram_used_mb && data.ram_total_mb
    ? `${Math.round(data.ram_percent || 0)}% (${(data.ram_used_mb / 1024).toFixed(1)}GB / ${(data.ram_total_mb / 1024).toFixed(1)}GB)`
    : `${Math.round(data.ram_percent || data.memory_percent || 0)}%`;

  const diskLabel = data.disk_used_gb != null && data.disk_total_gb
    ? `${Math.round(data.disk_percent || 0)}% (${data.disk_used_gb}GB / ${data.disk_total_gb}GB)`
    : `${Math.round(data.disk_percent || 0)}%`;

  const pingVal = data.ping_ms ?? data.internet_ping_ms;
  const pingLabel = pingVal != null ? `${Math.round(pingVal)} ms` : '—';

  const cards = [
    ['RAM', ramLabel, 'memory-stick'],
    ['CPU', `${Math.round(data.cpu_percent || 0)}%`, 'cpu'],
    ['Disk', diskLabel, 'hard-drive'],
    ['Ping', pingLabel, 'wifi'],
    ['API Requests (7d)', String(data.api_requests_7d ?? '0'), 'activity'],
    ['API Requests (30d)', String(data.api_requests_30d ?? data.api_requests ?? '0'), 'bar-chart-2'],
    ['Projects', String(data.project_count ?? projects.length ?? 0), 'layers'],
  ];

  target.innerHTML = cards.map(([label, value, icon]) => `
    <div class="monitor-metric-card">
      <i data-lucide="${icon}"></i>
      <span>${esc(label)}</span>
      <strong>${esc(value)}</strong>
    </div>
  `).join('');
  refreshIcons();
}

async function loadOverviewMonitor() {
  try {
    const data = await api('/platform/overview/metrics');
    renderDashboardMetrics(data);
  } catch (error) {
    const target = document.getElementById('overview-monitor-grid');
    if (target) target.innerHTML = `<div class="platform-error">Metrics unavailable: ${esc(error.message)}</div>`;
  }
}

async function loadDockerStore() {
  const panel = document.getElementById('platform-store-panel');
  const grid = document.getElementById('platform-store-grid');
  if (!panel || !grid) return;
  panel.classList.remove('hidden');
  try {
    const data = await api('/platform/store/catalog');
    const apps = data.apps || [];
    let category = 'All';
    let sort = 'Popular';
    const categories = ['All', ...new Set(apps.map(app => app.category))];
    const categoryEl = document.getElementById('docker-library-categories');
    if (categoryEl) categoryEl.innerHTML = categories.map(item => `<button type="button" class="docker-category ${item === category ? 'active' : ''}" data-category="${esc(item)}">${esc(item)}</button>`).join('');
    const render = (query = '') => {
      const normalized = query.toLowerCase().trim();
      let visible = apps.filter(app => (category === 'All' || app.category === category) && (!normalized || `${app.name} ${app.description} ${app.category} ${app.image}`.toLowerCase().includes(normalized)));
      if (sort === 'A–Z') visible = [...visible].sort((a,b) => a.name.localeCompare(b.name));
      const count = document.getElementById('docker-library-count'); if (count) count.textContent = `${visible.length} apps`;
      const featured = document.getElementById('docker-library-featured');
      if (featured && !normalized && category === 'All' && apps[0]) {
        const app = apps[0];
        featured.innerHTML = `<article class="docker-featured-card" style="--store-color:${esc(app.color)}"><div class="docker-featured-copy"><span class="docker-featured-label">Featured this week</span><h2>${esc(app.name)} for your next deployment</h2><p>${esc(app.description)}</p><div class="docker-featured-meta"><span>${esc(app.size)}</span><span>${esc(app.image)}</span></div><button type="button" class="docker-featured-btn" data-store-app="${esc(app.slug)}"><i data-lucide="arrow-down-to-line"></i>Install ${esc(app.name)}</button></div><div class="docker-featured-icon"><img src="${esc(app.icon)}" alt="" loading="lazy"></div></article>`;
      } else if (featured) featured.innerHTML = '';
      grid.innerHTML = visible.map(app => `<article class="docker-app-card"><div class="docker-app-card-head" style="--store-color:${esc(app.color)}"><img src="${esc(app.icon)}" alt="" loading="lazy"><span>${esc(app.category)}</span><button type="button" class="docker-app-more" aria-label="More about ${esc(app.name)}"><i data-lucide="ellipsis"></i></button></div><div class="docker-app-card-body"><h3>${esc(app.name)}</h3><p>${esc(app.description)}</p><div class="docker-app-card-foot"><span>${esc(app.size)}</span><span>${esc(app.image)}</span></div><button type="button" class="docker-app-install" data-store-app="${esc(app.slug)}"><span>Install</span><i data-lucide="plus"></i></button></div></article>`).join('') || '<div class="platform-empty">No applications match this view.</div>';
      const install = async (btn) => { btn.disabled = true; const label = btn.querySelector('span'); if (label) label.textContent = 'Installing…'; try { await api('/platform/store/install', {method:'POST', body: JSON.stringify({slug: btn.dataset.storeApp})}); if (label) label.textContent = 'Added'; } catch (error) { btn.disabled = false; if (label) label.textContent = 'Retry'; const message = document.getElementById('platform-page-message'); if (message) message.textContent = `Install failed: ${error.message}`; } };
      document.querySelectorAll('[data-store-app]').forEach(btn => btn.addEventListener('click', () => install(btn)));
      refreshIcons();
    };
    categoryEl?.querySelectorAll('[data-category]').forEach(btn => btn.addEventListener('click', () => { category = btn.dataset.category; categoryEl.querySelectorAll('.docker-category').forEach(item => item.classList.toggle('active', item === btn)); render(document.getElementById('platform-store-filter')?.value || ''); }));
    document.getElementById('platform-store-filter')?.addEventListener('input', event => render(event.target.value));
    document.getElementById('docker-library-sort')?.addEventListener('click', event => { sort = sort === 'Popular' ? 'A–Z' : 'Popular'; event.currentTarget.querySelector('span').textContent = sort; render(document.getElementById('platform-store-filter')?.value || ''); });
    document.getElementById('docker-library-refresh')?.addEventListener('click', () => loadDockerStore());
    render();
  } catch (error) { grid.innerHTML = `<div class="platform-error">Library unavailable: ${esc(error.message)}</div>`; }
}

const DEDICATED_PAGE_CONFIG = {
  projects:{icon:'layers-3',eyebrow:'Workspace',title:'Projects',intro:'Launch, deploy, and organize applications by environment.',action:'Create project',fields:['Project name','Repository URL'],tone:'violet'},
  overview:{icon:'layout-dashboard',eyebrow:'Command center',title:'Overview',intro:'See the health and activity of your self-hosted platform at a glance.',action:'Refresh metrics',tone:'blue'},
  schedules:{icon:'calendar-clock',eyebrow:'Automation',title:'Schedules',intro:'Create recurring backups, jobs, and deployment tasks.',action:'New schedule',fields:['Schedule name','Cron expression'],tone:'amber'},
  traefik:{icon:'route',eyebrow:'Networking',title:'Traefik',intro:'Inspect routes, certificates, and proxy readiness before traffic reaches your apps.',action:'Validate routes',tone:'cyan'},
  profile:{icon:'user-round',eyebrow:'Account',title:'Profile',intro:'Manage your operator identity and workspace preferences.',action:'Save profile',fields:['Display name','Email'],tone:'violet'},
  sessions:{icon:'shield-check',eyebrow:'Security',title:'Sessions',intro:'Review active operator sessions and revoke stale access.',action:'Review sessions',tone:'rose'},
  'remote-servers':{icon:'server',eyebrow:'Infrastructure',title:'Remote Servers',intro:'Register deployment nodes and monitor their availability.',action:'Add server',fields:['Server name','Host/IP'],tone:'green'},
  'audit-logs':{icon:'list-checks',eyebrow:'Governance',title:'Audit Logs',intro:'Trace operator actions and platform changes in one chronological stream.',action:'Refresh log',tone:'slate'},
  'ssh-keys':{icon:'key-round',eyebrow:'Credentials',title:'SSH Keys',intro:'Generate and manage deployment credentials without leaving the console.',action:'Generate key',fields:['Key name','Algorithm (ed25519 or rsa)'],tone:'orange'},
  ai:{icon:'sparkles',eyebrow:'Intelligence',title:'AI Providers',intro:'Configure model providers and inspect the engines available to Syte.',action:'Refresh models',tone:'purple'},
  tags:{icon:'tags',eyebrow:'Organization',title:'Tags',intro:'Create a consistent vocabulary for filtering projects and resources.',action:'Create tag',fields:['Tag name','Color'],tone:'pink'},
  git:{icon:'git-branch',eyebrow:'Source control',title:'Git Sources',intro:'Connect repositories and keep deployments tied to trusted source providers.',action:'Add source',fields:['Provider name','Repository URL'],tone:'orange'},
  registry:{icon:'container',eyebrow:'Images',title:'Registries',intro:'Manage private image registries used by your deployments.',action:'Add registry',fields:['Registry name','Registry URL'],tone:'blue'},
  secrets:{icon:'lock-keyhole',eyebrow:'Configuration',title:'Secrets',intro:'Store environment variables and keep sensitive values out of application code.',action:'Add secret',fields:['Variable name','Value'],tone:'rose'},
  'dns-providers':{icon:'globe-2',eyebrow:'Domains',title:'DNS Providers',intro:'Connect DNS automation providers for domain verification and records.',action:'Add provider',fields:['Provider name','Zone'],tone:'cyan'},
  's3-destinations':{icon:'archive',eyebrow:'Backups',title:'S3 Destinations',intro:'Configure durable object storage destinations for backups and exports.',action:'Add destination',fields:['Destination name','Bucket'],tone:'green'},
  certificates:{icon:'badge-check',eyebrow:'TLS',title:'Certificates',intro:'Track certificate coverage and expiration across your platform.',action:'Refresh certificates',tone:'blue'},
  notifications:{icon:'bell-ring',eyebrow:'Delivery',title:'Notifications',intro:'Route deployment, backup, and security events to your team.',action:'Add channel',fields:['Channel name','Webhook URL'],tone:'amber'},
  billing:{icon:'credit-card',eyebrow:'Usage',title:'Billing',intro:'Understand platform usage and resource growth before it becomes a surprise.',action:'Recalculate usage',tone:'slate'},
  license:{icon:'badge-dollar-sign',eyebrow:'Entitlement',title:'License',intro:'Inspect installation capabilities and entitlement status.',action:'Check entitlement',tone:'violet'},
  sso:{icon:'scan-face',eyebrow:'Identity',title:'SSO',intro:'Configure identity providers and centralize operator access.',action:'Add provider',fields:['Provider','Issuer URL'],tone:'purple'},
  documentation:{icon:'book-open',eyebrow:'References',title:'Documentation',intro:'Open the API reference and operational guides for this installation.',action:'Open API docs',tone:'slate'},
  support:{icon:'life-buoy',eyebrow:'Help center',title:'Support',intro:'Run diagnostics and collect the information needed to resolve incidents.',action:'Run diagnostics',tone:'rose'},
};

function renderIndependentMobilePage(page, data, target) {
  if (['docker','profile','sessions','remote-servers','audit-logs','ssh-keys'].includes(page)) return false;
  const rows = data.resources || [];
  const rowName = row => esc(String(row.name || row.title || row.domain || row.provider || row.feature || row.uuid || 'Untitled resource'));
  const rowState = row => esc(String(row.status || row.state || row.type || row._table || 'ready'));
  const records = rows.slice(0, 10);
  const forms = {
    projects:['Project name','Repository URL'], schedules:['Schedule name','Cron expression'], tags:['Tag name','Color'], git:['Provider name','Repository URL'],
    registry:['Registry name','Registry URL'], secrets:['Variable name','Secret value'], 'dns-providers':['Provider name','Zone'], 's3-destinations':['Destination name','Bucket'],
    notifications:['Channel name','Webhook URL'], sso:['Provider','Issuer URL'],
  };
  const form = forms[page] ? `<form class="mobile-domain-form" data-mobile-form="${esc(page)}"><label>${esc(forms[page][0])}<input name="primary" required></label><label>${esc(forms[page][1])}<input name="secondary" required></label><button type="submit"><i data-lucide="plus"></i>Add</button></form>` : '';
  const action = `<button type="button" class="mobile-page-action" data-mobile-action="${esc(page)}"><i data-lucide="zap"></i>${esc(PLATFORM_PAGE_BLUEPRINTS[page]?.control ? 'Run check' : 'Refresh')}</button>`;
  const layouts = {
    overview:`<section class="mobile-overview"><header><p>Platform pulse</p><h2>Everything looks calm.</h2><span>${data.resource_count || 0} tracked resources across the platform</span></header><div class="mobile-pulse-grid"><div><i data-lucide="activity"></i><strong>${data.resource_count || 0}</strong><span>resources</span></div><div><i data-lucide="shield-check"></i><strong>Live</strong><span>status</span></div><div><i data-lucide="refresh-cw"></i><strong>Now</strong><span>last sync</span></div></div>${action}</section>`,
    projects:`<section class="mobile-projects"><header><div><p>Application portfolio</p><h2>Ship without friction</h2></div><i data-lucide="rocket"></i></header>${form}<div class="mobile-project-track">${records.map((r,i)=>`<article><span>${String(i+1).padStart(2,'0')}</span><div><strong>${rowName(r)}</strong><small>${rowState(r)}</small></div><i data-lucide="arrow-up-right"></i></article>`).join('') || '<p>Start by connecting a repository.</p>'}</div></section>`,
    schedules:`<section class="mobile-schedules"><header><i data-lucide="calendar-days"></i><div><p>Automation calendar</p><h2>Jobs on your rhythm</h2></div></header>${form}<div class="mobile-schedule-rail">${records.map(r=>`<article><span class="mobile-clock"><i data-lucide="clock-3"></i></span><div><strong>${rowName(r)}</strong><small>${esc(String(r.frequency || r.cron || rowState(r)))}</small></div><em>${r.enabled === 0 ? 'paused' : 'active'}</em></article>`).join('') || '<p>No scheduled work yet.</p>'}</div></section>`,
    traefik:`<section class="mobile-network"><div class="mobile-network-hero"><i data-lucide="network"></i><p>Traffic control</p><h2>Routes & certificates</h2><span>Validate routing before clients reach production.</span>${action}</div><div class="mobile-route-list">${records.map(r=>`<div><i data-lucide="route"></i><span><strong>${rowName(r)}</strong><small>${esc(String(r.domain || 'domain pending'))}</small></span><em>${rowState(r)}</em></div>`).join('') || '<p>No proxy routes found.</p>'}</div></section>`,
    ai:`<section class="mobile-ai"><header><div><p>Model control room</p><h2>AI providers</h2></div><i data-lucide="sparkles"></i></header><div class="mobile-ai-orb"><span>Syte AI</span><strong>${data.resource_count || 0}</strong><small>configured engines</small></div>${action}<div class="mobile-ai-list">${records.map(r=>`<div><i data-lucide="cpu"></i><span>${rowName(r)}</span><em>${rowState(r)}</em></div>`).join('') || '<p>Refresh the model catalog to discover available engines.</p>'}</div></section>`,
    tags:`<section class="mobile-tags"><header><p>Resource taxonomy</p><h2>Make the workspace findable</h2></header>${form}<div class="mobile-tag-cloud">${records.map(r=>`<span style="--tag-color:${esc(r.color || '#111')}"><i data-lucide="tag"></i>${rowName(r)}</span>`).join('') || '<p>Add tags to classify projects and services.</p>'}</div></section>`,
    git:`<section class="mobile-git"><header><i data-lucide="git-fork"></i><div><p>Source of truth</p><h2>Connected repositories</h2></div></header>${form}<div class="mobile-git-branches">${records.map(r=>`<article><span></span><div><strong>${rowName(r)}</strong><small>${esc(String(r.url || r.provider || 'repository'))}</small></div><i data-lucide="git-branch"></i></article>`).join('') || '<p>Connect a Git source to begin deploying.</p>'}</div></section>`,
    registry:`<section class="mobile-registry"><header><div><p>Container supply chain</p><h2>Image registries</h2></div><i data-lucide="boxes"></i></header>${form}<div class="mobile-registry-shelf">${records.map(r=>`<article><i data-lucide="package-check"></i><strong>${rowName(r)}</strong><small>${esc(String(r.url || r.endpoint || rowState(r)))}</small></article>`).join('') || '<p>No private registries configured.</p>'}</div></section>`,
    secrets:`<section class="mobile-secrets"><header><i data-lucide="shield-ellipsis"></i><div><p>Encrypted configuration</p><h2>Secrets vault</h2></div></header>${form}<div class="mobile-secret-stack">${records.map(r=>`<div><span><i data-lucide="lock-keyhole"></i>${rowName(r)}</span><code>••••••••••••</code></div>`).join('') || '<p>Store a secret to keep values out of your codebase.</p>'}</div></section>`,
    'dns-providers':`<section class="mobile-dns"><header><div><p>Domain automation</p><h2>DNS control plane</h2></div><i data-lucide="globe-2"></i></header>${form}<div class="mobile-dns-map">${records.map(r=>`<article><i data-lucide="map-pin"></i><strong>${rowName(r)}</strong><span>${esc(String(r.zone || rowState(r)))}</span></article>`).join('') || '<p>Connect a provider to automate verification and records.</p>'}</div></section>`,
    's3-destinations':`<section class="mobile-storage"><header><i data-lucide="cloud"></i><div><p>Durable backups</p><h2>Object storage</h2></div></header>${form}<div class="mobile-bucket-list">${records.map(r=>`<div><i data-lucide="archive"></i><span><strong>${rowName(r)}</strong><small>${esc(String(r.bucket || r.region || rowState(r)))}</small></span><em>ready</em></div>`).join('') || '<p>Connect an S3 destination for backup retention.</p>'}</div></section>`,
    certificates:`<section class="mobile-certificates"><header><div><p>Transport security</p><h2>Certificate horizon</h2></div><i data-lucide="badge-check"></i></header><div class="mobile-cert-ring"><strong>${data.resource_count || 0}</strong><span>certificates</span></div>${action}<div class="mobile-cert-list">${records.map(r=>`<div><i data-lucide="shield-check"></i><span>${rowName(r)}</span><small>${esc(String(r.expires_at || rowState(r)))}</small></div>`).join('') || '<p>No certificate records are currently tracked.</p>'}</div></section>`,
    notifications:`<section class="mobile-notifications"><header><i data-lucide="bell-ring"></i><div><p>Event delivery</p><h2>Notification channels</h2></div></header>${form}<div class="mobile-notification-feed">${records.map(r=>`<article><span><i data-lucide="send"></i></span><div><strong>${rowName(r)}</strong><small>${esc(String(r.type || rowState(r)))}</small></div><em>${r.enabled === 0 ? 'off' : 'on'}</em></article>`).join('') || '<p>Add a channel to receive deployment and security events.</p>'}</div></section>`,
    billing:`<section class="mobile-billing"><header><p>Resource usage</p><h2>Platform consumption</h2></header><div class="mobile-usage-meter"><span></span><strong>${data.resource_count || 0}</strong><small>tracked services</small></div>${action}<p class="mobile-muted">Usage is calculated from real platform resources and refreshed on demand.</p></section>`,
    license:`<section class="mobile-license"><div class="mobile-license-card"><i data-lucide="badge-dollar-sign"></i><p>Installation entitlement</p><h2>Capabilities ready</h2><span>${data.resource_count || 0} feature records</span>${action}</div><div class="mobile-license-list">${records.map(r=>`<div><i data-lucide="check"></i><span>${rowName(r)}</span><em>${rowState(r)}</em></div>`).join('') || '<p>Run an entitlement check for installation details.</p>'}</div></section>`,
    sso:`<section class="mobile-sso"><header><div><p>Centralized identity</p><h2>Single sign-on</h2></div><i data-lucide="scan-face"></i></header>${form}<div class="mobile-sso-flow"><span>Identity provider</span><i data-lucide="arrow-right"></i><span>Syte operators</span></div><div class="mobile-sso-list">${records.map(r=>`<div><i data-lucide="fingerprint"></i><strong>${rowName(r)}</strong><small>${esc(String(r.issuer || rowState(r)))}</small></div>`).join('') || '<p>Connect an identity provider to centralize access.</p>'}</div></section>`,
    documentation:`<section class="mobile-docs"><header><i data-lucide="book-open-check"></i><div><p>Operator handbook</p><h2>Documentation</h2></div></header><a class="mobile-docs-cta" href="/api/"><span>Open interactive API reference</span><i data-lucide="arrow-up-right"></i></a><div class="mobile-docs-grid"><span>Deployments</span><span>Databases</span><span>Backups</span><span>Security</span></div></section>`,
    support:`<section class="mobile-support"><header><div><p>Incident desk</p><h2>Support diagnostics</h2></div><i data-lucide="life-buoy"></i></header><pre>syte diagnostics\nstatus: ready\nresources: ${data.resource_count || 0}\nnetwork: check on demand</pre>${action}<p class="mobile-muted">Run diagnostics to collect a current platform snapshot.</p></section>`,
  };
  const html = layouts[page] || layouts.overview;
  target.innerHTML = `<section class="mobile-domain-page mobile-${esc(page)}">${html}</section>`;
  target.querySelector('[data-mobile-form]')?.addEventListener('submit', async event => { event.preventDefault(); const formData = new FormData(event.currentTarget); try { await api(`/platform/navigation/${encodeURIComponent(page)}/records`,{method:'POST',body:JSON.stringify({primary:formData.get('primary'),secondary:formData.get('secondary')})}); await loadPlatformPage(page); } catch(error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; } });
  target.querySelectorAll('[data-mobile-action]').forEach(button => button.addEventListener('click', async () => { try { await api(`/platform/navigation/${encodeURIComponent(page)}/actions`,{method:'POST',body:JSON.stringify({action:PLATFORM_PAGE_BLUEPRINTS[page]?.control || `${page}-actions`})}); await loadPlatformPage(page); } catch(error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; } }));
  refreshIcons(); return true;
}

function metricPercent(value) {
  return Math.max(0, Math.min(100, Number(value || 0)));
}

function renderServerNavigationPerformance(metrics = liveSystemMetrics) {
  const indicator = document.getElementById('server-nav-performance');
  if (!indicator) return;
  if (!metrics || !Number.isFinite(Number(metrics.cpu_percent)) || !Number.isFinite(Number(metrics.ram_percent))) {
    indicator.className = 'nav-server-performance is-unavailable';
    indicator.setAttribute('aria-label', 'Server performance is loading');
    indicator.setAttribute('aria-valuenow', '0');
    indicator.title = 'Loading server performance';
    indicator.firstElementChild?.style.setProperty('width', '0%');
    return;
  }
  const cpu = metricPercent(metrics.cpu_percent);
  const ram = metricPercent(metrics.ram_percent);
  const load = Math.round((cpu + ram) / 2);
  const tone = load >= 85 ? 'is-high' : load >= 65 ? 'is-elevated' : 'is-healthy';
  indicator.className = `nav-server-performance ${tone}`;
  indicator.setAttribute('aria-label', `Combined server load ${load} percent, calculated from CPU ${Math.round(cpu)} percent and RAM ${Math.round(ram)} percent`);
  indicator.setAttribute('aria-valuenow', String(load));
  indicator.title = `Combined server load: ${load}% (CPU ${Math.round(cpu)}% + RAM ${Math.round(ram)}%)`;
  indicator.firstElementChild?.style.setProperty('width', `${load}%`);
}

function pushOverviewMetricSample(key, value) {
  const values = overviewMetricHistory[key];
  if (!values) return;
  values.push(metricPercent(value));
  if (values.length > 18) values.shift();
}

function recordLiveSystemMetrics(metrics) {
  liveSystemMetrics = metrics;
  pushOverviewMetricSample('ram', metrics.ram_percent);
  pushOverviewMetricSample('cpu', metrics.cpu_percent);
  pushOverviewMetricSample('disk', metrics.disk_percent);
  renderServerNavigationPerformance(metrics);
  renderOverviewLiveMetrics();
}

function serverChecklistCountryOptions(selected = '') {
  const countries = ['Local', 'Australia', 'Brazil', 'Canada', 'France', 'Germany', 'India', 'Japan', 'Netherlands', 'Poland', 'Romania', 'Singapore', 'United Kingdom', 'United States', 'Unknown'];
  const value = String(selected || 'Unknown');
  const options = countries.includes(value) ? countries : [value, ...countries];
  return options.map(country => `<option value="${esc(country)}" ${country === value ? 'selected' : ''}>${esc(country)}</option>`).join('');
}

function serverChecklistMetric(value) {
  return value == null || Number(value) <= 0 ? '—' : `${Math.round(Number(value))}%`;
}

function serverChecklistPing(value) {
  return value == null || Number(value) <= 0 ? 'Waiting' : `${Math.round(Number(value))} ms`;
}

function renderRemoteServersWorkspace(target) {
  target.innerHTML = '<section class="server-checklist-page"><p class="server-checklist-loading">Loading server checklist…</p></section>';
  api('/platform/fleet').then(fleet => {
    const nodes = fleet.nodes || [];
    const summary = fleet.summary || {};
    const reporting = nodes.filter(node => node.metrics);
    const averageLoad = reporting.length ? Math.round(reporting.reduce((sum, node) => sum + Number(node.load_percent || 0), 0) / reporting.length) : null;
    const serverRows = nodes.map(node => {
      const metrics = node.metrics || {};
      const status = String(node.status || 'pending');
      const isLocal = Boolean(node.is_local);
      return `<article class="server-checklist-row" data-server-row="${esc(node.uuid)}">
        <div class="server-checklist-identity"><span class="server-checklist-status ${esc(status)}" title="${esc(status)}"></span><div><input class="server-checklist-name" data-server-name="${esc(node.uuid)}" value="${esc(node.name || 'Unnamed server')}" aria-label="Server name"><small>${esc(node.host || 'Host pending')}${isLocal ? ' · Sycord host' : ''}</small></div></div>
        <label class="server-checklist-field"><span>Country</span><select data-server-country="${esc(node.uuid)}" aria-label="Country for ${esc(node.name || 'server')}">${serverChecklistCountryOptions(node.country)}</select></label>
        <div class="server-checklist-metrics" aria-label="Server resource usage"><span><small>CPU</small><b>${serverChecklistMetric(metrics.cpu_percent)}</b></span><span><small>RAM</small><b>${serverChecklistMetric(metrics.memory_percent)}</b></span><span><small>Disk</small><b>${serverChecklistMetric(metrics.disk_percent)}</b></span></div>
        <div class="server-checklist-state"><span class="server-checklist-state-pill ${esc(status)}"><i></i>${esc(status)}</span><small>${serverChecklistPing(metrics.ping_ms)}</small></div>
        <div class="server-checklist-actions"><button type="button" class="server-checklist-save" data-server-save="${esc(node.uuid)}">Save</button>${isLocal ? '<span class="server-checklist-main">Main</span>' : `<button type="button" class="server-checklist-setup" data-fleet-script="${esc(node.uuid)}">Setup</button>`}</div>
      </article>`;
    }).join('') || '<div class="server-checklist-empty"><i data-lucide="server"></i><h3>No servers yet</h3><p>Add the first server to begin monitoring its availability and resource usage.</p></div>';
    target.innerHTML = `<section class="server-checklist-page">
      <header class="server-checklist-header"><div><p>Infrastructure</p><h2>Servers</h2><span>Manage the Sycord host and every enrolled deployment server from one live checklist.</span></div><button class="server-checklist-refresh" type="button" data-server-refresh="1"><i data-lucide="refresh-cw"></i><span>Refresh</span></button></header>
      <section class="server-checklist-summary" aria-label="Server status summary"><article><span>Servers</span><strong>${summary.total_nodes || 0}</strong></article><article><span>Online</span><strong>${summary.online_nodes || 0}</strong></article><article><span>Combined load</span><strong>${averageLoad == null ? '—' : `${averageLoad}%`}</strong><small>CPU + RAM average</small></article><article><span>Heartbeat</span><strong>${reporting.length}/${nodes.length}</strong><small>Reporting nodes</small></article></section>
      <section class="server-checklist-add"><div><p>Add server</p><h3>Enroll a server</h3><span>Choose the display name and country yourself. Sycord stores the country locally and does not perform an external IP lookup.</span></div><form data-server-enroll="1"><label>Name<input name="name" required maxlength="120" placeholder="web-02"></label><label>Host<input name="host" required maxlength="255" placeholder="203.0.113.10"></label><label>Country<select name="country">${serverChecklistCountryOptions('Unknown')}</select></label><label>Type<select name="server_type"><option value="vps">VPS</option><option value="micro">Micro server</option><option value="dedicated">Dedicated</option><option value="edge">Edge</option><option value="build">Build worker</option></select></label><button type="submit"><i data-lucide="plus"></i><span>Add server</span></button></form></section>
      <section class="server-checklist-table"><header><div><p>Active servers</p><h3>Checklist</h3></div><span>${nodes.length} tracked</span></header><div class="server-checklist-columns" aria-hidden="true"><span>Server</span><span>Country</span><span>Performance</span><span>Status & ping</span><span>Actions</span></div><div class="server-checklist-list">${serverRows}</div></section>
      <div class="server-checklist-dialog" data-fleet-script-panel="1" hidden><section role="dialog" aria-modal="true" aria-label="Server heartbeat setup"><header><div><p>Server heartbeat</p><h3>Install monitoring helper</h3><span>Review the script before running it as root on the enrolled server.</span></div><button type="button" data-fleet-script-close="1" aria-label="Close"><i data-lucide="x"></i></button></header><pre data-fleet-script-content="1" tabindex="0"></pre><footer><button type="button" data-fleet-script-close="1">Close</button><button type="button" data-fleet-script-copy="1"><i data-lucide="copy"></i><span>Copy script</span></button></footer></section></div>
    </section>`;
    const refresh = () => renderRemoteServersWorkspace(target);
    target.querySelectorAll('[data-server-refresh]').forEach(button => button.addEventListener('click', refresh));
    target.querySelector('[data-server-enroll]')?.addEventListener('submit', async event => {
      event.preventDefault();
      const form = new FormData(event.currentTarget);
      const type = String(form.get('server_type') || 'vps');
      try {
        const result = await api('/platform/fleet/servers', {method: 'POST', body: JSON.stringify({name: form.get('name'), host: form.get('host'), country: form.get('country'), server_type: type, role_websites: type !== 'build', role_router: type === 'edge', role_workers: type === 'build', load_balancing_enabled: type !== 'build'})});
        toast(result.message || 'Server added.');
        refresh();
      } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not add the server.'); }
    });
    target.querySelectorAll('[data-server-save]').forEach(button => button.addEventListener('click', async () => {
      const id = button.dataset.serverSave;
      const name = target.querySelector(`[data-server-name="${CSS.escape(id)}"]`)?.value?.trim();
      const country = target.querySelector(`[data-server-country="${CSS.escape(id)}"]`)?.value;
      if (!name || !country) { toast('Enter both a server name and country.'); return; }
      button.disabled = true;
      try { const result = await api(`/platform/fleet/servers/${encodeURIComponent(id)}`, {method: 'PUT', body: JSON.stringify({name, country})}); toast(result.message || 'Server details saved.'); refresh(); }
      catch (error) { toast(normalizeFetchError(error?.message) || 'Could not save server details.'); button.disabled = false; }
    }));
    target.querySelectorAll('[data-fleet-script]').forEach(button => button.addEventListener('click', async () => {
      try {
        const result = await api(`/platform/fleet/servers/${encodeURIComponent(button.dataset.fleetScript)}/setup-script`);
        const panel = target.querySelector('[data-fleet-script-panel]');
        panel.hidden = false;
        target.querySelector('[data-fleet-script-content]').textContent = result.script;
      } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not generate the setup script.'); }
    }));
    target.querySelectorAll('[data-fleet-script-close]').forEach(button => button.addEventListener('click', () => { target.querySelector('[data-fleet-script-panel]').hidden = true; }));
    target.querySelector('[data-fleet-script-panel]')?.addEventListener('mousedown', event => { if (event.target === event.currentTarget) event.currentTarget.hidden = true; });
    target.querySelector('[data-fleet-script-copy]')?.addEventListener('click', async () => {
      try { await navigator.clipboard.writeText(target.querySelector('[data-fleet-script-content]').textContent); toast('Heartbeat setup script copied.'); }
      catch (_) { toast('Select the script and copy it manually.'); }
    });
    refreshIcons();
  }).catch(error => { target.innerHTML = `<section class="server-checklist-page"><p class="platform-error">Servers are unavailable: ${esc(normalizeFetchError(error?.message) || 'Unknown error')}</p></section>`; });
}

function renderDedicatedPage(page, data) {
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  if (page === 'remote-servers') { renderRemoteServersWorkspace(target); return; }
  if (page === 'docker') { target.innerHTML = ''; return; }
  target.innerHTML = '<section class="intentional-blank-page" aria-label="Blank workspace"></section>';
  return;
  const rows = data.resources || [];
  if (['profile','sessions','remote-servers','audit-logs','ssh-keys'].includes(page)) {
    const configs = {
      profile:{icon:'user-round-cog',tone:'violet',eyebrow:'Identity settings',title:'Your operator profile',intro:'Control how Syte identifies you across projects, deployments, and notifications.'},
      sessions:{icon:'scan-eye',tone:'rose',eyebrow:'Access control',title:'Active sessions',intro:'See where your operator account is signed in and remove access you no longer recognize.'},
      'remote-servers':{icon:'server-cog',tone:'green',eyebrow:'Infrastructure fleet',title:'Deployment nodes',intro:'Watch server availability and keep your deployment fleet ready for traffic.'},
      'audit-logs':{icon:'list-filter',tone:'slate',eyebrow:'Event history',title:'Audit trail',intro:'Search the full operator event stream with source and outcome context.'},
      'ssh-keys':{icon:'key-round',tone:'orange',eyebrow:'Secure credentials',title:'SSH key vault',intro:'Generate, fingerprint, and rotate keys used to reach deployment servers.'},
    }[page];
    const list = rows.slice(0, 12);
    const content = {
      profile:`<div class="six-profile-layout"><div class="six-profile-avatar">${esc(String((list[0]?.name || 'OP').slice(0,2)).toUpperCase())}</div><div class="six-profile-fields"><label>Display name<input id="six-profile-name" value="${esc(list[0]?.name || '')}" placeholder="Your name"></label><label>Email address<input id="six-profile-email" value="${esc(list[0]?.email || '')}" placeholder="operator@example.com" type="email"></label><label>Timezone<select id="six-profile-timezone"><option>UTC</option><option>Europe/Berlin</option><option>America/New_York</option><option>Asia/Tokyo</option></select></label><button class="dedicated-primary" data-six-action="profile-save"><i data-lucide="save"></i>Save profile</button></div></div>`,
      sessions:`<div class="six-security-banner"><i data-lucide="shield-check"></i><div><strong>${list.length || 0} active sessions detected</strong><span>Review device, browser, and last-seen information before revoking access.</span></div><button class="dedicated-primary" data-six-action="sessions-revoke"><i data-lucide="log-out"></i>Revoke stale</button></div><div class="six-session-list">${list.map(row => `<div><i data-lucide="monitor-smartphone"></i><span><strong>${esc(row.user_agent || row.name || 'Operator session')}</strong><small>${esc(row.last_seen_at || row.created_at || 'Recently active')}</small></span><em>${esc(row.status || 'active')}</em></div>`).join('') || '<p class="dedicated-empty">No session records have been reported.</p>'}</div>`,
      'remote-servers':`<div class="six-node-grid">${list.map((row,index) => `<article class="six-node-card"><div class="six-node-top"><i data-lucide="server"></i><span>${esc(row.status || 'ready')}</span></div><h3>${esc(row.name || `Deployment node ${index+1}`)}</h3><p>${esc(row.ip || row.host || 'Host not configured')}</p><div class="six-node-bar"><span style="width:${Math.min(100,Math.max(8,Number(row.percent || row.status_percent || 72)))}%"></span></div><small>Availability</small></article>`).join('') || '<p class="dedicated-empty">No deployment nodes configured.</p>'}</div><button class="dedicated-primary" data-six-action="server-add"><i data-lucide="plus"></i>Add deployment node</button>`,
      'audit-logs':`<div class="six-audit-toolbar"><label class="six-search"><i data-lucide="search"></i><input id="six-audit-filter" placeholder="Filter events, sources, or actors…"></label><button class="docker-library-sort" data-six-action="audit-refresh"><i data-lucide="refresh-cw"></i>Refresh</button></div><div class="six-audit-timeline">${list.map(row => `<div class="six-audit-event"><span class="six-audit-dot"></span><div><strong>${esc(row.event || row.name || 'Platform event')}</strong><p>${esc(row.message || row.source || row._table || 'Recorded operator activity')}</p><small>${esc(row.created_at || 'Recently')}</small></div><em>${esc(row.accepted === 0 ? 'blocked' : 'accepted')}</em></div>`).join('') || '<p class="dedicated-empty">No audit events recorded.</p>'}</div>`,
      'ssh-keys':`<div class="six-key-layout"><form class="six-key-generator" id="six-ssh-form"><div class="six-form-icon"><i data-lucide="key-round"></i></div><h3>Generate a deployment key</h3><p>Private material is returned once and never rendered in the key list.</p><label>Key name<input name="name" required placeholder="production-deploy"></label><label>Algorithm<select name="algorithm"><option value="ed25519">Ed25519 · recommended</option><option value="rsa">RSA · compatibility</option></select></label><button class="dedicated-primary" type="submit"><i data-lucide="wand-sparkles"></i>Generate key</button></form><div class="six-key-list"><p class="dedicated-label">Stored fingerprints</p>${list.map(row => `<div><i data-lucide="key"></i><span><strong>${esc(row.name || 'Deployment key')}</strong><small>${esc(row.fingerprint || row.public_key || 'Fingerprint pending')}</small></span><button data-six-action="key-rotate" data-key-id="${esc(row.uuid || '')}" aria-label="Rotate key"><i data-lucide="rotate-cw"></i></button></div>`).join('') || '<p class="dedicated-empty">No generated keys yet.</p>'}</div></div>`,
    }[page];
    target.innerHTML = `<section class="six-page six-${cfg.tone}"><div class="six-page-hero"><div class="dedicated-icon"><i data-lucide="${cfg.icon}"></i></div><div><p class="dedicated-eyebrow">${cfg.eyebrow}</p><h2>${cfg.title}</h2><p>${cfg.intro}</p></div></div><div class="six-page-body">${content}</div></section>`;
    target.querySelector('#six-ssh-form')?.addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const result = await api('/platform/ssh-keys/generate',{method:'POST',body:JSON.stringify({name:form.get('name'),algorithm:form.get('algorithm')})}); document.getElementById('platform-page-message').textContent = `${result.message} Fingerprint: ${result.key?.fingerprint || 'generated'}`; await loadPlatformPage(page); } catch(error) { document.getElementById('platform-page-message').textContent = `Key generation failed: ${error.message}`; } });
    target.querySelectorAll('[data-six-action]').forEach(button => button.addEventListener('click', async () => { const action = button.dataset.sixAction; if (action === 'profile-save') { const name = document.getElementById('six-profile-name')?.value; const email = document.getElementById('six-profile-email')?.value; await api('/platform/navigation/profile/records',{method:'POST',body:JSON.stringify({primary:name,secondary:email})}); } else { await api(`/platform/navigation/${encodeURIComponent(page)}/actions`,{method:'POST',body:JSON.stringify({action:PLATFORM_PAGE_BLUEPRINTS[page]?.control || `${page}-actions`})}); } await loadPlatformPage(page); }));
    refreshIcons(); return;
  }
  const cfg = DEDICATED_PAGE_CONFIG[page] || DEDICATED_PAGE_CONFIG.overview;
  const genericRows = data.resources || [];
  const rowsHtml = genericRows.slice(0, 8).map(row => `<li><span><strong>${esc(String(row.name || row.title || row.uuid || row.event || 'Resource'))}</strong><small>${esc(String(row.status || row.state || row.type || row._table || 'tracked'))}</small></span><i data-lucide="chevron-right"></i></li>`).join('') || '<li class="dedicated-empty">No records yet. Use the action above to create the first one.</li>';
  const fields = cfg.fields ? `<form class="dedicated-form" data-dedicated-form="${esc(page)}"><label>${esc(cfg.fields[0])}<input name="primary" required></label><label>${esc(cfg.fields[1])}<input name="secondary" required></label><button type="submit" class="dedicated-primary"><i data-lucide="plus"></i>${esc(cfg.action)}</button></form>` : `<button type="button" class="dedicated-primary" data-dedicated-action="${esc(page)}"><i data-lucide="zap"></i>${esc(cfg.action)}</button>`;
  target.innerHTML = `<section class="dedicated-shell dedicated-${esc(cfg.tone)}"><div class="dedicated-hero"><div class="dedicated-icon"><i data-lucide="${esc(cfg.icon)}"></i></div><div><p class="dedicated-eyebrow">${esc(cfg.eyebrow)}</p><h2>${esc(cfg.title)}</h2><p>${esc(cfg.intro)}</p></div></div><div class="dedicated-stat-row"><div><span>Tracked resources</span><strong>${esc(String(data.resource_count || 0))}</strong></div><div><span>Workspace status</span><strong>Operational</strong></div><div><span>Last sync</span><strong>Live</strong></div></div><div class="dedicated-columns"><div class="dedicated-action-panel"><p class="dedicated-label">Primary workflow</p>${fields}<p class="dedicated-note">Changes are persisted through the Syte platform API.</p></div><div class="dedicated-list-panel"><div class="dedicated-panel-head"><div><p class="dedicated-label">Live records</p><h3>${esc(cfg.title)} activity</h3></div><span>${esc(String(genericRows.length))} shown</span></div><ul>${rowsHtml}</ul></div></div></section>`;
  target.querySelector('form')?.addEventListener('submit', async event => { event.preventDefault(); const form = new FormData(event.currentTarget); try { const ssh = page === 'ssh-keys'; const endpoint = ssh ? '/platform/ssh-keys/generate' : `/platform/navigation/${encodeURIComponent(page)}/records`; const body = ssh ? {name:form.get('primary'), algorithm:form.get('secondary') || 'ed25519'} : {primary:form.get('primary'), secondary:form.get('secondary')}; const result = await api(endpoint, {method:'POST', body:JSON.stringify(body)}); if (ssh) { const message = document.getElementById('platform-page-message'); if (message) message.textContent = `${result.message} Fingerprint: ${result.key?.fingerprint || 'generated'}`; } await loadPlatformPage(page); } catch (error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; } });
  target.querySelector('[data-dedicated-action]')?.addEventListener('click', async event => { const btn = event.currentTarget; btn.disabled = true; try { await api(`/platform/navigation/${encodeURIComponent(page)}/actions`, {method:'POST', body:JSON.stringify({action: PLATFORM_PAGE_BLUEPRINTS[page]?.control || `${page}-actions`})}); await loadPlatformPage(page); } catch (error) { document.getElementById('platform-page-message').textContent = `Action failed: ${error.message}`; btn.disabled = false; } });
  refreshIcons();
}

function renderPlatformDetails(page, resources) {
  const table = document.getElementById('platform-detail-table');
  const columns = PLATFORM_PAGE_BLUEPRINTS[page]?.columns || ['name','status'];
  if (!table) return;
  table.innerHTML = resources?.length ? `<table><thead><tr>${columns.map(column => `<th>${esc(column.replaceAll('_',' '))}</th>`).join('')}</tr></thead><tbody>${resources.map(row => `<tr>${columns.map(column => `<td>${esc(String(row[column] ?? '—'))}</td>`).join('')}</tr>`).join('')}</tbody></table>` : '<div class="platform-empty">No operational records yet.</div>';
}

function overviewMetricState(value) {
  const percent = metricPercent(value);
  if (percent >= 85) return ['critical', 'High'];
  if (percent >= 65) return ['watch', 'Observe'];
  return ['healthy', 'Healthy'];
}

function overviewSparklinePoints(values = []) {
  const data = values.length > 1 ? values : [values[0] || 0, values[0] || 0];
  return data.map((value, index) => `${(index / (data.length - 1)) * 100},${94 - metricPercent(value) * .82}`).join(' ');
}

function overviewMetricDetail(metric, key) {
  if (key === 'ram' && metric.ram_used_mb != null && metric.ram_total_mb != null) {
    return `${(Number(metric.ram_used_mb) / 1024).toFixed(1)} GB of ${(Number(metric.ram_total_mb) / 1024).toFixed(1)} GB`;
  }
  if (key === 'disk' && metric.disk_used_gb != null && metric.disk_total_gb != null) {
    return `${Number(metric.disk_used_gb).toFixed(1)} GB of ${Number(metric.disk_total_gb).toFixed(1)} GB`;
  }
  return key === 'cpu' ? 'Live processor usage' : 'Live host capacity';
}

function renderOverviewLiveMetrics() {
  const target = document.getElementById('platform-dedicated-page');
  if (!target || !target.querySelector('.overview-metric-grid') || !liveSystemMetrics) return;
  const system = liveSystemMetrics;
  [['ram', 'ram_percent'], ['cpu', 'cpu_percent'], ['disk', 'disk_percent']].forEach(([key, field]) => {
    const card = target.querySelector(`[data-overview-metric="${key}"]`);
    if (!card) return;
    const percent = Math.round(metricPercent(system[field]));
    const [tone, state] = overviewMetricState(percent);
    card.className = `overview-metric-card ${tone}`;
    card.querySelector('[data-overview-value]')?.replaceChildren(`${percent}%`);
    card.querySelector('[data-overview-detail]')?.replaceChildren(overviewMetricDetail(system, key));
    const status = card.querySelector('[data-overview-status]');
    if (status) status.textContent = state;
    const line = card.querySelector('polyline');
    if (line) line.setAttribute('points', overviewSparklinePoints(overviewMetricHistory[key]));
  });
  const checked = target.querySelector('[data-overview-updated]');
  if (checked) checked.textContent = `Updated ${new Date().toLocaleTimeString([], {hour: '2-digit', minute: '2-digit'})}`;
}

function renderOverviewHealth(data) {
  const metrics = data.metrics || {};
  const services = data.services || {};
  const overall = String(data.overall || 'attention');
  const overallCopy = overall === 'healthy'
    ? 'All core services are responding normally.'
    : overall === 'degraded'
      ? 'A managed service needs attention.'
      : 'Review the workspace before the next deployment.';
  const fallback = liveSystemMetrics || {
    ram_percent: metrics.ram_percent ?? metrics.memory_percent,
    cpu_percent: metrics.cpu_percent,
    disk_percent: metrics.disk_percent,
    ram_used_mb: metrics.ram_used_mb,
    ram_total_mb: metrics.ram_total_mb,
    disk_used_gb: metrics.disk_used_gb,
    disk_total_gb: metrics.disk_total_gb,
  };
  if (!liveSystemMetrics) {
    liveSystemMetrics = fallback;
    pushOverviewMetricSample('ram', fallback.ram_percent);
    pushOverviewMetricSample('cpu', fallback.cpu_percent);
    pushOverviewMetricSample('disk', fallback.disk_percent);
  }
  const metricCard = (key, label, icon, field) => {
    const percent = Math.round(metricPercent(fallback[field]));
    const [tone, state] = overviewMetricState(percent);
    return `<article class="overview-metric-card ${tone}" data-overview-metric="${key}"><header><span>${esc(label)}</span><strong data-overview-value>${percent}%</strong></header><div class="overview-sparkline" aria-hidden="true"><svg viewBox="0 0 100 100" preserveAspectRatio="none"><path d="M0 18H100M0 42H100M0 66H100M0 90H100"></path><polyline points="${overviewSparklinePoints(overviewMetricHistory[key])}"></polyline></svg></div><footer><span class="overview-metric-state" data-overview-status><i></i>${esc(state)}</span><small data-overview-detail>${esc(overviewMetricDetail(fallback, key))}</small></footer></article>`;
  };
  const serviceRow = (key, label, icon) => {
    const service = services[key] || {state: 'unavailable', detail: 'Status unavailable.'};
    const state = String(service.state || 'unavailable');
    return `<article class="overview-service-row"><span class="overview-service-icon"><i data-lucide="${icon}"></i></span><div><strong>${esc(label)}</strong><small>${esc(service.detail || 'Status unavailable.')}</small></div><span class="overview-state ${esc(state)}"><i></i>${esc(state)}</span></article>`;
  };
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  target.innerHTML = `<section class="overview-workspace" aria-live="polite"><header class="overview-workspace-hero"><div><p>Workspace health</p><h2>Overview</h2><span>${esc(overallCopy)}</span></div><span class="overview-overall-state ${esc(overall)}"><i></i>${esc(overall === 'healthy' ? 'Operational' : overall === 'degraded' ? 'Attention required' : 'Review needed')}</span></header><section class="overview-metric-grid" aria-label="Live host performance">${metricCard('ram', 'RAM', 'memory-stick', 'ram_percent')}${metricCard('cpu', 'CPU', 'cpu', 'cpu_percent')}${metricCard('disk', 'Disk', 'hard-drive', 'disk_percent')}</section><p class="overview-metric-updated" data-overview-updated>Waiting for the next metric sample</p><section class="overview-services-card" aria-labelledby="overview-services-title"><div class="overview-services-heading"><div><p>Service status</p><h3 id="overview-services-title">Core services</h3></div><button type="button" class="btn-pill btn-ghost btn-sm" id="overview-health-refresh"><i data-lucide="refresh-cw"></i><span>Refresh</span></button></div><div class="overview-service-list">${serviceRow('web', 'Web service', 'monitor-up')}${serviceRow('api', 'API', 'braces')}${serviceRow('apps', 'Managed apps', 'layers-3')}</div></section></section>`;
  target.querySelector('#overview-health-refresh')?.addEventListener('click', () => loadPlatformPage('overview'));
  renderOverviewLiveMetrics();
  refreshIcons();
}

async function renderProfileWorkspace() {
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  target.innerHTML = '<section class="legacy-profile-gate"><p class="profile-loading">Checking operator session…</p></section>';
  try {
    const session = await api('/operator/session');
    syraCsrfToken = session.authenticated ? (session.csrf_token || '') : '';
    if (!session.authenticated) {
      target.innerHTML = `<section class="legacy-profile-gate"><div class="legacy-profile-card"><div class="legacy-profile-brand"><span>S</span><div><p>Syte operator</p><h1>Welcome back</h1></div></div><p>Sign in to manage your protected operator profile.</p><form id="legacy-profile-login" class="legacy-profile-form"><label for="legacy-profile-key">Operator key</label><input id="legacy-profile-key" type="password" autocomplete="current-password" placeholder="Enter your operator key" required><button class="btn-pill btn-primary" type="submit">Sign in</button></form><small id="legacy-profile-message">Your key is exchanged for a secure HttpOnly session cookie.</small></div></section>`;
      target.querySelector('#legacy-profile-login')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const input = target.querySelector('#legacy-profile-key');
        const message = target.querySelector('#legacy-profile-message');
        try {
          const result = await api('/operator/session', {method: 'POST', body: JSON.stringify({bootstrap_token: input?.value || ''})});
          syraCsrfToken = result.csrf_token || '';
          setSyraSessionState(true);
          await renderProfileWorkspace();
        } catch (error) { if (message) message.textContent = error.message; }
      });
      return;
    }
    setSyraSessionState(true);
    const current = await api('/auth/profile');
    const profile = current.account || {};
    const name = profile.display_name || 'Operator';
    let selectedAvatar = profile.avatar_icon || 'user';
    const choices = Object.entries(SYTE_ACCOUNT_ICON).map(([key, icon]) => `<button type="button" data-avatar-icon="${key}" class="legacy-avatar-choice ${key === selectedAvatar ? 'selected' : ''}" aria-label="Use ${key} profile icon"><i data-lucide="${icon}"></i></button>`).join('');
    target.innerHTML = `<section class="legacy-profile-page"><header><span class="legacy-profile-avatar"><i data-lucide="${SYTE_ACCOUNT_ICON[selectedAvatar] || 'user-round'}"></i></span><div><p>Authenticated Syte account</p><h1>${esc(name)}</h1><small>${esc(profile.email || 'No email address configured')}</small></div><button type="button" class="btn-pill btn-ghost" id="legacy-profile-logout">Sign out</button></header><div class="legacy-profile-grid"><form id="legacy-profile-form" class="legacy-profile-card legacy-profile-form"><h2>Profile</h2><p>Choose the identity shown across your Syte workspace.</p><label for="legacy-profile-name">Display name</label><input id="legacy-profile-name" value="${esc(profile.display_name || '')}" maxlength="120" placeholder="Your name"><label>Profile icon</label><div class="legacy-avatar-picker">${choices}</div><button class="btn-pill btn-primary" type="submit">Save profile</button><small id="legacy-profile-save-message"></small></form><aside class="legacy-profile-card legacy-profile-security"><h2>Account security</h2><p>Your email is your sign-in identity. The profile icon is displayed on every authenticated Syte page.</p><dl><div><dt>Email</dt><dd>${esc(profile.email || '')}</dd></div><div><dt>Role</dt><dd>${esc(profile.role || 'operator')}</dd></div></dl></aside></div></section>`;
    target.querySelectorAll('[data-avatar-icon]').forEach((choice) => choice.addEventListener('click', () => {
      selectedAvatar = choice.dataset.avatarIcon || 'user';
      target.querySelectorAll('[data-avatar-icon]').forEach((item) => item.classList.toggle('selected', item.dataset.avatarIcon === selectedAvatar));
    }));
    target.querySelector('#legacy-profile-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const message = target.querySelector('#legacy-profile-save-message');
      try {
        const result = await api('/auth/profile', {method: 'PUT', body: JSON.stringify({display_name: target.querySelector('#legacy-profile-name')?.value || '', avatar_icon: selectedAvatar})});
        syteAccount = result.account || syteAccount;
        renderLegacyAccountCorner(syteAccount);
        if (message) message.textContent = result.message || 'Profile updated.';
      } catch (error) { if (message) message.textContent = error.message; }
    });
    target.querySelector('#legacy-profile-logout')?.addEventListener('click', async () => {
      try { await api('/auth/session', {method: 'DELETE'}); } finally { syraCsrfToken = ''; syteAccount = null; setSyraSessionState(false); document.getElementById('legacy-account-corner')?.classList.add('hidden'); document.getElementById('account-login-screen')?.classList.remove('hidden'); document.body.classList.add('account-auth-pending'); await initializeLegacyAccountGate(); }
    });
  } catch (error) {
    target.innerHTML = `<section class="legacy-profile-gate"><p class="profile-loading">${esc(error.message)}</p></section>`;
  }
}

async function loadPlatformPage(page = 'overview') {
  const safePage = PLATFORM_PAGE_LABELS[page] ? page : 'overview';
  activePlatformPage = safePage;
  const title = document.getElementById('platform-page-title');
  const description = document.getElementById('platform-page-description');
  const list = document.getElementById('platform-resource-list');
  const actions = document.getElementById('platform-action-list');
  const summary = document.getElementById('platform-summary-grid');
  const count = document.getElementById('platform-resource-count');
  const message = document.getElementById('platform-page-message');
  const workspace = document.getElementById('platform-workspace');
  const isOverview = safePage === 'overview';
  const isProfile = safePage === 'profile';
  const isRemoteServers = safePage === 'remote-servers';
  const isGit = safePage === 'git';
  const isNotifications = safePage === 'notifications';
  const isCertificates = safePage === 'certificates';
  const isBlankWorkspace = safePage !== 'docker' && !isOverview && !isProfile && !isRemoteServers && !isGit && !isNotifications && !isCertificates;
  workspace?.classList.toggle('is-blank-workspace', isBlankWorkspace);
  workspace?.classList.toggle('is-overview-workspace', isOverview);
  workspace?.classList.toggle('is-profile-workspace', isProfile);
  workspace?.classList.toggle('is-remote-servers-workspace', isRemoteServers);
  if (isBlankWorkspace) {
    const blankTarget = document.getElementById('platform-dedicated-page');
    if (blankTarget) blankTarget.innerHTML = '<section class="intentional-blank-page" aria-label="Blank workspace"></section>';
    return;
  }
  if (isProfile) {
    await renderProfileWorkspace();
    return;
  }
  if (isGit) {
    await renderGitWorkspace();
    return;
  }
  if (isNotifications) {
    await renderNotificationWorkspace();
    return;
  }
  if (isCertificates) {
    await renderCertificateWorkspace();
    return;
  }
  if (isOverview) {
    const blankTarget = document.getElementById('platform-dedicated-page');
    if (blankTarget) blankTarget.innerHTML = '<section class="overview-loading">Loading system health…</section>';
    try {
      renderOverviewHealth(await api('/platform/overview/health'));
    } catch (error) {
      if (blankTarget) blankTarget.innerHTML = `<section class="overview-error">System health is unavailable: ${esc(error.message)}</section>`;
    }
    return;
  }
  if (title) title.textContent = PLATFORM_PAGE_LABELS[safePage];
  if (list) list.innerHTML = '<div class="platform-loading">Loading live platform data…</div>';
  if (actions) actions.innerHTML = '';
  try {
    const data = await api(`/platform/navigation/${encodeURIComponent(safePage)}`);
    if (description) description.textContent = data.description || '';
    if (count) count.textContent = String(data.resource_count || 0);
    const blueprint = PLATFORM_PAGE_BLUEPRINTS[safePage] || {};
    renderDedicatedPage(safePage, data);
    const heading = document.getElementById('platform-resource-heading');
    if (heading) heading.textContent = blueprint.heading || 'Resources';
    renderPlatformControls(safePage, data);
    if (summary) summary.innerHTML = [
      ['Resources', data.resource_count || 0],
      ['Page', PLATFORM_PAGE_LABELS[safePage]],
      ['Source', 'Syte platform API'],
    ].map(([label, value]) => `<div class="platform-summary-card"><span>${esc(String(label))}</span><strong>${esc(String(value))}</strong></div>`).join('');
    if (list) list.innerHTML = data.resources?.length
      ? data.resources.map((row) => {
        const name = row.name || row.title || row.uuid || row.id || 'Resource';
        const status = row.status || row.state || row.type || row._table || 'tracked';
        return `<div class="platform-resource-row"><span><strong>${esc(String(name))}</strong><small>${esc(String(row._table || 'platform resource'))}</small></span><em>${esc(String(status))}</em></div>`;
      }).join('')
      : '<div class="platform-empty">No records are configured for this page yet. The page is live and will populate as resources are added.</div>';
    renderPlatformDetails(safePage, data.resources || []);
    if (actions) actions.innerHTML = (data.actions || []).map((action) => action.href
      ? `<a class="btn-pill btn-ghost" href="${esc(action.href)}"><i data-lucide="external-link"></i><span>${esc(action.label)}</span></a>`
      : `<button type="button" class="btn-pill btn-ghost platform-action-btn" data-action="${esc(action.id)}"><i data-lucide="refresh-cw"></i><span>${esc(action.label)}</span></button>`).join('');
    if (message) message.textContent = 'Data is sourced from the running Syte platform store; secret values are never rendered.';
    refreshIcons();
  } catch (error) {
    if (list) list.innerHTML = `<div class="platform-error">Could not load this page: ${esc(error.message)}</div>`;
    if (message) message.textContent = 'The page is available, but the protected platform API did not return data.';
  }
}

let globalAiActiveTab = 'chat';
let globalAiProviderType = '9router';

const GLOBAL_AI_PROVIDER_PRESETS = {
  '9router': { name: '9Router', apiBase: '', model: '', copy: 'Add an API key from your 9Router dashboard. Enabled 9Router models appear in the model picker.' },
  openai: { name: 'OpenAI', apiBase: 'https://api.openai.com/v1', model: 'gpt-4.1-mini', copy: 'Use an OpenAI project API key. The Agent sends requests from the Syte server using OpenAI-compatible chat completions.' },
  anthropic: { name: 'Anthropic', apiBase: 'https://api.anthropic.com/v1', model: 'claude-sonnet-4-6', copy: 'Use an Anthropic API key. Anthropic’s OpenAI-compatible endpoint is used for Agent chat and tool calls.' },
  openrouter: { name: 'OpenRouter', apiBase: 'https://openrouter.ai/api/v1', model: 'openai/gpt-4.1-mini', copy: 'Use an OpenRouter API key and choose a provider/model identifier for the default model.' },
  custom: { name: '', apiBase: '', model: '', copy: 'Connect any HTTPS OpenAI-compatible API with its server-side API key, base URL, and default model ID.' },
};

function setGlobalAiProviderType(providerType) {
  const next = GLOBAL_AI_PROVIDER_PRESETS[providerType] ? providerType : '9router';
  globalAiProviderType = next;
  const preset = GLOBAL_AI_PROVIDER_PRESETS[next];
  document.querySelectorAll('.global-ai-provider-type').forEach((button) => {
    const active = button.dataset.providerType === next;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-checked', String(active));
  });
  const fields = document.getElementById('global-ai-provider-fields');
  const copy = document.getElementById('global-ai-provider-preset-copy');
  const presetIcon = document.querySelector('#global-ai-provider-preset i');
  const name = document.getElementById('global-ai-provider-name');
  const base = document.getElementById('global-ai-provider-base');
  const model = document.getElementById('global-ai-provider-model');
  const save = document.getElementById('global-ai-save-provider');
  fields?.classList.toggle('hidden', next === '9router');
  if (copy) copy.textContent = preset.copy;
  if (presetIcon) presetIcon.setAttribute('data-lucide', next === '9router' ? 'route' : next === 'custom' ? 'plug-zap' : 'key-round');
  if (name) name.value = preset.name;
  if (base) base.value = preset.apiBase;
  if (model) model.value = preset.model;
  if (save) save.querySelector('span').textContent = next === '9router' ? 'Save 9Router key' : `Add ${preset.name || 'provider'}`;
  refreshIcons();
}

function renderGlobalAiProviderList(data) {
  const list = document.getElementById('global-ai-provider-list');
  if (!list) return;
  const router = data?.provider || {};
  const routerCatalog = data?.router_catalog || {};
  const external = Array.isArray(data?.external_providers) ? data.external_providers : [];
  const cards = [{
    name: '9Router',
    type: 'Gateway',
    detail: routerCatalog.count ? `${routerCatalog.count} discovered model${routerCatalog.count === 1 ? '' : 's'}` : 'Connect a 9Router key to load models',
    connected: Boolean(router.api_key_set),
    icon: 'route',
  }, ...external.map((provider) => ({
    name: provider.name,
    type: provider.id === 'anthropic' ? 'Anthropic compatible' : 'OpenAI compatible',
    detail: provider.default_model,
    connected: Boolean(provider.api_key_set),
    icon: provider.id === 'anthropic' ? 'brain-circuit' : 'key-round',
  }))];
  list.innerHTML = cards.map((provider) => `<article class="global-ai-provider-card ${provider.connected ? 'is-connected' : ''}"><span class="global-ai-provider-icon"><i data-lucide="${provider.icon}"></i></span><span class="global-ai-provider-card-copy"><strong>${esc(provider.name)}</strong><small>${esc(provider.type)} · ${esc(provider.detail)}</small></span><span class="global-ai-provider-state"><i data-lucide="${provider.connected ? 'check' : 'minus'}"></i>${provider.connected ? 'Connected' : 'Not connected'}</span></article>`).join('');
  refreshIcons();
}

async function loadGlobalAiProviderCatalog() {
  const list = document.getElementById('global-ai-provider-list');
  if (!list) return;
  list.innerHTML = '<p class="hint">Loading provider status…</p>';
  try {
    const data = await api('/models');
    syncCustomModelOptions(data.available_models || []);
    renderGlobalAiProviderList(data);
    syncGlobalAiModelSelection();
  } catch (error) {
    list.innerHTML = `<p class="hint">Could not load providers: ${esc(normalizeFetchError(error.message))}</p>`;
  }
}

async function saveGlobalAiProvider() {
  const apiKey = document.getElementById('global-ai-provider-api-key')?.value?.trim() || '';
  const button = document.getElementById('global-ai-save-provider');
  const preset = GLOBAL_AI_PROVIDER_PRESETS[globalAiProviderType];
  if (!apiKey) return toast('Paste an API key before saving this provider.');
  if (button) {
    button.disabled = true;
    button.querySelector('span').textContent = 'Saving…';
  }
  try {
    const data = await api('/models/provider', {
      method: 'PUT',
      body: JSON.stringify({
        provider_type: globalAiProviderType,
        api_key: apiKey,
        name: document.getElementById('global-ai-provider-name')?.value?.trim() || preset.name,
        api_base: document.getElementById('global-ai-provider-base')?.value?.trim() || preset.apiBase,
        default_model: document.getElementById('global-ai-provider-model')?.value?.trim() || preset.model,
      }),
    });
    const input = document.getElementById('global-ai-provider-api-key');
    if (input) input.value = '';
    toast(data.message || `${preset.name || 'Provider'} saved.`);
    await loadGlobalAiProviderCatalog();
    await loadSettings();
  } catch (error) {
    toast(`Could not save provider: ${normalizeFetchError(error.message)}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.querySelector('span').textContent = globalAiProviderType === '9router'
        ? 'Save 9Router key'
        : `Add ${preset.name || 'provider'}`;
    }
  }
}


function updateGlobalAiModelSummary() {
  const session = document.getElementById('global-ai-session-model');
  const label = document.getElementById('global-ai-model-label');
  const description = document.getElementById('global-ai-model-description');
  const profile = session?.value || document.getElementById('debug-chat-profile')?.value || 'auto';
  const option = session?.options?.[session.selectedIndex];
  if (label) label.textContent = profile === 'auto' ? 'Automatic routing' : (option?.textContent || profile);
  if (description) description.textContent = profile === 'auto'
    ? 'Syte selects the best available model for each task.'
    : 'This profile is applied to the next message in the selected project.';
}

function syncGlobalAiModelSelection(profile = document.getElementById('debug-chat-profile')?.value || 'auto') {
  const chatProfile = document.getElementById('debug-chat-profile');
  const sessionModel = document.getElementById('global-ai-session-model');
  if (chatProfile && profile && [...chatProfile.options].some((option) => option.value === profile)) {
    chatProfile.value = profile;
  }
  if (sessionModel && profile && [...sessionModel.options].some((option) => option.value === profile)) {
    sessionModel.value = profile;
  }
  updateGlobalAiModelSummary();
}

function setGlobalAiTab(tab) {
  const next = tab === 'models' ? 'models' : 'chat';
  globalAiActiveTab = next;
  const chatTab = document.getElementById('global-ai-tab-chat');
  const modelsTab = document.getElementById('global-ai-tab-models');
  const chatPanel = document.getElementById('global-ai-panel-chat');
  const modelsPanel = document.getElementById('global-ai-panel-models');
  chatTab?.classList.toggle('is-active', next === 'chat');
  modelsTab?.classList.toggle('is-active', next === 'models');
  chatTab?.setAttribute('aria-selected', String(next === 'chat'));
  modelsTab?.setAttribute('aria-selected', String(next === 'models'));
  chatPanel?.classList.toggle('hidden', next !== 'chat');
  modelsPanel?.classList.toggle('hidden', next !== 'models');
  if (next === 'models') {
    syncGlobalAiModelSelection();
    void loadSettings();
    void loadGlobalAiProviderCatalog();
  }
  refreshIcons();
}

async function saveGlobalAiDefaultModel() {
  const select = document.getElementById('global-ai-default-model');
  const button = document.getElementById('global-ai-save-default-model');
  const profile = select?.value;
  if (!profile) return toast('Choose a default model first.');
  if (button) {
    button.disabled = true;
    button.querySelector('span').textContent = 'Saving…';
  }
  try {
    const result = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({ agent_default_model_profile: profile }),
    });
    const legacySelect = document.getElementById('agent-default-profile');
    if (legacySelect && [...legacySelect.options].some((option) => option.value === profile)) legacySelect.value = profile;
    toast(Array.isArray(result.messages) ? result.messages.join(' ') : 'Default model saved');
  } catch (error) {
    toast(`Could not save default model: ${normalizeFetchError(error.message)}`);
  } finally {
    if (button) {
      button.disabled = false;
      button.querySelector('span').textContent = 'Save default';
    }
  }
}

async function openGlobalAiWorkspace() {
  const host = document.getElementById('global-ai-chat-host');
  const panel = document.getElementById('svc-panel-debug-chat');
  const select = document.getElementById('global-ai-project');
  if (!host || !panel || !select) return;
  if (panel.parentElement !== host) host.appendChild(panel);

  await loadProjects({ silent: true });
  await ensureDebugChatModelOptions();
  syncGlobalAiModelSelection();
  const previous = select.value || activeServiceId || '';
  select.innerHTML = '<option value="">Choose a project</option>' + projects
    .map(project => `<option value="${esc(project.id)}">${esc(displayTitle(project))}</option>`)
    .join('');
  select.value = projects.some(project => project.id === previous) ? previous : '';
  if (select.value) await setGlobalAiProject(select.value, { focus: false });
  else renderGlobalAiProjectContext(null);
}

function renderGlobalAiProjectContext(project) {
  const chip = document.getElementById('global-ai-context-chip');
  const empty = document.getElementById('global-ai-empty');
  const host = document.getElementById('global-ai-chat-host');
  const avatar = document.getElementById('global-ai-project-avatar');
  const name = document.getElementById('global-ai-project-name');
  const meta = document.getElementById('global-ai-project-meta');
  chip?.classList.toggle('hidden', !project);
  empty?.classList.toggle('hidden', Boolean(project));
  host?.classList.toggle('hidden', !project);
  if (!project) return;
  if (avatar) avatar.textContent = ((project.name || project.domain || 'S').trim()[0] || 'S').toUpperCase();
  if (name) name.textContent = displayTitle(project);
  if (meta) meta.textContent = `${project.domain || project.branch || 'Project'} · ${project.running ? 'live' : 'not running'}`;
}

async function setGlobalAiProject(projectId, { focus = true } = {}) {
  const project = projects.find(item => item.id === projectId);
  const select = document.getElementById('global-ai-project');
  if (!project) {
    renderGlobalAiProjectContext(null);
    return;
  }
  activeServiceId = project.id;
  activeSvcTab = 'debug-chat';
  if (select) select.value = project.id;
  renderGlobalAiProjectContext(project);
  await openDebugChatTab();
  if (focus) document.getElementById('debug-chat-input')?.focus();
}

function clearGlobalAiProject() {
  stopAgentActivityStream();
  stopDebugChatBrainPoll();
  activeServiceId = null;
  activeSvcTab = 'general';
  debugChatLoadedProjectId = '';
  const select = document.getElementById('global-ai-project');
  if (select) select.value = '';
  renderGlobalAiProjectContext(null);
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
  if (name === 'dashboard') { activeServiceId = null; }
  if (name === 'platform') {
    if (activePlatformPage === 'docker') activePlatformPage = 'overview';
    document.getElementById('platform-workspace')?.classList.remove('docker-library-mode');
    loadPlatformPage(activePlatformPage);
    if (activePlatformPage === 'overview') loadOverviewMonitor();
  }
  if (name === 'server-swarm') renderServerSwarm();
  if (name === 'logs') renderLogsList();
  if (name === 'ssl') loadSslDashboard();
  if (name === 'settings') loadSettings();
  if (name === 'sycord') refreshIcons();
  if (name === 'share-it') loadShareItTemplates();
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
      option.textContent = `${provider} · ${model.name}`;
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
  { profile: 'syra-nano', provider: 'Google Gemini', name: 'Gemini 2.5 Flash' },
  { profile: 'syra-ultra', provider: 'Aliyun', name: 'Qwen 3.7 Plus' },
  { profile: 'syra-havy', provider: 'VyceAI', name: 'Claude Sonnet 4.6' },
];

function syncCustomModelOptions(models) {
  const available = Array.isArray(models) ? models : [];
  const selectable = [...STATIC_MODEL_PROFILES, ...available].filter((model, index, rows) =>
    rows.findIndex((candidate) => candidate.profile === model.profile) === index,
  );
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
  // The legacy settings sheet was removed. Error-recovery and historical links
  // now take operators to the single Model tab provider configuration surface.
  showView('ai');
  setGlobalAiTab('models');
  void loadGlobalAiProviderCatalog();
}

function closeAiSettings() {
  // Kept as a compatibility no-op for legacy save callbacks.
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
  const isMultipart = typeof FormData !== 'undefined' && opts.body instanceof FormData;
  const headers = { ...(isMultipart ? {} : { 'Content-Type': 'application/json' }), ...(opts.headers || {}) };
  if (shouldAttachApiKey(path)) headers['X-API-Key'] = getApiKey();
  const method = (opts.method || 'GET').toUpperCase();
  const isOperatorAction = (
    (path.startsWith('/settings/syra') || path.startsWith('/settings/router') || path.startsWith('/settings/github') || path.startsWith('/github') || path.startsWith('/tokens') || path.startsWith('/ssl') || path.startsWith('/platform/operator/profile') || path.startsWith('/auth/profile') || path.startsWith('/projects/git/github') || path === '/projects/import/github' || path.startsWith('/share/templates/') || (path === '/operator/session' && method === 'DELETE') || (path === '/auth/session' && method === 'DELETE'))
    && !['GET', 'HEAD', 'OPTIONS'].includes(method)
  );
  if (isOperatorAction && syraCsrfToken) headers['X-Syte-CSRF'] = syraCsrfToken;
  let res;
  try {
    res = await fetch(API + path, { credentials: 'same-origin', ...opts, headers });
    if (res.status === 401 && getApiKey()) {
      setApiKey('');
      const retryHeaders = { ...headers };
      delete retryHeaders['X-API-Key'];
      res = await fetch(API + path, { credentials: 'same-origin', ...opts, headers: retryHeaders });
    }
  } catch (err) {
    highLoadNetworkErrorCount++;
    const rawMsg = String(err?.message || err || '');
    const isNetwork = !rawMsg || rawMsg === 'TypeError' || rawMsg.includes('Load failed') || rawMsg.includes('Failed to fetch') || rawMsg.includes('NetworkError');
    if (highLoadNetworkErrorCount >= 4) {
      showCrashScreen({
        title: 'Network / High Load Error',
        subtitle: 'The server is unreachable or under high load.',
        message: 'Multiple consecutive API requests failed due to high load or network loss.',
        details: err?.stack || String(err)
      });
    }
    const normalizedMsg = normalizeFetchError(rawMsg);
    const apiErr = new Error(normalizedMsg);
    apiErr.isNetworkError = isNetwork;
    apiErr.originalError = err;
    throw apiErr;
  }
  highLoadNetworkErrorCount = 0;
  if (res.status === 401 && (
    path.startsWith('/settings/syra') || path.startsWith('/settings/router') || path.startsWith('/settings/github') || path.startsWith('/github') || path.startsWith('/tokens') || path.startsWith('/ssl') || path.startsWith('/auth/') || path === '/operator/session'
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

// ---------------------------------------------------------------------------
// Operator login screen (protected views)
// ---------------------------------------------------------------------------

// Which UI routes require operator auth before their data loads.
const OPERATOR_PROTECTED_VIEWS = ['ssl', 'router'];
// REST prefixes that can be authenticated with an API key (session-stored).
const OPERATOR_API_KEY_PATHS = ['/ssl', '/settings/syra', '/settings/router', '/settings/github', '/github', '/tokens', '/platform/operator/profile'];

function isOperatorView(name) {
  return OPERATOR_PROTECTED_VIEWS.includes(name);
}

// The browser is considered operator-authenticated if it holds a session cookie
// (checked lazily against /api/operator/session) or a session-scoped API key.
async function operatorAuthenticated() {
  if (await restoreOperatorSession()) return true;
  return Boolean(getApiKey());
}

let loginReturnView = null;

function showLoginScreen(returnView) {
  // The web UI is always navigable. Protected API actions report their own
  // authentication error without blocking the entire console behind a key gate.
  loginReturnView = returnView || loginReturnView;
  return true;
}

function hideLoginScreen() {
  const screen = document.getElementById('login-screen');
  if (screen) screen.classList.add('hidden');
  document.body.classList.remove('login-locked');
  const keyInput = document.getElementById('login-api-key');
  if (keyInput) keyInput.value = '';
  const bootInput = document.getElementById('login-bootstrap-key');
  if (bootInput) bootInput.value = '';
  clearLoginError();
}

function setLoginError(msg) {
  const err = document.getElementById('login-error');
  if (!err) return;
  err.textContent = msg || '';
  err.classList.toggle('hidden', !msg);
}

function clearLoginError() {
  setLoginError('');
}

// Heuristic for an operator-auth failure (missing cookie session or rejected /
// revoked API key) surfaced by verify_operator_session_or_token.
function isAuthError(e) {
  const msg = String(e && e.message || '');
  return /operator authentication|operator session|api key/i.test(msg);
}

async function forceLoginForView(viewName) {
  // Do not gate navigation on a bootstrap/API key. The server still enforces
  // authentication for protected mutations and returns a normal API error.
  return true;
}

async function loginWithApiKey(key) {
  setApiKey(key.trim());
  // Verify the key against an operator endpoint before closing the screen.
  try {
    await api('/ssl', { cache: 'no-store' });
    hideLoginScreen();
    if (await forceLoginForView(loginReturnView)) {
      if (loginReturnView === 'ssl') await loadSslDashboard();
      if (loginReturnView === 'router') await loadRouterTab();
      loginReturnView = null;
    }
    toast('Signed in with API key');
  } catch (e) {
    setApiKey('');
    setLoginError('Invalid API key — ' + e.message);
  }
}

async function loginWithBootstrapKey(key) {
  if (window.location.protocol !== 'https:') {
    setLoginError('Operator session requires HTTPS. Open the configured GUI domain.');
    return;
  }
  try {
    const session = await api('/operator/session', {
      method: 'POST',
      body: JSON.stringify({ bootstrap_token: key.trim() }),
    });
    operatorSessionRestorePromise = null;
    syraCsrfToken = session.csrf_token || '';
    if (!syraCsrfToken) throw new Error('Operator session was not created');
    hideLoginScreen();
    if (await forceLoginForView(loginReturnView)) {
      if (loginReturnView === 'ssl') await loadSslDashboard();
      if (loginReturnView === 'router') await loadRouterTab();
      loginReturnView = null;
    }
    toast('Operator session enabled');
  } catch (e) {
    setLoginError('Operator session failed — ' + e.message);
  }
}

function switchLoginTab(which) {
  const apiForm = document.getElementById('login-form-api');
  const keyForm = document.getElementById('login-form-key');
  const tabApi = document.getElementById('login-tab-bootstrap');
  const tabKey = document.getElementById('login-tab-method');
  const isApi = which === 'api';
  if (apiForm) apiForm.classList.toggle('hidden', !isApi);
  if (keyForm) keyForm.classList.toggle('hidden', isApi);
  if (tabApi) tabApi.classList.toggle('is-active', isApi);
  if (tabKey) tabKey.classList.toggle('is-active', !isApi);
  if (tabApi) tabApi.setAttribute('aria-selected', isApi ? 'true' : 'false');
  if (tabKey) tabKey.setAttribute('aria-selected', isApi ? 'false' : 'true');
  clearLoginError();
}

// Eagerly resolve the API key for operator paths so API-key sign-in persists
// across protected views within this tab session.
function shouldAttachApiKey(path) {
  const key = getApiKey();
  if (!key) return false;
  // GUI routes are public on same-origin — but operator-protected paths must
  // carry the session-stored API key when no cookie session exists.
  if (typeof window !== 'undefined' && window.location?.origin) {
    const guiPrefixes = [
      '/projects/',
      '/agent_dashboard',
      '/system',
      '/operator/',
    ];
    if (guiPrefixes.some(prefix => path.startsWith(prefix))) return false;
  }
  return OPERATOR_API_KEY_PATHS.some(prefix => path.startsWith(prefix));
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
    // Keep navigation insight current independently of whichever subtab is open.
    recordLiveSystemMetrics(sys);
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
        if (conn) {
          const label = conn.querySelector('span');
          if (label) label.textContent = 'Visit';
          else conn.textContent = hostPortLabel(p);
        }
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

// ---------------------------------------------------------------------------
// SSL dashboard (monitor / configure / resolve)
// ---------------------------------------------------------------------------

let sslData = null;

async function loadSslDashboard() {
  const content = document.getElementById('ssl-content');
  const refreshBtn = document.getElementById('ssl-refresh-btn');
  const resolveBtn = document.getElementById('ssl-resolve-btn');
  if (!content) return;
  if (!(await forceLoginForView('ssl'))) {
    return;
  }
  if (refreshBtn) refreshBtn.disabled = true;
  if (resolveBtn) resolveBtn.disabled = true;
  try {
    sslData = await api('/ssl');
    renderSslDashboard(sslData);
  } catch (e) {
    // A 401 (missing session or rejected API key) means the operator session
    // expired or the key was revoked — show the login screen instead of a
    // dead-end error.
    if (isAuthError(e)) {
      showLoginScreen('ssl');
      return;
    }
    if (content) content.innerHTML = `<p class="hint block">Could not load SSL status — ${esc(String(e && e.message || e))}</p>`;
  } finally {
    if (refreshBtn) refreshBtn.disabled = false;
    if (resolveBtn) resolveBtn.disabled = false;
  }
}

function sslCheckRow(label, ok, detail) {
  const icon = ok
    ? '<i data-lucide="check-circle" style="color:var(--ok,#16a34a)"></i>'
    : '<i data-lucide="x-circle" style="color:var(--err,#dc2626)"></i>';
  return `<div class="ssl-check-row">
    <span class="ssl-check-icon">${icon}</span>
    <span class="ssl-check-label">${esc(label)}</span>
    <span class="ssl-check-detail">${detail ? esc(detail) : ''}</span>
  </div>`;
}

function sslHostSummary(host) {
  if (!host || !host.configured) {
    return `<span class="badge badge-ssl badge-ssl-http">HTTP</span> <span class="hint">n/a</span>`;
  }
  const active = host.active;
  // live_state is the authoritative probe result (serving / invalid-cert /
  // down / pending / malformed) merged from the live debug pass.
  const liveState = host.live_state;
  const title = host.live_detail ? ` title="${esc(host.live_detail)}"` : '';
  if (liveState && liveState !== 'serving') {
    const cls = liveState === 'invalid-cert' ? 'badge-ssl-http' : 'badge-ssl-preview-pending';
    const label = liveState === 'invalid-cert' ? 'invalid cert' : liveState === 'dedicated-cert-missing' ? 'dedicated cert missing' : liveState === 'down' ? 'not serving' : liveState;
    return `<span class="badge badge-ssl ${cls}" ${title}>${esc(label)}</span> <code>${esc(host.domain)}</code>`;
  }
  return active
    ? `<span class="badge badge-ssl badge-ssl-https">HTTPS</span> <a href="${esc(host.url)}" target="_blank" rel="noopener" class="link" ${title}>${esc(host.domain)}</a>`
    : `<span class="badge badge-ssl badge-ssl-preview-pending">pending</span> <code>${esc(host.domain)}</code>`;
}

function sslStateBadge(state, detail) {
  const map = {
    'serving': ['badge-ssl-https', 'serving'],
    'down': ['badge-ssl-pending', 'down'],
    'pending': ['badge-ssl-preview-pending', 'pending'],
    'malformed': ['badge-ssl-http', 'malformed'],
    'invalid-cert': ['badge-ssl-http', 'invalid cert'],
    'cert-error': ['badge-ssl-preview-pending', 'cert error'],
    'dedicated-cert-missing': ['badge-ssl-preview-pending', 'dedicated cert missing'],
    'not-configured': ['badge-ssl-http', 'no domain'],
  };
  const [cls, label] = map[state] || ['badge-ssl-http', state || 'unknown'];
  const title = detail ? `title="${esc(detail)}"` : '';
  return `<span class="badge badge-ssl ${cls}" ${title}>${esc(label)}</span>`;
}

function sslDebugRow(d) {
  const name = esc(d.name || 'endpoint');
  const domain = d.domain ? `<code>${esc(d.domain)}</code>` : '<span class="hint">—</span>';
  const latency = d.latency_ms != null ? ` <span class="hint">${d.latency_ms}ms</span>` : '';
  const note = d.note ? `<small class="ssl-debug-note">${esc(d.note)}</small>` : '';
  return `<div class="ssl-debug-row">
    <span class="ssl-debug-name">${name}</span>
    <span class="ssl-debug-domain">${domain}</span>
    <span class="ssl-debug-state">${sslStateBadge(d.state, d.detail)}${latency}</span>
    ${note}
  </div>`;
}

function renderSslDashboard(d) {
  const content = document.getElementById('ssl-content');
  if (!content) return;

  const cf = d.cloudflare || {};
  const caddy = d.caddy || {};
  const monitor = d.almalinux_monitor || {};
  const caddyMon = d.caddy_monitor || {};

  const cloudflareChecks = [
    ['Cloudflare API token saved', Boolean(cf.token_configured),
      cf.token_configured ? 'zone DNS-edit token present' : 'see Settings → Preview domain'],
    ['Wildcard TLS enabled', Boolean(cf.wildcard_tls_enabled || cf.ready),
      cf.wildcard_tls_enabled ? 'preview wildcard TLS on' : 'enable via Cloudflare token'],
    ['Caddy Cloudflare DNS plugin', Boolean(cf.caddy_plugin_installed),
      cf.caddy_plugin_installed ? 'dns.providers.cloudflare present' : 'run "Apply & resolve SSL"'],
    ['Caddy systemd env', Boolean(cf.systemd_env_configured),
      cf.systemd_env_configured ? 'EnvironmentFile configured' : 'needed for DNS-01 challenges'],
  ].map(([label, ok, detail]) => sslCheckRow(label, ok, detail)).join('');

  // Caddy server settings monitor — version, boot enablement, uptime, paths.
  const caddyRows = [
    ['Installed', Boolean(caddyMon.installed), caddyMon.installed ? 'binary found' : 'not installed'],
    ['Running', Boolean(caddyMon.active), caddyMon.active ? 'active' : 'not running'],
    ['Enabled at boot', Boolean(caddyMon.enabled), caddyMon.enabled ? 'systemd enabled' : 'not enabled'],
    ['Version', Boolean(caddyMon.version), caddyMon.version || 'unknown'],
    ['Uptime', caddyMon.uptime_seconds != null, formatUptime(caddyMon.uptime_seconds)],
    ['Config file', Boolean(caddyMon.config_exists), caddyMon.config_path || 'missing'],
    ['systemd EnvironmentFile', Boolean(caddyMon.systemd_env_configured),
      caddyMon.systemd_env_configured ? 'Cloudflare token drop-in' : 'not configured'],
    ['Cloudflare DNS plugin', Boolean(caddyMon.cloudflare_plugin_installed),
      caddyMon.cloudflare_plugin_installed ? 'dns.providers.cloudflare' : 'missing'],
  ].map(([label, ok, detail]) => sslCheckRow(label, ok, detail)).join('');

  const totals = d.totals || { configured: 0, active: 0, pending: 0 };

  // High-visibility alert: the shared wildcard cert is a self-signed placeholder,
  // which is why every *.sycord.site (GUI subdomains, previews, 9router) shows
  // "cert error" even though Caddy holds a cert file.
  let wildcardAlert = '';
  if (d.wildcard_cert && d.wildcard_cert.self_signed) {
    const zone = d.wildcard_cert.suggested_zone || (d.projects_debug && d.projects_debug[0] && (d.projects_debug[0].production?.domain || '').split('.').slice(-2).join('.'));
    wildcardAlert = `
      <div class="ssl-alert">
        <i data-lucide="alert-triangle"></i>
        <div>
          <strong>Wildcard certificate is self-signed / not trusted</strong>
          <p>The shared <code>*.${esc(zone || 'zone')}</code> cert is a Caddy placeholder (issuer: ${esc(d.wildcard_cert.issuer || 'Caddy Local Authority')}), so browsers reject it. This is why every subdomain (GUI, previews, 9router) shows "cert error" even though Caddy holds a certificate file. Fix the DNS-01 prerequisites, then press <strong>Apply &amp; resolve SSL</strong> to re-issue a real Let's Encrypt wildcard cert.</p>
        </div>
      </div>`;
  }

  const hints = (d.action_hints || []).length
    ? `<div class="ssl-hints">
        ${d.action_hints.map(h => `<p><i data-lucide="info"></i> ${esc(h)}</p>`).join('')}
       </div>`
    : '';

  // Live per-endpoint HTTPS debug (most audible signal for "is it serving").
  const debugRows = (d.debug || []).map(sslDebugRow).join('');
  const projectDebugRows = (d.projects_debug || []).map(pd => {
    const prod = pd.production || [];
    const prev = pd.preview || [];
    return `<div class="ssl-debug-project">
      <span class="ssl-debug-project-name">${esc(pd.project)}</span>
      <span class="ssl-debug-project-hosts">
        <span class="ssl-debug-inline"><span class="hint">prod</span> ${sslDebugRow(prod)}</span>
        <span class="ssl-debug-inline"><span class="hint">preview</span> ${sslDebugRow(prev)}</span>
      </span>
    </div>`;
  }).join('');

  const rows = (d.projects || []).map(p => `
    <div class="ssl-project-row">
      <div class="ssl-project-name">
        <strong>${esc(p.name)}</strong>
        <span class="badge badge-ssl badge-ssl-${cssClassSafe(p.badge)}">${esc(p.badge_label)}</span>
      </div>
      <div class="ssl-project-hosts">
        <div class="ssl-host"><span class="hint">production</span> ${sslHostSummary(p.production)}</div>
        <div class="ssl-host"><span class="hint">preview</span> ${sslHostSummary(p.preview)}</div>
      </div>
    </div>
  `).join('') || '<p class="hint block">No projects yet.</p>';

  const customTls = customTlsControlsHtml(d);
  const issuance = certificateIssuanceHtml(d);

  const monitorCards = (monitor.endpoints || []).map(monitorEndpointHtml).join('');

  content.innerHTML = `
    <div class="ssl-totals">
      <div class="swarm-stat"><span class="swarm-label">Configured</span><span class="swarm-value">${totals.configured}</span></div>
      <div class="swarm-stat"><span class="swarm-label">Active HTTPS</span><span class="swarm-value">${totals.active}</span></div>
      <div class="swarm-stat"><span class="swarm-label">Pending</span><span class="swarm-value">${totals.pending}</span></div>
    </div>
    <div class="projects-card panel-form">
      <div class="projects-title-block mb"><i data-lucide="monitor-check" class="projects-icon"></i><div><h3>AlmaLinux status monitor <span class="hint">— live DNS + HTTPS for sycord.site surfaces</span></h3></div></div>
      <div class="monitor-hostbar">
        <span class="monitor-hostbar-item"><span class="hint">OS</span> <strong>${esc(monitor.os || '—')}</strong></span>
        <span class="monitor-hostbar-item"><span class="hint">hostname</span> <strong>${esc(monitor.hostname || '—')}</strong></span>
        <span class="monitor-hostbar-item"><span class="hint">public IP</span> <strong>${esc(monitor.public_ip || '—')}</strong></span>
      </div>
      <div class="monitor-grid">${monitorCards || '<p class="hint block">No endpoints to monitor.</p>'}</div>
    </div>
    <div class="ssl-grid">
      <div class="projects-card panel-form">
        <div class="projects-title-block mb"><i data-lucide="server" class="projects-icon"></i><div><h3>Caddy server settings</h3></div></div>
        <div class="ssl-checks">${caddyRows}</div>
      </div>
      <div class="projects-card panel-form">
        <div class="projects-title-block mb"><i data-lucide="cloud" class="projects-icon"></i><div><h3>Cloudflare (wildcard DNS-01)</h3></div></div>
        <div class="ssl-checks">${cloudflareChecks}</div>
      </div>
    </div>
    ${wildcardAlert}
    ${hints}
    <div class="projects-card panel-form">
      <div class="projects-title-block mb"><i data-lucide="activity" class="projects-icon"></i><div><h3>Live HTTPS debug <span class="hint">— is each endpoint actually serving?</span></h3></div></div>
      <div class="ssl-debug-list">${debugRows || '<p class="hint block">No endpoint debug.</p>'}</div>
      ${projectDebugRows ? `<div class="ssl-debug-project-list">${projectDebugRows}</div>` : ''}
    </div>
    <div class="projects-card panel-form">
      <div class="projects-title-block mb"><i data-lucide="shield-check" class="projects-icon"></i><div><h3>Certificates by project</h3></div></div>
      <div class="ssl-project-list">${rows}</div>
    </div>
    ${issuance}
    <div class="projects-card panel-form">
      <div class="projects-title-block mb"><i data-lucide="key-round" class="projects-icon"></i><div><h3>Custom TLS <span class="hint">— per-app and sycord.site dedicated certs</span></h3></div></div>
      ${customTls}
    </div>
  `;
  refreshIcons();
  wireCustomTls();
  wireCertificateIssuance();
}

function monitorEndpointHtml(ep) {
  const state = ep.state || 'unknown';
  const badge = sslStateBadge(state, ep.detail);
  const latency = ep.latency_ms != null ? `<span class="hint">${ep.latency_ms}ms</span>` : '';
  const dns = ep.resolves
    ? `<span class="monitor-dns ok">DNS <span class="hint">${(ep.ips || []).join(', ') || 'resolves'}</span></span>`
    : `<span class="monitor-dns bad">DNS unresolved</span>`;
  const cert = ep.cert_active
    ? `<span class="monitor-cert ok">cert stored</span>`
    : `<span class="monitor-cert bad">no cert</span>`;
  const link = ep.domain && ep.reachable
    ? `<a href="https://${esc(ep.domain)}" target="_blank" rel="noopener" class="link">${esc(ep.domain)}</a>`
    : `<code>${esc(ep.domain || '—')}</code>`;
  return `
    <div class="monitor-card">
      <div class="monitor-card-head">
        <strong>${esc(ep.name)}</strong>
        <span class="monitor-card-state">${badge}${latency}</span>
      </div>
      <div class="monitor-card-domain">${link}</div>
      <div class="monitor-card-meta">${dns}${cert}</div>
      ${ep.detail && !ep.reachable ? `<p class="monitor-card-detail">${esc(ep.detail)}</p>` : ''}
    </div>`;
}

function formatUptime(seconds) {
  if (seconds == null) return 'n/a';
  const s = Math.round(seconds);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ${s % 60}s`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ${m % 60}m`;
  const days = Math.floor(h / 24);
  return `${days}d ${h % 24}h`;
}

function cssClassSafe(s) {
  return String(s || 'http').replace(/[^a-z0-9-]/gi, '');
}

function customTlsControlsHtml(d) {
  // Global sycord.site / custom host setting.
  const globalHost = esc((d && d.custom_tls_host) || '');
  const globalPort = esc((d && d.custom_tls_port) || '');
  const nineUpstream = esc((d && d.nine_router_upstream) || '');
  const projectRows = (d.projects || []).map(p => {
    const pid = cssClassSafe(p.id);
    const local = p.custom_tls_domain ? p.custom_tls_domain : '';
    const enabled = Boolean(p.custom_tls_enabled);
    return `<div class="ssl-custom-row" data-id="${esc(p.id)}">
      <strong class="ssl-custom-name">${esc(p.name)}</strong>
      <input class="ssl-custom-input" data-role="domain" value="${esc(local)}" placeholder="custom-domain.example.com">
      <label class="ssl-custom-toggle"><input type="checkbox" data-role="enabled" ${enabled ? 'checked' : ''}> enable</label>
      <button type="button" class="btn-pill btn-primary btn-sm ssl-custom-save" data-role="save">Save</button>
      <span class="hint ssl-custom-status"></span>
    </div>`;
  }).join('') || '<p class="hint block">No projects yet.</p>';

  return `
    <div class="ssl-custom-global">
      <div class="ssl-custom-global-title"><strong>Global host</strong> <span class="hint">e.g. sycord.site apex or a dedicated domain with its own cert</span></div>
      <div class="ssl-custom-global-row">
        <input class="ssl-custom-input" id="ssl-custom-global-domain" value="${globalHost}" placeholder="sycord.site">
        <input class="ssl-custom-input" id="ssl-custom-global-port" value="${globalPort}" placeholder="port (default ${esc(String((d && d.gui_port) || 8787))})">
        <button type="button" class="btn-pill btn-primary btn-sm" id="ssl-custom-global-save">Save global TLS</button>
        <span class="hint ssl-custom-status" id="ssl-custom-global-status"></span>
      </div>
    </div>
    <div class="ssl-custom-global">
      <div class="ssl-custom-global-title"><strong>9Router gateway</strong> <span class="hint">https://9router.sycord.site — Caddy auto SSL → gateway upstream host:port</span></div>
      <div class="ssl-custom-global-row">
        <input class="ssl-custom-input" id="ssl-nine-router-upstream" value="${nineUpstream}" placeholder="65.75.203.134:20128">
        <button type="button" class="btn-pill btn-primary btn-sm" id="ssl-nine-router-save">Save 9Router upstream</button>
        <span class="hint ssl-custom-status" id="ssl-nine-router-status"></span>
      </div>
    </div>
    <div class="ssl-custom-project-list">${projectRows}</div>
  `;
}

function wireCustomTls() {
  // Per-project save
  document.querySelectorAll('.ssl-custom-row').forEach(row => {
    const saveBtn = row.querySelector('[data-role="save"]');
    if (!saveBtn) return;
    saveBtn.addEventListener('click', async () => {
      const id = row.dataset.id;
      const domain = (row.querySelector('[data-role="domain"]').value || '').trim();
      const enabled = row.querySelector('[data-role="enabled"]').checked;
      const statusEl = row.querySelector('.ssl-custom-status');
      if (statusEl) statusEl.textContent = 'Applying…';
      try {
        const res = await api(`/ssl/projects/${id}/custom-tls`, {
          method: 'POST',
          body: JSON.stringify({ custom_tls_domain: domain, custom_tls_enabled: enabled }),
        });
        if (statusEl) statusEl.textContent = res.message || 'saved';
        toast(res.message || 'custom TLS saved');
      } catch (e) {
        if (statusEl) statusEl.textContent = 'Error: ' + e.message;
        toast('Error: ' + e.message);
      }
    });
  });
  // Global save
  const globalSave = document.getElementById('ssl-custom-global-save');
  if (globalSave) {
    globalSave.addEventListener('click', async () => {
      const domain = (document.getElementById('ssl-custom-global-domain').value || '').trim();
      const port = (document.getElementById('ssl-custom-global-port').value || '').trim();
      const statusEl = document.getElementById('ssl-custom-global-status');
      if (statusEl) statusEl.textContent = 'Applying…';
      try {
        const res = await api('/settings', {
          method: 'PUT',
          body: JSON.stringify({ custom_tls_host: domain, custom_tls_port: port }),
        });
        if (statusEl) statusEl.textContent = (res.messages || []).join(' · ') || 'saved';
        toast('Global custom TLS saved');
      } catch (e) {
        if (statusEl) statusEl.textContent = 'Error: ' + e.message;
        toast('Error: ' + e.message);
      }
    });
  }
  // 9Router gateway upstream save
  const nineSave = document.getElementById('ssl-nine-router-save');
  if (nineSave) {
    nineSave.addEventListener('click', async () => {
      const upstream = (document.getElementById('ssl-nine-router-upstream').value || '').trim();
      const statusEl = document.getElementById('ssl-nine-router-status');
      if (statusEl) statusEl.textContent = 'Applying…';
      try {
        const res = await api('/settings', {
          method: 'PUT',
          body: JSON.stringify({ nine_router_upstream: upstream }),
        });
        if (statusEl) statusEl.textContent = (res.messages || []).join(' · ') || 'saved';
        toast('9Router gateway upstream saved — Caddy SSL route updated');
      } catch (e) {
        if (statusEl) statusEl.textContent = 'Error: ' + e.message;
        toast('Error: ' + e.message);
      }
    });
  }
}

async function applyResolveSsl() {
  const content = document.getElementById('ssl-content');
  const btn = document.getElementById('ssl-resolve-btn');
  const refresh = document.getElementById('ssl-refresh-btn');
  if (!(await forceLoginForView('ssl'))) return;
  if (btn) btn.disabled = true;
  if (refresh) refresh.disabled = true;
  if (content) {
    content.innerHTML = '<p class="hint block">Applying Caddy configuration and resolving certificates…</p>';
    refreshIcons();
  }
  let payload = null;
  try {
    payload = await api('/ssl/resolve', { method: 'POST' });
  } catch (e) {
    if (isAuthError(e)) {
      showLoginScreen('ssl');
      if (btn) btn.disabled = false;
      if (refresh) refresh.disabled = false;
      return;
    }
    if (content) content.innerHTML = `<p class="hint block">Resolve failed — ${esc(String(e && e.message || e))}</p>`;
    if (btn) btn.disabled = false;
    if (refresh) refresh.disabled = false;
    return;
  }
  sslData = payload;
  renderSslDashboard(payload);
  const messages = payload.messages || [];
  if (messages.length) {
    const note = document.createElement('div');
    note.className = 'ssl-hints ssl-resolve-result';
    note.innerHTML = messages.map(m => `<p>${esc(m)}</p>`).join('');
    content.prepend(note);
  }
  refreshIcons();
}

document.addEventListener('DOMContentLoaded', () => {
  const refreshBtn = document.getElementById('ssl-refresh-btn');
  const resolveBtn = document.getElementById('ssl-resolve-btn');
  if (refreshBtn) refreshBtn.addEventListener('click', loadSslDashboard);
  if (resolveBtn) resolveBtn.addEventListener('click', applyResolveSsl);

  // Operator login screen
  const apiForm = document.getElementById('login-form-api');
  const keyForm = document.getElementById('login-form-key');
  const tabApi = document.getElementById('login-tab-bootstrap');
  const tabKey = document.getElementById('login-tab-method');
  if (apiForm) apiForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const input = document.getElementById('login-api-key');
    if (!input || !input.value.trim()) return setLoginError('Enter an API key');
    loginWithApiKey(input.value);
  });
  if (keyForm) keyForm.addEventListener('submit', (e) => {
    e.preventDefault();
    const input = document.getElementById('login-bootstrap-key');
    if (!input || !input.value.trim()) return setLoginError('Enter an operator credential');
    loginWithBootstrapKey(input.value);
  });
  if (tabApi) tabApi.addEventListener('click', () => switchLoginTab('api'));
  if (tabKey) tabKey.addEventListener('click', () => switchLoginTab('key'));
  document.addEventListener('keydown', (e) => {
    const screen = document.getElementById('login-screen');
    if (e.key === 'Escape' && screen && !screen.classList.contains('hidden')) hideLoginScreen();
  });
});

async function loadProjects(options = {}) {
  const { silent = false } = options;
  try {
    projects = await api('/projects');
    renderServices();
    await loadPlatformDatabases();
    updateStats();
    void loadOverviewMonitor();
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

let platformDatabases = [];

async function loadPlatformDatabases() {
  const list = document.getElementById('platform-databases-list');
  if (!list) return;
  try {
    platformDatabases = await api('/platform/databases');
    renderPlatformDatabases();
  } catch (error) {
    list.innerHTML = `<span class="hint">Could not load managed databases: ${esc(normalizeFetchError(error.message))}</span>`;
  }
}

function renderPlatformDatabases() {
  const list = document.getElementById('platform-databases-list');
  if (!list) return;
  if (!platformDatabases.length) {
    list.innerHTML = '<span class="hint">No managed databases yet. Create one through the platform API.</span>';
    return;
  }
  list.innerHTML = platformDatabases.map((db) => {
    const state = db.status || 'stopped';
    return `<article class="platform-database-card">
      <div class="platform-database-main">
        <div class="platform-database-title"><i data-lucide="database"></i><strong>${esc(db.name || db.uuid)}</strong><span class="platform-database-type">${esc(db.database_type || '')}</span></div>
        <div class="platform-database-meta"><span class="status-dot ${state === 'running' ? 'is-live' : ''}"></span>${esc(state)} · ${db.is_public ? `public :${esc(String(db.public_port || ''))}` : 'private network'}</div>
      </div>
      <div class="platform-database-actions">
        <button type="button" class="btn-pill btn-ghost btn-sm" onclick="platformDatabaseAction('${esc(db.uuid)}','${state === 'running' ? 'stop' : 'start'}')">${state === 'running' ? 'Stop' : 'Start'}</button>
        <button type="button" class="btn-pill btn-ghost btn-sm" onclick="copyPlatformDatabaseConnection('${esc(db.uuid)}')">Copy URL</button>
        <button type="button" class="btn-pill btn-danger btn-sm" onclick="deletePlatformDatabase('${esc(db.uuid)}')">Delete</button>
      </div>
    </article>`;
  }).join('');
  refreshIcons();
}

async function platformDatabaseAction(uuid, action) {
  try {
    await api(`/platform/databases/${encodeURIComponent(uuid)}/${action}`, { method: 'POST' });
    await loadPlatformDatabases();
    showToast(`Database ${action}ed`);
  } catch (error) {
    showToast(normalizeFetchError(error.message), 'error');
  }
}

async function copyPlatformDatabaseConnection(uuid) {
  try {
    const details = await api(`/platform/databases/${encodeURIComponent(uuid)}/connection`);
    const value = details.internal_url || details.public_url || '';
    await navigator.clipboard.writeText(value);
    showToast('Connection URL copied');
  } catch (error) {
    showToast(normalizeFetchError(error.message), 'error');
  }
}

async function deletePlatformDatabase(uuid) {
  if (!window.confirm('Delete this database container? Its named volume will be preserved.')) return;
  try {
    await api(`/platform/databases/${encodeURIComponent(uuid)}`, { method: 'DELETE' });
    await loadPlatformDatabases();
    showToast('Database deleted');
  } catch (error) {
    showToast(normalizeFetchError(error.message), 'error');
  }
}

document.getElementById('platform-databases-refresh')?.addEventListener('click', loadPlatformDatabases);

function updateActiveServiceMeta(p) {
  updateServiceStatusDot(p);
  if (activeSvcTab === 'general') {
    renderQuickActions(p);
    updateServiceConnLink(p);
    renderDeploymentSitePreview(p);
  } else if (activeSvcTab === 'preview') {
    renderPreviewSection(p);
  }
}

function updateServiceConnLink(p) {
  const conn = document.getElementById('svc-conn');
  if (!conn) return;
  const link = p.url || '#';
  const label = conn.querySelector('span');
  if (label) label.textContent = 'Visit';
  else conn.textContent = connLabel(p);
  conn.href = link;
  conn.title = link === '#' ? 'Deployment URL is pending' : `Open ${connLabel(p)}`;
  conn.toggleAttribute('aria-disabled', link === '#');
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

function projectCardFavicon(p) {
  if (!p?.url) return '/static/syte-logo.png?v=0.9.2';
  try {
    const site = new URL(p.url);
    return new URL('/favicon.ico', site.origin).toString();
  } catch {
    return '/static/syte-logo.png?v=0.9.2';
  }
}

function projectCardSource(p) {
  if (!p?.git_url) return 'Manual source';
  return p.git_url
    .replace(/^https:\/\/(?:www\.)?github\.com\//i, '')
    .replace(/^git@github\.com:/i, '')
    .replace(/\.git$/i, '');
}

function projectCardDate(p) {
  const raw = p?.created_at || p?.updated_at;
  if (!raw) return 'Recently created';
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? 'Recently created' : date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
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
    const domain = p.domain || hostPortLabel(p) || 'Domain pending';
    const favicon = projectCardFavicon(p);
    const source = projectCardSource(p);
    const fallback = '/static/syte-logo.png?v=0.9.2';
    const preview = p.preview_image || 'https://hebbkx1anhila5yf.public.blob.vercel-storage.com/ebeb44b1-3705-4fab-a6be-8f82b0c68659-cadwzgxxhwt13Mm2gwI4SlMVn9grf4.jpeg';
    return `
    <article class="project-card project-card-share-it" tabindex="0" role="button" aria-label="Open ${esc(p.name)}" onclick="openService('${p.id}')" onkeydown="if(event.key==='Enter'||event.key===' '){event.preventDefault();openService('${p.id}')} ">
      <div class="project-card-share-head">
        <div class="project-card-identity"><h3>${esc(p.name)}</h3><span>by ${esc(source.split('/')[0] || 'syte')}</span></div>
        <span class="project-card-status ${status}" title="${esc(status)}"></span>
      </div>
      <div class="project-card-preview-wrap">
        <img class="project-card-preview" src="${esc(preview)}" alt="Preview of ${esc(p.name)}" onerror="this.onerror=null;this.src='${fallback}'">
      </div>
      <div class="project-card-share-footer"><span class="project-card-domain">${esc(domain)}</span><span class="project-card-share-arrow" aria-hidden="true">↗</span></div>
    </article>`;
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

let projectImportSource = 'git';
let importedProjectId = null;
let importedProjectAnalysis = null;
let githubSourceStatus = null;
let githubSourceRepositories = [];
let githubSourceSelection = null;

function renderTopbarGitProfile(status) {
  const profile = document.getElementById('topbar-git-profile');
  if (!profile) return;
  const connected = Boolean(status?.connected);
  profile.classList.toggle('hidden', !connected);
  if (!connected) { profile.innerHTML = ''; return; }
  const login = String(status?.login || 'GitHub');
  const avatar = String(status?.avatar_url || '').trim();
  profile.setAttribute('aria-label', `Open Git connection for ${login}`);
  profile.title = `GitHub: ${login}`;
  profile.innerHTML = avatar
    ? `<img src="${esc(avatar)}" alt="${esc(login)}">`
    : '<img src="/static/vendor/github-svgl.svg" alt="GitHub">';
}

function renderGithubSourceStatus(status) {
  githubSourceStatus = status || { configured: false, connected: false };
  renderTopbarGitProfile(githubSourceStatus);
  const connect = document.getElementById('github-connect-btn');
  const disconnect = document.getElementById('github-disconnect-btn');
  const account = document.getElementById('github-source-account');
  const browser = document.getElementById('github-repository-browser');
  const description = document.getElementById('github-source-description');
  if (!connect || !disconnect || !account || !browser || !description) return;
  const configured = Boolean(githubSourceStatus.configured);
  const connected = Boolean(githubSourceStatus.connected);
  connect.classList.toggle('hidden', connected);
  disconnect.classList.toggle('hidden', !connected);
  connect.disabled = !configured;
  browser.classList.toggle('hidden', !connected);
  if (!configured) {
    description.textContent = 'GitHub OAuth must be configured by an operator before accounts can connect.';
    account.classList.add('hidden');
  } else if (!connected) {
    description.textContent = 'Connect GitHub to browse repositories you can access, including private repositories.';
    account.classList.add('hidden');
  } else {
    description.textContent = 'Choose a repository and branch. OAuth credentials stay encrypted and never become part of the Git remote.';
    account.classList.remove('hidden');
    account.innerHTML = `${githubSourceStatus.avatar_url ? `<img src="${esc(githubSourceStatus.avatar_url)}" alt="">` : '<i data-lucide="circle-user-round"></i>'}<span class="github-connected-dot"></span><span>Connected as <strong>${esc(githubSourceStatus.login || 'GitHub account')}</strong></span>`;
  }
  refreshIcons();
}

async function fastAddGithubRepository(fullName) {
  const repository = githubSourceRepositories.find((item) => item.full_name === fullName) || { full_name: fullName, name: fullName.split('/')[1] || fullName, default_branch: 'main' };
  const repoName = repository.name || fullName.split('/')[1] || fullName;
  const branch = repository.default_branch || 'main';
  toast(`Preparing quick deployment for ${fullName}…`);
  try {
    const result = await api('/projects/import/github', {
      method: 'POST',
      body: JSON.stringify({
        name: repoName,
        repository: repository.full_name,
        branch,
        base_directory: '/',
        in_app_notifications: true,
      })
    });
    if (!result.project?.id) throw new Error('The repository was imported but no project was created.');
    await api(`/projects/${result.project.id}/deploy-detected`, {
      method: 'POST',
      body: JSON.stringify({base_directory: '/', env_vars: {}, in_app_notifications: true}),
    });
    toast(`Quick deployment queued for ${result.project.name || repoName}`);
    await loadProjects();
    openService(result.project.id);
  } catch (error) {
    toast(normalizeFetchError(error?.message) || 'Could not quick deploy this repository.');
  }
}

async function renderGitWorkspace() {
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  target.innerHTML = '<section class="legacy-fleet-page"><p class="legacy-fleet-loading">Loading Git status…</p></section>';

  try {
    const status = await api('/projects/git/github/status');
    githubSourceStatus = status;
    let repos = [];
    if (status.connected) {
      const result = await api('/projects/git/github/repositories');
      repos = result.repositories || [];
      githubSourceRepositories = repos;
    }

    const accountHtml = status.connected
      ? `<div class="github-source-account" style="display:flex;align-items:center;gap:12px;"><img src="${esc(status.avatar_url || '/static/syte-logo.png')}" alt="" style="width:36px;height:36px;border-radius:50%;"><span class="github-connected-dot"></span><span>Connected as <strong>${esc(status.login || 'GitHub account')}</strong></span><button type="button" class="btn-pill btn-ghost btn-sm" id="git-tab-disconnect-btn"><i data-lucide="unlink"></i>Disconnect</button></div>`
      : `<div style="display:flex;align-items:center;justify-content:space-between;width:100%;"><p style="margin:0;color:#666;">Connect a GitHub account to fast add repositories directly as Syte projects.</p><button type="button" class="btn-pill btn-primary btn-sm" id="git-tab-connect-btn" ${status.configured ? '' : 'disabled'}><i data-lucide="github"></i>Connect GitHub</button></div>`;

    const repoListHtml = repos.length
      ? repos.map(repo => `
        <article class="project-card git-repository-card" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;margin-bottom:10px;">
          <div style="min-width:0;flex:1;margin-right:12px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <strong style="font-size:15px;color:#111;">${esc(repo.full_name)}</strong>
              ${repo.private ? '<span class="github-private-badge">Private</span>' : '<span class="github-private-badge">Public</span>'}
            </div>
            ${repo.description ? `<p style="margin:4px 0 0;font-size:12px;color:#666;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(repo.description)}</p>` : ''}
          </div>
          <button type="button" class="btn-pill btn-primary git-tab-fast-add" data-fast-add-repo="${esc(repo.full_name)}">
            <i data-lucide="rocket"></i><span>Quick Deploy</span>
          </button>
        </article>
      `).join('')
      : `<p class="dedicated-empty">${status.connected ? 'No repositories found in connected Git account.' : 'Connect GitHub above to list repositories that can be fast added.'}</p>`;

    target.innerHTML = `
      <section class="legacy-fleet-page git-workspace-page">
        <header class="legacy-fleet-header">
          <div>
            <p>Source & Integration</p>
            <h2>Git Account & Repositories</h2>
            <span>Connect GitHub, browse permitted repositories, quick deploy a detected build, or disconnect at any time.</span>
          </div>
        </header>
        <section class="legacy-fleet-control-card git-source-card" style="padding:18px;margin-bottom:20px;">
          <div style="display:flex;align-items:center;justify-content:space-between;width:100%;">
            ${accountHtml}
          </div>
        </section>
        <section>
          <div class="git-repository-toolbar" style="display:flex;align-items:center;justify-content:space-between;margin-bottom:14px;">
            <h3 style="margin:0;font-size:18px;color:#111;">Repositories (${repos.length})</h3>
            <label class="git-repository-search" style="display:flex;align-items:center;gap:8px;border:1px solid #ddd;border-radius:8px;padding:4px 10px;background:#fff;">
              <i data-lucide="search" style="width:16px;height:16px;color:#777;"></i>
              <input type="search" id="git-tab-search" placeholder="Filter repositories..." style="border:0;outline:0;font-size:13px;">
            </label>
          </div>
          <div id="git-tab-repo-list">${repoListHtml}</div>
        </section>
      </section>
    `;

    target.querySelector('#git-tab-connect-btn')?.addEventListener('click', () => connectGithubSource(window.open('', '_blank')));
    target.querySelector('#git-tab-disconnect-btn')?.addEventListener('click', async () => { await disconnectGithubSource(); renderGitWorkspace(); });
    target.querySelectorAll('.git-tab-fast-add').forEach(btn => {
      btn.addEventListener('click', (e) => {
        const repo = e.currentTarget.dataset.fastAddRepo;
        if (repo) fastAddGithubRepository(repo);
      });
    });

    target.querySelector('#git-tab-search')?.addEventListener('input', (e) => {
      const q = e.target.value.toLowerCase().trim();
      const listEl = target.querySelector('#git-tab-repo-list');
      if (!listEl) return;
      const filtered = repos.filter(r => !q || [r.full_name, r.description].join(' ').toLowerCase().includes(q));
      listEl.innerHTML = filtered.map(repo => `
        <article class="project-card git-repository-card" style="display:flex;align-items:center;justify-content:space-between;padding:14px 18px;margin-bottom:10px;">
          <div style="min-width:0;flex:1;margin-right:12px;">
            <div style="display:flex;align-items:center;gap:8px;">
              <strong style="font-size:15px;color:#111;">${esc(repo.full_name)}</strong>
              ${repo.private ? '<span class="github-private-badge">Private</span>' : '<span class="github-private-badge">Public</span>'}
            </div>
            ${repo.description ? `<p style="margin:4px 0 0;font-size:12px;color:#666;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(repo.description)}</p>` : ''}
          </div>
          <button type="button" class="btn-pill btn-primary git-tab-fast-add" data-fast-add-repo="${esc(repo.full_name)}">
            <i data-lucide="rocket"></i><span>Quick Deploy</span>
          </button>
        </article>
      `).join('') || '<p class="dedicated-empty">No repositories match filter.</p>';
      target.querySelectorAll('.git-tab-fast-add').forEach(btn => {
        btn.addEventListener('click', (ev) => {
          const repo = ev.currentTarget.dataset.fastAddRepo;
          if (repo) fastAddGithubRepository(repo);
        });
      });
      refreshIcons();
    });

    refreshIcons();
  } catch (error) {
    target.innerHTML = `<section class="legacy-fleet-page"><p class="platform-error">Git workspace unavailable: ${esc(error.message)}</p></section>`;
  }
}

let sycordPwaRegistration = null;

function base64UrlToUint8Array(value) {
  const padded = `${value}${'='.repeat((4 - value.length % 4) % 4)}`.replace(/-/g, '+').replace(/_/g, '/');
  const raw = atob(padded);
  return Uint8Array.from(raw, (character) => character.charCodeAt(0));
}

async function registerSycordPwa() {
  if (!('serviceWorker' in navigator) || !window.isSecureContext) return null;
  try {
    sycordPwaRegistration = await navigator.serviceWorker.register('/service-worker.js', {scope: '/'});
    return sycordPwaRegistration;
  } catch (error) {
    console.warn('Sycord PWA registration failed', error);
    return null;
  }
}

async function enableSycordPwaAlerts() {
  if (!('Notification' in window) || !('PushManager' in window)) {
    throw new Error('This browser does not support PWA alerts.');
  }
  const registration = sycordPwaRegistration || await registerSycordPwa();
  if (!registration) throw new Error('PWA registration is unavailable. Open Sycord over HTTPS and try again.');
  const permission = await Notification.requestPermission();
  if (permission !== 'granted') throw new Error('Allow notifications in the browser or iPhone settings to enable alerts.');
  const key = await api('/notifications/push/vapid-public-key');
  let subscription = await registration.pushManager.getSubscription();
  if (!subscription) {
    subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: base64UrlToUint8Array(key.public_key),
    });
  }
  await api('/notifications/push-subscriptions', {method: 'POST', body: JSON.stringify({subscription: subscription.toJSON()})});
  return 'This device can now receive Sycord PWA alerts.';
}

function notificationTime(value) {
  if (!value) return 'Just now';
  const timestamp = new Date(value);
  return Number.isNaN(timestamp.getTime()) ? value : timestamp.toLocaleString();
}

function notificationSettingValue(settings, section, key, fallback = '') {
  return settings?.[section]?.[key] ?? fallback;
}

async function renderNotificationWorkspace() {
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  target.innerHTML = '<section class="legacy-fleet-page"><p class="legacy-fleet-loading">Loading notification settings…</p></section>';
  try {
    const [settings, eventData] = await Promise.all([api('/notifications/settings'), api('/notifications')]);
    const notifications = eventData.notifications || [];
    const browserSupported = 'Notification' in window && 'PushManager' in window && 'serviceWorker' in navigator;
    const permission = 'Notification' in window ? Notification.permission : 'unsupported';
    const eventRows = notifications.length ? notifications.map((item) => `<article class="sycord-notification-row ${item.is_read ? 'is-read' : ''}"><span class="sycord-notification-icon"><i data-lucide="${item.event.includes('failed') ? 'triangle-alert' : item.event.includes('deployment') ? 'rocket' : 'bell'}"></i></span><div><strong>${esc(item.title)}</strong><p>${esc(item.message)}</p><small>${esc(notificationTime(item.created_at))}</small></div></article>`).join('') : '<p class="dedicated-empty">No in-app notifications yet. Enable the PWA option while adding a web app, then project actions will appear here.</p>';
    target.innerHTML = `
      <section class="legacy-fleet-page sycord-notify-page">
        <header class="legacy-fleet-header"><div><p>Delivery & alerts</p><h2>Notifications</h2><span>Choose how Sycord reports every supported project action.</span></div></header>
        <div class="sycord-notify-grid">
          <section class="legacy-fleet-control-card sycord-notify-card">
            <div class="sycord-notify-card-heading"><i data-lucide="smartphone"></i><div><h3>Sycord PWA alerts</h3><p>Installed PWA notifications and an in-app activity centre. On iPhone, add Sycord to Home Screen before enabling alerts.</p></div></div>
            <div class="sycord-notify-status"><span class="${browserSupported ? 'is-ready' : 'is-muted'}">${browserSupported ? (permission === 'granted' ? 'Device permission granted' : 'Permission required') : 'Not supported by this browser'}</span></div>
            <div class="sycord-notify-actions"><button type="button" class="btn-pill btn-primary" id="notify-enable-pwa" ${browserSupported ? '' : 'disabled'}><i data-lucide="bell-ring"></i><span>Enable device alerts</span></button><button type="button" class="btn-pill btn-ghost" id="notify-test"><i data-lucide="send"></i><span>Send test</span></button></div>
          </section>
          <section class="legacy-fleet-control-card sycord-notify-card">
            <div class="sycord-notify-card-heading"><i data-lucide="mail"></i><div><h3>Email delivery</h3><p>Send all supported actions to configured workspace recipients.</p></div></div>
            <form id="notification-settings-form" class="sycord-notify-form">
              <label class="sycord-toggle"><input type="checkbox" id="notify-email-enabled" ${notificationSettingValue(settings, 'email', 'enabled') ? 'checked' : ''}><span>Enable email delivery</span></label>
              <label>Recipients<textarea id="notify-email-recipients" rows="2" placeholder="ops@example.com\nteam@example.com">${esc(notificationSettingValue(settings, 'email', 'recipients'))}</textarea></label>
              <div class="sycord-notify-form-row"><label>SMTP host<input id="notify-smtp-host" value="${esc(notificationSettingValue(settings, 'email', 'smtp_host'))}" placeholder="smtp.example.com"></label><label>Port<input id="notify-smtp-port" type="number" min="1" max="65535" value="${esc(String(notificationSettingValue(settings, 'email', 'smtp_port', 587)))}"></label></div>
              <div class="sycord-notify-form-row"><label>Sender<input id="notify-email-sender" type="email" value="${esc(notificationSettingValue(settings, 'email', 'sender'))}" placeholder="alerts@sycord.com"></label><label>SMTP username<input id="notify-smtp-username" value="${esc(notificationSettingValue(settings, 'email', 'smtp_username'))}" placeholder="optional"></label></div>
              <label>SMTP password<input id="notify-smtp-password" type="password" autocomplete="new-password" placeholder="${notificationSettingValue(settings, 'email', 'password_set') ? 'Saved — leave blank to keep' : 'Optional if your SMTP server does not require it'}"></label>
              <label class="sycord-toggle"><input type="checkbox" id="notify-smtp-tls" ${notificationSettingValue(settings, 'email', 'use_tls', true) ? 'checked' : ''}><span>Use STARTTLS</span></label>
              <button type="submit" class="btn-pill btn-primary"><i data-lucide="save"></i><span>Save channels</span></button>
            </form>
          </section>
          <section class="legacy-fleet-control-card sycord-notify-card">
            <div class="sycord-notify-card-heading"><i data-lucide="webhook"></i><div><h3>Webhook delivery</h3><p>Post structured event data to automation, chat, or incident tools.</p></div></div>
            <label class="sycord-toggle"><input type="checkbox" id="notify-webhook-enabled" ${notificationSettingValue(settings, 'webhook', 'enabled') ? 'checked' : ''}><span>Enable webhook delivery</span></label>
            <label class="sycord-webhook-label">Destination URLs<textarea id="notify-webhook-urls" rows="7" placeholder="https://hooks.example.com/sycord">${esc(notificationSettingValue(settings, 'webhook', 'urls'))}</textarea><small>One HTTPS endpoint per line. Sycord posts the event, title, message, project, time, and safe action metadata.</small></label>
          </section>
        </div>
        <section class="legacy-fleet-control-card sycord-notification-history"><div class="sycord-notify-history-head"><div><p>Installed app activity</p><h3>In-app notifications <span>${eventData.unread_count || 0} unread</span></h3></div><button type="button" class="btn-pill btn-ghost btn-sm" id="notify-read-all"><i data-lucide="check-check"></i><span>Mark all read</span></button></div><div class="sycord-notification-list">${eventRows}</div></section>
      </section>`;
    target.querySelector('#notify-enable-pwa')?.addEventListener('click', async () => {
      try { toast(await enableSycordPwaAlerts()); await renderNotificationWorkspace(); }
      catch (error) { toast(error.message || 'Could not enable PWA alerts.'); }
    });
    target.querySelector('#notify-test')?.addEventListener('click', async () => {
      try { const result = await api('/notifications/test', {method: 'POST'}); toast(result.message); await renderNotificationWorkspace(); }
      catch (error) { toast(error.message || 'Could not send test notification.'); }
    });
    target.querySelector('#notify-read-all')?.addEventListener('click', async () => {
      await api('/notifications/read', {method: 'POST', body: JSON.stringify({event_ids: []})});
      await renderNotificationWorkspace();
    });
    target.querySelector('#notification-settings-form')?.addEventListener('submit', async (event) => {
      event.preventDefault();
      const payload = {email: {
        enabled: Boolean(target.querySelector('#notify-email-enabled')?.checked),
        recipients: target.querySelector('#notify-email-recipients')?.value || '',
        smtp_host: target.querySelector('#notify-smtp-host')?.value || '',
        smtp_port: Number(target.querySelector('#notify-smtp-port')?.value || 587),
        smtp_username: target.querySelector('#notify-smtp-username')?.value || '',
        smtp_password: target.querySelector('#notify-smtp-password')?.value || '',
        sender: target.querySelector('#notify-email-sender')?.value || '',
        use_tls: Boolean(target.querySelector('#notify-smtp-tls')?.checked),
      }, webhook: {
        enabled: Boolean(target.querySelector('#notify-webhook-enabled')?.checked),
        urls: target.querySelector('#notify-webhook-urls')?.value || '',
      }};
      try { const result = await api('/notifications/settings', {method: 'PUT', body: JSON.stringify(payload)}); toast(result.message); await renderNotificationWorkspace(); }
      catch (error) { toast(error.message || 'Could not save notification settings.'); }
    });
    refreshIcons();
  } catch (error) {
    target.innerHTML = `<section class="legacy-fleet-page"><p class="platform-error">Notifications are unavailable: ${esc(error.message)}</p></section>`;
  }
}

const repositoryFrameworkCatalog = [
  {label: 'Next.js', asset: 'nextjs-svgl.svg', patterns: [/next\.?js\b/, /\bnext[-_]/]},
  {label: 'Nuxt', asset: 'nuxt-svgl.svg', patterns: [/\bnuxt\b/]},
  {label: 'Remix', asset: 'remix-svgl.svg', patterns: [/\bremix\b/]},
  {label: 'Astro', asset: 'astro-svgl.svg', patterns: [/\bastro\b/]},
  {label: 'Svelte', asset: 'svelte-svgl.svg', patterns: [/\bsvelte\b/]},
  {label: 'Angular', asset: 'angular-svgl.svg', patterns: [/\bangular\b/]},
  {label: 'Vue', asset: 'vue-svgl.svg', patterns: [/\bvue\b/]},
  {label: 'Vite', asset: 'vite-svgl.svg', patterns: [/\bvite\b/]},
  {label: 'React', asset: 'react-svgl.svg', patterns: [/\breact\b/]},
  {label: 'Django', asset: 'django-svgl.svg', patterns: [/\bdjango\b/]},
  {label: 'Flask', asset: 'flask-svgl.svg', patterns: [/\bflask\b/]},
  {label: 'Laravel', asset: 'laravel-svgl.svg', patterns: [/\blaravel\b/]},
  {label: 'Express', asset: 'express-svgl.svg', patterns: [/\bexpress\b/]},
  {label: 'Node.js', asset: 'nodejs-svgl.svg', patterns: [/node\.?js/, /\bnode\b/]},
  {label: 'TypeScript', asset: 'typescript-svgl.svg', patterns: [/\btypescript\b/]},
  {label: 'JavaScript', asset: 'javascript-svgl.svg', patterns: [/\bjavascript\b/]},
  {label: 'Python', asset: 'python-svgl.svg', patterns: [/\bpython\b/]},
];

function repositoryFramework(repo) {
  const topics = Array.isArray(repo?.topics) ? repo.topics : [];
  const fingerprint = [repo?.name, repo?.full_name, repo?.description, repo?.language, ...topics].filter(Boolean).join(' ').toLowerCase();
  return repositoryFrameworkCatalog.find((framework) => framework.patterns.some((pattern) => pattern.test(fingerprint))) || null;
}

function renderRepositoryFrameworkIcon(framework) {
  if (!framework) return '<span class="deployment-structured-repository-icon deployment-structured-repository-icon-fallback" aria-label="Code repository"><i data-lucide="code-2"></i></span>';
  return `<span class="deployment-structured-repository-icon deployment-structured-framework-icon" title="${esc(framework.label)}"><img src="/static/vendor/frameworks/${framework.asset}?v=__VERSION__" alt="${esc(framework.label)}"></span>`;
}

function renderGithubRepositories() {
  const list = document.getElementById('github-repository-list');
  const search = (document.getElementById('github-repository-search')?.value || '').trim().toLowerCase();
  if (!list) return;
  const repositories = githubSourceRepositories.filter((repo) => !search || [repo.full_name, repo.description, repo.language, ...(repo.topics || [])].join(' ').toLowerCase().includes(search));
  if (!repositories.length) {
    list.innerHTML = `<p class="github-repository-empty">${githubSourceRepositories.length ? 'No repositories match this search.' : 'No repositories are available to this GitHub connection.'}</p>`;
    return;
  }
  list.innerHTML = repositories.map((repo) => {
    const framework = repositoryFramework(repo);
    const details = [framework?.label || repo.language || '', repo.private ? '<i data-lucide="lock-keyhole"></i> Private' : 'Public', repo.description ? esc(repo.description) : ''].filter(Boolean).join(' · ');
    return `
      <div class="github-repository-item deployment-structured-repository ${githubSourceSelection?.full_name === repo.full_name ? 'is-selected' : ''}" role="option" aria-selected="${githubSourceSelection?.full_name === repo.full_name}" data-github-repository="${esc(repo.full_name)}">
        ${renderRepositoryFrameworkIcon(framework)}
        <div class="deployment-structured-repository-copy">
          <span class="github-repository-name">${esc(repo.full_name)}</span>
          <span class="deployment-structured-repository-meta">${details}</span>
        </div>
        <button type="button" class="deployment-structured-import git-fast-add-btn" data-fast-add-repo="${esc(repo.full_name)}">
          <span>Import</span>
        </button>
      </div>
    `;
  }).join('');
  list.querySelectorAll('.git-fast-add-btn').forEach((btn) => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      const repo = e.currentTarget.dataset.fastAddRepo;
      if (repo) fastAddGithubRepository(repo);
    });
  });
  refreshIcons();
}

async function loadGithubRepositories() {
  const list = document.getElementById('github-repository-list');
  if (!githubSourceStatus?.connected) return;
  if (list) list.innerHTML = '<p class="github-repository-empty">Loading GitHub repositories…</p>';
  try {
    const result = await api('/projects/git/github/repositories');
    githubSourceRepositories = result.repositories || [];
    renderGithubRepositories();
  } catch (error) {
    if (list) list.innerHTML = `<p class="github-repository-empty">${esc(normalizeFetchError(error?.message) || 'Could not load GitHub repositories.')}</p>`;
  }
}

async function loadGithubSourceStatus({ loadRepositories = true } = {}) {
  try {
    const result = await api('/projects/git/github/status');
    renderGithubSourceStatus(result);
    if (result.connected && loadRepositories) await loadGithubRepositories();
  } catch (error) {
    renderGithubSourceStatus({ configured: false, connected: false });
  }
}

async function selectGithubRepository(fullName) {
  const repository = githubSourceRepositories.find((item) => item.full_name === fullName);
  const branchSelect = document.getElementById('github-branch-select');
  if (!repository || !branchSelect) return;
  githubSourceSelection = repository;
  document.getElementById('create-git-url').value = repository.clone_url || '';
  const nameInput = document.getElementById('create-name');
  if (nameInput && !nameInput.value.trim()) nameInput.value = repository.name || '';
  branchSelect.disabled = true;
  branchSelect.innerHTML = '<option value="">Loading branches…</option>';
  renderGithubRepositories();
  try {
    const result = await api(`/projects/git/github/repositories/${encodeURIComponent(repository.full_name)}/branches`);
    const branches = result.branches || [];
    branchSelect.innerHTML = branches.length ? branches.map((item) => `<option value="${esc(item.name)}">${esc(item.name)}</option>`).join('') : '<option value="">No branches available</option>';
    const preferred = branches.some((item) => item.name === repository.default_branch) ? repository.default_branch : branches[0]?.name || '';
    branchSelect.value = preferred;
    document.getElementById('create-branch').value = preferred;
    branchSelect.disabled = !branches.length;
  } catch (error) {
    branchSelect.innerHTML = '<option value="">Could not load branches</option>';
    toast(normalizeFetchError(error?.message) || 'Could not load repository branches');
  }
  refreshIcons();
}

function resetGithubSourceSelection() {
  githubSourceSelection = null;
  const branchSelect = document.getElementById('github-branch-select');
  if (branchSelect) {
    branchSelect.disabled = true;
    branchSelect.innerHTML = '<option value="">Choose a repository first</option>';
  }
  renderGithubRepositories();
}

async function connectGithubSource(popup) {
  try {
    const result = await api('/projects/git/github/connect');
    if (!result.authorization_url) throw new Error('GitHub did not provide an authorization URL.');
    if (popup) popup.location.href = result.authorization_url;
    else window.location.assign(result.authorization_url);
  } catch (error) {
    popup?.close();
    toast(normalizeFetchError(error?.message) || 'Could not start GitHub authorization.');
  }
}

async function disconnectGithubSource() {
  try {
    await api('/projects/git/github/disconnect', { method: 'DELETE' });
    githubSourceRepositories = [];
    resetGithubSourceSelection();
    renderGithubSourceStatus({ configured: githubSourceStatus?.configured, connected: false });
    toast('GitHub connection removed');
  } catch (error) {
    toast(normalizeFetchError(error?.message) || 'Could not disconnect GitHub.');
  }
}

function setProjectImportSource(source) {
  projectImportSource = source === 'zip' ? 'zip' : 'git';
  document.querySelectorAll('[data-import-source]').forEach(tab => {
    const active = tab.dataset.importSource === projectImportSource;
    tab.classList.toggle('active', active);
    tab.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  document.getElementById('deploy-git-fields')?.classList.toggle('hidden', projectImportSource !== 'git');
  document.getElementById('deploy-zip-fields')?.classList.toggle('hidden', projectImportSource !== 'zip');
  refreshIcons();
}

function setProjectDeployButton(label, icon = 'scan-search', disabled = false) {
  const button = document.getElementById('deploy-btn');
  if (!button) return;
  button.disabled = disabled;
  button.innerHTML = `<i data-lucide="${icon}"></i><span>${esc(label)}</span>`;
  refreshIcons();
}

function renderProjectAnalysis(analysis) {
  importedProjectAnalysis = analysis;
  const panel = document.getElementById('deploy-analysis');
  const grid = document.getElementById('deploy-detection-grid');
  const suggestions = document.getElementById('deploy-env-suggestions');
  panel?.classList.remove('hidden');
  if (!analysis) return;
  const fields = [
    ['Framework', analysis.framework || 'Custom'], ['Language', analysis.language || 'Unknown'],
    ['Build pack', analysis.build_pack || 'Manual'], ['Base directory', analysis.base_directory || '/'],
    ['Port', analysis.exposed_port ? `:${analysis.exposed_port}` : 'Auto'], ['Source files', String(analysis.files_detected || 0)],
  ];
  if (grid) grid.innerHTML = fields.map(([label, value]) => `<article><span>${esc(label)}</span><strong>${esc(value)}</strong></article>`).join('');
  const warningList = [...(analysis.warnings || []), ...(analysis.error ? [analysis.error] : [])];
  if (grid && warningList.length) grid.insertAdjacentHTML('beforeend', `<aside class="deploy-analysis-warning"><i data-lucide="triangle-alert"></i><p>${warningList.map(esc).join('<br>')}</p></aside>`);
  if (suggestions) {
    const values = analysis.environment_suggestions || [];
    suggestions.innerHTML = values.length ? values.map(item => `<button type="button" data-env-suggestion="${esc(item.key)}"><strong>${esc(item.key)}</strong><span>${esc(item.source || 'source')}</span><i data-lucide="plus"></i></button>`).join('') : '<p class="deploy-empty-hint">No referenced variable names were found. You can add them manually below.</p>';
  }
  const start = document.getElementById('create-start-cmd');
  const build = document.getElementById('create-build-cmd');
  if (start && !start.value) start.value = analysis.start_command || '';
  if (build && !build.value) build.value = analysis.build_command || '';
  setProjectDeployButton(analysis.status === 'ready' ? 'Deploy project' : 'Resolve configuration', analysis.status === 'ready' ? 'rocket' : 'sliders-horizontal', analysis.status !== 'ready');
  refreshIcons();
}

function resetCreateForm() {
  importedProjectId = null;
  importedProjectAnalysis = null;
  setProjectImportSource('git');
  resetGithubSourceSelection();
  ['create-name', 'create-git-url', 'create-start-cmd', 'create-build-cmd', 'create-env-vars'].forEach(id => {
    const input = document.getElementById(id);
    if (input) input.value = '';
  });
  ['create-branch', 'create-base-directory', 'create-zip-base-directory'].forEach(id => {
    const input = document.getElementById(id);
    if (input) input.value = id === 'create-branch' ? 'main' : '/';
  });
  const archive = document.getElementById('create-source-zip');
  if (archive) archive.value = '';
  document.getElementById('deploy-analysis')?.classList.add('hidden');
  const placeholder = document.getElementById('create-log-placeholder');
  const logPanel = document.getElementById('deploy-log-panel');
  placeholder?.classList.remove('hidden');
  logPanel?.classList.add('hidden');
  if (logPanel) clearLogPanel(logPanel);
  setProjectDeployButton('Analyze source');
  refreshIcons();
}

function appendSuggestedEnvironment(key) {
  const target = document.getElementById('create-env-vars');
  if (!target || !key) return;
  const current = target.value.trimEnd();
  if (!new RegExp(`(^|\\n)${key.replace(/[.*+?^${}()|[\\]\\]/g, '\\$&')}=`).test(current)) target.value = `${current}${current ? '\\n' : ''}${key}=`;
  target.focus();
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

function serviceEnvironmentEntries(project) {
  const values = project?.env_vars;
  if (!values || typeof values !== 'object' || Array.isArray(values)) return [];
  return Object.entries(values).sort(([left], [right]) => left.localeCompare(right));
}

function closeServiceEnvironmentModal() {
  document.getElementById('svc-env-modal')?.classList.add('hidden');
}

function openServiceEnvironmentModal(project, key = '') {
  const modal = document.getElementById('svc-env-modal');
  const keyInput = document.getElementById('svc-env-key');
  const valueInput = document.getElementById('svc-env-value');
  const original = document.getElementById('svc-env-original-key');
  const title = document.getElementById('svc-env-modal-title');
  if (!modal || !keyInput || !valueInput || !original || !title) return;
  const values = Object.fromEntries(serviceEnvironmentEntries(project));
  original.value = key;
  keyInput.value = key;
  valueInput.value = key ? String(values[key] ?? '') : '';
  title.textContent = key ? 'Edit variable' : 'Add variable';
  modal.classList.remove('hidden');
  keyInput.focus();
}

async function persistServiceEnvironment(project, env_vars) {
  const result = await api(`/projects/${encodeURIComponent(project.id)}`, {
    method: 'PUT',
    body: JSON.stringify({env_vars}),
  });
  toast(result.message || 'Environment saved');
  await loadProjects();
  const refreshed = projects.find(item => item.id === project.id);
  if (refreshed) renderServiceDashboard(refreshed, false);
}

async function renderServiceRollbackHistory(project) {
  const target = document.getElementById('svc-rollback-history');
  if (!target) return;
  target.innerHTML = '<p class="hint">Loading deployment history…</p>';
  try {
    const payload = await api(`/projects/${encodeURIComponent(project.id)}/deployments?limit=20`);
    const rows = payload.deployments || [];
    if (!rows.length) {
      target.innerHTML = '<p class="hint">No deployments have been recorded for this project.</p>';
      return;
    }
    target.innerHTML = rows.map((run) => {
      const status = cssClassSafe(run.status || 'queued');
      const when = run.started_at ? new Date(run.started_at).toLocaleString() : '—';
      const canRollback = run.status === 'succeeded' && Boolean(run.commit_sha);
      const detail = run.error || (run.commit_sha ? `Commit ${String(run.commit_sha).slice(0, 12)}` : 'No recorded Git commit');
      return `<article class="svc-rollback-run"><div class="svc-rollback-run-state ${status}"></div><div><strong>${esc(run.trigger || 'manual deploy')}</strong><span>${esc(when)} · ${esc(detail)}</span></div>${canRollback ? `<button type="button" class="btn-pill btn-ghost btn-sm" data-svc-rollback-run="${esc(run.id)}"><i data-lucide="rotate-ccw"></i><span>Rollback</span></button>` : '<em>Not rollback-ready</em>'}</article>`;
    }).join('');
    target.querySelectorAll('[data-svc-rollback-run]').forEach(button => {
      button.onclick = async () => {
        const runId = button.dataset.svcRollbackRun;
        if (!runId) return;
        button.disabled = true;
        try {
          const result = await api(`/projects/${encodeURIComponent(project.id)}/deployments/${encodeURIComponent(runId)}/rollback`, {method: 'POST'});
          toast(result.message || 'Rollback queued');
          await renderServiceRollbackHistory(project);
        } catch (error) {
          toast(normalizeFetchError(error?.message) || 'Could not queue rollback.');
        } finally {
          button.disabled = false;
        }
      };
    });
    refreshIcons();
  } catch (error) {
    target.innerHTML = `<p class="hint">${esc(normalizeFetchError(error?.message) || 'Unable to load deployment history.')}</p>`;
  }
}

function renderServiceManagementWorkspaces(project) {
  const environmentCards = document.getElementById('svc-env-cards');
  if (environmentCards) {
    const entries = serviceEnvironmentEntries(project);
    environmentCards.innerHTML = entries.length
      ? entries.map(([key, value]) => `<article class="svc-env-card"><div><code>${esc(key)}</code><span>${esc(String(value))}</span></div><div><button type="button" class="svc-env-card-action" data-svc-env-edit="${esc(key)}" aria-label="Edit ${esc(key)}"><i data-lucide="pencil"></i></button><button type="button" class="svc-env-card-action danger" data-svc-env-delete="${esc(key)}" aria-label="Delete ${esc(key)}"><i data-lucide="trash-2"></i></button></div></article>`).join('')
      : '<p class="svc-env-empty">No variables are configured. Add the first runtime value when your application needs one.</p>';
    environmentCards.querySelectorAll('[data-svc-env-edit]').forEach(button => {
      button.onclick = () => openServiceEnvironmentModal(project, button.dataset.svcEnvEdit || '');
    });
    environmentCards.querySelectorAll('[data-svc-env-delete]').forEach(button => {
      button.onclick = async () => {
        const key = button.dataset.svcEnvDelete || '';
        if (!key || !window.confirm(`Remove ${key} from this project environment?`)) return;
        const next = Object.fromEntries(serviceEnvironmentEntries(project));
        delete next[key];
        try { await persistServiceEnvironment(project, next); }
        catch (error) { toast(normalizeFetchError(error?.message) || 'Could not remove variable.'); }
      };
    });
  }

  const addEnvironment = document.getElementById('svc-env-add-btn');
  if (addEnvironment) addEnvironment.onclick = () => openServiceEnvironmentModal(project);
  document.querySelectorAll('[data-svc-env-close]').forEach(button => { button.onclick = closeServiceEnvironmentModal; });
  const envForm = document.getElementById('svc-env-form');
  if (envForm) {
    envForm.onsubmit = async event => {
      event.preventDefault();
      const original = document.getElementById('svc-env-original-key')?.value || '';
      const key = document.getElementById('svc-env-key')?.value.trim() || '';
      const value = document.getElementById('svc-env-value')?.value || '';
      if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) return toast('Use a valid environment variable key.');
      const next = Object.fromEntries(serviceEnvironmentEntries(project));
      if (original && original !== key) delete next[original];
      next[key] = value;
      try {
        await persistServiceEnvironment(project, next);
        closeServiceEnvironmentModal();
      } catch (error) {
        toast(normalizeFetchError(error?.message) || 'Could not save variable.');
      }
    };
  }

  document.querySelectorAll('[data-svc-edit-project]').forEach(button => { button.onclick = () => openServiceEditModal(project); });
  const primaryDomain = document.getElementById('svc-primary-domain');
  if (primaryDomain) {
    const domain = project.domain || '';
    primaryDomain.innerHTML = `<span>Production domain</span>${domain && project.url ? `<a href="${esc(project.url)}" target="_blank" rel="noopener">${esc(domain)}<i data-lucide="arrow-up-right"></i></a>` : `<strong>${esc(domain || 'Not configured')}</strong>`}`;
  }
  const customDomain = document.getElementById('svc-custom-tls-domain');
  const customEnabled = document.getElementById('svc-custom-tls-enabled');
  if (customDomain) customDomain.value = project.custom_tls_domain || '';
  if (customEnabled) customEnabled.checked = Boolean(project.custom_tls_enabled);
  const customDomainForm = document.getElementById('svc-custom-domain-form');
  if (customDomainForm) {
    customDomainForm.onsubmit = async event => {
      event.preventDefault();
      const status = document.getElementById('svc-custom-tls-result');
      if (status) status.textContent = 'Applying domain configuration…';
      try {
        const result = await api(`/ssl/projects/${encodeURIComponent(project.id)}/custom-tls`, {
          method: 'POST',
          body: JSON.stringify({custom_tls_domain: customDomain?.value.trim() || '', custom_tls_enabled: Boolean(customEnabled?.checked)}),
        });
        if (status) status.textContent = result.message || 'Custom domain saved.';
        toast(result.message || 'Custom domain saved');
        await loadProjects();
        const refreshed = projects.find(item => item.id === project.id);
        if (refreshed) renderServiceDashboard(refreshed, false);
      } catch (error) {
        if (status) status.textContent = normalizeFetchError(error?.message) || 'Could not save custom domain.';
      }
    };
  }
  document.querySelectorAll('[data-svc-open-certificates]').forEach(button => {
    button.onclick = () => { activePlatformPage = 'certificates'; showView('platform'); };
  });

  const memory = document.getElementById('svc-resource-memory');
  const cpus = document.getElementById('svc-resource-cpus');
  if (memory) memory.value = project.resource_memory || '';
  if (cpus) cpus.value = project.resource_cpus || '';
  const speedForm = document.getElementById('svc-speed-form');
  if (speedForm) {
    speedForm.onsubmit = async event => {
      event.preventDefault();
      const status = document.getElementById('svc-speed-status');
      try {
        const result = await api(`/projects/${encodeURIComponent(project.id)}/deployment-config`, {
          method: 'PUT',
          body: JSON.stringify({resource_memory: memory?.value || '', resource_cpus: cpus?.value || ''}),
        });
        if (status) status.textContent = 'Deployment limits saved. Redeploy to apply them.';
        toast(result.message || 'Deployment limits saved');
        await loadProjects();
      } catch (error) { if (status) status.textContent = normalizeFetchError(error?.message) || 'Could not save deployment limits.'; }
    };
  }

  const branch = document.getElementById('svc-settings-branch');
  const startCommand = document.getElementById('svc-settings-start-command');
  const autoDeploy = document.getElementById('svc-settings-auto-deploy');
  if (branch) branch.value = project.branch || 'main';
  if (startCommand) startCommand.value = project.start_command || '';
  if (autoDeploy) autoDeploy.checked = Boolean(project.auto_deploy);
  const autoState = document.getElementById('svc-auto-deploy-state');
  if (autoState) {
    autoState.textContent = !project.git_url
      ? 'Import a GitHub repository before automatic branch deployment can be enabled.'
      : project.github_account_id
        ? `GitHub account is linked. ${project.auto_deploy ? `Watching ${project.branch || 'main'} every 5 minutes.` : 'Enable to queue the current branch head once, then only newer commits.'}`
        : 'Enable while signed in to the connected GitHub account to link this project securely.';
  }
  const settingsForm = document.getElementById('svc-deployment-settings-form');
  if (settingsForm) {
    settingsForm.onsubmit = async event => {
      event.preventDefault();
      try {
        const result = await api(`/projects/${encodeURIComponent(project.id)}/deployment-config`, {
          method: 'PUT',
          body: JSON.stringify({
            branch: branch?.value.trim() || 'main',
            start_command: startCommand?.value.trim() || '',
            auto_deploy: Boolean(autoDeploy?.checked),
          }),
        });
        toast(result.message || 'Deployment settings saved');
        await loadProjects();
        const refreshed = projects.find(item => item.id === project.id);
        if (refreshed) renderServiceDashboard(refreshed, false);
      } catch (error) {
        toast(normalizeFetchError(error?.message) || 'Could not save deployment settings.');
      }
    };
  }

  const refreshRollbackHistory = document.getElementById('svc-rollback-refresh');
  if (refreshRollbackHistory) refreshRollbackHistory.onclick = () => renderServiceRollbackHistory(project);
  if (activeSvcTab === 'rollbacks') void renderServiceRollbackHistory(project);
  if (activeSvcTab === 'release') void renderReleaseWorkspace(project);
  refreshIcons();
}

function releaseEventTime(value) {
  try { return value ? new Date(value).toLocaleString() : '—'; } catch (_) { return '—'; }
}

async function renderReleaseWorkspace(project) {
  const target = document.getElementById('release-workspace-content');
  if (!target) return;
  target.innerHTML = '<p class="hint">Loading release controls…</p>';
  try {
    const payload = await api(`/projects/${encodeURIComponent(project.id)}/release`);
    const workspace = payload.workspace || {};
    const policy = workspace.policy || {};
    const environments = workspace.environments || [];
    const approvals = workspace.approvals || [];
    const team = workspace.team || [];
    const restorePoints = workspace.restore_points || [];
    const events = workspace.events || [];
    const hostMetrics = workspace.host_metrics || {};
    const hostPressure = Math.max(Number(hostMetrics.cpu_percent || 0), Number(hostMetrics.ram_percent || 0), Number(hostMetrics.disk_percent || 0));
    const hostPressureState = hostPressure >= Number(policy.resource_alert_percent || 85) ? 'Attention' : 'Within policy';
    const canManage = Boolean(payload.can_manage);
    const disabled = canManage ? '' : ' disabled';
    const envRows = environments.map((environment) => {
      const isPreview = environment.kind === 'preview';
      const action = isPreview
        ? `<div class="release-row-actions"><button type="button" class="btn-pill btn-ghost btn-sm" data-release-preview="start"${disabled}><i data-lucide="monitor-up"></i><span>Start</span></button><button type="button" class="btn-pill btn-ghost btn-sm" data-release-preview="stop"${disabled}><i data-lucide="square"></i><span>Stop</span></button></div>`
        : `<button type="button" class="btn-pill btn-primary btn-sm" data-release-deploy="${esc(environment.id)}"${disabled}><i data-lucide="rocket"></i><span>${environment.require_approval ? 'Request release' : 'Release'}</span></button>`;
      return `<article class="release-environment-card release-${esc(environment.kind)}">
        <header><div><span class="release-environment-kind">${esc(environment.kind)}</span><h3>${esc(environment.name)}</h3></div>${action}</header>
        <form class="release-environment-form" data-release-environment="${esc(environment.id)}">
          <label>Branch<input name="branch" value="${esc(environment.branch || 'main')}"${disabled}></label>
          <label>Domain<input name="domain" value="${esc(environment.domain || '')}" placeholder="Optional domain"${disabled}></label>
          <label class="release-switch"><input type="checkbox" name="auto_deploy"${environment.auto_deploy ? ' checked' : ''}${disabled}><span>Automatic deploy</span></label>
          <label class="release-switch"><input type="checkbox" name="require_approval"${environment.require_approval ? ' checked' : ''}${disabled}><span>Require approval</span></label>
          ${canManage ? '<button type="submit" class="release-inline-save">Save</button>' : ''}
        </form>
      </article>`;
    }).join('');
    const approvalRows = approvals.length
      ? approvals.map((approval) => `<article class="release-list-row"><div><strong>${esc(approval.environment_name || 'Release')} · ${esc(approval.status)}</strong><span>${esc(approval.requested_by || 'operator')} · ${esc(releaseEventTime(approval.created_at))}${approval.note ? ` · ${esc(approval.note)}` : ''}</span></div>${approval.status === 'pending' && canManage ? `<div class="release-row-actions"><button type="button" data-release-approval="approve:${esc(approval.id)}">Approve</button><button type="button" data-release-approval="reject:${esc(approval.id)}">Reject</button></div>` : ''}</article>`).join('')
      : '<p class="release-empty">No approval requests. Protected environments create one before they queue a release.</p>';
    const restoreRows = restorePoints.length
      ? restorePoints.map((point) => `<article class="release-list-row"><div><strong>${esc(point.label)}</strong><span>${esc(point.status)} · ${esc(releaseEventTime(point.created_at))}</span></div>${canManage && point.status !== 'verified' ? `<button type="button" data-release-verify="${esc(point.id)}">Verify</button>` : ''}</article>`).join('')
      : '<p class="release-empty">No recovery points recorded yet.</p>';
    const teamRows = team.length
      ? team.map((member) => `<article class="release-list-row"><div><strong>${esc(member.display_name || member.email)}</strong><span>${esc(member.email)} · ${esc(member.role)}</span></div>${canManage ? `<button type="button" data-release-member-delete="${esc(member.id)}" aria-label="Remove ${esc(member.email)}"><i data-lucide="x"></i></button>` : ''}</article>`).join('')
      : '<p class="release-empty">Only the current operator can manage this project until a project role is added.</p>';
    const eventRows = events.length
      ? events.slice(0, 12).map((event) => `<article class="release-timeline-row ${cssClassSafe(event.severity || 'info')}"><span></span><div><strong>${esc(event.title)}</strong><small>${esc(event.detail || event.event_type || '')} · ${esc(releaseEventTime(event.created_at))}</small></div></article>`).join('')
      : '<p class="release-empty">Release, preview, recovery, and policy activity will appear here.</p>';
    target.innerHTML = `
      <div class="release-overview-strip">
        <div><span>Strategy</span><strong>${esc(String(policy.deployment_strategy || 'rolling').replace('_', ' '))}</strong></div>
        <div><span>Preview retention</span><strong>${esc(String(policy.preview_retention_days || 7))} days</strong></div>
        <div><span>Host pressure</span><strong>${esc(String(Math.round(hostPressure)))}% · ${esc(hostPressureState)}</strong></div>
        <div><span>Resource alert</span><strong>${esc(String(policy.resource_alert_percent || 85))}%</strong></div>
        <div><span>Backup policy</span><strong>${policy.backup_enabled ? esc(policy.backup_schedule || 'daily') : 'Off'}</strong></div>
      </div>
      ${!canManage ? '<p class="release-readonly"><i data-lucide="lock"></i>You have view-only project access. Ask an owner, admin, or deployer to change release controls.</p>' : ''}
      <section class="release-section"><header><div><p>Release path</p><h3>Environments</h3></div><span>Each environment keeps its own branch, domain, automation, and protection policy.</span></header><div class="release-environments-grid">${envRows}</div></section>
      <section class="release-section release-protection-grid"><div><header><div><p>Production control</p><h3>Approvals</h3></div></header><div class="release-list">${approvalRows}</div></div><div><header><div><p>Release policy</p><h3>Strategy and guardrails</h3></div></header><form id="release-policy-form" class="release-policy-form"><label>Strategy<select name="deployment_strategy"${disabled}><option value="rolling"${policy.deployment_strategy === 'rolling' ? ' selected' : ''}>Rolling</option><option value="blue_green"${policy.deployment_strategy === 'blue_green' ? ' selected' : ''}>Blue-green</option><option value="canary"${policy.deployment_strategy === 'canary' ? ' selected' : ''}>Canary</option></select></label><label>Canary traffic<input name="canary_percent" type="number" min="1" max="100" value="${esc(String(policy.canary_percent || 10))}"${disabled}></label><label>Alert threshold<input name="resource_alert_percent" type="number" min="50" max="100" value="${esc(String(policy.resource_alert_percent || 85))}"${disabled}></label><label>Storage budget (MB)<input name="storage_limit_mb" type="number" min="0" value="${esc(String(policy.storage_limit_mb || 0))}"${disabled}></label><label class="release-switch"><input name="preview_enabled" type="checkbox"${policy.preview_enabled ? ' checked' : ''}${disabled}><span>Allow preview deployments</span></label><label class="release-switch"><input name="backup_enabled" type="checkbox"${policy.backup_enabled ? ' checked' : ''}${disabled}><span>Track backup policy</span></label><label>Backup cadence<select name="backup_schedule"${disabled}><option value="daily"${policy.backup_schedule === 'daily' ? ' selected' : ''}>Daily</option><option value="weekly"${policy.backup_schedule === 'weekly' ? ' selected' : ''}>Weekly</option></select></label><label>Keep backups<input name="backup_retention_days" type="number" min="1" max="3650" value="${esc(String(policy.backup_retention_days || 14))}"${disabled}></label>${canManage ? '<button type="submit" class="btn-pill btn-primary"><i data-lucide="save"></i><span>Save policy</span></button>' : ''}</form></div></section>
      <section class="release-section release-recovery-grid"><div><header><div><p>Recovery readiness</p><h3>Restore points</h3></div>${canManage ? '<button type="button" class="btn-pill btn-ghost btn-sm" id="release-create-restore"><i data-lucide="archive-restore"></i><span>Record point</span></button>' : ''}</header><div class="release-list">${restoreRows}</div></div><div><header><div><p>Project access</p><h3>Team roles</h3></div></header><div class="release-list">${teamRows}</div>${canManage ? '<form id="release-team-form" class="release-team-form"><input name="email" type="email" placeholder="teammate@example.com" required><input name="display_name" placeholder="Name"><select name="role"><option value="viewer">Viewer</option><option value="deployer">Deployer</option><option value="admin">Admin</option><option value="owner">Owner</option></select><button type="submit">Add role</button></form>' : ''}</div></section>
      <section class="release-section"><header><div><p>Operational timeline</p><h3>Release activity</h3></div><span>Deploy queue, preview, policy, recovery, and access events.</span></header><div class="release-timeline">${eventRows}</div></section>`;

    const refresh = document.getElementById('release-refresh');
    if (refresh) refresh.onclick = () => renderReleaseWorkspace(project);
    target.querySelectorAll('[data-release-environment]').forEach((form) => {
      form.onsubmit = async (event) => {
        event.preventDefault();
        const data = new FormData(form);
        try {
          await api(`/projects/${encodeURIComponent(project.id)}/release/environments/${encodeURIComponent(form.dataset.releaseEnvironment)}`, {method: 'PUT', body: JSON.stringify({branch: data.get('branch'), domain: data.get('domain'), auto_deploy: data.get('auto_deploy') === 'on', require_approval: data.get('require_approval') === 'on'})});
          toast('Environment saved');
          await renderReleaseWorkspace(project);
        } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not save environment.'); }
      };
    });
    target.querySelectorAll('[data-release-deploy]').forEach((button) => {
      button.onclick = async () => {
        button.disabled = true;
        try {
          const result = await api(`/projects/${encodeURIComponent(project.id)}/release/deploy`, {method: 'POST', body: JSON.stringify({environment_id: button.dataset.releaseDeploy})});
          toast(result.message || (result.approval_required ? 'Approval requested' : 'Release queued'));
          await renderReleaseWorkspace(project);
          if (result.project) { await loadProjects(); }
        } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not queue release.'); } finally { button.disabled = false; }
      };
    });
    target.querySelectorAll('[data-release-preview]').forEach((button) => {
      button.onclick = async () => { try { const result = await api(`/projects/${encodeURIComponent(project.id)}/release/preview/${button.dataset.releasePreview}`, {method: 'POST'}); toast(result.message || 'Preview action complete'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Preview action failed.'); } };
    });
    const policyForm = document.getElementById('release-policy-form');
    if (policyForm) policyForm.onsubmit = async (event) => {
      event.preventDefault(); const data = new FormData(policyForm);
      try { await api(`/projects/${encodeURIComponent(project.id)}/release/policy`, {method: 'PUT', body: JSON.stringify({deployment_strategy: data.get('deployment_strategy'), canary_percent: Number(data.get('canary_percent')), preview_enabled: data.get('preview_enabled') === 'on', resource_alert_percent: Number(data.get('resource_alert_percent')), storage_limit_mb: Number(data.get('storage_limit_mb')), backup_enabled: data.get('backup_enabled') === 'on', backup_schedule: data.get('backup_schedule'), backup_retention_days: Number(data.get('backup_retention_days'))})}); toast('Release policy saved'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not save policy.'); }
    };
    target.querySelectorAll('[data-release-approval]').forEach((button) => {
      button.onclick = async () => { const [decision, approvalId] = (button.dataset.releaseApproval || '').split(':'); try { await api(`/projects/${encodeURIComponent(project.id)}/release/approvals/${encodeURIComponent(approvalId)}/decision`, {method: 'POST', body: JSON.stringify({approved: decision === 'approve', note: ''})}); toast(`Release ${decision}d`); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not decide approval.'); } };
    });
    const restoreButton = document.getElementById('release-create-restore');
    if (restoreButton) restoreButton.onclick = async () => { try { await api(`/projects/${encodeURIComponent(project.id)}/release/restore-points`, {method: 'POST', body: JSON.stringify({label: `Manual point · ${new Date().toLocaleString()}`})}); toast('Recovery point recorded'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not create recovery point.'); } };
    target.querySelectorAll('[data-release-verify]').forEach((button) => { button.onclick = async () => { try { await api(`/projects/${encodeURIComponent(project.id)}/release/restore-points/${encodeURIComponent(button.dataset.releaseVerify)}/verify`, {method: 'POST'}); toast('Recovery point verified'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not verify recovery point.'); } }; });
    const teamForm = document.getElementById('release-team-form');
    if (teamForm) teamForm.onsubmit = async (event) => { event.preventDefault(); const data = new FormData(teamForm); try { await api(`/projects/${encodeURIComponent(project.id)}/release/team`, {method: 'POST', body: JSON.stringify({email: data.get('email'), display_name: data.get('display_name'), role: data.get('role')})}); toast('Project role saved'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not save project role.'); } };
    target.querySelectorAll('[data-release-member-delete]').forEach((button) => { button.onclick = async () => { try { await api(`/projects/${encodeURIComponent(project.id)}/release/team/${encodeURIComponent(button.dataset.releaseMemberDelete)}`, {method: 'DELETE'}); toast('Project role removed'); await renderReleaseWorkspace(project); } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not remove project role.'); } }; });
    refreshIcons();
  } catch (error) {
    target.innerHTML = `<p class="hint">${esc(normalizeFetchError(error?.message) || 'Unable to load release controls.')}</p>`;
  }
}

function switchSvcTab(tab) {
  const allowed = ['general', 'release', 'domains', 'env', 'firewall', 'cdn', 'speed', 'logs', 'rollbacks', 'preview', 'settings'];
  if (!allowed.includes(tab)) tab = 'general';
  const prevTab = activeSvcTab;
  activeSvcTab = tab;
  document.querySelectorAll('.nav-sublink[data-svc-tab]').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.svcTab === tab);
  });
  document.querySelectorAll('.svc-tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.svcPanel === tab);
  });
  if (tab === 'preview') {
    previewTabActive = true;
    const p = projects.find(x => x.id === activeServiceId);
    if (p) renderPreviewSection(p);
  } else if (tab === 'release') {
    const p = projects.find(x => x.id === activeServiceId);
    if (p) void renderReleaseWorkspace(p);
  } else if (tab === 'rollbacks') {
    const p = projects.find(x => x.id === activeServiceId);
    if (p) void renderServiceRollbackHistory(p);
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

function projectEditField(id) {
  return document.getElementById(id);
}

function setProjectEditTab(tab) {
  document.querySelectorAll('[data-project-settings-tab]').forEach(button => {
    button.classList.toggle('active', button.dataset.projectSettingsTab === tab);
  });
  document.querySelectorAll('[data-project-settings-panel]').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.projectSettingsPanel === tab);
  });
  document.querySelector('.project-settings-panels')?.scrollTo({top: 0, behavior: 'auto'});
}

async function copyProjectEditText(value, successMessage) {
  if (!value) return toast('Nothing is available to copy.');
  try {
    await navigator.clipboard.writeText(value);
    const result = projectEditField('svc-edit-utility-result');
    if (result) result.textContent = successMessage;
    toast(successMessage);
  } catch (_) {
    toast('Copy is unavailable in this browser context.');
  }
}

function projectEditSnapshot(project) {
  return JSON.stringify({
    project_id: project.id,
    name: project.name,
    repository: project.git_url || null,
    branch: project.branch || 'main',
    domain: project.domain || null,
    deploy_type: project.deploy_type || 'auto',
    start_command: project.start_command || null,
    healthcheck_path: project.healthcheck_path || '/',
    healthcheck_interval: project.healthcheck_interval || null,
    auto_deploy: Boolean(project.auto_deploy),
    resource_memory: project.resource_memory || null,
    resource_cpus: project.resource_cpus || null,
  }, null, 2);
}

function openServiceEditModal(p) {
  const modal = projectEditField('svc-edit-modal');
  const nameInput = projectEditField('svc-edit-name-input');
  const domainInput = projectEditField('svc-edit-domain-input');
  if (!modal || !nameInput || !domainInput) return;
  const setValue = (id, value) => { const input = projectEditField(id); if (input) input.value = value ?? ''; };
  nameInput.value = p.name || '';
  domainInput.value = p.domain || '';
  setValue('svc-edit-healthcheck-path', p.healthcheck_path || '/');
  setValue('svc-edit-git-url', p.git_url || 'No repository connected');
  setValue('svc-edit-branch', p.branch || 'main');
  setValue('svc-edit-start-command', p.start_command || '');
  setValue('svc-edit-deploy-type', p.deploy_type || 'auto');
  setValue('svc-edit-healthcheck-interval', p.healthcheck_interval || '');
  setValue('svc-edit-resource-memory', p.resource_memory || '');
  setValue('svc-edit-resource-cpus', p.resource_cpus || '');
  setValue('svc-edit-dockerfile-path', p.dockerfile_path || '');
  setValue('svc-edit-compose-file', p.compose_file || '');
  setValue('svc-edit-docker-image', p.docker_image || '');
  const autoDeploy = projectEditField('svc-edit-auto-deploy');
  if (autoDeploy) autoDeploy.checked = Boolean(p.auto_deploy);
  const mark = projectEditField('svc-edit-project-mark');
  if (mark) mark.textContent = displayTitle(p).slice(0, 1).toUpperCase() || 'S';
  const title = projectEditField('svc-edit-title');
  if (title) title.textContent = displayTitle(p);
  const meta = projectEditField('svc-edit-project-meta');
  if (meta) meta.textContent = `${p.running ? 'Production running' : 'Project stopped'} · ${p.branch || 'main'} · ${p.status || 'ready'}`;
  const environmentCount = projectEditField('svc-edit-env-count');
  if (environmentCount) environmentCount.textContent = String(Object.keys(p.env_vars || {}).length);
  const releaseSummary = projectEditField('svc-edit-release-summary');
  if (releaseSummary) releaseSummary.textContent = p.git_url ? 'Protection and recovery ready' : 'Connect Git to govern releases';
  const liveLink = projectEditField('svc-edit-open-live');
  if (liveLink) { liveLink.href = p.url || '#'; liveLink.toggleAttribute('aria-disabled', !p.url); }
  const copyGit = projectEditField('svc-edit-copy-git');
  if (copyGit) { copyGit.disabled = !p.git_url; copyGit.onclick = () => copyProjectEditText(p.git_url || '', 'Repository URL copied'); }
  const copyId = projectEditField('svc-edit-copy-id');
  if (copyId) copyId.onclick = () => copyProjectEditText(p.id, 'Project ID copied');
  const copyConfig = projectEditField('svc-edit-copy-config');
  if (copyConfig) copyConfig.onclick = () => copyProjectEditText(projectEditSnapshot(p), 'Safe configuration copied');
  const runHealth = projectEditField('svc-edit-run-health');
  if (runHealth) runHealth.onclick = async () => {
    const result = projectEditField('svc-edit-utility-result');
    runHealth.disabled = true;
    if (result) result.textContent = 'Running health check…';
    try {
      const health = await api(`/projects/${encodeURIComponent(p.id)}/health`);
      const text = health.healthy ? `Healthy · ${health.status_code || 'reachable'} · ${health.detail || ''}` : `Unhealthy · ${health.detail || 'No response'}`;
      if (result) result.textContent = text;
      toast(text);
    } catch (error) {
      const text = normalizeFetchError(error?.message) || 'Health check could not run.';
      if (result) result.textContent = text;
      toast(text);
    } finally { runHealth.disabled = false; }
  };
  const openEnvironment = projectEditField('svc-edit-open-environment');
  if (openEnvironment) openEnvironment.onclick = () => { closeServiceEditModal(); switchSvcTab('env'); };
  const openRelease = projectEditField('svc-edit-open-release');
  if (openRelease) openRelease.onclick = () => { closeServiceEditModal(); switchSvcTab('release'); };
  document.querySelectorAll('[data-project-settings-tab]').forEach(button => { button.onclick = () => setProjectEditTab(button.dataset.projectSettingsTab || 'general'); });
  modal.classList.remove('hidden');
  modal.dataset.projectId = p.id;
  setProjectEditTab('general');
  nameInput.focus();
  refreshIcons();
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

function renderDeploymentSitePreview(p) {
  const frame = document.getElementById('svc-deploy-preview-frame');
  const placeholder = document.getElementById('svc-deploy-preview-placeholder');
  const previewLabel = document.getElementById('svc-preview-label');
  const previewOpen = document.getElementById('svc-preview-open');
  const url = p.url || '';
  const live = Boolean(url && p.running);
  if (previewLabel) previewLabel.textContent = live ? (p.domain || connLabel(p) || 'Live site preview') : 'Preparing live site';
  if (previewOpen) {
    previewOpen.href = live ? url : '#';
    previewOpen.toggleAttribute('aria-disabled', !live);
  }
  if (!frame || !placeholder) return;
  if (live) {
    if (frame.dataset.previewUrl !== url) {
      frame.src = url;
      frame.dataset.previewUrl = url;
    }
    frame.classList.remove('hidden');
    placeholder.classList.add('hidden');
  } else {
    frame.classList.add('hidden');
    frame.removeAttribute('src');
    delete frame.dataset.previewUrl;
    const title = placeholder.querySelector('strong');
    const detail = placeholder.querySelector('span');
    if (title) title.textContent = p.status === 'deploying' ? 'Deployment in progress' : 'Live preview unavailable';
    if (detail) detail.textContent = p.status === 'deploying'
      ? 'Your site will appear here as soon as the release starts.'
      : 'Start or deploy this project to load its live site here.';
    placeholder.classList.remove('hidden');
  }
}

async function loadServiceHealth(projectId) {
  const state = document.getElementById('svc-health-state');
  const detail = document.getElementById('svc-health-detail');
  if (!state || !detail) return;
  state.textContent = 'Checking…';
  state.className = 'svc-health-state is-checking';
  try {
    const result = await api(`/projects/${encodeURIComponent(projectId)}/health`);
    state.textContent = result.healthy ? `Healthy · ${result.status_code}` : 'Unhealthy';
    state.className = `svc-health-state ${result.healthy ? 'is-healthy' : 'is-unhealthy'}`;
    detail.textContent = `${result.url} · ${result.detail || 'No response detail'}`;
  } catch (err) {
    state.textContent = 'Health check failed';
    state.className = 'svc-health-state is-unhealthy';
    detail.textContent = normalizeFetchError(err?.message) || 'Unable to probe the service.';
  }
}
async function loadDeploymentHistory(projectId) {
  const list = document.getElementById('svc-deployments-list');
  if (!list) return;
  try {
    const payload = await api(`/projects/${encodeURIComponent(projectId)}/deployments?limit=8`);
    const rows = payload.deployments || [];
    if (!rows.length) {
      list.innerHTML = '<span class="hint">No deployments recorded yet.</span>';
      return;
    }
    list.innerHTML = rows.map((run) => {
      const status = cssClassSafe(run.status || 'queued');
      const when = run.started_at ? new Date(run.started_at).toLocaleString() : '—';
      const duration = run.duration_ms ? `${Math.round(run.duration_ms / 1000)}s` : '—';
      return `<div class="svc-deployment-row"><span class="svc-deployment-status ${status}"></span><span class="svc-deployment-main"><strong>${esc(run.status || 'queued')}</strong><small>${esc(run.trigger || 'manual')} · ${esc(when)}</small></span><span class="svc-deployment-duration">${esc(duration)}</span></div>`;
    }).join('');
  } catch (err) {
    list.innerHTML = `<span class="hint">${esc(normalizeFetchError(err?.message) || 'Unable to load deployment history.')}</span>`;
  }
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
  if (uuidPill) uuidPill.textContent = `${p.name || 'deployment'} · ${String(p.id || '').slice(0, 8)}`;
  const deployName = document.getElementById('svc-deploy-name');
  if (deployName) deployName.textContent = displayTitle(p);
  const deploymentStatus = statusLabel(p);
  ['svc-deploy-status', 'svc-deploy-status-summary'].forEach((id) => {
    const target = document.getElementById(id);
    if (target) target.textContent = deploymentStatus;
  });
  const deploymentState = document.getElementById('svc-deploy-state-dot');
  if (deploymentState) {
    deploymentState.className = 'syte-state-dot';
    deploymentState.classList.add(p.status === 'deploying' ? 'deploying' : p.running ? 'ready' : 'stopped');
  }
  const deploymentDomain = document.getElementById('svc-deploy-domain');
  if (deploymentDomain) {
    deploymentDomain.textContent = p.domain || connLabel(p);
    deploymentDomain.href = p.url || '#';
    deploymentDomain.toggleAttribute('aria-disabled', !p.url);
  }
  const deploymentSource = document.getElementById('svc-deploy-source');
  if (deploymentSource) deploymentSource.textContent = p.git_url ? p.git_url.replace(/^https:\/\/github\.com\//, '').replace(/\.git$/, '') : 'Manual source';
  document.querySelectorAll('[data-svc-open]').forEach((button) => { button.onclick = () => switchSvcTab(button.dataset.svcOpen || 'logs'); });
  const deployNow = document.getElementById('svc-deploy-now');
  if (deployNow) deployNow.onclick = () => serviceDeploy(p.id);
  const editDomain = document.getElementById('svc-domain-edit');
  if (editDomain) editDomain.onclick = () => openServiceEditModal(p);
  renderDeploymentSitePreview(p);
  renderServiceManagementWorkspaces(p);

  if (activeSvcTab === 'general') {
    renderQuickActions(p);
    renderStackBadge(p);
    void loadServiceHealth(p.id);
    void loadDeploymentHistory(p.id);
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
  const modal = projectEditField('svc-edit-modal');
  const id = modal?.dataset.projectId;
  if (!id) return;
  const value = id => projectEditField(id)?.value.trim() || '';
  const name = value('svc-edit-name-input');
  let domain = value('svc-edit-domain-input');
  domain = domain.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  if (!name) return toast('Name is required');
  const saveButton = projectEditField('svc-edit-save-btn');
  const state = projectEditField('svc-edit-save-state');
  if (saveButton) saveButton.disabled = true;
  if (state) state.textContent = 'Saving project configuration…';
  try {
    await api(`/projects/${encodeURIComponent(id)}`, {method: 'PUT', body: JSON.stringify({name})});
    await api(`/projects/${encodeURIComponent(id)}/deployment-config`, {
      method: 'PUT',
      body: JSON.stringify({
        branch: value('svc-edit-branch') || 'main',
        start_command: value('svc-edit-start-command'),
        deploy_type: value('svc-edit-deploy-type') || 'auto',
        dockerfile_path: value('svc-edit-dockerfile-path'),
        docker_image: value('svc-edit-docker-image'),
        compose_file: value('svc-edit-compose-file'),
        healthcheck_path: value('svc-edit-healthcheck-path') || '/',
        healthcheck_interval: Number(value('svc-edit-healthcheck-interval')) || null,
        auto_deploy: Boolean(projectEditField('svc-edit-auto-deploy')?.checked),
        resource_memory: value('svc-edit-resource-memory'),
        resource_cpus: value('svc-edit-resource-cpus'),
      }),
    });
    if (domain) {
      const email = (await api('/settings')).admin_email;
      await api(`/projects/${encodeURIComponent(id)}/domain`, {method: 'POST', body: JSON.stringify({domain, email: email || 'admin@localhost'})});
    }
    if (state) state.textContent = 'Saved. Runtime limits and build settings apply on the next deployment.';
    toast('Project configuration saved');
    await loadProjects();
    const p = projects.find(item => item.id === id);
    if (p) { renderServiceDashboard(p, false); setBreadcrumb(displayTitle(p)); }
  } catch (error) {
    const message = normalizeFetchError(error?.message) || 'Could not save project configuration.';
    if (state) state.textContent = message;
    toast(message);
  } finally {
    if (saveButton) saveButton.disabled = false;
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

async function importProjectSource() {
  const name = document.getElementById('create-name')?.value.trim();
  if (!name) throw new Error('Enter a project name');
  const baseDirectory = (document.getElementById(projectImportSource === 'git' ? 'create-base-directory' : 'create-zip-base-directory')?.value || '/').trim() || '/';
  const inAppNotifications = Boolean(document.getElementById('create-in-app-notifications')?.checked);
  if (projectImportSource === 'git') {
    const gitUrl = document.getElementById('create-git-url')?.value.trim();
    const branch = (githubSourceSelection ? document.getElementById('github-branch-select')?.value : document.getElementById('create-branch')?.value)?.trim() || 'main';
    if (githubSourceSelection) {
      if (!document.getElementById('github-branch-select')?.value) throw new Error('Choose a branch for the connected GitHub repository');
      return api('/projects/import/github', { method: 'POST', body: JSON.stringify({ name, repository: githubSourceSelection.full_name, branch, base_directory: baseDirectory, in_app_notifications: inAppNotifications }) });
    }
    if (!gitUrl) throw new Error('Enter a repository URL or choose a connected GitHub repository');
    return api('/projects/import/repository', { method: 'POST', body: JSON.stringify({ name, git_url: gitUrl, branch, base_directory: baseDirectory, in_app_notifications: inAppNotifications }) });
  }
  const archive = document.getElementById('create-source-zip')?.files?.[0];
  if (!archive) throw new Error('Choose a ZIP archive');
  const form = new FormData();
  form.set('name', name); form.set('base_directory', baseDirectory); form.set('in_app_notifications', String(inAppNotifications)); form.set('archive', archive);
  return api('/projects/import/zip', { method: 'POST', body: form });
}

async function reanalyzeImportedProject() {
  if (!importedProjectId) return;
  const baseDirectory = (document.getElementById(projectImportSource === 'git' ? 'create-base-directory' : 'create-zip-base-directory')?.value || '/').trim() || '/';
  const result = await api(`/projects/${importedProjectId}/analyze`, { method: 'POST', body: JSON.stringify({ base_directory: baseDirectory }) });
  renderProjectAnalysis(result.analysis);
  toast('Build plan refreshed');
}

async function deployImportedProject() {
  if (!importedProjectId || !importedProjectAnalysis) throw new Error('Import a source before deployment');
  const baseDirectory = (document.getElementById(projectImportSource === 'git' ? 'create-base-directory' : 'create-zip-base-directory')?.value || '/').trim() || '/';
  const envVars = parseEnv(document.getElementById('create-env-vars')?.value || '');
  const startCommand = document.getElementById('create-start-cmd')?.value.trim() || null;
  const result = await api(`/projects/${importedProjectId}/deploy-detected`, {
    method: 'POST', body: JSON.stringify({ base_directory: baseDirectory, env_vars: envVars, start_command: startCommand, in_app_notifications: Boolean(document.getElementById('create-in-app-notifications')?.checked) }),
  });
  return result;
}

document.getElementById('create-form')?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const logPanel = document.getElementById('deploy-log-panel');
  const placeholder = document.getElementById('create-log-placeholder');
  placeholder?.classList.add('hidden'); logPanel?.classList.remove('hidden'); clearLogPanel(logPanel);
  try {
    if (!importedProjectId) {
      setProjectDeployButton('Importing source…', 'loader-circle', true);
      const result = await importProjectSource();
      importedProjectId = result.project.id;
      appendLogLine(logPanel, result.message || 'Source imported', 'log-ok');
      renderProjectAnalysis(result.analysis);
      toast(`Detected ${result.analysis.framework || result.analysis.language || 'project'} application`);
      return;
    }
    setProjectDeployButton('Deploying…', 'loader-circle', true);
    const result = await deployImportedProject();
    appendLogLine(logPanel, result.message || 'Deployment queued', 'log-info');
    toast('Deployment queued');
    await loadProjects();
    openService(importedProjectId);
    switchSvcTab('logs');
    const logs = document.getElementById('svc-live-logs');
    loadLogSnapshot(importedProjectId, logs).then(() => startLogStream(importedProjectId, logs, { liveOnly: true, clearFirst: false }));
  } catch (error) {
    appendLogLine(logPanel, 'Error: ' + error.message, 'log-err');
    toast(error.message);
    if (!importedProjectId) setProjectDeployButton('Analyze source');
    else setProjectDeployButton('Deploy project', 'rocket');
  }
});

document.querySelectorAll('[data-import-source]').forEach(tab => tab.addEventListener('click', () => {
  if (importedProjectId) return toast('Reset this draft before changing its source type.');
  setProjectImportSource(tab.dataset.importSource);
}));

document.getElementById('github-connect-btn')?.addEventListener('click', () => {
  const popup = window.open('', 'syte-github-connect', 'popup=yes,width=600,height=720');
  if (!popup) return toast('Allow pop-ups for this site to connect GitHub.');
  popup.document.title = 'Connecting GitHub…';
  void connectGithubSource(popup);
});

document.getElementById('github-disconnect-btn')?.addEventListener('click', () => void disconnectGithubSource());
document.getElementById('github-repositories-refresh')?.addEventListener('click', () => void loadGithubRepositories());
document.getElementById('github-repository-search')?.addEventListener('input', renderGithubRepositories);
document.getElementById('github-repository-list')?.addEventListener('click', (event) => {
  const button = event.target.closest('[data-github-repository]');
  if (button) void selectGithubRepository(button.dataset.githubRepository);
});
document.getElementById('github-branch-select')?.addEventListener('change', (event) => {
  const branch = event.target.value;
  const manualBranch = document.getElementById('create-branch');
  if (manualBranch) manualBranch.value = branch;
});
document.getElementById('create-git-url')?.addEventListener('input', () => {
  if (githubSourceSelection && document.getElementById('create-git-url')?.value !== githubSourceSelection.clone_url) resetGithubSourceSelection();
});
window.addEventListener('message', (event) => {
  if (event.origin !== window.location.origin || event.data?.type !== 'syte-github-oauth') return;
  if (event.data.ok) {
    toast(`GitHub connected${event.data.login ? ` as ${event.data.login}` : ''}`);
    void loadGithubSourceStatus();
  } else if (event.data.message) {
    toast(event.data.message);
  }
});

document.getElementById('reanalyze-source')?.addEventListener('click', async () => {
  try { await reanalyzeImportedProject(); } catch (error) { toast('Analysis failed: ' + error.message); }
});

document.getElementById('deploy-env-suggestions')?.addEventListener('click', event => {
  const button = event.target.closest('[data-env-suggestion]');
  if (button) appendSuggestedEnvironment(button.dataset.envSuggestion);
});

document.getElementById('create-name-focus')?.addEventListener('click', () => {
  document.getElementById('create-name')?.focus();
});

document.getElementById('check-nine-router-tls-btn')?.addEventListener('click', checkNineRouterLocalTls);

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

function renderNineRouterLocalTlsStatus(status) {
  const badge = document.getElementById('nine-router-local-tls-badge');
  const detail = document.getElementById('nine-router-local-tls-detail');
  if (!badge || !detail) return;
  const state = status?.state || 'unknown';
  const labels = {
    serving: ['badge-ssl-https', 'serving'],
    'invalid-cert': ['badge-ssl-http', 'invalid cert'],
    'cert-error': ['badge-ssl-http', 'TLS error'],
    'caddy-down': ['badge-ssl-http', 'Caddy not serving'],
    'bad-response': ['badge-ssl-preview-pending', 'bad response'],
    malformed: ['badge-ssl-http', 'invalid host'],
  };
  const [className, label] = labels[state] || ['badge-ssl-preview-pending', state];
  badge.innerHTML = `<span class="badge badge-ssl ${className}">${esc(label)}</span>`;
  detail.textContent = status?.detail || 'No local TLS probe result.';
}

async function checkNineRouterLocalTls() {
  const button = document.getElementById('check-nine-router-tls-btn');
  const detail = document.getElementById('nine-router-local-tls-detail');
  if (button) button.disabled = true;
  if (detail) detail.textContent = 'Checking 127.0.0.1:20128…';
  try {
    const status = await api('/settings/9router-tls');
    renderNineRouterLocalTlsStatus(status);
  } catch (e) {
    renderNineRouterLocalTlsStatus({ state: 'caddy-down', detail: `Local TLS check failed: ${e.message}` });
  } finally {
    if (button) button.disabled = false;
  }
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
    const githubRepo = document.getElementById('github-repo');
    const githubToken = document.getElementById('github-token');
    const githubTokenStatus = document.getElementById('github-token-status');
    if (githubRepo) githubRepo.value = s.github_repo || '';
    if (githubToken) githubToken.placeholder = s.github_token_set
      ? 'token saved — enter new value to replace'
      : 'personal access token (optional for public read access)';
    if (githubTokenStatus) githubTokenStatus.textContent = s.github_token_set
      ? `Token configured via ${s.github_token_source || 'settings'}. It is never shown here.`
      : 'Token is not configured. Public repositories can still be read with GitHub rate limits.';
    const githubTrackingCard = document.getElementById('github-tracking-card');
    const githubCredentialsLoaded = Boolean(
      s.github_token_set || (s.github_token_source && s.github_token_source !== 'none')
    );
    if (githubTrackingCard) githubTrackingCard.classList.toggle('hidden', githubCredentialsLoaded);

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
    renderNineRouterLocalTlsStatus(s.nine_router_local_tls);
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
    const globalDefaultModel = document.getElementById('global-ai-default-model');
    if (globalDefaultModel && [...globalDefaultModel.options].some((option) => option.value === defaultProfile)) {
      globalDefaultModel.value = defaultProfile;
    }
    syncGlobalAiModelSelection();
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
    const commits = Array.isArray(info.recent_mergeable_commits)
      ? info.recent_mergeable_commits.slice(0, 3)
      : [];
    const commitsEl = document.getElementById('syte-mergeable-commits');
    if (commitsEl) {
      if (!commits.length) {
        commitsEl.innerHTML = '';
        commitsEl.classList.add('hidden');
      } else {
        commitsEl.innerHTML = `
          <span class="update-mergeable-label">mergeable commits</span>
          ${commits.map((commit) => `
            <div class="update-commit-row">
              <a href="${esc(commit.commit_url || commit.pr_url || '#')}" target="_blank" rel="noopener" class="update-commit-sha">${esc(commit.sha || 'commit')}</a>
              <span class="update-commit-message">${esc(commit.message || '')}</span>
              ${commit.pr_number ? `<a href="${esc(commit.pr_url || '#')}" target="_blank" rel="noopener" class="update-commit-pr">PR #${esc(String(commit.pr_number))}</a>` : ''}
            </div>
          `).join('')}
        `;
        commitsEl.classList.remove('hidden');
      }
    }
  } catch {
    el.textContent = 'Will pull latest open GitHub PR (fallback: main)';
    document.getElementById('syte-mergeable-commits')?.classList.add('hidden');
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
    list.innerHTML = '<p class="hint">Protected API access is required to manage API keys.</p>';
    return;
  }
  try {
    const res = await api('/tokens');
    if (!res.tokens?.length) {
      list.innerHTML = '<p class="hint">no tokens yet</p>';
      return;
    }
    list.innerHTML = res.tokens.map(t => {
      const scopes = Array.isArray(t.scopes) ? t.scopes : [];
      const expiry = t.expires_at ? new Date(t.expires_at).toLocaleString() : 'No expiry';
      return `<article class="api-key-row"><div><strong>${esc(t.name)}</strong><code>${esc(t.prefix)}…</code><span>Expires: ${esc(expiry)} · ${esc(String(t.rate_limit_per_minute || 60))} req/min</span><small>${scopes.map(scope => `<em>${esc(scope)}</em>`).join('')}</small></div><button class="btn-pill btn-ghost btn-sm" onclick="revokeToken('${t.id}')">revoke</button></article>`;
    }).join('');
    refreshIcons();
  } catch {
    list.innerHTML = '<p class="hint">could not load tokens</p>';
  }
}

async function revokeToken(id) {
  if (!confirm('Revoke this API token?')) return;
  if (!await restoreOperatorSession()) {
    return toast('Protected API access is required to manage API keys');
  }
  try {
    await api(`/tokens/${id}`, { method: 'DELETE' });
    toast('token revoked');
    await loadTokens();
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

document.getElementById('token-rate-limit')?.addEventListener('input', event => {
  const output = document.getElementById('token-rate-output');
  if (output) output.textContent = `${event.currentTarget.value} requests/min`;
});

document.getElementById('create-token-btn')?.addEventListener('click', async () => {
  const name = document.getElementById('token-name')?.value || 'default';
  const expiresInput = document.getElementById('token-expires-at')?.value || '';
  const scopes = [...document.querySelectorAll('input[name="token-scope"]:checked')].map(input => input.value);
  const rateLimit = Number(document.getElementById('token-rate-limit')?.value || 60);
  if (!scopes.length) return toast('Choose at least one API permission.');
  if (!await restoreOperatorSession()) {
    return toast('Protected API access is required to manage API keys');
  }
  try {
    const expiresAt = expiresInput ? new Date(expiresInput).toISOString() : null;
    const res = await api('/tokens', { method: 'POST', body: JSON.stringify({ name, expires_at: expiresAt, scopes, rate_limit_per_minute: rateLimit }) });
    const box = document.getElementById('new-token-box');
    box.textContent = `Token (copy for external API use — not needed for the web GUI):\n${res.token}`;
    box.classList.remove('hidden');
    toast('token created — copy it now');
    await loadTokens();
  } catch (e) {
    toast('Error: ' + e.message);
  }
});

document.getElementById('topbar-git-profile')?.addEventListener('click', () => {
  activePlatformPage = 'git';
  showView('platform');
});

loadSystem();
loadProjects();
loadSettings();
loadTokens();
void loadGithubSourceStatus();
appContext = getContext();
applyContext();
startStatsPoll();
setupCrashScreenHandlers();
void registerSycordPwa();
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
    return;
  }
  console.error('[Syte] Uncaught error:', event.error || msg);
  showCrashScreen({
    title: 'Application Crash',
    subtitle: 'An uncaught runtime error occurred.',
    message: msg,
    details: event?.error?.stack || (event?.filename ? `${event.filename}:${event.lineno}:${event.colno}` : '')
  });
});

window.addEventListener('unhandledrejection', (event) => {
  const reason = event?.reason;
  const rawMsg = String(reason?.message || reason?.name || reason || '');
  const isLoadFailed = !rawMsg || rawMsg === 'TypeError' || /^typeerror/i.test(rawMsg) || /load failed/i.test(rawMsg) || /failed to fetch/i.test(rawMsg) || /networkerror/i.test(rawMsg);

  if (isLoadFailed) {
    highLoadNetworkErrorCount++;
    console.warn('[Syte] Handled network/load rejection:', rawMsg || 'Load failed');
    if (event && typeof event.preventDefault === 'function') {
      event.preventDefault();
    }
    if (highLoadNetworkErrorCount >= 4) {
      showCrashScreen({
        title: 'High Load / Connection Loss',
        subtitle: 'The server is unreachable or failing under load.',
        message: 'Could not reach the Syte server. Server may be down or experiencing high load.',
        details: reason?.stack || String(reason || 'Load failed')
      });
    }
    return;
  }

  console.error('[Syte] Unhandled promise rejection:', reason);
  if (event && typeof event.preventDefault === 'function') {
    event.preventDefault();
  }
  showCrashScreen({
    title: 'Unhandled Async Error',
    subtitle: 'An unexpected async error occurred in the application.',
    message: reason?.message || String(reason || 'Unhandled Promise Rejection'),
    details: reason?.stack || ''
  });
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

document.querySelectorAll('[data-settings-tab]').forEach((button) => {
  button.addEventListener('click', () => setSettingsMiniTab(button.dataset.settingsTab));
});
document.getElementById('save-github-settings-btn')?.addEventListener('click', async () => {
  const repo = document.getElementById('github-repo')?.value.trim() || '';
  const token = document.getElementById('github-token')?.value.trim() || '';
  if (!await operatorAuthenticated()) {
    showLoginScreen('settings');
    return;
  }
  const button = document.getElementById('save-github-settings-btn');
  if (button) { button.disabled = true; button.textContent = 'saving…'; }
  try {
    const body = { repo };
    if (token) body.token = token;
    const result = await api('/settings/github', { method: 'PUT', body: JSON.stringify(body) });
    toast((result.messages || []).join(' ') || 'Git settings saved');
    if (token) document.getElementById('github-token').value = '';
    await loadSettings();
    await loadGitTracking();
  } catch (error) {
    toast(`Git settings failed: ${error.message}`);
  } finally {
    if (button) { button.disabled = false; button.textContent = 'Save Git settings'; }
  }
});
document.getElementById('refresh-github-tracking-btn')?.addEventListener('click', () => loadGitTracking());
restoreSettingsMiniTab();

document.getElementById('project-filter')?.addEventListener('input', (e) => {
  projectFilterText = e.target.value;
  renderServices();
});

document.querySelectorAll('.nav-sublink[data-view]').forEach(el => {
  if (el.tagName === 'A' && !el.dataset.platformPage) return;
  el.addEventListener('click', (event) => {
    event.preventDefault();
    event.stopPropagation();
    if (el.dataset.platformPage) activePlatformPage = el.dataset.platformPage;
    showView(el.dataset.view);
  });
});
document.getElementById('platform-page-refresh')?.addEventListener('click', () => loadPlatformPage(activePlatformPage));
document.getElementById('overview-monitor-refresh')?.addEventListener('click', loadOverviewMonitor);
document.getElementById('platform-action-list')?.addEventListener('click', (event) => {
  if (event.target.closest('[data-action="refresh"]')) loadPlatformPage(activePlatformPage);
});
document.getElementById('nav-group-main-toggle')?.addEventListener('click', () => toggleNavGroup('nav-group-main'));
document.getElementById('nav-service-head')?.addEventListener('click', () => showView('dashboard'));
document.getElementById('sidebar-service-tabs')?.addEventListener('click', (event) => {
  const btn = event.target.closest('.nav-sublink[data-svc-tab]');
  if (!btn?.dataset.svcTab) return;
  event.preventDefault();
  event.stopPropagation();
  switchSvcTab(btn.dataset.svcTab);
});
document.getElementById('global-ai-project')?.addEventListener('change', async (event) => {
  const projectId = event.target.value;
  if (!projectId) {
    clearGlobalAiProject();
    return;
  }
  await setGlobalAiProject(projectId);
});
document.getElementById('global-ai-project-close')?.addEventListener('click', clearGlobalAiProject);
document.getElementById('global-ai-tab-chat')?.addEventListener('click', () => setGlobalAiTab('chat'));
document.getElementById('global-ai-tab-models')?.addEventListener('click', () => setGlobalAiTab('models'));
document.getElementById('global-ai-session-model')?.addEventListener('change', (event) => {
  const profile = event.target.value || 'auto';
  const chatProfile = document.getElementById('debug-chat-profile');
  if (chatProfile) {
    chatProfile.value = profile;
    chatProfile.dataset.userSelected = '1';
    chatProfile.dispatchEvent(new Event('change'));
  }
  syncGlobalAiModelSelection(profile);
});
document.getElementById('global-ai-save-default-model')?.addEventListener('click', saveGlobalAiDefaultModel);
document.getElementById('global-ai-open-model-manager')?.addEventListener('click', () => showView('models'));
document.querySelectorAll('.global-ai-provider-type').forEach((button) => button.addEventListener('click', () => setGlobalAiProviderType(button.dataset.providerType)));
document.getElementById('global-ai-save-provider')?.addEventListener('click', saveGlobalAiProvider);
document.getElementById('global-ai-refresh-providers')?.addEventListener('click', loadGlobalAiProviderCatalog);
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
  if (select) {
    select.dataset.userSelected = '1';
    syncGlobalAiModelSelection(select.value || 'auto');
  }
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
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      void sendDebugChatMessage();
    }
  });
  input.addEventListener('input', updateDebugChatControls);
  const contextToggle = document.getElementById('debug-chat-context-toggle');
  const contextSummary = document.getElementById('debug-chat-context-summary');
  contextToggle?.addEventListener('click', () => {
    const isOpen = !contextSummary?.classList.contains('hidden');
    contextSummary?.classList.toggle('hidden', isOpen);
    contextToggle.setAttribute('aria-expanded', isOpen ? 'false' : 'true');
  });
  for (const id of [
    'debug-chat-thinking-level', 'debug-chat-context-window', 'debug-chat-stream-limit',
    'debug-chat-memory-depth', 'debug-chat-plan-mode', 'debug-chat-agent-mode',
    'debug-chat-max-steps', 'debug-chat-deployment-readiness',
  ]) {
    document.getElementById(id)?.addEventListener('change', () => {
      updateDebugChatContextSummary();
      updateDebugChatControls();
    });
  }
  updateDebugChatContextSummary();
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

document.getElementById('svc-health-refresh')?.addEventListener('click', () => {
  if (activeServiceId) void loadServiceHealth(activeServiceId);
});
document.getElementById('svc-deployments-refresh')?.addEventListener('click', () => {
  if (activeServiceId) void loadDeploymentHistory(activeServiceId);
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
document.getElementById('svc-edit-close-btn')?.addEventListener('click', closeServiceEditModal);
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
// Managed 9Router deployment
// ---------------------------------------------------------------------------

let routerData = null;
let routerInitialPassword = '';
let routerPasswordRevealed = false;
let routerInstallDebug = '';

function renderRouterTab(data) {
  const content = document.getElementById('router-content');
  const startBtn = document.getElementById('router-start-btn');
  const stopBtn = document.getElementById('router-stop-btn');
  const restartBtn = document.getElementById('router-restart-btn');
  if (!content) return;
  routerData = data || {};
  const running = Boolean(routerData.running);
  const ready = Boolean(routerData.ready);
  const enabled = Boolean(routerData.enabled);
  const badgeClass = ready ? 'badge-running' : (running || enabled ? 'badge-warning' : 'badge-stopped');
  const badgeText = ready ? 'Ready' : (running ? 'Starting' : (enabled ? 'Configured · stopped' : 'Not deployed'));
  if (startBtn) startBtn.disabled = ready;
  if (stopBtn) stopBtn.disabled = !running;
  if (restartBtn) restartBtn.disabled = !running;

  const ssl = routerData.ssl || {};
  const sslState = ssl.state || 'unknown';
  const sslLabel = sslState === 'serving' ? (ssl.dedicated_cert === false ? 'Dedicated SSL missing' : 'HTTPS serving') : sslState === 'dedicated-cert-missing' ? 'Dedicated SSL missing' : sslState === 'invalid-cert' || sslState === 'cert-error' ? 'SSL error' : sslState === 'down' ? 'HTTPS not serving' : sslState;
  const sslClass = sslState === 'serving' && ssl.dedicated_cert !== false ? 'badge-running' : 'badge-warning';
  const initialDebug = routerInstallDebug || 'Load diagnostics to inspect AlmaLinux, Docker, Caddy, DNS, and container failures.';
  const warning = routerData.warning
    ? `<div class="note router-warning"><strong>Important:</strong> ${esc(routerData.warning)}</div>`
    : '';
  const suggestedDomain = routerData.suggested_gui_domain || '';
  const domainConflict = routerData.gui_domain_conflict
    ? `<div class="note router-warning">
        <strong>GUI domain conflict:</strong> Settings → GUI domain is empty or set to
        <code>${esc(routerData.public_host || '9router.sycord.site')}</code>, the same host 9Router needs
        to take over. Starting 9Router is blocked until a different GUI domain is configured
        (this is also the default set by "Start Syra").
        ${suggestedDomain ? `<button type="button" class="btn-pill btn-sm" id="router-fix-domain-btn" data-domain="${esc(suggestedDomain)}">Use ${esc(suggestedDomain)}</button>` : ''}
      </div>`
    : '';
  content.innerHTML = `
    ${warning}
    ${domainConflict}
    <div class="router-status-head"><span class="badge ${badgeClass}">${badgeText}</span><span class="hint">${esc(routerData.message || '')}</span></div>
    <div class="swarm-grid router-status-grid">
      <div class="swarm-stat"><span class="swarm-label">Public API</span><a class="swarm-value link" href="${esc(routerData.public_api_url || 'https://9router.sycord.site/v1')}" target="_blank" rel="noopener">${esc(routerData.public_api_url || 'https://9router.sycord.site/v1')}</a></div>
      <div class="swarm-stat"><span class="swarm-label">Web GUI</span><a class="swarm-value link" href="${esc(routerData.dashboard_url || 'https://9router.sycord.site/dashboard')}" target="_blank" rel="noopener">${esc(routerData.dashboard_url || 'https://9router.sycord.site/dashboard')}</a></div>
      <div class="swarm-stat"><span class="swarm-label">Public SSL</span><span class="swarm-value"><span class="badge ${sslClass}">${esc(sslLabel)}</span> <small>${esc(ssl.detail || 'No public HTTPS probe yet.')}</small></span></div>
      <div class="swarm-stat"><span class="swarm-label">Container</span><span class="swarm-value">${esc(routerData.container_id || '—')}</span></div>
      <div class="swarm-stat"><span class="swarm-label">Image</span><span class="swarm-value">${esc(routerData.image || 'decolua/9router:0.5.50')}</span></div>
      <div class="swarm-stat full"><span class="swarm-label">Data</span><span class="swarm-value"><code>/var/lib/syte/9router</code> · persistent across redeploys</span></div>
    </div>
    <p class="hint block router-help">The official 9Router web GUI is at <code>https://9router.sycord.site/dashboard</code> and its OpenAI-compatible API is at <code>/v1</code>. AlmaLinux prepares Docker, Caddy, firewalld, DNS, and HTTPS before deployment. The container listens on port <code>20128</code> internally and is bound to loopback host port <code>${esc(routerData.port || 20129)}</code>. A separate GUI domain is required so the Syte console remains reachable.</p>
    <details class="router-logs-details"><summary>Installation diagnostics</summary><div class="router-log-actions"><button type="button" class="btn-pill btn-ghost btn-sm" id="router-debug-refresh"><i data-lucide="refresh-cw"></i><span>Refresh diagnostics</span></button></div><pre id="router-install-debug" class="router-logs">${esc(initialDebug)}</pre></details>
    <details class="router-logs-details"><summary>Recent container logs</summary><div class="router-log-actions"><button type="button" class="btn-pill btn-ghost btn-sm" id="router-logs-refresh"><i data-lucide="refresh-cw"></i><span>Refresh logs</span></button></div><pre id="router-logs" class="router-logs">Load logs when needed.</pre></details>
  `;
  document.getElementById('router-debug-refresh')?.addEventListener('click', loadRouterDebug);
  document.getElementById('router-logs-refresh')?.addEventListener('click', loadRouterLogs);
  document.getElementById('router-fix-domain-btn')?.addEventListener('click', async (event) => {
    const btn = event.currentTarget;
    const domain = btn.dataset.domain;
    if (!domain) return;
    btn.disabled = true;
    btn.textContent = 'Applying…';
    try {
      const settings = await api('/settings');
      const email = settings.admin_email;
      if (!email || !email.includes('@') || email.endsWith('@localhost')) {
        toast('Set a valid admin email in Settings first, then retry.');
        return;
      }
      const res = await api('/settings', {
        method: 'PUT',
        body: JSON.stringify({ gui_domain: domain, admin_email: email }),
      });
      toast(Array.isArray(res.messages) ? res.messages.join(' ') : `GUI domain set to ${domain}`);
      await loadRouterTab();
    } catch (error) {
      toast(`Could not set GUI domain: ${error.message}`);
    } finally {
      btn.disabled = false;
      btn.textContent = `Use ${domain}`;
    }
  });
  refreshIcons();
}

async function loadRouterPassword() {
  const passwordEl = document.getElementById('router-initial-password');
  const statusEl = document.getElementById('router-password-status');
  const revealBtn = document.getElementById('router-password-reveal-btn');
  if (!passwordEl || !statusEl) return;
  try {
    const result = await api('/settings/router/password');
    routerInitialPassword = result.password || '';
    passwordEl.dataset.password = routerInitialPassword;
    passwordEl.textContent = routerPasswordRevealed && routerInitialPassword
      ? routerInitialPassword
      : (routerInitialPassword ? '••••••••••••' : 'Not created yet');
    statusEl.textContent = routerInitialPassword
      ? (result.is_new ? 'Newly created credential' : 'Saved operator credential')
      : 'Deploy 9Router to create its credential.';
    if (revealBtn) {
      revealBtn.disabled = !routerInitialPassword;
      revealBtn.setAttribute('aria-label', routerPasswordRevealed ? 'Hide initial password' : 'Reveal initial password');
      revealBtn.title = routerPasswordRevealed ? 'Hide password' : 'Reveal password';
      revealBtn.innerHTML = `<i data-lucide="${routerPasswordRevealed ? 'eye-off' : 'eye'}"></i>`;
    }
  } catch (error) {
    passwordEl.dataset.password = '';
    passwordEl.textContent = 'Unavailable';
    statusEl.textContent = isAuthError(error) ? 'Sign in to view this credential.' : 'Could not load credential.';
    if (revealBtn) revealBtn.disabled = true;
  }
  refreshIcons();
}

function toggleRouterPassword() {
  if (!routerInitialPassword) return;
  routerPasswordRevealed = !routerPasswordRevealed;
  const passwordEl = document.getElementById('router-initial-password');
  const revealBtn = document.getElementById('router-password-reveal-btn');
  if (passwordEl) passwordEl.textContent = routerPasswordRevealed ? routerInitialPassword : '••••••••••••';
  if (revealBtn) {
    revealBtn.setAttribute('aria-label', routerPasswordRevealed ? 'Hide initial password' : 'Reveal initial password');
    revealBtn.title = routerPasswordRevealed ? 'Hide password' : 'Reveal password';
    revealBtn.innerHTML = `<i data-lucide="${routerPasswordRevealed ? 'eye-off' : 'eye'}"></i>`;
  }
  refreshIcons();
}

async function copyRouterPassword() {
  if (!routerInitialPassword) return;
  try {
    await navigator.clipboard.writeText(routerInitialPassword);
    toast('9Router initial password copied');
  } catch (error) {
    toast('Could not copy the password. Reveal it and copy manually.');
  }
}

async function loadRouterTab() {
  const content = document.getElementById('router-content');
  if (!content) return;
  if (!(await forceLoginForView('router'))) return;
  content.innerHTML = '<p class="hint block">Loading 9Router status…</p>';
  try {
    const [status] = await Promise.all([api('/settings/router/status'), loadRouterPassword()]);
    renderRouterTab(status);
  } catch (error) {
    if (isAuthError(error)) {
      showLoginScreen('router');
      return;
    }
    content.innerHTML = `<p class="hint block">Could not load 9Router status: ${esc(error.message)}</p>`;
  }
}

async function routerAction(action) {
  if (!(await forceLoginForView('router'))) return;
  const button = document.getElementById(`router-${action}-btn`);
  if (button) button.disabled = true;
  try {
    const result = await api(`/settings/router/${action}`, { method: 'POST' });
    if (result.initial_password) routerInitialPassword = result.initial_password;
    if (result.host_setup || result.proxy_message || result.message) {
      routerInstallDebug = [
        result.host_setup?.message,
        ...(Array.isArray(result.host_setup?.steps) ? result.host_setup.steps : []),
        result.message,
        result.proxy_message,
      ].filter(Boolean).join('\n');
    }
    toast(result.message || `9Router ${action} complete`);
    await loadRouterTab();
  } catch (error) {
    if (isAuthError(error)) {
      showLoginScreen('router');
      return;
    }
    toast(`9Router ${action} failed: ${error.message}`);
    await loadRouterTab();
  }
}

async function loadRouterDebug() {
  const debug = document.getElementById('router-install-debug');
  if (!debug) return;
  debug.textContent = 'Loading diagnostics…';
  try {
    const result = await api('/settings/router/debug');
    const sections = [
      result.installation_log ? `=== INSTALLATION / HOST / CADDY ===\n${result.installation_log}` : '',
      result.container_logs ? `=== CONTAINER LOGS ===\n${result.container_logs}` : '',
      result.container_log_error ? `=== CONTAINER LOG ERROR ===\n${result.container_log_error}` : '',
    ].filter(Boolean);
    routerInstallDebug = sections.join('\n\n') || 'No Router installation diagnostics have been recorded yet.';
    debug.textContent = routerInstallDebug;
  } catch (error) {
    debug.textContent = `Could not load installation diagnostics: ${error.message}`;
  }
}

async function loadRouterLogs() {
  const logs = document.getElementById('router-logs');
  if (!logs) return;
  logs.textContent = 'Loading…';
  try {
    const result = await api('/settings/router/logs?lines=120');
    logs.textContent = result.logs || 'No logs available.';
  } catch (error) {
    logs.textContent = `Could not load logs: ${error.message}`;
  }
}

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
  // Navigation and read-only UI are not blocked by an unlock screen. Protected
  // actions continue through the API boundary and surface an ordinary error.
  return true;
}

async function unlockSyra() {
  if (window.location.protocol !== 'https:') {
    return toast('Operator session requires HTTPS. Open the configured GUI domain.');
  }
  const input = document.getElementById('syra-bootstrap-key');
  const button = document.getElementById('syra-unlock-btn');
  const bootstrapToken = input?.value.trim() || '';
  if (!bootstrapToken) return toast('Enter the operator credential');
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
    toast('Operator session enabled for this browser');
    await initSyraTab();
  } catch (e) {
      toast('Operator session failed: ' + e.message);
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
      if (statusLabel) statusLabel.textContent = 'Authentication available for protected actions';
      if (publicStatus) {
        publicStatus.classList.remove('is-ready');
        publicStatus.classList.add('is-pending');
        publicStatus.textContent = 'Protected actions require an operator API session.';
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

document.getElementById('router-refresh-btn')?.addEventListener('click', loadRouterTab);
document.getElementById('router-password-reveal-btn')?.addEventListener('click', toggleRouterPassword);
document.getElementById('router-password-copy-btn')?.addEventListener('click', copyRouterPassword);
document.getElementById('router-start-btn')?.addEventListener('click', () => routerAction('start'));
document.getElementById('router-stop-btn')?.addEventListener('click', () => routerAction('stop'));
document.getElementById('router-restart-btn')?.addEventListener('click', () => routerAction('restart'));

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


document.querySelectorAll('.nav-placeholder[data-nav-placeholder]').forEach((item) => {
  item.addEventListener('click', () => {
    const label = item.dataset.navPlaceholder || 'This section';
    toast(`${label} is available in the navigation and is being wired to the platform API.`);
  });
});


let syteAccount = null;
const SYTE_ACCOUNT_ICON = {user: 'user-round', sparkles: 'sparkles', shield: 'shield-check', rocket: 'rocket', leaf: 'leaf', heart: 'heart', camera: 'camera'};

function renderLegacyAccountCorner(account) {
  const button = document.getElementById('legacy-account-corner');
  if (!button || !account) return;
  const icon = SYTE_ACCOUNT_ICON[account.avatar_icon] || 'user-round';
  button.innerHTML = `<span class="legacy-account-icon"><i data-lucide="${icon}"></i></span><span>${esc(account.display_name || account.email)}</span>`;
  button.classList.remove('hidden');
  button.onclick = async () => { showView('platform'); await loadPlatformPage('profile'); };
  refreshIcons();
}

function showLegacyAccountApp(account) {
  syteAccount = account;
  document.body.classList.remove('account-auth-pending');
  document.getElementById('account-login-screen')?.classList.add('hidden');
  renderLegacyAccountCorner(account);
  // Project-source status may have been fetched before the HttpOnly account
  // session existed. Refresh it after authentication so Connect GitHub does
  // not remain disabled with a stale unauthenticated response.
  void loadGithubSourceStatus();
}

function legacyAccountLoginMarkup(setup) {
  const title = setup ? 'Create an account' : 'Welcome back';
  const description = setup
    ? 'Create your Sycord workspace account to continue.'
    : 'Enter your details to access your workspace.';
  const switcher = setup
    ? '<p class="account-auth-switch">Already have an account? <button id="legacy-account-login-switch" type="button">Log in</button></p>'
    : '<p class="account-auth-switch">First time here? <button id="legacy-account-setup-switch" type="button">Create an account</button></p>';
  return `<div class="account-auth-layout">
    <aside class="account-auth-aside" aria-label="Sycord introduction">
      <a class="account-auth-brand" href="/" aria-label="Sycord home"><img src="/static/syte-logo.png?v=__VERSION__" alt=""><span>Sycord</span></a>
      <blockquote class="account-auth-quote">“A focused workspace for shipping projects with confidence.”<cite>— Sycord</cite></blockquote>
    </aside>
    <main class="account-auth-main">
      <div class="account-auth-topbar">
        <a class="account-auth-mobile-brand" href="/" aria-label="Sycord home"><img src="/static/syte-logo.png?v=__VERSION__" alt=""><span>Sycord</span></a>
        ${switcher}
      </div>
      <section class="account-login-card" aria-labelledby="account-auth-title">
        <div class="account-login-icon" aria-hidden="true"><i data-lucide="${setup ? 'user-round-plus' : 'lock-keyhole'}"></i></div>
        <h1 id="account-auth-title">${title}</h1>
        <p class="account-auth-description">${description}</p>
        <form id="legacy-account-login-form" class="account-login-form">
          ${setup ? '<label for="legacy-account-name">Name</label><input id="legacy-account-name" maxlength="120" autocomplete="name" placeholder="Your name" required>' : ''}
          <label for="legacy-account-email">Email address</label>
          <input id="legacy-account-email" type="email" autocomplete="email" placeholder="name@example.com" required>
          <label for="legacy-account-password">Password</label>
          <input id="legacy-account-password" type="password" autocomplete="${setup ? 'new-password' : 'current-password'}" minlength="${setup ? 12 : 1}" placeholder="${setup ? 'At least 12 characters' : 'Enter your password'}" required>
          <button class="account-auth-submit" type="submit">${setup ? 'Create account' : 'Sign in'}</button>
        </form>
        <div class="account-auth-divider" aria-hidden="true"><span></span><small>SECURE ACCESS</small><span></span></div>
        <p class="account-auth-terms">By continuing, you agree to the workspace access policy and responsible-use terms.</p>
        <p id="legacy-account-login-error" class="account-login-error" role="alert"></p>
      </section>
    </main>
  </div>`;
}

async function initializeLegacyAccountGate() {
  const screen = document.getElementById('account-login-screen');
  if (!screen) return;
  try {
    const session = await api('/auth/session');
    if (session.authenticated && session.account) {
      syraCsrfToken = session.csrf_token || '';
      setSyraSessionState(true);
      showLegacyAccountApp(session.account);
      return;
    }
    const setupState = await api('/auth/setup');
    let setup = Boolean(setupState.needs_first_account);
    const render = () => {
      screen.innerHTML = legacyAccountLoginMarkup(setup);
      screen.querySelector('#legacy-account-login-form')?.addEventListener('submit', async (event) => {
        event.preventDefault();
        const error = screen.querySelector('#legacy-account-login-error');
        if (error) error.textContent = '';
        const email = screen.querySelector('#legacy-account-email')?.value || '';
        const password = screen.querySelector('#legacy-account-password')?.value || '';
        const displayName = screen.querySelector('#legacy-account-name')?.value || '';
        try {
          const result = await api(setup ? '/auth/setup' : '/auth/login', {method: 'POST', body: JSON.stringify(setup ? {email, password, display_name: displayName} : {email, password})});
          syraCsrfToken = result.csrf_token || '';
          setSyraSessionState(true);
          showLegacyAccountApp(result.account);
        } catch (err) { if (error) error.textContent = err.message; }
      });
      screen.querySelector('#legacy-account-setup-switch')?.addEventListener('click', () => { setup = true; render(); });
      screen.querySelector('#legacy-account-login-switch')?.addEventListener('click', () => { setup = false; render(); });
      refreshIcons();
    };
    render();
  } catch (error) {
    screen.innerHTML = `<div class="account-login-card"><div class="account-login-icon"><i data-lucide="triangle-alert"></i></div><p class="account-login-kicker">Syte secure workspace</p><h1>Unable to start sign in</h1><p class="account-login-error">${esc(error.message)}</p></div>`;
    refreshIcons();
  }
}

document.addEventListener('DOMContentLoaded', initializeLegacyAccountGate);


async function renderCertificateWorkspace() {
  const target = document.getElementById('platform-dedicated-page');
  if (!target) return;
  target.innerHTML = '<section class="certificate-workspace-loading">Loading certificate readiness…</section>';
  try {
    const data = await api('/ssl');
    sslData = data;
    const projects = data.projects || [];
    const projectRows = projects.length
      ? projects.map(project => {
        const production = project.production || {};
        const label = production.badge_label || project.badge_label || (production.domain ? 'pending' : 'not configured');
        return `<article class="certificate-project-state"><div><strong>${esc(project.name || project.id)}</strong><span>${esc(production.domain || 'No production domain')}</span></div><em>${esc(label)}</em></article>`;
      }).join('')
      : '<p class="certificate-empty">Add a project and domain to begin certificate issuance.</p>';
    target.innerHTML = `<section class="certificate-workspace" aria-label="Certificate management">
      <header class="certificate-workspace-header"><div><p>Certification</p><h2>Domains and automatic TLS</h2><span>Check DNS before issuing. Normal certificates require a direct record; wildcards use Cloudflare DNS-01.</span></div><div class="certificate-workspace-provider"><img src="/static/vendor/cloudflare-svgl.svg?v=__VERSION__" alt="Cloudflare"><span>Cloudflare</span></div></header>
      ${certificateIssuanceHtml(data)}
      <section class="certificate-project-statuses" aria-label="Project certificate status"><div class="certificate-status-heading"><h3>Project certificate status</h3><button type="button" class="btn-pill btn-ghost btn-sm" data-certificate-refresh><i data-lucide="refresh-cw"></i><span>Refresh</span></button></div>${projectRows}</section>
    </section>`;
    wireCertificateIssuance();
    target.querySelector('[data-certificate-refresh]')?.addEventListener('click', () => loadPlatformPage('certificates'));
    refreshIcons();
  } catch (error) {
    target.innerHTML = `<section class="certificate-workspace-error">Could not load certificate readiness: ${esc(normalizeFetchError(error?.message) || 'unknown error')}</section>`;
  }
}

function certificateIssuanceHtml(data) {
  const projects = data.projects || [];
  const projectOptions = projects.map(project => `<option value="${esc(project.id)}">${esc(project.name || project.id)}</option>`).join('');
  return `<section class="certificate-issuance" aria-label="Certificate issuance">
    <header><div><p>Certificate issue</p><h3>Issue a domain certificate</h3><span>Use a direct DNS-only record for normal domains. Wildcards use Cloudflare DNS-01 after a Cloudflare API token is configured.</span></div><div class="certificate-provider"><img src="/static/vendor/cloudflare-svgl.svg?v=__VERSION__" alt="Cloudflare"><span>Cloudflare DNS</span></div></header>
    <form data-certificate-issue="1"><label>Project<select name="project_id" required>${projectOptions || '<option value="">No project available</option>'}</select></label><label>Domain<input name="domain" required placeholder="app.example.com" autocomplete="off"></label><label class="certificate-wildcard"><input type="checkbox" name="wildcard"><span>Issue wildcard DNS-01 certificate</span></label><button type="submit" ${projectOptions ? '' : 'disabled'}><i data-lucide="shield-check"></i><span>Request certificate</span></button></form>
    <div class="certificate-dns-guide" data-certificate-guide="1"><p>Enter a domain to inspect DNS readiness and record guidance.</p></div>
  </section>`;
}

function certificateRecordLine(record) {
  if (!record) return '';
  const text = `${record.type}  ${record.name}  ${record.value}`;
  return `<div class="certificate-record"><div><strong>${esc(record.type)}</strong><code>${esc(record.name)}</code><span>${esc(record.value)}</span><small>${esc(record.proxy || 'DNS only')}</small></div><button type="button" data-copy-certificate-record="${esc(text)}"><i data-lucide="copy"></i><span>Copy</span></button></div>`;
}

function renderCertificateDnsGuide(target, guide) {
  const dns = guide?.dns;
  const cf = guide?.cloudflare || {};
  if (!dns) { target.innerHTML = '<p>Enter a domain to inspect DNS readiness and record guidance.</p>'; return; }
  const dnsState = !dns.resolves ? ['needs DNS', 'bad'] : dns.direct_to_sycord ? ['direct to Sycord', 'ok'] : ['DNS does not point directly to Sycord', 'bad'];
  target.innerHTML = `<div class="certificate-guide-head"><span class="certificate-dns-state ${dnsState[1]}">${esc(dnsState[0])}</span><span class="certificate-proxy-state">Cloudflare proxy: <b>off / DNS only</b></span></div><p class="certificate-guide-note">${dns.resolves ? `Resolved IPs: ${esc((dns.ips || []).join(', ') || 'none')}.` : 'No DNS answer was found yet.'} Normal certificate validation needs the A record to resolve directly to this Sycord host; turn the Cloudflare proxy off while issuing.</p><div class="certificate-records"><div><p>Normal domain record</p>${certificateRecordLine(dns.normal_record)}</div><div><p>Wildcard DNS-01 record</p>${certificateRecordLine(dns.wildcard_record)}</div></div><div class="certificate-provider-state"><img src="/static/vendor/cloudflare-svgl.svg?v=__VERSION__" alt=""><span>Cloudflare DNS token ${cf.token_configured ? 'configured' : 'not configured'} · ${cf.caddy_plugin_installed ? 'Caddy plugin ready' : 'Caddy plugin required for wildcard issuance'}</span></div>`;
  refreshIcons();
}

function wireCertificateIssuance() {
  const form = document.querySelector('[data-certificate-issue]');
  const guideTarget = document.querySelector('[data-certificate-guide]');
  if (!form || !guideTarget) return;
  const domainInput = form.querySelector('[name="domain"]');
  let checkTimer = null;
  const checkGuidance = async () => {
    const domain = domainInput.value.trim();
    if (!domain) { renderCertificateDnsGuide(guideTarget, null); return; }
    guideTarget.innerHTML = '<p>Checking domain DNS…</p>';
    try { renderCertificateDnsGuide(guideTarget, await api(`/certificates/guide?domain=${encodeURIComponent(domain)}`)); }
    catch (error) { guideTarget.innerHTML = `<p>Could not check DNS: ${esc(normalizeFetchError(error?.message) || 'unknown error')}</p>`; }
  };
  domainInput.addEventListener('input', () => { clearTimeout(checkTimer); checkTimer = setTimeout(checkGuidance, 500); });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const values = new FormData(form);
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    try {
      const result = await api('/certificates/issue', {method: 'POST', body: JSON.stringify({project_id: values.get('project_id'), domain: values.get('domain'), wildcard: values.get('wildcard') === 'on'})});
      renderCertificateDnsGuide(guideTarget, result);
      toast(result.message || 'Certificate request applied.');
      if (typeof loadSslDashboard === 'function') void loadSslDashboard();
    } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not request the certificate.'); }
    finally { button.disabled = false; }
  });
  guideTarget.addEventListener('click', async event => {
    const button = event.target.closest('[data-copy-certificate-record]');
    if (!button) return;
    try { await navigator.clipboard.writeText(button.dataset.copyCertificateRecord); toast('DNS record copied.'); }
    catch (_) { toast('Select the record and copy it manually.'); }
  });
}

// Share It — Syte-hosted template catalog.
let shareItTemplates = [];
let selectedShareTemplate = null;
async function loadShareItTemplates() {
  const list = document.getElementById('share-it-template-list');
  if (!list) return;
  list.innerHTML = '<div class="share-it-loading">Loading Syte-hosted templates…</div>';
  try {
    const payload = await api('/share/templates');
    shareItTemplates = payload.templates || [];
    renderShareItTemplates();
  } catch (error) { list.innerHTML = `<div class="share-it-loading">${escapeHtml(normalizeFetchError(error?.message) || 'Unable to load templates.')}</div>`; }
}
function renderShareItTemplates() {
  const list = document.getElementById('share-it-template-list');
  const query = (document.getElementById('share-it-filter')?.value || '').trim().toLowerCase();
  if (!list) return;
  const rows = shareItTemplates.filter(t => !query || `${t.name} ${t.summary} ${t.framework}`.toLowerCase().includes(query));
  list.innerHTML = rows.length ? rows.map(template => `<article class="share-it-template-card"><div class="share-it-template-icon"><i data-lucide="${escapeHtml(template.icon || 'layout-template')}"></i></div><div><span>${escapeHtml(template.framework)}</span><h2>${escapeHtml(template.name)}</h2><p>${escapeHtml(template.summary)}</p><small>${escapeHtml(template.runtime)} · Syte hosted source</small></div><button type="button" class="btn-pill btn-primary" data-share-template="${escapeHtml(template.id)}">Use template</button></article>`).join('') : '<div class="share-it-loading">No matching Syte-hosted templates.</div>';
  list.querySelectorAll('[data-share-template]').forEach(button => { button.onclick = () => openShareItProvision(button.dataset.shareTemplate); });
  refreshIcons();
}
function openShareItProvision(templateId) {
  selectedShareTemplate = shareItTemplates.find(template => template.id === templateId) || null;
  if (!selectedShareTemplate) return;
  document.getElementById('share-it-provision-title').textContent = selectedShareTemplate.name;
  document.getElementById('share-it-provision-copy').textContent = `${selectedShareTemplate.description} Its scoped server credential is injected only as a hosted runtime variable.`;
  document.getElementById('share-it-instance-name').value = '';
  document.getElementById('share-it-access-password').value = '';
  document.getElementById('share-it-provision').classList.remove('hidden');
  document.getElementById('share-it-instance-name').focus();
}
async function provisionShareItTemplate() {
  const name = document.getElementById('share-it-instance-name')?.value.trim();
  const accessPassword = document.getElementById('share-it-access-password')?.value || '';
  const button = document.getElementById('share-it-provision-submit');
  if (!selectedShareTemplate || !name) return toast('Provide a hosted project name.');
  if (accessPassword.length < 12) return toast('Set a workspace access password of at least 12 characters.');
  button.disabled = true;
  try {
    const result = await api(`/share/templates/${encodeURIComponent(selectedShareTemplate.id)}/provision`, { method: 'POST', body: JSON.stringify({name, access_password: accessPassword}) });
    document.getElementById('share-it-access-password').value = '';
    toast(result.message || 'Hosted template created.');
    document.getElementById('share-it-provision').classList.add('hidden');
    await loadProjects();
    const project = projects.find(item => item.id === result.project?.id);
    if (project) openService(project.id);
  } catch (error) { toast(normalizeFetchError(error?.message) || 'Could not create the hosted template.'); }
  finally { button.disabled = false; }
}
document.getElementById('share-it-filter')?.addEventListener('input', renderShareItTemplates);
document.getElementById('share-it-provision-cancel')?.addEventListener('click', () => document.getElementById('share-it-provision')?.classList.add('hidden'));
document.getElementById('share-it-provision-submit')?.addEventListener('click', provisionShareItTemplate);

// Escape text returned by the template catalog before it is interpolated into Share It markup.
function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>'"]/g, character => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  }[character]));
}
