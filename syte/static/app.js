const API = '/api';

let projects = [];

async function api(path, opts = {}) {
  const res = await fetch(API + path, {
    headers: { 'Content-Type': 'application/json' },
    ...opts,
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail || res.statusText);
  }
  return res.json();
}

function showView(name) {
  document.querySelectorAll('.view').forEach(v => v.classList.remove('active'));
  document.querySelectorAll('.nav-item').forEach(n => n.classList.remove('active'));
  document.getElementById('view-' + name)?.classList.add('active');
  document.querySelector(`[data-view="${name}"]`)?.classList.add('active');
  refreshIcons();
}

function refreshIcons() {
  if (window.lucide) lucide.createIcons();
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

async function loadSystem() {
  try {
    const sys = await api('/system');
    document.getElementById('sys-ip').textContent = sys.public_ip;
    const ipInput = document.getElementById('set-ip');
    if (ipInput && !ipInput.value) ipInput.placeholder = sys.public_ip;
    const guiUrl = document.getElementById('gui-url');
    if (guiUrl) guiUrl.textContent = sys.gui_url;
    const ver = document.getElementById('syte-version');
    if (ver) ver.textContent = 'v' + sys.version;
  } catch (e) { /* offline */ }
}

async function loadProjects() {
  try {
    projects = await api('/projects');
    renderServices();
  } catch (e) {
    console.error(e);
  }
}

function renderServices() {
  const list = document.getElementById('services-list');
  const empty = document.getElementById('empty-state');

  if (!projects.length) {
    list.innerHTML = '';
    empty.classList.remove('hidden');
    return;
  }

  empty.classList.add('hidden');
  list.innerHTML = projects.map(p => `
    <div class="service-card" onclick="openService('${p.id}')">
      <h3>${esc(p.name)}</h3>
      <div class="service-meta">
        <span class="badge ${p.running ? 'badge-running' : 'badge-stopped'}">
          ${p.running ? 'Running' : 'Stopped'}
        </span>
        <span class="badge" style="background:#222;color:#aaa">:${p.port}</span>
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
    <div class="detail-row"><span>Status</span><span>${p.running ? 'Running' : 'Stopped'}</span></div>
    <div class="detail-row"><span>URL</span><span><a href="${esc(p.url)}" target="_blank">${esc(p.url)}</a></span></div>
    <div class="detail-row"><span>Port</span><span>${p.port}</span></div>
    <div class="detail-row"><span>Domain</span><span>${esc(p.domain || '—')}</span></div>
    <div class="detail-row"><span>Git</span><span style="max-width:60%;word-break:break-all">${esc(p.git_url || '—')}</span></div>
    <div class="detail-row"><span>Branch</span><span>${esc(p.branch)}</span></div>
    <div class="detail-row"><span>Workspace</span><span style="font-size:0.75rem">/var/lib/syte/workspaces/${esc(p.id)}</span></div>
    <div class="logs-box" id="modal-logs">Loading logs…</div>
  `;

  const actions = document.getElementById('modal-actions');
  actions.innerHTML = `
    ${p.running
      ? `<button class="btn btn-sm btn-ghost" onclick="serviceAction('${id}','stop')">Stop</button>`
      : `<button class="btn btn-sm btn-primary" onclick="serviceAction('${id}','start')">Start</button>`
    }
    <button class="btn btn-sm btn-primary" onclick="serviceAction('${id}','update')">Pull & Restart</button>
    <button class="btn btn-sm btn-danger" onclick="serviceAction('${id}','delete')">Remove</button>
  `;

  document.getElementById('modal').classList.remove('hidden');
  refreshIcons();

  try {
    const { logs } = await api(`/projects/${id}/logs`);
    document.getElementById('modal-logs').textContent = logs;
  } catch {
    document.getElementById('modal-logs').textContent = 'Could not load logs.';
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
      if (action === 'update') toast('Update complete — data preserved.');
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
  btn.textContent = 'Deploying…';

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
    toast(`Deployed: ${res.project.name}`);
    await loadProjects();
    setTimeout(() => showView('dashboard'), 1500);
  } catch (err) {
    toast('Deploy failed: ' + err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = 'Deploy Service';
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
    toast(res.messages?.join(' ') || 'Settings saved');
    await loadSystem();
  } catch (e) {
    toast('Error: ' + e.message);
  }
});

document.getElementById('save-domain-btn')?.addEventListener('click', async () => {
  const domain = document.getElementById('set-domain').value.trim();
  if (!domain) return toast('Enter a domain for the web GUI');

  try {
    const res = await api('/settings', {
      method: 'PUT',
      body: JSON.stringify({
        gui_domain: domain,
        admin_email: document.getElementById('set-email').value || undefined,
      }),
    });
    toast(res.messages?.join(' ') || 'GUI domain applied');
    if (res.gui_url) {
      document.getElementById('gui-url').textContent = res.gui_url;
    }
    await loadSystem();
  } catch (e) {
    toast('Error: ' + e.message);
  }
});

document.getElementById('update-syte-btn')?.addEventListener('click', async () => {
  const btn = document.getElementById('update-syte-btn');
  btn.disabled = true;

  try {
    const res = await api('/system/update', { method: 'POST' });
    const box = document.getElementById('update-result');
    box.textContent = res.message;
    box.classList.remove('hidden');
    toast('Syte is updating and will restart…');
  } catch (e) {
    toast('Update failed: ' + e.message);
    btn.disabled = false;
  }
});

document.querySelectorAll('.nav-item').forEach(btn => {
  btn.addEventListener('click', () => showView(btn.dataset.view));
});

async function loadSettings() {
  try {
    const s = await api('/settings');
    const ip = document.getElementById('set-ip');
    const email = document.getElementById('set-email');
    const domain = document.getElementById('set-domain');
    const ver = document.getElementById('syte-version');
    if (ip && s.public_ip) ip.value = s.public_ip;
    if (email && s.admin_email) email.value = s.admin_email;
    if (domain && s.gui_domain) domain.value = s.gui_domain;
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
