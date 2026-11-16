import { h, $, $$, icon, clear, toast, debounce, fmt } from "./ui.js";
import { api, get, post, state } from "./api.js";

const VIEWS = [
  { id: "overview", label: "Overview", icon: "home", group: "Workspace" },
  { id: "ask", label: "Ask", icon: "ask", group: "Workspace" },
  { id: "library", label: "Library", icon: "lib", group: "Workspace" },
  { id: "graph", label: "Graph", icon: "graph", group: "Knowledge" },
  { id: "themes", label: "Themes", icon: "theme", group: "Knowledge" },
  { id: "claims", label: "Claims", icon: "claim", group: "Knowledge" },
  { id: "quality", label: "Graph quality", icon: "quality", group: "Knowledge" },
  { id: "compare", label: "Retrieval lab", icon: "compare", group: "Operate" },
  { id: "evaluation", label: "Evaluation", icon: "eval", group: "Operate" },
  { id: "observability", label: "Observability", icon: "obs", group: "Operate" },
  { id: "admin", label: "Admin", icon: "admin", group: "Operate", role: "admin" },
];
const loaders = {
  overview: () => import("./views/overview.js"), ask: () => import("./views/ask.js"), library: () => import("./views/library.js"),
  graph: () => import("./views/explore.js"), themes: () => import("./views/themes.js"), claims: () => import("./views/claims.js"),
  quality: () => import("./views/quality.js"), compare: () => import("./views/compare.js"), evaluation: () => import("./views/evaluation.js"),
  observability: () => import("./views/observability.js"), admin: () => import("./views/admin.js"),
};
const root = $("#app");
let current = null, health = {}, navBadges = {};

state.onAuthLost = () => { if (state.user) { state.user = null; boot(); } };

async function boot() {
  try {
    const me = await get("/api/auth/me");
    state.csrf = me.csrf; state.user = me.user; health = await get("/api/health");
    if (!me.user) return authScreen(me.needs_setup);
    shell();
  } catch (e) {
    clear(root).append(h("div", { class: "empty", style: { minHeight: "100vh" } }, icon("obs"), h("h2", null, "Can't reach the server"), h("p", null, e.message), h("button", { class: "btn primary", onClick: boot }, "Try again")));
  }
}

// ---------------------------------------------------------------- auth
function authScreen(setup) {
  let mode = setup ? "register" : "login";
  const form = h("form", { class: "auth-card", novalidate: true });
  const hero = h("div", { class: "auth-hero" },
    h("canvas"), h("div", { class: "row" }, h("div", { class: "logo", style: { width: "38px", height: "38px", borderRadius: "11px", background: "var(--accent)", display: "grid", placeItems: "center" } }, icon("graph")), h("b", { style: { fontFamily: "var(--serif)", fontSize: "22px" } }, "GraphRAG Atlas")),
    h("div", null, h("div", { class: "eyebrow" }, "Document intelligence"), h("h1", null, "Every answer, traced to its page."), h("p", null, "Upload PDFs, build a knowledge graph, and ask questions that span documents — with citations, evidence paths and contradictions surfaced for you.")),
    h("div", { class: "feat" }, ["Page citations", "Entity resolution", "Hybrid retrieval", "Communities", "Contradictions"].map((t) => h("span", { class: "chip" }, t))));
  const draw = () => {
    clear(form);
    const err = h("div", { class: "err", role: "alert" });
    const inputs = { email: h("input", { type: "email", autocomplete: "username", required: true, placeholder: "you@university.edu" }), password: h("input", { type: "password", autocomplete: mode === "login" ? "current-password" : "new-password", required: true, placeholder: mode === "login" ? "Your password" : "At least 8 characters" }), name: h("input", { type: "text", autocomplete: "name", placeholder: "Your name" }) };
    form.append(...[h("div", null, h("div", { class: "eyebrow" }, setup ? "First-time setup" : mode === "login" ? "Welcome back" : "Join the workspace"),
      h("h2", null, setup ? "Create the administrator account" : mode === "login" ? "Sign in" : "Create your account"),
      setup ? h("p", { class: "muted", style: { marginTop: "8px" } }, "This first account manages users and settings.") : null),
      mode === "register" ? h("label", { class: "field" }, "Name", inputs.name) : null,
      h("label", { class: "field" }, "Email", inputs.email), h("label", { class: "field" }, "Password", inputs.password), err,
      h("button", { class: "btn primary", type: "submit", style: { justifyContent: "center", padding: "11px" } }, mode === "login" ? "Sign in" : "Create account"),
      setup ? null : h("p", { class: "muted", style: { textAlign: "center", fontSize: "14px" } }, mode === "login" ? "New here? " : "Already registered? ",
        h("a", { href: "#", style: { color: "var(--accent)", fontWeight: 600 }, onClick: (e) => { e.preventDefault(); mode = mode === "login" ? "register" : "login"; draw(); } }, mode === "login" ? "Create an account" : "Sign in"))].filter(Boolean));
    form.onsubmit = async (e) => {
      e.preventDefault(); err.textContent = ""; const btn = form.querySelector("button[type=submit]"); btn.disabled = true;
      try {
        const r = await post(mode === "login" ? "/api/auth/login" : "/api/auth/register", { email: inputs.email.value, password: inputs.password.value, name: inputs.name.value });
        state.csrf = r.csrf; state.user = r.user; shell();
      } catch (ex) { err.textContent = ex.message; btn.disabled = false; }
    };
    setTimeout(() => inputs.email.focus(), 50);
  };
  draw();
  clear(root).append(h("div", { class: "auth" }, hero, h("div", { class: "auth-form" }, form)));
  netBackground(hero.querySelector("canvas"));
}
function netBackground(cv) {
  const ctx = cv.getContext("2d"); let w, hgt, pts = [];
  const size = () => { const r = cv.parentElement.getBoundingClientRect(); w = cv.width = r.width; hgt = cv.height = r.height; pts = Array.from({ length: Math.round(w * hgt / 14000) }, () => ({ x: Math.random() * w, y: Math.random() * hgt, vx: (Math.random() - .5) * .35, vy: (Math.random() - .5) * .35, r: 1.5 + Math.random() * 2.5 })); };
  size(); window.addEventListener("resize", size);
  const tick = () => {
    if (!cv.isConnected) return; ctx.clearRect(0, 0, w, hgt);
    for (const p of pts) { p.x += p.vx; p.y += p.vy; if (p.x < 0 || p.x > w) p.vx *= -1; if (p.y < 0 || p.y > hgt) p.vy *= -1; }
    for (let i = 0; i < pts.length; i++) for (let j = i + 1; j < pts.length; j++) { const d = Math.hypot(pts[i].x - pts[j].x, pts[i].y - pts[j].y); if (d < 140) { ctx.strokeStyle = `rgba(243,238,227,${0.22 * (1 - d / 140)})`; ctx.beginPath(); ctx.moveTo(pts[i].x, pts[i].y); ctx.lineTo(pts[j].x, pts[j].y); ctx.stroke(); } }
    pts.forEach((p, i) => { ctx.fillStyle = i % 7 === 0 ? "#FF6B3D" : "rgba(243,238,227,.7)"; ctx.beginPath(); ctx.arc(p.x, p.y, p.r, 0, 7); ctx.fill(); });
    requestAnimationFrame(tick);
  };
  tick();
}

// ---------------------------------------------------------------- shell
function shell() {
  const u = state.user, isOff = health.mode !== "live";
  const groups = {};
  VIEWS.filter((v) => !v.role || v.role === u.role).forEach((v) => (groups[v.group] ||= []).push(v));
  const rail = h("aside", { class: "rail", "aria-label": "Primary" },
    h("a", { class: "brand", href: "#overview" }, h("span", { class: "logo" }, svgLogo()), h("span", null, h("b", null, "Atlas"), h("small", null, "GraphRAG"))),
    h("nav", { class: "nav", id: "nav" }, Object.entries(groups).map(([g, vs]) => [h("div", { class: "nav-group" }, g), vs.map((v) => h("a", { href: "#" + v.id, dataset: { view: v.id } }, icon(v.icon), v.label, v.id === "claims" ? h("span", { class: "badge", id: "badge-claims", hidden: true }) : null))])),
    h("div", { class: "rail-foot" },
      h("div", { class: "engine" + (isOff ? " off" : ""), role: "status" }, h("span", { class: "dot" }), h("div", null, h("b", null, isOff ? "Offline engine" : health.llm.model), h("span", null, isOff ? "Extractive answers · add GROQ_API_KEY for the LLM" : "Groq · live generation"))),
      h("div", { class: "user-chip" }, h("div", { class: "avatar" }, (u.name || u.email)[0].toUpperCase()), h("div", null, h("b", null, u.name), h("span", null, u.role)),
        h("button", { class: "icon-btn", title: "Sign out", "aria-label": "Sign out", onClick: async () => { await post("/api/auth/logout"); state.user = null; boot(); } }, icon("out")))));
  const crumb = h("span", { class: "crumb", id: "crumb" });
  const top = h("header", { class: "topbar" },
    h("button", { class: "icon-btn menu-btn", "aria-label": "Menu", onClick: () => rail.classList.toggle("open") }, icon("menu")), crumb,
    h("span", { class: "chip " + (isOff ? "ochre" : "teal"), style: { marginLeft: "auto" }, title: isOff ? "No language model: answers are quotes from your documents. Set GROQ_API_KEY and restart to go online." : "Answers are written by " + health.llm.model }, h("i", { style: { background: "currentColor", borderRadius: "50%" } }), isOff ? "Offline mode" : "Online · " + health.llm.model),
    h("button", { class: "cmdk", onClick: palette, "aria-label": "Open command palette" }, icon("search"), h("span", null, "Jump to a page or entity"), h("kbd", null, "Ctrl K")),
    h("button", { class: "icon-btn", id: "theme", "aria-label": "Toggle theme", onClick: toggleTheme }, icon(document.documentElement.dataset.theme === "dark" ? "sun" : "moon")));
  const main = h("main", { class: "main", id: "main", tabindex: "-1" }, top, h("div", { id: "outlet" }));
  clear(root).append(h("div", { class: "shell" }, rail, main));
  window.onscroll = () => top.classList.toggle("scrolled", scrollY > 6);
  window.onhashchange = route; document.onkeydown = (e) => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); palette(); } };
  rail.addEventListener("click", (e) => e.target.closest("a") && rail.classList.remove("open"));
  refreshBadges(); route();
}
function svgLogo() { const s = document.createElementNS("http://www.w3.org/2000/svg", "svg"); s.setAttribute("viewBox", "0 0 32 32"); s.setAttribute("width", "20"); s.setAttribute("height", "20"); s.innerHTML = '<path d="M7 23 16 8l9 15z" fill="none" stroke="#FFF8F2" stroke-width="2" stroke-linejoin="round"/><circle cx="16" cy="8" r="3" fill="#FFF8F2"/><circle cx="7" cy="23" r="2.4" fill="#FFF8F2"/><circle cx="25" cy="23" r="2.4" fill="#FFF8F2"/>'; return s; }
function toggleTheme() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  document.documentElement.dataset.theme = next; try { localStorage.setItem("atlas-theme", next); } catch {}
  const b = $("#theme"); b.replaceChildren(icon(next === "dark" ? "sun" : "moon"));
}
async function refreshBadges() {
  try { const c = await get("/api/contradictions?status=open"); const b = $("#badge-claims"); if (b) { b.hidden = !c.length; b.textContent = c.length; } } catch {}
}

async function route() {
  if (!state.user || !$("#crumb")) return;
  const id = (location.hash.slice(1).split("?")[0]) || "overview", v = VIEWS.find((x) => x.id === id) || VIEWS[0];
  if (v.role && state.user.role !== v.role) { location.hash = "#overview"; return; }
  $$("#nav a").forEach((a) => a.classList.toggle("on", a.dataset.view === v.id));
  $("#crumb").textContent = v.group + " / " + v.label; document.title = v.label + " · GraphRAG Atlas";
  if (current && current.destroy) try { current.destroy(); } catch {}
  const outlet = clear($("#outlet")); outlet.append(h("div", { class: "view" }, h("div", { class: "skeleton", style: { height: "40px", width: "280px", marginBottom: "20px" } }), h("div", { class: "grid g3" }, [1, 2, 3].map(() => h("div", { class: "skeleton", style: { height: "120px" } })))));
  scrollTo(0, 0);
  try {
    const mod = await loaders[v.id]();
    current = await mod.render({ user: state.user, health, refreshBadges, go: (x) => (location.hash = "#" + x) });
    if (location.hash.slice(1).split("?")[0] !== v.id && location.hash) return;
    clear(outlet).append(current.el); if (current.mounted) current.mounted();
  } catch (e) {
    console.error(e); clear(outlet).append(h("div", { class: "view" }, h("div", { class: "empty" }, icon("obs"), h("h3", null, "This page failed to load"), h("p", null, e.message), h("button", { class: "btn", onClick: route }, "Retry"))));
  }
}

// ---------------------------------------------------------------- command palette
function palette() {
  if ($(".palette")) return;
  const scrim = h("div", { class: "scrim", style: { zIndex: 79 } }), input = h("input", { type: "text", placeholder: "Jump to a page, find an entity, or ask a question…", "aria-label": "Command palette" }), list = h("ul");
  let items = [], idx = 0, seq = 0;
  const close = () => { box.remove(); scrim.remove(); };
  const run = (it) => { close(); it.run(); };
  const paint = () => { clear(list); items.forEach((it, i) => list.append(h("li", { class: i === idx ? "on" : "", onClick: () => run(it), onMouseenter: () => { idx = i; $$("li", list).forEach((l, j) => l.classList.toggle("on", j === i)); } }, icon(it.icon), it.label, h("small", null, it.hint || "")))); };
  const build = async () => {
    const q = input.value.trim().toLowerCase(), mine = ++seq;
    items = VIEWS.filter((v) => (!v.role || v.role === state.user.role) && (!q || v.label.toLowerCase().includes(q))).map((v) => ({ label: v.label, icon: v.icon, hint: "Page", run: () => (location.hash = "#" + v.id) }));
    if (q.length > 2) items.push({ label: `Ask: “${input.value.trim()}”`, icon: "ask", hint: "Question", run: () => { sessionStorage.setItem("ask-prefill", input.value.trim()); location.hash = "#ask"; if (location.hash === "#ask") window.dispatchEvent(new Event("ask-prefill")); } });
    idx = 0; paint();
    if (q.length > 1) { try { const ents = await get("/api/entities/search?q=" + encodeURIComponent(q)); if (mine !== seq) return; items.push(...ents.slice(0, 6).map((e) => ({ label: e.name, icon: "graph", hint: e.type || "Entity", run: () => { sessionStorage.setItem("graph-focus", e.id); location.hash = "#graph"; window.dispatchEvent(new Event("graph-focus")); } }))); paint(); } catch {} }
  };
  input.addEventListener("input", debounce(build, 120));
  input.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); else if (e.key === "ArrowDown") { idx = Math.min(items.length - 1, idx + 1); paint(); e.preventDefault(); } else if (e.key === "ArrowUp") { idx = Math.max(0, idx - 1); paint(); e.preventDefault(); } else if (e.key === "Enter" && items[idx]) run(items[idx]); });
  scrim.addEventListener("click", close);
  const box = h("div", { class: "palette", role: "dialog" }, input, list);
  document.body.append(scrim, box); input.focus(); build();
}

boot();
