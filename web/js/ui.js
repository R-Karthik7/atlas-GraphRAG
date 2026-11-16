// DOM helpers, icons, toasts, drawers, formatting.
export function h(tag, attrs, ...kids) {
  const el = document.createElement(tag);
  if (attrs) for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") el.className = v;
    else if (k === "style" && typeof v === "object") Object.assign(el.style, v);
    else if (k.startsWith("on") && typeof v === "function") el.addEventListener(k.slice(2).toLowerCase(), v);
    else if (k === "dataset") Object.assign(el.dataset, v);
    else if (v === true) el.setAttribute(k, "");
    else el.setAttribute(k, v);
  }
  add(el, kids);
  return el;
}
export function add(el, kids) {
  for (const k of kids.flat(Infinity)) {
    if (k == null || k === false) continue;
    el.append(k instanceof Node ? k : document.createTextNode(String(k)));
  }
  return el;
}
export const $ = (s, r = document) => r.querySelector(s);
export const $$ = (s, r = document) => [...r.querySelectorAll(s)];
export const clear = (el) => { el.replaceChildren(); return el; };

const P = {
  home: "M3 11l9-8 9 8M5 10v10h5v-6h4v6h5V10",
  ask: "M4 5h16v11H9l-5 4z M8 9h8M8 12h5",
  lib: "M5 3h9l5 5v13H5z M14 3v5h5 M8 13h8M8 17h8",
  graph: "M6 7a2 2 0 100-.01M18 6a2 2 0 100-.01M12 18a2 2 0 100-.01M7.6 8.2l3.2 7.6M16.4 7.6l-3.2 8M8 7h8",
  theme: "M12 3a9 9 0 100 18 9 9 0 000-18z M12 7a5 5 0 100 10 5 5 0 000-10z M12 11a1 1 0 100 2 1 1 0 000-2z",
  claim: "M9 12l2 2 4-4 M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6z",
  quality: "M4 20V10M10 20V4M16 20v-8M22 20H2",
  compare: "M5 4v16M12 8v12M19 12v8",
  eval: "M4 19l5-6 4 3 7-10 M4 5v14h16",
  obs: "M3 12h4l2-6 4 12 2-6h6",
  admin: "M12 3l8 4v5c0 4.5-3.4 7.8-8 9-4.6-1.2-8-4.5-8-9V7z M9.5 12.5l1.8 1.8 3.4-3.6",
  search: "M11 4a7 7 0 100 14 7 7 0 000-14zM21 21l-4.3-4.3",
  send: "M4 12h15M13 6l6 6-6 6",
  upload: "M12 16V5M7.5 9.5L12 5l4.5 4.5M5 15v3.5A1.5 1.5 0 006.5 20h11a1.5 1.5 0 001.5-1.5V15",
  sun: "M12 8a4 4 0 100 8 4 4 0 000-8zM12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5L19 19M5 19l1.5-1.5M17.5 6.5L19 5",
  moon: "M20 14.5A8 8 0 019.5 4 8.5 8.5 0 1020 14.5z",
  x: "M6 6l12 12M18 6L6 18", up: "M7 11l5-5 5 5M12 6v13", down: "M7 13l5 5 5-5M12 18V5",
  bolt: "M13 2L4 14h7l-1 8 9-12h-7z", plus: "M12 5v14M5 12h14", check: "M5 13l4 4L19 7",
  menu: "M4 7h16M4 12h16M4 17h16", out: "M9 4H5v16h4M16 8l4 4-4 4M20 12H9", file: "M6 3h8l4 4v14H6z M14 3v5h5",
  merge: "M6 4v6a4 4 0 004 4h4a4 4 0 014 4v2M6 4l-2 2M6 4l2 2M18 20l-2-2M18 20l2-2", link: "M10 14a4 4 0 005.7 0l3-3a4 4 0 00-5.7-5.7l-1 1M14 10a4 4 0 00-5.7 0l-3 3a4 4 0 005.7 5.7l1-1",
  refresh: "M20 11a8 8 0 10-2.3 5.7M20 4v7h-7", archive: "M4 7h16v4H4zM6 11v9h12v-9M10 15h4", trash: "M5 7h14M10 7V4h4v3M7 7l1 13h8l1-13",
  eye: "M2 12s4-7 10-7 10 7 10 7-4 7-10 7S2 12 2 12zM12 9a3 3 0 100 6 3 3 0 000-6z", lock: "M6 11h12v9H6zM8.5 11V8a3.5 3.5 0 017 0v3",
  thumbup: "M7 11v9H4v-9zM7 11l4-7a2 2 0 012 2v4h5.5a1.5 1.5 0 011.5 1.8l-1.3 6.6A2 2 0 0116.7 20H7",
  thumbdown: "M17 13V4h3v9zM17 13l-4 7a2 2 0 01-2-2v-4H5.5A1.5 1.5 0 014 12.2l1.3-6.6A2 2 0 017.3 4H17",
  cube: "M12 3l8 4.5v9L12 21l-8-4.5v-9zM12 12l8-4.5M12 12L4 7.5M12 12v9",
};
export function icon(name, cls) {
  const ns = "http://www.w3.org/2000/svg";
  const s = document.createElementNS(ns, "svg");
  s.setAttribute("viewBox", "0 0 24 24"); s.setAttribute("aria-hidden", "true");
  if (cls) s.setAttribute("class", cls);
  const p = document.createElementNS(ns, "path"); p.setAttribute("d", P[name] || P.cube); s.append(p);
  return s;
}
export function svg(tag, attrs = {}, ...kids) {
  const el = document.createElementNS("http://www.w3.org/2000/svg", tag);
  for (const [k, v] of Object.entries(attrs)) if (v != null) el.setAttribute(k, v);
  for (const k of kids.flat()) if (k) el.append(k);
  return el;
}

export function toast(msg, kind = "") {
  const t = h("div", { class: "toast " + kind, role: "status" }, msg);
  $("#toasts").append(t);
  setTimeout(() => { t.style.transition = "opacity .3s, transform .3s"; t.style.opacity = 0; t.style.transform = "translateX(20px)"; setTimeout(() => t.remove(), 300); }, kind === "bad" ? 6000 : 3200);
}

// ---- formatting
export const fmt = {
  ms: (v) => v == null ? "–" : v >= 1000 ? (v / 1000).toFixed(2) + " s" : Math.round(v) + " ms",
  bytes: (b) => b > 1048576 ? (b / 1048576).toFixed(1) + " MB" : Math.max(1, Math.round(b / 1024)) + " KB",
  cost: (c) => !c ? "$0" : c < 0.0001 ? "<$0.0001" : "$" + (c < 0.01 ? c.toFixed(5) : c.toFixed(3)),
  num: (n) => n == null ? "–" : Number(n).toLocaleString(),
  pct: (n, d = 0) => n == null ? "–" : (n * 100).toFixed(d) + "%",
  ago(ts) {
    const s = Math.max(0, Date.now() / 1000 - ts);
    if (s < 60) return "just now"; if (s < 3600) return Math.floor(s / 60) + " min ago";
    if (s < 86400) return Math.floor(s / 3600) + " h ago"; return Math.floor(s / 86400) + " d ago";
  },
  date: (ts) => new Date(ts * 1000).toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" }),
};

export function countUp(el, to, { dur = 900, dec = 0, suffix = "" } = {}) {
  if (matchMedia("(prefers-reduced-motion: reduce)").matches || !isFinite(to)) { el.textContent = Number(to).toLocaleString(undefined, { maximumFractionDigits: dec }) + suffix; return; }
  const t0 = performance.now();
  const tick = (t) => {
    const p = Math.min(1, (t - t0) / dur), e = 1 - Math.pow(1 - p, 3);
    el.textContent = (to * e).toLocaleString(undefined, { minimumFractionDigits: dec, maximumFractionDigits: dec }) + suffix;
    if (p < 1 && el.isConnected) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

export function skeleton(n = 3, height = 18) {
  return h("div", { class: "grid" }, Array.from({ length: n }, () => h("div", { class: "skeleton", style: { height: height + "px" } })));
}
export function empty(ic, title, text, action) {
  return h("div", { class: "empty" }, icon(ic), h("h3", null, title), h("p", null, text), action);
}

export function drawer(title, body, { wide } = {}) {
  const prev = document.activeElement;
  const scrim = h("div", { class: "scrim" });
  const close = () => { d.remove(); scrim.remove(); document.removeEventListener("keydown", esc); prev && prev.focus && prev.focus(); };
  const esc = (e) => e.key === "Escape" && close();
  const d = h("aside", { class: "drawer", role: "dialog", "aria-label": title, style: wide ? { width: "min(920px,100%)" } : null },
    h("header", null, h("h2", null, title), h("button", { class: "icon-btn", "aria-label": "Close", onClick: close }, icon("x"))),
    h("div", { class: "body" }, body));
  scrim.addEventListener("click", close);
  document.addEventListener("keydown", esc);
  document.body.append(scrim, d);
  d.querySelector("button").focus();
  return { close, el: d };
}
export function confirmDialog(title, text, okLabel = "Confirm", danger = false) {
  return new Promise((res) => {
    const scrim = h("div", { class: "scrim" });
    const done = (v) => { m.remove(); scrim.remove(); res(v); };
    const m = h("div", { class: "modal", role: "alertdialog" }, h("h2", null, title), h("p", { class: "muted", style: { margin: "10px 0 20px" } }, text),
      h("div", { class: "row" }, h("span", { class: "grow" }), h("button", { class: "btn", onClick: () => done(false) }, "Cancel"),
        h("button", { class: "btn " + (danger ? "dark" : "primary"), onClick: () => done(true) }, okLabel)));
    scrim.addEventListener("click", () => done(false));
    document.body.append(scrim, m);
    m.querySelector(".btn.primary,.btn.dark").focus();
  });
}

// Safe rich text for answers: **bold**, [n] citations, newlines.
export function richText(text, onCite, cites = new Map()) {
  const frag = document.createDocumentFragment();
  const re = /(\*\*[^*]+\*\*|\[\d+\])/g;
  for (const part of String(text).split(re)) {
    if (!part) continue;
    if (/^\*\*[^*]+\*\*$/.test(part)) frag.append(h("strong", null, part.slice(2, -2)));
    else if (/^\[\d+\]$/.test(part)) {
      const n = Number(part.slice(1, -1));
      frag.append(h("button", { class: "cite", "aria-label": "Source " + n, dataset: { n }, onClick: () => onCite && onCite(n) }, n));
    } else frag.append(part);
  }
  return frag;
}

export function toneFor(i) { return `var(--c${i % 10})`; }
export function debounce(fn, ms = 250) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }
export function chipForSource(s) {
  const map = { vector: ["Vector", "steel"], fulltext: ["Full-text", "ochre"], graph: ["Graph", "teal"], claims: ["Claims", "bad"], community: ["Theme", "accent"] };
  const [label, tone] = map[s] || [s, ""];
  return h("span", { class: "chip " + tone }, h("i"), label);
}
