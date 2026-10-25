// QuantumLoop service worker: app shell cache, offline fallback, background sync of queued visits.
const VERSION = 'ql-v1';
const SHELL = ['/offline/', '/static/css/quantum.css', '/static/css/app.css', '/static/js/app.js', '/static/js/field.js', '/static/js/offline-db.js', '/static/img/logo-navy.webp', '/static/img/logo-mark.png', '/static/img/icon-192.png'];
importScripts('/static/js/offline-db.js');

self.addEventListener('install', e => { e.waitUntil(caches.open(VERSION).then(c => c.addAll(SHELL)).then(() => self.skipWaiting())); });
self.addEventListener('activate', e => { e.waitUntil(caches.keys().then(keys => Promise.all(keys.filter(k => k !== VERSION).map(k => caches.delete(k)))).then(() => self.clients.claim())); });

self.addEventListener('fetch', e => {
  const req = e.request;
  if (req.method !== 'GET') return;
  const url = new URL(req.url);
  if (url.origin !== location.origin) return;
  // static: cache first, refresh in background
  if (url.pathname.startsWith('/static/')) {
    e.respondWith(caches.match(req).then(hit => { const net = fetch(req).then(r => { if (r.ok) caches.open(VERSION).then(c => c.put(req, r.clone())); return r; }).catch(() => hit); return hit || net; }));
    return;
  }
  // pages: network first, cached copy if offline, offline page as last resort
  if (req.mode === 'navigate') {
    e.respondWith(fetch(req).then(r => { if (r.ok) caches.open(VERSION).then(c => c.put(req, r.clone())); return r; }).catch(() => caches.match(req).then(hit => hit || caches.match('/offline/'))));
  }
});

async function syncQueued() {
  const items = await QL_DB.all();
  let synced = 0;
  for (const item of items) {
    try {
      const r = await qlSyncOne(item);
      if (r.ok || (r.status === 400 && r.body && r.body.permanent)) { await QL_DB.remove(item.offline_id); synced++; }
    } catch (e) { /* still offline; try again next time */ }
  }
  const clients = await self.clients.matchAll({ type: 'window' });
  clients.forEach(c => c.postMessage({ type: 'visits-synced', synced }));
}
self.addEventListener('sync', e => { if (e.tag === 'sync-visits') e.waitUntil(syncQueued()); });
self.addEventListener('message', e => { if (e.data && e.data.type === 'sync-now') e.waitUntil(syncQueued()); });
