// Shared helpers for all pages (no external dependencies).
const KS = window.KS || { tiers: {}, roles: {} };
const W = {
  me: null,
  esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])); },
  csrf() { const m = document.querySelector('meta[name="csrf-token"]'); return m ? m.content : ((W.me && W.me.csrf) || ""); },
  async api(path, opts = {}) {
    const o = { credentials: "same-origin", ...opts, headers: { "Accept": "application/json", ...(opts.headers || {}) } };
    if (o.raw !== undefined) { o.body = o.raw; delete o.raw; }
    else if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
    if (o.method && o.method !== "GET") o.headers["X-CSRF-Token"] = W.csrf();
    let r;
    try { r = await fetch(path, o); } catch (e) { const err = new Error("No connection - nothing was saved. Check your network and try again."); err.status = 0; throw err; }
    if (r.status === 401 && !path.startsWith("/api/auth/me")) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); throw new Error("Your session has ended - please sign in again."); }
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      let msg = (data && data.detail) ? data.detail : r.statusText;
      if (data && data.errors) msg = "Please check " + data.errors.map(e => W.fieldMsg(e)).join("; ");
      msg = String(msg); msg = msg.charAt(0).toUpperCase() + msg.slice(1);
      const err = new Error(msg); err.status = r.status; err.code = data && data.code; err.data = data; throw err;
    }
    return data;
  },
  // a readable message for one validation error, e.g. "note: at least 10 characters"
  fieldMsg(e) { const f = String((e.loc || []).slice(-1)[0] ?? "this field").replace(/_/g, " ");
    const m = String(e.msg || "").replace(/^String should have at least (\d+) characters?$/, "at least $1 characters")
      .replace(/^String should have at most (\d+) characters?$/, "at most $1 characters").replace(/^Field required$/, "required")
      .replace(/^Input should be /, "should be ").replace(/^Value error, /, "");
    return `${f}: ${m}`; },
  // inline error inside a form/dialog (kept until the next attempt) - toasts alone disappear
  formError(container, msg) { if (!container) return; let el = container.querySelector(".form-error");
    if (!msg) { if (el) el.remove(); return; }
    if (!el) { el = document.createElement("p"); el.className = "notice-inline err form-error"; el.setAttribute("role", "alert"); el.dir = "auto";
      const btns = container.querySelector(".form-buttons, .dlg-buttons, .tb-actions"); btns ? btns.before(el) : container.appendChild(el); }
    el.textContent = msg; },
  // disable a button while a save is running (no double submits)
  busy(b, on) { if (!b) return; b.disabled = !!on; on ? b.setAttribute("aria-busy", "true") : b.removeAttribute("aria-busy"); },
  toast(msg, kind = "") {
    const el = document.createElement("div"); el.className = "t " + kind; el.setAttribute("role", kind === "err" ? "alert" : "status");
    const span = document.createElement("span"); span.dir = "auto"; span.textContent = msg; el.appendChild(span);
    document.getElementById("toast").appendChild(el); setTimeout(() => el.remove(), kind === "err" ? 8000 : 4000);
  },
  // accessible modal dialogs (native <dialog>: focus trap, Esc to cancel)
  // save (optional): async function(value) run while the dialog stays open; on error the message is shown in the
  // dialog and everything typed is kept, so nothing is lost when the server refuses the input
  _dialog(inner, onOk, dir = "", save = null) {
    return new Promise(resolve => {
      const d = document.createElement("dialog"); d.className = "dlg"; d.setAttribute("aria-labelledby", "dlg-title");
      if (dir) d.dir = dir;  // e.g. rtl for an Arabic/Urdu patient page
      d.innerHTML = `<form method="dialog">${inner}</form>`; document.body.appendChild(d);
      const f = d.querySelector("form");
      f.querySelector("[data-cancel]").onclick = () => d.close("cancel");
      f.onsubmit = async ev => { if (ev.submitter && ev.submitter.hasAttribute("data-cancel")) return; ev.preventDefault(); const v = onOk(f); if (v === false) return;
        if (save) { const b = f.querySelector("[data-ok]"); W.busy(b, true); W.formError(f, "");
          try { const r = await save(v); d.close("ok"); resolve(r === undefined ? v : r); }
          catch (e) { W.formError(f, e.message); W.busy(b, false); }
          return; }
        d.close("ok"); resolve(v); };
      d.addEventListener("close", () => { if (d.returnValue !== "ok") resolve(null); d.remove(); });
      d.showModal(); const first = f.querySelector("textarea, input, [data-ok]"); if (first) first.focus();
    });
  },
  confirm(title, body = "", { ok = "Confirm", danger = false, cancel = "Cancel", dir = "", save = null } = {}) {
    return W._dialog(`<h2 id="dlg-title">${W.esc(title)}</h2>${body ? `<p>${body}</p>` : ""}
      <div class="dlg-buttons"><button type="button" class="secondary" data-cancel>${W.esc(cancel)}</button><button type="submit" data-ok class="${danger ? "danger" : ""}">${W.esc(ok)}</button></div>`, () => true, dir, save);
  },
  ask(title, label, { ok = "Confirm", body = "", min = 1, danger = false, placeholder = "", save = null, optional = false } = {}) {
    return W._dialog(`<h2 id="dlg-title">${W.esc(title)}</h2>${body ? `<p class="small">${body}</p>` : ""}
      <label for="dlg-text">${W.esc(label)}<textarea id="dlg-text" rows="3" ${optional ? "" : `required minlength="${min}"`} placeholder="${W.esc(placeholder)}"></textarea></label>
      <div class="dlg-buttons"><button type="button" class="secondary" data-cancel>Cancel</button><button type="submit" data-ok class="${danger ? "danger" : ""}">${W.esc(ok)}</button></div>`,
      f => { const v = f.querySelector("textarea").value.trim(); if (optional && !v) return ""; if (v.length < min) { f.querySelector("textarea").setAttribute("aria-invalid", "true"); W.formError(f, `Please write at least ${min} characters.`); return false; } return v; }, "", save);
  },
  loading(text = "Loading…") { return `<div class="loading" role="status">${W.esc(text)}</div>`; },
  empty(title, text = "", action = "") { return `<div class="empty"><h3>${W.esc(title)}</h3>${text ? `<p>${text}</p>` : ""}${action}</div>`; },
  dt(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}), hour: "2-digit", minute: "2-digit" }); },
  ago(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") ? s : s + "Z"); const h = (d - Date.now()) / 36e5;
    const a = Math.abs(h); const txt = a < 1 ? Math.round(a * 60) + " min" : a < 48 ? Math.round(a) + " h" : Math.round(a / 24) + " days";
    return h < 0 ? txt + " ago" : "in " + txt; },
  // status always = shape + text + colour (never colour alone)
  sla(st, frac) { const L = { red: ["▲", "Overdue"], amber: ["●", "Due soon"], green: ["✓", "On track"], none: ["–", "No deadline"] }[st] || ["", st];
    return `<span class="chip sla-${st}" title="${frac != null ? Math.round(frac * 100) + "% of the time allowed has passed" : ""}"><span class="shape" aria-hidden="true">${L[0]}</span>${L[1]}</span>`; },
  tier(t) { const T = KS.tiers[t]; if (!T) return `<span class="chip">${W.esc(t)}</span>`;
    return `<span class="chip tier-${t}" title="${W.esc(T.text)}"><span class="sr-only">Urgency: </span>${T.code} · ${T.name}</span>`; },
  tierText(t) { const T = KS.tiers[t]; return T ? T.text : t; },
  progName: { colorectal_fit: ["Bowel", "FIT", "FIT = faecal immunochemical test, a home stool test for hidden blood"],
    cervical: ["Cervical", "HPV", "HPV = human papillomavirus test (cervical screening)"],
    breast: ["Breast", "mammogram", "Breast X-ray screening"],
    diabetes_hba1c: ["Diabetes", "HbA1c", "HbA1c = blood test of average blood sugar over about 3 months"] },
  prog(p) { const x = W.progName[p]; return x ? `${x[0]} <abbr title="${W.esc(x[2])}">(${x[1]})</abbr>` : W.esc(p); },
  progText(p) { const x = W.progName[p]; return x ? `${x[0]} (${x[1]})` : p; },
  role(r) { return (KS.roles && KS.roles[r]) || String(r || "").replace(/_/g, " "); },
  pct(v) { return v == null ? "–" : (typeof v === "string" ? v : (v * 100).toFixed(1) + "%"); },
  can(p) { return !!(W.me && W.me.permissions.includes(p)); },
  // label every table cell with its column heading, so tables can become stacked cards on phones
  labelTables(root = document) {
    root.querySelectorAll("table").forEach(t => {
      const heads = [...t.querySelectorAll("thead th")].map(th => th.textContent.trim());
      if (!t.closest(".table-scroll")) { const w = document.createElement("div"); w.className = "table-scroll"; t.parentNode.insertBefore(w, t); w.appendChild(t); }
      if (!heads.length) return;
      t.querySelectorAll("tbody tr").forEach(tr => [...tr.children].forEach((td, i) => { if (!td.hasAttribute("data-label")) td.setAttribute("data-label", heads[i] || ""); }));
    });
  },
  bars(el, rows, labels = ["Baseline", "With KareShafaa"], fmt = v => v) {
    const max = Math.max(1e-9, ...rows.flatMap(r => [r.a || 0, r.b || 0]));
    el.innerHTML = `<div class="legend small"><span class="b0" style="background:#9aaab3"></span>${labels[0]}<span style="background:#0a6b66"></span>${labels[1]}</div>` +
      rows.map(r => `<div class="row"><div>${W.esc(r.label)}</div><div>
        <div class="bar b0" style="width:${(100 * (r.a || 0) / max).toFixed(1)}%" title="${labels[0]}: ${fmt(r.a)}"></div>
        <div class="bar b1" style="width:${(100 * (r.b || 0) / max).toFixed(1)}%" title="${labels[1]}: ${fmt(r.b)}"></div>
        <span class="small muted">${labels[0]} ${fmt(r.a)} → ${labels[1]} <b>${fmt(r.b)}</b></span></div></div>`).join("");
  },
  async init() {
    try { W.me = await W.api("/api/auth/me"); } catch (e) { W.me = null; }
    return W.me;
  },
  requireLogin() { if (!W.me) { location.href = "/login?next=" + encodeURIComponent(location.pathname + location.search); return false; } return true; },
};
document.addEventListener("DOMContentLoaded", () => {
  W.ready = document.querySelector('meta[name="csrf-token"]') ? W.init() : Promise.resolve(null);
  // tablet: expandable icon rail
  const tg = document.getElementById("nav-toggle");
  if (tg) { if (localStorage.getItem("ks-nav-open") === "1") { document.body.classList.add("nav-open"); tg.setAttribute("aria-expanded", "true"); }
    tg.onclick = () => { const open = document.body.classList.toggle("nav-open"); tg.setAttribute("aria-expanded", String(open)); localStorage.setItem("ks-nav-open", open ? "1" : "0"); }; }
  // close the account menu on outside click / Esc
  const um = document.getElementById("usermenu");
  if (um) { document.addEventListener("click", e => { if (um.open && !um.contains(e.target)) um.open = false; });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && um.open) { um.open = false; um.querySelector("summary").focus(); } }); }
  const main = document.getElementById("main");
  if (main) { W.labelTables(main); new MutationObserver(() => W.labelTables(main)).observe(main, { childList: true, subtree: true }); }
});
