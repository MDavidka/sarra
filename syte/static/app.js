const API = '/api';
const API_KEY_STORAGE = 'syte_api_key';
const CONTEXT_STORAGE = 'syte_context';

let projects = [];
let logStream = null;
let previewStream = null;
let activeServiceId = null;
let deployPollTimer = null;
let previewPollTimer = null;
let projectFilterText = '';
let projectSortMode = 'newest';
let appContext = 'non-conected';
let statsPollTimer = null;
let activeSvcTab = 'general';
let logsAutoScroll = true;

const STACK_META = {
  nextjs: { label: 'next.js', icon: 'N', cls: '' },
  python: { label: 'python', icon: 'Py', cls: 'stack-python' },
  javascript: { label: 'javascript', icon: 'JS', cls: 'stack-javascript' },
  shell: { label: 'shell', icon: '$', cls: 'stack-shell' },
  docker: { label: 'docker', icon: 'D', cls: '' },
};

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

function showView(name) {
  if (name !== 'new-service' && name !== 'service') {
    stopLogStream();
    stopPreviewStream();
  }
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');

  const sidebarActive = (name === 'new-service' || name === 'service') ? 'dashboard' : name;
  document.querySelectorAll('.sidebar-link').forEach(el => {
    if (el.tagName === 'A') {
      el.classList.remove('active');
      return;
    }
    el.classList.toggle('active', el.dataset.view === sidebarActive);
  });

  if (name === 'users') loadTokens();
  if (name === 'dashboard') activeServiceId = null;
  if (name === 'server-swarm') renderServerSwarm();
  if (name === 'logs') renderLogsList();
  if (name === 'sycord') refreshIcons();
  if (name === 'service') {
    const p = projects.find(x => x.id === activeServiceId);
    setBreadcrumb(p?.name || 'Project');
  } else {
    setBreadcrumb(BREADCRUMBS[name] || 'Syte');
  }
  closeDrawer();
  refreshIcons();
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
      <span class="badge ${statusClass(p)}">${statusLabel(p)}</span>
    </div>
  `).join('');
  refreshIcons();
}

async function loadProjects() {
  try {
    projects = await api('/projects');
    renderServices();
    updateStats();
    if (activeServiceId) {
      const p = projects.find(x => x.id === activeServiceId);
      if (p) renderServiceDashboard(p, false);
    }
  } catch (e) {
    console.error(e);
  }
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
  list.innerHTML = visible.map(p => `
    <div class="service-card" onclick="openService('${p.id}')">
      <h3>${esc(p.name)}</h3>
      <div class="service-meta">
        <span class="badge ${statusClass(p)}">${statusLabel(p)}</span>
        <span class="badge badge-dim">:${p.port}</span>
        <span class="badge badge-dim">${p.deploy_type === 'docker' ? 'docker' : 'shell'}</span>
      </div>
      <div class="service-url">
        <a href="${esc(p.url)}" target="_blank" onclick="event.stopPropagation()">${esc(p.url)}</a>
      </div>
    </div>
  `).join('');
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

function connLabel(p) {
  if (p.domain) return p.domain;
  try {
    const u = new URL(p.url);
    return u.host;
  } catch {
    return p.url || '—';
  }
}

function switchSvcTab(tab) {
  activeSvcTab = tab;
  document.querySelectorAll('.svc-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.svcTab === tab);
  });
  document.querySelectorAll('.svc-tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.svcPanel === tab);
  });
  refreshIcons();
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

function renderServiceEmbed(p) {
  const frame = document.getElementById('svc-embed-frame');
  const placeholder = document.getElementById('svc-preview-placeholder');
  const conn = document.getElementById('svc-conn');
  if (conn) {
    conn.textContent = connLabel(p);
    conn.href = p.url || '#';
  }
  if (!frame || !placeholder) return;
  if (p.running && p.url) {
    frame.src = p.url;
    frame.classList.remove('hidden');
    placeholder.classList.add('hidden');
  } else {
    frame.src = 'about:blank';
    frame.classList.add('hidden');
    placeholder.classList.remove('hidden');
  }
}

function openService(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  activeServiceId = id;
  activeSvcTab = 'general';
  switchSvcTab('general');
  renderServiceDashboard(p, true);
  showView('service');
}

function renderServiceDashboard(p, resetLogs) {
  document.getElementById('svc-title').textContent = p.name;
  updateServiceStatusDot(p);
  renderStackBadge(p);
  renderServiceEmbed(p);

  const branchLabel = document.getElementById('svc-branch-label');
  if (branchLabel) branchLabel.textContent = p.branch || 'main';

  const uuidPill = document.getElementById('svc-uuid-pill');
  if (uuidPill) uuidPill.textContent = `UUID: ${p.id}`;

  const domainInput = document.getElementById('svc-domain-input');
  if (domainInput) domainInput.value = p.domain || '';

  const envInput = document.getElementById('svc-env-input');
  if (envInput) envInput.value = formatEnv(p.env_vars);

  document.getElementById('svc-info-body').innerHTML = `
    <div class="info-cell"><span>status</span><strong>${esc(statusLabel(p))}</strong></div>
    <div class="info-cell"><span>type</span><strong>${esc(p.deploy_type || 'shell')}</strong></div>
    <div class="info-cell"><span>port</span><strong>${p.port}</strong></div>
    <div class="info-cell"><span>stack</span><strong>${esc(detectStack(p))}</strong></div>
    <div class="info-cell full"><span>url</span><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></div>
    <div class="info-cell full"><span>git</span><span>${esc(p.git_url || '—')}</span></div>
    <div class="info-cell"><span>branch</span><strong>${esc(p.branch || 'main')}</strong></div>
    <div class="info-cell"><span>start cmd</span><span>${esc(p.start_command || '—')}</span></div>
  `;

  document.getElementById('svc-workspace-body').innerHTML = `
    <div class="info-cell full"><span>workspace</span><code>${esc(p.workspace_path || '—')}</code></div>
    <div class="info-cell full"><span>app</span><code>${esc(p.app_path || '—')}</code></div>
    <div class="info-cell full"><span>data</span><code>${esc(p.data_path || '—')}</code></div>
  `;
  loadWorkspaceFiles(p.id);
  renderPreviewSection(p);

  const actions = document.getElementById('svc-deploy-actions');
  actions.innerHTML = `
    <button class="btn-pill btn-primary" onclick="serviceDeploy('${p.id}')">
      <i data-lucide="rocket"></i><span>Deploy</span>
    </button>
    ${p.running
      ? `<button class="btn-pill btn-ghost" onclick="serviceAction('${p.id}','stop')"><i data-lucide="square"></i><span>Stop</span></button>`
      : `<button class="btn-pill btn-ghost" onclick="serviceAction('${p.id}','start')"><i data-lucide="play"></i><span>Start</span></button>`
    }
    <button class="btn-pill btn-danger" onclick="serviceAction('${p.id}','delete')">
      <i data-lucide="trash-2"></i><span>Remove</span>
    </button>
  `;

  const deployMeta = document.getElementById('svc-deploy-meta');
  if (deployMeta) {
    deployMeta.innerHTML = p.status === 'deploying'
      ? '<span class="badge badge-deploying">deployment in progress</span>'
      : p.running
        ? '<span class="badge badge-running">container running</span>'
        : '<span class="badge badge-stopped">not running</span>';
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
  } else {
    updateServiceStatusDot(p);
    renderServiceEmbed(p);
    if (deployMeta) {
      deployMeta.innerHTML = p.status === 'deploying'
        ? '<span class="badge badge-deploying">deployment in progress</span>'
        : p.running
          ? '<span class="badge badge-running">container running</span>'
          : '<span class="badge badge-stopped">not running</span>';
    }
  }

  document.getElementById('svc-domain-btn').onclick = () => saveServiceDomain(p.id);
  document.getElementById('svc-env-save-btn').onclick = () => saveServiceEnv(p.id);
  document.getElementById('svc-edit-name-btn').onclick = () => editServiceName(p.id);
  refreshIcons();
}

function renderPreviewSection(p) {
  const actions = document.getElementById('svc-preview-actions');
  const wrap = document.getElementById('svc-preview-wrap');
  const frame = document.getElementById('svc-preview-frame');
  const hint = document.getElementById('svc-preview-hint');
  const logsEl = document.getElementById('svc-preview-logs');
  const logsWrap = document.getElementById('svc-preview-logs-wrap');
  if (!actions) return;

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
    wrap?.classList.remove('hidden');
    if (frame && live) frame.src = p.preview_domain_url || p.preview_url;
    const urlLabel = p.preview_domain
      ? `${p.preview_domain_url || p.preview_url}`
      : p.preview_url;
    hint.textContent = live
      ? `Live — ${urlLabel}${p.preview_domain ? ' (HTTPS)' : ''}`
      : `Starting on ${p.preview_domain || `port ${p.preview_port || '…'}`}`;
    logsWrap?.classList.remove('hidden');
    if (p.preview_running && !previewStream) startPreviewLogStream(p.id, logsEl);
    if (p.preview_running && !p.preview_ready) startPreviewPoll(p.id);
  } else {
    wrap?.classList.add('hidden');
    if (frame) frame.src = 'about:blank';
    hint.textContent = 'Fast dev server with hot reload — no docker build';
    logsWrap?.classList.add('hidden');
    stopPreviewStream();
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
  if (previewPollTimer) return;
  previewPollTimer = setInterval(async () => {
    try {
      const st = await api(`/projects/${projectId}/preview/status`);
      await loadProjects();
      const p = projects.find(x => x.id === projectId);
      if (p && activeServiceId === projectId) {
        renderPreviewSection(p);
        if (st.preview_ready) stopPreviewPoll();
      }
    } catch { /* */ }
  }, 1500);
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
    await loadProjects();
    const p = projects.find(x => x.id === id);
    if (p) renderPreviewSection(p);
    if (logsEl) startPreviewLogStream(id, logsEl);
    startPreviewPoll(id);
  } catch (e) {
    hint.textContent = 'preview failed';
    if (logsEl) appendLogLine(logsEl, 'Error: ' + e.message, 'log-err');
    toast('Error: ' + e.message);
  }
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

async function loadWorkspaceFiles(projectId, subpath = '') {
  const el = document.getElementById('svc-workspace-files');
  if (!el) return;
  el.innerHTML = '<span class="ws-empty">loading…</span>';
  try {
    const q = subpath ? `?path=${encodeURIComponent(subpath)}` : '';
    const res = await api(`/projects/${projectId}/workspace/files${q}`);
    if (!res.files?.length) {
      el.innerHTML = '<span class="ws-empty">empty workspace — add files via API or deploy</span>';
      return;
    }
    el.innerHTML = res.files.map(f => `
      <div class="ws-file-row">
        <span class="${f.type === 'directory' ? 'ws-dir' : ''}">${f.type === 'directory' ? '📁' : '📄'} ${esc(f.name)}</span>
        <span>${f.type === 'file' && f.size != null ? formatBytes(f.size) : ''}</span>
      </div>
    `).join('');
  } catch (e) {
    el.innerHTML = `<span class="ws-empty">could not list files: ${esc(e.message)}</span>`;
  }
}

function formatBytes(n) {
  if (n < 1024) return n + ' B';
  if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
  return (n / (1024 * 1024)).toFixed(1) + ' MB';
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
    loadWorkspaceFiles(id);
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

async function saveServiceDomain(id) {
  let domain = document.getElementById('svc-domain-input')?.value.trim() || '';
  domain = domain.replace(/^https?:\/\//i, '').replace(/\/.*$/, '');
  if (!domain) return toast('Enter a domain');
  try {
    const email = (await api('/settings')).admin_email;
    const res = await api(`/projects/${id}/domain`, {
      method: 'POST',
      body: JSON.stringify({ domain, email: email || 'admin@localhost' }),
    });
    toast(res.message || 'Domain applied');
    await loadProjects();
    const p = projects.find(x => x.id === id);
    if (p) renderServiceDashboard(p, false);
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

async function editServiceName(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  const name = prompt('Project name', p.name);
  if (!name || name.trim() === p.name) return;
  try {
    await api(`/projects/${id}`, {
      method: 'PUT',
      body: JSON.stringify({ name: name.trim() }),
    });
    toast('Project renamed');
    await loadProjects();
    const updated = projects.find(x => x.id === id);
    if (updated) {
      renderServiceDashboard(updated, false);
      setBreadcrumb(updated.name);
    }
  } catch (e) {
    toast('Error: ' + e.message);
  }
}

document.getElementById('create-form')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const btn = document.getElementById('deploy-btn');
  btn.disabled = true;
  btn.textContent = 'deploying…';

  const body = {
    name: document.getElementById('svc-name').value,
    git_url: document.getElementById('svc-git').value || null,
    branch: document.getElementById('svc-branch').value || 'main',
    start_command: document.getElementById('svc-cmd').value || null,
    domain: document.getElementById('svc-domain').value || null,
    env_vars: parseEnv(document.getElementById('svc-env').value),
  };

  const logPanel = document.getElementById('deploy-log-panel');
  logPanel?.classList.remove('hidden');
  clearLogPanel(logPanel);

  try {
    const res = await api('/projects', { method: 'POST', body: JSON.stringify(body) });
    toast(`deploying: ${res.project.name}`);
    await loadProjects();
    openService(res.project.id);
    const logsEl = document.getElementById('svc-live-logs');
    loadLogSnapshot(res.project.id, logsEl).then(() => {
      startLogStream(res.project.id, logsEl, { liveOnly: true, clearFirst: false });
    });
  } catch (err) {
    if (logPanel) appendLogLine(logPanel, 'Error: ' + err.message, 'log-err');
    toast('deploy failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'deploy';
  }
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

document.getElementById('update-syte-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('update-syte-btn');
  btn.disabled = true;
  btn.textContent = 'updating…';
  try {
    const res = await api('/system/update', { method: 'POST' });
    const box = document.getElementById('update-result');
    box.textContent = res.message;
    box.classList.remove('hidden');
    toast('syte is updating…');
  } catch (e) {
    toast('update failed: ' + e.message);
    btn.disabled = false;
    btn.textContent = 'update syte';
  }
});

async function loadSettings() {
  try {
    const s = await api('/settings');
    const ip = document.getElementById('set-ip');
    const email = document.getElementById('set-email');
    const domain = document.getElementById('set-domain');
    if (ip && s.public_ip) ip.value = s.public_ip;
    if (email && s.admin_email) email.value = s.admin_email;
    if (domain && s.gui_domain) domain.value = s.gui_domain.replace(/^https?:\/\//i, '');
    const directUrl = document.getElementById('direct-url');
    const guiUrl = document.getElementById('gui-url');
    const ver = document.getElementById('syte-version');
    if (directUrl && s.direct_url) directUrl.textContent = s.direct_url;
    if (guiUrl) guiUrl.textContent = s.domain_url || 'not configured';
    if (ver && s.version) ver.textContent = 'v' + s.version;
  } catch { /* */ }
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

document.getElementById('project-sort')?.addEventListener('change', (e) => {
  projectSortMode = e.target.value;
  renderServices();
});

document.getElementById('sort-toggle')?.addEventListener('click', () => {
  const sel = document.getElementById('project-sort');
  if (!sel) return;
  const opts = ['newest', 'oldest', 'name'];
  const idx = opts.indexOf(sel.value);
  sel.value = opts[(idx + 1) % opts.length];
  projectSortMode = sel.value;
  renderServices();
});

document.querySelectorAll('.sidebar-link[data-view]').forEach(el => {
  if (el.tagName === 'A') return;
  el.addEventListener('click', () => showView(el.dataset.view));
});
document.getElementById('sidebar-toggle')?.addEventListener('click', openDrawer);
document.getElementById('sidebar-backdrop')?.addEventListener('click', closeDrawer);

document.getElementById('svc-tabs')?.addEventListener('click', (e) => {
  const btn = e.target.closest('.svc-tab');
  if (!btn?.dataset.svcTab) return;
  switchSvcTab(btn.dataset.svcTab);
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

document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') closeDrawer();
});

window.addEventListener('resize', () => {
  if (!window.matchMedia('(max-width: 768px)').matches) closeDrawer();
});
