import { h, icon, clear, countUp, fmt, empty, toneFor, svg } from "../ui.js";
import { get } from "../api.js";
import { gauge } from "../charts.js";

export async function render({ user, go }) {
  const d = await get("/api/overview"), c = d.counts, q = d.quality;
  const el = h("section", { class: "view" });
  const kpis = [["documents", "Documents", "lib"], ["pages", "Pages", "file"], ["passages", "Passages", "cube"], ["entities", "Entities", "graph"], ["relations", "Relationships", "link"], ["themes", "Themes", "theme"], ["claims", "Claims", "claim"], ["conflicts", "Open conflicts", "bolt"]];
  el.append(h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Overview"), h("h1", null, `Good ${new Date().getHours() < 12 ? "morning" : new Date().getHours() < 18 ? "afternoon" : "evening"}, ${user.name.split(" ")[0]}.`),
    h("p", null, c.documents ? `Your library holds ${fmt.num(c.documents)} documents mapped into ${fmt.num(c.entities)} entities. ${c.conflicts ? `${c.conflicts} statements conflict and need a look.` : "No conflicting statements are open."}` : "Add your first document to build a knowledge graph.")),
    h("span", { class: "grow" }), h("a", { class: "btn primary", href: "#ask" }, icon("ask"), "Ask a question"), h("a", { class: "btn", href: "#library" }, icon("upload"), "Add documents")));
  if (d.processing.length) el.append(h("div", { class: "card pad", style: { marginBottom: "16px", display: "grid", gap: "10px" } }, h("b", null, `Indexing ${d.processing.length} document${d.processing.length > 1 ? "s" : ""}…`), d.processing.slice(0, 4).map((p) => h("div", null, h("div", { class: "row" }, h("span", null, p.title), h("span", { class: "faint sp" }, p.stage || p.status)), h("div", { class: "bar" }, h("i", { style: { width: (p.progress_total ? (p.progress_done / p.progress_total) * 100 : 12) + "%" } }))))));
  const grid = h("div", { class: "grid g4 stagger" });
  kpis.forEach(([k, label, ic], i) => { const n = h("div", { class: "n" }, "0"); grid.append(h("div", { class: "card kpi", style: { "--i": i } }, n, h("div", { class: "l" }, label), c[k] === 0 && k === "conflicts" ? h("span", { class: "tag chip teal" }, "clear") : null)); countUp(n, c[k] || 0); });
  el.append(grid);
  const types = d.types, tot = types.reduce((s, t) => s + t.n, 0) || 1;
  const ring = h("div", { style: { position: "relative", width: "150px", height: "150px", flex: "none" } }, gauge(q.score, { size: 150, stroke: 13, color: q.score >= 70 ? "var(--teal)" : q.score >= 50 ? "var(--ochre)" : "var(--accent)" }), h("div", { style: { position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" } }, h("div", null, h("div", { style: { font: "600 34px var(--serif)" } }, q.score), h("div", { class: "faint", style: { fontSize: "11px", letterSpacing: ".12em" } }, "GRAPH QUALITY"))));
  el.append(h("div", { class: "grid", style: { gridTemplateColumns: "1.1fr 1fr", marginTop: "16px" } },
    h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Knowledge graph health"), h("a", { class: "btn sm", href: "#quality" }, "Review")),
      h("div", { class: "row", style: { gap: "26px", alignItems: "center" } }, ring, h("div", { class: "grow" }, Object.entries(q.parts).map(([k, v]) => h("div", { class: "part" }, h("span", { style: { textTransform: "capitalize" } }, k), h("div", { class: "bar" }, h("i", { style: { width: v + "%", background: v >= 70 ? "var(--teal)" : v >= 45 ? "var(--ochre)" : "var(--accent)" } })), h("span", null, v)))))),
    h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Entity mix")), types.length ? h("div", { style: { display: "grid", gap: "10px" } },
      h("div", { style: { display: "flex", height: "14px", borderRadius: "99px", overflow: "hidden", background: "var(--surface-3)" } }, types.map((t, i) => h("i", { title: `${t.etype}: ${t.n}`, style: { width: (t.n / tot) * 100 + "%", background: toneFor(i), display: "block" } }))),
      h("div", { class: "row wrap" }, types.map((t, i) => h("span", { class: "chip" }, h("i", { style: { background: toneFor(i), borderRadius: "50%" } }), `${t.etype} · ${t.n}`)))) : empty("graph", "No entities yet", "They appear once documents are indexed."))));
  el.append(h("div", { class: "grid", style: { gridTemplateColumns: "1fr 1fr", marginTop: "16px" } },
    h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Most connected entities")), d.top_entities.length ? h("div", { class: "stagger" }, d.top_entities.map((e, i) => h("div", { class: "row", style: { padding: "8px 0", borderBottom: "1px dashed var(--line)", "--i": i } }, h("b", { style: { width: "22px", color: "var(--ink-3)" } }, i + 1), h("a", { href: "#graph", style: { textDecoration: "none", fontWeight: 600 }, onClick: () => sessionStorage.setItem("graph-focus", e.id) }, e.name), h("span", { class: "chip" }, e.etype || "Concept"), h("span", { class: "sp mono faint" }, e.degree + " links")))) : h("p", { class: "muted" }, "Nothing yet.")),
    h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Recent activity"), h("span", { class: "chip" }, d.queries_24h + " questions today")), d.activity.length ? h("div", { class: "timeline" }, d.activity.map((a) => h("div", null, h("time", null, fmt.ago(a.ts)), h("span", null, h("b", null, a.title), " — ", a.message)))) : h("p", { class: "muted" }, "No activity yet."))));
  const t = setInterval(async () => { if (!el.isConnected) return clearInterval(t); }, 8000);
  return { el, destroy() { clearInterval(t); } };
}
