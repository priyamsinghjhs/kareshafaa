// Shared helpers for all pages (no external dependencies).
const W = {
  me: null,
  esc(s) { return String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c])); },
  async api(path, opts = {}) {
    const o = { credentials: "same-origin", ...opts, headers: { "Accept": "application/json", ...(opts.headers || {}) } };
    if (o.raw !== undefined) { o.body = o.raw; delete o.raw; }
    else if (o.body && typeof o.body !== "string") { o.body = JSON.stringify(o.body); o.headers["Content-Type"] = "application/json"; }
    const r = await fetch(path, o);
    const ct = r.headers.get("content-type") || "";
    const data = ct.includes("json") ? await r.json() : await r.text();
    if (!r.ok) {
      let msg = (data && data.detail) ? data.detail : r.statusText;
      if (data && data.errors) msg += ": " + data.errors.map(e => e.loc.slice(-1)[0] + " " + e.msg).join("; ");
      const err = new Error(msg); err.status = r.status; throw err;
    }
    return data;
  },
  toast(msg, kind = "") {
    const el = document.createElement("div"); el.className = "t " + kind; el.textContent = msg;
    document.getElementById("toast").appendChild(el); setTimeout(() => el.remove(), kind === "err" ? 7000 : 3500);
  },
  dt(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") || s.includes("+") ? s : s + "Z");
    return d.toLocaleString(undefined, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" }); },
  ago(s) { if (!s) return "–"; const d = new Date(s.endsWith("Z") ? s : s + "Z"); const h = (d - Date.now()) / 36e5;
    const a = Math.abs(h); const txt = a < 1 ? Math.round(a * 60) + "m" : a < 48 ? Math.round(a) + "h" : Math.round(a / 24) + "d";
    return h < 0 ? txt + " ago" : "in " + txt; },
  sla(st, frac) { const lbl = { red: "Overdue", amber: "Due soon", green: "On track", none: "No clock" }[st] || st;
    return `<span class="chip sla-${st}" title="${frac != null ? Math.round(frac * 100) + "% of SLA elapsed" : ""}">${lbl}</span>`; },
  tier(t) { const L = { critical: ["T0", "Critical"], urgent: ["T1", "Urgent"], high: ["T2", "Soon"], routine: ["T3", "Routine"] }[t] || [t, ""];
    return `<span class="chip tier-${t}" title="${L[0]} ${L[1]}">${L[0]} ${L[1]}</span>`; },
  prog(p) { return { colorectal_fit: "Bowel (FIT)", cervical: "Cervical (HPV)", breast: "Breast (mammo)", diabetes_hba1c: "Diabetes (HbA1c)" }[p] || p; },
  pct(v) { return v == null ? "–" : (typeof v === "string" ? v : (v * 100).toFixed(1) + "%"); },
  homeFor(role) { return { patient: "/patient", analyst: "/dashboard", admin: "/governance", translator: "/governance" }[role] || "/queue"; },
  navFor(role) {
    const L = { queue: ["/queue", "Work queue"], consults: ["/consults", "Consultations"], dash: ["/dashboard", "KPIs & simulation"],
      gov: ["/governance", "Governance"], audit: ["/audit", "Audit"], patient: ["/patient", "My results"],
      ingest: ["/ingest", "Results inbox"], demo: ["/demo", "Demo scenarios"] };
    const m = { patient: ["patient"], coordinator: ["queue", "ingest", "demo"], gp: ["queue", "consults", "demo"], specialist: ["queue", "consults"],
      clinical_lead: ["queue", "consults", "ingest", "dash", "gov", "audit", "demo"], analyst: ["dash", "gov"], admin: ["gov", "audit", "dash", "demo"], translator: ["gov"] };
    return (m[role] || []).map(k => L[k]);
  },
  async init() {
    try { W.me = await W.api("/api/auth/me"); } catch (e) { W.me = null; }
    const nav = document.getElementById("nav-links");
    if (W.me) {
      nav.innerHTML = W.navFor(W.me.role).map(([h, t]) => `<a href="${h}" class="${location.pathname.startsWith(h) ? "active" : ""}">${t}</a>`).join("");
      document.getElementById("whoami").innerHTML = `${W.esc(W.me.display_name)} · <b>${W.esc(W.me.role)}</b>`;
    }
    const sw = document.getElementById("role-switcher");
    if (sw) {
      try {
        const d = await W.api("/api/auth/demo-users");
        const featured = d.users.filter(u => u.featured), rest = d.users.filter(u => !u.featured);
        const opt = u => `<option value="${W.esc(u.username)}" ${W.me && W.me.display_name === u.display_name ? "selected" : ""}>${W.esc(u.role)} · ${W.esc(u.display_name)}</option>`;
        sw.innerHTML = `<option value="">— switch demo user —</option><optgroup label="Featured demo users">${featured.map(opt).join("")}</optgroup>` +
          `<optgroup label="All staff">${rest.map(opt).join("")}</optgroup>`;
        sw.onchange = async () => { if (!sw.value) return; const r = await W.login(sw.value); location.href = W.homeFor(r.user.role); };
      } catch (e) { sw.style.display = "none"; }
    }
    return W.me;
  },
  async login(username) { return W.api("/api/auth/demo-login", { method: "POST", body: { username } }); },
  async switchTo(username, url) {
    try { await W.api("/api/auth/demo-login", { method: "POST", body: { username } }); location.href = url || W.homeFor((W.me || {}).role); }
    catch (e) { W.toast(e.message, "err"); }
  },
  requireLogin() { if (!W.me) { document.querySelector("main").innerHTML = `<div class="empty">Please choose a demo user (top right, or on the <a href="/">home page</a>).</div>`; return false; } return true; },
  bars(el, rows, labels = ["Baseline", "With Wasl"], fmt = v => v) {
    const max = Math.max(1e-9, ...rows.flatMap(r => [r.a || 0, r.b || 0]));
    el.innerHTML = `<div class="legend small"><span class="b0" style="background:#b0bec5"></span>${labels[0]}<span style="background:#00796b"></span>${labels[1]}</div>` +
      rows.map(r => `<div class="row"><div>${W.esc(r.label)}</div><div>
        <div class="bar b0" style="width:${(100 * (r.a || 0) / max).toFixed(1)}%" title="${labels[0]}: ${fmt(r.a)}"></div>
        <div class="bar b1" style="width:${(100 * (r.b || 0) / max).toFixed(1)}%" title="${labels[1]}: ${fmt(r.b)}"></div>
        <span class="small muted">${fmt(r.a)} → <b>${fmt(r.b)}</b></span></div></div>`).join("");
  },
};
document.addEventListener("DOMContentLoaded", () => { W.ready = W.init(); });
