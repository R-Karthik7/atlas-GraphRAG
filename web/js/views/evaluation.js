import { h, icon, clear, toast, fmt, empty } from "../ui.js";
import { get, post } from "../api.js";
import { groupedBars, barChart } from "../charts.js";

const NAMES = { vector: "Vector", fulltext: "Full-text", graph: "Graph", hybrid: "Hybrid", auto: "Auto" };
export async function render({ user }) {
  let timer = null, runId = null;
  const body = h("div"), btn = h("button", { class: "btn primary", onClick: start }, icon("eval"), "Run evaluation");
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Evaluation"), h("h1", null, "Measure retrieval and answers"), h("p", null, "Runs your question set through every strategy and scores source hit-rate, MRR, keyword coverage, grounding, refusal behaviour, latency, tokens and cost. Mark answers in Ask to grow the question set.")), h("span", { class: "grow" }), user.role !== "viewer" ? btn : null), body);
  async function start() { btn.disabled = true; try { const r = await post("/api/evaluation/run", {}); runId = r.id; toast("Evaluation started", "good"); poll(); } catch (e) { toast(e.message, "bad"); btn.disabled = false; } }
  async function poll() {
    clearTimeout(timer);
    try {
      const l = await get("/api/evaluation"), run = l.runs.find((r) => r.id === runId) || l.runs[0];
      paint(l, run); if (run && run.status === "running") { btn.disabled = true; runId = run.id; timer = setTimeout(poll, 1200); } else btn.disabled = false;
      if (run && run.status === "done" && !run._detail) { const d = await get("/api/evaluation/" + run.id); run._detail = d; paintDetail(run, d); }
    } catch (e) { clear(body).append(h("p", { class: "err" }, e.message)); }
  }
  function paint(l, run) {
    clear(body);
    if (!run) return body.append(empty("eval", "No evaluation yet", `${l.questions} questions are ready. Run an evaluation to compare strategies.`, h("button", { class: "btn primary", onClick: start }, "Run evaluation")));
    if (run.status === "running") return body.append(h("div", { class: "card pad" }, h("b", null, `Running… ${run.done}/${run.total}`), h("div", { class: "bar", style: { marginTop: "10px", height: "10px" } }, h("i", { style: { width: (run.done / run.total) * 100 + "%" } }))));
    if (run.status === "failed") return body.append(h("div", { class: "card pad" }, h("b", { class: "err" }, "Run failed: " + run.error)));
    const s = run.summary || {}, keys = Object.keys(s);
    const cards = h("div", { class: "grid g5 stagger" }, keys.map((k, i) => h("div", { class: "card kpi", style: { "--i": i } }, h("div", { class: "l", style: { marginTop: 0, marginBottom: "8px" } }, NAMES[k]), h("div", { class: "n" }, fmt.pct(s[k].source_hit)), h("div", { class: "faint", style: { fontSize: "12.5px", marginTop: "6px" } }, `hit rate · MRR ${s[k].mrr != null ? s[k].mrr.toFixed(2) : "–"} · ${fmt.ms(s[k].latency_ms)}`))));
    const retr = h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "10px" } }, "Retrieval quality"), groupedBars([{ label: "Hit rate", k: "source_hit" }, { label: "Recall", k: "source_recall" }, { label: "MRR", k: "mrr" }, { label: "Evidence", k: "evidence_found" }].map((m) => ({ label: m.label, values: keys.map((k) => s[k][m.k] || 0) })), keys.map((k) => NAMES[k])), h("div", { class: "row wrap", style: { marginTop: "6px" } }, keys.map((k, i) => h("span", { class: "chip" }, h("i", { style: { background: `var(--c${i})`, borderRadius: "50%" } }), NAMES[k]))));
    const ans = h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "10px" } }, "Answer quality"), groupedBars([{ label: "Keywords", k: "keyword_coverage" }, { label: "Grounding", k: "grounding" }, { label: "Right behaviour", k: "correct_behaviour" }].map((m) => ({ label: m.label, values: keys.map((k) => s[k][m.k] || 0) })), keys.map((k) => NAMES[k])));
    const cost = h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "10px" } }, "Latency (mean ms) and cost"), barChart(keys.map((k, i) => ({ label: NAMES[k], value: s[k].latency_ms || 0, color: `var(--c${i})` })), { fmt: (v) => Math.round(v) }), h("p", { class: "faint", style: { fontSize: "12.5px", marginTop: "6px" } }, "Total cost for this run: " + fmt.cost(keys.reduce((t, k) => t + (s[k].total_cost || 0), 0)) + " · tokens/question: " + keys.map((k) => `${NAMES[k]} ${fmt.num(Math.round(s[k].tokens || 0))}`).join(", ")));
    body.append(cards, h("div", { class: "grid g2", style: { marginTop: "16px" } }, retr, ans), h("div", { style: { marginTop: "16px" } }, cost), h("div", { id: "detail", style: { marginTop: "16px" } }), h("p", { class: "faint", style: { marginTop: "14px", fontSize: "12.5px" } }, `Run #${run.id} · ${fmt.date(run.ts)} · ${run.config.questions} questions · ${run.config.model}`));
    if (run._detail) paintDetail(run, run._detail);
  }
  function paintDetail(run, d) {
    const host = document.getElementById("detail"); if (!host) return; const by = {}; d.results.forEach((r) => ((by[r.qid] ||= { q: r.question, type: r.qtype, m: {} }).m[r.strategy] = r.metrics));
    const keys = Object.keys(run.summary || {});
    host.replaceChildren(h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "10px" } }, "Per-question results"), h("div", { style: { overflowX: "auto" } }, h("table", { class: "t" }, h("thead", null, h("tr", null, h("th", null, "Question"), h("th", null, "Type"), keys.map((k) => h("th", null, NAMES[k])))), h("tbody", null, Object.entries(by).map(([id, r]) => h("tr", null, h("td", { style: { maxWidth: "420px" } }, id + " · " + r.q), h("td", null, h("span", { class: "chip" }, r.type)), keys.map((k) => { const m = r.m[k] || {}, ok = m.source_hit != null ? m.source_hit : m.correct_behaviour; return h("td", null, h("span", { class: "chip " + (m.error ? "bad" : ok >= 1 ? "teal" : ok > 0 ? "ochre" : "bad"), title: m.error || "" }, m.source_hit != null ? (m.source_hit ? "hit" : "miss") : (m.correct_behaviour ? "refused ✓" : "answered ✗"))); })))))))); 
  }
  poll();
  return { el, destroy() { clearTimeout(timer); } };
}
