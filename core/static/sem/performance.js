(() => {
  const chart = document.querySelector('[data-performance-chart]');
  if (!chart) return;
  const points = JSON.parse(document.getElementById('performance-points').textContent);
  const canvas = chart.querySelector('canvas');
  const tooltip = chart.querySelector('.performance-tooltip');
  const picker = document.getElementById('performance-page');
  const details = document.getElementById('performance-details');
  const summary = document.getElementById('performance-selection');
  const format = new Intl.NumberFormat('nl-NL', {maximumFractionDigits: 2});
  const fragment = document.createDocumentFragment();
  points.forEach((p, i) => fragment.append(new Option(`${p.title} · ${p.url}`, String(i))));
  picker.append(fragment);
  let plotted = [], selected = null, hovered = null;
  function detailUrl(point) { const url = new URL(chart.dataset.detailUrl, location.href); url.searchParams.set('url', point.url); return url.href; }
  function description(point) { return `${point.title}\n${point.url}\n${chart.dataset.xLabel}: ${format.format(point.x)} · Organisch verkeer: ${format.format(point.y)}`; }
  function choose(index) {
    selected = index;
    picker.value = index === null ? '' : String(index);
    details.classList.toggle('d-none', index === null);
    summary.textContent = index === null ? '' : description(points[index]);
    if (index !== null) details.href = detailUrl(points[index]);
    draw();
  }
  function draw() {
    const width = chart.clientWidth, height = 440;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = height * ratio;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
    const left = Math.min(100, width * .28), right = 24, top = 28, bottom = 72;
    const plotW = width - left - right, plotH = height - top - bottom;
    const maxX = points.reduce((max, p) => Math.max(max, p.x), 1);
    const maxY = points.reduce((max, p) => Math.max(max, p.y), 1);
    ctx.font = '12px system-ui'; ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
      const x = left + plotW * i / 5, y = top + plotH * (1 - i / 5);
      ctx.strokeStyle = '#e0e7e8'; ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
      ctx.fillStyle = '#647580'; ctx.textAlign = 'right'; ctx.fillText(format.format(maxY * i / 5), left - 10, y + 4);
      ctx.textAlign = 'center'; ctx.fillText(format.format(maxX * i / 5), x, height - bottom + 24);
    }
    ctx.fillStyle = '#182d38'; ctx.textAlign = 'center'; ctx.fillText(chart.dataset.xLabel, left + plotW / 2, height - 15);
    ctx.save(); ctx.translate(16, top + plotH / 2); ctx.rotate(-Math.PI / 2); ctx.fillText('Organisch verkeer (Ahrefs)', 0, 0); ctx.restore();
    plotted = points.map(p => ({x: left + p.x / maxX * plotW, y: top + (1 - p.y / maxY) * plotH}));
    plotted.forEach((p, i) => { ctx.beginPath(); ctx.arc(p.x, p.y, i === selected ? 6 : 3.5, 0, Math.PI * 2); ctx.fillStyle = i === selected ? '#0b2d4d' : '#16a6c19c'; ctx.fill(); });
  }
  canvas.addEventListener('pointermove', event => {
    const bounds = canvas.getBoundingClientRect(), x = event.clientX - bounds.left, y = event.clientY - bounds.top;
    let nearest = null, distance = 144;
    plotted.forEach((p, i) => { const d = (p.x - x) ** 2 + (p.y - y) ** 2; if (d < distance) { distance = d; nearest = i; } });
    hovered = nearest; tooltip.hidden = nearest === null; canvas.style.cursor = nearest === null ? 'default' : 'pointer';
    if (nearest !== null) { tooltip.textContent = description(points[nearest]); tooltip.style.left = `${Math.min(Math.max(0,x + 12), Math.max(0,chart.clientWidth - 310))}px`; tooltip.style.top = `${Math.max(0,y - 100)}px`; }
  });
  canvas.addEventListener('pointerleave', () => { tooltip.hidden = true; hovered = null; });
  canvas.addEventListener('click', () => { if (hovered !== null) choose(hovered); });
  picker.addEventListener('change', () => choose(picker.value === '' ? null : Number(picker.value)));
  new ResizeObserver(draw).observe(chart);
})();
