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

// Shape-preserving cubic interpolation: rounded joins without artificial peaks.
window.semDrawSmoothTrend = function (ctx, points) {
  if (!points.length) return;
  ctx.moveTo(points[0].x, points[0].y);
  if (points.length < 2) return;
  const slopes = points.slice(1).map((point, i) => (point.y - points[i].y) / (point.x - points[i].x));
  const tangents = points.map((point, i) => {
    if (i === 0) return slopes[0];
    if (i === points.length - 1) return slopes[i - 1];
    const before = slopes[i - 1], after = slopes[i];
    if (before * after <= 0) return 0;
    const left = point.x - points[i - 1].x, right = points[i + 1].x - point.x;
    const a = 2 * right + left, b = right + 2 * left;
    return (a + b) / (a / before + b / after);
  });
  for (let i = 0; i < points.length - 1; i++) {
    const start = points[i], end = points[i + 1], third = (end.x - start.x) / 3;
    ctx.bezierCurveTo(start.x + third, start.y + tangents[i] * third,
      end.x - third, end.y - tangents[i + 1] * third, end.x, end.y);
  }
};
