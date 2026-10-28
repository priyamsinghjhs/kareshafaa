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

function facts(pairs) { return `<dl class="facts">${pairs.map(([k, x, wide]) => `<div${wide ? ' class="wide"' : ""}><dt>${W.esc(k)}</dt><dd>${x}</dd></div>`).join("")}</dl>`; }

function editable(id, title, view, formHtml) {
  const open = OPEN.has(id) && R.can_write;
  return `<section class="card pr-sec" id="sec-${id}"><div class="row between"><h2 class="sec-title" style="margin:0">${W.esc(title)}</h2>${R.can_write ? `<button class="secondary sm" data-edit="${id}" aria-expanded="${open}">${open ? "Close" : "Edit"}</button>` : ""}</div>
    ${open ? `<form class="action-form" data-form="${id}" novalidate aria-label="Edit ${W.esc(title)}">${formHtml}<div class="form-buttons"><button type="submit">Save changes</button><button type="button" class="secondary" data-cancel="${id}">Cancel</button></div></form>` : view}</section>`;
}

function render() {
  const D = R.demographics, C = R.contact, K = R.care, O = PF.opts;
  const gender = PF.label(O.genders, D.gender), lang = PF.label(O.languages, D.language);
  const draft = id => ({ ...(id === "demographics" ? D : id === "contact" ? C : K), ...(DRAFT[id] || {}) });
  const head = `<a class="back-link" href="/patients"><svg class="icon" aria-hidden="true"><use href="#i-back"/></svg>Back to patients</a>
    <div class="case-head"><h1>${W.esc(D.given_name)} ${W.esc(D.family_name)}</h1>
    <p class="muted small" style="margin:0">${W.esc(R.mrn)} · ${D.age_estimated ? "about " : ""}${D.age} years · ${W.esc(gender)}${R.sample ? ' · <span class="chip">Sample record</span>' : ""}${R.deceased ? ' · <span class="chip sla-none">Deceased</span>' : ""}</p></div>
    ${new URLSearchParams(location.search).get("registered") ? `<div class="card ok" role="status">Patient registered. Next: invite them to the patient app, or add a screening result.</div>` : ""}`;
  const personal = editable("demographics", "Personal details", facts([["Name", W.esc(D.given_name + " " + D.family_name)], ["Date of birth", D.birth_date ? dday(D.birth_date) : (D.birth_year ? W.esc(D.birth_year) + " (year only)" : NR)],
      ["Sex", W.esc(gender)], ["Emirates ID", v(D.national_id)], ["Nationality", v(D.nationality || D.nationality_group)], ["Preferred language", W.esc(lang)]]), PF.demographics(draft("demographics")));
  const chan = [C.has_smartphone ? "App" : "", C.whatsapp ? "WhatsApp" : "", C.phone_valid ? "SMS / phone" : ""].filter(Boolean).join(", ");
  const contact = editable("contact", "Contact details", facts([["Mobile phone", v(C.phone)], ["Email", v(C.email)], ["Preferred contact", C.preferred_channel ? W.esc(PF.label(O.channels, C.preferred_channel)) : '<span class="muted">No preference</span>'],
      ["Can be reached by", chan ? W.esc(chan) : '<b>No valid phone or app</b>'], ["Address", v([C.address_line, C.city].filter(Boolean).join(", ")), true], ["Emirate / region", v([C.emirate, C.region && C.region !== C.emirate ? C.region : ""].filter(Boolean).join(" · "))]]), PF.contact(draft("contact")));
  const care = editable("care", "Health centre and family doctor", facts([["Registered health centre", v(K.managing_org)], ["Family doctor (GP)", v(K.gp)],
      ["Registered", R.registration.at ? `${dday(R.registration.at)}<span class="sub small muted">by ${W.esc(R.registration.by || "–")}${R.registration.source === "result_import" ? " · from a lab result" : ""}</span>` : '<span class="muted">Before registration was recorded</span>'],
      ...(R.registration.duplicate_override_reason ? [["Registered despite a similar record", W.esc(R.registration.duplicate_override_reason), true]] : [])]), PF.care(draft("care")));
  const cases = `<section class="card"><h2 class="sec-title">Screening cases</h2>${R.cases.length ? `<table class="compact"><thead><tr><th>Case</th><th>Programme</th><th>Step</th><th>Opened</th><th>Closed</th></tr></thead><tbody>${R.cases.map(c => `<tr><td>${c.can_open ? `<a href="/cases/${c.id}">Case #${c.id}</a>` : `#${c.id}`}</td><td>${W.prog(c.programme)}</td><td>${W.esc(c.state_label)}</td><td>${dday(c.created_at)}</td><td>${c.closed_at ? dday(c.closed_at) + (c.closure_reason ? `<span class="sub small muted">${W.esc(c.closure_reason.replace(/_/g, " "))}</span>` : "") : "–"}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted">No screening cases yet. A case opens automatically when a result needs follow-up.</p>`}</section>`;
  const results = `<section class="card"><h2 class="sec-title">Results</h2>${R.results.length ? `<table class="compact"><thead><tr><th>Date</th><th>Test</th><th>Result</th><th>Conclusion</th><th>Case</th></tr></thead><tbody>${R.results.map(r => `<tr><td>${dday(r.issued_at)}</td><td>${W.esc(r.display)}${r.ordered_by || r.site ? `<span class="sub small muted">${[r.ordered_by ? "Ordered by " + W.esc(r.ordered_by) : "", r.site ? "at " + W.esc(r.site) : "", r.collected_at ? "collected " + dday(r.collected_at) : ""].filter(Boolean).join(" · ")}</span>` : ""}</td><td>${v(r.values)}</td><td><span class="chip ${r.conclusion === "negative" ? "sla-green" : "sla-amber"}">${W.esc(r.conclusion.replace(/_/g, " "))}</span></td><td>${r.case_id ? `<a href="/cases/${r.case_id}">#${r.case_id}</a>` : "–"}</td></tr>`).join("")}</tbody></table>` : `<p class="small muted">No results on this record yet.</p>`}</section>`;
  const consents = `<section class="card"><h2 class="sec-title">Consent</h2>${R.consents.length ? `<p class="small">${R.consents.map(c => `<span class="chip ${c.decision === "permit" ? "sla-green" : "sla-red"}">${c.decision === "permit" ? "✓" : "✕"} ${W.esc(c.scope.replace(/_/g, " "))}</span>`).join(" ")}</p>` : `<p class="small muted">No consent decisions recorded yet.</p>`}</section>`;
  const changes = `<section class="card flat"><h2 class="sec-title">Changes to this record</h2>${R.changes.length ? `<ul class="timeline">${R.changes.map(c => `<li><span class="small muted">${W.dt(c.at)}</span> <b>${W.esc(c.summary)}</b> by ${W.esc(c.by)}${c.role === "patient" ? ' <span class="chip sla-amber">by the patient</span>' : ""}${c.fields.length ? `<div class="small muted">Fields: ${W.esc(c.fields.join(", ").replace(/_/g, " "))}</div>` : ""}</li>`).join("")}</ul>` : `<p class="small muted">No changes recorded.</p>`}</section>`;
  const extra = EXTRA_SECTIONS.map(s => s.render(R)).join("");
  pr().innerHTML = head + `<div class="grid-2 pr-grid"><div>${personal}${contact}${care}${extra}</div><div>${cases}${results}${consents}${changes}</div></div>`;
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
}

async function load() { R = await W.api(`/api/patients/${PID}`); render(); }
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  try { await PF.options(); await load(); } catch (e) { pr().innerHTML = W.empty("Could not open this record", W.esc(e.message), '<a role="button" href="/patients">Back to patients</a>'); } });
