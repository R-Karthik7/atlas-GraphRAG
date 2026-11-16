import { h, icon, drawer, skeleton, fmt, toast, chipForSource } from "../ui.js";
import { get } from "../api.js";

function highlighted(text, terms) {
  const words = [...new Set((terms || []).map((t) => t.toLowerCase()).filter((t) => t.length > 3))];
  if (!words.length) return text;
  const re = new RegExp("(" + words.map((w) => w.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|") + ")", "ig");
  return text.split(re).map((p, i) => (i % 2 ? h("mark", { class: "hl" }, p) : p));
}
export async function openSource(chunkId, { terms = [] } = {}) {
  const body = h("div", null, skeleton(5));
  const d = drawer("Source passage", body);
  try {
    const p = await get("/api/passages/" + encodeURIComponent(chunkId));
    const pdf = p.kind === "pdf", page = p.page_start;
    body.replaceChildren(
      h("div", { class: "grid" },
        h("div", { class: "row wrap" }, h("span", { class: "chip accent" }, p.kind.toUpperCase()), h("b", null, p.title), p.where ? h("span", { class: "chip" }, p.where) : null, p.section && p.page_start ? h("span", { class: "chip" }, "§ " + p.section) : null,
          h("span", { class: "sp" }), pdf ? h("a", { class: "btn sm", href: `/api/documents/${p.doc_id}/file#page=${page}`, target: "_blank", rel: "noopener" }, icon("file"), "Open PDF at page " + page) : null),
        p.flags ? h("div", { class: "chip bad" }, "Contains instruction-like text — it is sanitised before reaching the model") : null,
        p.before ? h("p", { class: "ctx" }, "… " + p.before) : null,
        h("div", { class: "card pad passage-text" }, highlighted(p.text, terms)),
        p.after ? h("p", { class: "ctx" }, p.after + " …") : null,
        p.entities.length ? h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Entities in this passage"), h("div", { class: "row wrap" }, p.entities.map((e) => h("span", { class: "chip teal" }, e)))) : null,
        pdf ? h("div", null, h("h3", { style: { margin: "6px 0 10px" } }, "Page preview"), h("iframe", { class: "pdfframe", title: "PDF page " + page, src: `/api/documents/${p.doc_id}/file#page=${page}&view=FitH` })) : null));
  } catch (e) { body.replaceChildren(h("p", { class: "err" }, e.message)); }
  return d;
}
