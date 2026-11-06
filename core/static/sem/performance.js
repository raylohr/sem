(() => {
  const chart = document.querySelector('[data-performance-chart]');
  if (!chart) return;
  const points = JSON.parse(document.getElementById('performance-points').textContent);
  const canvas = chart.querySelector('canvas');
  const tooltip = chart.querySelector('.performance-tooltip');
  const picker = document.getElementById('performance-page');
  const details = document.getElementById('performance-details');
  const summary = document.getElementById('performance-selection');
  const xScale = document.getElementById('performance-x-scale');
  const yScale = document.getElementById('performance-y-scale');
  const trendControl = document.getElementById('performance-trend');
  const trendStatus = document.getElementById('performance-trend-status');
  const trendCache = new Map();
  let saveQueue = Promise.resolve();
  function savePreferences() {
    document.getElementById('performance-trend-value').value = trendControl.checked ? '1' : '0';
    const body = new URLSearchParams({x: chart.dataset.xField,
      x_scale: xScale.value, y_scale: yScale.value, trend: trendControl.checked ? '1' : '0'});
    const url = new URL(location.href);
    body.forEach((value, key) => url.searchParams.set(key, value));
    window.history.replaceState(null, '', url);
    const csrf = document.querySelector('.performance-preferences-token input').value;
    saveQueue = saveQueue.catch(() => {}).then(async () => {
      try {
        const response = await fetch(chart.dataset.preferencesUrl, {method:'POST', body,
          credentials:'same-origin', keepalive:true, headers:{'X-CSRFToken':csrf}, redirect:'error'});
        if (!response.ok) throw new Error('save failed');
      } catch (_) { trendStatus.textContent = 'Instellingen konden niet worden opgeslagen. Wijzig opnieuw om het nogmaals te proberen.'; }
    });
  }
  const transform = (value, mode) => mode === 'log' ? Math.log1p(value) : value;
  const inverse = (value, mode) => mode === 'log' ? Math.expm1(value) : value;
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
    const width = chart.clientWidth, height = window.innerWidth < 768 ? 380 : 480;
    const ratio = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * ratio); canvas.height = height * ratio;
    canvas.style.width = `${width}px`; canvas.style.height = `${height}px`;
    const ctx = canvas.getContext('2d'); ctx.scale(ratio, ratio);
    const left = Math.min(100, width * .28), right = 24, top = 28, bottom = 72;
    const plotW = width - left - right, plotH = height - top - bottom;
    const maxX = points.reduce((max, p) => Math.max(max, p.x), 1);
    const maxY = points.reduce((max, p) => Math.max(max, p.y), 1);
    const extentX = transform(maxX, xScale.value), extentY = transform(maxY, yScale.value);
    canvas.setAttribute('aria-label', `Scatterplot: ${chart.dataset.xLabel} (${xScale.value}) tegenover organisch verkeer (${yScale.value}). Kies hieronder een pagina om de waarden te bekijken.`);
    const background = ctx.createLinearGradient(0, top, 0, top + plotH);
    background.addColorStop(0, '#f8fbfd'); background.addColorStop(1, '#ffffff');
    ctx.fillStyle = background; ctx.fillRect(left, top, plotW, plotH);
    ctx.font = '11px system-ui'; ctx.lineWidth = 1;
    for (let i = 0; i <= 5; i++) {
      const x = left + plotW * i / 5, y = top + plotH * (1 - i / 5);
      ctx.strokeStyle = '#e5edf2'; ctx.setLineDash([3, 5]);
      ctx.beginPath(); ctx.moveTo(left, y); ctx.lineTo(width - right, y); ctx.stroke();
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, top + plotH); ctx.stroke(); ctx.setLineDash([]);
      ctx.fillStyle = '#647580'; ctx.textAlign = 'right'; ctx.fillText(format.format(inverse(extentY * i / 5, yScale.value)), left - 10, y + 4);
      ctx.textAlign = 'center'; ctx.fillText(format.format(inverse(extentX * i / 5, xScale.value)), x, height - bottom + 24);
    }
    ctx.fillStyle = '#182d38'; ctx.textAlign = 'center'; ctx.fillText(`${chart.dataset.xLabel} (${xScale.value === 'log' ? 'Log' : 'Linear'})`, left + plotW / 2, height - 15);
    ctx.save(); ctx.translate(16, top + plotH / 2); ctx.rotate(-Math.PI / 2); ctx.fillText(`Organisch verkeer (Ahrefs · ${yScale.value === 'log' ? 'Log' : 'Linear'})`, 0, 0); ctx.restore();
    const normalized = points.map(p => ({x: transform(p.x, xScale.value) / extentX, y: transform(p.y, yScale.value) / extentY}));
    plotted = normalized.map(p => ({x: left + p.x * plotW, y: top + (1 - p.y) * plotH}));
    if (trendControl.checked) {
      const key = `${xScale.value}:${yScale.value}`;
      if (!trendCache.has(key)) trendCache.set(key, window.semPerformanceTrend(normalized));
      const curve = trendCache.get(key);
      trendStatus.textContent = curve.length ? 'Oranje lijn: niet-lineaire trend, lokaal geschat op de gekozen asschalen.' : 'Trend niet beschikbaar: minimaal vijf URLs en drie verschillende X-waarden nodig.';
      if (curve.length) {
        ctx.save(); ctx.beginPath(); ctx.rect(left, top, plotW, plotH); ctx.clip();
        ctx.beginPath(); ctx.strokeStyle = '#d97732'; ctx.lineWidth = 2.75; ctx.lineJoin = 'round'; ctx.lineCap = 'round';
        window.semDrawSmoothTrend(ctx, curve.map(p => ({x: left + p.x * plotW, y: top + (1 - p.y) * plotH})));
        ctx.stroke(); ctx.restore();
      }
    } else trendStatus.textContent = 'Trendlijn verborgen';
    plotted.forEach((p, i) => {
      const active = i === selected || i === hovered;
      if (active) { ctx.beginPath(); ctx.arc(p.x, p.y, 11, 0, Math.PI * 2); ctx.fillStyle = '#0891b21c'; ctx.fill(); }
      ctx.beginPath(); ctx.arc(p.x, p.y, active ? 5 : 3.8, 0, Math.PI * 2);
      ctx.fillStyle = active ? '#0e7490' : '#0891b28c'; ctx.fill();
      if (active) { ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.75; ctx.stroke(); }
    });
    if (hovered !== null) {
      const p = plotted[hovered]; ctx.save(); ctx.setLineDash([3, 4]); ctx.strokeStyle = '#0e74904d'; ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(left, p.y); ctx.lineTo(p.x, p.y); ctx.lineTo(p.x, top + plotH); ctx.stroke(); ctx.restore();
    }
  }
  canvas.addEventListener('pointermove', event => {
    const bounds = canvas.getBoundingClientRect(), x = event.clientX - bounds.left, y = event.clientY - bounds.top;
    let nearest = null, distance = 144;
    plotted.forEach((p, i) => { const d = (p.x - x) ** 2 + (p.y - y) ** 2; if (d < distance) { distance = d; nearest = i; } });
    const changed = hovered !== nearest; hovered = nearest; if (changed) draw(); tooltip.hidden = nearest === null; canvas.style.cursor = nearest === null ? 'default' : 'pointer';
    if (nearest !== null) { const point = points[nearest];
      tooltip.querySelector('[data-tooltip-title]').textContent = point.title;
      tooltip.querySelector('[data-tooltip-url]').textContent = point.url;
      tooltip.querySelector('[data-tooltip-x-label]').textContent = chart.dataset.xLabel;
      tooltip.querySelector('[data-tooltip-x]').textContent = format.format(point.x);
      tooltip.querySelector('[data-tooltip-y]').textContent = format.format(point.y); tooltip.style.left = `${Math.min(Math.max(0,x + 12), Math.max(0,chart.clientWidth - 310))}px`; tooltip.style.top = `${Math.max(0,y - 160)}px`; }
  });
  canvas.addEventListener('pointerleave', () => { tooltip.hidden = true; hovered = null; draw(); });
  canvas.addEventListener('click', () => { if (hovered !== null) choose(hovered); });
  picker.addEventListener('change', () => choose(picker.value === '' ? null : Number(picker.value)));
  [xScale, yScale].forEach(control => control.addEventListener('change', () => {
    tooltip.hidden = true; hovered = null; canvas.style.cursor = 'default'; draw();
    const url = new URL(location.href);
    url.searchParams.set('x_scale', xScale.value); url.searchParams.set('y_scale', yScale.value);
    window.history.replaceState(null, '', url);
    savePreferences();
  }));
  trendControl.addEventListener('change', () => { draw(); savePreferences(); });
  new ResizeObserver(draw).observe(chart);
})();
