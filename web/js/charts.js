import { svg } from "./ui.js";
const W = 600;
function scale(max) { const p = Math.pow(10, Math.floor(Math.log10(max || 1))); const n = (max || 1) / p; return (n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10) * p; }

export function lineChart(points, { height = 190, color = "var(--accent)", fmt = (v) => v, area = true, labels = [] } = {}) {
  const el = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${height}`, preserveAspectRatio: "none", role: "img" });
  el.style.height = height + "px";
  if (!points.length) { el.append(svg("text", { x: W / 2, y: height / 2, "text-anchor": "middle" }, "No data yet")); el.lastChild.textContent = "No data yet"; return el; }
  const pad = { l: 40, r: 10, t: 10, b: 22 }, max = scale(Math.max(...points, 1)), iw = W - pad.l - pad.r, ih = height - pad.t - pad.b;
  const x = (i) => pad.l + (points.length === 1 ? iw / 2 : (i / (points.length - 1)) * iw), y = (v) => pad.t + ih - (v / max) * ih;
  for (let g = 0; g <= 4; g++) {
    const gy = pad.t + (ih * g) / 4;
    el.append(svg("line", { x1: pad.l, x2: W - pad.r, y1: gy, y2: gy, class: "grid-l" }));
    const t = svg("text", { x: pad.l - 6, y: gy + 4, "text-anchor": "end" }); t.textContent = fmt(max * (1 - g / 4)); el.append(t);
  }
  const d = points.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join(" ");
  if (area) el.append(svg("path", { d: `${d} L${x(points.length - 1)},${pad.t + ih} L${x(0)},${pad.t + ih}Z`, fill: color, opacity: ".12" }));
  const line = svg("path", { d, fill: "none", stroke: color, "stroke-width": 2.4, "stroke-linejoin": "round", "stroke-linecap": "round", class: "draw", "vector-effect": "non-scaling-stroke" });
  line.style.setProperty("--len", "2200"); el.append(line);
  points.forEach((v, i) => { if (points.length <= 40) { const c = svg("circle", { cx: x(i), cy: y(v), r: 3, fill: "var(--surface)", stroke: color, "stroke-width": 2 }); const t = svg("title"); t.textContent = `${labels[i] || i + 1}: ${fmt(v)}`; c.append(t); el.append(c); } });
  [0, Math.floor((points.length - 1) / 2), points.length - 1].forEach((i) => { if (labels[i]) { const t = svg("text", { x: x(i), y: height - 5, "text-anchor": i === 0 ? "start" : i === points.length - 1 ? "end" : "middle" }); t.textContent = labels[i]; el.append(t); } });
  return el;
}

export function barChart(items, { height = 200, fmt = (v) => v, max = null, colors = [] } = {}) {
  const el = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${height}`, role: "img" }); el.style.height = height + "px";
  const pad = { l: 40, r: 8, t: 10, b: 38 }, m = max || scale(Math.max(...items.map((i) => i.value || 0), 0.0001)), ih = height - pad.t - pad.b, iw = W - pad.l - pad.r;
  for (let g = 0; g <= 4; g++) { const gy = pad.t + (ih * g) / 4; el.append(svg("line", { x1: pad.l, x2: W - pad.r, y1: gy, y2: gy, class: "grid-l" })); const t = svg("text", { x: pad.l - 6, y: gy + 4, "text-anchor": "end" }); t.textContent = fmt(m * (1 - g / 4)); el.append(t); }
  const bw = iw / items.length;
  items.forEach((it, i) => {
    const hgt = ((it.value || 0) / m) * ih, bx = pad.l + i * bw + bw * 0.18, w = bw * 0.64;
    const r = svg("rect", { x: bx, y: pad.t + ih - hgt, width: w, height: Math.max(hgt, 1), rx: 5, fill: it.color || colors[i] || `var(--c${i % 10})`, class: "grow-y" }); r.style.setProperty("--i", i);
    const t = svg("title"); t.textContent = `${it.label}: ${fmt(it.value)}`; r.append(t); el.append(r);
    const v = svg("text", { x: bx + w / 2, y: pad.t + ih - hgt - 5, "text-anchor": "middle", style: "fill:var(--ink-2);font-weight:600" }); v.textContent = fmt(it.value); el.append(v);
    const l = svg("text", { x: bx + w / 2, y: height - 16, "text-anchor": "middle" }); l.textContent = it.label; el.append(l);
  });
  return el;
}

export function groupedBars(groups, series, { height = 230, fmt = (v) => (v * 100).toFixed(0) + "%", max = 1 } = {}) {
  const el = svg("svg", { class: "chart", viewBox: `0 0 ${W} ${height}`, role: "img" }); el.style.height = height + "px";
  const pad = { l: 40, r: 8, t: 10, b: 28 }, ih = height - pad.t - pad.b, iw = W - pad.l - pad.r, gw = iw / groups.length;
  for (let g = 0; g <= 4; g++) { const gy = pad.t + (ih * g) / 4; el.append(svg("line", { x1: pad.l, x2: W - pad.r, y1: gy, y2: gy, class: "grid-l" })); const t = svg("text", { x: pad.l - 6, y: gy + 4, "text-anchor": "end" }); t.textContent = fmt(max * (1 - g / 4)); el.append(t); }
  groups.forEach((gr, gi) => {
    const bw = (gw * 0.78) / series.length;
    series.forEach((s, si) => {
      const v = gr.values[si] || 0, hh = (v / max) * ih, x = pad.l + gi * gw + gw * 0.11 + si * bw;
      const r = svg("rect", { x, y: pad.t + ih - hh, width: bw - 3, height: Math.max(hh, 1), rx: 4, fill: `var(--c${si % 10})`, class: "grow-y" }); r.style.setProperty("--i", gi * series.length + si);
      const t = svg("title"); t.textContent = `${gr.label} · ${s}: ${fmt(v)}`; r.append(t); el.append(r);
    });
    const l = svg("text", { x: pad.l + gi * gw + gw / 2, y: height - 8, "text-anchor": "middle" }); l.textContent = gr.label; el.append(l);
  });
  return el;
}

export function gauge(value, { size = 160, label = "", color = "var(--accent)", stroke = 12 } = {}) {
  const r = (size - stroke) / 2, c = 2 * Math.PI * r, el = svg("svg", { viewBox: `0 0 ${size} ${size}`, width: size, height: size, role: "img", "aria-label": `${label} ${Math.round(value)}` });
  el.append(svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: "var(--surface-3)", "stroke-width": stroke }));
  const arc = svg("circle", { cx: size / 2, cy: size / 2, r, fill: "none", stroke: color, "stroke-width": stroke, "stroke-linecap": "round", "stroke-dasharray": c, "stroke-dashoffset": c, transform: `rotate(-90 ${size / 2} ${size / 2})` });
  arc.style.transition = "stroke-dashoffset 1.2s cubic-bezier(.2,.7,.2,1)"; el.append(arc);
  requestAnimationFrame(() => requestAnimationFrame(() => arc.setAttribute("stroke-dashoffset", c * (1 - Math.max(0, Math.min(100, value)) / 100))));
  return el;
}
export function sparkline(points, { w = 90, h = 30, color = "var(--accent)" } = {}) {
  const el = svg("svg", { class: "spark", width: w, height: h, viewBox: `0 0 ${w} ${h}` });
  if (points.length < 2) return el;
  const mx = Math.max(...points, 1), mn = Math.min(...points, 0);
  const d = points.map((v, i) => `${i ? "L" : "M"}${(i / (points.length - 1)) * w},${h - 3 - ((v - mn) / (mx - mn || 1)) * (h - 6)}`).join(" ");
  el.append(svg("path", { d, fill: "none", stroke: color, "stroke-width": 2, "stroke-linecap": "round", class: "draw", style: "--len:300" })); return el;
}
