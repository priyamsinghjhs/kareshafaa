// Patient record (staff): details with in-place editing, cases, results, consents and the change history.
const PID = document.getElementById("pr-root").dataset.patient;
let R = null;
const OPEN = new Set();          // edit forms the user opened survive a re-render
const DRAFT = {};                // values typed into a form survive a failed save / re-render
const pr = () => document.getElementById("pr-root");
const NR = '<span class="muted">Not recorded</span>';
const v = x => (x === null || x === undefined || x === "") ? NR : W.esc(x);
const dday = s => s ? new Date(s.length <= 10 ? s + "T12:00:00" : (/Z|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z")).toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : NR;
const EXTRA_SECTIONS = [];      // later sections register here: {id, render(R) -> html, wire?(R)}
let SHOWN_CODE = null;          // a new activation code is shown once, until the page is left

const ACCOUNT_STATE = { none: ["No app account yet", ""], invited: ["Invited – waiting for the patient to activate", "sla-amber"],
  invite_expired: ["Activation code expired – send a new one", "sla-red"], active: ["Active", "sla-green"], inactive: ["Deactivated by an administrator", "sla-none"] };
function accountSection(R) {
  const A = R.account, [label, cls] = ACCOUNT_STATE[A.state] || [A.state, ""], C = R.contact;
  const isReset = SHOWN_CODE && SHOWN_CODE.purpose === "reset";
  const code = SHOWN_CODE ? `<div class="card ok" role="status" id="new-code"><b>${isReset ? "Password reset code" : "Activation code"} (shown once):</b>
      <p class="code-big" dir="ltr">${W.esc(SHOWN_CODE.code)}</p>
      <p class="small">Username <b dir="ltr">${W.esc(SHOWN_CODE.username)}</b> · expires ${W.dt(SHOWN_CODE.expires_at)} · single use.
      ${SHOWN_CODE.channel === "sms" ? "Sent by simulated SMS (code masked in the stored message)." : "Give it to the patient in person; they open the address below, enter the code and their date of birth, and choose a password."}${isReset ? " Their other sessions end when they set the new password." : ""}</p>
      <p class="small">${isReset ? "Reset page" : "Activation page"}: <a href="${W.esc(SHOWN_CODE.link)}" target="_blank" rel="noopener noreferrer">${W.esc(SHOWN_CODE.link.split("?")[0])}</a> ${SHOWN_CODE.channel === "sms" ? " (the SMS holds the full personal link)" : ""}</p>
      <div class="row"><button class="secondary sm" id="copy-code">Copy code</button><button class="ghost sm" id="hide-code">Done</button></div></div>` : "";
  const inv = A.invite ? `<p class="small">Open code …${W.esc(A.invite.code_hint.slice(-4))} created ${W.dt(A.invite.created_at)} by ${W.esc(A.invite.created_by)} (${A.invite.channel === "sms" ? "SMS" : "in person"}), expires ${W.dt(A.invite.expires_at)}.</p>` : "";
  const last = !A.invite && A.last_invite && A.last_invite.revoked_at ? `<p class="small muted">Last code cancelled ${W.dt(A.last_invite.revoked_at)}${A.last_invite.revoke_reason ? ": " + W.esc(A.last_invite.revoke_reason) : ""}.</p>` : "";
  const can = A.can_invite && A.state !== "inactive" && (A.state !== "active" || A.can_reset);
  const RR = A.reset_request;
  const rr = RR ? `<div class="notice-inline warn" role="status" id="reset-request"><b>Password reset requested</b> from the sign-in page ${W.dt(RR.created_at)} (the patient's date of birth, ID ending and mobile matched).
      ${RR.due_at ? `Reply due ${W.ago(RR.due_at)}.` : ""} ${A.can_reset ? "Check who you are speaking to, then send a reset code below." : "A care coordinator handles it."}</div>` : "";
  const verify = A.state === "active" && A.can_reset ? `<label for="verify-method">How did you check who the patient is?<select id="verify-method" name="verify_method" required>
        <option value="">Choose…</option><option value="in_person_id">Seen in person with photo ID</option>
        <option value="phone_call_checks">Phone call to the registered mobile: date of birth, ID number and address checked</option>
        <option value="video_call_id">Video call with photo ID</option></select></label>` : "";
  const smsOk = C.phone && C.phone_valid;
  const btnText = A.state === "none" ? "Create account and send code" : A.state === "active" ? "Send a new code (forgotten password)" : "Send a new code";
  return `<section class="card pr-sec" id="sec-account"><h2 class="sec-title">Patient app account</h2>
    ${facts([["Status", `<span class="chip ${cls}">${W.esc(label)}</span>`], ["Username", A.username ? `<span dir="ltr">${W.esc(A.username)}</span>` : NR],
      ["Activated", A.activated_at ? W.dt(A.activated_at) : '<span class="muted">Not yet</span>'], ["Last sign-in", A.last_login_at ? W.dt(A.last_login_at) : '<span class="muted">Never</span>']])}
    ${rr}${code}${inv}${last}
    ${can ? `<form class="action-form" id="invite-form" aria-label="Patient app account" novalidate>
      <fieldset class="checks" style="border:0;padding:0;margin:0 0 var(--s-2)"><legend class="small"><b>How should the patient get the code?</b></legend>
        <label class="check"><input type="radio" name="channel" value="sms" ${smsOk ? "checked" : "disabled"}> By SMS to ${smsOk ? W.esc(C.phone) : "(no valid mobile number)"}</label>
        <label class="check"><input type="radio" name="channel" value="in_person" ${smsOk ? "" : "checked"}> I will give it in person</label></fieldset>
      ${verify}
      ${R.demographics.birth_date ? "" : `<p class="notice-inline err small">Add the full date of birth first – the patient confirms it when activating.</p>`}
      <div class="form-buttons"><button type="submit" ${R.demographics.birth_date ? "" : "disabled"}>${btnText}</button>${A.invite ? `<button type="button" class="secondary" id="revoke-code">Cancel the open code</button>` : ""}${RR && A.can_reset ? `<button type="button" class="secondary" id="close-reset">Close the request without a code</button>` : ""}</div></form>` :
      (A.state === "inactive" ? `<p class="small muted">An administrator can reactivate this account (Admin → Users).</p>`
        : A.state === "active" && A.can_invite ? `<p class="small muted" id="reset-coord-only">Password resets are done by care coordinators, after checking who the patient is.</p>` : "")}
    ${A.invite_messages.length ? `<details class="small"><summary>Messages sent (${A.invite_messages.length})</summary>${A.invite_messages.map(m => `<p class="msg"><span class="muted">${W.dt(m.at)} · ${W.esc(m.channel)}</span><br>${W.esc(m.content)}</p>`).join("")}</details>` : ""}
  </section>`;
}
function wireAccount() {
  const f = document.getElementById("invite-form");
  if (f) f.onsubmit = async ev => { ev.preventDefault(); W.formError(f, "");
    const ch = (f.querySelector("input[name=channel]:checked") || {}).value;
    if (!ch) { W.formError(f, "Choose how the patient gets the code."); return; }
    const vm = f.querySelector("#verify-method"); if (vm) vm.removeAttribute("aria-invalid");
    if (vm && !vm.value) { vm.setAttribute("aria-invalid", "true"); W.formError(f, "Say how you checked who the patient is before sending a reset code."); vm.focus(); return; }
    const b = f.querySelector("button[type=submit]"); W.busy(b, true);
    try { const r = await W.api(`/api/patients/${PID}/account-invite`, { method: "POST", body: { channel: ch, verify_method: vm ? vm.value : null } });
      SHOWN_CODE = r; W.toast(ch === "sms" ? "Account ready – code sent (simulated SMS)" : "Account ready – give the code to the patient", "ok"); await load();
      document.getElementById("new-code")?.scrollIntoView({ block: "center" }); }
    catch (e) { W.busy(b, false); W.formError(f, e.message); } };
  const rv = document.getElementById("revoke-code");
  if (rv) rv.onclick = async () => { const why = await W.ask("Cancel the open activation code?", "Reason", { ok: "Cancel the code", min: 5, danger: true, placeholder: "e.g. sent to the wrong number" });
    if (!why) return;
    try { await W.api(`/api/patients/${PID}/account-invite/revoke`, { method: "POST", body: { reason: why } }); SHOWN_CODE = null; W.toast("Code cancelled", "ok"); await load(); }
    catch (e) { W.toast(e.message, "err"); } };
  const cr = document.getElementById("close-reset");
  if (cr) cr.onclick = async () => { const why = await W.ask("Close the password reset request?", "What happened (kept in the audit trail)", { ok: "Close the request", min: 5, placeholder: "e.g. could not confirm who the caller was; asked them to come in with ID" });
    if (!why) return;
    try { await W.api(`/api/signin-help/${R.account.reset_request.id}/close`, { method: "POST", body: { outcome: "closed", note: why } }); W.toast("Request closed", "ok"); await load(); }
    catch (e) { W.toast(e.message, "err"); } };
  const cp = document.getElementById("copy-code");
  if (cp) cp.onclick = () => navigator.clipboard && navigator.clipboard.writeText(SHOWN_CODE.code).then(() => W.toast("Copied", "ok"));
  const hd = document.getElementById("hide-code");
  if (hd) hd.onclick = () => { SHOWN_CODE = null; render(); };
}

function facts(pairs) { return `<dl class="facts">${pairs.map(([k, x, wide]) => `<div${wide ? ' class="wide"' : ""}><dt>${W.esc(k)}</dt><dd>${x}</dd></div>`).join("")}</dl>`; }

function editable(id, title, view, formHtml) {
  const open = OPEN.has(id) && R.can_write;
  return `<section class="card pr-sec" id="sec-${id}"><div class="row between"><h2 class="sec-title" style="margin:0">${W.esc(title)}</h2>${R.can_write ? `<button class="secondary sm" data-edit="${id}" aria-expanded="${open}">${open ? "Close" : "Edit"}</button>` : ""}</div>
    ${open ? `<form class="action-form" data-form="${id}" novalidate aria-label="Edit ${W.esc(title)}">${formHtml}<div class="form-buttons"><button type="submit">Save changes</button><button type="button" class="secondary" data-cancel="${id}">Cancel</button></div></form>` : view}</section>`;
}

function render() {
  const D = R.demographics, C = R.contact, K = R.care, O = PF.opts;
  const gender = PF.label(O.genders, D.gender), lang = PF.label(O.languages, D.language);
  const I = R.insurance || {}, N = R.contacts || {};
  const draft = id => ({ ...({ demographics: D, contact: C, care: K, insurance: I, contacts: N }[id]), ...(DRAFT[id] || {}) });
  const head = `<a class="back-link" href="/patients"><svg class="icon" aria-hidden="true"><use href="#i-back"/></svg>Back to patients</a>
    <div class="case-head"><h1>${W.esc(D.given_name)} ${W.esc(D.family_name)}</h1>
    <p class="muted small" style="margin:0">${W.esc(R.mrn)} · ${D.age_estimated ? "about " : ""}${D.age} years · ${W.esc(gender)}${R.sample ? ' · <span class="chip">Sample record</span>' : ""}${R.deceased ? ' · <span class="chip sla-none">Deceased</span>' : ""}</p></div>
    ${new URLSearchParams(location.search).get("registered") ? `<div class="card ok" role="status">Patient registered. Next: invite them to the patient app, or add a screening result.</div>` : ""}`;
  const personal = editable("demographics", "Personal details", facts([["Name", W.esc(D.given_name + " " + D.family_name)], ["Date of birth", D.birth_date ? dday(D.birth_date) : (D.birth_year ? W.esc(D.birth_year) + " (year only)" : NR)],
      ["Sex", W.esc(gender)], ["Emirates ID", v(D.national_id)], ...(D.other_document ? [[D.other_document.label, `<span dir="ltr">${W.esc(D.other_document.number)}</span>`]] : []), ["Nationality", v(D.nationality || D.nationality_group)], ["Preferred language", W.esc(lang)]]), PF.demographics(draft("demographics")));
  const chan = [C.has_smartphone ? "App" : "", C.whatsapp ? "WhatsApp" : "", C.phone_valid ? "SMS / phone" : ""].filter(Boolean).join(", ");
  const contact = editable("contact", "Contact details", facts([["Mobile phone", v(C.phone)], ["Email", v(C.email)], ["Preferred contact", C.preferred_channel ? W.esc(PF.label(O.channels, C.preferred_channel)) : '<span class="muted">No preference</span>'],
      ["Can be reached by", chan ? W.esc(chan) : '<b>No valid phone or app</b>'], ["Address", v([C.address_line, C.city].filter(Boolean).join(", ")), true], ["Emirate / region", v([C.emirate, C.region && C.region !== C.emirate ? C.region : ""].filter(Boolean).join(" · "))]]), PF.contact(draft("contact")));
  const care = editable("care", "Health centre and family doctor", facts([["Registered health centre", v(K.managing_org)], ["Family doctor (GP)", v(K.gp)],
      ["Registered", R.registration.at ? `${dday(R.registration.at)}<span class="sub small muted">by ${W.esc(R.registration.by || "–")}${R.registration.source === "result_import" ? " · from a lab result" : ""}</span>` : '<span class="muted">Before registration was recorded</span>'],
      ...(R.registration.duplicate_override_reason ? [["Registered despite a similar record", W.esc(R.registration.duplicate_override_reason), true]] : [])]), PF.care(draft("care")));
  const E = I.eligibility || { status: "missing", warn: true, message: "" };
  // Yes -> the full policy; No (self-pay) or not answered -> one compact line, no empty insurance fields
  const HI = PF.hasIns(I), warnBox = E.warn ? `<div class="card notice cov-warn" role="status" data-eligibility="${W.esc(E.status)}"><span class="shape" aria-hidden="true">!</span> ${W.esc(E.message)}</div>` : `<p class="small"><span class="chip sla-green" data-eligibility="ok">✓ ${W.esc(E.message)}</span></p>`;
  const insView = HI === true ? warnBox + facts([["Has insurance", "Yes"], ["Insurer", v(I.payer_label)], ["Plan / network", v(I.insurance_plan)], ["Member / card number", I.insurance_member_id ? `<span dir="ltr">${W.esc(I.insurance_member_id)}</span>${I.member_masked ? '<span class="sub small muted">Last 4 shown for your role</span>' : ""}` : NR], ["Cover ends on", I.insurance_valid_to ? dday(I.insurance_valid_to) : NR],
      ["Coverage status", W.esc(({ active: "Active", expiring: "Active, ends soon", expired: "Expired", unknown: "Unknown" })[I.coverage_status] || "Unknown")],
      ["Pre-authorisation needed", I.insurance_preauth_required === true ? "Yes" : I.insurance_preauth_required === false ? "No" : NR]])
    : HI === false ? `<p data-insurance-state="no"><b>No insurance</b> <span class="chip sla-amber">Self-pay</span><span class="sub small muted">Discuss costs before a referral or appointment.</span></p>`
    : `<p data-insurance-state="not_recorded"><span class="muted">Insurance not recorded</span><span class="sub small">Ask the patient whether they have insurance before booking a referral or appointment.</span></p>`;
  const insurance = editable("insurance", "Insurance", insView, PF.insurance(draft("insurance")));
  const PCX = { permit: '<span class="chip sla-green">✓ Patient agrees</span>', deny: '<span class="chip sla-red">✕ Patient does not agree</span>' };
  const kin = editable("contacts", "Next of kin and guardian", facts([["Name", v(N.nok_name)], ["Relationship", v(N.relationship_label)], ["Phone", v(N.nok_phone)], ["Legal guardian", N.nok_name ? (N.nok_is_guardian ? "Yes" : "No") : NR],
      ["May discuss care with them (proxy consent)", PCX[N.proxy_consent] || '<span class="muted">Not asked yet</span>', true], ...(N.proxy_note ? [["Consent note", W.esc(N.proxy_note), true]] : [])]), PF.contacts(draft("contacts")));
  const cases = `<section class="card"><h2 class="sec-title">Screening cases</h2>${R.cases.length ? `<table class="compact"><thead><tr><th>Case</th><th>Programme</th><th>Step</th><th>Opened</th><th>Closed</th></tr></thead><tbody>${R.cases.map(c => `<tr><td><a href="/cases/${c.id}">Case #${c.id}</a></td><td>${W.prog(c.programme)}</td><td>${W.esc(c.state_label)}</td><td>${dday(c.created_at)}</td><td>${c.closed_at ? dday(c.closed_at) + (c.closure_reason ? `<span class="sub small muted">${W.esc(c.closure_reason.replace(/_/g, " "))}</span>` : "") : "–"}</td></tr>`).join("")}</tbody></table>` : (R.other_care_episodes ? "" : `<p class="small muted">No screening cases yet. A case opens automatically when a result needs follow-up.</p>`)}${R.other_care_episodes ? `<p class="small muted" data-other-episodes>${W.esc(R.other_care_episodes_note)}</p>` : ""}</section>`;
  const results = `<section class="card"><h2 class="sec-title">Results</h2>${R.results.length ? `<table class="compact"><thead><tr><th>Date</th><th>Test</th><th>Result</th><th>Conclusion</th><th>Case</th></tr></thead><tbody>${R.results.map(r => `<tr><td>${dday(r.issued_at)}</td><td>${W.esc(r.display)}${r.ordered_by || r.site ? `<span class="sub small muted">${[r.ordered_by ? "Ordered by " + W.esc(r.ordered_by) : "", r.site ? "at " + W.esc(r.site) : "", r.collected_at ? "collected " + dday(r.collected_at) : ""].filter(Boolean).join(" · ")}</span>` : ""}</td><td>${v(r.values)}</td><td><span class="chip ${r.conclusion === "negative" ? "sla-green" : "sla-amber"}">${W.esc(r.conclusion.replace(/_/g, " "))}</span></td><td>${r.case_id ? `<a href="/cases/${r.case_id}">#${r.case_id}</a>` : "–"}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted">No results on this record yet.</p>`}</section>`;
  const RS = { due: "Due", invited: "Invited", completed: "Screened", cancelled: "Cancelled", superseded: "Replaced" };
  const recalls = `<section class="card"><h2 class="sec-title">Next screening (recall)</h2>${(R.recalls || []).length ? `<table class="compact"><thead><tr><th>Programme</th><th>Due</th><th>Why</th><th>Status</th></tr></thead><tbody>${R.recalls.map(r => `<tr><td>${W.prog(r.programme)}</td><td class="nowrap">${dday(r.due_on)}</td><td class="small">${W.esc(r.reason_text)}</td><td><span class="chip ${["due", "invited"].includes(r.status) ? "sla-amber" : r.status === "completed" ? "sla-green" : "sla-none"}">${RS[r.status] || W.esc(r.status)}</span>${r.invited_at ? `<span class="sub small muted">invited ${W.dt(r.invited_at)}</span>` : ""}${r.closed_note && r.status === "cancelled" ? `<span class="sub small muted">${W.esc(r.closed_note)}</span>` : ""}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted">No recall yet. One is set after a negative result or when a case closes.</p>`}</section>`;
  const consents = `<section class="card"><h2 class="sec-title">Consent</h2>${R.consents.length ? `<p class="small">${R.consents.map(c => `<span class="chip ${c.decision === "permit" ? "sla-green" : "sla-red"}">${c.decision === "permit" ? "✓" : "✕"} ${W.esc(c.scope.replace(/_/g, " "))}</span>`).join(" ")}</p>` : `<p class="small muted">No consent decisions recorded yet.</p>`}</section>`;
  const changes = `<section class="card flat"><h2 class="sec-title">Changes to this record</h2>${R.changes.length ? `<ul class="timeline">${R.changes.map(c => `<li><span class="small muted">${W.dt(c.at)}</span> <b>${W.esc(c.summary)}</b> by ${W.esc(c.by)}${c.role === "patient" ? ' <span class="chip sla-amber">by the patient</span>' : ""}${c.fields.length ? `<div class="small muted">Fields: ${W.esc(c.fields.join(", ").replace(/_/g, " "))}</div>` : ""}</li>`).join("")}</ul>` : `<p class="small muted">No changes recorded.</p>`}</section>`;
  const extra = EXTRA_SECTIONS.map(s => s.render(R)).join("");
  pr().innerHTML = head + `<div class="grid-2 pr-grid"><div>${personal}${contact}${insurance}${kin}${care}${extra}${R.account ? accountSection(R) : ""}</div><div>${cases}${results}${recalls}${consents}${changes}</div></div>`;
  wire();
}

function wire() {
  pr().querySelectorAll("[data-edit]").forEach(b => b.onclick = () => { const id = b.dataset.edit; OPEN.has(id) ? (OPEN.delete(id), delete DRAFT[id]) : OPEN.add(id); render();
    const f = pr().querySelector(`[data-form="${id}"]`); if (f) f.querySelector("input, select").focus(); });
  pr().querySelectorAll("[data-cancel]").forEach(b => b.onclick = () => { OPEN.delete(b.dataset.cancel); delete DRAFT[b.dataset.cancel]; render(); });
  pr().querySelectorAll("form[data-form]").forEach(f => { PF.wire(f); const id = f.dataset.form;
    f.onsubmit = async ev => { ev.preventDefault(); W.formError(f, "");
      const bad = PF.check(f); if (bad.length) { W.formError(f, bad.join(" ")); return; }
      if (!f.reportValidity()) return;
      const body = PF.collect(f, PF.KEYS[id] || []); DRAFT[id] = body;
      const b = f.querySelector("button[type=submit]"); W.busy(b, true);
      try { const r = await W.api(`/api/patients/${PID}/${id}`, { method: "POST", body });
        OPEN.delete(id); delete DRAFT[id]; W.toast(r.changed.length ? "Saved" : "Nothing changed", "ok"); (r.warnings || []).forEach(w => W.toast(w, ""));
        await load(); }
      catch (e) { W.busy(b, false); W.formError(f, e.message + (e.data && e.data.candidates ? " " + e.data.candidates.map(c => `${c.name} (${c.mrn})`).join(", ") : "")); } }; });
  EXTRA_SECTIONS.forEach(s => s.wire && s.wire(R));
  wireAccount();
}

async function load() { R = await W.api(`/api/patients/${PID}`);
  // #edit-contact / #edit-insurance (links from the case summary) open that section's form straight away
  const m = !load.done && location.hash.match(/^#edit-(contact|insurance)$/);
  if (m && R.can_write) { OPEN.add(m[1]); render(); const f = pr().querySelector(`[data-form="${m[1]}"]`);
    if (f) { f.closest("section").scrollIntoView({ block: "start" }); (f.querySelector(m[1] === "contact" ? '[name="email"]' : "input, select")).focus(); }
    load.done = true; return; }
  render();
  if (!load.done && /^#[a-z][\w-]*$/.test(location.hash)) { const el = document.querySelector(location.hash); if (el) el.scrollIntoView({ block: "start" }); }
  load.done = true; }
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  try { await PF.options(); await load(); } catch (e) { pr().innerHTML = W.empty("Could not open this record", W.esc(e.message), '<a role="button" href="/patients">Back to patients</a>'); } });
