import { h, $, icon, clear, toast, fmt, empty, confirmDialog, drawer, skeleton } from "../ui.js";
import { get, post, api } from "../api.js";

const FLOW = ["extracting", "chunking", "embedding", "graph", "resolving"];
const LABEL = { queued: "Queued", extracting: "Reading", chunking: "Splitting", embedding: "Embedding", graph: "Graph", resolving: "Linking", ready: "Ready", failed: "Failed" };

export async function render({ user }) {
  let timer = null, filter = "all";
  const input = h("input", { type: "file", multiple: true, accept: ".pdf,.docx,.txt,.md", hidden: true });
  const graphOpt = h("input", { type: "checkbox", checked: true }), vis = h("select", { style: { width: "auto" }, "aria-label": "Visibility" }, h("option", { value: "shared" }, "Shared with everyone"), h("option", { value: "private" }, "Only me"));
  const canUpload = user.role !== "viewer";
  const drop = h("div", { class: "drop", tabindex: 0, role: "button", "aria-label": "Add documents" }, h("div", { class: "ico" }, icon("upload")), h("div", { class: "grow" }, h("h2", null, "Drop PDFs, Word or text files"), h("p", { class: "muted", style: { marginTop: "6px" } }, "Every page is indexed so answers can cite “p. 12”. Up to 40 MB each.")),
    h("div", { class: "opts", onClick: (e) => e.stopPropagation() }, h("label", null, graphOpt, "Build knowledge graph"), h("label", null, "Visibility", vis)), input);
  const list = h("div"), tabs = h("div", { class: "tabs" });
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Library"), h("h1", null, "Your documents"), h("p", null, "Upload, version, archive and re-index. Each file runs through extraction, page-aware chunking, embeddings, entity resolution and claim detection.")), h("span", { class: "grow" }), tabs),
    canUpload ? drop : h("div", { class: "card pad muted" }, "Your role is viewer: you can search documents but not upload."), list);
  ["all", "mine", "project", "archived"].forEach((t) => tabs.append(h("button", { class: t === filter ? "on" : "", onClick: (e) => { filter = t; [...tabs.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paint(); } }, { all: "All", mine: "Uploads", project: "Project files", archived: "Archived" }[t])));

  async function upload(files) {
    if (!files.length) return; const fd = new FormData(); [...files].forEach((f) => fd.append("files", f)); fd.append("graph", graphOpt.checked ? "1" : "0"); fd.append("visibility", vis.value);
    try {
      const r = await api("/api/documents", { method: "POST", form: fd });
      if (r.added.length) toast(`${r.added.length} file${r.added.length > 1 ? "s" : ""} added — indexing started`, "good");
      r.rejected.forEach((x) => toast(`${x.name}: ${x.reason}`, "bad")); await refresh();
    } catch (e) { toast(e.message, "bad"); }
  }
  drop.addEventListener("click", () => input.click()); drop.addEventListener("keydown", (e) => (e.key === "Enter" || e.key === " ") && (e.preventDefault(), input.click()));
  input.addEventListener("change", () => { upload(input.files); input.value = ""; });
  ["dragenter", "dragover"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.add("over"); }));
  ["dragleave", "drop"].forEach((n) => drop.addEventListener(n, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
  drop.addEventListener("drop", (e) => upload(e.dataTransfer.files));

  let docs = [];
  function paint() {
    const rows = docs.filter((d) => (filter === "archived" ? d.archived : !d.archived && (filter === "all" || (filter === "mine" ? !d.builtin : d.builtin))) && (d.is_current || d.archived || filter === "all" && false));
    clear(list);
    if (!rows.length) return list.append(empty("lib", filter === "archived" ? "Nothing archived" : "No documents here yet", filter === "all" ? "Drop a PDF above to get started." : "Switch tabs to see other documents."));
    list.append(h("div", { class: "docs stagger" }, rows.map((d, i) => card(d, i))));
  }
  function card(d, i) {
    const active = FLOW.includes(d.status) || d.status === "queued", idx = FLOW.indexOf(d.status);
    const act = (label, ic, fn, cls = "") => h("button", { class: "btn sm ghost " + cls, title: label, "aria-label": label, onClick: fn }, icon(ic), label);
    return h("article", { class: "card doc" + (d.archived ? " arch" : ""), style: { "--i": Math.min(i, 12) } },
      h("div", { class: "row", style: { alignItems: "flex-start", gap: "14px" } }, h("div", { class: "kind " + d.kind }, d.kind.toUpperCase()),
        h("div", { class: "grow", style: { minWidth: 0 } }, h("h3", null, d.title), h("div", { class: "row wrap", style: { marginTop: "6px", gap: "6px" } },
          h("span", { class: "chip " + (d.status === "ready" ? "teal" : d.status === "failed" ? "bad" : "ochre") }, h("i"), LABEL[d.status] || d.status), d.version > 1 ? h("span", { class: "chip steel" }, "v" + d.version) : null,
          !d.is_current && !d.archived ? h("span", { class: "chip" }, "superseded") : null, d.visibility === "private" ? h("span", { class: "chip" }, icon("lock"), "Private") : null, d.builtin ? h("span", { class: "chip" }, "Project") : null, d.archived ? h("span", { class: "chip" }, "Archived") : null))),
      active ? h("div", null, h("div", { class: "steps" }, FLOW.map((_, k) => h("i", { class: k < idx ? "done" : k === idx ? "on" : "" }))), h("div", { class: "row", style: { marginTop: "6px", fontSize: "12.5px" } }, h("span", { class: "muted" }, d.stage || LABEL[d.status]), d.progress_total ? h("span", { class: "sp mono faint" }, `${d.progress_done}/${d.progress_total}`) : null)) : null,
      d.error ? h("p", { class: "err", role: "alert" }, d.error) : null, d.warning ? h("p", { class: "faint", style: { fontSize: "12.5px" } }, "⚠ " + d.warning) : null,
      d.status === "ready" || d.status === "resolving" ? h("div", { class: "facts" }, d.pages ? h("span", null, h("b", null, d.pages), " pages") : null, h("span", null, h("b", null, d.chunks), " passages"), h("span", null, h("b", null, d.entities), " entities"), h("span", null, h("b", null, d.claims), " claims"), h("span", { class: "faint" }, fmt.bytes(d.size)), d.owner_name ? h("span", { class: "faint" }, "by " + d.owner_name) : null) : null,
      h("div", { class: "row wrap", style: { gap: "2px" } }, act("Details", "eye", () => details(d)), d.can_edit && !active ? [d.status !== "queued" ? act("Re-index", "refresh", () => doAct(d, "reindex")) : null,
        d.archived ? act("Restore", "archive", () => doAct(d, "restore")) : act("Archive", "archive", () => doAct(d, "archive")),
        !d.builtin ? act(d.visibility === "private" ? "Share" : "Make private", d.visibility === "private" ? "eye" : "lock", () => doAct(d, "visibility", { visibility: d.visibility === "private" ? "shared" : "private" })) : null,
        !d.builtin ? act("Delete", "trash", async () => { if (await confirmDialog("Delete this document?", `“${d.title}” and everything extracted from it will be removed permanently.`, "Delete", true)) { try { await api(`/api/documents/${d.id}`, { method: "DELETE" }); toast("Deleted"); refresh(); } catch (e) { toast(e.message, "bad"); } } }, "danger") : null] : null));
  }
  async function doAct(d, a, body) { try { await post(`/api/documents/${d.id}/${a}`, body); toast({ archive: "Archived — excluded from search", restore: "Restored", reindex: "Re-indexing started", visibility: "Visibility updated" }[a], "good"); refresh(); } catch (e) { toast(e.message, "bad"); } }
  async function details(d) {
    const body = h("div", null, skeleton(4)), dr = drawer(d.title, body);
    try {
      const x = await get("/api/documents/" + d.id);
      body.replaceChildren(h("div", { class: "grid" },
        h("div", { class: "row wrap" }, h("a", { class: "btn sm", href: `/api/documents/${d.id}/file`, target: "_blank", rel: "noopener" }, icon("file"), x.kind === "pdf" ? "Open original" : "Download"), x.status === "ready" ? h("a", { class: "btn sm primary", href: "#ask", onClick: () => dr.close() }, icon("ask"), "Ask about this") : null),
        x.outline.length ? h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Outline"), h("div", { class: "card pad" }, x.outline.map((o) => h("div", { class: "row", style: { padding: "5px 0" } }, h("span", null, o.section), h("span", { class: "sp mono faint" }, o.page ? "p. " + o.page : o.passages + " passages"))))) : null,
        x.versions.length > 1 ? h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Versions"), x.versions.map((v) => h("div", { class: "row", style: { padding: "6px 0" } }, h("span", { class: "chip steel" }, "v" + v.version), h("span", { class: "muted" }, fmt.date(v.created_at)), v.is_current ? h("span", { class: "chip teal" }, "current") : null))) : null,
        h("div", null, h("h3", { style: { marginBottom: "8px" } }, "Lifecycle"), h("div", { class: "timeline" }, x.events.map((e) => h("div", null, h("time", null, fmt.ago(e.ts)), h("span", null, e.message)))))));
    } catch (e) { body.replaceChildren(h("p", { class: "err" }, e.message)); }
  }
  async function refresh() {
    try { const r = await get("/api/documents"); docs = r.documents; paint(); clearTimeout(timer); if (el.isConnected) timer = setTimeout(refresh, r.busy ? 1500 : 6000); } catch (e) { if (e.status !== 401) timer = setTimeout(refresh, 8000); }
  }
  clear(list).append(skeleton(4, 80)); await refresh();
  return { el, destroy() { clearTimeout(timer); } };
}
