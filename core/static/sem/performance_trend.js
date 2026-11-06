// Local linear smoothing in the displayed axis coordinates; no extrapolation.
window.semPerformanceTrend = function (points) {
  if (points.length < 5 || new Set(points.map(p => p.x)).size < 3) return [];
  const min = Math.min(...points.map(p => p.x));
  const max = Math.max(...points.map(p => p.x));
  const neighbours = Math.min(points.length, Math.max(5, Math.ceil(points.length * .4)));
  const curve = [];
  for (let i = 0; i <= 80; i++) {
    const x = min + (max - min) * i / 80;
    const distances = points.map(p => Math.abs(p.x - x)).sort((a, b) => a - b);
    const bandwidth = Math.max(distances[neighbours - 1] * 1.000001, (max - min) * .001);
    let sw = 0, sx = 0, sy = 0, sxx = 0, sxy = 0;
    for (const p of points) {
      const dx = p.x - x, distance = Math.abs(dx) / bandwidth;
      if (distance >= 1) continue;
      const weight = (1 - distance ** 3) ** 3;
      sw += weight; sx += weight * dx; sy += weight * p.y;
      sxx += weight * dx * dx; sxy += weight * dx * p.y;
    }
    if (!sw) continue;
    const variance = sxx - sx * sx / sw;
    const slope = variance > 1e-14 ? (sxy - sx * sy / sw) / variance : 0;
    const y = (sy - slope * sx) / sw;
    if (Number.isFinite(y)) curve.push({x, y: Math.max(0, Math.min(1, y))});
  }
  return curve;
};
