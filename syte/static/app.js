const API = '/api';
const API_KEY_STORAGE = 'syte_api_key';

let projects = [];
let logStream = null;
let activeServiceId = null;
let deployPollTimer = null;

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
  container.scrollTop = container.scrollHeight;
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
  logStream.onmessage = (e) => {
    try {
      const msg = JSON.parse(e.data);
      if (msg.text) appendLogLine(targetEl, msg.text, msg.type);
    } catch { /* ping */ }
  };
  logStream.onerror = () => {
    appendLogLine(targetEl, '[stream disconnected — showing saved logs]', 'log-warn');
    stopLogStream();
    loadLogSnapshot(projectId, targetEl);
  };

  deployPollTimer = setInterval(async () => {
    await loadProjects();
    const p = projects.find(x => x.id === projectId);
    if (!p || activeServiceId !== projectId) return;
    updateServiceBadge(p);
    if (p.status === 'deploying') {
      wasDeploying = true;
      if (hint) hint.textContent = 'deployment in progress…';
      return;
    }
    if (wasDeploying) {
      wasDeploying = false;
      if (hint) hint.textContent = 'deploy finished — full log below';
      await loadLogSnapshot(projectId, targetEl, true);
      stopLogStream();
    }
  }, 2000);
}

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
}

function showView(name) {
  if (name !== 'new-service' && name !== 'service') stopLogStream();
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');
  document.querySelectorAll('.nav-item[data-view]').forEach(el => {
    el.classList.toggle('active', el.dataset.view === name);
  });
  if (name === 'api-keys') loadTokens();
  if (name === 'dashboard') activeServiceId = null;
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
  const total = document.getElementById('stat-total');
  const run = document.getElementById('stat-running');
  if (total) total.textContent = projects.length;
  if (run) run.textContent = running;
}

async function loadSystem() {
  try {
    const sys = await api('/system');
    const ipEl = document.getElementById('sys-ip');
    if (ipEl) ipEl.textContent = sys.public_ip;
    const ipInput = document.getElementById('set-ip');
    if (ipInput && !ipInput.value) ipInput.placeholder = sys.public_ip;
    const directUrl = document.getElementById('direct-url');
    if (directUrl) directUrl.textContent = sys.direct_url;
    const guiUrl = document.getElementById('gui-url');
    if (guiUrl) guiUrl.textContent = sys.domain_url || 'not configured';
    const ver = document.getElementById('syte-version');
    if (ver) ver.textContent = 'v' + sys.version;
  } catch { /* offline */ }
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
  const footer = document.getElementById('panel-footer');

  if (!projects.length) {
    list.innerHTML = '';
    empty?.classList.remove('hidden');
    footer?.classList.add('hidden');
    return;
  }

  empty?.classList.add('hidden');
  footer?.classList.remove('hidden');
  list.innerHTML = projects.map(p => `
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
}

function statusClass(p) {
  if (p.status === 'deploying') return 'badge-deploying';
  return p.running ? 'badge-running' : 'badge-stopped';
}

function statusLabel(p) {
  if (p.status === 'deploying') return 'deploying';
  return p.running ? 'running' : 'stopped';
}

function updateServiceBadge(p) {
  const badge = document.getElementById('svc-status-badge');
  if (!badge) return;
  badge.className = `badge ${statusClass(p)}`;
  badge.textContent = statusLabel(p);
}

function openService(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;
  activeServiceId = id;
  renderServiceDashboard(p, true);
  showView('service');
}

function renderServiceDashboard(p, resetLogs) {
  document.getElementById('svc-title').textContent = p.name;
  updateServiceBadge(p);

  const domainInput = document.getElementById('svc-domain-input');
  if (domainInput) domainInput.value = p.domain || '';

  document.getElementById('svc-info-body').innerHTML = `
    <div class="info-cell"><span>url</span><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></div>
    <div class="info-cell"><span>port</span><strong>${p.port}</strong></div>
    <div class="info-cell"><span>type</span><strong>${esc(p.deploy_type || 'shell')}</strong></div>
    <div class="info-cell"><span>branch</span><strong>${esc(p.branch || 'main')}</strong></div>
    <div class="info-cell full"><span>git</span><span>${esc(p.git_url || '—')}</span></div>
    <div class="info-cell full"><span>uuid</span><code>${esc(p.id)}</code></div>
  `;

  document.getElementById('svc-workspace-body').innerHTML = `
    <div class="info-cell full"><span>workspace</span><code>${esc(p.workspace_path || '—')}</code></div>
    <div class="info-cell full"><span>app</span><code>${esc(p.app_path || '—')}</code></div>
    <div class="info-cell full"><span>data</span><code>${esc(p.data_path || '—')}</code></div>
  `;
  loadWorkspaceFiles(p.id);

  const actions = document.getElementById('svc-deploy-actions');
  actions.innerHTML = `
    <button class="btn-pill btn-primary" onclick="serviceDeploy('${p.id}')">
      <i data-lucide="rocket"></i><span>deploy</span>
    </button>
    ${p.running
      ? `<button class="btn-pill btn-ghost" onclick="serviceAction('${p.id}','stop')"><i data-lucide="square"></i><span>stop</span></button>`
      : `<button class="btn-pill btn-ghost" onclick="serviceAction('${p.id}','start')"><i data-lucide="play"></i><span>start</span></button>`
    }
    <button class="btn-pill btn-danger" onclick="serviceAction('${p.id}','delete')">
      <i data-lucide="trash-2"></i><span>remove</span>
    </button>
  `;

  const logsEl = document.getElementById('svc-live-logs');
  const hint = document.getElementById('svc-log-hint');
  if (resetLogs) {
    if (p.status === 'deploying') {
      hint.textContent = 'deployment in progress…';
      loadLogSnapshot(p.id, logsEl).then(() => {
        startLogStream(p.id, logsEl, { liveOnly: true, clearFirst: false });
      });
    } else {
      hint.textContent = 'full deploy history — failures and build output';
      stopLogStream();
      loadLogSnapshot(p.id, logsEl);
    }
  }

  document.getElementById('svc-domain-btn').onclick = () => saveServiceDomain(p.id);
  refreshIcons();
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
  hint.textContent = 'deployment in progress…';
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
      document.getElementById('svc-log-hint').textContent = 'starting service…';
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
  if (!domain) return toast('enter a domain');
  try {
    const email = (await api('/settings')).admin_email;
    const res = await api(`/projects/${id}/domain`, {
      method: 'POST',
      body: JSON.stringify({ domain, email: email || 'admin@localhost' }),
    });
    toast(res.message || 'domain applied');
    await loadProjects();
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
refreshIcons();
