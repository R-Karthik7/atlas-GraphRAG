import { h, $, icon, clear, toast, debounce, empty, drawer, skeleton, toneFor } from "../ui.js";
import { get } from "../api.js";
import { Graph } from "../graph.js";
import { openSource } from "./source.js";

export async function render() {
  let g = null, data = null, comms = [], pathFrom = null;
  const search = h("input", { type: "search", placeholder: "Find an entity…", "aria-label": "Find an entity" });
  const color = h("select", { "aria-label": "Color by" }, h("option", { value: "type" }, "Color by type"), h("option", { value: "community" }, "Color by theme"));
  const limit = h("select", { "aria-label": "Nodes" }, [60, 90, 150, 250].map((n) => h("option", { value: n, selected: n === 90 }, n + " nodes")));
  const docSel = h("select", { "aria-label": "Document" }, h("option", { value: "" }, "All documents"));
  const info = h("span", { class: "chip" }, "…");
  const tip = h("div", { class: "tip", hidden: true }), cv = h("canvas");
  const legend = h("div", { class: "row wrap", style: { position: "absolute", left: "14px", bottom: "12px", zIndex: 3, fontSize: "12px" } });
  const panel = h("aside", { class: "card pad", style: { alignSelf: "start", position: "sticky", top: "70px", maxHeight: "calc(100vh - 100px)", overflow: "auto" } }, empty("graph", "Pick an entity", "Click a node to see aliases, relationships, claims and the passages it appears in. Shift-click a second node to trace the shortest path."));
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Knowledge graph"), h("h1", null, "Explore the graph"), h("p", null, "Resolved entities connected by extracted relationships. Drag, zoom, and click to inspect.")), h("span", { class: "grow" }), info),
    h("div", { class: "explore" }, h("div", { class: "card bigmap" }, cv, tip, h("div", { class: "ctrl" }, search, color, limit, docSel), legend), panel));

  async function load(focusId) {
    const p = new URLSearchParams({ limit: limit.value }); if (search.value.trim()) p.set("q", search.value.trim()); if (docSel.value) p.set("doc", docSel.value);
    data = await get("/api/graph?" + p); info.textContent = `${data.nodes.length} of ${data.total_entities} entities · ${data.links.length} links`;
    if (!data.nodes.length) { g.setData({ nodes: [], links: [] }); return; }
    g.setData({ nodes: data.nodes.map((n) => ({ ...n, id: String(n.id), kind: "entity" })), links: data.links.map((l) => ({ ...l, source: String(l.source), target: String(l.target) })) });
    legendPaint(); if (focusId && g.by.get(String(focusId))) { g.select(String(focusId)); detail(focusId); }
  }
  function legendPaint() {
    clear(legend); const by = color.value; g.o.colorBy = by;
    if (by === "type") data.types.forEach((t) => legend.append(h("span", { class: "chip" }, h("i", { style: { background: g.col.pal[g.typeIndex(t) % 10], borderRadius: "50%" } }), t)));
    else legend.append(h("span", { class: "chip" }, "Colors show detected themes"));
    g.draw();
  }
  async function detail(id) {
    clear(panel).append(skeleton(5));
    try {
      const e = await get("/api/entities/" + id);
      clear(panel).append(h("div", { class: "grid" }, h("div", null, h("span", { class: "chip accent" }, e.type || "Concept"), h("h2", { style: { marginTop: "8px" } }, e.name), e.community ? h("p", { class: "muted", style: { marginTop: "4px" } }, "Theme: ", e.community.title) : null),
        h("div", { class: "row wrap" }, h("button", { class: "btn sm", onClick: () => { sessionStorage.setItem("ask-prefill", `What is ${e.name}?`); location.hash = "#ask"; window.dispatchEvent(new Event("ask-prefill")); } }, icon("ask"), "Ask about it"), h("button", { class: "btn sm", onClick: () => { pathFrom = String(id); toast("Now shift-click another node to trace a path"); } }, icon("link"), "Trace path from here")),
        e.aliases.length > 1 ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, "Also appears as"), h("div", { class: "row wrap" }, e.aliases.map((a) => h("span", { class: "chip" }, a.alias, a.count > 1 ? " ×" + a.count : "")))) : null,
        e.relations.length ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, `Relationships · ${e.relations.length}`), e.relations.slice(0, 14).map((r) => h("div", { class: "row", style: { padding: "5px 0", fontSize: "13.5px", flexWrap: "wrap" } }, r.direction === "in" ? h("a", { href: "#", onClick: (ev) => { ev.preventDefault(); focus(r.other_id); }, style: { fontWeight: 600 } }, r.other) : null, h("span", { class: "chip" }, r.relation.toLowerCase().replace(/_/g, " ")), r.direction === "out" ? h("a", { href: "#", onClick: (ev) => { ev.preventDefault(); focus(r.other_id); }, style: { fontWeight: 600 } }, r.other) : null, h("button", { class: "chip", style: { cursor: "pointer" }, onClick: () => openSource(r.chunk_id) }, r.page ? "p. " + r.page : "source")))) : null,
        e.claims.length ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, "Claims"), e.claims.slice(0, 5).map((c) => h("p", { class: "muted", style: { fontSize: "13px", padding: "4px 0" } }, "“" + c.sentence + "”"))) : null,
        e.passages.length ? h("div", null, h("h3", { style: { marginBottom: "6px" } }, "Mentioned in"), e.passages.slice(0, 4).map((p) => h("div", { class: "card pad", style: { marginBottom: "8px", cursor: "pointer", padding: "10px 12px" }, onClick: () => openSource(p.chunk_id) }, h("b", { style: { fontSize: "13px" } }, p.title), " ", h("span", { class: "faint", style: { fontSize: "12px" } }, p.where), h("p", { class: "muted", style: { fontSize: "13px", marginTop: "4px" } }, p.text)))) : null));
    } catch (er) { clear(panel).append(h("p", { class: "err" }, er.message)); }
  }
  async function focus(id) { search.value = ""; if (!g.by.get(String(id))) await load(id); else { g.select(String(id)); detail(id); } }
  async function tracePath(a, b) {
    try { const p = await get(`/api/graph/path?a=${a}&b=${b}`); if (!p.nodes.length) return toast("No connection found between those two entities", "bad"); g.highlightPath(p.nodes.map((n) => String(n.id))); toast(`Path found: ${p.nodes.map((n) => n.label).join(" → ")}`); } catch (e) { toast(e.message, "bad"); }
  }
  const mounted = async () => {
    g = new Graph(cv, { tip, colorBy: "type", labels: "auto", onSelect: (n) => { if (!n) { clear(panel).append(empty("graph", "Pick an entity", "Click a node to inspect it.")); g.highlightPath(null); return; } if (pathFrom && pathFrom !== n.id) { tracePath(pathFrom, n.id); pathFrom = null; return; } detail(n.id); } });
    cv.addEventListener("pointerdown", (e) => { if (e.shiftKey && g.sel && !pathFrom) pathFrom = g.sel.id; });
    const docs = await get("/api/documents").catch(() => ({ documents: [] })); docs.documents.filter((d) => d.status === "ready" && d.is_current && !d.archived).forEach((d) => docSel.append(h("option", { value: d.id }, d.title)));
    const f = sessionStorage.getItem("graph-focus"); sessionStorage.removeItem("graph-focus");
    try { await load(f); } catch (e) { toast(e.message, "bad"); }
    search.addEventListener("input", debounce(() => load().catch((e) => toast(e.message, "bad")), 300)); [limit, docSel].forEach((s) => s.addEventListener("change", () => load().catch((e) => toast(e.message, "bad")))); color.addEventListener("change", legendPaint);
  };
  const onFocus = () => { const f = sessionStorage.getItem("graph-focus"); if (f) { sessionStorage.removeItem("graph-focus"); focus(f); } };
  window.addEventListener("graph-focus", onFocus);
  return { el, mounted, destroy() { g && g.destroy(); window.removeEventListener("graph-focus", onFocus); } };
}
