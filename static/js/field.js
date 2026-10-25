// Field sales: capture position, compress the photo on the device, ping while a day is active,
// queue visits offline (IndexedDB) and sync them when the network returns, offer PWA install.
(function () {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const geoForms = document.querySelectorAll('.geo-form');
  const chip = document.getElementById('geoChip'), chipText = document.getElementById('geoText'), state = document.getElementById('geoState');
  const visitForm = document.getElementById('visitForm');
  let last = null;

  // ---------- position ----------
  function apply(pos) {
    last = pos;
    const { latitude: lat, longitude: lng, accuracy } = pos.coords;
    geoForms.forEach(f => { f.querySelector('[name=lat]').value = lat; f.querySelector('[name=lng]').value = lng; const a = f.querySelector('[name=accuracy]'); if (a) a.value = Math.round(accuracy); });
    if (visitForm) {
      visitForm.querySelector('[name=lat]').value = lat; visitForm.querySelector('[name=lng]').value = lng; visitForm.querySelector('[name=accuracy]').value = Math.round(accuracy);
      // Browsers cannot see mock-location apps; a native wrapper can set this. Flag impossible readings meanwhile.
      visitForm.querySelector('[name=mock_location]').value = (accuracy === 0 || pos.coords.speed > 80) ? 'on' : '';
    }
    const good = accuracy <= 250;
    if (chip) { chip.classList.toggle('ok', good); chip.classList.toggle('warn', !good); chipText.textContent = `${lat.toFixed(5)}, ${lng.toFixed(5)} · ±${Math.round(accuracy)} m${good ? '' : ' · low accuracy, move outdoors'}`; }
    if (state) state.textContent = `Location ok (±${Math.round(accuracy)} m).`;
  }
  function fail(err) {
    const msg = err.code === 1 ? 'Location permission denied — allow it in your browser settings.' : 'Could not get location yet.';
    if (chip) { chip.classList.add('warn'); chipText.textContent = msg; }
    if (state) state.textContent = msg;
  }
  if ('geolocation' in navigator && (geoForms.length || visitForm)) {
    navigator.geolocation.getCurrentPosition(apply, fail, { enableHighAccuracy: true, timeout: 15000, maximumAge: 10000 });
    navigator.geolocation.watchPosition(apply, () => {}, { enableHighAccuracy: true, maximumAge: 20000 });
  }
  if (document.getElementById('geoBanner') && csrf) {
    setInterval(() => { if (!last || !navigator.onLine) return; const b = new URLSearchParams({ lat: last.coords.latitude, lng: last.coords.longitude }); fetch('/field/ping/', { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: b }).catch(() => {}); }, 120000);
  }

  // ---------- online / offline pill ----------
  const netPill = document.getElementById('netPill');
  const netState = () => { if (netPill) netPill.hidden = navigator.onLine; };
  netState(); addEventListener('online', netState); addEventListener('offline', netState);

  // ---------- visit form: photo compression + submit with offline queue ----------
  if (visitForm) {
    const input = visitForm.querySelector('input[type=file]');
    visitForm.querySelector('[name=offline_id]').value = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
    visitForm.querySelector('[name=captured_at]').value = new Date().toISOString();
    input?.addEventListener('change', async () => {
      const file = input.files[0]; if (!file || !file.type.startsWith('image/')) return;
      try {
        const bmp = await createImageBitmap(file);
        const scale = Math.min(1, 1280 / Math.max(bmp.width, bmp.height));
        const c = document.createElement('canvas'); c.width = Math.round(bmp.width * scale); c.height = Math.round(bmp.height * scale);
        c.getContext('2d').drawImage(bmp, 0, 0, c.width, c.height);
        const blob = await new Promise(r => c.toBlob(r, 'image/jpeg', 0.72));
        const dt = new DataTransfer(); dt.items.add(new File([blob], 'visit.jpg', { type: 'image/jpeg' })); input.files = dt.files;
        const prev = document.getElementById('photoPreview'); prev.hidden = false; prev.querySelector('img').src = URL.createObjectURL(blob); document.getElementById('photoSize').textContent = `${Math.round(blob.size / 1024)} KB`;
      } catch (e) { /* keep the original file */ }
    });

    visitForm.addEventListener('submit', async e => {
      if (typeof QL_DB === 'undefined') return; // no queue support → plain form post
      e.preventDefault();
      const btn = document.getElementById('submitBtn'); btn.disabled = true; btn.textContent = 'Submitting…';
      const fd = new FormData(visitForm);
      const photo = input?.files?.[0] || null;
      const fields = {}; for (const [k, v] of fd.entries()) if (k !== 'photo' && k !== 'csrfmiddlewaretoken') fields[k] = v;
      const item = { offline_id: fields.offline_id, fields, photo, csrf, title: fields.new_company || visitForm.querySelector('[name=company] option:checked')?.textContent?.trim() || fields.contact_name || 'Visit', captured_at: fields.captured_at };
      const queue = async () => { await QL_DB.put(item); try { const reg = await navigator.serviceWorker?.ready; await reg?.sync?.register('sync-visits'); } catch (x) {} location.href = '/field/?queued=1'; };
      if (!navigator.onLine) return queue();
      try {
        const r = await qlSyncOne(item);
        if (r.ok) { location.href = '/field/?saved=1'; return; }
        if (r.status === 400 && r.body.errors) {
          document.getElementById('formErr').innerHTML = Object.entries(r.body.errors).map(([k, v]) => `<div class="field-err" style="margin-bottom:8px">${k === '__all__' ? '' : k.replace('_', ' ') + ': '}${v[0]}</div>`).join('');
          btn.disabled = false; btn.innerHTML = 'Submit for approval'; window.scrollTo({ top: 0, behavior: 'smooth' }); return;
        }
        return queue(); // 5xx or anything odd: keep it safe on the phone
      } catch (err) { return queue(); } // network dropped mid-submit
    });
  }

  // ---------- My day: queued visits + sync ----------
  const syncCard = document.getElementById('syncCard');
  async function renderQueue() {
    if (!syncCard || typeof QL_DB === 'undefined') return;
    const items = await QL_DB.all();
    syncCard.hidden = items.length === 0;
    document.getElementById('syncList').innerHTML = items.map(i => `<li><span class="avatar sm">⏳</span><div class="t"><b>${(i.title || 'Visit').replace(/</g, '&lt;')}</b><small>captured ${new Date(i.captured_at).toLocaleString('en-IN', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })}${i.photo ? ' · photo attached' : ''}${i.error ? ' · <span style="color:var(--red-700)">' + i.error + '</span>' : ''}</small></div></li>`).join('');
  }
  async function syncAll() {
    if (typeof QL_DB === 'undefined' || !navigator.onLine) return;
    const items = await QL_DB.all(); let n = 0;
    for (const it of items) {
      try {
        const r = await qlSyncOne(it);
        if (r.ok) { await QL_DB.remove(it.offline_id); n++; }
        else if (r.status === 400 && r.body.permanent) { it.error = Object.values(r.body.errors || {}).flat()[0] || 'rejected'; await QL_DB.put(it); }
      } catch (e) { break; }
    }
    if (n) { await renderQueue(); location.replace('/field/?saved=1'); } else renderQueue();
  }
  if (syncCard) {
    renderQueue(); syncAll();
    addEventListener('online', syncAll);
    document.getElementById('syncBtn').addEventListener('click', syncAll);
    navigator.serviceWorker?.addEventListener('message', e => { if (e.data?.type === 'visits-synced') renderQueue(); });
  }

  // ---------- install prompt ----------
  const installBtn = document.getElementById('installBtn');
  if (installBtn) {
    const show = () => { if (window.QL_installPrompt) installBtn.hidden = false; };
    show(); document.addEventListener('ql-installable', show);
    installBtn.addEventListener('click', async () => { const p = window.QL_installPrompt; if (!p) return; p.prompt(); await p.userChoice; window.QL_installPrompt = null; installBtn.hidden = true; });
  }
})();
