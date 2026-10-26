// Dynamics chart: this-month bars, last-month dashes, hatched hover and projection bars.
(function () {
  const host = document.getElementById('chart'); if (!host) return;
  const tip = document.getElementById('tip');
  const data = JSON.parse(host.dataset.chart);
  const cur = data.this, last = data.last, days = cur.length, today = data.today;
  const inr = n => '₹' + Math.round(n).toLocaleString('en-IN');
  const fmtK = n => n >= 1e5 ? '₹' + (n / 1e5).toFixed(1) + 'L' : n >= 1e3 ? '₹' + Math.round(n / 1e3) + 'K' : '₹' + Math.round(n);
  const niceMax = v => { if (v <= 0) return 1000; const p = Math.pow(10, Math.floor(Math.log10(v))); const m = v / p; const s = m <= 1 ? 1 : m <= 2 ? 2 : m <= 2.5 ? 2.5 : m <= 5 ? 5 : 10; return s * p; };

  function render() {
    const W = host.clientWidth - 20, H = parseFloat(getComputedStyle(host).getPropertyValue('--h')) || 260;
    const padL = 54, padR = 6, padT = 14, padB = 28, plotW = W - padL - padR, plotH = H - padT - padB;
    const max = niceMax(Math.max(...cur.filter(v => v != null), ...last) * 1.1), slot = plotW / days, bw = Math.max(5, slot * .62);
    const y = v => padT + plotH - (v / max) * plotH;
    let s = `<svg viewBox="0 0 ${W} ${H}" style="height:${H}px"><defs>
      <pattern id="hatchOn" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="1.6" height="5" fill="rgba(255,255,255,.45)"/></pattern>
      <pattern id="hatchOff" width="5" height="5" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="5" height="5" fill="#F3F2F7"/><rect width="1.4" height="5" fill="#DDDCE6"/></pattern></defs>`;
    for (let g = 0; g <= 4; g++) { const v = max * g / 4, yy = y(v); s += `<line class="gl" x1="${padL}" x2="${W - padR}" y1="${yy}" y2="${yy}"/><text class="yl" x="${padL - 8}" y="${yy + 4}" text-anchor="end">${fmtK(v)}</text>`; }
    for (let i = 0; i < days; i++) {
      const x = padL + i * slot + (slot - bw) / 2;
      // Zero days draw nothing: a stub or a dash on every empty day turns a sparse month into noise.
      if (cur[i] != null && cur[i] > 0) { const h = plotH - (y(cur[i]) - padT); s += `<rect class="bar" data-i="${i}" x="${x}" y="${y(cur[i])}" width="${bw}" height="${Math.max(h, 3)}" rx="4"/><rect class="bar-hatch" x="${x}" y="${y(cur[i])}" width="${bw}" height="${Math.max(h, 3)}" rx="4"/>`; }
      else if (cur[i] == null && last[i] > 0) { const h = plotH - (y(last[i]) - padT); s += `<rect class="bar-future" x="${x}" y="${y(last[i])}" width="${bw}" height="${Math.max(h, 3)}" rx="4"/>`; }
      if (last[i] > 0) s += `<line class="lm" x1="${x - 2}" x2="${x + bw + 2}" y1="${y(last[i])}" y2="${y(last[i])}"/>`;
      s += `<rect class="hit" data-i="${i}" x="${padL + i * slot}" y="${padT}" width="${slot}" height="${plotH}"/>`;
      if (i === 0 || (i + 1) % 5 === 0) s += `<text class="xl" x="${x + bw / 2}" y="${H - 8}" text-anchor="middle">${i + 1} ${data.month}</text>`;
    }
    s += '</svg>';
    host.querySelector('svg')?.remove(); host.insertAdjacentHTML('afterbegin', s);
    const svg = host.querySelector('svg');
    svg.querySelectorAll('.hit').forEach(h => {
      h.onmouseenter = () => {
        const i = +h.dataset.i; svg.querySelectorAll('.bar').forEach(b => b.classList.toggle('on', +b.dataset.i === i));
        const a = cur[i], b = last[i]; const dl = a != null && b ? Math.round((a - b) / b * 100) : null;
        tip.innerHTML = `<b>${i + 1} ${data.month}</b><div><span class="k"><i></i>This month</span><span>${a != null ? inr(a) : '—'}</span></div><div><span class="k"><i class="dash"></i>Last month</span><span>${inr(b)}</span></div>${dl != null ? `<span class="delta ${dl >= 0 ? 'delta-up' : 'delta-down'}">${dl >= 0 ? '↑' : '↓'} ${Math.abs(dl)}% vs last month</span>` : a == null ? '<span class="muted" style="font-size:12px">Not yet</span>' : ''}`;
        tip.style.display = 'block'; const bx = padL + i * slot + slot / 2, tw = tip.offsetWidth;
        tip.style.left = Math.min(Math.max(bx - tw / 2 + 8, 0), W - tw + 8) + 'px'; tip.style.top = (y(a ?? b) - tip.offsetHeight - 14) + 'px';
      };
      h.onmouseleave = () => { tip.style.display = 'none'; svg.querySelectorAll('.bar').forEach(b => b.classList.remove('on')); };
    });
  }
  render(); new ResizeObserver(render).observe(host);
})();
