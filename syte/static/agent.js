/* =========================================================================
   SyteAgent — high-performance Agent tab client.

   A clean, self-contained replacement for the old "debug chat" tab. It is a
   pure frontend consumer of the existing OpenHands activity backend and does
   not change any server contract:

     GET  /api/projects/{id}/agent                         → status
     POST /api/projects/{id}/agent/{warm|start|stop|restart|interrupt}
     GET  /api/projects/{id}/agent/activity?since_id&limit&latest  → history
     GET  /api/projects/{id}/agent/activity/stream?live=1&format=tagged&since_id
     POST /api/projects/{id}/agent/chat  {message, model_profile}

   Performance strategy:
     - Consume the compact `[tag]<json>` SSE stream (cheap to parse).
     - Buffer token deltas and flush once per animation frame into a single
       live bubble (no per-token DOM mutation).
     - Inline SVG icons (no per-event icon library reflow).
     - Cap rendered DOM nodes and prune the oldest.
     - Reconnect with exponential backoff, resuming from `since_id`.

   Exposes `window.SyteAgent = { open, close, setDefaultProfile }`.
   Reuses the global helpers defined in app.js: api(), toast(), openAiSettings().
   ========================================================================= */
(function () {
  'use strict';

  // Client watchdog must exceed the server cold-boot budget (~180s) plus the
  // model response window before it declares a turn stalled.
  const REQUEST_TIMEOUT_MS = 300_000;
  const MAX_DOM_NODES = 160;
  const HISTORY_LIMIT = 200;
  const TERMINAL_MAX_LINES = 600;
  const PANE_MAX_ITEMS = 300;

  const ICONS = {
    brain: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 5a3 3 0 1 0-5.997.125 4 4 0 0 0-2.526 5.77 4 4 0 0 0 .556 6.588A4 4 0 1 0 12 18Z"/><path d="M12 5a3 3 0 1 1 5.997.125 4 4 0 0 1 2.526 5.77 4 4 0 0 1-.556 6.588A4 4 0 1 1 12 18Z"/></svg>',
    file: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M15 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V7Z"/><path d="M14 2v5h5"/></svg>',
    edit: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"/><path d="M18.5 2.5a2.12 2.12 0 0 1 3 3L12 15l-4 1 1-4Z"/></svg>',
    trash: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>',
    eye: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></svg>',
    search: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="11" cy="11" r="8"/><path d="m21 21-4.3-4.3"/></svg>',
    terminal: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="m4 17 6-6-6-6"/><path d="M12 19h8"/></svg>',
    wrench: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76Z"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M20 6 9 17l-5-5"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M18 6 6 18M6 6l12 12"/></svg>',
    zap: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z"/></svg>',
    cog: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z"/><path d="M19.4 15a1.65 1.65 0 0 0 .33 1.82l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.65 1.65 0 0 0-1.82-.33 1.65 1.65 0 0 0-1 1.51V21a2 2 0 0 1-4 0v-.09A1.65 1.65 0 0 0 9 19.4a1.65 1.65 0 0 0-1.82.33l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06a1.65 1.65 0 0 0 .33-1.82 1.65 1.65 0 0 0-1.51-1H3a2 2 0 0 1 0-4h.09A1.65 1.65 0 0 0 4.6 9a1.65 1.65 0 0 0-.33-1.82l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06a1.65 1.65 0 0 0 1.82.33H9a1.65 1.65 0 0 0 1-1.51V3a2 2 0 0 1 4 0v.09a1.65 1.65 0 0 0 1 1.51 1.65 1.65 0 0 0 1.82-.33l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06a1.65 1.65 0 0 0-.33 1.82V9a1.65 1.65 0 0 0 1.51 1H21a2 2 0 0 1 0 4h-.09a1.65 1.65 0 0 0-1.51 1Z"/></svg>',
  };

  // ------------------------------------------------------------------ state
  const S = {
    projectId: null,
    open: false,
    since: 0,
    es: null,                 // EventSource
    reconnectTimer: null,
    reconnectAttempt: 0,
    connState: 'idle',        // idle|connecting|live|reconnecting|error
    rendered: new Set(),      // event ids already drawn
    terminalReqs: new Set(),  // request ids that already reached a terminal state
    streamBuffers: new Map(), // requestId -> streamed text
    rafId: null,
    stepByCall: new Map(),    // tool_call_id -> {step, body, status}
    finalizedEl: new Map(),   // requestId -> finalized assistant body element
    activeReq: '',
    busy: false,
    sendInFlight: false,
    stopping: false,
    replaying: false,
    autoScroll: true,
    watchdog: null,
    reqStartedAt: 0,
    idleStatus: 'Ready',
    lastUserMessage: '',
    // stats
    turnCount: 0,
    toolCount: 0,
    files: new Set(),
    tokenChars: 0,
    turnStart: 0,
    durationTimer: null,
    paneCounts: { terminal: 0, files: 0, tools: 0 },
  };

  let defaultProfile = 'syra-base';

  // --------------------------------------------------------------- element ids
  const $ = (id) => document.getElementById(id);
  const thread = () => $('agent-thread');
  const termView = () => $('agent-terminal');
  const filesView = () => $('agent-files');
  const toolsView = () => $('agent-tools');

  // ---------------------------------------------------------------- markdown
  function escHtml(s) {
    const d = document.createElement('div');
    d.textContent = String(s ?? '');
    return d.innerHTML;
  }

  // Minimal, dependency-free Markdown subset (fenced/inline code, bold, italic,
  // links, headings, lists). Code spans are tokenised before inline formatting.
  function renderMarkdown(text) {
    const src = String(text ?? '');
    if (!src.trim()) return '';
    const store = [];
    const stash = (html) => { const t = `\u0000${store.length}\u0000`; store.push(html); return t; };
    let s = escHtml(src);
    s = s.replace(/```([a-zA-Z0-9_+#-]*)\n?([\s\S]*?)```/g, (_m, _lang, code) =>
      stash(`<pre class="chat-code"><code>${code.replace(/\n+$/, '')}</code></pre>`));
    s = s.replace(/`([^`\n]+)`/g, (_m, code) => stash(`<code class="chat-inline-code">${code}</code>`));
    const inline = (line) => line
      .replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
        (_m, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`)
      .replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*(?!\*)/g, '$1<em>$2</em>');
    const out = [];
    let listType = null, para = [];
    const flushP = () => { if (para.length) { out.push(`<div class="chat-p">${para.join('<br>')}</div>`); para = []; } };
    const closeL = () => { if (listType) { out.push(`</${listType}>`); listType = null; } };
    for (const line of s.split('\n')) {
      const isTok = /^\s*\u0000\d+\u0000\s*$/.test(line);
      const h = /^(#{1,3})\s+(.+)$/.exec(line);
      const ul = /^\s*[-*]\s+(.+)$/.exec(line);
      const ol = /^\s*(\d+)\.\s+(.+)$/.exec(line);
      if (isTok) { flushP(); closeL(); out.push(line.trim()); }
      else if (h) { flushP(); closeL(); out.push(`<div class="chat-heading chat-h${h[1].length}">${inline(h[2])}</div>`); }
      else if (ul) { flushP(); if (listType !== 'ul') { closeL(); out.push('<ul class="chat-list">'); listType = 'ul'; } out.push(`<li>${inline(ul[1])}</li>`); }
      else if (ol) { flushP(); if (listType !== 'ol') { closeL(); out.push('<ol class="chat-list">'); listType = 'ol'; } out.push(`<li>${inline(ol[2])}</li>`); }
      else if (line.trim() === '') { flushP(); closeL(); }
      else { closeL(); para.push(inline(line)); }
    }
    flushP(); closeL();
    return out.join('').replace(/\u0000(\d+)\u0000/g, (_m, i) => store[Number(i)] ?? '');
  }

  // ------------------------------------------------------------- scroll utils
  function onThreadScroll() {
    const el = thread();
    if (!el) return;
    S.autoScroll = (el.scrollHeight - el.scrollTop - el.clientHeight) < 80;
  }
  function scrollToBottom(force) {
    const el = thread();
    if (!el || (!S.autoScroll && !force)) return;
    el.scrollTop = el.scrollHeight;
    if (force) S.autoScroll = true;
  }
  function hideEmpty() { $('agent-empty')?.classList.add('hidden'); }
  function pruneThread() {
    const el = thread();
    if (!el) return;
    const nodes = [...el.children].filter((n) => n.id !== 'agent-empty' && !n.classList.contains('streaming'));
    const excess = nodes.length - MAX_DOM_NODES;
    for (let i = 0; i < excess; i++) nodes[i].remove();
  }

  // -------------------------------------------------------------- connection
  function setConn(state, detail) {
    S.connState = state;
    const dot = $('agent-conn-dot');
    const text = $('agent-conn-text');
    const det = $('agent-conn-detail');
    const label = {
      idle: 'Idle', connecting: 'Connecting…', live: 'Connected · streaming live',
      reconnecting: 'Reconnecting…', error: 'Connection lost',
    }[state] || state;
    if (dot) { dot.className = 'agent-conn-dot ' + ({ live: 'live', connecting: 'connecting', reconnecting: 'connecting', error: 'error' }[state] || ''); }
    if (text) text.textContent = label;
    if (det) det.textContent = detail || '';
  }

  // ------------------------------------------------------------- status pill
  function setStatusPill(kind, label) {
    const dot = document.querySelector('#agent-status-pill .agent-status-dot');
    const text = document.querySelector('#agent-status-pill .agent-status-text');
    if (dot) dot.className = 'agent-status-dot ' + (kind || '');
    if (text) text.textContent = label;
  }

  async function refreshStatus() {
    if (!S.projectId) return;
    try {
      const res = await api(`/projects/${S.projectId}/agent`);
      const model = res.agent_model?.profile || res.agent_model?.model || '';
      if (res.agent_running && res.agent_healthy) {
        setStatusPill('ok', model ? `Ready · ${model}` : 'Ready');
        S.idleStatus = model ? `Ready · ${model}` : 'Ready';
      } else if (res.agent_status === 'starting' || res.agent_warming) {
        setStatusPill('warm', 'Warming agent…');
        S.idleStatus = 'Warming…';
      } else if (res.agent_install_ok === false) {
        setStatusPill('err', 'OpenHands not installed');
        S.idleStatus = 'OpenHands not installed';
      } else if (res.agent_backend && !res.agent_backend.ok) {
        setStatusPill('err', 'Connect an AI provider');
        S.idleStatus = 'Connect an AI provider';
      } else if (res.agent_last_error) {
        setStatusPill('err', 'Agent needs attention');
        S.idleStatus = 'Agent needs attention';
      } else {
        setStatusPill('', 'Idle · starts on first message');
        S.idleStatus = 'Idle · starts on first message';
      }
    } catch {
      setStatusPill('err', 'Status unavailable');
    }
  }

  // ------------------------------------------------------------------- stats
  function updateStats() {
    const set = (id, v) => { const el = $(id); if (el) el.textContent = v; };
    set('agent-stat-turns', String(S.turnCount));
    set('agent-stat-files', String(S.files.size));
    set('agent-stat-tools', String(S.toolCount));
  }
  function updateDuration() {
    const el = $('agent-stat-duration');
    if (!el) return;
    if (!S.turnStart) { el.textContent = '—'; return; }
    const secs = Math.round((Date.now() - S.turnStart) / 1000);
    el.textContent = secs < 60 ? `${secs}s` : `${Math.floor(secs / 60)}m${String(secs % 60).padStart(2, '0')}`;
  }
  function startDurationTimer() {
    S.turnStart = Date.now();
    if (S.durationTimer) clearInterval(S.durationTimer);
    S.durationTimer = setInterval(updateDuration, 1000);
    updateDuration();
  }
  function stopDurationTimer() {
    if (S.durationTimer) { clearInterval(S.durationTimer); S.durationTimer = null; }
    updateDuration();
  }

  function bumpPane(name) {
    S.paneCounts[name] = (S.paneCounts[name] || 0) + 1;
    const el = document.querySelector(`.agent-pane-tab[data-pane-tab="${name}"] .agent-pane-count`);
    if (el) el.textContent = String(S.paneCounts[name]);
  }

  // --------------------------------------------------------- token streaming
  function ensureStreamBubble(rid) {
    const key = rid || 'pending';
    let bubble = $(`agent-stream-${key}`);
    if (bubble) return bubble.querySelector('.agent-msg-body');
    hideEmpty();
    bubble = document.createElement('div');
    bubble.className = 'agent-msg assistant streaming';
    bubble.id = `agent-stream-${key}`;
    bubble.innerHTML = '<div class="agent-msg-head">Assistant</div><div class="agent-msg-body agent-md"></div>';
    thread().appendChild(bubble);
    scrollToBottom();
    return bubble.querySelector('.agent-msg-body');
  }
  function flushStreams() {
    S.rafId = null;
    for (const [rid, text] of S.streamBuffers.entries()) {
      const body = ensureStreamBubble(rid);
      if (body) { body.textContent = text; body.dataset.raw = text; }
    }
    scrollToBottom();
  }
  function queueDelta(rid, delta, snapshot) {
    const key = rid || 'pending';
    const next = snapshot || ((S.streamBuffers.get(key) || '') + (delta || ''));
    S.streamBuffers.set(key, next);
    S.tokenChars += (delta || '').length;
    if (!S.rafId) S.rafId = requestAnimationFrame(flushStreams);
  }
  // Finalise the streaming bubble for a request. Idempotent: a request can be
  // finalised by message_snapshot, assistant_message, and request_completed;
  // subsequent calls update the same bubble instead of creating duplicates.
  function finalizeStream(rid, finalText) {
    const key = rid || 'pending';
    if (S.rafId) { cancelAnimationFrame(S.rafId); S.rafId = null; }
    const buffered = S.streamBuffers.get(key) || '';
    S.streamBuffers.delete(key);
    const text = finalText || buffered;
    let bubble = $(`agent-stream-${key}`);
    let body = bubble?.querySelector('.agent-msg-body');
    if (!body) body = S.finalizedEl.get(key) || null;   // already finalised earlier
    if (!body && text) body = ensureStreamBubble(key);   // late reply with no prior deltas
    if (body && text) {
      const prev = body.dataset.raw || '';
      if (!S.finalizedEl.has(key) || text.length >= prev.length) {
        body.dataset.raw = text;
        body.innerHTML = renderMarkdown(text);
      }
    }
    bubble = $(`agent-stream-${key}`);
    if (bubble) { bubble.classList.remove('streaming'); bubble.removeAttribute('id'); }
    if (body) S.finalizedEl.set(key, body);
    scrollToBottom();
  }
  function finalizeAllStreams() {
    for (const [rid, text] of [...S.streamBuffers.entries()]) finalizeStream(rid, text);
  }

  // ---------------------------------------------------------- render helpers
  function addMessage(role, title, text, extra) {
    hideEmpty();
    const el = document.createElement('div');
    el.className = `agent-msg ${role}`;
    const head = document.createElement('div');
    head.className = 'agent-msg-head';
    head.textContent = title;
    const body = document.createElement('div');
    body.className = 'agent-msg-body agent-md';
    body.dataset.raw = String(text ?? '');
    body.innerHTML = renderMarkdown(text);
    el.appendChild(head);
    el.appendChild(body);
    if (typeof extra === 'function') extra(el, body);
    thread().appendChild(el);
    pruneThread();
    scrollToBottom();
    return el;
  }

  function addThinking(text) {
    hideEmpty();
    const d = document.createElement('details');
    d.className = 'agent-think';
    d.innerHTML = `<summary>${ICONS.brain}<span>Reasoning</span></summary><div class="agent-think-body"></div>`;
    d.querySelector('.agent-think-body').textContent = String(text ?? '');
    thread().appendChild(d);
    pruneThread();
    scrollToBottom();
  }

  const STEP_META = {
    file_read: { icon: 'eye', cls: '', label: 'Read file' },
    file_search: { icon: 'search', cls: '', label: 'Search' },
    command_run: { icon: 'terminal', cls: '', label: 'Run command' },
    tool_call: { icon: 'wrench', cls: '', label: 'Tool call' },
    tool_call_started: { icon: 'wrench', cls: '', label: 'Tool call' },
    service_action: { icon: 'cog', cls: '', label: 'Service' },
  };

  function addStep(ev, meta) {
    hideEmpty();
    const d = document.createElement('details');
    d.className = 'agent-step flash';
    const detail = String(ev.text || '').replace(/\s+/g, ' ').slice(0, 400);
    d.innerHTML = `
      <summary>
        <span class="agent-step-icon">${ICONS[meta.icon] || ICONS.wrench}</span>
        <span class="agent-step-title">${escHtml(ev.title || meta.label)}</span>
        <span class="agent-step-detail">${escHtml(detail)}</span>
        <span class="agent-step-status run">Running</span>
      </summary>
      <div class="agent-step-body"></div>`;
    thread().appendChild(d);
    pruneThread();
    scrollToBottom();
    requestAnimationFrame(() => d.classList.remove('flash'));
    const callId = ev.tool || ev.requestId;
    if (ev.toolCallId || callId) {
      S.stepByCall.set(ev.toolCallId || callId, {
        step: d,
        body: d.querySelector('.agent-step-body'),
        status: d.querySelector('.agent-step-status'),
      });
    }
    return d;
  }

  function completeStep(ev) {
    const key = ev.toolCallId || ev.tool || ev.requestId;
    const ref = key ? S.stepByCall.get(key) : null;
    const isErr = !!ev.isError;
    if (ref) {
      if (ev.text) { ref.body.textContent = String(ev.text).slice(0, 4000); }
      ref.status.textContent = isErr ? 'Failed' : 'Done';
      ref.status.className = 'agent-step-status ' + (isErr ? 'err' : 'ok');
      const icon = ref.step.querySelector('.agent-step-icon');
      if (icon) icon.classList.add(isErr ? 'err' : 'ok');
      return;
    }
    // No matching open step — render a standalone result card.
    hideEmpty();
    const d = document.createElement('details');
    d.className = 'agent-step';
    d.innerHTML = `
      <summary>
        <span class="agent-step-icon ${isErr ? 'err' : 'ok'}">${isErr ? ICONS.x : ICONS.check}</span>
        <span class="agent-step-title">${escHtml(ev.title || 'Result')}</span>
        <span class="agent-step-detail"></span>
        <span class="agent-step-status ${isErr ? 'err' : 'ok'}">${isErr ? 'Failed' : 'Done'}</span>
      </summary>
      <div class="agent-step-body"></div>`;
    d.querySelector('.agent-step-body').textContent = String(ev.text || '').slice(0, 4000);
    thread().appendChild(d);
    pruneThread();
    scrollToBottom();
  }

  function fileOp(type) {
    if (type === 'file_created') return 'created';
    if (type === 'file_deleted') return 'deleted';
    if (type === 'file_read') return 'read';
    if (type === 'file_search') return 'search';
    return 'modified';
  }

  function addFileCard(ev) {
    hideEmpty();
    const op = fileOp(ev.type);
    const path = String(ev.text || '').split('\n')[0].slice(0, 300);
    if (path && op !== 'read' && op !== 'search') S.files.add(path);
    const el = document.createElement('div');
    el.className = 'agent-file flash';
    el.innerHTML = `<span class="agent-file-op ${op}">${op}</span><span class="agent-file-path"></span>`;
    el.querySelector('.agent-file-path').textContent = path || ev.title || 'file';
    thread().appendChild(el);
    pruneThread();
    scrollToBottom();
    requestAnimationFrame(() => el.classList.remove('flash'));
  }

  // ------------------------------------------------------------- side panes
  function appendTerminal(text, kind) {
    const el = termView();
    if (!el) return;
    el.querySelector('.agent-term-empty')?.remove();
    for (const raw of String(text).split('\n')) {
      const line = document.createElement('div');
      line.className = 'agent-term-line' + (kind === 'cmd' ? ' agent-term-cmd' : kind === 'err' ? ' agent-term-err' : '');
      line.textContent = raw;
      el.appendChild(line);
    }
    while (el.childElementCount > TERMINAL_MAX_LINES) el.firstElementChild.remove();
    el.scrollTop = el.scrollHeight;
    bumpPane('terminal');
  }

  function appendPaneItem(view, opClass, opLabel, text) {
    const el = view();
    if (!el) return;
    el.querySelector('.agent-pane-empty')?.remove();
    const item = document.createElement('div');
    item.className = 'agent-pane-item';
    item.innerHTML = `<span class="agent-pane-item-op ${opClass}">${escHtml(opLabel)}</span><span class="agent-pane-item-text"></span>`;
    item.querySelector('.agent-pane-item-text').textContent = String(text || '').split('\n')[0].slice(0, 300);
    el.appendChild(item);
    while (el.childElementCount > PANE_MAX_ITEMS) el.firstElementChild.remove();
    el.scrollTop = el.scrollHeight;
  }

  // ----------------------------------------------------------- normalization
  function normalizeRest(e) {
    const p = e.payload || {};
    const type = e.event_type || '';
    let text = e.detail || '';
    if (type === 'token_delta') text = p.delta || text;
    else if (type === 'request_completed') text = p.reply || text;
    else if (type === 'assistant_message' || type === 'message_snapshot') text = p.content || text;
    return {
      id: e.id, type, role: e.role || '', title: e.title || '', text,
      requestId: p.request_id || '', phase: p.phase || '', tool: p.tool || '',
      toolCallId: p.tool_call_id || '', isError: !!p.is_error, snapshot: p.snapshot || '',
    };
  }
  function normalizeTagged(b) {
    return {
      id: b.id, type: b.type || '', role: b.role || '', title: b.title || '', text: b.text || '',
      requestId: b.request_id || '', phase: b.phase || '', tool: b.tool || '',
      toolCallId: b.tool_call_id || '', isError: !!b.is_error, snapshot: b.snapshot || '',
    };
  }

  // --------------------------------------------------------- event dispatch
  const STEP_TYPES = new Set(['file_read', 'file_search', 'command_run', 'tool_call', 'tool_call_started', 'service_action']);
  const FILE_TYPES = new Set(['file_created', 'file_modified', 'file_deleted', 'file_read', 'file_changed']);
  const REFRESH_TYPES = new Set(['file_created', 'file_modified', 'file_deleted', 'file_changed', 'service_action', 'request_completed']);

  function handleEvent(ev) {
    if (!ev || !ev.type) return;
    if (ev.id != null) {
      if (S.rendered.has(ev.id)) return;
      S.rendered.add(ev.id);
      S.since = Math.max(S.since, ev.id);
      if (S.rendered.size > 3000) {
        for (const old of [...S.rendered].slice(0, 800)) S.rendered.delete(old);
      }
    }
    const rid = ev.requestId || '';
    const terminal = ev.type === 'request_completed' || ev.type === 'request_failed';
    if (terminal && rid) {
      if (S.terminalReqs.has(rid)) return;
      S.terminalReqs.add(rid);
    }

    switch (ev.type) {
      case 'request_started': {
        if (!S.replaying) {
          S.activeReq = rid || S.activeReq;
          setBusy(true);
          setConnDetail('Planning…');
          if (rid) armWatchdog(rid);
        }
        return;
      }
      case 'token_delta': {
        if (!S.replaying) setConnDetail('Writing…');
        queueDelta(rid, ev.text, ev.snapshot);
        return;
      }
      case 'thinking': {
        addThinking(ev.text);
        if (!S.replaying) setConnDetail('Thinking…');
        return;
      }
      case 'message_snapshot':
        finalizeStream(rid, ev.text);
        return;
      case 'assistant_message': {
        // Route through the same bubble as the token stream so the streamed
        // text and the final message never render twice.
        if (ev.text) finalizeStream(rid, ev.text);
        return;
      }
      case 'request_completed': {
        finalizeStream(rid || S.activeReq, ev.text);
        endTurn(rid, true);
        return;
      }
      case 'request_failed': {
        finalizeStream(rid || S.activeReq, '');
        renderError(ev);
        endTurn(rid, false);
        return;
      }
      case 'processing': {
        if (!S.replaying) setConnDetail('Working…');
        return;
      }
      case 'command_run': {
        addStep(ev, STEP_META.command_run);
        appendTerminal(ev.text, 'cmd');
        appendPaneItem(toolsView, 'tool', 'run', ev.text);
        S.toolCount++; updateStats();
        return;
      }
      case 'command_output': {
        completeStep(ev);
        appendTerminal(ev.text, ev.isError ? 'err' : 'out');
        return;
      }
      case 'tool_call':
      case 'tool_call_started': {
        addStep(ev, STEP_META[ev.type]);
        appendPaneItem(toolsView, 'tool', ev.tool || 'tool', ev.text || ev.title);
        S.toolCount++; updateStats();
        return;
      }
      case 'tool_call_finished': {
        completeStep(ev);
        return;
      }
      case 'service_action': {
        addStep(ev, STEP_META.service_action);
        appendPaneItem(toolsView, 'tool', 'service', ev.text || ev.title);
        S.toolCount++; updateStats();
        break;
      }
      case 'file_search':
      case 'file_read': {
        addStep(ev, STEP_META[ev.type]);
        appendPaneItem(filesView, fileOp(ev.type), fileOp(ev.type), ev.text);
        break;
      }
      case 'file_created':
      case 'file_modified':
      case 'file_deleted':
      case 'file_changed': {
        addFileCard(ev);
        appendPaneItem(filesView, fileOp(ev.type), fileOp(ev.type), ev.text);
        updateStats();
        break;
      }
      case 'user_message': {
        // Skip if we already rendered this user bubble optimistically. Scan the
        // last few user bubbles so an intervening step/stream bubble is fine.
        const bubbles = thread()?.querySelectorAll('.agent-msg.user .agent-msg-body');
        if (bubbles && bubbles.length) {
          for (let i = bubbles.length - 1; i >= 0 && i >= bubbles.length - 3; i--) {
            if ((bubbles[i].dataset.raw || '') === ev.text) return;
          }
        }
        addMessage('user', 'You', ev.text);
        return;
      }
      case 'agent_started':
      case 'agent_stopped':
      case 'agent_restarted':
      case 'status': {
        if (!S.replaying) refreshStatus();
        return;
      }
      default:
        return;
    }

    if (!S.replaying && REFRESH_TYPES.has(ev.type)) onWorkspaceChange();
  }

  function setConnDetail(text) {
    const det = $('agent-conn-detail');
    if (det) det.textContent = text || '';
  }

  function renderError(ev) {
    const code = ev.errorCode || '';
    const fallback = ev.text || 'The request could not be completed.';
    const known = {
      api_key_missing: { title: 'Connect an AI provider', detail: 'Add the API key for this model profile, then retry.', settings: true },
      agent_server_not_installed: { title: 'OpenHands is not installed', detail: fallback },
      request_timeout: { title: 'This response took too long', detail: 'The turn may still finish in the background — reconnect to check, or retry.' },
    };
    const info = known[code] || {
      title: ev.title || 'The request failed',
      detail: fallback,
      settings: /api key|provider key|credentials/i.test(fallback),
    };
    addMessage('error', info.title, info.detail, (el) => {
      const actions = document.createElement('div');
      actions.className = 'agent-error-actions';
      if (S.lastUserMessage) {
        const retry = document.createElement('button');
        retry.type = 'button'; retry.className = 'agent-error-btn'; retry.textContent = 'Retry';
        retry.addEventListener('click', () => retryMessage(S.lastUserMessage));
        actions.appendChild(retry);
      }
      if (info.settings && typeof window.openAiSettings === 'function') {
        const st = document.createElement('button');
        st.type = 'button'; st.className = 'agent-error-btn'; st.textContent = 'Provider settings';
        st.addEventListener('click', window.openAiSettings);
        actions.appendChild(st);
      }
      const rc = document.createElement('button');
      rc.type = 'button'; rc.className = 'agent-error-btn'; rc.textContent = 'Reconnect';
      rc.addEventListener('click', reconnect);
      actions.appendChild(rc);
      el.appendChild(actions);
    });
  }

  function endTurn(rid, ok) {
    if (S.replaying) return;
    const isActive = !S.activeReq || (rid && rid === S.activeReq);
    if (!isActive) return;
    clearWatchdog();
    setBusy(false);
    S.activeReq = '';
    S.turnCount++;
    stopDurationTimer();
    updateStats();
    setConnDetail(ok ? 'Response ready' : (S.stopping ? 'Response stopped' : 'Response failed'));
    setTimeout(() => { if (!S.busy) setConnDetail(''); }, 2600);
    refreshStatus();
    if (ok) onWorkspaceChange();
  }

  async function onWorkspaceChange() {
    if (!S.projectId || typeof window.loadProjects !== 'function') return;
    try { await window.loadProjects({ silent: true }); } catch { /* ignore */ }
  }

  // ------------------------------------------------------------- SSE stream
  function parseTagged(data) {
    // Format: [tag]<json>
    const m = /^\[([^\]]+)\]<([\s\S]*)>$/.exec(data);
    if (!m) return null;
    let body;
    try { body = JSON.parse(m[2]); } catch { return null; }
    return { tag: m[1], body };
  }

  function startStream() {
    stopStream(false);
    const pid = S.projectId;
    const params = new URLSearchParams({ live: '1', format: 'tagged', since_id: String(S.since) });
    setConn(S.reconnectAttempt > 0 ? 'reconnecting' : 'connecting');
    const es = new EventSource(`/api/projects/${pid}/agent/activity/stream?${params}`);
    S.es = es;
    es.onopen = () => { S.reconnectAttempt = 0; setConn('live', S.busy ? 'Working…' : ''); };
    es.onmessage = (e) => {
      const parsed = parseTagged(e.data);
      if (!parsed) return;
      setConn('live', S.busy ? (($('agent-conn-detail')?.textContent) || 'Working…') : '');
      const { tag, body } = parsed;
      if (tag === 'ping') { if (body.since_id != null) S.since = Math.max(S.since, body.since_id); return; }
      if (tag === 'session') { syncHistory(pid); return; }
      if (body && body.type) {
        if (body.id != null) S.since = Math.max(S.since, body.id);
        handleEvent(normalizeTagged(body));
      }
    };
    es.onerror = () => {
      es.close();
      if (S.es === es) S.es = null;
      setConn('reconnecting');
      void syncHistory(pid);
      scheduleReconnect();
    };
  }

  function stopStream(resetConn) {
    if (S.reconnectTimer) { clearTimeout(S.reconnectTimer); S.reconnectTimer = null; }
    if (S.es) { S.es.close(); S.es = null; }
    if (resetConn !== false) setConn('idle');
  }

  function scheduleReconnect() {
    if (!S.open) return;
    const attempt = S.reconnectAttempt++;
    const delay = attempt === 0 ? 900 : Math.min(15000, 900 * Math.pow(2, attempt));
    S.reconnectTimer = setTimeout(() => { if (S.open) startStream(); }, delay);
  }

  async function reconnect() {
    if (!S.open) return;
    stopStream();
    S.reconnectAttempt = 0;
    await syncHistory(S.projectId);
    startStream();
  }

  async function syncHistory(pid) {
    if (pid !== S.projectId) return false;
    try {
      const res = await api(`/projects/${pid}/agent/activity?since_id=${S.since}&limit=120`);
      for (const e of res.events || []) handleEvent(normalizeRest(e));
      return true;
    } catch { return false; }
  }

  async function loadHistory(pid) {
    S.replaying = true;
    try {
      const res = await api(`/projects/${pid}/agent/activity?since_id=0&limit=${HISTORY_LIMIT}&latest=1`);
      const events = res.events || [];
      // Detect a turn that is still open (no terminal event) so we can resume it.
      const pending = new Map();
      for (const e of events) {
        const r = e.payload?.request_id || '';
        if (e.event_type === 'request_started' && r) pending.set(r, true);
        else if (r && (e.event_type === 'request_completed' || e.event_type === 'request_failed')) pending.delete(r);
      }
      clearThreadDom(true);
      for (const e of events) handleEvent(normalizeRest(e));
      const lastId = events.reduce((max, e) => Math.max(max, e.id || 0), 0);
      if (lastId) S.since = Math.max(S.since, lastId);
      const openReq = [...pending.keys()].pop() || '';
      if (openReq) {
        S.activeReq = openReq;
        setBusy(true);
        setConnDetail('Reconnected to the active response');
        startDurationTimer();
        armWatchdog(openReq);
      }
    } catch (e) {
      addMessage('error', 'Could not load history', normalizeErr(e));
    } finally {
      S.replaying = false;
      finalizeAllStreams();
      if (!S.activeReq && !S.sendInFlight) setBusy(false);
    }
  }

  function normalizeErr(e) {
    const msg = (e && e.message || '').trim();
    if (!msg || /load failed|failed to fetch|networkerror/i.test(msg)) {
      return 'Could not reach the Syte server. Your message is safe to retry when the connection returns.';
    }
    return msg;
  }

  // ------------------------------------------------------------- watchdog
  function clearWatchdog() {
    if (S.watchdog) { clearTimeout(S.watchdog); S.watchdog = null; }
    S.reqStartedAt = 0;
  }
  function armWatchdog(rid) {
    clearWatchdog();
    S.reqStartedAt = Date.now();
    const check = async () => {
      if (S.activeReq !== rid) { clearWatchdog(); return; }
      await syncHistory(S.projectId);
      if (S.activeReq !== rid) return;
      if (Date.now() - S.reqStartedAt >= REQUEST_TIMEOUT_MS) {
        S.terminalReqs.add(rid);
        finalizeStream(rid);
        S.activeReq = '';
        clearWatchdog();
        setBusy(false);
        stopDurationTimer();
        renderError({ type: 'request_failed', title: 'Response is taking longer than expected',
          text: 'The agent has not reported a final result yet. It may still be finishing in the background — reconnect to pick up its latest activity, or retry.',
          errorCode: 'request_timeout' });
        return;
      }
      S.watchdog = setTimeout(check, S.connState === 'live' ? 8000 : 3000);
    };
    S.watchdog = setTimeout(check, 4000);
  }

  // ------------------------------------------------------------- composer
  function setBusy(busy) {
    S.busy = busy;
    if (!busy) S.stopping = false;
    updateControls();
  }

  function updateControls() {
    const send = $('agent-send');
    const stop = $('agent-stop');
    const input = $('agent-input');
    const model = $('agent-model');
    const hasText = !!String(input?.value || '').trim();
    const busy = S.busy || S.sendInFlight || S.replaying;
    if (send) {
      send.disabled = busy || !hasText;
      send.classList.toggle('is-loading', S.sendInFlight);
    }
    if (stop) {
      stop.classList.toggle('hidden', !S.busy);
      stop.disabled = !S.busy || S.stopping;
      const label = stop.querySelector('span');
      if (label) label.textContent = S.stopping ? 'Stopping…' : 'Stop';
    }
    if (model) model.disabled = busy;
    const interruptBtn = document.querySelector('[data-agent-action="interrupt"]');
    if (interruptBtn) interruptBtn.disabled = !S.busy;
  }

  function autoGrow(el) {
    if (!el) return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight, 168) + 'px';
  }

  async function send() {
    const input = $('agent-input');
    const message = String(input?.value || '').trim();
    if (!message) return;
    if (!S.projectId) { toast('Open a project before using the agent.'); return; }
    if (S.replaying) { toast('The conversation is still loading. Try again in a moment.'); return; }
    if (S.busy || S.sendInFlight) { toast('The agent is still working. Stop the current response first.'); input?.focus(); return; }

    S.sendInFlight = true;
    updateControls();
    setConnDetail('Sending…');
    S.lastUserMessage = message;
    addMessage('user', 'You', message);
    scrollToBottom(true);
    startDurationTimer();

    const profile = $('agent-model')?.value || defaultProfile;
    if (input) { input.value = ''; autoGrow(input); }

    let accepted = false;
    try {
      const res = await api(`/projects/${S.projectId}/agent/chat`, {
        method: 'POST',
        body: JSON.stringify({ message, model_profile: profile }),
      });
      if (!res.ok) {
        renderError({ type: 'request_failed', title: 'Request failed', text: chatError(res), errorCode: res.error || '' });
        S.sendInFlight = false; setBusy(false); stopDurationTimer(); setConnDetail('Request failed');
      } else if (res.request_id && (res.status === 'accepted' || !res.reply)) {
        accepted = true;
        S.sendInFlight = false;
        if (S.terminalReqs.has(res.request_id)) {
          S.activeReq = ''; setBusy(false); stopDurationTimer();
        } else {
          S.activeReq = res.request_id;
          setBusy(true);
          setConnDetail('Planning…');
          armWatchdog(res.request_id);
        }
      } else if (res.reply) {
        await syncHistory(S.projectId);
        const last = thread()?.lastElementChild;
        const lastRaw = last?.querySelector('.agent-msg-body')?.dataset.raw || '';
        if (!lastRaw.includes(res.reply) && !res.reply.includes(lastRaw)) {
          addMessage('assistant', 'Assistant', res.reply);
        }
        S.sendInFlight = false; setBusy(false); stopDurationTimer(); setConnDetail('Response ready');
      } else {
        throw new Error('The agent accepted the connection but returned no response or request id.');
      }
    } catch (e) {
      renderError({ type: 'request_failed', title: 'Request failed', text: normalizeErr(e), errorCode: 'network_error' });
      S.sendInFlight = false;
      if (!S.activeReq) { setBusy(false); stopDurationTimer(); }
      if (!S.activeReq && input && !String(input.value || '').trim()) { input.value = message; autoGrow(input); }
    } finally {
      S.sendInFlight = false;
      if (!accepted && !S.activeReq) setBusy(false);
      updateControls();
      scrollToBottom();
    }
  }

  function chatError(res) {
    if (!res) return 'Unknown error';
    const parts = [res.message, res.error].filter(Boolean);
    if (res.status_code) parts.push(`HTTP ${res.status_code}`);
    return parts.join(' — ') || 'Unknown error';
  }

  async function retryMessage(message) {
    if (S.busy || S.sendInFlight) { toast('Wait for the current response, or stop it, before retrying.'); return; }
    const input = $('agent-input');
    if (!input) return;
    input.value = message || S.lastUserMessage;
    autoGrow(input);
    input.focus();
    await send();
  }

  async function interrupt() {
    if (!S.projectId || !S.busy || S.stopping) return;
    S.stopping = true;
    updateControls();
    setConnDetail('Stopping response…');
    try {
      const res = await api(`/projects/${S.projectId}/agent/interrupt`, { method: 'POST' });
      if (!res.ok) throw new Error(chatError(res));
      if ((res.message || '').startsWith('No active')) {
        finalizeStream(S.activeReq); clearWatchdog(); setBusy(false); S.activeReq = '';
        stopDurationTimer(); setConnDetail('Response stopped');
      } else {
        setTimeout(() => { if (S.projectId) void syncHistory(S.projectId); }, 1000);
      }
    } catch (e) {
      S.stopping = false; updateControls();
      toast('Could not stop response: ' + normalizeErr(e));
    }
  }

  async function control(action) {
    if (!S.projectId) return;
    const map = {
      start: ['start', 'Starting agent…'],
      stop: ['stop', 'Stopping agent…'],
      restart: ['restart', 'Restarting agent…'],
    };
    const [path, msg] = map[action] || [];
    if (!path) return;
    setStatusPill('warm', msg);
    try {
      const res = await api(`/projects/${S.projectId}/agent/${path}`, { method: 'POST' });
      toast(res.message || `Agent ${action} requested`);
    } catch (e) {
      toast(`Could not ${action} agent: ` + normalizeErr(e));
    }
    await refreshStatus();
  }

  function warm() {
    if (!S.projectId) return;
    void api(`/projects/${S.projectId}/agent/warm`, { method: 'POST' }).catch(() => {});
  }

  // ------------------------------------------------------------- panes/view
  function clearThreadDom(keepEmpty) {
    if (S.rafId) { cancelAnimationFrame(S.rafId); S.rafId = null; }
    S.streamBuffers.clear();
    S.stepByCall.clear();
    S.finalizedEl.clear();
    const el = thread();
    if (el) {
      el.innerHTML = '';
      const empty = buildEmpty();
      el.appendChild(empty);
      if (!keepEmpty) empty.classList.add('hidden');
    }
    S.rendered.clear();
    S.autoScroll = true;
  }

  function buildEmpty() {
    const d = document.createElement('div');
    d.className = 'agent-empty';
    d.id = 'agent-empty';
    d.innerHTML = `
      <div class="agent-empty-icon">${ICONS.zap}</div>
      <h3>Your OpenHands modification specialist</h3>
      <p>Describe a change, fix, or task. I can edit files, run commands, search the codebase, and verify the result — streaming every step live.</p>`;
    return d;
  }

  function resetPanes() {
    S.paneCounts = { terminal: 0, files: 0, tools: 0 };
    document.querySelectorAll('.agent-pane-tab .agent-pane-count').forEach((el) => { el.textContent = '0'; });
    const t = termView(); if (t) t.innerHTML = '<div class="agent-term-empty">No command output yet.</div>';
    const f = filesView(); if (f) f.innerHTML = '<div class="agent-pane-empty">No file changes yet.</div>';
    const to = toolsView(); if (to) to.innerHTML = '<div class="agent-pane-empty">No tool calls yet.</div>';
  }

  function switchPaneTab(name) {
    document.querySelectorAll('.agent-pane-tab').forEach((b) => b.classList.toggle('active', b.dataset.paneTab === name));
    document.querySelectorAll('.agent-pane-view').forEach((v) => v.classList.toggle('active', v.dataset.paneView === name));
  }

  function resetSession() {
    S.turnCount = 0; S.toolCount = 0; S.files.clear(); S.tokenChars = 0;
    updateStats(); stopDurationTimer();
  }

  // --------------------------------------------------------------- lifecycle
  let wired = false;
  function wire() {
    if (wired) return;
    const root = $('agent-tab');
    if (!root) return;
    wired = true;

    $('agent-send')?.addEventListener('click', send);
    $('agent-stop')?.addEventListener('click', interrupt);

    const input = $('agent-input');
    if (input) {
      input.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
      });
      input.addEventListener('input', () => { autoGrow(input); updateControls(); });
    }

    $('agent-thread')?.addEventListener('scroll', onThreadScroll, { passive: true });

    root.querySelectorAll('[data-agent-action]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const a = btn.dataset.agentAction;
        if (a === 'interrupt') interrupt();
        else if (a === 'clear') { clearThreadDom(true); resetPanes(); resetSession(); toast('Cleared view (history preserved on the server)'); }
        else control(a);
      });
    });

    root.querySelectorAll('.agent-pane-tab').forEach((btn) => {
      btn.addEventListener('click', () => switchPaneTab(btn.dataset.paneTab));
    });

    root.querySelectorAll('[data-agent-quick]').forEach((chip) => {
      chip.addEventListener('click', () => {
        const input = $('agent-input');
        if (!input) return;
        input.value = chip.dataset.agentQuick || '';
        autoGrow(input);
        input.focus();
        updateControls();
      });
    });

    $('agent-pane-toggle')?.addEventListener('click', () => {
      $('agent-tab')?.classList.toggle('pane-hidden');
    });

    const term = termView();
    document.querySelector('[data-agent-term-copy]')?.addEventListener('click', () => {
      const text = [...(term?.querySelectorAll('.agent-term-line') || [])].map((l) => l.textContent).join('\n');
      if (text && navigator.clipboard) navigator.clipboard.writeText(text).then(() => toast('Terminal output copied'));
    });
  }

  function open(projectId) {
    if (!projectId) return;
    wire();
    const changed = projectId !== S.projectId;
    S.open = true;
    S.projectId = projectId;
    warm();
    if (changed) {
      clearWatchdog();
      S.activeReq = ''; S.sendInFlight = false; S.stopping = false; S.busy = false;
      S.since = 0; S.terminalReqs.clear();
      resetPanes(); resetSession();
      updateControls();
      loadHistory(projectId).then(() => { if (S.open && S.projectId === projectId) startStream(); });
    } else {
      syncHistory(projectId).then(() => { if (S.open) startStream(); });
    }
    refreshStatus();
    // Keep native icons in the static markup rendered.
    if (typeof window.refreshIcons === 'function') window.refreshIcons();
  }

  function close() {
    S.open = false;
    stopStream();
    clearWatchdog();
    finalizeAllStreams();
  }

  function setDefaultProfile(profile) {
    if (!profile) return;
    defaultProfile = profile;
    const sel = $('agent-model');
    if (sel && !S.busy) sel.value = profile;
  }

  window.SyteAgent = { open, close, setDefaultProfile };
})();
