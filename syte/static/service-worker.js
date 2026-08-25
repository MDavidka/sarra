const SYCORD_CACHE = 'sycord-shell-v1';
const SYCORD_SHELL = ['/', '/manifest.webmanifest'];

self.addEventListener('install', (event) => {
  event.waitUntil(caches.open(SYCORD_CACHE).then((cache) => cache.addAll(SYCORD_SHELL)).catch(() => undefined));
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let data = {};
  try { data = event.data ? event.data.json() : {}; } catch (_) { data = {body: event.data?.text() || ''}; }
  const title = data.title || 'Sycord notification';
  const options = {
    body: data.body || 'A Sycord project event occurred.',
    icon: '/static/syte-logo.png',
    badge: '/static/syte-logo.png',
    data: {url: data.url || '/', event: data.event || ''},
    tag: data.event ? `sycord-${data.event}` : undefined,
    renotify: false,
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url || '/', self.location.origin).href;
  event.waitUntil(self.clients.matchAll({type: 'window', includeUncontrolled: true}).then((clients) => {
    const existing = clients.find((client) => client.url.startsWith(self.location.origin));
    if (existing) return existing.focus().then(() => existing.navigate(target));
    return self.clients.openWindow(target);
  }));
});
