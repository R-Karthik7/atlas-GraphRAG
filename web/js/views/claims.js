import { h, icon, clear, toast, empty, fmt, debounce } from "../ui.js";
import { get, post } from "../api.js";
import { openSource } from "./source.js";

export async function render({ user, refreshBadges }) {
  let tab = "conflicts", status = "open";
  const canEdit = user.role !== "viewer";
  const body = h("div"), tabs = h("div", { class: "tabs" }), sub = h("div", { class: "tabs" }), search = h("input", { type: "search", placeholder: "Filter claims…", style: { width: "260px" } });
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Claims & contradictions"), h("h1", null, "Where your documents disagree"), h("p", null, "Checkable statements are extracted from every passage and compared across documents: conflicting numbers, creators, languages, and affirm-vs-deny pairs.")), h("span", { class: "grow" }), tabs));
  [["conflicts", "Conflicts"], ["claims", "All claims"]].forEach(([id, l]) => tabs.append(h("button", { class: id === tab ? "on" : "", onClick: (e) => { tab = id; [...tabs.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paint(); } }, l)));
  el.append(body);
  [["open", "Open"], ["confirmed", "Confirmed"], ["dismissed", "Dismissed"]].forEach(([id, l]) => sub.append(h("button", { class: id === status ? "on" : "", onClick: (e) => { status = id; [...sub.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paint(); } }, l)));
  const KIND = { numeric: ["Number differs", "ochre"], exclusive: ["Different value", "steel"], negation: ["Affirm vs deny", "bad"] };
  async function review(id, s, card) { try { await post("/api/contradictions/" + id, { status: s }); toast(s === "confirmed" ? "Marked as a real conflict" : s === "dismissed" ? "Dismissed — it won't come back" : "Reopened", "good"); card.style.transition = ".4s"; card.style.opacity = 0; card.style.transform = "scale(.97)"; setTimeout(paint, 350); refreshBadges(); } catch (e) { toast(e.message, "bad"); } }
  function side(c, label) {
    return h("div", { class: "side-c" }, h("div", { class: "row" }, h("b", { style: { fontSize: "13px" } }, c.doc_title), c.page ? h("span", { class: "chip" }, "p. " + c.page) : null), h("q", null, c.sentence), h("button", { class: "btn sm", style: { justifySelf: "start" }, onClick: () => openSource(c.chunk_id) }, icon("eye"), "View source"));
  }
  async function paint() {
    clear(body); body.append(skeleton());
    try {
      if (tab === "conflicts") {
        const rows = await get("/api/contradictions?status=" + status); clear(body).append(h("div", { class: "row", style: { marginBottom: "14px" } }, sub, h("span", { class: "faint" }, rows.length + " " + status)));
        if (!rows.length) return body.append(empty("claim", status === "open" ? "No open conflicts" : "Nothing here", status === "open" ? "Nothing in your documents currently contradicts itself — or add more documents to compare." : "Reviewed items show up here."));
        body.append(h("div", { class: "grid stagger" }, rows.map((c, i) => { const [k, tone] = KIND[c.kind] || [c.kind, ""]; const card = h("article", { class: "card conflict " + c.status, style: { "--i": Math.min(i, 10) } },
          h("div", { class: "row wrap" }, h("span", { class: "chip " + tone }, k), h("b", null, c.a.subject), h("span", { class: "chip" }, c.predicate.replace("quantity:", "").replace(/_/g, " ")), h("span", { class: "chip" }, "confidence " + Math.round(c.score * 100) + "%"), c.verdict_by === "llm" ? h("span", { class: "chip teal" }, "LLM-verified") : null),
          h("p", { class: "muted" }, c.reason), h("div", { class: "vs" }, side(c.a), h("div", { class: "bolt" }, icon("bolt")), side(c.b)),
          canEdit ? h("div", { class: "row" }, c.status !== "confirmed" ? h("button", { class: "btn sm dark", onClick: () => review(c.id, "confirmed", card) }, "Confirm conflict") : null, c.status !== "dismissed" ? h("button", { class: "btn sm", onClick: () => review(c.id, "dismissed", card) }, "Not a conflict") : null, c.status !== "open" ? h("button", { class: "btn sm ghost", onClick: () => review(c.id, "open", card) }, "Reopen") : null) : null); return card; })));
      } else {
        const rows = await get("/api/claims" + (search.value ? "?q=" + encodeURIComponent(search.value) : "")); clear(body).append(h("div", { class: "row", style: { marginBottom: "14px" } }, search, h("span", { class: "faint" }, rows.length + " claims")));
        if (!rows.length) return body.append(empty("claim", "No claims yet", "Claims are extracted from documents with the knowledge graph enabled."));
        body.append(h("div", { class: "card" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["Subject", "Statement", "Source", ""].map((t) => h("th", null, t)))), h("tbody", null, rows.map((c) => h("tr", null, h("td", null, h("b", null, c.subject), h("div", { class: "faint", style: { fontSize: "12px" } }, c.predicate.replace("quantity:", "").replace(/_/g, " "), c.polarity < 0 ? " (negated)" : "")), h("td", { style: { maxWidth: "520px" } }, c.sentence), h("td", null, c.doc_title, c.page ? " · p. " + c.page : ""), h("td", null, h("button", { class: "btn sm ghost", onClick: () => openSource(c.chunk_id) }, "View"))))))));
        search.oninput = debounce(paint, 300);
      }
    } catch (e) { clear(body).append(h("p", { class: "err" }, e.message)); }
  }
  function skeleton() { return h("div", { class: "grid" }, [1, 2].map(() => h("div", { class: "skeleton", style: { height: "150px" } }))); }
  await paint();
  return { el };
}
