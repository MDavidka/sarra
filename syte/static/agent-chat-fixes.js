(() => {
  'use strict';

  const originalFetch = window.fetch.bind(window);

  function eventSession(event) {
    const raw = event?.payload?.session ?? event?.session ?? null;
    const value = Number(raw);
    return Number.isFinite(value) && value > 0 ? value : null;
  }

  function latestSessionEvents(events) {
    if (!Array.isArray(events) || !events.length) return events || [];
    const sessions = events.map(eventSession).filter(Boolean);
    if (!sessions.length) return events;
    const latest = Math.max(...sessions);
    const firstLatestIndex = events.findIndex((event) => eventSession(event) === latest);
    if (firstLatestIndex < 0) return events;
    return events.slice(firstLatestIndex).filter((event) => {
      const session = eventSession(event);
      return session == null || session === latest;
    });
  }

  window.fetch = async (...args) => {
    const response = await originalFetch(...args);
    try {
      const requestUrl = typeof args[0] === 'string' ? args[0] : args[0]?.url || '';
      if (!requestUrl.includes('/agent/activity') || requestUrl.includes('/stream')) return response;
      const clone = response.clone();
      const payload = await clone.json();
      if (!Array.isArray(payload?.events)) return response;
      payload.events = latestSessionEvents(payload.events);

      // Also unstick the chat if we see a completed event
      const hasEndEvent = payload.events.some(e =>
        e.event_type === 'request_completed' ||
        e.event_type === 'request_failed' ||
        e.event_type === 'agent_stopped'
      );
      if (hasEndEvent && typeof window.setDebugChatBusy === 'function') {
         setTimeout(() => {
            window.setDebugChatBusy(false);
            if (typeof window.updateDebugChatAgentStatus === 'function') {
                window.updateDebugChatAgentStatus();
            }
         }, 100);
      }

      return new Response(JSON.stringify(payload), {
        status: response.status,
        statusText: response.statusText,
        headers: response.headers,
      });
    } catch (_) {
      return response;
    }
  };

  // Listen to SSE to unstick
  const origEventSource = window.EventSource;
  if (origEventSource && !origEventSource.__agentChatPatched) {
      window.EventSource = function(...args) {
          const es = new origEventSource(...args);
          es.addEventListener('message', (event) => {
              try {
                  const data = JSON.parse(event.data);
                  if (
                      data.event_type === 'request_completed' ||
                      data.event_type === 'request_failed' ||
                      data.event_type === 'agent_stopped'
                  ) {
                      if (typeof window.setDebugChatBusy === 'function') {
                          setTimeout(() => window.setDebugChatBusy(false), 50);
                      }
                  }
              } catch (e) {}
          });
          return es;
      };
      window.EventSource.__agentChatPatched = true;
  }

  function normalizeStatus(label, detail = '') {
    const text = String(label || '').toLowerCase();
    if (text.includes('stop')) return ['Stopping…', 'Finishing the current operation safely'];
    if (text.includes('sending') || text.includes('connect')) return ['Connecting…', detail];
    if (text.includes('planning') || text.includes('thinking')) return ['Generating…', detail || 'The model is preparing a response'];
    if (text.includes('working') || text.includes('writing') || text.includes('reading') || text.includes('editing') || text.includes('running')) {
      return ['Generating…', detail || 'The agent is working on your request'];
    }
    if (text.includes('retry')) return ['Reconnecting…', detail || 'Retrying the model request'];
    return [label, detail];
  }

  function patchRuntimeFunctions() {
    if (typeof window.setDebugChatActivity === 'function' && !window.setDebugChatActivity.__agentChatPatched) {
      const original = window.setDebugChatActivity;
      const wrapped = function(label, detail, icon) {
        const [nextLabel, nextDetail] = normalizeStatus(label, detail);
        return original.call(this, nextLabel, nextDetail, icon);
      };
      wrapped.__agentChatPatched = true;
      window.setDebugChatActivity = wrapped;
    }

    if (typeof window.loadDebugChatHistory === 'function' && !window.loadDebugChatHistory.__agentChatPatched) {
      const original = window.loadDebugChatHistory;
      const wrapped = async function(projectId) {
        const result = await original.call(this, projectId);
        document.querySelectorAll('.debug-chat-messages').forEach((container) => {
          container.scrollTop = container.scrollHeight;
        });
        return result;
      };
      wrapped.__agentChatPatched = true;
      window.loadDebugChatHistory = wrapped;
    }
  }

  function improveStatusCard() {
    const activity = document.getElementById('debug-chat-activity');
    if (!activity) return;
    const label = activity.querySelector('.debug-chat-activity-label, strong, b');
    const detail = activity.querySelector('.debug-chat-activity-detail');
    if (!label) return;
    const [nextLabel, nextDetail] = normalizeStatus(label.textContent, detail?.textContent || '');
    if (nextLabel && label.textContent !== nextLabel) label.textContent = nextLabel;
    if (detail && nextDetail && detail.textContent !== nextDetail) detail.textContent = nextDetail;
    activity.setAttribute('role', 'status');
    activity.setAttribute('aria-live', 'polite');
  }

  function addStyles() {
    if (document.getElementById('agent-chat-fixes-style')) return;
    const style = document.createElement('style');
    style.id = 'agent-chat-fixes-style';
    style.textContent = `
      #svc-panel-debug-chat { min-height: calc(100dvh - 74px); }
      .debug-chat-shell, .debug-chat-layout { max-width: 980px; margin-inline: auto; }
      #debug-chat-activity { border-radius: 14px; min-height: 58px; padding: 10px 14px; }
      #debug-chat-activity .debug-chat-activity-detail { line-height: 1.3; }
      .debug-chat-messages { scroll-behavior: smooth; overscroll-behavior: contain; }
      .debug-chat-bubble { max-width: min(760px, 92%); }
      .debug-chat-assistant, .debug-chat-thinking, .debug-chat-action { margin-right: auto; }
      .debug-chat-user { margin-left: auto; }
      .debug-chat-composer, .debug-chat-input-wrap {
        position: sticky;
        bottom: 0;
        z-index: 12;
        background: color-mix(in srgb, var(--surface, #fff) 94%, transparent);
        backdrop-filter: blur(14px);
        -webkit-backdrop-filter: blur(14px);
        padding-bottom: max(10px, env(safe-area-inset-bottom));
      }
      @media (max-width: 720px) {
        #svc-panel-debug-chat { padding-inline: 10px; }
        .debug-chat-toolbar, .debug-chat-topbar { gap: 8px; flex-wrap: wrap; }
        #debug-chat-activity { display: grid; grid-template-columns: auto 1fr auto; gap: 8px; }
        #debug-chat-activity .debug-chat-activity-detail { grid-column: 2 / -1; text-align: left; }
        .debug-chat-lanes, .debug-chat-panel { border-radius: 16px; }
        .debug-chat-messages { max-height: calc(100dvh - 390px); min-height: 260px; padding: 12px; }
        .debug-chat-bubble { max-width: 100%; border-radius: 14px; }
        .debug-chat-bubble-body { font-size: 15px; line-height: 1.55; overflow-wrap: anywhere; }
        .debug-chat-composer textarea, #debug-chat-input { min-height: 92px; font-size: 16px; }
        .debug-chat-composer-actions, .debug-chat-input-actions { gap: 8px; }
        .debug-chat-composer button, .debug-chat-input-actions button { min-height: 46px; }
      }
    `;
    document.head.appendChild(style);
  }

  function boot() {
    addStyles();
    patchRuntimeFunctions();
    improveStatusCard();
    const observer = new MutationObserver(() => {
      patchRuntimeFunctions();
      improveStatusCard();
    });
    observer.observe(document.documentElement, { childList: true, subtree: true, characterData: true });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot, { once: true });
  } else {
    boot();
  }
})();
