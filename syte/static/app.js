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
  if (name === 'ai') { loadSettings(); loadAiDashboard(); }
  if (name === 'settings') loadSettings();
  const aiSettingsBtn = document.getElementById('ai-header-settings-btn');
  if (aiSettingsBtn) aiSettingsBtn.classList.toggle('hidden', name !== 'ai');
  if (name === 'sycord') refreshIcons();
  if (name === 'new-service') resetCreateForm();
  if (name === 'service') {
    const p = projects.find(x => x.id === activeServiceId);
    setBreadcrumb(p ? displayTitle(p) : 'Project');
  } else {
    setBreadcrumb(BREADCRUMBS[name] || 'Syte');
  }
  closeDrawer();
  refreshIcons();
}

let aiApiConfigured = false;

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

function updateAiApiWarning() {
  const warn = document.getElementById('ai-api-warning');
  const bridgeBase = document.getElementById('continue-bridge-base')?.value?.trim();
  const keySet = aiApiConfigured || document.getElementById('continue-bridge-key')?.placeholder?.includes('saved');
  const ok = Boolean(bridgeBase) && keySet;
  if (warn) warn.classList.toggle('hidden', ok);
  return ok;
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
  list.innerHTML = visible.map(p => `
    <div class="service-card" onclick="openService('${p.id}')">
      <h3>${esc(p.name)}</h3>
      <div class="service-meta">
        <span class="badge ${statusClass(p)}">${statusLabel(p)}</span>
        ${sslBadgeHtml(p)}
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
  const allowed = ['general', 'env', 'logs', 'preview'];
  if (!allowed.includes(tab)) tab = 'general';
  activeSvcTab = tab;
  document.querySelectorAll('.svc-tab').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.svcTab === tab);
  });
  document.querySelectorAll('.svc-tab-panel').forEach(panel => {
    panel.classList.toggle('active', panel.dataset.svcPanel === tab);
  });
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

function renderServiceEmbed(p) {
  const frame = document.getElementById('svc-embed-frame');
  const placeholder = document.getElementById('svc-preview-placeholder');
  const conn = document.getElementById('svc-conn');
  if (conn) {
    const link = (p.preview_running && p.preview_ready && p.preview_url)
      ? (p.preview_domain_url || p.preview_url)
      : p.url;
    conn.textContent = connLabel(p);
    conn.href = link || '#';
  }
  if (!frame || !placeholder) return;
  const previewLive = p.preview_running && p.preview_ready && p.preview_url;
  const embedUrl = previewLive
    ? (p.preview_domain_url || p.preview_url)
    : (p.running && p.url ? p.url : null);
  if (embedUrl) {
    if (frame.src !== embedUrl) frame.src = embedUrl;
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
  document.getElementById('svc-title').textContent = displayTitle(p);
  updateServiceStatusDot(p);
  renderStackBadge(p);
  renderServiceEmbed(p);

  const branchLabel = document.getElementById('svc-branch-label');
  if (branchLabel) branchLabel.textContent = p.branch || 'main';

  const uuidPill = document.getElementById('svc-uuid-pill');
  if (uuidPill) uuidPill.textContent = `UUID: ${p.id}`;

  const envInput = document.getElementById('svc-env-input');
  if (envInput) envInput.value = formatEnv(p.env_vars);

  renderQuickActions(p);

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

  document.getElementById('svc-workspace-body').innerHTML = `
    <div class="info-cell full"><span>workspace</span><code>${esc(p.workspace_path || '—')}</code></div>
    <div class="info-cell full"><span>app</span><code>${esc(p.app_path || '—')}</code></div>
    <div class="info-cell full"><span>data</span><code>${esc(p.data_path || '—')}</code></div>
  `;
  loadWorkspaceFiles(p.id);
  renderPreviewSection(p);

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
    if (frame && live) {
      const frameSrc = (p.preview_tls_ok !== false && p.preview_domain_url)
        ? p.preview_domain_url
        : (p.preview_fetch_url || p.preview_url);
      frame.src = frameSrc;
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
        renderPreviewSection({ ...p, iframe: st.iframe });
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
    if (p) renderPreviewSection({ ...p, iframe: res.iframe });
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
  const bridgeBase = document.getElementById('continue-bridge-base')?.value?.trim() || '';
  const bridgeKey = document.getElementById('continue-bridge-key')?.value?.trim() || '';
  const internalSecret = document.getElementById('syra-internal-secret')?.value?.trim() || '';
  const maxRaw = document.getElementById('agent-max-count')?.value?.trim();
  if (!bridgeBase) return toast('Bridge API URL is required');
  if (!bridgeKey && !aiApiConfigured) return toast('Provider API key is required');
  const body = {
    continue_bridge_api_base: bridgeBase,
    continue_provider: document.getElementById('continue-provider')?.value || 'openai',
    continue_default_model_profile: document.getElementById('continue-default-profile')?.value || 'syra-base',
    continue_syra_nano_model: document.getElementById('continue-model-nano')?.value?.trim() || '',
    continue_syra_base_model: document.getElementById('continue-model-base')?.value?.trim() || '',
    continue_syra_havy_model: document.getElementById('continue-model-havy')?.value?.trim() || '',
  };
  if (bridgeKey) body.continue_bridge_api_key = bridgeKey;
  if (internalSecret) body.syra_internal_secret = internalSecret;
  if (maxRaw) body.agent_max_count = parseInt(maxRaw, 10);
  btn.disabled = true;
  btn.textContent = 'saving…';
  try {
    const res = await api('/settings', { method: 'PUT', body: JSON.stringify(body) });
    toast(Array.isArray(res.messages) ? res.messages.join(' ') : 'Provider settings saved');
    if (bridgeKey) document.getElementById('continue-bridge-key').value = '';
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
    const continueBridgeBase = document.getElementById('continue-bridge-base');
    const continueBridgeKey = document.getElementById('continue-bridge-key');
    const continueDefaultProfile = document.getElementById('continue-default-profile');
    const continueModelNano = document.getElementById('continue-model-nano');
    const continueModelBase = document.getElementById('continue-model-base');
    const continueModelHavy = document.getElementById('continue-model-havy');
    const continueProvider = document.getElementById('continue-provider');
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
    if (continueBridgeBase) continueBridgeBase.value = s.continue_bridge_api_base || '';
    if (continueDefaultProfile && s.continue_default_model_profile) continueDefaultProfile.value = s.continue_default_model_profile;
    if (continueModelNano) continueModelNano.value = s.continue_syra_nano_model || '';
    if (continueModelBase) continueModelBase.value = s.continue_syra_base_model || '';
    if (continueModelHavy) continueModelHavy.value = s.continue_syra_havy_model || '';
    if (continueProvider && s.continue_provider) continueProvider.value = s.continue_provider;
    if (agentMaxCount && s.agent_max_count) agentMaxCount.value = s.agent_max_count;
    if (agentMaxCount && !s.agent_max_count) agentMaxCount.placeholder = '50';
    if (continueBridgeKey) {
      continueBridgeKey.placeholder = s.continue_bridge_api_key_set
        ? 'key saved — enter new value to replace'
        : 'sk-… required';
    }
    aiApiConfigured = Boolean(s.continue_bridge_api_base) && Boolean(s.continue_bridge_api_key_set);
    const keyHint = document.getElementById('continue-bridge-key-hint');
    if (keyHint) {
      keyHint.textContent = s.continue_bridge_api_key_set
        ? 'API key saved on server'
        : 'Required — agents cannot call models without this';
    }
    if (syraInternalSecret) {
      syraInternalSecret.placeholder = s.syra_internal_secret_set
        ? 'internal secret saved — enter new value to replace'
        : 'shared secret for sycord.com -> Syte';
    }
    if (continueRuntimeStatus) {
      const parts = [];
      if (s.continue_bridge_api_base) parts.push(`bridge: ${s.continue_bridge_api_base}`);
      parts.push(`default: ${s.continue_default_model_profile || 'syra-base'}`);
      parts.push(s.continue_bridge_api_key_set ? 'bridge key saved' : 'no bridge key');
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
  } catch { /* */ }
}

function appendAiChatMsg(role, text) {
  const log = document.getElementById('ai-chat-log');
  if (!log) return;
  const div = document.createElement('div');
  div.className = `ai-chat-msg ${role}`;
  div.textContent = (role === 'user' ? 'You: ' : role === 'agent' ? 'Agent: ' : '') + text;
  log.appendChild(div);
  log.scrollTop = log.scrollHeight;
}

function renderAiChatProjects() {
  const sel = document.getElementById('ai-chat-project');
  if (!sel) return;
  const current = sel.value;
  sel.innerHTML = '<option value="">Select project…</option>' +
    projects.map(p => `<option value="${esc(p.id)}">${esc(displayTitle(p))}</option>`).join('');
  if (current) sel.value = current;
}

async function loadAiDashboard() {
  renderAiChatProjects();
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
    if (hint) {
      hint.textContent = onboard.complete
        ? 'Ready for sycord.com agent requests'
        : 'Tap settings (top right) to set provider API';
    }
    updateAiApiWarning();
  } catch { /* */ }
  refreshIcons();
}

async function sendAiChat() {
  const uuid = document.getElementById('ai-chat-project')?.value;
  const message = document.getElementById('ai-chat-input')?.value?.trim();
  const profile = document.getElementById('ai-chat-profile')?.value;
  const statusEl = document.getElementById('ai-chat-status');
  if (!uuid) return toast('select a project first');
  if (!message) return;
  if (!updateAiApiWarning()) {
    toast('Configure provider API first');
    openAiSettings();
    return;
  }
  appendAiChatMsg('user', message);
  document.getElementById('ai-chat-input').value = '';
  if (statusEl) statusEl.textContent = 'Agent thinking…';
  const btn = document.getElementById('ai-chat-send-btn');
  if (btn) btn.disabled = true;
  try {
    const res = await api(`/projects/${uuid}/agent/chat`, {
      method: 'POST',
      body: JSON.stringify({ message, model_profile: profile }),
    });
    if (res.ok && res.reply) {
      appendAiChatMsg('agent', res.reply);
      if (statusEl) statusEl.textContent = `Model: ${res.model || '—'} · Provider shown in agent response only`;
    } else {
      appendAiChatMsg('system', res.message || 'No reply from agent');
      if (statusEl) statusEl.textContent = res.message || 'Communication failed';
    }
    await loadAiDashboard();
  } catch (e) {
    appendAiChatMsg('system', e.message);
    if (statusEl) statusEl.textContent = e.message;
  } finally {
    if (btn) btn.disabled = false;
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

document.getElementById('ai-header-settings-btn')?.addEventListener('click', openAiSettings);
document.getElementById('ai-settings-close')?.addEventListener('click', closeAiSettings);
document.getElementById('ai-settings-backdrop')?.addEventListener('click', closeAiSettings);

document.getElementById('ai-chat-send-btn')?.addEventListener('click', sendAiChat);
document.getElementById('ai-chat-input')?.addEventListener('keydown', (e) => {
  if (e.key === 'Enter' && !e.shiftKey) {
    e.preventDefault();
    sendAiChat();
  }
});

document.getElementById('ai-test-agent-btn')?.addEventListener('click', async () => {
  const uuid = document.getElementById('ai-chat-project')?.value;
  if (!uuid) return toast('select a project first');
  if (!updateAiApiWarning()) {
    toast('Configure provider API first');
    openAiSettings();
    return;
  }
  appendAiChatMsg('system', 'Running agent test…');
  try {
    const res = await api(`/projects/${uuid}/agent/test`, { method: 'POST', body: '{}' });
    if (res.ok) {
      appendAiChatMsg('system', `Test passed — reply: ${res.reply || 'ok'}`);
      toast('Agent test passed');
    } else {
      appendAiChatMsg('system', res.message || 'Test failed');
      toast(res.message || 'Test failed');
    }
    await loadAiDashboard();
  } catch (e) {
    appendAiChatMsg('system', e.message);
    toast('Error: ' + e.message);
  }
});

document.getElementById('ai-start-agent-btn')?.addEventListener('click', async () => {
  const uuid = document.getElementById('ai-chat-project')?.value;
  if (!uuid) return toast('select a project first');
  try {
    const res = await api(`/projects/${uuid}/agent/start`, { method: 'POST', body: '{}' });
    appendAiChatMsg('system', res.message || 'Agent started');
    toast(res.message || 'Agent started');
    await loadAiDashboard();
  } catch (e) {
    toast('Error: ' + e.message);
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
