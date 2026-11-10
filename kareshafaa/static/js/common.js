// Shared helpers for all pages (no external dependencies).
// page configuration from the JSON data block in base.html (not an inline script: CSP script-src 'self')
const KS = (() => { const el = document.getElementById("ks-config"); try { return el ? JSON.parse(el.textContent) : { tiers: {}, roles: {} }; } catch (e) { return { tiers: {}, roles: {} }; } })();
window.KS = KS;
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
  _dialog(inner, onOk, dir = "", save = null, onOpen = null) {
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
      if (onOpen) onOpen(f); d.showModal(); const first = f.querySelector("textarea, input, [data-ok]"); if (first) first.focus();
    });
  },
  confirm(title, body = "", { ok = "Confirm", danger = false, cancel = "Cancel", dir = "", save = null } = {}) {
    return W._dialog(`<h2 id="dlg-title">${W.esc(title)}</h2>${body ? `<p>${body}</p>` : ""}
      <div class="dlg-buttons"><button type="button" class="secondary" data-cancel>${W.esc(cancel)}</button><button type="submit" data-ok class="${danger ? "danger" : ""}">${W.esc(ok)}</button></div>`, () => true, dir, save);
  },
  // one-button information dialog (nothing to confirm): resolves when closed
  alert(title, body = "", { ok = "OK" } = {}) {
    return W._dialog(`<h2 id="dlg-title">${W.esc(title)}</h2>${body ? `<p>${body}</p>` : ""}
      <div class="dlg-buttons"><button type="submit" data-ok data-cancel>${W.esc(ok)}</button></div>`, () => true);
  },
  ask(title, label, { ok = "Confirm", body = "", min = 1, danger = false, placeholder = "", save = null, optional = false, suggest = null } = {}) {
    return W._dialog(`<h2 id="dlg-title">${W.esc(title)}</h2>${body ? `<p class="small">${body}</p>` : ""}
      <label for="dlg-text">${W.esc(label)}<textarea id="dlg-text" rows="3" ${optional ? "" : `required minlength="${min}"`} placeholder="${W.esc(placeholder)}"></textarea></label>
      <div class="dlg-buttons"><button type="button" class="secondary" data-cancel>Cancel</button><button type="submit" data-ok class="${danger ? "danger" : ""}">${W.esc(ok)}</button></div>`,
      f => { const v = f.querySelector("textarea").value.trim(); if (optional && !v) return ""; if (v.length < min) { f.querySelector("textarea").setAttribute("aria-invalid", "true"); W.formError(f, `Please write at least ${min} characters.`); return false; } return v; }, "", save, suggest ? f => W.attachSuggest(f, suggest) : null);
  },
  loading(text = "Loading…") { return `<div class="loading" role="status">${W.esc(text)}</div>`; },
  // inline icon from the SVG sprite in templates/_icons.html (decorative: the text next to it carries the meaning)
  ic(name, cls = "") { return `<svg class="icon ic-${name} ${cls}" aria-hidden="true"><use href="#i-${name}"/></svg>`; },
  // empty state: icon + one short line (errors get the warning icon)
  empty(title, text = "", action = "", icon = "") { const i = icon || (/^(Could not|No connection)/.test(title) ? "alert" : "ok");
    return `<div class="empty">${W.ic(i, "empty-ic")}<h3>${W.esc(title)}</h3>${text ? `<p>${text}</p>` : ""}${action}</div>`; },
  // number-first stat tile: big figure, 1-2 word label, icon (status colour always comes with the label)
  stat(href, v, label, cls = "", icon = "", attrs = "") { const tag = href ? "a" : "div";
    return `<${tag} class="stat ${cls}"${href ? ` href="${href}"` : ""} ${attrs}>${icon ? W.ic(icon, "stat-ic") : ""}<span class="v">${v ?? "–"}</span><span class="l">${label}</span></${tag}>`; },
  dt(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", ...(d.getFullYear() !== new Date().getFullYear() ? { year: "numeric" } : {}), hour: "2-digit", minute: "2-digit" }); },
  ago(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") ? s : s + "Z"); const h = (d - Date.now()) / 36e5;
    const a = Math.abs(h); const txt = a < 1 ? Math.round(a * 60) + " min" : a < 48 ? Math.round(a) + " h" : Math.round(a / 24) + " days";
    return h < 0 ? txt + " ago" : "in " + txt; },
  // status always = shape + text + colour (never colour alone)
  sla(st, frac) { const L = { red: ["▲", "Overdue"], amber: ["●", "Due soon"], green: ["✓", "On track"], none: ["–", "No deadline"] }[st] || ["", st];
    return `<span class="chip sla-${st}" title="${frac != null ? Math.round(frac * 100) + "% of the time allowed has passed" : ""}"><span class="shape" aria-hidden="true">${L[0]}</span>${L[1]}</span>`; },
  // plain time left on a deadline: "Overdue by 3 days", "5 h left", "2 days left" (colour + words, never colour alone)
  left(s) { if (!s) return { text: "No deadline", cls: "none" }; const d = new Date(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z"), ms = d - Date.now(), a = Math.abs(ms) / 36e5;
    const span = a < 1 ? Math.max(1, Math.round(a * 60)) + " min" : a < 48 ? Math.round(a) + " h" : Math.round(a / 24) + " days";
    return ms < 0 ? { text: "Overdue by " + span, cls: "red" } : { text: span + " left", cls: a < 24 ? "amber" : "green" }; },
  leftHtml(s, st) { const L = W.left(s); const cls = st === "red" || L.cls === "red" ? "red" : st === "amber" ? "amber" : L.cls;
    return `<span class="time-left ${cls}">${cls === "none" ? "" : W.ic("clock")}${W.esc(L.text)}</span>`; },
  langName(c) { const n = { en: "English", ar: "Arabic", hi: "Hindi", ur: "Urdu", ml: "Malayalam", tl: "Tagalog", bn: "Bengali", ta: "Tamil", te: "Telugu",
    fa: "Persian", fr: "French", ru: "Russian", zh: "Chinese", ps: "Pashto", ne: "Nepali", si: "Sinhala", so: "Somali", am: "Amharic", id: "Indonesian", es: "Spanish" }[c];
    return n || String(c || "–"); },
  // the one thing to do next on a case, in plain words (the button opens the case, where the existing checks apply)
  nextAction(r) {
    if (r.escalation_level && W.me && W.me.role === "clinical_lead") return "Review escalation";
    if (r.review_required) return "Review result";
    if (r.transfer_pending) return "Accept hand-over";
    if (r.patient_change_request) return "Change appointment";
    if (r.call_task) return "Call patient";
    if (r.open_consult) return "Answer question";
    return ({ issued: "Triage result", triaged: "Tell the patient their result", patient_notified: "Check understanding", acknowledged: "Arrange next step",
      confirmatory_test: "Record test result", consultation: "Record consultation", referral_sent: "Chase referral", referral_accepted: "Book appointment",
      referral_rejected: "Re-route referral", appointment_booked: "Record attendance", appointment_attended: "Record outcome",
      treatment_or_surveillance_started: "Close the loop", unreachable: "Reach the patient", no_show: "Rebook patient",
      declined_pending_review: "Review decision" })[r.state] || "Open case";
  },
  // "Needs you now": up to n cases, most urgent first, one clear button each
  nowList(rows, n = 5) {
    return `<ol class="now-list">${rows.slice(0, n).map(r => `<li class="now-item ${W.esc(r.sla_status)}" data-case="${r.id}">
      <div><div class="chips">${W.tier(r.tier)} ${W.leftHtml(r.due_at, r.sla_status)}${r.escalation_level ? ` <span class="chip sla-red">Escalated</span>` : ""}${r.review_required ? ` <span class="chip sla-red">Needs review</span>` : ""}</div>
        <div class="now-who">${W.esc(r.patient)}</div>
        <div class="now-what">${W.esc(W.progText(r.programme))} · now: ${W.esc(r.state_label)}</div>
        <div class="now-meta">Speaks ${W.esc(W.langName(r.language))} · ${r.owner_is_pool ? "Team pool" : W.esc(String(r.owner || "").split(" (")[0])} · #${r.id}</div></div>
      <a role="button" class="now-go" href="/cases/${r.id}">${W.esc(W.nextAction(r))}</a></li>`).join("")}</ol>`;
  },
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
      if (!t.closest(".table-scroll")) { const w = document.createElement("div"); w.className = "table-scroll"; w.tabIndex = 0; w.setAttribute("role", "region"); w.setAttribute("aria-label", "Table, scrolls sideways"); t.parentNode.insertBefore(w, t); w.appendChild(t); }
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
// Draft-text suggestions on staff clinical note fields (POST /api/assist/suggest; kareshafaa/services/assist.py).
// Advisory only: the draft appears under the field with Use / Dismiss. Nothing is inserted unless the user presses
// Use, and nothing is ever submitted. Off (no API key, AI switch off) or failing: the field works exactly as before.
// meta: { caseId, action, evidenceType, consultId, field, optionFrom: [select names whose value adds context] }
W.SUGGEST_NOTE = "Draft only. Check against the record before you use it.";
W.attachSuggest = (root, meta = {}) => {
  if (!root || !meta.caseId || !meta.action || W._sgOff) return;
  const tas = root.matches && root.matches("textarea") ? [root] : root.querySelectorAll("textarea:not([data-no-suggest])");
  tas.forEach(ta => { const d = ta.dataset; d.suggestCase = String(meta.caseId); d.suggestAction = meta.action;
    d.suggestField = meta.field || (["note", "notes", "description", "summary", "question", "answer", "response", "rationale"].includes(ta.name) ? ta.name : "note");
    if (meta.evidenceType) d.suggestEvidence = meta.evidenceType; if (meta.consultId) d.suggestConsult = String(meta.consultId);
    if (meta.optionFrom) d.suggestOption = [].concat(meta.optionFrom).join(","); });
};
(() => {
  const DEBOUNCE = 600, timers = new WeakMap(), inflight = new WeakMap();
  const panelOf = ta => ta._sgPanel && ta._sgPanel.isConnected ? ta._sgPanel : null;
  const hide = ta => { const p = panelOf(ta); if (p) p.remove(); ta._sgPanel = null; };
  const place = (ta, html, cls = "") => { let p = panelOf(ta);
    if (!p) { p = document.createElement("div"); p.className = "suggest"; p.setAttribute("role", "status"); p.setAttribute("aria-live", "polite");
      (ta.closest("label") || ta).after(p); ta._sgPanel = p; }
    p.className = "suggest " + cls; p.innerHTML = html; return p; };
  const optionOf = ta => { const f = ta.form || ta.closest("form"); if (!f || !ta.dataset.suggestOption) return null;
    for (const n of ta.dataset.suggestOption.split(",")) { const s = f.querySelector(`select[name="${n}"]`); if (s && s.value) return s.value; } return null; };
  const insert = (ta, text) => { const max = ta.maxLength > 0 ? ta.maxLength : 0; const t = max ? text.slice(0, max) : text;
    ta.focus(); ta.select(); let ok = false;
    try { ok = document.execCommand && document.execCommand("insertText", false, t); } catch (e) { ok = false; }  // keeps Ctrl+Z
    if (!ok || ta.value !== t) { ta.value = t; ta.dispatchEvent(new Event("input", { bubbles: true })); }
    ta._sgLast = ta.value.trim(); };
  async function ask(ta) {
    if (W._sgOff || !ta.isConnected || !W.me || W.me.role === "patient") return;
    const draft = ta.value.trim(); if (draft === ta._sgLast || draft === ta._sgDismissed) return;
    ta._sgLast = draft;
    const prev = inflight.get(ta); if (prev) prev.abort();
    const ac = new AbortController(); inflight.set(ta, ac);
    const d = ta.dataset, body = { case_id: +d.suggestCase, action: d.suggestAction, field: d.suggestField || "note", draft: draft.slice(0, 2000) };
    if (d.suggestEvidence) body.evidence_type = d.suggestEvidence; if (d.suggestConsult) body.consult_id = +d.suggestConsult;
    const opt = optionOf(ta); if (opt && /^[a-z0-9_]{1,40}$/.test(opt)) body.option = opt;
    const slow = setTimeout(() => { if (!ac.signal.aborted) place(ta, `${W.ic("bolt", "sg-ic")}<span class="sg-wait">Drafting a suggestion…</span>`, "wait"); }, 350);
    let r = null;
    try { r = await W.api("/api/assist/suggest", { method: "POST", body, signal: ac.signal }); }
    catch (e) { clearTimeout(slow); if (ac.signal.aborted) return; r = { enabled: true, unavailable: true }; }
    clearTimeout(slow); if (ac.signal.aborted || inflight.get(ta) !== ac) return; inflight.delete(ta);
    if (!r.enabled) { W._sgOff = true; hide(ta); return; }  // switched off or not set up: stop asking on this page
    if (!r.suggestion) { ta._sgLast = undefined;
      const p = place(ta, `${W.ic("bolt", "sg-ic")}<span class="sg-wait">Suggestion unavailable</span>`, "wait");
      setTimeout(() => { if (ta._sgPanel === p) hide(ta); }, 2500); return; }
    const p = place(ta, `<div class="sg-row">${W.ic("bolt", "sg-ic")}<p class="sg-text" dir="auto"></p></div>
      <div class="sg-actions"><button type="button" class="secondary sm" data-sg-use>Use</button><button type="button" class="ghost sm" data-sg-dismiss>Dismiss</button></div>
      <p class="sg-note">${W.esc(r.disclaimer || W.SUGGEST_NOTE)}</p>`);
    p.querySelector(".sg-text").textContent = r.suggestion;
    p.querySelector("[data-sg-use]").onclick = () => { insert(ta, r.suggestion); hide(ta); };
    p.querySelector("[data-sg-dismiss]").onclick = () => { ta._sgDismissed = ta.value.trim(); hide(ta); ta.focus(); };
  }
  const schedule = (ta, ms) => { clearTimeout(timers.get(ta)); const prev = inflight.get(ta); if (prev) { prev.abort(); inflight.delete(ta); ta._sgLast = undefined; }
    timers.set(ta, setTimeout(() => ask(ta), ms)); };
  document.addEventListener("focusin", e => { const ta = e.target; if (ta && ta.matches && ta.matches("textarea[data-suggest-case]")) schedule(ta, DEBOUNCE); });
  document.addEventListener("input", e => { const ta = e.target; if (ta && ta.matches && ta.matches("textarea[data-suggest-case]") && ta.value.trim() !== ta._sgLast) schedule(ta, DEBOUNCE); });
})();
document.addEventListener("DOMContentLoaded", () => {
  // after a cold start the "starting" page reopens this page with ?ks_wait=1: tidy the address bar
  if (new URLSearchParams(location.search).has("ks_wait")) { const u = new URL(location.href); u.searchParams.delete("ks_wait"); history.replaceState(null, "", u); }
  W.ready = document.querySelector('meta[name="csrf-token"]') ? W.init() : Promise.resolve(null);
  // tablet: expandable icon rail
  const tg = document.getElementById("nav-toggle");
  if (tg) { if (localStorage.getItem("ks-nav-open") === "1") { document.body.classList.add("nav-open"); tg.setAttribute("aria-expanded", "true"); }
    tg.onclick = () => { const open = document.body.classList.toggle("nav-open"); tg.setAttribute("aria-expanded", String(open)); localStorage.setItem("ks-nav-open", open ? "1" : "0"); }; }
  // close the account menu on outside click / Esc
  const um = document.getElementById("usermenu");
  if (um) { document.addEventListener("click", e => { if (um.open && !um.contains(e.target)) um.open = false; });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && um.open) { um.open = false; um.querySelector("summary").focus(); } }); }
  // phone "More" sheet: close on outside tap / Esc
  document.querySelectorAll(".bn-more, .nav-more").forEach(m => { if (!m.classList.contains("bn-more")) return;
    document.addEventListener("click", e => { if (m.open && !m.contains(e.target)) m.open = false; });
    document.addEventListener("keydown", e => { if (e.key === "Escape" && m.open) { m.open = false; m.querySelector("summary").focus(); } }); });
  const main = document.getElementById("main");
  if (main) { W.labelTables(main); new MutationObserver(() => W.labelTables(main)).observe(main, { childList: true, subtree: true }); }
});

// Inline event handlers (onclick= etc.) are blocked by the Content-Security-Policy (script-src 'self', no
// 'unsafe-inline'). Generated markup uses data-click="functionName" with optional data-args (a JSON array; "$el"
// stands for the clicked element) instead, and forms that are filtered live carry data-nosubmit.
window.reloadPage = () => location.reload();
document.addEventListener("click", e => {
  const el = e.target.closest && e.target.closest("[data-click]");
  if (!el || el.disabled) return;
  const fn = window[el.dataset.click];
  if (typeof fn !== "function") return;
  if (el.tagName === "A") e.preventDefault();
  let args = [];
  try { args = el.dataset.args ? JSON.parse(el.dataset.args) : []; } catch (err) { return; }
  fn(...args.map(a => a === "$el" ? el : a));
});
document.addEventListener("submit", e => { if (e.target.matches && e.target.matches("form[data-nosubmit]")) e.preventDefault(); });
