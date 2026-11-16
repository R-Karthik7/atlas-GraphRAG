// Force-directed graph on canvas: drag, zoom/pan, hover focus, animated entrance, flowing particles, path highlight.
const clamp = (v, a, b) => Math.max(a, Math.min(b, v));
export class Graph {
  constructor(canvas, o = {}) {
    this.c = canvas; this.ctx = canvas.getContext("2d"); this.host = canvas.parentElement;
    this.o = { colorBy: "kind", onSelect() {}, onHover() {}, labels: "auto", particles: false, ...o };
    this.nodes = []; this.links = []; this.by = new Map(); this.adj = new Map();
    this.view = { k: 1, x: 0, y: 0 }; this.hover = null; this.sel = null; this.focus = null; this.pathLinks = null;
    this.alpha = 1; this.run = false; this.drag = null; this.t0 = performance.now(); this.tip = o.tip || null;
    this.reduced = matchMedia("(prefers-reduced-motion: reduce)").matches;
    this.colors(); this.bind();
    this.ro = new ResizeObserver(() => this.resize()); this.ro.observe(this.host); this.resize();
    this.mo = new MutationObserver(() => { this.colors(); this.draw(); }); this.mo.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  }
  colors() {
    const s = getComputedStyle(document.documentElement), g = (n) => s.getPropertyValue(n).trim();
    this.col = { ink: g("--ink"), ink2: g("--ink-2"), ink3: g("--ink-3"), line: g("--line-2"), bg: g("--surface"), accent: g("--accent"), teal: g("--teal"), ochre: g("--ochre"), steel: g("--steel"), pal: Array.from({ length: 10 }, (_, i) => g("--c" + i)) };
  }
  color(n) {
    const c = this.col;
    if (n.kind === "question") return c.ink;
    if (n.kind === "chunk") return { vector: c.steel, fulltext: c.ochre, graph: c.teal }[(n.via || [])[0]] || c.ink2;
    if (this.o.colorBy === "community") return n.community != null ? c.pal[(n.community || 0) % 10] : c.ink3;
    if (this.o.colorBy === "type") { const t = this.typeIndex(n.type); return c.pal[t % 10]; }
    return n.seed ? c.accent : c.teal;
  }
  typeIndex(t) { this._types = this._types || []; let i = this._types.indexOf(t); if (i < 0) { this._types.push(t); i = this._types.length - 1; } return i; }
  resize() {
    const r = this.host.getBoundingClientRect(), d = Math.min(devicePixelRatio || 1, 2);
    this.w = r.width; this.h = r.height; this.c.width = Math.max(1, r.width * d); this.c.height = Math.max(1, r.height * d);
    this.ctx.setTransform(d, 0, 0, d, 0, 0); this.draw();
  }
  setData({ nodes, links }, { keep = false } = {}) {
    const old = this.by; this.by = new Map(); this.adj = new Map();
    const cx = this.w / 2, cy = this.h / 2;
    this.nodes = nodes.map((n, i) => {
      const prev = keep ? old.get(n.id) : null, a = (i / Math.max(1, nodes.length)) * Math.PI * 2, rad = 40 + Math.sqrt(i) * 28;
      const o = { vx: 0, vy: 0, born: performance.now() + i * 22, ...n, x: prev ? prev.x : n.kind === "question" ? cx : cx + Math.cos(a) * rad + (Math.random() - .5) * 20, y: prev ? prev.y : n.kind === "question" ? cy : cy + Math.sin(a) * rad + (Math.random() - .5) * 20 };
      o.r = this.radius(o); this.by.set(o.id, o); this.adj.set(o.id, new Set()); return o;
    });
    this.links = links.map((l) => ({ ...l, s: this.by.get(l.source), t: this.by.get(l.target) })).filter((l) => l.s && l.t);
    for (const l of this.links) { this.adj.get(l.s.id).add(l.t.id); this.adj.get(l.t.id).add(l.s.id); }
    this.sel = this.hover = null; this.focus = null; this.pathLinks = null;
    this.view = { k: 1, x: 0, y: 0 }; this.alpha = 1; this.start(); this.fitSoon = 60;
  }
  radius(n) { return n.kind === "question" ? 15 : n.kind === "chunk" ? 11 : 5 + Math.min(11, Math.sqrt((n.degree || 0) + (n.mentions || 0) * 1.5 + 1) * 2.2) + (n.seed ? 3 : 0); }
  start() { if (!this.run) { this.run = true; requestAnimationFrame((t) => this.frame(t)); } }
  frame(t) {
    if (!this.c.isConnected) { this.run = false; return; }
    if (this.alpha > 0.012) this.step(); else if (!this.o.particles && !this.hasAnim(t)) { this.draw(t); this.run = false; return; }
    this.draw(t); requestAnimationFrame((x) => this.frame(x));
  }
  hasAnim(t) { return this.nodes.some((n) => t - n.born < 700) || this.pathLinks; }
  step() {
    const N = this.nodes, a = this.alpha, cx = this.w / 2, cy = this.h / 2, big = N.length > 140;
    for (let i = 0; i < N.length; i++) for (let j = i + 1; j < N.length; j++) {
      const p = N[i], q = N[j]; let dx = q.x - p.x, dy = q.y - p.y, d2 = dx * dx + dy * dy + 0.01;
      if (big && d2 > 90000) continue; const d = Math.sqrt(d2), f = ((p.r + q.r) * 38 + 600) / d2 * a;
      dx = (dx / d) * f; dy = (dy / d) * f; p.vx -= dx; p.vy -= dy; q.vx += dx; q.vy += dy;
    }
    for (const l of this.links) {
      const dx = l.t.x - l.s.x, dy = l.t.y - l.s.y, d = Math.sqrt(dx * dx + dy * dy) || 1;
      const rest = l.kind === "retrieved" ? 130 : l.kind === "mentions" ? 70 : 80, f = (d - rest) * 0.06 * a;
      const fx = (dx / d) * f, fy = (dy / d) * f; l.s.vx += fx; l.s.vy += fy; l.t.vx -= fx; l.t.vy -= fy;
    }
    for (const n of N) {
      n.vx += (cx - n.x) * 0.012 * a; n.vy += (cy - n.y) * 0.012 * a;
      if (n.kind === "question") { n.vx += (cx - n.x) * 0.08; n.vy += (cy - n.y) * 0.08; }
      if (n === this.drag?.node) { n.vx = n.vy = 0; continue; }
      n.vx *= 0.82; n.vy *= 0.82; n.x += clamp(n.vx, -22, 22); n.y += clamp(n.vy, -22, 22);
    }
    this.alpha *= 0.985;
    if (this.fitSoon && --this.fitSoon === 0) this.fit();
  }
  fit() {
    if (!this.nodes.length) return;
    let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
    for (const n of this.nodes) { x0 = Math.min(x0, n.x - n.r); y0 = Math.min(y0, n.y - n.r); x1 = Math.max(x1, n.x + n.r); y1 = Math.max(y1, n.y + n.r); }
    const k = clamp(Math.min((this.w - 80) / (x1 - x0 || 1), (this.h - 100) / (y1 - y0 || 1)), 0.35, 2.6);
    this.view = { k, x: this.w / 2 - ((x0 + x1) / 2) * k, y: this.h / 2 - ((y0 + y1) / 2) * k };
  }
  tx(x, y) { return [x * this.view.k + this.view.x, y * this.view.k + this.view.y]; }
  active(n) { return !this.focus || this.focus.has(n.id); }
  draw(t = performance.now()) {
    const ctx = this.ctx, c = this.col; ctx.clearRect(0, 0, this.w, this.h);
    ctx.save(); ctx.translate(this.view.x, this.view.y); ctx.scale(this.view.k, this.view.k);
    const pl = this.pathLinks;
    for (const l of this.links) {
      const on = this.active(l.s) && this.active(l.t), inPath = pl && pl.has(l), dim = (this.focus || pl) && !(on && !pl) && !inPath;
      ctx.globalAlpha = dim ? 0.07 : l.kind === "mentions" ? 0.28 : l.kind === "co" ? 0.18 : 0.55;
      ctx.strokeStyle = inPath ? c.accent : l.kind === "retrieved" ? c.ink3 : c.line; ctx.lineWidth = (inPath ? 3 : l.kind === "rel" ? 1.6 : 1) / Math.sqrt(this.view.k);
      ctx.setLineDash(l.kind === "mentions" || l.kind === "co" ? [3, 4] : []); ctx.beginPath(); ctx.moveTo(l.s.x, l.s.y); ctx.lineTo(l.t.x, l.t.y); ctx.stroke();
      if ((this.o.particles && !dim && !this.reduced) || inPath) {
        const p = ((t / (inPath ? 900 : 2200)) + (l.s.x * 0.013)) % 1; ctx.setLineDash([]); ctx.globalAlpha = inPath ? 1 : 0.7; ctx.fillStyle = inPath ? c.accent : c.ink2;
        ctx.beginPath(); ctx.arc(l.s.x + (l.t.x - l.s.x) * p, l.s.y + (l.t.y - l.s.y) * p, inPath ? 3.4 : 2, 0, 7); ctx.fill();
      }
    }
    ctx.setLineDash([]);
    const showAll = this.o.labels === "all" || (this.view.k > 1.15 && this.nodes.length <= 45) || this.view.k > 2.2;
    for (const n of this.nodes) {
      const age = clamp((t - n.born) / 520, 0, 1); if (age <= 0) continue; const e = 1 - Math.pow(1 - age, 3), sc = e * (1 + 0.15 * Math.sin(age * Math.PI));
      const dim = (this.focus && !this.focus.has(n.id)) || (pl && !n.onPath), isSel = n === this.sel, isHov = n === this.hover;
      ctx.globalAlpha = dim ? 0.18 : 1; const r = n.r * sc, col = this.color(n);
      if (isSel || isHov || n.seed) { ctx.globalAlpha = dim ? 0.1 : 0.22; ctx.fillStyle = col; ctx.beginPath(); ctx.arc(n.x, n.y, r + 7 + (n.seed ? 2 * Math.sin(t / 400) : 0), 0, 7); ctx.fill(); ctx.globalAlpha = dim ? 0.18 : 1; }
      ctx.fillStyle = col; ctx.beginPath();
      if (n.kind === "chunk") { const s = r * 1.6; ctx.roundRect(n.x - s / 2, n.y - s / 2, s, s, 4); } else ctx.arc(n.x, n.y, r, 0, 7);
      ctx.fill(); ctx.lineWidth = 2 / this.view.k; ctx.strokeStyle = c.bg; ctx.stroke();
      if (n.kind === "chunk") { ctx.fillStyle = c.bg; ctx.font = "700 10px Instrument Sans, sans-serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText(n.label, n.x, n.y + 0.5); }
      else if (n.kind === "question") { ctx.fillStyle = c.bg; ctx.font = "700 12px Fraunces, serif"; ctx.textAlign = "center"; ctx.textBaseline = "middle"; ctx.fillText("?", n.x, n.y + 1); }
      else if ((showAll && !dim) || isHov || isSel || n.seed || (!dim && n.r > 12.5 && this.o.labels !== "none") || n.onPath) {
        ctx.fillStyle = c.ink; ctx.font = `${n.seed || isSel ? 700 : 600} ${11.5 / Math.max(0.8, Math.sqrt(this.view.k))}px Instrument Sans, sans-serif`; ctx.textAlign = "center"; ctx.textBaseline = "top";
        const txt = n.label.length > 24 ? n.label.slice(0, 23) + "…" : n.label; ctx.lineWidth = 3.5; ctx.strokeStyle = c.bg; ctx.strokeText(txt, n.x, n.y + r + 3); ctx.fillText(txt, n.x, n.y + r + 3);
      }
    }
    ctx.restore(); ctx.globalAlpha = 1;
  }
  setFocus(ids) { this.focus = ids ? new Set(ids) : null; this.draw(); }
  highlightPath(ids) {
    if (!ids || !ids.length) { this.pathLinks = null; this.nodes.forEach((n) => (n.onPath = false)); this.draw(); return; }
    const set = new Set(ids); this.nodes.forEach((n) => (n.onPath = set.has(n.id)));
    this.pathLinks = new Set(this.links.filter((l) => set.has(l.s.id) && set.has(l.t.id) && Math.abs(ids.indexOf(l.s.id) - ids.indexOf(l.t.id)) === 1));
    this.alpha = Math.max(this.alpha, 0.01); this.start();
  }
  select(id) { this.sel = id ? this.by.get(id) : null; this.focus = this.sel ? new Set([this.sel.id, ...this.adj.get(this.sel.id)]) : null; this.draw(); }
  pos(e) { const r = this.c.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  pick(px, py) {
    const x = (px - this.view.x) / this.view.k, y = (py - this.view.y) / this.view.k; let best = null, bd = 1e9;
    for (const n of this.nodes) { const d = Math.hypot(n.x - x, n.y - y); if (d < n.r + 5 && d < bd) { best = n; bd = d; } }
    return best;
  }
  bind() {
    const c = this.c;
    c.addEventListener("pointerdown", (e) => { const [px, py] = this.pos(e), n = this.pick(px, py); c.setPointerCapture(e.pointerId); this.drag = n ? { node: n, moved: false } : { pan: true, px, py, vx: this.view.x, vy: this.view.y, moved: false }; });
    c.addEventListener("pointermove", (e) => {
      const [px, py] = this.pos(e);
      if (this.drag?.node) { const n = this.drag.node; n.x = (px - this.view.x) / this.view.k; n.y = (py - this.view.y) / this.view.k; this.drag.moved = true; this.alpha = Math.max(this.alpha, 0.25); this.start(); }
      else if (this.drag?.pan) { this.view.x = this.drag.vx + px - this.drag.px; this.view.y = this.drag.vy + py - this.drag.py; this.drag.moved = Math.hypot(px - this.drag.px, py - this.drag.py) > 3; this.draw(); }
      else { const n = this.pick(px, py); if (n !== this.hover) { this.hover = n; c.style.cursor = n ? "pointer" : "grab"; this.o.onHover(n); this.showTip(n, px, py); this.draw(); } else if (n) this.showTip(n, px, py); }
    });
    c.addEventListener("pointerup", () => { const d = this.drag; this.drag = null; if (d && d.node && !d.moved) { this.select(d.node.id); this.o.onSelect(d.node); } else if (d && d.pan && !d.moved) { this.select(null); this.o.onSelect(null); } });
    c.addEventListener("pointerleave", () => { this.hover = null; this.showTip(null); this.draw(); });
    c.addEventListener("wheel", (e) => { e.preventDefault(); const [px, py] = this.pos(e), k0 = this.view.k, k = clamp(k0 * (e.deltaY < 0 ? 1.12 : 0.89), 0.25, 4); this.view.x = px - ((px - this.view.x) / k0) * k; this.view.y = py - ((py - this.view.y) / k0) * k; this.view.k = k; this.draw(); }, { passive: false });
    c.addEventListener("dblclick", () => { this.fit(); this.draw(); });
  }
  showTip(n, px, py) {
    if (!this.tip) return; if (!n) { this.tip.hidden = true; return; }
    this.tip.hidden = false; this.tip.textContent = n.kind === "chunk" ? `Passage ${n.label} · ${n.source || ""}` : n.kind === "question" ? "Your question" : n.label + (n.type ? ` · ${n.type}` : "") + (n.degree != null ? ` · ${n.degree} links` : "");
    this.tip.style.left = clamp(px + 14, 4, this.w - 200) + "px"; this.tip.style.top = clamp(py + 14, 4, this.h - 40) + "px";
  }
  destroy() { this.ro.disconnect(); this.mo.disconnect(); this.run = false; }
}
