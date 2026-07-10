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
let agentActivityStream = null;
let agentActivityReconnectTimer = null;
let debugChatSinceId = 0;
let debugChatRenderedIds = new Set();
let debugChatAutoScroll = true;
let debugChatBusy = false;
let debugChatReplayingHistory = false;
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
  if (agentActivityReconnectTimer) {
    clearTimeout(agentActivityReconnectTimer);
    agentActivityReconnectTimer = null;
  }
  if (agentActivityStream) {
    agentActivityStream.close();
    agentActivityStream = null;
  }
  setDebugChatLiveIndicator(false);
}

function setDebugChatLiveIndicator(live) {
  const dot = document.getElementById('debug-chat-live');
  if (dot) dot.classList.toggle('live', !!live);
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

function scrollDebugChatToBottom() {
  const el = getDebugChatMessagesEl();
  if (el && debugChatAutoScroll) el.scrollTop = el.scrollHeight;
}

function clearDebugChatPanel() {
  const el = getDebugChatMessagesEl();
  if (!el) return;
  el.innerHTML = '';
  const empty = document.createElement('div');
  empty.className = 'debug-chat-empty';
  empty.id = 'debug-chat-empty';
  empty.innerHTML = '<i data-lucide="sparkles"></i><p>Describe changes for this website. The AI agent edits files in your workspace — start preview to see updates live.</p>';
  el.appendChild(empty);
  debugChatRenderedIds.clear();
  debugChatSinceId = 0;
  refreshIcons();
}

const DEBUG_CHAT_ACTION_LABELS = {
  file_created: 'Create file',
  file_modified: 'Rewrite file',
  file_deleted: 'Delete file',
  file_read: 'Read file',
  file_search: 'Search',
  command_run: 'Run command',
  tool_call: 'Tool call',
  service_action: 'Service',
  request_started: 'Request started',
  request_completed: 'Completed',
  processing: 'Working',
};

function debugChatIconForEvent(eventType) {
  const map = {
    user_message: 'user',
    assistant_message: 'bot',
    thinking: 'brain',
    tool_call: 'wrench',
    command_run: 'terminal',
    service_action: 'settings-2',
    file_created: 'file-plus-2',
    file_modified: 'file-pen-line',
    file_deleted: 'file-x-2',
    file_read: 'file-text',
    file_search: 'search',
    request_started: 'play-circle',
    request_completed: 'check-circle-2',
    request_failed: 'circle-alert',
    processing: 'loader',
    agent_started: 'play',
    agent_stopped: 'square',
    status: 'info',
  };
  return map[eventType] || 'circle-dot';
}

function debugChatRoleForEvent(event) {
  const type = event.event_type;
  if (type === 'user_message') return 'user';
  if (type === 'assistant_message' || type === 'request_completed') return 'assistant';
  if (type === 'request_failed') return 'error';
  if (type === 'thinking') return 'thinking';
  if (type === 'processing') return 'processing';
  if ([
    'file_created', 'file_modified', 'file_deleted', 'file_read', 'file_search',
    'tool_call', 'command_run', 'service_action', 'request_started',
  ].includes(type)) {
    return 'action';
  }
  return 'system';
}

function debugChatActionTitle(event) {
  if (event.event_type === 'request_completed') return 'Assistant';
  if (event.event_type === 'request_started') return 'Request';
  return DEBUG_CHAT_ACTION_LABELS[event.event_type] || event.title || event.event_type || 'Action';
}

function setDebugChatTyping(show) {
  const messagesEl = getDebugChatMessagesEl();
  if (!messagesEl) return;
  const existing = document.getElementById('debug-chat-typing');
  if (!show) {
    existing?.remove();
    return;
  }
  if (existing) return;
  hideDebugChatEmpty();
  const bubble = document.createElement('div');
  bubble.className = 'debug-chat-bubble debug-chat-thinking debug-chat-typing';
  bubble.id = 'debug-chat-typing';
  bubble.innerHTML = `
    <div class="debug-chat-bubble-head">
      <i data-lucide="loader"></i>
      <span>Agent working…</span>
    </div>
  `;
  messagesEl.appendChild(bubble);
  scrollDebugChatToBottom();
  refreshIcons();
}

function appendDebugChatBubble(event) {
  const messagesEl = getDebugChatMessagesEl();
  if (!messagesEl || !event) return;

  const role = debugChatRoleForEvent(event);
  const detail = event.detail || event.payload?.content || event.payload?.reply || '';
  const actionTitle = debugChatActionTitle(event);

  hideDebugChatEmpty();
  if (event.event_type === 'processing') {
    setDebugChatTyping(true);
    return;
  }
  setDebugChatTyping(false);

  const bubble = document.createElement('div');
  bubble.className = `debug-chat-bubble debug-chat-${role}`;
  if (event.id != null) bubble.dataset.eventId = String(event.id);

  const iconName = debugChatIconForEvent(event.event_type);

  if (role === 'user' || role === 'assistant' || role === 'error') {
    const title = role === 'user' ? 'You' : role === 'error' ? 'Error' : 'Assistant';
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <i data-lucide="${iconName}"></i>
        <span>${esc(title)}</span>
      </div>
      <div class="debug-chat-bubble-body">${esc(detail)}</div>
    `;
  } else if (role === 'thinking') {
    bubble.innerHTML = `
      <div class="debug-chat-bubble-head">
        <i data-lucide="${iconName}"></i>
        <span>${esc(event.title || 'Thinking')}</span>
      </div>
      <div class="debug-chat-bubble-body debug-chat-thinking">${esc(detail)}</div>
    `;
  } else if (role === 'action') {
    bubble.classList.add('debug-chat-action-new');
    bubble.innerHTML = `
      <div class="debug-chat-action-row">
        <i data-lucide="${iconName}"></i>
        <div class="debug-chat-action-text">
          <strong>${esc(actionTitle)}</strong>
          ${detail ? `<span>${esc(detail)}</span>` : ''}
        </div>
      </div>
    `;
    requestAnimationFrame(() => bubble.classList.remove('debug-chat-action-new'));
  } else {
    bubble.innerHTML = `
      <div class="debug-chat-system-row">
        <i data-lucide="${iconName}"></i>
        <span>${esc(event.title || event.event_type)}${detail ? ` — ${esc(detail)}` : ''}</span>
      </div>
    `;
  }

  messagesEl.appendChild(bubble);
  scrollDebugChatToBottom();
  refreshIcons();
}

function shouldSkipDebugChatEvent(event) {
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
  if (!event || shouldSkipDebugChatEvent(event)) return;
  const eventId = event.id;
  if (eventId != null && debugChatRenderedIds.has(eventId)) return;
  if (eventId != null) debugChatRenderedIds.add(eventId);

  appendDebugChatBubble(event);
  if (eventId != null) debugChatSinceId = Math.max(debugChatSinceId, eventId);

  const refreshTypes = [
    'file_created', 'file_modified', 'file_deleted', 'file_search',
    'service_action', 'command_run', 'request_completed',
  ];
  if (refreshTypes.includes(event.event_type)) {
    onDebugChatWorkspaceChange();
  }
  if (event.event_type === 'request_started') {
    if (!debugChatReplayingHistory) {
      setDebugChatTyping(true);
      setDebugChatBusy(true);
    }
  }
  if (event.event_type === 'request_completed' || event.event_type === 'request_failed') {
    if (!debugChatReplayingHistory) {
      setDebugChatTyping(false);
      setDebugChatBusy(false);
    }
  }
}

async function onDebugChatWorkspaceChange() {
  if (!activeServiceId || activeSvcTab !== 'debug-chat') return;
  await loadProjects({ silent: true });
}

async function loadDebugChatHistory(projectId) {
  debugChatReplayingHistory = true;
  try {
    const res = await api(`/projects/${projectId}/agent/activity?since_id=0&limit=500`);
    clearDebugChatPanel();
    for (const event of res.events || []) {
      handleDebugChatActivity(event);
    }
    const lastId = (res.events || []).reduce((max, e) => Math.max(max, e.id || 0), 0);
    if (lastId) debugChatSinceId = Math.max(debugChatSinceId, lastId);
  } catch (e) {
    appendDebugChatBubble({
      event_type: 'request_failed',
      title: 'Could not load history',
      detail: e.message,
    });
  } finally {
    debugChatReplayingHistory = false;
    setDebugChatTyping(false);
    setDebugChatBusy(false);
  }
}

function scheduleAgentActivityReconnect(projectId, attempt = 0) {
  if (activeSvcTab !== 'debug-chat' || activeServiceId !== projectId) return;
  const delay = Math.min(15000, 1000 * Math.pow(2, attempt));
  agentActivityReconnectTimer = setTimeout(() => {
    startAgentActivityStream(projectId, attempt + 1);
  }, delay);
}

function startAgentActivityStream(projectId, reconnectAttempt = 0) {
  if (agentActivityReconnectTimer) {
    clearTimeout(agentActivityReconnectTimer);
    agentActivityReconnectTimer = null;
  }
  if (agentActivityStream) {
    agentActivityStream.close();
    agentActivityStream = null;
  }
  const params = new URLSearchParams({ live: '1', since_id: String(debugChatSinceId) });
  const key = getApiKey();
  if (key) params.set('api_key', key);
  agentActivityStream = new EventSource(`${API}/projects/${projectId}/agent/activity/stream?${params}`);
  setDebugChatLiveIndicator(true);
  agentActivityStream.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.type === 'activity' && msg.event) {
        handleDebugChatActivity(msg.event);
      } else if (msg.type === 'ping' && msg.since_id != null) {
        debugChatSinceId = Math.max(debugChatSinceId, msg.since_id);
      }
    } catch { /* ignore */ }
  };
  agentActivityStream.onerror = () => {
    setDebugChatLiveIndicator(false);
    agentActivityStream?.close();
    agentActivityStream = null;
    scheduleAgentActivityReconnect(projectId, reconnectAttempt);
  };
}

async function updateDebugChatAgentStatus() {
  const statusEl = document.getElementById('debug-chat-status');
  if (!statusEl || !activeServiceId) return;
  try {
    const res = await api(`/projects/${activeServiceId}/agent`);
    if (res.agent_running) {
      const model = res.agent_model?.profile || res.agent_model?.model || 'agent';
      statusEl.textContent = `Agent online · ${model}`;
    } else {
      statusEl.textContent = 'Agent offline — starts on first message';
    }
  } catch {
    statusEl.textContent = 'Agent status unavailable';
  }
}

function setDebugChatBusy(busy) {
  debugChatBusy = busy;
  const btn = document.getElementById('debug-chat-send');
  const input = document.getElementById('debug-chat-input');
  if (btn) btn.disabled = busy;
  if (input) input.disabled = busy;
}

async function openDebugChatTab() {
  if (!activeServiceId) return;
  await loadDebugChatHistory(activeServiceId);
  startAgentActivityStream(activeServiceId);
  await updateDebugChatAgentStatus();
  refreshIcons();
}

async function sendDebugChatMessage() {
  const input = document.getElementById('debug-chat-input');
  const message = input?.value.trim();
  if (!message || !activeServiceId || debugChatBusy) return;

  setDebugChatBusy(true);
  hideDebugChatEmpty();
  appendDebugChatBubble({
    event_type: 'user_message',
    title: 'You',
    detail: message,
  });
  setDebugChatTyping(true);

  const profile = document.getElementById('debug-chat-profile')?.value || 'syra-base';
  const sentMessage = message;
  if (input) input.value = '';
  try {
    const res = await api(`/projects/${activeServiceId}/agent/chat`, {
      method: 'POST',
      body: JSON.stringify({ message: sentMessage, model_profile: profile }),
    });
    if (!res.ok) {
      appendDebugChatBubble({
        event_type: 'request_failed',
        title: 'Request failed',
        detail: res.message || res.error || 'Unknown error',
      });
      toast(res.message || 'Agent request failed');
    }
    await updateDebugChatAgentStatus();
  } catch (e) {
    appendDebugChatBubble({
      event_type: 'request_failed',
      title: 'Request failed',
      detail: e.message,
    });
    toast('Error: ' + e.message);
  } finally {
    setDebugChatTyping(false);
    setDebugChatBusy(false);
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

  const key = getApiKey();
  const params = new URLSearchParams();
  if (liveOnly) params.set('live', '1');
  if (key) params.set('api_key', key);
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

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
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

let aiApiConfigured = { nano: false, base: false, havy: false };

function aiKeySaved(id) {
  return document.getElementById(id)?.placeholder?.includes('saved');
}

function updateAiApiWarning() {
  const warn = document.getElementById('ai-api-warning');
  const profile = document.getElementById('ai-test-profile')?.value || 'syra-base';
  const keyForProfile = {
    'syra-nano': 'continue-nano-key',
    'syra-base': 'continue-base-key',
    'syra-havy': 'continue-havy-key',
  };
  const savedForProfile = {
    'syra-nano': aiApiConfigured.nano,
    'syra-base': aiApiConfigured.base,
    'syra-havy': aiApiConfigured.havy,
  };
  const inputId = keyForProfile[profile] || 'continue-base-key';
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
  const key = getApiKey();
  if (key) headers['X-API-Key'] = key;
  const res = await fetch(API + path, { headers, ...opts });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    const message = Array.isArray(detail)
      ? detail.map(d => d.msg || d).join(', ')
      : (typeof detail === 'object' && detail?.message ? detail.message : (detail || res.statusText));
    throw new Error(message);
  }
  return res.json();
}

function toast(msg) {
  const el = document.getElementById('toast');
  el.textContent = msg;
  el.classList.remove('hidden');
  setTimeout(() => el.classList.add('hidden'), 3000);
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
    openDebugChatTab();
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
    if (activeServiceId) servicePreviewStopQuiet(activeServiceId);
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

  if (p.preview_running && p.preview_url) {
    if (frame && placeholder) {
      if (live) {
        const frameSrc = (p.preview_tls_ok !== false && p.preview_domain_url)
          ? p.preview_domain_url
          : (p.preview_fetch_url || p.preview_url);
        setPreviewFrameSrc(frame, frameSrc);
        frame.classList.remove('hidden');
        placeholder.classList.add('hidden');
      } else {
        frame.classList.add('hidden');
        placeholder.classList.remove('hidden');
      }
    }
    const urlLabel = p.preview_domain
      ? `${p.preview_domain_url || p.preview_url}`
      : p.preview_url;
    hint.textContent = live
      ? `Live — ${urlLabel}${p.preview_domain && p.preview_tls_ok !== false ? ' (HTTPS)' : ''}${iframeHintLine(p.iframe)}`
      : `Starting on ${p.preview_domain || `port ${p.preview_port || '…'}`}${iframeHintLine(p.iframe)}`;
    if (p.preview_tls_hint) {
      hint.textContent += ` — ${p.preview_tls_hint}`;
    }
    logsWrap?.classList.remove('hidden');
    if (p.preview_running && !previewStream) startPreviewLogStream(p.id, logsEl);
    if (p.preview_running && !p.preview_ready) startPreviewPoll(p.id);
  } else {
    lastPreviewFrameSrc = '';
    if (frame) {
      frame.classList.add('hidden');
      frame.removeAttribute('src');
    }
    placeholder?.classList.remove('hidden');
    hint.textContent = 'Fast dev server with hot reload — auto-stops after 5 min or when you leave this tab';
    logsWrap?.classList.add('hidden');
    stopPreviewStream();
    stopPreviewPoll();
  }
  refreshIcons();
}

function startPreviewLogStream(projectId, targetEl) {
  stopPreviewStream();
  if (!targetEl) return;
  const key = getApiKey();
  const params = new URLSearchParams({ live: '1' });
  if (key) params.set('api_key', key);
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
      const st = await api(`/projects/${projectId}/preview/status`);
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
  const nanoKey = document.getElementById('continue-nano-key')?.value?.trim() || '';
  const baseKey = document.getElementById('continue-base-key')?.value?.trim() || '';
  const havyKey = document.getElementById('continue-havy-key')?.value?.trim() || '';
  const internalSecret = document.getElementById('syra-internal-secret')?.value?.trim() || '';
  const maxRaw = document.getElementById('agent-max-count')?.value?.trim();
  const needNano = !nanoKey && !aiApiConfigured.nano;
  const needBase = !baseKey && !aiApiConfigured.base;
  const needHavy = !havyKey && !aiApiConfigured.havy;
  if (!nanoKey && !baseKey && !havyKey && needNano && needBase && needHavy) {
    return toast('Enter at least one model API key');
  }
  const body = {
    continue_default_model_profile: document.getElementById('continue-default-profile')?.value || 'syra-base',
  };
  if (nanoKey) body.continue_syra_nano_api_key = nanoKey;
  if (baseKey) body.continue_syra_base_api_key = baseKey;
  if (havyKey) body.continue_syra_havy_api_key = havyKey;
  if (internalSecret) body.syra_internal_secret = internalSecret;
  if (maxRaw) body.agent_max_count = parseInt(maxRaw, 10);
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const res = await api('/settings', { method: 'PUT', body: JSON.stringify(body) });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'Provider settings saved');
    if (nanoKey) document.getElementById('continue-nano-key').value = '';
    if (baseKey) document.getElementById('continue-base-key').value = '';
    if (havyKey) document.getElementById('continue-havy-key').value = '';
    if (internalSecret) document.getElementById('syra-internal-secret').value = '';
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
    const continueDefaultProfile = document.getElementById('continue-default-profile');
    const continueNanoKey = document.getElementById('continue-nano-key');
    const continueBaseKey = document.getElementById('continue-base-key');
    const continueHavyKey = document.getElementById('continue-havy-key');
    const agentMaxCount = document.getElementById('agent-max-count');
    const continueRuntimeStatus = document.getElementById('continue-runtime-status');
    const syraInternalSecret = document.getElementById('syra-internal-secret');
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
    if (continueDefaultProfile && s.continue_default_model_profile) continueDefaultProfile.value = s.continue_default_model_profile;
    if (agentMaxCount && s.agent_max_count) agentMaxCount.value = s.agent_max_count;
    if (agentMaxCount && !s.agent_max_count) agentMaxCount.placeholder = '50';
    const keyFields = [
      ['continue-nano-key', 'continue-nano-key-hint', s.continue_syra_nano_api_key_set, 'Verted nano key saved', 'Verted API key required'],
      ['continue-base-key', 'continue-base-key-hint', s.continue_syra_base_api_key_set, 'DeepSeek base key saved', 'DeepSeek API key required'],
      ['continue-havy-key', 'continue-havy-key-hint', s.continue_syra_havy_api_key_set, 'Verted havy key saved', 'Verted API key required'],
    ];
    keyFields.forEach(([inputId, hintId, saved, savedText, requiredText]) => {
      const input = document.getElementById(inputId);
      const hint = document.getElementById(hintId);
      if (input) {
        input.placeholder = saved ? 'key saved — enter new value to replace' : 'required';
      }
      if (hint) hint.textContent = saved ? savedText : requiredText;
    });
    aiApiConfigured = {
      nano: Boolean(s.continue_syra_nano_api_key_set),
      base: Boolean(s.continue_syra_base_api_key_set),
      havy: Boolean(s.continue_syra_havy_api_key_set),
    };
    if (syraInternalSecret) {
      syraInternalSecret.placeholder = s.syra_internal_secret_set
        ? 'internal secret saved — enter new value to replace'
        : 'shared secret for sycord.com -> Syte';
    }
    if (continueRuntimeStatus) {
      const parts = [];
      parts.push(`default: ${s.continue_default_model_profile || 'syra-base'}`);
      parts.push(s.continue_syra_nano_api_key_set ? 'nano key saved' : 'no nano key');
      parts.push(s.continue_syra_base_api_key_set ? 'base key saved' : 'no base key');
      parts.push(s.continue_syra_havy_api_key_set ? 'havy key saved' : 'no havy key');
      parts.push(s.syra_internal_secret_set ? 'internal secret saved' : 'no internal secret');
      continueRuntimeStatus.textContent = parts.join(' · ');
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
    const doneCount = ['internal_api', 'ai_models', 'provider', 'cli_server'].filter(k => onboard[k]).length;
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
    return `
      <div class="ai-debug-block">
        <strong>${esc(p.profile)}</strong> · ${esc(p.label)} · key: ${p.api_key_set ? esc(p.api_key_hint) : 'missing'}
        <div class="hint">${esc(p.api_base)} · ${esc(p.model)}</div>
        <table class="ai-debug-table">
          <thead><tr><th>Probe</th><th>Method</th><th>Result</th><th>HTTP</th><th>Time</th><th>Detail</th></tr></thead>
          <tbody>${probes || '<tr><td colspan="6">No probes — key not saved</td></tr>'}</tbody>
        </table>
      </div>
    `;
  }).join('');

  const hints = (report.hints || []).map(h => `<div class="ai-debug-hint">${esc(h)}</div>`).join('');
  const agent = report.agent || {};
  const config = report.config || {};

  el.innerHTML = `
    <div class="hint">Generated ${esc(report.generated_at || '')} · active profile <strong>${esc(report.active_profile || '')}</strong></div>
    <div class="ai-debug-steps">${steps || '<p class="hint">No steps recorded.</p>'}</div>
    ${hints ? `<div class="ai-debug-hints">${hints}</div>` : ''}
    <div><strong>Provider probes (all profiles)</strong>${profiles}</div>
    <div>
      <strong>Agent runtime</strong>
      <div class="hint">status ${esc(agent.agent_status || '—')} · port ${agent.agent_port ?? '—'} · CLI ${report.continue_cli?.installed ? esc(report.continue_cli.version || 'installed') : 'missing'}</div>
      ${agent.serve_command ? `<div class="hint">serve cmd: <code>${esc(agent.serve_command)}</code></div>` : ''}
      ${agent.agent_last_error ? `<div class="ai-debug-hint">${esc(agent.agent_last_error)}</div>` : ''}
    </div>
    ${config.snippet ? `<div><strong>config.yaml</strong><pre class="ai-debug-config">${esc(config.snippet)}</pre></div>` : ''}
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
    box.textContent = `Token (copy now):\n${res.token}`;
    box.classList.remove('hidden');
    setApiKey(res.token);
    toast('token created');
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
document.getElementById('debug-chat-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendDebugChatMessage();
  }
});
document.getElementById('debug-chat-clear')?.addEventListener('click', () => {
  clearDebugChatPanel();
  if (activeServiceId && activeSvcTab === 'debug-chat') {
    startAgentActivityStream(activeServiceId);
  }
});

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
