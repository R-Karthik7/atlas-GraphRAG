import { h, icon, clear, toast, fmt, empty, chipForSource } from "../ui.js";
import { get, post } from "../api.js";
import { openSource } from "./source.js";

const NAMES = { vector: "Vector", fulltext: "Full-text", graph: "Graph", hybrid: "Hybrid", auto: "Auto-routed" };
export async function render() {
  const q = h("input", { type: "text", maxlength: 1000, placeholder: "Ask a question that needs two passages to answer…", "aria-label": "Question to compare" });
  const btn = h("button", { class: "btn primary", type: "submit" }, "Run all five");
  const out = h("div");
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Retrieval lab"), h("h1", null, "Compare strategies"), h("p", null, "The same question through vector, full-text, graph, hybrid and the auto-router — side by side, with the passages each one found.")), h("span", { class: "grow" })),
    h("form", { class: "card composer", style: { display: "flex", gap: "10px", alignItems: "center" }, onSubmit: (e) => { e.preventDefault(); run(); } }, q, btn), h("div", { class: "examples" }), out);
  get("/api/examples").then((ex) => ex.slice(0, 4).forEach((e) => $ex.append(h("button", { class: "ex", onClick: () => { q.value = e.question; run(); } }, e.question)))).catch(() => {});
  const $ex = el.querySelector(".examples");
  async function run() {
    const question = q.value.trim(); if (!question) return; btn.disabled = true; clear(out).append(h("div", { class: "cmp" }, Object.keys(NAMES).map(() => h("div", { class: "card col" }, [1, 2, 3, 4].map(() => h("div", { class: "skeleton", style: { height: "40px" } }))))));
    try {
      const r = await post("/api/compare", { question }); clear(out);
      const keys = Object.keys(NAMES), ids = new Map();
      keys.forEach((k) => (r[k].chunks || []).forEach((c) => { if (!ids.has(c.chunk_id)) ids.set(c.chunk_id, c); }));
      out.append(h("div", { class: "cmp stagger" }, keys.map((k, i) => { const x = r[k]; return h("article", { class: "card col", style: { "--i": i } },
        h("h3", null, NAMES[k], x.route && k === "auto" ? h("span", { class: "chip accent" }, x.route.route) : null), !x.ok ? h("p", { class: "err" }, x.error) : [
          h("div", { class: "row wrap" }, h("span", { class: "chip" }, fmt.ms(x.latency_ms)), x.grounding?.score != null ? h("span", { class: "chip teal" }, "grounded " + Math.round(x.grounding.score * 100) + "%") : null, x.refused ? h("span", { class: "chip ochre" }, "refused") : null),
          h("p", { style: { fontSize: "14px" } }, x.answer.replace(/\*\*/g, "")), h("div", { class: "hr", style: { margin: "4px 0" } }),
          x.chunks.map((c) => h("div", { style: { fontSize: "13px", padding: "6px 0", borderBottom: "1px dashed var(--line)", cursor: "pointer" }, onClick: () => openSource(c.chunk_id) }, h("b", null, c.rank + ". " + c.title), " ", h("span", { class: "faint" }, c.where)))]); })));
      const rows = [...ids.values()];
      out.append(h("div", { class: "card pad", style: { marginTop: "16px" } }, h("h2", null, "Which passages each strategy retrieved"), h("p", { class: "muted", style: { margin: "6px 0 12px" } }, "Passages found by several strategies are usually the strongest evidence. Gaps show what only one method can reach."),
        h("table", { class: "t matrix" }, h("thead", null, h("tr", null, h("th", null, "Passage"), keys.map((k) => h("th", null, NAMES[k])))), h("tbody", null, rows.map((c) => h("tr", null, h("td", null, h("b", null, c.title), " ", h("span", { class: "faint" }, c.where), h("div", { class: "faint", style: { fontSize: "12.5px", maxWidth: "520px" } }, c.text.slice(0, 120) + "…")), keys.map((k, i) => { const hit = (r[k].chunks || []).find((x) => x.chunk_id === c.chunk_id); return h("td", null, hit ? h("span", { class: "dot", style: { animationDelay: i * 60 + "ms" }, title: "rank " + hit.rank }) : h("span", { class: "dot none" })); })))))));
    } catch (e) { clear(out).append(h("p", { class: "err" }, e.message)); toast(e.message, "bad"); }
    btn.disabled = false;
  }
  return { el };
}
