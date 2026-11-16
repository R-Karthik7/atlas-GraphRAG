import { h, $, $$, icon, clear, toast, fmt, richText, chipForSource, empty } from "../ui.js";
import { get, post, stream } from "../api.js";
import { Graph } from "../graph.js";
import { gauge } from "../charts.js";
import { openSource } from "./source.js";

const MODES = [["auto", "Auto"], ["vector", "Vector"], ["fulltext", "Full-text"], ["graph", "Graph"], ["hybrid", "Hybrid"], ["global", "Global"]];
const MODE_NOTE = { auto: "The router picks the strategy for each question and shows why.", vector: "Matches meaning with embeddings.", fulltext: "Exact keyword matching with BM25.", graph: "Follows entity relationships across documents.", hybrid: "Vector + full-text + graph, fused and reranked.", global: "Answers corpus-wide questions from theme reports." };
const STEPS = [["route", "Route"], ["retrieve", "Retrieve"], ["rerank", "Rerank"], ["generate", "Generate"], ["verify", "Verify"]];
const load = (k, d) => { try { return JSON.parse(localStorage.getItem("atlas-ask-" + k)) ?? d; } catch { return d; } };
const save = (k, v) => { try { localStorage.setItem("atlas-ask-" + k, JSON.stringify(v)); } catch {} };

export async function render({ user }) {
  const S = { mode: load("mode", "auto"), top_k: load("topk", 5), hops: load("hops", 2), scope: "all", rerank: true, style: load("style", "detailed"), busy: false, result: null, marks: {}, ctrl: null };
  let graph = null;
  const ta = h("textarea", { rows: 1, maxlength: 1000, placeholder: "Ask anything about your documents…", "aria-label": "Your question" });
  const send = h("button", { class: "send", type: "submit", "aria-label": "Ask" }, icon("send"));
  const scope = h("select", { "aria-label": "Search scope" }, h("option", { value: "all" }, "All documents"));
  const pill = h("span", { class: "pill" });
  const modeBtns = MODES.map(([id, label]) => h("button", { type: "button", dataset: { mode: id }, onClick: () => setMode(id) }, label));
  const modes = h("div", { class: "modes", role: "radiogroup" }, pill, modeBtns);
  const note = h("p", { class: "faint", style: { fontSize: "12.5px", marginTop: "8px" } });
  const stepper = (key, label, lo, hi) => { const out = h("output", null, S[key]); return h("div", { class: "stepper" }, label, h("button", { type: "button", "aria-label": "Fewer " + label, onClick: () => { S[key] = Math.max(lo, S[key] - 1); out.textContent = S[key]; save(key === "top_k" ? "topk" : "hops", S[key]); } }, "−"), out, h("button", { type: "button", "aria-label": "More " + label, onClick: () => { S[key] = Math.min(hi, S[key] + 1); out.textContent = S[key]; save(key === "top_k" ? "topk" : "hops", S[key]); } }, "+")); };
  const styleSel = h("select", { "aria-label": "Answer length", style: { width: "auto", padding: "6px 10px", fontSize: "13px" }, title: "Short = one or two sentences. Detailed = full explanation.", onChange: (e) => { S.style = e.target.value; save("style", S.style); } }, [["detailed", "Detailed answer"], ["concise", "Short answer"]].map(([v, l]) => h("option", { value: v, selected: v === S.style }, l)));
  const rerankBox = h("input", { type: "checkbox", checked: true, onChange: (e) => (S.rerank = e.target.checked) });
  const form = h("form", { class: "card composer", autocomplete: "off" }, h("div", { class: "row", style: { alignItems: "flex-start" } }, ta, send),
    h("div", { class: "tools" }, scope, modes, stepper("top_k", "Passages", 1, 10), stepper("hops", "Hops", 1, 4), styleSel, h("label", { class: "stepper", style: { gap: "6px" } }, rerankBox, "Rerank")), note);
  const examples = h("div", { class: "examples" });
  const pipe = h("div", { class: "card pipe", hidden: true });
  const out = h("div");
  const mapHost = h("div", { class: "card map" }, h("canvas"), h("div", { class: "tip", hidden: true }), h("div", { class: "cap" }, "Ask a question to see the evidence map."));
  const side = h("div", { class: "side" });
  const tabs = h("div", { class: "tabs" }); const tabBody = h("div");
  const el = h("section", { class: "view" },
    h("div", { class: "hero-q" }, h("div", { class: "eyebrow" }, "Ask"), h("h1", null, "Ask your library, ", h("em", null, "see the evidence."))),
    h("div", { class: "ask" }, h("div", null, form, examples, pipe, out), side));

  function setMode(id) {
    S.mode = id; save("mode", id); note.textContent = MODE_NOTE[id];
    modeBtns.forEach((b) => { const on = b.dataset.mode === id; b.classList.toggle("on", on); b.setAttribute("role", "radio"); b.setAttribute("aria-checked", on); if (on) requestAnimationFrame(() => { pill.style.left = b.offsetLeft + "px"; pill.style.width = b.offsetWidth + "px"; }); });
  }
  const resize = () => { ta.style.height = "auto"; ta.style.height = Math.min(180, ta.scrollHeight) + "px"; };
  ta.addEventListener("input", resize);
  ta.addEventListener("keydown", (e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); form.requestSubmit(); } });
  form.addEventListener("submit", (e) => { e.preventDefault(); ask(); });

  // ---- pipeline
  function pipeInit() {
    pipe.hidden = false; clear(pipe);
    STEPS.forEach(([id, label], i) => { if (i) pipe.append(h("div", { class: "ln", dataset: { ln: id } }, h("i"))); pipe.append(h("div", { class: "st", dataset: { st: id } }, h("span", { class: "d" }, i + 1), label, h("small"))); });
  }
  function pipeSet(id, state, ms) {
    const st = $(`[data-st="${id}"]`, pipe); if (!st) return; st.className = "st " + state; if (state === "done") { st.querySelector(".d").textContent = "✓"; const ln = $(`[data-ln="${id}"]`, pipe); ln && ln.classList.add("done"); }
    if (ms != null) st.querySelector("small").textContent = fmt.ms(ms);
  }

  async function ask(question) {
    question = (question ?? ta.value).trim(); if (!question || S.busy) return; ta.value = question; resize();
    S.busy = true; send.disabled = true; S.marks = {}; S.result = null; S.ctrl?.abort(); S.ctrl = new AbortController();
    clear(out); pipeInit(); pipeSet("route", "on");
    const result = {}; let answerCard = null;
    const body = { question, mode: S.mode, top_k: S.top_k, hops: S.hops, scope: S.scope, rerank: S.rerank, style: S.style };
    try {
      await stream("/api/ask/stream", body, {
        stage: ({ stage }) => { if (stage === "retrieve") { pipeSet("route", "done"); pipeSet("retrieve", "on"); } if (stage === "generate") { pipeSet("generate", "on"); } },
        retrieved: (r) => {
          Object.assign(result, r); pipeSet("route", "done"); pipeSet("retrieve", "done"); pipeSet("rerank", r.trace.reranked ? "done" : "done");
          answerCard = h("article", { class: "card answer-card", "aria-live": "polite" }, routeBox(r), h("div", { class: "answer-text" }, h("span", { class: "faint" }, "Writing the answer…"), h("span", { class: "cur" })), h("div", { class: "meta-row", hidden: true }));
          out.append(answerCard, sourcesBox(r)); showSide(r);
        },
        answer: (a) => {
          Object.assign(result, a); const sp = Object.fromEntries((a.spans || []).map((s) => [s.name, s.ms]));
          pipeSet("route", "done", sp.route); pipeSet("retrieve", "done", ["retrieve.vector", "retrieve.fulltext", "retrieve.graph", "retrieve.communities", "retrieve.claims", "fuse"].reduce((t, k) => t + (sp[k] || 0), 0));
          pipeSet("rerank", "done", sp.rerank); pipeSet("generate", "done", sp.generate); pipeSet("verify", "done", sp.verify);
          reveal(answerCard, result);
        },
        error: ({ message }) => { toast(message, "bad"); pipe.hidden = true; out.append(h("div", { class: "card pad", style: { borderColor: "var(--bad)" } }, h("b", null, "Couldn't answer that"), h("p", { class: "muted" }, message))); },
      }, S.ctrl.signal);
    } catch (e) { if (e.name !== "AbortError") { toast(e.message, "bad"); pipe.hidden = true; } }
    S.busy = false; send.disabled = false; S.result = result;
    if (result.question && history.replaceState) save("last", question);
  }

  function routeBox(r) {
    const ro = r.route || {};
    return h("div", { class: "route" }, icon("bolt"), h("div", null, h("b", null, { auto: "Auto", relational: "Relational", lookup: "Lookup", semantic: "Semantic", global: "Global", claims: "Conflicts", vector: "Vector", fulltext: "Full-text", graph: "Graph", hybrid: "Hybrid" }[ro.route] || ro.route, " route"), " · ", ro.reason, ro.seeds && ro.seeds.length ? h("div", { style: { marginTop: "6px" }, class: "row wrap" }, h("span", { class: "faint" }, "Entities found:"), ro.seeds.map((s) => h("span", { class: "chip accent" }, s))) : null));
  }

  function reveal(card, r) {
    const target = $(".answer-text", card), meta = $(".meta-row", card), text = r.answer || "", words = text.split(/(\s+)/), cites = (n) => openCite(r, n);
    let i = 0; const per = Math.max(1, Math.ceil(words.length / 90)), delay = Math.max(8, Math.min(30, 2000 / Math.max(1, words.length / per)));
    const finish = () => { clear(target).append(richText(text, cites)); attachPopovers(target, r); showMeta(meta, r); meta.hidden = false; };
    if (matchMedia("(prefers-reduced-motion: reduce)").matches) return finish();
    const tick = () => { if (!card.isConnected) return; i = Math.min(words.length, i + per * 2); clear(target).append(richText(words.slice(0, i).join(""), cites), h("span", { class: "cur" })); if (i < words.length) setTimeout(tick, delay); else finish(); };
    tick();
  }
  function openCite(r, n) { const c = r.chunks[n - 1]; if (!c) return; if (c.kind === "community") return c.rep_chunks?.[0] && openSource(c.rep_chunks[0]); openSource(c.chunk_id, { terms: (r.question || "").split(/\W+/) }); }
  function attachPopovers(root, r) {
    $$(".cite", root).forEach((b) => {
      let pop; const show = () => { const c = r.chunks[Number(b.dataset.n) - 1]; if (!c) return; const rc = b.getBoundingClientRect(); pop = h("div", { class: "pop" }, h("b", null, c.title), h("span", { class: "faint" }, c.where || ""), h("p", null, c.text)); pop.style.left = Math.min(innerWidth - 350, rc.left) + "px"; pop.style.top = (rc.bottom + 8 > innerHeight - 200 ? rc.top - 190 : rc.bottom + 8) + "px"; document.body.append(pop); };
      b.addEventListener("mouseenter", show); b.addEventListener("mouseleave", () => pop && pop.remove()); b.addEventListener("blur", () => pop && pop.remove());
    });
  }

  function showMeta(meta, r) {
    const g = r.grounding || {}, score = g.score, u = r.usage || {};
    const ring = score == null ? null : h("div", { class: "ring", title: `Grounding: ${g.supported}/${g.total} sentences supported by cited passages` }, gauge(score * 100, { size: 44, stroke: 5, color: score >= 0.75 ? "var(--teal)" : score >= 0.5 ? "var(--ochre)" : "var(--bad)" }), h("b", null, Math.round(score * 100)));
    clear(meta).append(ring, ring ? h("span", null, "grounded", g.unsupported?.length ? h("span", { class: "chip ochre", style: { marginLeft: "6px" }, title: g.unsupported.join("\n") }, g.unsupported.length + " weak") : null) : h("span", { class: "chip" }, r.refused ? "Not in your documents" : "No check needed"),
      h("span", null, "Time ", h("b", null, fmt.ms(r.latency_ms))), h("span", null, "Tokens ", h("b", null, fmt.num((u.tokens_in || 0) + (u.tokens_out || 0)))), h("span", null, "Cost ", h("b", null, u.calls ? fmt.cost(u.cost) : "$0 (offline)")), h("span", { class: "faint" }, r.model),
      h("span", { class: "sp fb" }, feedback(r)));
    if (r.notice) meta.before(h("div", { class: "chip ochre", style: { marginTop: "12px" } }, r.notice));
  }

  function feedback(r) {
    const box = h("span", { class: "fb" }); const up = h("button", { class: "icon-btn", "aria-label": "Good answer", title: "Good answer" }, icon("thumbup")), down = h("button", { class: "icon-btn", "aria-label": "Bad answer", title: "Needs work" }, icon("thumbdown"));
    const submit = async (rating, tags = [], comment = "") => { try { await post("/api/feedback", { query_id: r.id, rating, tags, comment, chunk_marks: S.marks }); toast(rating > 0 ? "Thanks — marked helpful" : "Thanks — we'll review it", "good"); up.disabled = down.disabled = true; (rating > 0 ? up : down).style.color = rating > 0 ? "var(--teal)" : "var(--bad)"; box.querySelector(".fbform")?.remove(); } catch (e) { toast(e.message, "bad"); } };
    up.onclick = () => submit(1);
    down.onclick = () => {
      if (box.querySelector(".fbform")) return; const picked = new Set(); const c = h("textarea", { rows: 2, placeholder: "What went wrong? (optional)", maxlength: 500 });
      const f = h("div", { class: "card pad fbform", style: { position: "absolute", right: "26px", marginTop: "44px", width: "320px", zIndex: 20, display: "grid", gap: "10px" } }, h("b", null, "What was wrong?"),
        h("div", { class: "row wrap" }, ["Wrong answer", "Incomplete", "Bad sources", "Made things up"].map((t) => h("button", { type: "button", class: "chip", onClick: (e) => { picked.has(t) ? picked.delete(t) : picked.add(t); e.currentTarget.classList.toggle("accent"); } }, t))), c,
        h("div", { class: "row" }, h("button", { type: "button", class: "btn sm", onClick: () => f.remove() }, "Cancel"), h("button", { type: "button", class: "btn sm primary sp", onClick: () => submit(-1, [...picked], c.value) }, "Send")));
      box.append(f);
    };
    box.append(h("span", { class: "faint", style: { fontSize: "12px" } }, "Helpful?"), up, down); return box;
  }

  function sourcesBox(r) {
    const wrap = h("div", { class: "sources stagger" }, h("div", { class: "row" }, h("h2", null, r.route?.route === "global" ? "Theme reports" : "Sources"), h("span", { class: "chip" }, r.chunks.length)));
    if (!r.chunks.length) wrap.append(empty("lib", "Nothing relevant found", "Try rephrasing, widening the scope, or adding documents."));
    r.chunks.forEach((c, i) => {
      const isC = c.kind === "community", mv = c.rank_before && c.rank_after !== undefined ? c.rank_before - c.rank : 0;
      const feats = c.features ? Object.entries(c.features).filter(([k]) => k !== "length").slice(0, 8) : [];
      const card = h("div", { class: "card src", style: { "--i": i } },
        h("div", { class: "top" }, h("span", { class: "num" }, i + 1), h("b", null, c.title), c.where ? h("span", { class: "chip" }, c.where) : null, c.retrieval_sources.map(chipForSource), c.flagged ? h("span", { class: "chip bad" }, "flagged") : null,
          mv ? h("span", { class: "move " + (mv > 0 ? "up" : "down"), title: `Reranker moved this from #${c.rank_before} to #${c.rank}` }, mv > 0 ? "▲" : "▼", Math.abs(mv)) : null,
          h("span", { class: "sp row" },
            !isC ? [h("button", { class: "icon-btn", style: { width: "28px", height: "28px" }, title: "Mark helpful", "aria-label": "Mark helpful", onClick: (e) => mark(c.chunk_id, "helpful", e.currentTarget) }, icon("thumbup")), h("button", { class: "icon-btn", style: { width: "28px", height: "28px" }, title: "Mark irrelevant", "aria-label": "Mark irrelevant", onClick: (e) => mark(c.chunk_id, "irrelevant", e.currentTarget) }, icon("thumbdown"))] : null,
            h("button", { class: "btn sm", onClick: () => openCite(r, i + 1) }, icon("eye"), "View"))),
        h("p", { class: "tx", onClick: (e) => e.currentTarget.parentElement.classList.toggle("open") }, c.text),
        c.matched_entities.length ? h("div", { class: "row wrap" }, c.matched_entities.slice(0, 7).map((e) => h("span", { class: "chip " + (c.direct_entities.includes(e) ? "accent" : "") }, e))) : null,
        feats.length ? h("div", { class: "feat-bars" }, feats.map(([k, v]) => h("div", null, h("span", null, k), h("div", { class: "bar" }, h("i", { style: { width: Math.max(2, Math.min(100, Math.abs(v) * 100)) + "%", background: k === "prior" ? "var(--ochre)" : "" } }))))) : null);
      wrap.append(card);
    });
    return wrap;
  }
  function mark(id, v, btn) {
    const row = btn.parentElement; S.marks[id] = S.marks[id] === v ? undefined : v; if (S.marks[id] === undefined) delete S.marks[id];
    $$("button", row).forEach((b, i) => { b.style.color = ""; }); if (S.marks[id]) btn.style.color = v === "helpful" ? "var(--teal)" : "var(--bad)";
    toast(S.marks[id] ? (v === "helpful" ? "Marked helpful — improves future ranking" : "Marked irrelevant — will rank lower next time") : "Mark removed");
  }

  // ---- side panel
  function showSide(r) {
    clear(side);
    const legend = h("div", { class: "legend" }, [["var(--ink)", "Question"], ["var(--steel)", "Vector"], ["var(--ochre)", "Full-text"], ["var(--teal)", "Graph"], ["var(--accent)", "Query entity"]].map(([c, t]) => h("span", null, h("i", { style: { background: c } }), t)));
    const canvasBox = h("div", { class: "card", style: { overflow: "hidden" } }, h("div", { class: "map" }, h("canvas"), h("div", { class: "tip", hidden: true }), h("div", { class: "cap" }, "Drag nodes · scroll to zoom · double-click to fit")), legend);
    const tabsEl = h("div", { class: "tabs", role: "tablist" }); const panes = {};
    const mk = (id, label, node) => { panes[id] = node; tabsEl.append(h("button", { role: "tab", onClick: () => sel(id), dataset: { t: id } }, label)); };
    const sel = (id) => { $$("button", tabsEl).forEach((b) => b.classList.toggle("on", b.dataset.t === id)); Object.entries(panes).forEach(([k, n]) => (n.hidden = k !== id)); if (id === "map") graph && graph.resize(); };
    const paths = h("div", null, (r.paths || []).length ? (r.paths || []).map((p, pi) => pathCard(p, pi, () => { sel("map"); const ids = p.nodes.map((n) => "e:" + n.toLowerCase()); graph.highlightPath(ids); })) : h("p", { class: "muted pad", style: { padding: "10px" } }, "No multi-hop path was needed for this question — the answer comes straight from the retrieved passages."));
    mk("map", "Evidence map", canvasBox); mk("paths", `Reasoning paths${(r.paths || []).length ? " · " + r.paths.length : ""}`, paths); mk("trace", "Trace", traceBox(r));
    side.append(tabsEl, ...Object.values(panes)); sel("map");
    graph && graph.destroy(); const cv = $("canvas", canvasBox); graph = new Graph(cv, { tip: $(".tip", canvasBox), particles: true, onSelect: (n) => n && n.kind === "chunk" && openCite(r, n.rank) });
    requestAnimationFrame(() => { graph.resize(); graph.setData(r.graph); });
  }
  function pathCard(p, i, onShow) {
    const box = h("div", { class: "path", style: { cursor: "pointer" }, onClick: onShow, title: "Click to highlight on the map" }); let k = 0;
    p.nodes.forEach((n, ni) => { box.append(h("span", { class: "node", style: { "--i": k++ } }, n)); const e = p.edges[ni]; if (e) box.append(h("span", { class: "arr", style: { "--i": k++ } }, "→", h("b", { title: e.doc_title + (e.page ? ", p. " + e.page : ""), onClick: (ev) => { ev.stopPropagation(); openSource(e.chunk_id); } }, e.relation.toLowerCase().replace(/_/g, " ") + (e.page ? " · p." + e.page : "")), "→")); });
    return box;
  }
  function traceBox(r) {
    const t = r.trace || {}, total = Math.max(1, ...((r.spans || []).map((s) => s.start_ms + s.ms)));
    return h("div", { class: "card pad", style: { display: "grid", gap: "14px" } },
      h("div", null, h("b", null, "Retrieval plan"), h("div", { class: "row wrap", style: { marginTop: "8px" } }, Object.entries(t.weights || {}).map(([k, v]) => h("span", { class: "chip " + ({ vector: "steel", fulltext: "ochre", graph: "teal" }[k] || "") }, k + " ×" + v)), t.hops ? h("span", { class: "chip" }, t.hops + " hops") : null, t.reranked ? h("span", { class: "chip accent" }, "reranked") : null)),
      t.candidates ? h("div", { class: "faint", style: { fontSize: "13px" } }, "Candidates: " + Object.entries(t.candidates).map(([k, v]) => `${v} ${k}`).join(" · ") + ` → ${t.fused} fused → ${r.chunks.length} shown`) : null,
      h("div", { class: "wf" }, (r.spans || []).map((s) => h("div", { class: "r" }, h("span", null, s.name), h("div", { class: "tr" }, h("i", { style: { left: (s.start_ms / total) * 100 + "%", width: Math.max(1.5, (s.ms / total) * 100) + "%" } })), h("span", { class: "mono" }, fmt.ms(s.ms))))));
  }

  // ---- init
  const docs = await get("/api/documents").catch(() => ({ documents: [] }));
  const ready = docs.documents.filter((d) => d.status === "ready" && d.is_current && !d.archived);
  ready.filter((d) => !d.builtin).slice(0, 12).forEach((d) => scope.append(h("option", { value: "doc:" + d.id }, "📄 " + d.title)));
  scope.append(h("option", { value: "project" }, "Project files only"), h("option", { value: "uploads" }, "My uploads only"));
  scope.onchange = () => (S.scope = scope.value);
  if (!ready.length) out.append(empty("lib", docs.documents.length ? "Your library is still indexing" : "Your library is empty", "Add a PDF in the Library, then come back to ask questions about it.", h("a", { class: "btn primary", href: "#library" }, "Open Library")));
  get("/api/examples").then((ex) => ex.slice(0, 6).forEach((q) => examples.append(h("button", { class: "ex", onClick: () => { if (q.scope && [...scope.options].some((o) => o.value === q.scope)) { scope.value = S.scope = q.scope; } ask(q.question); } }, h("small", null, q.type), q.question)))).catch(() => {});
  const prefill = () => { const p = sessionStorage.getItem("ask-prefill"); if (p) { sessionStorage.removeItem("ask-prefill"); ask(p); } };
  window.addEventListener("ask-prefill", prefill);
  return { el, mounted() { setMode(S.mode); const last = load("last", ""); if (last) ta.placeholder = last; ta.focus(); prefill(); }, destroy() { S.ctrl?.abort(); graph && graph.destroy(); window.removeEventListener("ask-prefill", prefill); } };
}
