import { h, icon, clear, empty, toneFor, drawer, fmt } from "../ui.js";
import { get } from "../api.js";

export async function render() {
  const all = await get("/api/communities"), coarse = all.filter((c) => c.level === 1), fine = all.filter((c) => c.level === 0);
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Community intelligence"), h("h1", null, "Themes across your library"), h("p", null, "Louvain community detection groups tightly connected entities. Each theme gets a report, which powers “global” questions about the whole collection.")), h("span", { class: "grow" }), h("button", { class: "btn primary", onClick: () => { sessionStorage.setItem("ask-prefill", "What are the main themes across the documents?"); location.hash = "#ask"; window.dispatchEvent(new Event("ask-prefill")); } }, icon("ask"), "Ask a global question")));
  if (!all.length) { el.append(empty("theme", "No themes yet", "Themes appear once at least a few connected entities exist. Add more documents.")); return { el }; }
  // packed overview: one bubble per theme, sized by entity count
  const bubbles = fine.slice(0, 40), max = Math.max(...bubbles.map((c) => c.size)), W = 900, H = 260;
  const ns = "http://www.w3.org/2000/svg", svgEl = document.createElementNS(ns, "svg"); svgEl.setAttribute("viewBox", `0 0 ${W} ${H}`); svgEl.style.width = "100%";
  const placed = [];
  bubbles.forEach((c, i) => { const r = 14 + (c.size / max) * 40; let x, y, tries = 0; do { x = r + Math.random() * (W - 2 * r); y = r + Math.random() * (H - 2 * r); tries++; } while (tries < 200 && placed.some((p) => Math.hypot(p.x - x, p.y - y) < p.r + r + 4)); placed.push({ x, y, r, c, i }); });
  placed.forEach(({ x, y, r, c, i }) => {
    const g = document.createElementNS(ns, "g"); g.style.cursor = "pointer"; g.style.animation = `pop .6s ${i * 40}ms both cubic-bezier(.2,.7,.2,1)`; g.style.transformOrigin = `${x}px ${y}px`;
    const ci = document.createElementNS(ns, "circle"); ci.setAttribute("cx", x); ci.setAttribute("cy", y); ci.setAttribute("r", r); ci.setAttribute("fill", toneFor(c.id)); ci.setAttribute("opacity", ".85");
    const t = document.createElementNS(ns, "title"); t.textContent = `${c.title} · ${c.size} entities`; g.append(ci, t);
    if (r > 26) { const tx = document.createElementNS(ns, "text"); tx.setAttribute("x", x); tx.setAttribute("y", y + 4); tx.setAttribute("text-anchor", "middle"); tx.setAttribute("fill", "#fff"); tx.setAttribute("font-size", "12"); tx.setAttribute("font-weight", "700"); tx.textContent = c.size; g.append(tx); }
    g.addEventListener("click", () => open(c)); svgEl.append(g);
  });
  el.append(h("div", { class: "card pad", style: { marginBottom: "18px" } }, h("div", { class: "card-h" }, h("h3", null, "Theme map"), h("span", { class: "chip" }, `${fine.length} themes${coarse.length ? " · " + coarse.length + " broad topics" : ""}`)), svgEl));
  if (coarse.length) { el.append(h("h2", { style: { margin: "6px 0 12px" } }, "Broad topics"), h("div", { class: "theme-grid stagger" }, coarse.map((c, i) => card(c, i)))); }
  el.append(h("h2", { style: { margin: "24px 0 12px" } }, "Themes"), h("div", { class: "theme-grid stagger" }, fine.map((c, i) => card(c, i))));
  function card(c, i) {
    return h("article", { class: "card theme", style: { "--tc": toneFor(c.id), "--i": Math.min(i, 14) }, tabindex: 0, role: "button", onClick: () => open(c), onKeydown: (e) => e.key === "Enter" && open(c) },
      h("div", { class: "row" }, h("span", { class: "chip" }, c.level ? "Broad topic" : "Theme"), h("span", { class: "chip" }, c.size + " entities"), h("span", { class: "sp faint", style: { fontSize: "12px" } }, "cohesion " + Math.round((c.cohesion || 0) * 100) + "%")),
      h("h3", null, c.title), h("p", { class: "muted", style: { fontSize: "14px", display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" } }, c.summary), h("div", { class: "row wrap" }, c.entities.slice(0, 5).map((e) => h("span", { class: "chip" }, e.name))));
  }
  function open(c) {
    drawer(c.title, h("div", { class: "grid" }, h("div", { class: "row wrap" }, h("span", { class: "chip accent" }, c.level ? "Broad topic" : "Theme"), h("span", { class: "chip" }, c.size + " entities"), h("span", { class: "chip" }, c.summarized_by === "llm" ? "LLM summary" : "Extractive summary")), h("p", { style: { fontFamily: "var(--serif)", fontSize: "17px", lineHeight: 1.7 } }, c.summary),
      h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Entities"), h("div", { class: "row wrap" }, c.entities.map((e) => h("a", { class: "chip teal", href: "#graph", style: { textDecoration: "none" }, onClick: () => sessionStorage.setItem("graph-focus", e.id) }, e.name)))),
      c.documents.length ? h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Documents"), c.documents.map((d) => h("div", { style: { padding: "4px 0" } }, d.title))) : null,
      h("button", { class: "btn primary", onClick: () => { sessionStorage.setItem("ask-prefill", `Summarize what the documents say about ${c.entities.slice(0, 2).map((e) => e.name).join(" and ")}.`); location.hash = "#ask"; window.dispatchEvent(new Event("ask-prefill")); } }, icon("ask"), "Ask about this theme")));
  }
  return { el };
}
