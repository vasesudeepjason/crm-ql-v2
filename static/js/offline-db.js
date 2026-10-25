// Tiny IndexedDB store for visits captured without a network. Shared by the page and the service worker.
const QL_DB = (() => {
  const NAME = 'quantumloop', STORE = 'visits', VERSION = 1;
  function open() {
    return new Promise((res, rej) => {
      const r = indexedDB.open(NAME, VERSION);
      r.onupgradeneeded = () => { const db = r.result; if (!db.objectStoreNames.contains(STORE)) db.createObjectStore(STORE, { keyPath: 'offline_id' }); };
      r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
    });
  }
  function tx(mode, fn) {
    return open().then(db => new Promise((res, rej) => { const t = db.transaction(STORE, mode); const out = fn(t.objectStore(STORE)); t.oncomplete = () => res(out && out.result !== undefined ? out.result : out); t.onerror = () => rej(t.error); }));
  }
  return {
    put: item => tx('readwrite', s => s.put(item)),
    all: () => tx('readonly', s => s.getAll()).then(r => r || []),
    remove: id => tx('readwrite', s => s.delete(id)),
    count: () => tx('readonly', s => s.count()),
  };
})();

// POST one queued visit back to the server. Resolves {ok, status, body}.
async function qlSyncOne(item) {
  const fd = new FormData();
  for (const [k, v] of Object.entries(item.fields)) fd.append(k, v);
  if (item.photo) fd.append('photo', item.photo, 'visit.jpg');
  const r = await fetch('/field/visits/new/', { method: 'POST', body: fd, headers: { 'X-CSRFToken': item.csrf, 'X-Offline-Sync': '1' }, credentials: 'same-origin' });
  let body = {};
  try { body = await r.json(); } catch (e) {}
  return { ok: r.ok, status: r.status, body };
}

if (typeof self !== 'undefined' && typeof window === 'undefined') { self.QL_DB = QL_DB; self.qlSyncOne = qlSyncOne; }
