// Patient app: where I am in the screening-to-care loop (step tracker + next step), my appointments (confirm / ask
// to change - the request goes to the care team, the appointment does not move), my results, messages (simulated),
// my contact details (own record only, audited on the server) and how we may contact me (consent, audited).
// Same checks as the staff form (PF rules in patients.js mirror services/patients.py); the server still decides.
(function () {
  let OPEN = false, DRAFT = null, P = null, CHANGE = null, CHANGE_DRAFT = {};
  const phoneOk = raw => { let s = String(raw || "").trim(); if (!s) return true; const plus = s.startsWith("+") || s.startsWith("00");
    let d = s.replace(/[\s().-]/g, "").replace(/^\+/, ""); if (d.startsWith("00")) d = d.slice(2); if (!/^\d+$/.test(d)) return false;
    if (!plus && d.startsWith("0") && (d.length === 9 || d.length === 10)) d = "971" + d.slice(1); else if (!plus && d.length === 9 && d[0] === "5") d = "971" + d;
    if (d.startsWith("971")) return /^5\d{8}$/.test(d.slice(3)) || /^[2-9]\d{7}$/.test(d.slice(3));
    return plus && d.length >= 8 && d.length <= 15; };
  const LOC = { en: "en-GB", ar: "ar-AE", hi: "hi-IN", ur: "ur-PK" };
  const when = (iso, lang, time = true) => { if (!iso) return "–"; const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(iso) ? iso : iso + "Z");
    try { return t.toLocaleString(LOC[lang], time ? { dateStyle: "medium", timeStyle: "short", timeZone: "Asia/Dubai" } : { dateStyle: "medium", timeZone: "Asia/Dubai" }); } catch (e) { return W.dt(iso); } };
  const day = (d, lang) => { if (!d) return "–"; try { return new Date(d.slice(0, 10) + "T12:00:00Z").toLocaleDateString(LOC[lang], { dateStyle: "long", timeZone: "Asia/Dubai" }); } catch (e) { return d; } };
  const cap = s => s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
  const fill = (s, o) => String(s || "").replace(/\{(\w+)\}/g, (m, k) => (k in o ? o[k] : m));
  const ICON = { done: "✓", current: "●", attention: "!", skipped: "–", todo: "" };

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

  // ---------------------------------------------------------------- where am I in the loop
  function tracker(L, T, lang, primary) {
    const st = L.stages.map(s => { const lab = s.key === "care" && s.status === "done" && /closed/.test(L.situation) ? T.stage.care : T.stage[s.key];
      const date = s.date ? (s.key === "recall" ? day(s.date, lang) : when(s.date, lang, s.key === "appointment" && s.status === "current")) : "";
      return `<li class="tk tk-${s.status}" data-stage="${s.key}" ${s.key === L.current ? 'aria-current="step"' : ""}><span class="tk-dot" aria-hidden="true">${ICON[s.status] || ""}</span>
        <span class="tk-body"><span class="tk-label">${W.esc(lab)}</span><span class="tk-meta">${W.esc(T.stage_status[s.status] || "")}${date ? " · " + W.esc(date) : ""}</span></span></li>`; }).join("");
    const a = L.appointment, sit = T.situation[L.situation] || "";
    const text = fill(sit, { when: a ? when(a.start, lang) : "", place: a && a.place ? a.place : "" });
    const R = L.recall;
    const recall = R ? `<p class="small" data-recall>${W.esc(fill(T.recall_on, { programme: L.programme_name, date: day(R.due_on, lang) }))}${R.overdue ? " " + W.esc(T.recall_overdue) : R.soon ? " " + W.esc(T.recall_soon) : ""}</p>`
      : (["normal", "closed", "closed_other"].includes(L.situation) ? `<p class="small muted" data-recall>${W.esc(T.recall_none)}</p>` : "");
    const go = [];
    if (L.situation === "answer") go.push(`<a role="button" class="sm" href="#tb">${W.esc(T.go_answer)}</a>`);
    if (L.situation === "booked" && a) go.push(`<button type="button" class="sm" data-confirm="${a.id}">${W.esc(T.confirm_btn)}</button><button type="button" class="secondary sm" data-change="${a.id}">${W.esc(T.change_btn)}</button>`);
    if (/^booked/.test(L.situation)) go.push(`<a role="button" class="secondary outline sm" href="#self-appts">${W.esc(T.go_appt)}</a>`);
    if (L.situation === "unreachable") go.push(`<a role="button" class="sm" href="#self-details">${W.esc(T.go_details)}</a>`);
    if (["missed", "declined", "unreachable"].includes(L.situation) && document.getElementById("p-ask")) go.push(`<a role="button" class="secondary outline sm" href="#p-ask">${W.esc(T.go_ask)}</a>`);
    return `<div class="loop ${primary ? "" : "loop-other"}" data-programme="${W.esc(L.programme)}" data-situation="${W.esc(L.situation)}">
      ${primary ? "" : ""}<h3 class="loop-prog">${W.esc(cap(L.programme_name))}</h3>
      <ol class="tracker" aria-label="${W.esc(T.loop_title)}: ${W.esc(cap(L.programme_name))}">${st}</ol>
      <div class="next-box" role="status"><h4>${W.esc(T.next_title)}</h4><p>${W.esc(text)}</p>${recall}${go.length ? `<div class="tb-actions">${go.join("")}</div>` : ""}</div></div>`;
  }

  function loopBox(lang) {
    const box = document.getElementById("ploop"); if (!box || !P) return;
    const T = P.text, L = P.loop || [];
    if (!L.length) { box.innerHTML = `<h3 class="loop-title">${W.esc(T.loop_title)}</h3><div class="next-box" role="status"><p>${W.esc(T.situation.none)}</p><p class="small muted">${W.esc(T.recall_none)}</p></div>`; return; }
    const [first, ...rest] = L;
    box.innerHTML = `<h3 class="loop-title">${W.esc(T.loop_title)}</h3><p class="small muted">${W.esc(T.loop_intro)}</p>${tracker(first, T, lang, true)}
      ${rest.map(x => `<details class="loop-more"><summary>${W.esc(cap(x.programme_name))} · ${W.esc((T.situation[x.situation] || "").split(/[.।۔]/)[0])}</summary>${tracker(x, T, lang, false)}</details>`).join("")}`;
  }

  // ---------------------------------------------------------------- appointments and results
  function changeForm(T, id) {
    const d = CHANGE_DRAFT[id] || {};
    return `<form class="action-form appt-change" id="chg-${id}" data-id="${id}" novalidate><h4>${W.esc(T.change_title)}</h4>
      <fieldset><legend>${W.esc(T.change_why)}</legend>${P.options.barriers.map(b => `<label class="check"><input type="radio" name="barrier" value="${b}" ${d.barrier === b ? "checked" : ""}> ${W.esc(T.barriers[b])}</label>`).join("")}</fieldset>
      <label>${W.esc(T.change_when)}<input name="preferred" maxlength="200" placeholder="${W.esc(T.change_when_ph)}" value="${W.esc(d.preferred || "")}"></label>
      <label>${W.esc(T.change_note)}<textarea name="note" rows="2" maxlength="1000">${W.esc(d.note || "")}</textarea></label>
      <p class="small muted">${W.esc(T.change_keep)}</p>
      <div class="tb-actions"><button type="submit">${W.esc(T.change_send)}</button><button type="button" class="secondary outline" data-change-cancel>${W.esc(T.cancel)}</button></div></form>`;
  }
  function apptItem(a, T, lang) {
    const chip = a.status === "upcoming" ? "sla-amber" : a.status === "fulfilled" ? "sla-green" : a.status === "noshow" ? "sla-red" : "";
    const cr = a.change_request;
    const notes = [a.moved_to ? fill(T.moved_to, { when: when(a.moved_to, lang) }) : "", a.reminder_sent_at ? fill(T.reminder_sent, { when: when(a.reminder_sent_at, lang) }) : "",
      a.confirmed && a.status === "upcoming" ? "✓ " + T.you_confirmed : "", cr && cr.status === "open" ? T.change_sent : "", cr && cr.status === "handled" && a.status === "upcoming" ? T.change_handled : ""].filter(Boolean);
    const acts = a.can_respond ? `<div class="tb-actions">${a.confirmed ? "" : `<button type="button" class="sm" data-confirm="${a.id}">${W.esc(T.confirm_btn)}</button>`}<button type="button" class="secondary sm" data-change="${a.id}">${W.esc(T.change_btn)}</button></div>` : "";
    return `<li class="hist-item ${a.status}" data-appt="${a.id}"><div><b>${W.esc(when(a.start, lang))}</b> <span class="chip ${chip}">${W.esc(a.status_text)}</span></div>
      <div class="small">${W.esc(cap(a.programme))}${a.place ? " · " + W.esc(a.place) : ""}</div>${notes.map(n => `<div class="small muted">${W.esc(n)}</div>`).join("")}
      ${CHANGE === a.id && a.can_respond ? changeForm(T, a.id) : acts}</li>`;
  }
  function historyBox(lang) {
    const box = document.getElementById("pappts"); if (!box || !P) return;
    const T = P.text, H = P.history;
    const appts = H.appointments.length ? `<ul class="hist">${H.appointments.map(a => apptItem(a, T, lang)).join("")}</ul>` : `<p class="small muted">${W.esc(T.no_appts)}</p>`;
    const results = H.results.length ? `<ul class="hist">${H.results.map(r => `<li class="hist-item"><div><b>${W.esc(when(r.date, lang, false))}</b> · ${W.esc(cap(r.programme))} <span class="small muted">(${W.esc(r.kind)})</span></div><div><span class="chip ${r.outcome === "normal" ? "sla-green" : r.outcome === "follow_up" ? "sla-amber" : ""}" data-outcome="${r.outcome}">${W.esc(r.outcome_text)}</span>${r.site ? ` <span class="small muted">${W.esc(r.site)}</span>` : ""}</div></li>`).join("")}</ul>` : `<p class="small muted">${W.esc(T.no_results)}</p>`;
    box.innerHTML = `<div class="card" id="self-appts"><h4>${W.esc(T.appts_title)}</h4>${appts}</div>
      <div class="card" id="self-results"><h4>${W.esc(T.results_title)}</h4>${results}</div>`;
    const f = CHANGE ? document.getElementById("chg-" + CHANGE) : null;
    if (f) {
      const keep = () => { const fd = new FormData(f); CHANGE_DRAFT[CHANGE] = { barrier: fd.get("barrier"), preferred: fd.get("preferred"), note: fd.get("note") }; };
      f.oninput = f.onchange = keep;
      f.querySelector("[data-change-cancel]").onclick = () => { CHANGE = null; render(lang); };
      f.onsubmit = async ev => { ev.preventDefault(); keep(); W.formError(f, ""); const d = CHANGE_DRAFT[CHANGE];
        if (!d.barrier) { W.formError(f, T.err_barrier); f.querySelector("input[name=barrier]").focus(); return; }
        const b = f.querySelector("button[type=submit]"); W.busy(b, true);
        try { await W.api(`/api/me/appointments/${CHANGE}/change-request`, { method: "POST", body: { barrier_code: d.barrier, preferred_times: d.preferred || null, note: d.note || null } });
          delete CHANGE_DRAFT[CHANGE]; CHANGE = null; W.toast(T.change_done, "ok"); await load(lang); }
        catch (e) { W.busy(b, false); W.formError(f, e.message); } };
    }
  }

  // ---------------------------------------------------------------- messages
  function messagesBox(lang) {
    const box = document.getElementById("pmsgs"); if (!box || !P) return;
    const T = P.text, M = P.messages || [];
    const item = m => `<li class="hist-item msg-item" data-msg="${m.id}"><div class="small"><b>${W.esc(T.msg_kind[m.category] || T.msg_kind.other)}</b> · ${W.esc(when(m.at, lang))} · ${W.esc(T.via[m.channel] || m.channel)}
        ${m.simulated ? `<span class="chip sim-chip" title="${W.esc(T.msgs_note)}">${W.esc(T.msgs_sim)}</span>` : ""}</div>
      <div ${m.language ? `lang="${W.esc(m.language)}" dir="auto"` : ""}>${W.esc(m.kind === "call" ? T.msg_call : m.kind === "account" ? T.msg_account : m.text)}</div></li>`;
    box.innerHTML = `<div class="card" id="pmsgs-card"><h4>${W.esc(T.msgs_title)}</h4><p class="notice-inline info" role="note">${W.esc(T.msgs_note)}</p>
      ${M.length ? `<ul class="hist msgs">${M.slice(0, 8).map(item).join("")}</ul>${M.length > 8 ? `<details class="small"><summary>+${M.length - 8}</summary><ul class="hist msgs">${M.slice(8).map(item).join("")}</ul></details>` : ""}` : `<p class="small muted">${W.esc(T.no_msgs)}</p>`}</div>`;
  }

  // ---------------------------------------------------------------- contact details and consent
  function consentCard(T, lang) {
    const C = P.consent || {};
    const row = sc => { const c = C[sc] || {}, on = c.decision !== "deny";
      return `<li class="consent-row" data-scope="${sc}"><span class="c-label">${W.esc(T.scopes[sc])}</span>
        <span class="chip ${on ? "sla-green" : "sla-red"}">${W.esc(on ? T.allowed : T.not_allowed)}</span>${c.since ? `<span class="xs muted">${W.esc(fill(T.since, { date: day(c.since, lang) }))}</span>` : ""}
        <button type="button" class="secondary outline sm" data-consent="${sc}" data-to="${on ? "deny" : "permit"}">${W.esc(on ? T.stop : T.allow)}</button></li>`; };
    const cc = C.care_coordination || {}, ccOn = cc.decision !== "deny";
    return `<div class="card" id="p-consent"><h4>${W.esc(T.consent_title)}</h4><p class="small muted">${W.esc(T.consent_intro)}</p>
      <ul class="consent-list">${["comm_app", "comm_sms", "comm_whatsapp", "comm_phone"].map(row).join("")}</ul>
      <h4>${W.esc(T.care_title)}</h4><p class="small" data-care="${ccOn ? "permit" : "deny"}">${W.esc(ccOn ? T.care_on : T.care_off)}</p>
      <p class="small muted">${W.esc(T.duty)}</p>
      <div class="tb-actions"><button type="button" class="${ccOn ? "secondary outline" : ""} sm" data-consent="care_coordination" data-to="${ccOn ? "deny" : "permit"}">${W.esc(ccOn ? T.withdraw : T.give)}</button></div></div>`;
  }

  function detailsBox(lang, rtl) {
    const box = document.getElementById("pprofile"); if (!box || !P) return;
    const T = P.text, C = P.contact;
    const v = x => x ? W.esc(x) : `<span class="muted">${W.esc(T.not_given)}</span>`;
    const ltr = x => x ? `<span dir="ltr">${W.esc(x)}</span>` : v(x);
    const details = OPEN ? form(T, DRAFT || C, P.options) : `<dl class="facts self-facts">
        <div><dt>${W.esc(T.phone)}</dt><dd>${ltr(C.phone)}${C.phone && C.whatsapp ? ' <span class="chip">WhatsApp</span>' : ""}</dd></div>
        <div><dt>${W.esc(T.email)}</dt><dd>${ltr(C.email)}</dd></div>
        <div><dt>${W.esc(T.channel)}</dt><dd>${C.preferred_channel ? W.esc(T.channels[C.preferred_channel]) : W.esc(T.no_pref)}</dd></div>
        <div class="wide"><dt>${W.esc(T.address)}</dt><dd>${v([C.address_line, C.city, C.emirate, C.region && C.region !== C.emirate ? C.region : ""].filter(Boolean).join(", "))}</dd></div></dl>
        <div class="tb-actions"><button class="secondary" id="self-edit">${W.esc(T.edit)}</button></div>`;
    box.innerHTML = `<div class="card" id="self-details"><h4>${W.esc(T.details_title)}</h4>${details}</div>${consentCard(T, lang)}`;
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

  function wire(lang, rtl) {
    const T = P.text, dir = rtl ? "rtl" : "ltr";
    document.querySelectorAll("#p-root [data-confirm]").forEach(b => b.onclick = async () => { W.busy(b, true);
      try { await W.api(`/api/me/appointments/${b.dataset.confirm}/confirm`, { method: "POST", body: {} }); W.toast(T.confirm_done, "ok"); await load(lang, rtl); }
      catch (e) { W.busy(b, false); W.toast(e.message, "err"); } });
    document.querySelectorAll("#p-root [data-change]").forEach(b => b.onclick = () => { CHANGE = +b.dataset.change; render(lang, rtl);
      const f = document.getElementById("chg-" + CHANGE); if (f) { f.scrollIntoView({ block: "center" }); const i = f.querySelector("input"); if (i) i.focus(); } });
    document.querySelectorAll("#p-root [data-consent]").forEach(b => b.onclick = async () => {
      const scope = b.dataset.consent, to = b.dataset.to;
      if (scope === "care_coordination" && to === "deny" && !(await W.confirm(T.withdraw_q, W.esc(T.withdraw_body), { ok: T.withdraw, cancel: T.keep, danger: true, dir }))) return;
      W.busy(b, true);
      try { await W.api("/api/me/contact-consent", { method: "POST", body: { scope, decision: to } }); W.toast(T.consent_saved, "ok"); await load(lang, rtl); }
      catch (e) { W.busy(b, false); W.toast(e.message, "err"); } });
  }

  function render(lang, rtl = ["ar", "ur"].includes(lang)) { if (!P) return; loopBox(lang); historyBox(lang); messagesBox(lang); detailsBox(lang, rtl); wire(lang, rtl); }

  async function load(lang, rtl) {
    if (rtl === undefined) rtl = ["ar", "ur"].includes(lang);
    try { P = await W.api("/api/me/profile?lang=" + encodeURIComponent(lang)); render(lang, rtl); }
    catch (e) { const box = document.getElementById("pprofile"); if (box) box.innerHTML = `<div class="card notice">${W.esc(e.message)}</div>`; }
  }
  // called by patient.html after each render (language switch re-renders; open forms keep their drafts)
  window.renderProfile = d => load(d.language, d.rtl);
})();
