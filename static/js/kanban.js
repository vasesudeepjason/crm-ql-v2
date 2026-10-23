// Kanban drag-and-drop. Native HTML5 DnD on pointer devices, tap-to-move fallback on touch.
(function () {
  const board = document.getElementById('board'); if (!board || board.dataset.canMove !== '1') return;
  const csrf = board.dataset.csrf;
  let dragging = null;

  async function move(card, col, reason) {
    const stage = col.dataset.stage, from = card.parentElement;
    const body = new URLSearchParams({ stage }); if (reason) body.set('reason', reason);
    const r = await fetch(card.dataset.url, { method: 'POST', headers: { 'X-CSRFToken': csrf, 'Content-Type': 'application/x-www-form-urlencoded' }, body });
    const data = await r.json().catch(() => ({}));
    if (r.status === 422 && data.needs_reason) { const why = prompt('Why was this lead lost?'); if (why) return move(card, col, why); return false; }
    if (!r.ok) { alert(data.error || 'Could not move the lead.'); return false; }
    col.querySelector('.col-b').prepend(card); card.classList.add('moved'); setTimeout(() => card.classList.remove('moved'), 600);
    recount(from.closest('.col')); recount(col);
    return true;
  }
  function recount(col) { const n = col.querySelectorAll('.kcard').length; col.querySelector('.col-h .n').textContent = n; }

  board.querySelectorAll('.kcard').forEach(c => {
    c.addEventListener('dragstart', e => { dragging = c; c.classList.add('dragging'); e.dataTransfer.effectAllowed = 'move'; });
    c.addEventListener('dragend', () => { c.classList.remove('dragging'); dragging = null; board.querySelectorAll('.col.over').forEach(x => x.classList.remove('over')); });
  });
  board.querySelectorAll('.col').forEach(col => {
    col.addEventListener('dragover', e => { if (!dragging) return; e.preventDefault(); col.classList.add('over'); });
    col.addEventListener('dragleave', () => col.classList.remove('over'));
    col.addEventListener('drop', e => { e.preventDefault(); col.classList.remove('over'); if (dragging && dragging.parentElement !== col.querySelector('.col-b')) move(dragging, col); });
  });

  // Touch fallback: tap a card to select, tap a column header to move it there.
  let selected = null;
  if (matchMedia('(pointer:coarse)').matches) {
    board.querySelectorAll('.kcard').forEach(c => c.addEventListener('click', e => {
      if (e.target.closest('a')) return;
      board.querySelectorAll('.kcard.selected').forEach(x => x.classList.remove('selected'));
      selected = selected === c ? null : c; if (selected) c.classList.add('selected');
    }));
    board.querySelectorAll('.col-h').forEach(h => h.addEventListener('click', () => { if (selected) { move(selected, h.closest('.col')); selected.classList.remove('selected'); selected = null; } }));
  }
})();
