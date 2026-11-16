import { h, icon, clear, toast, empty, countUp } from "../ui.js";
import { get, post } from "../api.js";
import { gauge } from "../charts.js";

export async function render({ user }) {
  const body = h("div"), el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Entity resolution"), h("h1", null, "Graph quality"), h("p", null, "Mentions like “RAG”, “Retrieval-Augmented Generation” and “retrieval augmented generation (RAG)” are merged into one canonical entity. Review near-duplicates the system wasn't sure about.")), h("span", { class: "grow" })), body);
  const canEdit = user.role !== "viewer";
  async function paint() {
    const d = await get("/api/resolution"), q = d.quality; clear(body);
    const col = q.score >= 70 ? "var(--teal)" : q.score >= 50 ? "var(--ochre)" : "var(--accent)";
    const num = h("div", { style: { font: "600 46px var(--serif)" } }, "0");
    body.append(h("div", { class: "card pad score-ring-wrap" }, h("div", { style: { position: "relative", width: "190px", height: "190px" } }, gauge(q.score, { size: 190, stroke: 15, color: col }), h("div", { style: { position: "absolute", inset: 0, display: "grid", placeItems: "center", textAlign: "center" } }, h("div", null, num, h("div", { class: "chip", style: { marginTop: "4px" } }, "Grade " + q.grade)))),
      h("div", null, Object.entries(q.parts).map(([k, v]) => h("div", { class: "part" }, h("span", { style: { textTransform: "capitalize" } }, k), h("div", { class: "bar" }, h("i", { style: { width: v + "%", background: v >= 70 ? "var(--teal)" : v >= 45 ? "var(--ochre)" : "var(--accent)" } })), h("span", null, v))), h("p", { class: "faint", style: { fontSize: "12.5px", marginTop: "8px" } }, "Score blends chunk coverage (30), connectivity (25), relation specificity (15), unresolved duplicates (15), typing (10) and density (5)."))));
    countUp(num, q.score);
    body.append(h("div", { class: "grid g4 stagger", style: { margin: "16px 0" } }, [["Entities", q.entities], ["Aliases merged", q.aliases_merged], ["Orphans", q.orphans], ["Components", q.components], ["Avg degree", q.avg_degree], ["Single-mention", q.single_mention], ["Passages with entities", `${q.chunks_with_entities}/${q.chunks}`], ["Review queue", q.duplicate_candidates]].map(([l, v], i) => h("div", { class: "card kpi", style: { "--i": i } }, h("div", { class: "n", style: { fontSize: "30px" } }, String(v)), h("div", { class: "l" }, l)))));
    const decide = async (p, action, row) => { try { await post("/api/resolution/decide", { a: p.a.id, b: p.b.id, action }); toast(action === "merge" ? "Merged — graph rebuilt" : "Kept separate — remembered", "good"); row.style.opacity = 0.3; await paint(); } catch (e) { toast(e.message, "bad"); } };
    body.append(h("div", { class: "grid", style: { gridTemplateColumns: "1.2fr 1fr" } },
      h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Possible duplicates"), h("span", { class: "chip" }, d.candidates.length)),
        d.candidates.length ? d.candidates.map((p) => { const row = h("div", { class: "pair" }, h("div", { class: "ent" }, h("b", null, p.a.name), h("span", { class: "faint" }, (p.a.etype || "Concept") + " · " + p.a.mention_count + " mentions")), h("span", { class: "chip ochre", title: p.reason }, Math.round(p.score * 100) + "%"), h("div", { class: "ent" }, h("b", null, p.b.name), h("span", { class: "faint" }, (p.b.etype || "Concept") + " · " + p.b.mention_count + " mentions")),
          canEdit ? h("div", { class: "row" }, h("button", { class: "btn sm dark", onClick: () => decide(p, "merge", row) }, icon("merge"), "Merge"), h("button", { class: "btn sm", onClick: () => decide(p, "separate", row) }, "Different")) : null); return row; }) : empty("check", "Nothing to review", "No look-alike entities were left unresolved.")),
      h("div", { class: "card pad" }, h("div", { class: "card-h" }, h("h2", null, "Merge log"), h("span", { class: "chip" }, d.merges.length)),
        d.merges.length ? h("div", { style: { maxHeight: "480px", overflow: "auto" } }, d.merges.map((m) => h("div", { style: { padding: "8px 0", borderBottom: "1px dashed var(--line)", fontSize: "13.5px" } }, h("span", { class: "mono" }, m.merged_name), " → ", h("b", null, m.canonical), h("div", { class: "faint", style: { fontSize: "12px" } }, m.reason + " · " + Math.round(m.score * 100) + "%")))) : empty("merge", "No merges yet", "Aliases found in your documents will be listed here."))));
  }
  try { await paint(); } catch (e) { body.append(h("p", { class: "err" }, e.message)); }
  return { el };
}
