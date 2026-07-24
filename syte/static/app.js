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
let debugChatActiveRequestId = '';
let debugChatStopping = false;
let debugChatActivityDismissTimer = null;
let debugChatRequestWatchdogTimer = null;
let debugChatRequestStartedAt = 0;
let debugChatSendInFlight = false;
let debugChatConnectionState = 'disconnected';
let debugChatTerminalRequestIds = new Set();
let debugChatIdleStatus = 'Agent ready';
let debugChatActivityLabel = '';
let debugChatResourceMode = '';
let projectFilterText = '';
let projectSortMode = 'newest';
let appContext = 'non-conected';
let statsPollTimer = null;
let activeSvcTab = 'general';
let logsAutoScroll = true;
let serverPublicIp = '';

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

function parseApiErrorPayload(err, statusText) {
  if (!err) return statusText || 'Request failed';
  const detail = err.detail;
  if (typeof detail === 'string') return detail;
  if (Array.isArray(detail)) return detail.map(d => d.msg || d).join(', ');
  if (detail && typeof detail === 'object') return detail.message || detail.error || statusText || 'Request failed';
  return err.message || statusText || 'Request failed';
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

function stopAgentActivityStream() {
  if (agentActivityPollTimer) {
    clearInterval(agentActivityPollTimer);
    agentActivityPollTimer = null;
  }
  if (agentActivityEventSource) {
    agentActivityEventSource.close();
    agentActivityEventSource = null;
  }
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
  if (!sync || !sync.turso_configured) {
    btn.classList.add('brain-unconfigured');
    btn.setAttribute('aria-label', 'Turso is not configured — messages are only saved locally');
    btn.title = 'Turso is not configured — messages are only saved locally';
    return;
  }
  if (sync.all_saved) {
    btn.classList.add('brain-saved');
    const label = `All ${sync.total_messages || 0} session message(s) saved to Turso`;
    btn.setAttribute('aria-label', label);
    btn.title = label;
  } else {
    btn.classList.add('brain-unsaved');
    const label = `${sync.synced_messages || 0} of ${sync.total_messages || 0} session messages saved to Turso — retrying`;
    btn.setAttribute('aria-label', label);
    btn.title = label;
  }
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

function getDebugChatMessagesEl() {
  return document.getElementById('debug-chat-messages');
}

function hideDebugChatEmpty() {
  document.getElementById('debug-chat-empty')?.classList.add('hidden');
}

function showDebugChatEmpty() {
  const empty = document.getElementById('debug-chat-empty');
  if (empty) empty.classList.remove('hidden');
}

function updateDebugChatScrollState() {
  const el = getDebugChatMessagesEl();
  if (!el) return;
  const distanceFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight;
  debugChatAutoScroll = distanceFromBottom < 72;
}

function scrollDebugChatToBottom(force = false) {
  const el = getDebugChatMessagesEl();
  if (!el || (!debugChatAutoScroll && !force)) return;
  el.scrollTop = el.scrollHeight;
  if (force) {
    debugChatAutoScroll = true;
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
      'syra-nano': 'nano',
      'syra-base': 'base',
      'syra-havy': 'pro',
      'syra-ultra': 'ultra',
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
  const el = getDebugChatMessagesEl();
  if (!el) return;
  if (debugChatStreamFlushFrame) {
    cancelAnimationFrame(debugChatStreamFlushFrame);
    debugChatStreamFlushFrame = null;
  }
  debugChatStreamBuffers.clear();
  el.innerHTML = '';
  const empty = document.createElement('div');
  empty.className = 'debug-chat-empty';
  empty.id = 'debug-chat-empty';
  empty.innerHTML = '<p>What would you like to change?</p>';
  el.appendChild(empty);
  debugChatRenderedIds.clear();
  if (resetCursor) {
    debugChatSinceId = 0;
    debugChatTerminalRequestIds.clear();
  }
  debugChatAutoScroll = true;
  setDebugChatTyping(false);
  if (!debugChatBusy) setDebugChatActivity(debugChatIdleStatus);
}

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
  request_completed: 'Completed',
  processing: 'Working',
  screenshot: 'Screenshot',
  question: 'Question',
  question_answered: 'Answer',
  agent_stopped: 'Stopped',
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
  screenshot_preview: { label: 'Screenshot preview', icon: 'monitor-smartphone' },
  ask_question: { label: 'Ask question', icon: 'circle-help' },
  env_get: { label: 'Get env', icon: 'key-round' },
  env_set: { label: 'Set env', icon: 'key-round' },
  request_env: { label: 'Request env', icon: 'key-round' },
  list_mcp_addons: { label: 'List MCP', icon: 'plug' },
  connect_mcp: { label: 'Connect MCP', icon: 'plug' },
  call_mcp: { label: 'Call MCP', icon: 'plug' },
  delegate_task: { label: 'Delegate task', icon: 'git-fork' },
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

function debugChatRoleForEvent(event) {
  const type = event.event_type;
  if (type === 'user_message') return 'user';
  if (type === 'assistant_message' || type === 'request_completed' || type === 'message_snapshot') return 'assistant';
  if (type === 'token_delta') return 'stream';
  if (type === 'request_failed') return 'error';
  if (type === 'thinking') return 'thinking';
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
  const messagesEl = getDebugChatMessagesEl();
  if (!messagesEl) return null;
  const existing = document.getElementById(`debug-chat-stream-${rid}`);
  if (existing) return existing.querySelector('.debug-chat-bubble-body');

  hideDebugChatEmpty();
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
  scrollDebugChatToBottom();
  return bubble.querySelector('.debug-chat-bubble-body');
}

function flushDebugChatStreamBuffers() {
  debugChatStreamFlushFrame = null;
  for (const [rid, text] of debugChatStreamBuffers.entries()) {
    const bodyEl = ensureStreamingAssistantBubble(rid);
    if (bodyEl) bodyEl.textContent = text;
  }
  scrollDebugChatToBottom();
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
  scrollDebugChatToBottom();
}

function finalizeAllDebugChatStreams() {
  for (const [requestId, text] of [...debugChatStreamBuffers.entries()]) {
    finalizeDebugChatStream(requestId, text);
  }
}

function debugChatErrorPresentation(event) {
  const code = event?.payload?.error || '';
  const fallback = event?.detail || event?.payload?.message || 'The request could not be completed.';
  const known = {
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
      `/api/projects/${encodeURIComponent(activeServiceId)}/agent/questions/${encodeURIComponent(questionId)}/answer`,
      { method: 'POST', body: JSON.stringify({ answer }) },
    );
    if (!res.ok) {
      toast(res.message || 'Failed to send answer', 'error');
      controls?.forEach((el) => { el.disabled = false; });
      return;
    }
    const status = formEl?.querySelector('.debug-chat-question-status');
    if (status) status.textContent = 'Answer sent';
    setDebugChatActivity('Working…', 'Continuing with your answer');
  } catch (err) {
    toast(String(err), 'error');
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

function appendDebugChatBubble(event) {
  const messagesEl = getDebugChatMessagesEl();
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
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <span>${esc(event.title || 'Plan')}</span>
      </div>
      <div class="debug-chat-bubble-body debug-chat-thinking">${esc(detail)}</div>
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
  } else if (role === 'action') {
    bubble.classList.add('debug-chat-action-new');
    const actionMeta = debugChatActionMeta(event);
    const compactDetail = String(detail || '').replace(/\s+/g, ' ').slice(0, 240);
    bubble.innerHTML = `
      <div class="debug-chat-action-row">
        <i data-lucide="${esc(actionMeta.icon)}" aria-hidden="true"></i>
        <div class="debug-chat-action-text">
          <strong>${esc(actionTitle)}</strong>
          ${compactDetail ? `<span>${esc(compactDetail)}</span>` : ''}
        </div>
      </div>
    `;
    requestAnimationFrame(() => bubble.classList.remove('debug-chat-action-new'));
  } else {
    bubble.innerHTML = `
      <div class="debug-chat-system-row">
        <span>${esc(event.title || event.event_type)}${detail ? ` — ${esc(detail)}` : ''}</span>
      </div>
    `;
  }

  if (role === 'error') addDebugChatErrorActions(bubble, event, errorPresentation);
  messagesEl.appendChild(bubble);
  // Full-document Lucide passes during history replay are extremely expensive
  // (hundreds of createIcons scans) and have caused mobile tab freezes/"Script error".
  if (!debugChatReplayingHistory) refreshIcons(bubble);
  scrollDebugChatToBottom();
}

function shouldSkipDebugChatEvent(event) {
  if (event.event_type === 'request_started') {
    return true;
  }
  if (event.event_type === 'token_delta') return true;
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
    const messagesEl = getDebugChatMessagesEl();
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
    const streamBubble = rid ? document.getElementById(`debug-chat-stream-${rid}`) : null;
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
    const messagesEl = getDebugChatMessagesEl();
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
  const eventRequestId = event.payload?.request_id || '';
  const isTerminal = event.event_type === 'request_completed'
    || event.event_type === 'request_failed'
    || event.event_type === 'agent_stopped';
  if (isTerminal && eventRequestId) {
    if (debugChatTerminalRequestIds.has(eventRequestId)) return;
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
  if (
    event.event_type === 'request_completed'
    || event.event_type === 'request_failed'
    || event.event_type === 'agent_stopped'
  ) {
    const requestId = eventRequestId || debugChatActiveRequestId;
    const isActiveRequest = !debugChatActiveRequestId
      || (Boolean(eventRequestId) && eventRequestId === debugChatActiveRequestId);
    const finalText = event.event_type === 'request_completed'
      ? debugChatDetailText(event)
      : '';
    // During history replay, bubbles are rendered via appendDebugChatBubble —
    // don't create streaming "Agent" placeholders (avoids duplicate/[object Object] artifacts).
    if (!debugChatReplayingHistory && isActiveRequest) {
      finalizeDebugChatStream(requestId, finalText);
      const wasStopping = debugChatStopping || event.event_type === 'agent_stopped';
      setDebugChatTyping(false);
      clearDebugChatRequestWatchdog();
      setDebugChatBusy(false);
      debugChatActiveRequestId = '';
      setDebugChatActivity(
        event.event_type === 'request_completed'
          ? 'Response ready'
          : (wasStopping || event.event_type === 'agent_stopped'
            ? 'Response stopped'
            : 'Response failed'),
        '',
        event.event_type === 'request_completed' ? 'check-circle-2' : 'circle-alert',
      );
      dismissDebugChatActivitySoon();
      void updateDebugChatAgentStatus();
    }
  }
  if (event.event_type === 'agent_started' && !debugChatReplayingHistory) {
    void updateDebugChatAgentStatus();
  }

  if (shouldSkipDebugChatEvent(event)) return;

  appendDebugChatBubble(event);
  if (!debugChatReplayingHistory && event.event_type === 'thinking') {
    setDebugChatActivity('Planning…', String(event.detail || 'Preparing a plan').replace(/\s+/g, ' ').slice(0, 160));
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
    setDebugChatActivity(
      phase,
      `${debugChatActionTitle(event)}${event.detail ? ` · ${event.detail}` : ''}`.slice(0, 200),
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

async function syncDebugChatHistory(projectId) {
  try {
    const res = await api(`/projects/${projectId}/agent/activity?since_id=${debugChatSinceId}&limit=500`);
    for (const event of res.events || []) {
      handleDebugChatActivity(event);
    }
    return true;
  } catch {
    return false;
  }
}

async function loadDebugChatHistory(projectId) {
  debugChatReplayingHistory = true;
  updateDebugChatControls();
  try {
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
    refreshIcons(getDebugChatMessagesEl() || undefined);
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
  'user_message', 'assistant_message', 'thinking', 'tool_call', 'command_run',
  'file_created', 'file_modified', 'file_deleted', 'file_read', 'file_search',
  'request_started', 'request_completed', 'request_failed', 'token_delta',
  'message_snapshot', 'tool_call_started', 'tool_call_finished', 'tool_error',
  'file_changed', 'command_output', 'agent_started', 'agent_stopped', 'status',
  'processing', 'service_action', 'screenshot', 'question', 'question_answered',
  'session_stopped', 'plan', 'message',
];

function handleAgentActivitySseFrame(evt) {
  try {
    const event = JSON.parse(evt.data || '{}');
    if (event && event.event_type) {
      applyDebugChatActivityEvent(event);
    }
  } catch (_) {
    /* ignore malformed frames */
  }
}

function bindAgentActivityEventSource(es) {
  es.onmessage = handleAgentActivitySseFrame;
  for (const type of DEBUG_CHAT_SSE_EVENT_TYPES) {
    es.addEventListener(type, handleAgentActivitySseFrame);
  }
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

function startAgentActivityStream(projectId) {
  stopAgentActivityStream();
  setDebugChatConnectionState('connecting');
  void loadDebugChatResumeSession(projectId);
  void pollAgentActivityOnce(projectId);

  try {
    const url = `${API}/projects/${projectId}/agent/activity/stream?session=last&since_id=${encodeURIComponent(debugChatSinceId || 0)}`;
    agentActivityEventSource = new EventSource(url);
    agentActivityEventSource.onopen = () => setDebugChatConnectionState('connected');
    bindAgentActivityEventSource(agentActivityEventSource);
    agentActivityEventSource.onerror = () => {
      setDebugChatConnectionState('reconnecting');
      if (agentActivityEventSource) {
        agentActivityEventSource.close();
        agentActivityEventSource = null;
      }
      // Fall back to polling if SSE drops.
      startAgentActivityPollFallback(projectId);
    };
  } catch (_) {
    startAgentActivityPollFallback(projectId);
  }
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
  try {
    const res = await api(`/projects/${activeServiceId}/agent/interrupt`, { method: 'POST' });
    if (!res.ok) throw new Error(formatAgentChatError(res));
    setDebugChatTyping(false);
    if ((res.message || '').startsWith('No active')) {
      finalizeDebugChatStream(debugChatActiveRequestId);
      clearDebugChatRequestWatchdog();
      setDebugChatBusy(false);
      debugChatActiveRequestId = '';
      setDebugChatActivity('Response stopped', 'Conversation history is preserved', 'square');
      dismissDebugChatActivitySoon();
    } else {
      setDebugChatActivity('Stopping response', 'Waiting for the agent to finish cancelling', 'square');
      setTimeout(() => {
        if (activeServiceId) void syncDebugChatHistory(activeServiceId);
      }, 1000);
    }
  } catch (e) {
    debugChatStopping = false;
    setDebugChatBusy(true);
    toast('Could not stop response: ' + normalizeFetchError(e.message));
    if (cancel) cancel.disabled = false;
  }
}

async function getDebugChatProfile() {
  const select = document.getElementById('debug-chat-profile');
  return select?.value || select?.getAttribute('value') || 'syra-base';
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

async function openDebugChatTab() {
  if (!activeServiceId) return;
  const projectId = activeServiceId;
  warmProjectAgent(projectId);
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
  hideDebugChatEmpty();
  debugChatLastUserMessage = message;
  appendDebugChatBubble({
    event_type: 'user_message',
    title: 'You',
    detail: message,
  });
  scrollDebugChatToBottom(true);
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
    const res = await api(`/projects/${activeServiceId}/agent/chat`, {
      method: 'POST',
      body: JSON.stringify({
        message: sentMessage,
        model_profile: profile,
      }),
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
        setDebugChatActivity('Working…', `${profile} · thinking and building`);
        armDebugChatRequestWatchdog(activeServiceId, res.request_id);
      }
    } else if (res.reply) {
      await syncDebugChatHistory(activeServiceId);
      const messagesEl = getDebugChatMessagesEl();
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

let aiApiConfigured = { nano: false, base: false, havy: false, ultra: false };

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
    'syra-base': ['agent-base-price-in', 'agent-base-price-out'],
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
  const profile = document.getElementById('ai-test-profile')?.value || 'syra-base';
  const keyForProfile = {
    'syra-nano': 'agent-nano-key',
    'syra-base': 'agent-base-key',
    'syra-havy': 'agent-havy-key',
    'syra-ultra': 'agent-ultra-key',
  };
  const savedForProfile = {
    'syra-nano': aiApiConfigured.nano,
    'syra-base': aiApiConfigured.base,
    'syra-havy': aiApiConfigured.havy,
    'syra-ultra': aiApiConfigured.ultra,
  };
  const inputId = keyForProfile[profile] || 'agent-base-key';
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
  refreshIcons();
}

function closeAiSettings() {
  const sheet = document.getElementById('ai-settings-sheet');
  if (!sheet) return;
  sheet.classList.add('hidden');
  document.body.classList.remove('ai-settings-open');
}

async function api(path, opts = {}) {
  const headers = { 'Content-Type': 'application/json', ...(opts.headers || {}) };
  if (shouldAttachApiKey(path)) headers['X-API-Key'] = getApiKey();
  let res = await fetch(API + path, { headers, ...opts });
  if (res.status === 401 && getApiKey()) {
    setApiKey('');
    const retryHeaders = { ...headers };
    delete retryHeaders['X-API-Key'];
    res = await fetch(API + path, { headers: retryHeaders, ...opts });
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(normalizeFetchError(parseApiErrorPayload(err, res.statusText)));
  }
  return res.json();
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
  const baseKey = document.getElementById('agent-base-key')?.value?.trim() || '';
  const havyKey = document.getElementById('agent-havy-key')?.value?.trim() || '';
  const ultraKey = document.getElementById('agent-ultra-key')?.value?.trim() || '';
  const internalSecret = document.getElementById('syra-internal-secret')?.value?.trim() || '';
  const maxRaw = document.getElementById('agent-max-count')?.value?.trim();
  const tursoDatabaseUrl = document.getElementById('turso-database-url')?.value?.trim() || '';
  const tursoAuthToken = document.getElementById('turso-auth-token')?.value?.trim() || '';
  const needNano = !nanoKey && !aiApiConfigured.nano;
  const needBase = !baseKey && !aiApiConfigured.base;
  const needHavy = !havyKey && !aiApiConfigured.havy;
  const needUltra = !ultraKey && !aiApiConfigured.ultra;
  if (!nanoKey && !baseKey && !havyKey && !ultraKey && needNano && needBase && needHavy && needUltra) {
    return toast('Enter at least one model API key');
  }
  const body = {
    agent_default_model_profile: document.getElementById('agent-default-profile')?.value || 'syra-base',
  };
  if (nanoKey) body.agent_syra_nano_api_key = nanoKey;
  if (baseKey) body.agent_syra_base_api_key = baseKey;
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
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const res = await api('/settings', { method: 'PUT', body: JSON.stringify(body) });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'Provider settings saved');
    if (nanoKey) document.getElementById('agent-nano-key').value = '';
    if (baseKey) document.getElementById('agent-base-key').value = '';
    if (havyKey) document.getElementById('agent-havy-key').value = '';
    if (ultraKey) document.getElementById('agent-ultra-key').value = '';
    if (internalSecret) document.getElementById('syra-internal-secret').value = '';
    if (tursoAuthToken) document.getElementById('turso-auth-token').value = '';
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
    const defaultProfile = s.agent_default_model_profile || 'syra-base';
    if (agentDefaultProfile) agentDefaultProfile.value = defaultProfile;
    if (window.customElements?.whenDefined) await customElements.whenDefined('sl-select');
    const debugChatProfile = document.getElementById('debug-chat-profile');
    if (debugChatProfile) debugChatProfile.value = defaultProfile;
    if (agentMaxCount && s.agent_max_count) agentMaxCount.value = s.agent_max_count;
    if (agentMaxCount && !s.agent_max_count) agentMaxCount.placeholder = '50';
    const keyFields = [
      ['agent-nano-key', 'agent-nano-key-hint', s.agent_syra_nano_api_key_set, 'Vertex AI nano key saved', 'Vertex AI API key required'],
      ['agent-base-key', 'agent-base-key-hint', s.agent_syra_base_api_key_set, 'DeepSeek base key saved', 'DeepSeek API key required'],
      ['agent-havy-key', 'agent-havy-key-hint', s.agent_syra_havy_api_key_set, 'Vertex AI pro key saved', 'Vertex AI API key required'],
      ['agent-ultra-key', 'agent-ultra-key-hint', s.agent_syra_ultra_api_key_set, 'Aliyun ultra key saved (sk-sp- Token Plan or Model Studio sk-)', 'Aliyun Token Plan sk-sp-… key required'],
    ];
    keyFields.forEach(([inputId, hintId, saved, savedText, requiredText]) => {
      const input = document.getElementById(inputId);
      const hint = document.getElementById(hintId);
      if (input) {
        input.placeholder = saved ? 'key saved — enter new value to replace' : 'required';
      }
      if (hint) hint.textContent = saved ? savedText : requiredText;
    });
    applyAiProviderCatalog(s.ai_providers || []);
    renderProviderKeyStatus(s.provider_keys || []);
    aiApiConfigured = {
      nano: Boolean(s.agent_syra_nano_api_key_set),
      base: Boolean(s.agent_syra_base_api_key_set),
      havy: Boolean(s.agent_syra_havy_api_key_set),
      ultra: Boolean(s.agent_syra_ultra_api_key_set),
    };
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
    if (agentRuntimeStatus) {
      const parts = [];
      parts.push(`default: ${defaultProfile}`);
      parts.push(s.agent_syra_nano_api_key_set ? 'nano key saved' : 'no nano key');
      parts.push(s.agent_syra_base_api_key_set ? 'base key saved' : 'no base key');
      parts.push(s.agent_syra_havy_api_key_set ? 'pro key saved' : 'no pro key');
      parts.push(s.agent_syra_ultra_api_key_set ? 'ultra key saved' : 'no ultra key');
      parts.push(s.syra_internal_secret_set ? 'internal secret saved' : 'no internal secret');
      parts.push(s.turso_configured ? 'Turso configured' : 'Turso not configured');
      agentRuntimeStatus.textContent = parts.join(' · ');
    }
    const directUrl = document.getElementById('direct-url');
    const guiUrl = document.getElementById('gui-url');
    const ver = document.getElementById('syte-version');
    if (directUrl && s.direct_url) directUrl.textContent = s.direct_url;
    if (guiUrl) guiUrl.textContent = s.domain_url || 'not configured';
    if (ver && s.version) ver.textContent = 'v' + s.version;
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
    const keysConfigured = [onboard.ai_models, aiApiConfigured.nano, aiApiConfigured.base, aiApiConfigured.havy].some(Boolean);
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
window.addEventListener('error', (event) => {
  const msg = String(event?.message || event?.error?.message || '');
  if (!msg) return;
  if (/^script error\.?$/i.test(msg.trim())) {
    console.error('[Syte] Cross-origin script error (often CDN/lucide). Details are masked by the browser.', event);
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
document.getElementById('debug-chat-messages')?.addEventListener('scroll', updateDebugChatScrollState, { passive: true });
document.getElementById('debug-chat-profile')?.addEventListener('change', () => {
  if (debugChatBusy) {
    const modelEl = document.getElementById('debug-chat-activity-model');
    const profile = document.getElementById('debug-chat-profile')?.value || '';
    const short = ({ 'syra-nano': 'nano', 'syra-base': 'base', 'syra-havy': 'pro', 'syra-ultra': 'ultra' })[profile] || profile;
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

document.getElementById('ai-header-settings-btn')?.addEventListener('click', openAiSettings);
document.getElementById('ai-settings-close')?.addEventListener('click', closeAiSettings);
document.getElementById('ai-settings-backdrop')?.addEventListener('click', closeAiSettings);

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
