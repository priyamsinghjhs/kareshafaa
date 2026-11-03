// Case timeline + context-aware actions.
const CASE_ID = document.getElementById("case-root").dataset.case;
let C = null, META = null, REC = null, DIR = { prac: null, orgs: null };
const REC_OPEN = new Set();  // record sections the user opened/closed survive a re-render
const root = () => document.getElementById("case-root");

async function dir() {
  if (!DIR.prac) [DIR.prac, DIR.orgs] = await Promise.all([W.api("/api/directory/practitioners"), W.api("/api/directory/organizations")]).then(r => [r[0].items, r[1].items]);
  return DIR;
}
const opts = (arr, v = x => x.id, l = x => x.name) => arr.map(x => `<option value="${W.esc(v(x))}">${W.esc(l(x))}</option>`).join("");
const kv = obj => Object.entries(obj).map(([k, v]) => `<option value="${k}">${W.esc(v)}</option>`).join("");

async function load() {
  [C, META, REC] = await Promise.all([W.api(`/api/cases/${CASE_ID}`), W.api(`/api/cases/${CASE_ID}/actions`),
    W.api(`/api/cases/${CASE_ID}/record`).catch(e => ({ error: e.message }))]);
  render();
}


const RISKY = { close_evidence: "Close this case?", close_authorised: "Sign off this closure?", patient_deceased: "Record this patient as deceased?",
  patient_deceased_revoke: "Correct the death record?", decline: "Record that the patient declined?", reopen: "Re-open this case?",
  unreachable: "Mark the patient as unreachable?", transfer: "Request a transfer of ownership?", decide_transfer: "Record your transfer decision?",
  decide_referral: "Record this referral decision?" };

function header() {
  const owner = `${C.owner_is_pool ? '<span class="chip pool">Team pool – needs a named owner</span> ' : ""}${W.esc(C.owner)}`;
  return `<a class="back-link" href="/queue"><svg class="icon" aria-hidden="true"><use href="#i-back"/></svg>Back to the queue</a>
   <div class="case-head"><h1>${W.esc(C.patient.name)}</h1>
   <p class="muted small" style="margin:0">Case #${C.id} · ${W.esc(C.patient.mrn)} · ${W.prog(C.programme)} screening</p>
   <div class="case-meta">${W.tier(C.tier)} ${W.sla(C.sla.status, C.sla.fraction)} ${C.sla.escalation_level ? `<span class="chip sla-red">Escalated (level ${C.sla.escalation_level})</span>` : ""}</div></div>
   ${coverage()}${record()}
   <h2 class="sec-title" id="step-h">Current step on the care pathway</h2>
   <dl class="facts" aria-labelledby="step-h">
     <div><dt>Current step</dt><dd>${W.esc(C.state_label)}</dd></div>
     <div><dt>Accountable owner</dt><dd>${owner}</dd></div>
     <div><dt>Step deadline</dt><dd>${deadline()}</dd></div>
     <div><dt>Urgency</dt><dd class="small">${W.esc(W.tierText(C.tier))}</dd></div>
   </dl>
   <p class="small muted stepper-count">Step ${Math.max(1, C.journey.findIndex(j => j.status === "current") + 1)} of ${C.journey.filter(j => j.status !== "side").length} on the care pathway</p>
   <ol class="stepper" aria-label="Care pathway progress">${C.journey.map(j => `<li class="st ${j.status}" ${j.status === "current" ? 'aria-current="step"' : ""}>${W.esc(j.label)}</li>`).join("")}</ol>
   ${C.review_required ? `<div class="card alert" role="alert"><h3>Clinical review needed</h3><p>This result cannot move on automatically until a clinician reviews it: ${(C.review_reasons || []).map(x => `<span class="chip sla-red">${W.esc(x.replace(/_/g, " "))}</span>`).join(" ")}</p><p class="small" style="margin:0">Rule ${W.esc(C.rule_id || "–")} · pathway <b>${W.esc((C.pathway || "–").replace(/_/g, " "))}</b>${C.target ? " · target " + W.esc(C.target) : ""}. Urgency was set to the safer level; lowering it needs a clinician's reason (recorded).</p></div>` : (C.rule_id ? `<p class="small muted">Clinical rule ${W.esc(C.rule_id)} · pathway ${W.esc((C.pathway || "").replace(/_/g, " "))}${C.target ? " · target " + W.esc(C.target) : ""}${C.target_due_at ? ` · pathway target date <b>${W.dt(C.target_due_at)}</b>` : ""}</p>` : "")}
   ${C.transfer_pending ? `<div class="card notice">Transfer waiting for <b>${W.esc(C.transfer_pending.to_owner)}</b> to accept. The current owner stays accountable until then. <span class="small muted">${W.esc(C.transfer_pending.note || "")}</span></div>` : ""}
   ${C.closure ? `<div class="card ok"><b>Closed</b> (${W.esc(C.closure.type.replace(/_/g, " "))}: <b>${W.esc(C.closure.reason)}</b>) on ${W.dt(C.closure.closed_at)}. ${W.esc(C.closure.note || "")} ${C.closure.evidence_id ? `· evidence #${C.closure.evidence_id}` : ""}
     <br><span class="small">${C.recall && C.recall.next_screening_due ? `Next screening due <b>${W.esc(C.recall.next_screening_due)}</b>${C.recall.recall ? ` (recall ${W.esc(C.recall.recall.status)})` : ""}.` : "No automatic recall for this closure (follow-up set by the treating team, or the person left the programme)."}</span></div>` : ""}`;
}

function coverage() {
  const V = C.coverage; if (!V) return "";
  const e = V.eligibility, k = V.nok;
  const nok = k ? `Next of kin: <b>${W.esc(k.name)}</b>${k.relationship ? ` (${W.esc(k.relationship)})` : ""}${k.guardian ? ' <span class="chip sla-amber">Guardian</span>' : ""}${k.phone ? " · " + W.esc(k.phone) : ""} · ${k.proxy_consent === "permit" ? '<span class="chip sla-green">May be contacted about care</span>' : k.proxy_consent === "deny" ? '<span class="chip sla-red">Do not discuss care</span>' : '<span class="chip">Proxy consent not asked</span>'}` : '<span class="muted">No next of kin recorded</span>';
  return `<div class="card ${e.warn ? "notice" : "flat"} cov-banner" data-coverage="${W.esc(e.status)}"><p class="small" style="margin:0">${e.warn ? '<span class="shape" aria-hidden="true">!</span> ' : ""}<b>${W.esc(V.payer || "Insurance")}</b>: ${W.esc(e.message)} <a href="/patients/${C.patient.id}#sec-insurance">Update</a></p><p class="small" style="margin:.25rem 0 0">${nok}</p></div>`;
}

function deadline() {
  const d = C.sla.due_at; if (!d) return "No deadline";
  const t = new Date(/Z|[+-]\d\d:?\d\d$/.test(d) ? d : d + "Z"), ms = t - Date.now(), days = Math.abs(ms) / 864e5;
  const span = days < 1 ? Math.max(1, Math.round(Math.abs(ms) / 36e5)) + " h" : Math.round(days) + (Math.round(days) === 1 ? " day" : " days");
  return `${W.dt(d)} <span class="small ${ms < 0 ? "overdue-text" : "muted"}">(${ms < 0 ? "overdue by " + span : "due in " + span})</span>`;
}

// ---------------------------------------------------------------- clinical record (doctor view)
const NR = () => `<span class="muted">${W.esc((REC && REC.none) || "None recorded")}</span>`;
const val = v => (v === null || v === undefined || v === "") ? NR() : W.esc(v);
const day = s => s ? new Date(/Z|[+-]\d\d:?\d\d$/.test(s) ? s : s + "Z").toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : NR();
const flagChip = (abn, txt) => abn ? `<span class="chip sla-red"><span class="shape" aria-hidden="true">▲</span>${W.esc(txt || "Abnormal")}</span>` : `<span class="chip sla-green"><span class="shape" aria-hidden="true">✓</span>${W.esc(txt || "Normal")}</span>`;
const tbl = (heads, rows) => rows.length ? `<div class="table-scroll"><table class="compact rec-table"><thead><tr>${heads.map(h => `<th scope="col">${h}</th>`).join("")}</tr></thead><tbody>${rows.join("")}</tbody></table></div>` : `<p class="small">${NR()}</p>`;
function sec(id, title, count, body, defOpen) {
  const open = REC_OPEN.has(id) ? true : REC_OPEN.has("!" + id) ? false : defOpen && matchMedia("(min-width: 900px)").matches;
  return `<details class="card rec-sec" data-sec="${id}" ${open ? "open" : ""}><summary><h3>${title}</h3><span class="count">${count}</span></summary><div class="rec-body">${body}</div></details>`;
}
function record() {
  if (!REC) return "";
  if (REC.error) return `<div class="card notice">The clinical record could not be loaded: ${W.esc(REC.error)}</div>`;
  const R = REC, S = R.summary, w = R.can_write;
  const alg = S.allergies.length ? S.allergies.map(a => a.nkda ? `<span class="chip sla-green">${W.esc(a.substance)}</span>` : `<span class="chip sla-red" title="${W.esc([a.reaction, a.severity].filter(Boolean).join(" · "))}"><span class="shape" aria-hidden="true">!</span>${W.esc(a.substance)}${a.reaction ? " – " + W.esc(a.reaction) : ""}</span>`).join(" ") : NR();
  const cond = S.conditions.length ? S.conditions.map(c => `<span class="chip ${c.status === "active" ? "sla-none" : ""}">${W.esc(c.display)}${c.status && c.status !== "active" ? " (" + W.esc(c.status) + ")" : ""}</span>`).join(" ") : NR();
  const summary = `<section class="card rec-summary" aria-labelledby="rs-h"><h3 id="rs-h">Patient summary</h3><dl class="facts rec-facts">
     <div><dt>Name</dt><dd>${W.esc(S.name)}</dd></div><div><dt>MRN</dt><dd>${W.esc(S.mrn)}</dd></div>
     <div><dt>Age / sex</dt><dd>${S.age} · ${W.esc(S.sex)}</dd></div><div><dt>Language</dt><dd>${val(S.language)}</dd></div>
     <div><dt>Contact preference</dt><dd>${val(S.contact_preference)}</dd></div><div><dt>Family doctor</dt><dd>${val(S.gp)}<span class="sub small muted">${W.esc(S.managing_org || "")}</span></dd></div>
     <div class="wide"><dt>Allergies</dt><dd>${alg}</dd></div><div class="wide"><dt>Key conditions</dt><dd>${cond}</dd></div></dl>
     ${S.deceased ? `<p class="notice-inline err">Recorded as deceased${S.deceased_record && S.deceased_record.by ? ` by ${W.esc(S.deceased_record.by)} on ${W.dt(S.deceased_record.at)}` : ""}.${S.deceased_record && S.deceased_record.note ? ` Source: ${W.esc(S.deceased_record.note)}` : ""}</p>` : ""}
     ${S.death_corrected ? `<p class="notice-inline info">A death recorded in error was corrected by ${W.esc(S.death_corrected.by || "–")} on ${W.dt(S.death_corrected.at)}: ${W.esc(S.death_corrected.note || "")}</p>` : ""}</section>`;
  const tests = tbl(["Date", "Test", "Result", "Flag", "Source"], R.tests.map(t => `<tr class="${t.triggering ? "trigger" : ""}">
     <td>${day(t.date)}</td><td><b>${W.esc(t.name)}</b>${t.triggering ? ` <span class="chip sla-amber">Result that opened this case</span>` : ""}<span class="sub">${W.esc(t.category === "investigation" ? "Investigation" : t.category === "confirmatory" ? "Confirmatory test" : "Screening test")}</span></td>
     <td>${val(t.result)}${t.reference_range ? `<span class="sub">Reference ${W.esc(t.reference_range)}</span>` : ""}</td><td>${flagChip(t.abnormal, t.interpretation && t.interpretation.charAt(0).toUpperCase() + t.interpretation.slice(1))}</td>
     <td>${val(t.source)}<span class="sub">${t.ordered_by ? "Ordered by: " + W.esc(t.ordered_by) : W.esc(t.record_source || "")}</span>${t.screening_site ? `<span class="sub">Screened at: ${W.esc(t.screening_site)}</span>` : ""}${t.collected_at ? `<span class="sub">Specimen taken: ${W.dt(t.collected_at)}</span>` : ""}</td></tr>`));
  const encs = tbl(["Date", "Type", "Clinician", "Reason", "Notes / outcome"], R.encounters.map(e => `<tr>
     <td>${day(e.date)}</td><td><b>${W.esc(e.type)}</b>${e.org ? `<span class="sub">${W.esc(e.org)}</span>` : ""}</td><td>${val(e.clinician)}${e.role ? `<span class="sub">${W.esc(e.role)}</span>` : ""}</td>
     <td>${val(e.reason)}</td><td>${e.notes ? `<div class="note">${W.esc(e.notes)}</div>` : ""}${e.outcome ? `<div><b>Outcome:</b> ${W.esc(e.outcome)}</div>` : (e.notes ? "" : NR())}</td></tr>`));
  const meds = tbl(["Medicine", "Dose", "Frequency", "Start", "Stop", "Prescriber", "Status"], R.medications.map(m => `<tr class="${m.status === "active" ? "" : "inactive"}">
     <td><b>${W.esc(m.name)}</b>${m.reason ? `<span class="sub">${W.esc(m.reason)}</span>` : ""}${m.route ? `<span class="sub">${W.esc(m.route)}</span>` : ""}${m.note ? `<span class="sub">Note: ${W.esc(m.note)}</span>` : ""}</td><td>${val(m.dose)}</td><td>${val(m.frequency)}</td>
     <td>${day(m.start)}</td><td>${m.end ? day(m.end) : NR()}${m.stop_reason ? `<span class="sub">${W.esc(m.stop_reason)}</span>` : ""}</td><td>${val(m.prescriber)}</td>
     <td><span class="chip ${m.status === "active" ? "sla-green" : "sla-none"}">${W.esc(m.status.charAt(0).toUpperCase() + m.status.slice(1))}</span>${w && m.status === "active" ? ` <button class="secondary sm" data-stop="${m.id}" data-name="${W.esc(m.name)}">Stop</button>` : ""}</td></tr>`));
  const refs = tbl(["Date", "Request", "To", "Status", "Detail"], R.referrals.map(r => `<tr><td>${day(r.date)}</td><td><b>${W.esc(r.service)}</b><span class="sub">${r.kind === "referral" ? "Referral" : "Confirmatory test"} · requested by ${W.esc(r.requester || "–")}</span></td>
     <td>${val(r.to)}${r.specialist ? `<span class="sub">${W.esc(r.specialist)}</span>` : ""}</td><td><span class="chip sla-none">${W.esc(r.status)}</span>${r.result ? ` <span class="chip ${r.result === "positive" ? "sla-red" : "sla-green"}">${W.esc(r.result)}</span>` : ""}</td>
     <td class="small">${r.summary ? W.esc(r.summary) : ""}${r.reason ? `<div>Reason: ${W.esc(r.reason)}</div>` : ""}${!r.summary && !r.reason ? NR() : ""}</td></tr>`));
  const apps = tbl(["Date", "Where", "Clinician", "Status"], R.appointments.map(a => `<tr><td>${a.date ? W.dt(a.date) : NR()}</td><td>${val(a.org)}</td><td>${val(a.clinician)}</td>
     <td><span class="chip ${a.status === "fulfilled" ? "sla-green" : a.status === "noshow" ? "sla-red" : "sla-none"}">${W.esc(a.upcoming ? "Booked (upcoming)" : a.status_label)}</span>${a.barrier ? `<span class="sub">Barrier: ${W.esc(a.barrier)}</span>` : ""}${a.note ? `<span class="sub">Note: ${W.esc(a.note)}</span>` : ""}</td></tr>`));
  const resp = R.responses.length ? R.responses.map(r => `<div class="resp"><div><b>${W.esc(r.title)}</b> <span class="small muted">${W.dt(r.date)}</span> ${r.kind === "teach_back" ? flagChip(!r.passed, r.result) : ""}</div>
     ${r.kind === "teach_back" ? `<ul class="small tb-ans">${r.answers.map(a => `<li><span class="muted">${W.esc(a.question)}</span> ${W.esc(a.answer)}</li>`).join("")}</ul>${r.confidence ? `<div class="small">Confidence ${r.confidence}/5</div>` : ""}` : `<div class="small">${W.esc(r.result)}</div>`}</div>`).join("") : `<p class="small">${NR()}</p>`;
  const out = tbl(["Date", "Contact", "Channel", "Status"], R.outreach.slice(0, 20).map(o => `<tr><td>${W.dt(o.date)}</td><td>${W.esc(o.what)}${o.attempt > 1 ? ` <span class="sub">attempt ${o.attempt}</span>` : ""}</td><td>${W.esc(o.channel)}</td>
     <td><span class="chip ${/completed|reached/.test(o.status) ? "sla-green" : /fail|no answer|wrong/.test(o.status) ? "sla-red" : "sla-none"}">${W.esc(o.status)}</span></td></tr>`)) + (R.outreach_total > 20 ? `<p class="small muted">Showing the latest 20 of ${R.outreach_total}. All messages are in the case timeline below.</p>` : "");
  const docs = R.documents.length ? `<ul class="docs">${R.documents.map(d => `<li><b>${d.href ? `<a href="${W.esc(d.href)}" target="_blank" rel="noopener">${W.esc(d.title)}</a>` : W.esc(d.title)}</b> <span class="small muted">${day(d.date)} · ${W.esc(d.kind)}</span>${d.text ? `<div class="small">${W.esc(d.text)}</div>` : ""}</li>`).join("")}</ul>` : `<p class="small">${NR()}</p>`;
  const others = R.cases.length > 1 ? `<p class="small">Screening cases for this patient: ${R.cases.map(c => c.current ? `<b>#${c.id} ${W.esc(W.progText(c.programme))} (this case)</b>` : `<a href="/cases/${c.id}">#${c.id} ${W.esc(W.progText(c.programme))}</a> <span class="muted">${W.esc(c.state.replace(/_/g, " "))}</span>`).join(" · ")}</p>` : "";
  return `<section class="record" aria-labelledby="rec-h"><div class="rec-head"><h2 id="rec-h">Clinical record</h2><a class="small" href="#case-timeline">Case timeline ↓</a></div>
    ${summary}${others}${R.hidden_cases ? `<p class="small muted">${R.hidden_cases} other case(s) for this patient are outside your organisation's access and are not shown.</p>` : ""}
    ${sec("tests", "Tests and investigations", R.tests.length, tests, true)}
    ${sec("enc", "Consultations and encounters", R.encounters.length, (w ? `<div class="rec-tools"><button class="secondary sm" data-rec="encounter">Add consultation note</button></div><div id="rec-form-encounter"></div>` : "") + encs, true)}
    ${sec("meds", "Medicines", R.medications.filter(m => m.status === "active").length + " active", (w ? `<div class="rec-tools"><button class="secondary sm" data-rec="medication">Add medicine</button></div><div id="rec-form-medication"></div>` : "") + meds, true)}
    ${sec("refs", "Referrals and appointments", R.referrals.length + R.appointments.length, `<h4>Referrals and test requests</h4>${refs}<h4>Appointments</h4>${apps}`, true)}
    ${sec("out", "Outreach and patient responses", R.outreach_total + R.responses.length, `<h4>Patient responses</h4>${resp}<h4>Outreach attempts</h4>${out}`, false)}
    ${sec("docs", "Care plans and documents", R.documents.length, docs, false)}</section>`;
}
function localNow() { const t = new Date(); t.setSeconds(0, 0); return new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); }
function recForm(kind) {
  const el = document.getElementById("rec-form-" + kind); if (!el) return;
  if (el.innerHTML) { el.innerHTML = ""; return; }
  const kinds = Object.entries(REC.encounter_kinds).filter(([k]) => k !== "e_consult").map(([k, v]) => `<option value="${k}">${W.esc(v)}</option>`).join("");
  el.innerHTML = kind === "encounter" ? `<form class="action-form" aria-label="Add consultation note"><h3>Add consultation note</h3><p class="small muted">Visible to the care team only – never shown to the patient.</p>
      <label>Type<select name="kind">${kinds}</select></label><label>Date and time<input type="datetime-local" name="start" value="${localNow()}" max="${localNow()}" required></label>
      <label>Reason<input name="reason" maxlength="300"></label><label>Clinical note<textarea name="notes" rows="4" minlength="5" maxlength="5000" required></textarea></label>
      <label>Outcome / plan<input name="outcome" maxlength="300"></label><div class="form-buttons"><button type="submit">Save note</button><button type="button" class="secondary" data-cancel>Cancel</button></div></form>`
    : `<form class="action-form" aria-label="Add medicine"><h3>Add medicine</h3>
      <label>Medicine<input name="name" minlength="2" maxlength="200" required placeholder="e.g. Metformin"></label>
      <div class="form-pair"><label>Dose<input name="dose" maxlength="80" placeholder="e.g. 500 mg"></label><label>Frequency<input name="frequency" maxlength="80" placeholder="e.g. Twice daily with meals"></label></div>
      <div class="form-pair"><label>Route<input name="route" maxlength="40" value="oral"></label><label>Start date<input type="date" name="start_date" value="${localNow().slice(0, 10)}"></label></div>
      <div class="form-pair"><label>End date (optional)<input type="date" name="end_date"></label><span></span></div>
      <label>Status<select name="status"><option value="active">Active</option><option value="completed">Completed course</option><option value="stopped">Stopped</option></select></label>
      <label>Reason / indication<input name="reason" maxlength="300"></label><label>Note<textarea name="note" rows="2" maxlength="2000"></textarea></label>
      <div class="form-buttons"><button type="submit">Save medicine</button><button type="button" class="secondary" data-cancel>Cancel</button></div></form>`;
  const f = el.querySelector("form"); f.querySelector("[data-cancel]").onclick = () => { el.innerHTML = ""; }; f.querySelector("select, input").focus();
  f.onsubmit = async ev => { ev.preventDefault(); const b = f.querySelector("button[type=submit]"); W.busy(b, true); W.formError(f, ""); const fd = Object.fromEntries(new FormData(f));
    try {
      if (kind === "encounter") await W.api(`/api/cases/${CASE_ID}/encounters`, { method: "POST", body: { kind: fd.kind, start: new Date(fd.start).toISOString(), reason: fd.reason || null, notes: fd.notes, outcome: fd.outcome || null } });
      else await W.api(`/api/cases/${CASE_ID}/medications`, { method: "POST", body: { name: fd.name, dose: fd.dose || null, frequency: fd.frequency || null, route: fd.route || null, start_date: fd.start_date ? new Date(fd.start_date + "T12:00:00").toISOString() : null, end_date: fd.end_date ? new Date(fd.end_date + "T12:00:00").toISOString() : null, status: fd.status, reason: fd.reason || null, note: fd.note || null } });
      REC_OPEN.add(kind === "encounter" ? "enc" : "meds"); W.toast(kind === "encounter" ? "Consultation note saved" : "Medicine saved", "ok"); await load();
    } catch (e) { W.formError(f, e.message); W.toast(e.message, "err"); W.busy(b, false); } };
}
function wireRecord() {
  const r = root();
  r.querySelectorAll("details.rec-sec").forEach(d => d.addEventListener("toggle", () => { const id = d.dataset.sec; REC_OPEN.delete(id); REC_OPEN.delete("!" + id); REC_OPEN.add(d.open ? id : "!" + id); }));
  r.querySelectorAll("[data-rec]").forEach(b => b.onclick = () => recForm(b.dataset.rec));
  r.querySelectorAll("[data-stop]").forEach(b => b.onclick = async () => {
    await W.ask(`Stop ${b.dataset.name}?`, "Reason for stopping", { ok: "Stop medicine", min: 3, danger: true,
      save: async why => { await W.api(`/api/cases/${CASE_ID}/medications/${b.dataset.stop}/stop`, { method: "POST", body: { reason: why } }); REC_OPEN.add("meds"); W.toast("Medicine stopped", "ok"); await load(); } }); });
}

function whyCard() {
  const w = C.why; if (!w) return "";
  const r = w.rule || {}, rev = w.review || {}, link = W.can("model:read") && r.id ? ` · <a href="/governance?tab=decisions#rule-${encodeURIComponent(r.id)}">rule card</a>` : "";
  return `<section class="card flat" id="why-priority" aria-labelledby="why-h"><h2 id="why-h" class="small" style="font-size:var(--fs-base)">Why this priority</h2>
   <p style="margin-bottom:var(--s-2)"><span class="chip">${W.esc(w.tier_label)}</span> <span class="small muted">set by the ${W.esc(w.decided_by)}</span></p>
   <ol class="small" id="why-steps">${(w.steps.length ? w.steps : ["Tier set when the result arrived."]).map(t => `<li>${W.esc(t)}</li>`).join("")}</ol>
   <dl class="facts small">${r.id ? `<div><dt>Rule</dt><dd><code>${W.esc(r.id)}</code>${link}</dd></div>` : ""}${r.source ? `<div><dt>Source</dt><dd>${W.esc(r.source)}</dd></div>` : ""}
    ${w.target_text ? `<div><dt>Pathway target</dt><dd>${W.esc(w.target_text)}${w.target_due_at ? ` – by ${W.dt(w.target_due_at)}` : ""}</dd></div>` : ""}
    ${rev.required ? `<div><dt>Waiting for clinician review</dt><dd>${rev.reasons.map(x => W.esc(x.replaceAll("_", " "))).join(", ")}</dd></div>` : ""}</dl>
   <p class="xs muted">${W.esc(w.human_in_loop)}</p></section>`;
}
function patientCard() {
  const p = C.patient, o = C.result, r = C.risk;
  const ch = [p.channels.smartphone ? "App" : "", p.channels.whatsapp ? "WhatsApp" : "", p.channels.phone_valid ? "Phone" : ""].filter(Boolean);
  return `<section class="card" aria-labelledby="pt-h"><h2 id="pt-h" class="small" style="font-size:var(--fs-base)">Patient</h2>
   <p class="small">${W.esc(p.name)} · ${W.esc(p.gender)} · age ${W.esc(p.age_band)} · speaks <b>${W.esc(p.language)}</b> · ${W.esc(p.region)}</p>
   <p class="small">Family doctor (GP): ${W.esc(p.gp || "–")}<br>${W.esc(p.managing_org)}</p>
   <p class="small">Can be reached by: ${ch.length ? ch.join(", ") : "<b>no valid phone or app</b>"}</p>
   <h3 class="small" style="font-size:var(--fs-base)">Screening result</h3>${o ? `<p class="small">${W.esc(o.display)}: ${o.observations.map(x => `<b>${W.esc(x.value ?? x.value_text)}</b> ${W.esc(x.unit || "")}`).join(", ")}<br><span class="muted">issued ${W.dt(o.issued_at)}</span></p>
     <dl class="facts result-meta" data-result-meta><div><dt>Ordered by</dt><dd>${o.ordering_clinician ? W.esc(o.ordering_clinician) : '<span class="muted">Not recorded</span>'}</dd></div><div><dt>Screening site</dt><dd>${o.screening_site ? W.esc(o.screening_site) : '<span class="muted">Not recorded</span>'}</dd></div><div><dt>Specimen taken</dt><dd>${o.specimen_collected_at ? W.dt(o.specimen_collected_at) : '<span class="muted">Not recorded</span>'}</dd></div></dl>` : "<p class='small muted'>–</p>"}
   <h3 class="small" style="font-size:var(--fs-base)">Consent</h3><p class="small">${C.consents.filter(c => c.status === "active").map(c => `<span class="chip ${c.decision === "permit" ? "sla-green" : "sla-red"}">${c.decision === "permit" ? "✓" : "✕"} ${W.esc(c.scope.replace(/_/g, " "))}: ${c.decision === "permit" ? "yes" : "no"}</span>${c.note ? ` <span class="small muted">${W.esc(c.note)}</span>` : ""}`).join(" ") || "–"}</p></section>
   ${whyCard()}
   <section class="card flat" aria-labelledby="ml-h"><h2 id="ml-h" class="small" style="font-size:var(--fs-base)">Outreach priority <span class="muted xs">(model v${r.model_version ?? "–"}, ordering only)</span></h2>
   <p style="margin-bottom:var(--s-2)"><b>${r.score == null ? "–" : Math.round(r.score * 100) + "%"}</b> <span class="small muted">chance of missing the next step</span></p>
   ${r.score == null ? "" : `<details class="small"><summary>Why this estimate</summary>${(r.rationale || []).length ? `<ul>${r.rationale.map(w => `<li>${W.esc(w.text)} – <b>${w.direction}</b> the chance (${w.effect > 0 ? "+" : ""}${Math.round(w.effect * 100)} points)</li>`).join("")}</ul>` : "<p>No single factor stands out.</p>"}<p class="muted">${W.esc(r.ordering)}. ${W.esc(r.use)}.</p></details>`}</section>`;
}

// the patient's answers to a booked appointment (patient app): "I will come" / "please change it" (never moves it)
const BARRIER_TEXT = { work: "work or study", transport: "transport", childcare: "childcare / family", cost: "cost", fear_anxiety: "worried / anxious",
  language: "language / interpreter", ramadan_hours: "Ramadan or prayer times", unknown: "another reason" };
function patientRequests() {
  const R = (C.patient_requests || []).filter(r => r.kind === "change" && r.status === "open"), can = (W.me.permissions || []).includes("appointment:write");
  const conf = (C.patient_requests || []).find(r => r.kind === "confirm" && C.appointments.some(a => a.id === r.appointment_id && a.status === "booked"));
  if (!R.length && !conf) return "";
  return `<section class="card notice" id="patient-requests" aria-labelledby="preq-h"><h2 id="preq-h" class="small" style="font-size:var(--fs-base)">From the patient app</h2>
    ${R.map(r => `<div class="msg" data-req="${r.id}"><b>Asks to change the appointment</b> <span class="small muted">${W.dt(r.at)}</span>
      <div class="small">What gets in the way: <b>${W.esc(BARRIER_TEXT[r.barrier_code] || r.barrier_code || "–")}</b></div>
      ${r.preferred_times ? `<div class="small">Suits better: ${W.esc(r.preferred_times)}</div>` : ""}${r.note ? `<div class="small">“${W.esc(r.note)}”</div>` : ""}
      <div class="small muted">The appointment has not moved. Move or cancel it (this closes the request), or mark it handled.</div>
      ${can ? `<button class="secondary sm" data-resolve="${r.id}" type="button">Mark as handled</button>` : ""}</div>`).join("")}
    ${conf ? `<p class="small">✓ Patient confirmed they will come (${W.dt(conf.at)}).</p>` : ""}</section>`;
}

function render() {
  const acts = C.actions, primary = acts.find(a => a.primary) || null, others = acts.filter(a => a !== primary);
  root().innerHTML = header() + `<div class="grid-2 case-grid"><div>
    <div class="tabs" role="tablist" aria-label="Case details" id="case-timeline"><button role="tab" aria-selected="true" data-t="steps">Timeline</button><button role="tab" aria-selected="false" class="secondary" data-t="events">Messages &amp; events</button><button role="tab" aria-selected="false" class="secondary" data-t="audit">Audit (${C.audit.length})</button></div>
    <div id="tabbody" role="tabpanel"></div></div>
    <aside aria-label="Actions and patient details"><section class="card ${primary ? "next-step" : ""}" aria-labelledby="act-h"><h2 id="act-h" style="font-size:var(--fs-lg)">${primary ? "Next step" : "Actions"}</h2>
      ${primary ? `<button class="primary-action" data-k="${primary.key}" data-ev="${primary.evidence_type || ""}">${W.esc(primary.label)}</button>` : ""}
      ${others.length ? (primary ? `<details class="more-actions"><summary>More actions (${others.length})</summary><div class="actions">${others.map(btn).join("")}</div></details>` : `<div class="actions">${others.map(btn).join("")}</div>`) : (primary ? "" : `<p class="small muted">There is nothing for your role to do on this case right now.</p>`)}
      <div id="aform"></div></section>${patientRequests()}${patientCard()}</aside></div>`;
  const tabs = { steps, events, audit: auditTable };
  root().querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
    root().querySelectorAll(".tabs button").forEach(x => { x.className = "secondary"; x.setAttribute("aria-selected", "false"); }); b.className = ""; b.setAttribute("aria-selected", "true");
    document.getElementById("tabbody").innerHTML = tabs[b.dataset.t]();
  });
  document.getElementById("tabbody").innerHTML = steps();
  root().querySelectorAll("[data-k]").forEach(b => b.onclick = () => openForm(b.dataset.k, b.dataset.ev));
  wireRecord();
  root().querySelectorAll("[data-resolve]").forEach(b => b.onclick = () => W.ask("Patient's change request handled", "What was done (e.g. phoned, the patient keeps the time)", { ok: "Mark as handled", min: 5,
    save: async note => { await W.api(`/api/cases/${CASE_ID}/appointment/requests/${b.dataset.resolve}/resolve`, { method: "POST", body: { reason: note } }); W.toast("Request marked as handled", "ok"); await load(); } }));
}
const btn = a => `<button class="secondary" data-k="${a.key}" data-ev="${a.evidence_type || ""}">${W.esc(a.label)}</button>`;

async function postRaw(path, body, okMsg, key, formEl = null) {
  if (key && RISKY[key] && !(await W.confirm(RISKY[key], "This is recorded in the case history with your name and cannot be silently undone.", { ok: "Yes, continue", danger: /close|deceased|decline|unreachable/.test(key) }))) return false;
  W.formError(formEl, "");
  try { const r = await W.api(path, { method: "POST", body }); W.toast(okMsg || "Saved", "ok"); await load(); return r || true; }
  catch (e) { W.formError(formEl, e.message); W.toast(e.message, "err"); return false; }
}
const post = (p, b, m) => postRaw(p, b, m);
window.respond = async (id, b) => { const ta = document.getElementById("resp-" + id), v = ta.value.trim(), box = ta.parentElement;
  if (v.length < 2) { W.formError(box, "Write your answer first (at least 2 characters)."); ta.focus(); return; }
  W.busy(b, true); const ok = await postRaw(`/api/consults/${id}/respond`, { response: v }, "Answer sent", null, box); if (!ok) W.busy(b, false); };
window.ackSummary = async id => { await W.ask("Acknowledge the care summary", "Note (optional)", { ok: "Acknowledge", optional: true,
  save: async note => { await W.api(`/api/gp-summaries/${id}/ack`, { method: "POST", body: { note: note || null } }); W.toast("Summary acknowledged", "ok"); await load(); } }); };
window.ackEsc = async id => { await W.ask("Acknowledge escalation", "What action are you taking?", { ok: "Acknowledge", min: 3,
  save: async note => { await W.api(`/api/escalations/${id}/ack`, { method: "POST", body: { note } }); W.toast("Escalation acknowledged", "ok"); await load(); } }); };

function events() {
  const ev = [];
  C.communications.forEach(m => ev.push([m.sent_at, `<b>${W.esc(m.category)}</b> via ${W.esc(m.channel)} → ${W.esc(m.to)} <span class="chip ${m.status === "completed" ? "sla-green" : m.status === "failed" ? "sla-red" : "sla-none"}">${W.esc(m.status)}</span> ${m.language ? `<span class="small muted">${m.language}${m.variant ? "/" + m.variant : ""}</span>` : ""}<div class="small muted">${W.esc(m.content).slice(0, 260)}</div>`]));
  (C.patient_requests || []).forEach(r => ev.push([r.at, r.kind === "confirm" ? "📱 patient confirmed the appointment (patient app)" : `📱 patient asked to change the appointment – ${W.esc(BARRIER_TEXT[r.barrier_code] || r.barrier_code || "")}${r.preferred_times ? " · suits better: " + W.esc(r.preferred_times) : ""}${r.note ? `<div class="small">${W.esc(r.note)}</div>` : ""}${r.status === "handled" ? `<div class="small muted">Handled ${W.dt(r.handled_at)} by ${W.esc(r.handled_by || "")}${r.handled_note ? ": " + W.esc(r.handled_note) : ""}</div>` : ""}`]));
  C.call_tasks.forEach(t => ev.push([t.completed_at || t.created_at, `📞 phone-call task (${W.esc(t.owner)}) – ${t.status}${t.outcome ? ": <b>" + W.esc(t.outcome) + "</b>" : ""}`]));
  C.teach_back.forEach(t => ev.push([t.at, `✅ teach-back (${t.mode}, ${t.language}): ${t.score}/${t.total} ${t.passed ? '<span class="chip sla-green">understood</span>' : '<span class="chip sla-red">not yet clear</span>'}${t.confidence ? " · confidence " + t.confidence + "/5" : ""}${t.patient_changed_answers ? (() => { const p = C.teach_back.find(o => o.id === t.replaces_id); return ` <span class="chip sla-amber">patient changed answers</span><div class="small muted">Replaces the answers sent ${p ? W.dt(p.at) + " (" + p.score + "/" + p.total + ")" : "earlier"}; both are kept in the record and the audit trail.</div>`; })() : ""}`]));
  C.service_requests.forEach(s => ev.push([s.decided_at || s.created_at, `📄 ${s.kind.replace("_", " ")} → ${W.esc(s.performer_org)} <span class="chip sla-none">${s.status}</span>${s.result ? " result <b>" + s.result + "</b>" : ""}${s.reason_code ? ` reason <b>${W.esc(s.reason_code)}</b>` : ""}${s.reason_note ? `: ${W.esc(s.reason_note)}` : ""}`]));
  C.appointments.forEach(a => ev.push([a.cancelled_at || a.start, `📅 appointment ${W.dt(a.start)} at ${W.esc(a.org)} <span class="chip ${a.status === "fulfilled" ? "sla-green" : a.status === "noshow" || a.status === "cancelled" ? "sla-red" : "sla-none"}">${a.status}</span>${a.barrier_code ? " barrier: " + W.esc(a.barrier_code) : ""}${a.cancel_reason ? ` · ${W.esc((META.appointment_change_reasons && META.appointment_change_reasons[a.cancel_reason]) || a.cancel_reason)}` : ""}${a.cancel_note ? `<div class="small">${W.esc(a.cancel_note)}</div>` : ""}${a.rescheduled_from_id ? `<div class="small muted">Moved from appointment #${a.rescheduled_from_id}</div>` : ""}${a.reminder_sent_at ? `<div class="small muted">Reminder sent ${W.dt(a.reminder_sent_at)}</div>` : ""}${a.note ? `<div class="small">Booking note: ${W.esc(a.note)}</div>` : ""}`]));
  (C.reviews || []).forEach(r => ev.push([r.at, `🩺 clinical review by ${W.esc(r.by || "")}${r.tier_from !== r.tier_to ? ` – urgency ${W.esc(r.tier_from || "")} → <b>${W.esc(r.tier_to || "")}</b>` : ""}<div class="small">${W.esc(r.note || "")}</div>`]));
  (C.lessons || []).forEach(l => ev.push([l.at, `📝 lesson learned (${W.esc(l.kind.replace(/_/g, " "))}): <b>${W.esc(l.reason_code.replace(/_/g, " "))}</b> <span class="small muted">by ${W.esc(l.by)}</span>${l.detail ? `<div class="small">${W.esc(l.detail)}</div>` : ""}`]));
  C.care_plans.forEach(p => ev.push([p.created_at, `🩺 ${p.kind} plan by ${W.esc(p.by)}: ${W.esc(p.description)}${p.review_due_on ? ` · review due ${W.esc(p.review_due_on)}` : ""}`]));
  (C.gp_summaries || []).forEach(g => ev.push([g.created_at, `📨 care summary to GP <b>${W.esc(g.to || "")}</b> (${g.kind === "case_closed" ? "case closed" : "care started"}) ${g.acknowledged_at ? `<span class="chip sla-green">acknowledged ${W.dt(g.acknowledged_at)}</span>${g.ack_note ? `<div class="small">GP note: ${W.esc(g.ack_note)}</div>` : ""}` : `<span class="chip ${g.overdue ? "sla-red" : "sla-amber"}">${g.overdue ? "not acknowledged · overdue" : "waiting for the GP"}</span>`}
    <details class="small"><summary>Read the summary</summary><div class="sum-text">${W.esc(g.text || "")}</div></details>${g.can_ack ? `<button class="secondary sm" onclick="ackSummary(${g.id})">Acknowledge</button>` : ""}`]));
  C.consults.forEach(q => ev.push([q.created_at, `💬 ${q.type === "patient" ? "patient-to-clinician" : "clinician-to-clinician"} consult → ${W.esc(q.target)} (${q.urgency}) <span class="chip ${q.status === "answered" ? "sla-green" : q.overdue ? "sla-red" : "sla-amber"}">${q.status}${q.overdue ? " · overdue" : ""}</span><div class="small">Q: ${W.esc(q.question)}</div>${q.response ? `<div class="small">A: ${W.esc(q.response)}</div>` : ""}${q.status === "requested" && W.me.practitioner_id === q.target_id ? `<div><label for="resp-${q.id}" class="sr-only">Your answer</label><textarea id="resp-${q.id}" rows="2" placeholder="Your answer…"></textarea><button class="secondary sm" onclick="respond(${q.id}, this)">Send answer</button></div>` : ""}`]));
  C.escalations.forEach(e => ev.push([e.created_at, `🚨 <b>L${e.level}</b> → ${W.esc(e.target)}: ${W.esc(e.note)} ${e.acknowledged_at ? '<span class="chip sla-green">acknowledged</span>' : (W.me.permissions.includes("escalation:ack") ? `<button class="secondary sm" onclick="ackEsc(${e.id})">Acknowledge</button>` : "")}`]));
  C.ownership_events.forEach(o => ev.push([o.at, `👤 ownership ${W.esc(o.kind)}: ${W.esc(o.from || "–")} → <b>${W.esc(o.to)}</b> ${W.esc(o.reason || "")}`]));
  ev.sort((a, b) => (b[0] || "").localeCompare(a[0] || ""));
  return ev.length ? `<ul class="timeline">${ev.map(([t, h]) => `<li class="ev"><span class="small muted">${W.dt(t)}</span> ${h}</li>`).join("")}</ul>` : W.empty("No events yet", "Messages, calls, referrals and appointments will appear here.");
}

function steps() {
  return `<ul class="timeline">${C.steps.slice().reverse().map(s => `<li class="${s.breached ? "breach" : ""}">
    <b>${W.esc(s.label)}</b> ${s.status === "in-progress" ? W.sla(s.sla_status, s.sla_fraction) : (s.breached ? '<span class="chip sla-red">▲ Missed deadline</span>' : "")}
    <div class="small">Owner: <b>${W.esc(s.owner)}</b> · ${W.dt(s.started_at)} → ${s.ended_at ? W.dt(s.ended_at) : "now"} ${s.sla_hours ? `· time allowed ${s.sla_hours} h` : ""}</div>
    ${s.note ? `<div class="small muted">${W.esc(s.note)}</div>` : ""}</li>`).join("")}</ul>`;
}

function auditTable() {
  return `<p class="small muted">Every view and change of this case, in an append-only, tamper-evident log.</p><table class="compact"><thead><tr><th>When</th><th>Who</th><th>Action</th><th>Event</th><th>Outcome</th></tr></thead><tbody>${C.audit.map(a =>
    `<tr><td class="small">${W.dt(a.ts)}</td><td class="small">${W.esc(a.actor)}<div class="muted">${W.esc(W.role(a.role))}</div></td><td>${a.action}</td><td class="small">${W.esc(a.event)}${a.detail && a.detail.to ? " → " + W.esc(a.detail.to) : ""}</td><td class="small">${a.outcome}</td></tr>`).join("")}</tbody></table>`;
}


const F = {
  note: (l = "Note", req = false) => `<label>${l}<textarea name="note" rows="2" ${req ? "required" : ""}></textarea></label>`,
  sel: (name, label, options) => `<label>${label}<select name="${name}">${options}</select></label>`,
};
async function openForm(key, evidenceType) {
  const el = document.getElementById("aform"), u = `/api/cases/${CASE_ID}`;
  const TITLE = Object.fromEntries(C.actions.map(a => [a.key, a.label]));
  const post = (p, b, m) => postRaw(p, b, m, key, el.querySelector("form"));
  const form = (html, onsubmit, submit = "Confirm") => {
    el.innerHTML = `<form class="action-form" aria-label="${W.esc(TITLE[key] || "Action")}"><h3>${W.esc(TITLE[key] || "Action")}</h3>${html}<div class="form-buttons"><button type="submit">${submit}</button><button type="button" class="secondary" id="cancel">Cancel</button></div></form>`;
    el.querySelector("#cancel").onclick = () => { el.innerHTML = ""; };
    const fm = el.querySelector("form"); const first = fm.querySelector("select, input:not([type=hidden]), textarea"); if (first) first.focus(); fm.scrollIntoView({ block: "nearest", behavior: "smooth" });
    fm.onsubmit = async ev => { ev.preventDefault(); const b = fm.querySelector("button[type=submit]"); W.busy(b, true); const fd = Object.fromEntries(new FormData(ev.target)); await onsubmit(fd); W.busy(b, false); };
  };
  const tr = (to, msg) => form(F.note(), fd => post(`${u}/transition`, { to_state: to, note: fd.note || null }, msg));
  const d = ["assign", "transfer", "consult", "refer", "order_confirmatory", "book"].includes(key) ? await dir() : null;
  switch (key) {
    case "triage": return tr("triaged", "Triage confirmed");
    case "consultation": return tr("consultation", "Consultation recorded");
    case "unreachable": return tr("unreachable", "Marked unreachable");
    case "notify": return post(`${u}/notify`, { category: "result_notification" }, "Notification recorded – sending to patients' phones is not connected yet");
    case "reminder": return post(`${u}/notify`, { category: "reminder" }, "Reminder recorded – sending to patients' phones is not connected yet");
    case "call": return form(F.sel("outcome", "Call outcome", kv({ reached: "Reached – result explained", no_answer: "No answer", wrong_number: "Wrong number" })) + F.note(),
      fd => post(`${u}/call`, { outcome: fd.outcome, note: fd.note || null }, "Call recorded"));
    case "teachback": {
      const lang = ["en", "ar", "hi", "ur"].includes(C.patient.language) ? C.patient.language : "en";
      const tb = await W.api(`${u}/teach-back-form?lang=${lang}`);
      return form(`<p class="small">Ask the patient in their own words; tick what they say.</p><div class="${["ar", "ur"].includes(lang) ? "rtl" : ""}">` + tb.questions.map(q => `<div class="q"><b>${W.esc(q.text)}</b>${q.options.map(o => `<label><input type="radio" name="${q.id}" value="${o.key}" required> ${W.esc(o.text)}</label>`).join("")}</div>`).join("") +
        `</div>` + F.sel("confidence", "Patient confidence (1–5)", [1, 2, 3, 4, 5].map(n => `<option value="${n}" ${n === 3 ? "selected" : ""}>${n}</option>`).join("")),
        fd => post(`${u}/teach-back`, { answers: { q1: fd.q1, q2: fd.q2, q3: fd.q3 }, confidence: +fd.confidence, language: lang }, "Teach-back recorded"));
    }
    case "order_confirmatory": return form(F.sel("org", "Performing organisation", opts(d.orgs.filter(o => ["lab", "hospital"].includes(o.kind)))),
      fd => post(`${u}/confirmatory`, { performer_org_id: +fd.org }, "Confirmatory test ordered"));
    case "confirm_result": return form(F.sel("result", "Confirmatory result", kv({ positive: "Positive – needs consultation", negative: "Negative" })),
      fd => post(`${u}/confirmatory/result`, { result: fd.result }, "Result recorded"));
    case "refer": {
      const blocked = C.actions.find(a => a.key === "refer").consent_blocked;
      const hosp = d.orgs.filter(o => o.kind === "hospital");
      return form((blocked ? `<p class="notice-inline err">Patient has <b>declined</b> sharing with other organisations. Record their consent first (Record consent → share_external).</p>` : "") +
        F.sel("org", "Receiving hospital", opts(hosp)) + F.sel("spec", "Specialist (optional)", `<option value="">Department decides</option>` + opts(d.prac.filter(p => p.role === "specialist"), p => p.id, p => p.name + " – " + (hosp.find(h => h.id === p.org_id) || {}).name)) +
        `<label>Clinical summary<textarea name="summary" rows="3" placeholder="Result, relevant history, medications, preferred language…"></textarea></label>`,
        fd => post(`${u}/referral`, { performer_org_id: +fd.org, performer_id: fd.spec ? +fd.spec : null, summary: fd.summary }, "Referral sent"), "Send referral");
    }
    case "decide_referral": return form(F.sel("accept", "Decision", kv({ true: "Accept", false: "Reject (reason required)" })) + F.sel("reason_code", "Reject reason", `<option value="">–</option>` + kv(META.reject_reasons)) + F.note("Note (required if rejecting)"),
      fd => post(`${u}/referral/decision`, { accept: fd.accept === "true", reason_code: fd.reason_code || null, note: fd.note || null }, "Referral decision recorded"));
    case "book": { const t = new Date(Date.now() + 7 * 864e5); t.setMinutes(0, 0, 0); const v = new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 16); // local wall-clock
      return form(`<label>Appointment date/time<input type="datetime-local" name="start" value="${v}" required></label>` +
        `<label>Reason if after the pathway target${C.target_due_at ? " (" + W.esc(String(C.target_due_at).slice(0, 10)) + ")" : ""}<input name="beyond" placeholder="e.g. earliest available slot; patient's choice"></label>`,
        fd => post(`${u}/appointment`, { start: new Date(fd.start).toISOString(), beyond_target_reason: fd.beyond || null }, "Appointment booked – confirmation recorded (sending to phones is not connected yet)")); }
    case "attendance": return form(F.sel("attended", "Outcome", kv({ true: "Attended", false: "No-show" })) + F.sel("barrier", "No-show barrier", `<option value="">–</option>` + META.barriers.map(b => `<option>${b}</option>`).join("")),
      fd => post(`${u}/attendance`, { attended: fd.attended === "true", barrier_code: fd.attended === "true" ? null : (fd.barrier || "unknown") }, "Attendance recorded"));
    case "reschedule": { const t = new Date(Date.now() + 7 * 864e5); t.setMinutes(0, 0, 0); const v = new Date(t - t.getTimezoneOffset() * 6e4).toISOString().slice(0, 16);
      const reasons = Object.entries(META.appointment_change_reasons || {}).map(([k,l]) => `<option value="${k}">${W.esc(l)}</option>`).join("");
      return form(`<label>New date/time<input type="datetime-local" name="start" value="${v}" required></label>` +
        F.sel("reason_code", "Why the change", reasons) +
        F.sel("barrier", "What got in the way (if the patient asked)", `<option value="">–</option>` + META.barriers.map(b => `<option>${b}</option>`).join("")) +
        F.note("Note (needed for clinical reason or booking error)") +
        `<label>Reason if after the pathway target<input name="beyond" placeholder="e.g. earliest available slot"></label>`,
        fd => post(`${u}/appointment/reschedule`, { start: new Date(fd.start).toISOString(), reason_code: fd.reason_code, barrier_code: fd.barrier || null, note: fd.note || null, beyond_target_reason: fd.beyond || null }, "Appointment moved – patient message recorded (sending to phones is not connected yet)")); }
    case "cancel_appointment": {
      const reasons = Object.entries(META.appointment_change_reasons || {}).map(([k,l]) => `<option value="${k}">${W.esc(l)}</option>`).join("");
      return form(F.sel("reason_code", "Why cancel", reasons) +
        F.sel("barrier", "What got in the way (if the patient asked)", `<option value="">–</option>` + META.barriers.map(b => `<option>${b}</option>`).join("")) +
        F.note("Note (needed for clinical reason or booking error)"),
        fd => post(`${u}/appointment/cancel`, { reason_code: fd.reason_code, barrier_code: fd.barrier || null, note: fd.note || null }, "Appointment cancelled – case back to clinic accepted for rebooking")); }
    case "appointment_reminder": return post(`${u}/appointment/remind`, {}, "Appointment reminder recorded (sending to phones is not connected yet)");
    case "start_care": { const sd = (C.recall && C.recall.surveillance_default_due) || "";
      const f0 = form(F.sel("kind", "Plan type", kv({ treatment: "Treatment", surveillance: "Surveillance" })) + `<label>Plan description<textarea name="description" rows="2" required></textarea></label>` +
        `<label data-review hidden>Review / re-test due<input type="date" name="review_due_on" value="${sd}" min="${new Date(Date.now() + 864e5).toISOString().slice(0, 10)}"><span class="hint small muted">Default from the programme rule (${W.esc(sd)}); the person is recalled on this date after the case closes.</span></label>`,
        fd => post(`${u}/care-plan`, { kind: fd.kind, description: fd.description, review_due_on: fd.kind === "surveillance" ? (fd.review_due_on || null) : null }, "Care plan recorded"));
      setTimeout(() => { const k = document.querySelector('#aform select[name="kind"]'), r = document.querySelector("#aform [data-review]");
        if (k && r) { const t = () => { r.hidden = k.value !== "surveillance"; }; k.addEventListener("change", t); t(); } }, 0);
      return f0; }
    case "close_evidence": return form(`<p class="small">Closure with linked evidence: <b>${W.esc(evidenceType)}</b>. The system verifies the evidence exists for this case.</p>` + F.note(),
      fd => post(`${u}/close`, { closure_type: "evidence", evidence_type: evidenceType, note: fd.note || null }, "Case closed with evidence"), "Close case");
    case "close_authorised": return form(`<p class="small">Authorised human decision – needs a reason code and a rationale (≥10 characters). Some reasons need supporting records (e.g. informed decline, full contact ladder).</p>` +
      F.sel("reason", "Reason", kv(META.closure_reasons)) + F.note("Rationale", true), fd => post(`${u}/close`, { closure_type: "authorised_decision", reason: fd.reason, note: fd.note }, "Case closed (authorised decision)"), "Sign off closure");
    case "decline": return form(F.note("What did the patient say? (≥10 characters, recorded with your name; informed decline needs a clinician conversation)", true), fd => post(`${u}/decline`, { note: fd.note }, "Decline recorded – clinician review"));
    case "patient_deceased": return form(`<p class="notice-inline err">Records the death for <b>this patient</b>: all messages and call tasks stop and every open case goes to clinical review for an authorised closure. Check the identity first.</p>` +
      F.note("Source and date of the notification (≥10 characters)", true), fd => post(`${u}/patient-deceased`, { note: fd.note }, "Death recorded – outreach stopped, open cases sent to review"), "Sign off");
    case "patient_deceased_revoke": return form(`<p class="small">Only for a death recorded against the wrong patient. Open cases are re-reviewed; cases closed as deceased are listed for re-opening.</p>` +
      F.note("Why the record is wrong (≥20 characters)", true), fd => post(`${u}/patient-deceased/revoke`, { note: fd.note }, "Death record corrected – cases sent to review"), "Sign off correction");
    case "reconsider": return form(F.note("Note", true), fd => post(`${u}/reconsider`, { note: fd.note }, "Resumed"));
    case "assign": return form(F.sel("owner", "New owner", opts(d.prac.filter(p => !p.is_pool && ["coordinator", "gp", "specialist", "clinical_lead"].includes(p.role)), p => p.id, p => p.name)) + `<label>Reason<input name="reason" required></label>`,
      fd => post(`${u}/assign`, { owner_id: +fd.owner, reason: fd.reason }, "Owner changed"));
    case "transfer": return form(F.sel("to", "Receiving clinician", opts(d.prac.filter(p => ["gp", "specialist"].includes(p.role)), p => p.id, p => p.name + " – " + (d.orgs.find(o => o.id === p.org_id) || {}).name)) + F.note("Reason / summary for the receiver", true),
      fd => post(`${u}/transfer`, { to_owner_id: +fd.to, note: fd.note }, "Transfer requested – you stay accountable until accepted"));
    case "decide_transfer": return form(F.sel("accept", "Decision", kv({ true: "Accept ownership", false: "Decline" })) + F.note(), fd => post(`${u}/transfer/decision`, { accept: fd.accept === "true", note: fd.note || null }, "Transfer decision recorded"));
    case "consult": return form(F.sel("target", "Ask clinician", opts(d.prac.filter(p => ["gp", "specialist", "clinical_lead"].includes(p.role) && p.id !== W.me.practitioner_id), p => p.id, p => p.name)) +
      F.sel("urgency", "Urgency", kv({ [C.tier]: C.tier, urgent: "urgent (4h)", high: "high (24h)", routine: "routine (72h)" })) + `<label>Question<textarea name="question" rows="3" required></textarea></label>`,
      fd => post(`${u}/consults`, { target_practitioner_id: +fd.target, urgency: fd.urgency, question: fd.question }, "Consultation requested"));
    case "lesson": return form(F.sel("kind", "Type", kv({ overdue: "Overdue / delay", drop_off: "Drop-off", incident: "Incident / near miss" })) + F.sel("reason_code", "Reason", META.delay_reasons.map(r => `<option>${r}</option>`).join("")) + `<label>Detail<input name="detail"></label>`,
      fd => post(`${u}/lessons`, { kind: fd.kind, reason_code: fd.reason_code, detail: fd.detail || null }, "Lesson recorded"));
    case "consent": return form(F.sel("scope", "Scope", ["share_external", "comm_app", "comm_sms", "comm_whatsapp", "comm_phone", "alt_contact", "analytics"].map(s => `<option>${s}</option>`).join("")) + F.sel("decision", "Decision", kv({ permit: "Permit", deny: "Deny" })) + F.note("How did the patient give this decision? (≥10 characters, recorded with your name)", true),
      fd => post(`${u}/consent`, { scope: fd.scope, decision: fd.decision, note: fd.note || null }, "Consent recorded"));
    case "review": return form(`<p class="small">Review the result and patient context. Keep or raise the urgency unless you are certain; lowering it is audited as a downgrade.</p>` + F.sel("tier", "Urgency after review", `<option value="">Keep ${W.esc(C.tier)}</option>` + kv({ critical: "Critical", urgent: "Urgent", high: "High", routine: "Routine" })) + F.note("Clinical review note (≥10 characters)", true),
      fd => post(`${u}/review`, { note: fd.note, tier: fd.tier || null }, "Review recorded"));
    case "reopen": return form(F.note("Reason for re-opening", true), fd => post(`${u}/reopen`, { note: fd.note }, "Case re-opened"));
    case "fhir": return window.open(`${u}/fhir`, "_blank");
  }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  try { await load(); } catch (e) { root().innerHTML = W.empty("Could not open this case", W.esc(e.message), '<a role="button" href="/queue">Back to the queue</a>'); } });
