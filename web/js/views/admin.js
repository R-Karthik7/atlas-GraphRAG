import { h, icon, clear, toast, fmt, empty, confirmDialog } from "../ui.js";
import { get, api, post } from "../api.js";
import { state } from "../api.js";

export async function render() {
  let tab = "users"; const body = h("div"), tabs = h("div", { class: "tabs" });
  const el = h("section", { class: "view" }, h("div", { class: "page-head" }, h("div", null, h("div", { class: "eyebrow" }, "Administration"), h("h1", null, "People, feedback and system"), h("p", null, "Manage roles, review feedback to improve ranking, inspect the audit trail, and sync the graph to Neo4j.")), h("span", { class: "grow" }), tabs), body);
  [["users", "Users"], ["feedback", "Feedback"], ["audit", "Audit log"], ["system", "System"]].forEach(([id, l]) => tabs.append(h("button", { class: id === tab ? "on" : "", onClick: (e) => { tab = id; [...tabs.children].forEach((b) => b.classList.toggle("on", b === e.currentTarget)); paint(); } }, l)));
  async function paint() {
    clear(body).append(h("div", { class: "skeleton", style: { height: "160px" } }));
    try {
      if (tab === "users") {
        const us = await get("/api/admin/users"); clear(body).append(h("div", { class: "card" }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["User", "Role", "Last sign-in", "Status"].map((t) => h("th", null, t)))), h("tbody", null, us.map((u) => {
          const sel = h("select", { style: { width: "auto" }, disabled: u.id === state.user.id, onChange: async () => { try { await api("/api/admin/users/" + u.id, { method: "PATCH", body: { role: sel.value } }); toast("Role updated", "good"); } catch (e) { toast(e.message, "bad"); sel.value = u.role; } } }, ["admin", "editor", "viewer"].map((r) => h("option", { value: r, selected: r === u.role }, r)));
          return h("tr", null, h("td", null, h("b", null, u.name), h("div", { class: "faint", style: { fontSize: "12.5px" } }, u.email)), h("td", null, sel), h("td", { class: "faint" }, u.last_login ? fmt.ago(u.last_login) : "never"), h("td", null, h("button", { class: "btn sm " + (u.active ? "" : "primary"), disabled: u.id === state.user.id, onClick: async () => { try { await api("/api/admin/users/" + u.id, { method: "PATCH", body: { active: !u.active } }); toast(u.active ? "Account disabled" : "Account enabled"); paint(); } catch (e) { toast(e.message, "bad"); } } }, u.active ? "Disable" : "Enable"))); })))),
          h("p", { class: "faint", style: { marginTop: "12px", fontSize: "13px" } }, "Admins manage everything · Editors upload and review · Viewers can search and read."));
      } else if (tab === "feedback") {
        const rows = await get("/api/feedback/queue?status=new"); clear(body);
        if (!rows.length) return body.append(empty("check", "Feedback queue is clear", "Thumbs and source marks from Ask show up here."));
        body.append(h("div", { class: "grid stagger" }, rows.map((f, i) => { const card = h("article", { class: "card pad", style: { "--i": Math.min(i, 8) } }, h("div", { class: "row wrap" }, h("span", { class: "chip " + (f.rating > 0 ? "teal" : "bad") }, f.rating > 0 ? "👍 helpful" : "👎 needs work"), (f.tags || []).map((t) => h("span", { class: "chip" }, t)), h("span", { class: "sp faint" }, (f.user_name || "anonymous") + " · " + fmt.ago(f.ts))), h("h3", { style: { margin: "10px 0 6px" } }, f.question), h("p", { class: "muted", style: { fontSize: "14px" } }, f.answer), f.comment ? h("p", { style: { marginTop: "8px", fontStyle: "italic" } }, "“" + f.comment + "”") : null,
          h("div", { class: "row", style: { marginTop: "12px" } }, h("button", { class: "btn sm", onClick: async () => { await post(`/api/feedback/${f.id}/resolve`, { action: "to_eval" }); toast("Added to the evaluation set", "good"); card.remove(); } }, icon("eval"), "Add to evaluation set"), h("button", { class: "btn sm ghost", onClick: async () => { await post(`/api/feedback/${f.id}/resolve`, { action: "done" }); card.remove(); } }, "Mark reviewed"))); return card; })));
      } else if (tab === "audit") {
        const rows = await get("/api/admin/audit"); clear(body).append(h("div", { class: "card", style: { overflowX: "auto" } }, h("table", { class: "t" }, h("thead", null, h("tr", null, ["When", "User", "Action", "Target", "IP"].map((t) => h("th", null, t)))), h("tbody", null, rows.map((r) => h("tr", null, h("td", { class: "faint" }, fmt.date(r.ts)), h("td", null, r.user_email || "—"), h("td", null, h("span", { class: "chip " + (r.action.includes("failed") ? "bad" : "") }, r.action)), h("td", { class: "mono" }, (r.target || "") + (r.detail ? " · " + r.detail : "")), h("td", { class: "faint mono" }, r.ip || "")))))));
      } else {
        const s = await get("/api/admin/system"); clear(body).append(h("div", { class: "grid g2" },
          h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "10px" } }, "Engine"), [["Mode", s.mode], ["Language model", s.llm.model || "none (extractive answers)"], ["Embeddings", `${s.embedding.model} · ${s.embedding.dim}d`], ["Reranker", s.reranker], ["Database", s.db_path], ["Users", s.users]].map(([k, v]) => h("div", { class: "row", style: { padding: "7px 0", borderBottom: "1px dashed var(--line)" } }, h("span", { class: "muted" }, k), h("span", { class: "sp mono", style: { fontSize: "13px" } }, v)))),
          h("div", { class: "card pad" }, h("h3", { style: { marginBottom: "6px" } }, "Neo4j mirror"), h("p", { class: "muted", style: { marginBottom: "14px" } }, s.neo4j_configured ? "Neo4j credentials found in .env. Push documents, passages (with page numbers), resolved entities, relationships and themes." : "Add NEO4J_URI, NEO4J_USERNAME and NEO4J_PASSWORD to .env to enable the mirror."), h("button", { class: "btn dark", disabled: !s.neo4j_configured, onClick: async (e) => { e.currentTarget.disabled = true; try { const r = await post("/api/admin/neo4j-sync"); toast("Synced: " + Object.entries(r.counts).map(([k, v]) => `${v} ${k}`).join(", "), "good"); } catch (er) { toast(er.message, "bad"); } e.currentTarget.disabled = false; } }, icon("refresh"), "Sync to Neo4j"))));
      }
    } catch (e) { clear(body).append(h("p", { class: "err" }, e.message)); }
  }
  await paint();
  return { el };
}
