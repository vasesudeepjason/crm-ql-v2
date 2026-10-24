// Field sales: capture position for start/end/visit forms, compress the photo on the device, ping while a day is active.
(function () {
  const csrf = document.querySelector('meta[name=csrf-token]')?.content;
  const geoForms = document.querySelectorAll('.geo-form');
  const chip = document.getElementById('geoChip'), chipText = document.getElementById('geoText'), state = document.getElementById('geoState');
  const visitForm = document.getElementById('visitForm');
  let last = null;

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

  // Ping every 2 minutes while on the "day active" screen.
  if (document.getElementById('geoBanner') && csrf) {
    setInterval(() => { if (!last) return; const b = new URLSearchParams({ lat: last.coords.latitude, lng: last.coords.longitude }); fetch('/field/ping/', { method: 'POST', headers: { 'X-CSRFToken': csrf }, body: b }).catch(() => {}); }, 120000);
  }

  // Compress the photo on the device (max 1280px, JPEG 0.72) before upload.
  const input = visitForm?.querySelector('input[type=file]');
  if (input) {
    visitForm.querySelector('[name=offline_id]').value = (crypto.randomUUID ? crypto.randomUUID() : String(Date.now()));
    input.addEventListener('change', async () => {
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
    visitForm.addEventListener('submit', () => { const b = document.getElementById('submitBtn'); b.disabled = true; b.textContent = 'Submitting…'; });
  }
})();
