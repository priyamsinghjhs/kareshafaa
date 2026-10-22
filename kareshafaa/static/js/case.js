// Case timeline + context-aware actions.
const CASE_ID = document.getElementById("case-root").dataset.case;
let C = null, META = null, DIR = { prac: null, orgs: null };
const root = () => document.getElementById("case-root");

async function dir() {
  if (!DIR.prac) [DIR.prac, DIR.orgs] = await Promise.all([W.api("/api/directory/practitioners"), W.api("/api/directory/organizations")]).then(r => [r[0].items, r[1].items]);
  return DIR;
}
const opts = (arr, v = x => x.id, l = x => x.name) => arr.map(x => `<option value="${W.esc(v(x))}">${W.esc(l(x))}</option>`).join("");
const kv = obj => Object.entries(obj).map(([k, v]) => `<option value="${k}">${W.esc(v)}</option>`).join("");

async function load() {
  [C, META] = await Promise.all([W.api(`/api/cases/${CASE_ID}`), W.api(`/api/cases/${CASE_ID}/actions`)]);
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
   <dl class="facts">
     <div><dt>Current step</dt><dd>${W.esc(C.state_label)}</dd></div>
     <div><dt>Accountable owner</dt><dd>${owner}</dd></div>
     <div><dt>Step deadline</dt><dd>${C.sla.due_at ? W.dt(C.sla.due_at) + ` <span class="small muted">(${W.ago(C.sla.due_at)})</span>` : "No deadline"}</dd></div>
     <div><dt>Urgency</dt><dd class="small">${W.esc(W.tierText(C.tier))}</dd></div>
   </dl>
   <p class="small muted stepper-count">Step ${Math.max(1, C.journey.findIndex(j => j.status === "current") + 1)} of ${C.journey.filter(j => j.status !== "side").length} on the care pathway</p>
   <ol class="stepper" aria-label="Care pathway progress">${C.journey.map(j => `<li class="st ${j.status}" ${j.status === "current" ? 'aria-current="step"' : ""}>${W.esc(j.label)}</li>`).join("")}</ol>
   ${C.review_required ? `<div class="card alert" role="alert"><h3>Clinical review needed</h3><p>This result cannot move on automatically until a clinician reviews it: ${(C.review_reasons || []).map(x => `<span class="chip sla-red">${W.esc(x.replace(/_/g, " "))}</span>`).join(" ")}</p><p class="small" style="margin:0">Rule ${W.esc(C.rule_id || "–")} · pathway <b>${W.esc((C.pathway || "–").replace(/_/g, " "))}</b>${C.target ? " · target " + W.esc(C.target) : ""}. Urgency was set to the safer level; lowering it needs a clinician's reason (recorded).</p></div>` : (C.rule_id ? `<p class="small muted">Clinical rule ${W.esc(C.rule_id)} · pathway ${W.esc((C.pathway || "").replace(/_/g, " "))}${C.target ? " · target " + W.esc(C.target) : ""}${C.target_due_at ? ` · pathway target date <b>${W.dt(C.target_due_at)}</b>` : ""}</p>` : "")}
   ${C.transfer_pending ? `<div class="card notice">Transfer waiting for <b>${W.esc(C.transfer_pending.to_owner)}</b> to accept. The current owner stays accountable until then. <span class="small muted">${W.esc(C.transfer_pending.note || "")}</span></div>` : ""}
   ${C.closure ? `<div class="card ok"><b>Closed</b> (${W.esc(C.closure.type.replace(/_/g, " "))}: <b>${W.esc(C.closure.reason)}</b>) on ${W.dt(C.closure.closed_at)}. ${W.esc(C.closure.note || "")} ${C.closure.evidence_id ? `· evidence #${C.closure.evidence_id}` : ""}</div>` : ""}`;
}

function patientCard() {
  const p = C.patient, o = C.result, r = C.risk;
  const ch = [p.channels.smartphone ? "App" : "", p.channels.whatsapp ? "WhatsApp" : "", p.channels.phone_valid ? "Phone" : ""].filter(Boolean);
  return `<section class="card" aria-labelledby="pt-h"><h2 id="pt-h" class="small" style="font-size:var(--fs-base)">Patient</h2>
   <p class="small">${W.esc(p.name)} · ${W.esc(p.gender)} · age ${W.esc(p.age_band)} · speaks <b>${W.esc(p.language)}</b> · ${W.esc(p.region)}</p>
   <p class="small">Family doctor (GP): ${W.esc(p.gp || "–")}<br>${W.esc(p.managing_org)}</p>
   <p class="small">Can be reached by: ${ch.length ? ch.join(", ") : "<b>no valid phone or app</b>"}</p>
   <h3 class="small" style="font-size:var(--fs-base)">Screening result</h3>${o ? `<p class="small">${W.esc(o.display)}: ${o.observations.map(x => `<b>${W.esc(x.value ?? x.value_text)}</b> ${W.esc(x.unit || "")}`).join(", ")}<br><span class="muted">issued ${W.dt(o.issued_at)}</span></p>` : "<p class='small muted'>–</p>"}
   <h3 class="small" style="font-size:var(--fs-base)">Consent</h3><p class="small">${C.consents.filter(c => c.status === "active").map(c => `<span class="chip ${c.decision === "permit" ? "sla-green" : "sla-red"}">${c.decision === "permit" ? "✓" : "✕"} ${W.esc(c.scope.replace(/_/g, " "))}: ${c.decision === "permit" ? "yes" : "no"}</span>`).join(" ") || "–"}</p></section>
   <section class="card flat" aria-labelledby="ml-h"><h2 id="ml-h" class="small" style="font-size:var(--fs-base)">Outreach priority <span class="muted xs">(model v${r.model_version ?? "–"}, ordering only)</span></h2>
   <p style="margin-bottom:var(--s-2)"><b>${r.score == null ? "–" : Math.round(r.score * 100) + "%"}</b> <span class="small muted">chance of missing the next step</span></p>
   ${r.score == null ? "" : `<details class="small"><summary>Why this estimate</summary>${(r.rationale || []).length ? `<ul>${r.rationale.map(w => `<li>${W.esc(w.text)} – <b>${w.direction}</b> the chance (${w.effect > 0 ? "+" : ""}${Math.round(w.effect * 100)} points)</li>`).join("")}</ul>` : "<p>No single factor stands out.</p>"}<p class="muted">${W.esc(r.ordering)}. ${W.esc(r.use)}.</p></details>`}</section>`;
}

function render() {
  const acts = C.actions, primary = acts.find(a => a.primary) || null, others = acts.filter(a => a !== primary);
  root().innerHTML = header() + `<div class="grid-2 case-grid"><div>
    <div class="tabs" role="tablist" aria-label="Case details"><button role="tab" aria-selected="true" data-t="steps">Timeline</button><button role="tab" aria-selected="false" class="secondary" data-t="events">Messages &amp; events</button><button role="tab" aria-selected="false" class="secondary" data-t="audit">Audit (${C.audit.length})</button></div>
    <div id="tabbody" role="tabpanel"></div></div>
    <aside aria-label="Actions and patient details"><section class="card ${primary ? "next-step" : ""}" aria-labelledby="act-h"><h2 id="act-h" style="font-size:var(--fs-lg)">${primary ? "Next step" : "Actions"}</h2>
      ${primary ? `<button class="primary-action" data-k="${primary.key}" data-ev="${primary.evidence_type || ""}">${W.esc(primary.label)}</button>` : ""}
      ${others.length ? (primary ? `<details class="more-actions"><summary>More actions (${others.length})</summary><div class="actions">${others.map(btn).join("")}</div></details>` : `<div class="actions">${others.map(btn).join("")}</div>`) : (primary ? "" : `<p class="small muted">There is nothing for your role to do on this case right now.</p>`)}
      <div id="aform"></div></section>${patientCard()}</aside></div>`;
  const tabs = { steps, events, audit: auditTable };
  root().querySelectorAll(".tabs button").forEach(b => b.onclick = () => {
    root().querySelectorAll(".tabs button").forEach(x => { x.className = "secondary"; x.setAttribute("aria-selected", "false"); }); b.className = ""; b.setAttribute("aria-selected", "true");
    document.getElementById("tabbody").innerHTML = tabs[b.dataset.t]();
  });
  document.getElementById("tabbody").innerHTML = steps();
  root().querySelectorAll("[data-k]").forEach(b => b.onclick = () => openForm(b.dataset.k, b.dataset.ev));
}
const btn = a => `<button class="secondary" data-k="${a.key}" data-ev="${a.evidence_type || ""}">${W.esc(a.label)}</button>`;

async function postRaw(path, body, okMsg, key) {
  if (key && RISKY[key] && !(await W.confirm(RISKY[key], "This is recorded in the case history with your name and cannot be silently undone.", { ok: "Yes, continue", danger: /close|deceased|decline|unreachable/.test(key) }))) return;
  try { const r = await W.api(path, { method: "POST", body }); W.toast(okMsg || "Saved", "ok"); await load(); return r; }
  catch (e) { W.toast(e.message, "err"); }
}
const post = (p, b, m) => postRaw(p, b, m);
window.respond = id => post(`/api/consults/${id}/respond`, { response: document.getElementById("resp-" + id).value }, "Answer sent");
window.ackEsc = async id => { const note = await W.ask("Acknowledge escalation", "What action are you taking?", { ok: "Acknowledge", min: 3 }); if (note) post(`/api/escalations/${id}/ack`, { note }, "Escalation acknowledged"); };

function events() {
  const ev = [];
  C.communications.forEach(m => ev.push([m.sent_at, `<b>${W.esc(m.category)}</b> via ${W.esc(m.channel)} → ${W.esc(m.to)} <span class="chip ${m.status === "completed" ? "sla-green" : m.status === "failed" ? "sla-red" : "sla-none"}">${W.esc(m.status)}</span> ${m.language ? `<span class="small muted">${m.language}${m.variant ? "/" + m.variant : ""}</span>` : ""}<div class="small muted">${W.esc(m.content).slice(0, 260)}</div>`]));
  C.call_tasks.forEach(t => ev.push([t.completed_at || t.created_at, `📞 phone-call task (${W.esc(t.owner)}) – ${t.status}${t.outcome ? ": <b>" + W.esc(t.outcome) + "</b>" : ""}`]));
  C.teach_back.forEach(t => ev.push([t.at, `✅ teach-back (${t.mode}, ${t.language}): ${t.score}/${t.total} ${t.passed ? '<span class="chip sla-green">understood</span>' : '<span class="chip sla-red">not yet clear</span>'}${t.confidence ? " · confidence " + t.confidence + "/5" : ""}`]));
  C.service_requests.forEach(s => ev.push([s.decided_at || s.created_at, `📄 ${s.kind.replace("_", " ")} → ${W.esc(s.performer_org)} <span class="chip sla-none">${s.status}</span>${s.result ? " result <b>" + s.result + "</b>" : ""}${s.reason_code ? ` reason <b>${W.esc(s.reason_code)}</b>: ${W.esc(s.reason_note || "")}` : ""}`]));
  C.appointments.forEach(a => ev.push([a.start, `📅 appointment ${W.dt(a.start)} at ${W.esc(a.org)} <span class="chip ${a.status === "fulfilled" ? "sla-green" : a.status === "noshow" ? "sla-red" : "sla-none"}">${a.status}</span>${a.barrier_code ? " barrier: " + W.esc(a.barrier_code) : ""}`]));
  C.care_plans.forEach(p => ev.push([p.created_at, `🩺 ${p.kind} plan by ${W.esc(p.by)}: ${W.esc(p.description)}`]));
  C.consults.forEach(q => ev.push([q.created_at, `💬 ${q.type === "patient" ? "patient-to-clinician" : "clinician-to-clinician"} consult → ${W.esc(q.target)} (${q.urgency}) <span class="chip ${q.status === "answered" ? "sla-green" : q.overdue ? "sla-red" : "sla-amber"}">${q.status}${q.overdue ? " · overdue" : ""}</span><div class="small">Q: ${W.esc(q.question)}</div>${q.response ? `<div class="small">A: ${W.esc(q.response)}</div>` : ""}${q.status === "requested" && W.me.practitioner_id === q.target_id ? `<div><label for="resp-${q.id}" class="sr-only">Your answer</label><textarea id="resp-${q.id}" rows="2" placeholder="Your answer…"></textarea><button class="secondary sm" onclick="respond(${q.id})">Send answer</button></div>` : ""}`]));
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
  const post = (p, b, m) => postRaw(p, b, m, key);
  const form = (html, onsubmit, submit = "Confirm") => {
    el.innerHTML = `<form class="action-form" aria-label="${W.esc(TITLE[key] || "Action")}"><h3>${W.esc(TITLE[key] || "Action")}</h3>${html}<div class="form-buttons"><button type="submit">${submit}</button><button type="button" class="secondary" id="cancel">Cancel</button></div></form>`;
    el.querySelector("#cancel").onclick = () => { el.innerHTML = ""; };
    const fm = el.querySelector("form"); const first = fm.querySelector("select, input:not([type=hidden]), textarea"); if (first) first.focus(); fm.scrollIntoView({ block: "nearest", behavior: "smooth" });
    fm.onsubmit = async ev => { ev.preventDefault(); const b = fm.querySelector("button[type=submit]"); b.setAttribute("aria-busy", "true"); b.disabled = true; const fd = Object.fromEntries(new FormData(ev.target)); await onsubmit(fd); b.removeAttribute("aria-busy"); b.disabled = false; };
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
        `</div>` + F.sel("confidence", "Patient confidence (1–5)", kv({ 3: "3", 1: "1", 2: "2", 4: "4", 5: "5" })),
        fd => post(`${u}/teach-back`, { answers: { q1: fd.q1, q2: fd.q2, q3: fd.q3 }, confidence: +fd.confidence, language: lang }, "Teach-back recorded"));
    }
    case "order_confirmatory": return form(F.sel("org", "Performing organisation", opts(d.orgs.filter(o => ["lab", "hospital"].includes(o.kind)))),
      fd => post(`${u}/confirmatory`, { performer_org_id: +fd.org }, "Confirmatory test ordered"));
    case "confirm_result": return form(F.sel("result", "Confirmatory result", kv({ positive: "Positive – needs consultation", negative: "Negative" })),
      fd => post(`${u}/confirmatory/result`, { result: fd.result }, "Result recorded"));
    case "refer": {
      const blocked = C.actions.find(a => a.key === "refer").consent_blocked;
      const hosp = d.orgs.filter(o => o.kind === "hospital");
      return form((blocked ? `<p class="small" class="notice-inline err">Patient has <b>declined</b> sharing with other organisations. Record their consent first (Record consent → share_external).</p>` : "") +
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
    case "start_care": return form(F.sel("kind", "Plan type", kv({ treatment: "Treatment", surveillance: "Surveillance" })) + `<label>Plan description<textarea name="description" rows="2" required></textarea></label>`,
      fd => post(`${u}/care-plan`, fd, "Care plan recorded"));
    case "close_evidence": return form(`<p class="small">Closure with linked evidence: <b>${W.esc(evidenceType)}</b>. The system verifies the evidence exists for this case.</p>` + F.note(),
      fd => post(`${u}/close`, { closure_type: "evidence", evidence_type: evidenceType, note: fd.note || null }, "Case closed with evidence"), "Close case");
    case "close_authorised": return form(`<p class="small">Authorised human decision – needs a reason code and a rationale (≥10 characters). Some reasons need supporting records (e.g. informed decline, full contact ladder).</p>` +
      F.sel("reason", "Reason", kv(META.closure_reasons)) + F.note("Rationale", true), fd => post(`${u}/close`, { closure_type: "authorised_decision", reason: fd.reason, note: fd.note }, "Case closed (authorised decision)"), "Sign off closure");
    case "decline": return form(F.note("What did the patient say? (≥10 characters, recorded with your name; informed decline needs a clinician conversation)", true), fd => post(`${u}/decline`, { note: fd.note }, "Decline recorded – clinician review"));
    case "patient_deceased": return form(`<p class="small" class="notice-inline err">Records the death for <b>this patient</b>: all messages and call tasks stop and every open case goes to clinical review for an authorised closure. Check the identity first.</p>` +
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
