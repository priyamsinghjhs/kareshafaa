// Patient app: "My contact details" (own record only, audited on the server) and appointment / result history.
// Same checks as the staff form (PF rules in patients.js mirror services/patients.py); the server still decides.
(function () {
  let OPEN = false, DRAFT = null, P = null;
  const phoneOk = raw => { let s = String(raw || "").trim(); if (!s) return true; const plus = s.startsWith("+") || s.startsWith("00");
    let d = s.replace(/[\s().-]/g, "").replace(/^\+/, ""); if (d.startsWith("00")) d = d.slice(2); if (!/^\d+$/.test(d)) return false;
    if (!plus && d.startsWith("0") && (d.length === 9 || d.length === 10)) d = "971" + d.slice(1); else if (!plus && d.length === 9 && d[0] === "5") d = "971" + d;
    if (d.startsWith("971")) return /^5\d{8}$/.test(d.slice(3)) || /^[2-9]\d{7}$/.test(d.slice(3));
    return plus && d.length >= 8 && d.length <= 15; };
  const LOC = { en: "en-GB", ar: "ar-AE", hi: "hi-IN", ur: "ur-PK" };
  const when = (iso, lang, time = true) => { if (!iso) return "–"; const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    try { return t.toLocaleString(LOC[lang], time ? { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Dubai" } : { dateStyle: "medium", timeZone: "Asia/Dubai" }); } catch (e) { return W.dt(iso); } };
  const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;

  function form(T, v, O) {
    const opt = (pairs, cur, blank) => (blank !== undefined ? `<option value="">${W.esc(blank)}</option>` : "") + pairs.map(([k, l]) => `<option value="${W.esc(k)}" ${String(cur ?? "") === String(k) ? "selected" : ""}>${W.esc(l)}</option>`).join("");
    return `<form id="self-form" class="action-form" novalidate>
      <label>${W.esc(T.phone)}<input name="phone" type="tel" dir="ltr" inputmode="tel" maxlength="30" autocomplete="tel" value="${W.esc(v.phone || "")}"><span class="hint small muted">${W.esc(T.phone_hint)}</span></label>
      <label class="check"><input type="checkbox" name="whatsapp" ${v.whatsapp ? "checked" : ""}> ${W.esc(T.whatsapp)}</label>
      <label>${W.esc(T.email)}<input name="email" type="email" dir="ltr" maxlength="200" autocomplete="email" value="${W.esc(v.email || "")}"></label>
      <label>${W.esc(T.channel)}<select name="preferred_channel">${opt(Object.entries(T.channels), v.preferred_channel, T.no_pref)}</select></label>
      <label>${W.esc(T.address)}<input name="address_line" maxlength="300" autocomplete="street-address" value="${W.esc(v.address_line || "")}"></label>
      <div class="form-pair"><label>${W.esc(T.city)}<input name="city" maxlength="100" value="${W.esc(v.city || "")}"></label>
      <label>${W.esc(T.emirate)}<select name="emirate" required>${opt(O.emirates.map(e => [e, e]), v.emirate, "–")}</select></label></div>
      <div data-region ${v.emirate && v.emirate !== "Abu Dhabi" ? "hidden" : ""}><label>${W.esc(T.region)}<select name="region">${opt(O.regions.map(r => [r, r]), v.region, "–")}</select></label></div>
      <div class="tb-actions"><button type="submit">${W.esc(T.save)}</button><button type="button" class="secondary outline" id="self-cancel">${W.esc(T.cancel)}</button></div></form>`;
  }

  function collect(f) {
    const g = n => f.querySelector(`[name="${n}"]`), out = {};
    ["phone", "email", "address_line", "city", "emirate", "region", "preferred_channel"].forEach(k => { const x = g(k).value.trim(); out[k] = x || null; });
    out.whatsapp = g("whatsapp").checked;
    if (out.emirate && out.emirate !== "Abu Dhabi") out.region = null;
    return out;
  }
  function check(f, T) {
    const g = n => f.querySelector(`[name="${n}"]`), bad = [];
    const mark = (n, ok, m) => { g(n).setAttribute("aria-invalid", ok ? "false" : "true"); if (!ok) bad.push(m); };
    mark("phone", phoneOk(g("phone").value), T.err_phone);
    const em = g("email").value.trim(); mark("email", !em || /^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$/.test(em), T.err_email);
    const ch = g("preferred_channel").value; if (["sms", "whatsapp", "phone"].includes(ch) && !g("phone").value.trim()) { mark("phone", false, T.err_channel); }
    if (g("emirate").value === "Abu Dhabi") mark("region", !!g("region").value, T.err_region);
    return bad;
  }

  function render(lang, rtl) {
    const box = document.getElementById("pprofile"); if (!box || !P) return;
    const T = P.text, C = P.contact, H = P.history;
    const v = x => x ? W.esc(x) : `<span class="muted">${W.esc(T.not_given)}</span>`;
    const ltr = x => x ? `<span dir="ltr">${W.esc(x)}</span>` : v(x);
    const details = OPEN ? form(T, DRAFT || C, P.options) : `<dl class="facts self-facts">
        <div><dt>${W.esc(T.phone)}</dt><dd>${ltr(C.phone)}${C.phone && C.whatsapp ? ' <span class="chip">WhatsApp</span>' : ""}</dd></div>
        <div><dt>${W.esc(T.email)}</dt><dd>${ltr(C.email)}</dd></div>
        <div><dt>${W.esc(T.channel)}</dt><dd>${C.preferred_channel ? W.esc(T.channels[C.preferred_channel]) : W.esc(T.no_pref)}</dd></div>
        <div class="wide"><dt>${W.esc(T.address)}</dt><dd>${v([C.address_line, C.city, C.emirate, C.region && C.region !== C.emirate ? C.region : ""].filter(Boolean).join(", "))}</dd></div></dl>
        <div class="tb-actions"><button class="secondary" id="self-edit">${W.esc(T.edit)}</button></div>`;
    const appts = H.appointments.length ? `<ul class="hist">${H.appointments.map(a => `<li class="hist-item ${a.status}"><div><b>${W.esc(when(a.start, lang))}</b> <span class="chip ${a.status === "upcoming" ? "sla-amber" : a.status === "fulfilled" ? "sla-green" : a.status === "noshow" ? "sla-red" : ""}">${W.esc(a.status_text)}</span></div><div class="small">${W.esc(cap(a.programme))}${a.place ? " · " + W.esc(a.place) : ""}</div></li>`).join("")}</ul>` : `<p class="small muted">${W.esc(T.no_appts)}</p>`;
    const results = H.results.length ? `<ul class="hist">${H.results.map(r => `<li class="hist-item"><div><b>${W.esc(when(r.date, lang, false))}</b> · ${W.esc(cap(r.programme))} <span class="small muted">(${W.esc(r.kind)})</span></div><div><span class="chip ${r.outcome === "normal" ? "sla-green" : r.outcome === "follow_up" ? "sla-amber" : ""}" data-outcome="${r.outcome}">${W.esc(r.outcome_text)}</span>${r.site ? ` <span class="small muted">${W.esc(r.site)}</span>` : ""}</div></li>`).join("")}</ul>` : `<p class="small muted">${W.esc(T.no_results)}</p>`;
    box.innerHTML = `<div class="card" id="self-details"><h4>${W.esc(T.details_title)}</h4>${details}</div>
      <div class="card" id="self-appts"><h4>${W.esc(T.appts_title)}</h4>${appts}</div>
      <div class="card" id="self-results"><h4>${W.esc(T.results_title)}</h4>${results}</div>`;
    const ed = document.getElementById("self-edit"); if (ed) ed.onclick = () => { OPEN = true; DRAFT = null; render(lang, rtl); const i = box.querySelector("#self-form input"); if (i) i.focus(); };
    const f = document.getElementById("self-form"); if (!f) return;
    const em = f.querySelector('[name="emirate"]'), reg = f.querySelector("[data-region]");
    em.onchange = () => { reg.hidden = em.value !== "Abu Dhabi" && em.value !== ""; };
    f.oninput = f.onchange = () => { DRAFT = collect(f); };  // kept across language switches and failed saves
    document.getElementById("self-cancel").onclick = () => { OPEN = false; DRAFT = null; render(lang, rtl); };
    f.onsubmit = async ev => { ev.preventDefault(); W.formError(f, "");
      const bad = check(f, T); DRAFT = collect(f);
      if (bad.length) { W.formError(f, bad.join(" ")); f.querySelector('[aria-invalid="true"]')?.focus(); return; }
      const b = f.querySelector("button[type=submit]"); W.busy(b, true);
      try { const r = await W.api("/api/me/contact", { method: "POST", body: DRAFT });
        OPEN = false; DRAFT = null; W.toast(r.changed.length ? T.saved : T.nothing, "ok"); await load(lang, rtl); }
      catch (e) { W.busy(b, false); W.formError(f, e.message); } };
  }

  async function load(lang, rtl) {
    const box = document.getElementById("pprofile"); if (!box) return;
    try { P = await W.api("/api/me/profile?lang=" + encodeURIComponent(lang)); render(lang, rtl); }
    catch (e) { box.innerHTML = `<div class="card notice">${W.esc(e.message)}</div>`; }
  }
  // called by patient.html after each render (language switch re-renders; an open form keeps its draft)
  window.renderProfile = d => load(d.language, d.rtl);
})();
