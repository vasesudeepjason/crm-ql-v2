// Shell behaviour: sidebar groups, rail collapse, mobile nav drawer, detail drawer, clock.
window.QL = (function () {
  const app = document.getElementById('app'), sb = document.getElementById('sidebar'), sbScrim = document.getElementById('sbScrim');
  const drawer = document.getElementById('drawer'), scrim = document.getElementById('scrim');
  const key = 'ql-rail';
  const isTablet = () => matchMedia('(max-width:1100px)').matches;

  try { if (localStorage.getItem(key) === '1') app.classList.add('rail'); } catch (e) {}

  document.querySelectorAll('.ng > .ni').forEach(b => b.addEventListener('click', () => {
    if (app.classList.contains('rail') && !isTablet()) { app.classList.remove('rail'); try { localStorage.setItem(key, '0'); } catch (e) {} }
    b.parentElement.classList.toggle('open');
  }));
  document.getElementById('sbCol')?.addEventListener('click', () => {
    if (isTablet()) { openNav(false); return; }
    const r = app.classList.toggle('rail');
    try { localStorage.setItem(key, r ? '1' : '0'); } catch (e) {}
  });
  const openNav = o => { sb.classList.toggle('open', o); sbScrim.classList.toggle('show', o); };
  document.getElementById('burger')?.addEventListener('click', () => openNav(true));
  sbScrim?.addEventListener('click', () => openNav(false));

  const openDrawer = () => { drawer.classList.add('open'); scrim.classList.add('show'); };
  const closeDrawer = () => { drawer.classList.remove('open'); scrim.classList.remove('show'); setTimeout(() => { if (!drawer.classList.contains('open')) drawer.innerHTML = ''; }, 300); document.querySelectorAll('tr.sel').forEach(r => r.classList.remove('sel')); };
  scrim?.addEventListener('click', closeDrawer);
  addEventListener('keydown', e => { if (e.key === 'Escape') { openNav(false); closeDrawer(); } });
  document.body.addEventListener('htmx:afterSwap', e => {
    if (e.detail.target === drawer) { document.querySelectorAll('tr.sel').forEach(r => r.classList.remove('sel')); const src = e.detail.requestConfig?.elt?.closest?.('tr'); src?.classList.add('sel'); }
  });
  document.body.addEventListener('htmx:responseError', e => {
    if (e.detail.xhr.status === 422) { const t = document.getElementById('drawerErr'); if (t) t.innerHTML = e.detail.xhr.responseText; }
  });

  const clock = document.getElementById('clock');
  if (clock) {
    const tz = clock.dataset.tz || 'Asia/Kolkata';
    const tick = () => { try { clock.textContent = new Date().toLocaleTimeString('en-IN', { hour: '2-digit', minute: '2-digit', hour12: false, timeZone: tz }); } catch (e) { clock.textContent = new Date().toLocaleTimeString(); } };
    tick(); setInterval(tick, 30000);
  }
  addEventListener('keydown', e => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') { e.preventDefault(); document.querySelector('.search input')?.focus(); } });

  // PWA: register the service worker (root scope) and remember the install prompt for the field screens.
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('/sw.js').catch(() => {});
  window.addEventListener('beforeinstallprompt', e => { e.preventDefault(); window.QL_installPrompt = e; document.dispatchEvent(new CustomEvent('ql-installable')); });

  return { openDrawer, closeDrawer, openNav };
})();
