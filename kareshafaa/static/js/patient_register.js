// Register a patient: one form, server-side duplicate check, "create anyway" needs a written reason (audited).
const FROM_MSG = new URLSearchParams(location.search).get("from_message");
let PREFILL = null;
const REG_EXTRA = [{ title: "Insurance", html: v => PF.insurance(v), keys: PF.KEYS.insurance }, { title: "Next of kin and guardian (optional)", html: v => PF.contacts(v), keys: PF.KEYS.contacts }];  // {title, html(v), keys, collect?}

function regForm(v) {
  const O = PF.opts;
  return `<form id="regf" class="reg-form" novalidate aria-label="Register a patient">
    ${PREFILL ? `<div class="card info"><b>From an unmatched result</b> (message #${PREFILL.message_id}, ${W.prog(PREFILL.programme)}). The details below were read from the lab message – check each one with the patient or their documents. When you save, the result is filed to the new record and becomes a tracked case.${PREFILL.values.birth_year && !PREFILL.values.birth_date ? ` The message gives birth year <b>${PREFILL.values.birth_year}</b> only.` : ""}</div>` : ""}
    <section class="card"><h2 class="sec-title">Personal details</h2>${PF.demographics(v)}</section>
    <section class="card"><h2 class="sec-title">Contact details</h2>${PF.contact(v)}</section>
    <section class="card"><h2 class="sec-title">Health centre and family doctor</h2>${PF.care(v)}
      ${PF.inp("mrn", "Hospital / health-centre MRN (optional)", v.mrn, { hint: "Leave empty to create a KareShafaa number (KS-…).", attrs: 'maxlength="40" autocomplete="off"' })}</section>
    ${REG_EXTRA.map(x => `<section class="card"><h2 class="sec-title">${W.esc(x.title)}</h2>${x.html(v)}</section>`).join("")}
    <section class="card"><h2 class="sec-title">Consent to be contacted</h2>
      <p class="small">Ask: “Do you agree that the screening team may contact you about your results and help arrange your follow-up?”</p>
      ${PF.sel("consent_care_coordination", "Patient's answer", [["permit", "Yes – agrees to be contacted"], ["deny", "No – does not agree"]], v.consent_care_coordination, { blank: "Not asked yet" })}
      ${PF.inp("consent_note", "How was it given? (optional)", v.consent_note, { attrs: 'maxlength="500" placeholder="e.g. in person at the front desk, with an interpreter"' })}</section>
    <div id="dup"></div>
    <div class="form-buttons sticky-actions"><button type="submit" id="save">Register patient</button><a role="button" class="secondary" href="/patients">Cancel</a></div>
  </form>`;
}

function showDup(err, form) {
  const box = document.getElementById("dup"), d = err.data || {};
  if (err.code === "duplicate_emirates_id") {
    box.innerHTML = `<div class="card alert" role="alert"><h3>This Emirates ID is already registered</h3><p>${W.esc(err.message)}</p>${PF.dupList(d.candidates || [])}</div>`;
  } else if (err.code === "possible_duplicate") {
    box.innerHTML = `<div class="card notice" role="alert"><h3>Possible duplicate – please check</h3><p>${W.esc(err.message)}</p>${PF.dupList(d.candidates || [])}
      <label>Why is this a different person? (at least ${d.min_reason || 10} characters, recorded with your name)<textarea name="create_anyway_reason" rows="2" maxlength="500" placeholder="e.g. different Emirates ID and mother's name checked with the patient"></textarea></label>
      <button type="button" id="anyway" class="secondary">Create anyway</button></div>`;
    document.getElementById("anyway").onclick = () => submit(form, true);
  } else { box.innerHTML = ""; W.formError(form, err.message); return; }
  box.scrollIntoView({ block: "center", behavior: "smooth" });
}

async function submit(form, anyway = false) {
  W.formError(form, "");
  const bad = PF.check(form);
  if (bad.length) { W.formError(form, bad.join(" ")); form.querySelector('[aria-invalid="true"]')?.focus(); return; }
  if (!form.reportValidity()) return;
  const keys = [...PF.KEYS.demographics, ...PF.KEYS.contact, ...PF.KEYS.care, "mrn", "consent_care_coordination", "consent_note"];
  const body = PF.collect(form, keys);
  REG_EXTRA.forEach(x => Object.assign(body, x.collect ? x.collect(form) : PF.collect(form, x.keys)));
  if (anyway) { const r = form.querySelector('[name="create_anyway_reason"]'); body.create_anyway_reason = r ? r.value.trim() : null;
    if (!body.create_anyway_reason || body.create_anyway_reason.length < 10) { W.formError(document.getElementById("dup"), "Please write the reason (at least 10 characters)."); r && r.focus(); return; } }
  if (FROM_MSG) body.from_message_id = +FROM_MSG;
  const b = document.getElementById("save"); W.busy(b, true);
  try {
    const r = await W.api("/api/patients", { method: "POST", body });
    W.toast(`Patient registered (${r.mrn})` + (r.case_id ? ` – result filed, case #${r.case_id}` : ""), "ok");
    if (r.warnings && r.warnings.length) W.toast(r.warnings.join(" "), "");
    const q = new URLSearchParams({registered: "1"});
    if (r.signup_email) q.set("signup", r.signup_email);
    location.href = `/patients/${r.patient_id}?${q}`;
  } catch (e) { W.busy(b, false); if (e.status === 409) showDup(e, form); else W.formError(form, e.message); }
}

document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  const root = document.getElementById("reg-root");
  try {
    await PF.options();
    let v = { language: "ar", emirate: "Abu Dhabi", nationality: "", region: "" };
    if (FROM_MSG) { PREFILL = await W.api("/api/patients/prefill?message_id=" + encodeURIComponent(FROM_MSG)); v = { ...v, ...PREFILL.values }; }
    root.innerHTML = regForm(v);
    const f = document.getElementById("regf"); PF.wire(f);
    f.onsubmit = ev => { ev.preventDefault(); submit(f); };
    f.querySelector("input").focus();
  } catch (e) { root.innerHTML = W.empty("Could not open the form", W.esc(e.message)); }
});
