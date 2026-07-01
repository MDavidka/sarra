const API = '/api';

let projects = [];

function injectLogos() {
  /* brand icon is served from /static/icon.png */
}

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
}

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');
  document.querySelectorAll('[data-view]').forEach(el => {
    el.classList.toggle('active', el.dataset.view === name);
  });
  refreshIcons();
}

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    const detail = err.detail;
    const message = Array.isArray(detail)
      ? detail.map(d => d.msg || d).join(', ')
      : (detail || res.statusText);
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
    if (guiUrl) {
      guiUrl.textContent = sys.domain_url || 'not configured';
    }
    const ver = document.getElementById('syte-version');
    if (ver) ver.textContent = 'v' + sys.version;
  } catch (e) { /* offline */ }
}

async function loadProjects() {
  try {
    projects = await api('/projects');
    renderServices();
    updateStats();
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
        <span class="badge ${p.running ? 'badge-running' : 'badge-stopped'}">
          ${p.running ? 'running' : 'stopped'}
        </span>
        <span class="badge" style="background:#2a2a2a;color:#8e8e8e">:${p.port}</span>
        <span class="badge" style="background:#2a2a2a;color:#8e8e8e">${p.deploy_type === 'docker' ? 'docker' : 'shell'}</span>
      </div>
      <div class="service-url">
        <a href="${esc(p.url)}" target="_blank" onclick="event.stopPropagation()">${esc(p.url)}</a>
      </div>
    </div>
  `).join('');
}

async function openService(id) {
  const p = projects.find(x => x.id === id);
  if (!p) return;

  document.getElementById('modal-title').textContent = p.name;
  document.getElementById('modal-body').innerHTML = `
    <div class="detail-row"><span>status</span><span>${p.running ? 'running' : 'stopped'}</span></div>
    <div class="detail-row"><span>url</span><span><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></span></div>
    <div class="detail-row"><span>port</span><span>${p.port}</span></div>
    <div class="detail-row"><span>domain</span><span>${esc(p.domain || '—')}</span></div>
    <div class="detail-row"><span>git</span><span style="max-width:60%;word-break:break-all;text-align:right">${esc(p.git_url || '—')}</span></div>
    <div class="detail-row"><span>branch</span><span>${esc(p.branch)}</span></div>
    <div class="detail-row"><span>deploy</span><span>${esc(p.deploy_type || 'shell')}${p.dockerfile_path ? ' (' + esc(p.dockerfile_path) + ')' : ''}</span></div>
    <div class="detail-row"><span>workspace</span><span style="font-size:0.72rem;text-align:right">/var/lib/syte/workspaces/${esc(p.id)}</span></div>
    <div class="logs-box" id="modal-logs">loading logs…</div>
  `;

  const actions = document.getElementById('modal-actions');
  actions.innerHTML = `
    ${p.running
      ? `<button class="btn-pill btn-ghost btn-sm" onclick="serviceAction('${id}','stop')">stop</button>`
      : `<button class="btn-pill btn-primary btn-sm" onclick="serviceAction('${id}','start')">start</button>`
    }
    <button class="btn-pill btn-primary btn-sm" onclick="serviceAction('${id}','update')">pull & restart</button>
    <button class="btn-pill btn-danger btn-sm" onclick="serviceAction('${id}','delete')">remove</button>
  `;

  document.getElementById('modal').classList.remove('hidden');
  refreshIcons();

  try {
    const { logs } = await api(`/projects/${id}/logs`);
    document.getElementById('modal-logs').textContent = logs;
  } catch {
    document.getElementById('modal-logs').textContent = 'could not load logs.';
  }
}

function closeModal() {
  document.getElementById('modal').classList.add('hidden');
}

async function serviceAction(id, action) {
  try {
    if (action === 'delete') {
      if (!confirm('Remove this service? Workspace data is kept on disk.')) return;
      const res = await api(`/projects/${id}`, { method: 'DELETE' });
      toast(res.message);
      closeModal();
    } else {
      const res = await api(`/projects/${id}/${action}`, { method: 'POST' });
      toast(res.message);
    }
    await loadProjects();
    if (action !== 'delete') openService(id);
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

  try {
    const res = await api('/projects', { method: 'POST', body: JSON.stringify(body) });
    const box = document.getElementById('deploy-result');
    box.textContent = res.message;
    box.classList.remove('hidden');
    toast(`deployed: ${res.project.name}`);
    await loadProjects();
    setTimeout(() => showView('dashboard'), 1500);
  } catch (err) {
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
    const msg = Array.isArray(res.messages) ? res.messages.join(' ') : 'domain applied';
    toast(msg);
    if (res.direct_url) {
      document.getElementById('direct-url').textContent = res.direct_url;
    }
    if (res.domain_url) {
      document.getElementById('gui-url').textContent = res.domain_url;
    }
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
    toast('syte is updating and will restart…');
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
    const directUrl = document.getElementById('direct-url');
    const guiUrl = document.getElementById('gui-url');
    const ver = document.getElementById('syte-version');
    if (ip && s.public_ip) ip.value = s.public_ip;
    if (email && s.admin_email) email.value = s.admin_email;
    if (domain && s.gui_domain) domain.value = s.gui_domain.replace(/^https?:\/\//i, '');
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

loadSystem();
loadProjects();
loadSettings();
refreshIcons();
