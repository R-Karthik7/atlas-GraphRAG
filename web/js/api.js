export class ApiError extends Error { constructor(msg, status) { super(msg); this.status = status; } }
export const state = { csrf: "", user: null, onAuthLost: null };

export async function api(path, { method = "GET", body, form } = {}) {
  const opts = { method, headers: { "X-CSRF-Token": state.csrf }, credentials: "same-origin" };
  if (form) opts.body = form;
  else if (body !== undefined) { opts.headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(body); }
  let res;
  try { res = await fetch(path, opts); } catch { throw new ApiError("Cannot reach the server. Is it still running?", 0); }
  let data = null;
  try { data = await res.json(); } catch { /* non-JSON */ }
  if (!res.ok) {
    if (res.status === 401 && state.onAuthLost) state.onAuthLost();
    throw new ApiError((data && data.error) || `Request failed (${res.status})`, res.status);
  }
  return data;
}
export const get = (p) => api(p);
export const post = (p, body) => api(p, { method: "POST", body: body ?? {} });

// Server-sent events over fetch (so we can POST and send the CSRF header).
export async function stream(path, body, handlers, signal) {
  const res = await fetch(path, { method: "POST", credentials: "same-origin", signal,
    headers: { "Content-Type": "application/json", "X-CSRF-Token": state.csrf }, body: JSON.stringify(body) });
  if (!res.ok) { let m = `Request failed (${res.status})`; try { m = (await res.json()).error || m; } catch {} if (res.status === 401 && state.onAuthLost) state.onAuthLost(); throw new ApiError(m, res.status); }
  const reader = res.body.getReader(), dec = new TextDecoder(); let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const raw = buf.slice(0, i); buf = buf.slice(i + 2);
      const ev = /^event: (.*)$/m.exec(raw)?.[1], data = /^data: (.*)$/m.exec(raw)?.[1];
      if (ev && handlers[ev]) { try { handlers[ev](JSON.parse(data)); } catch (e) { console.error(e); } }
    }
  }
}
