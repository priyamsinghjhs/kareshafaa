// queue.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
const VIEWS = [["all", "All open cases"], ["critical", "Critical: read-back needed"], ["overdue", "Overdue"], ["review", "Needs clinical review"], ["mine", "Assigned to me"], ["unassigned", "No named owner"],
  ["escalated", "Escalated"], ["rejected", "Referral rejected"], ["calls", "Phone calls to make"], ["transfers", "Transfers waiting"], ["consults", "Open consultations"]];
const TITLES = { all: "All open cases", review: "Patients needing review", mine: "Assigned to me", overdue: "Overdue steps" };
let view = new URLSearchParams(location.search).get("view") || "all", data = null, offset = 0, timer = null;
const PAGE = 25;
const EMPTY = { all: ["No open cases", "Every result has reached care or a documented closure."], mine: ["Nothing assigned to you", "No cases have you as the named owner right now."],
  overdue: ["Nothing overdue", "Every step is inside its deadline."], unassigned: ["Every case has an owner", "No case is waiting in a team pool."],
  rejected: ["No rejected referrals", "Nothing is waiting to be re-routed."], escalated: ["No open escalations", "Every escalation has been acknowledged."],
  review: ["No one needs review", "No results are waiting for a clinician. <a href='/queue'>See all cases</a>."], calls: ["No calls to make", "No phone-call tasks are waiting."],
  transfers: ["No transfers waiting", "No hand-overs are pending."], critical: ["No critical result is waiting", "Every critical result has a recorded clinician read-back."], consults: ["No open consultations", "No questions are waiting for an answer."] };
function stats() {
  const c = data.counts, S = [...((c.critical || 0) || view === "critical" ? [["critical", "Critical: read-back needed", "danger"]] : []), ["overdue", "Overdue", "danger"], ["review", "Need review", "warning"], ["unassigned", "No named owner", ""], ["escalated", "Escalated", "danger"]];
  document.getElementById("stats").innerHTML = S.map(([k, l, cls]) => `<a class="stat ${(c[k] || 0) ? cls : "ok"}" href="?view=${k}" data-v="${k}" ${k === view ? 'aria-current="true"' : ""}><span class="v">${c[k] ?? 0}</span><span class="l">${l}</span></a>`).join("");
  document.querySelectorAll("#stats a").forEach(a => a.onclick = e => { e.preventDefault(); setView(a.dataset.v); });
  const sel = document.getElementById("view");
  sel.innerHTML = VIEWS.map(([k, l]) => `<option value="${k}" ${k === view ? "selected" : ""}>${l} (${c[k] ?? 0})</option>`).join("");
}
function setView(v) { view = v; offset = 0; load(); }
async function load() {
  const prog = document.getElementById("prog").value, q = document.getElementById("search").value.trim();
  // keep the chosen view, programme and search in the address, so Back / reload shows the same list
  const qs = new URLSearchParams({ view }); if (prog) qs.set("programme", prog); if (q) qs.set("q", q);
  history.replaceState(null, "", "?" + qs.toString());
  data = await W.api(`/api/queue?view=${view}&limit=${PAGE}&offset=${offset}${prog ? "&programme=" + prog : ""}${q ? "&q=" + encodeURIComponent(q) : ""}`);
  document.getElementById("all-h").textContent = TITLES[view] || (VIEWS.find(v => v[0] === view) || [, "Work queue"])[1];
  stats(); render();
}
function flags(r) {
  return [r.read_back_needed ? '<span class="chip sla-red" title="Critical result: record the direct clinician contact and read-back">Critical: read-back needed</span>' : "",
    r.review_required ? `<span class="chip sla-red" title="${W.esc((r.review_reasons || []).join(", "))}">Needs review</span>` : "",
    r.escalation_level ? `<span class="chip sla-red" title="Escalation level ${r.escalation_level}">Escalated L${r.escalation_level}</span>` : "",
    r.call_task ? '<span class="chip flag">Call due</span>' : "", r.open_consult ? '<span class="chip flag">Question open</span>' : "",
    r.patient_change_request ? '<span class="chip flag" title="The patient asked to change the appointment (it has not moved)">Patient asks to change appointment</span>' : "",
    r.transfer_pending ? '<span class="chip flag">Transfer</span>' : ""].join(" ");
}
// "Needs you now": the 5 most urgent open cases in this person's scope, whatever list is chosen below
async function loadNow() {
  const d = await W.api("/api/queue?view=all&limit=5"), el = document.getElementById("now-list"), c = d.counts || {};
  document.getElementById("now-count").textContent = d.total ? `${c.overdue || 0} overdue · ${d.total} open` : "";
  el.innerHTML = d.items.length ? W.nowList(d.items) : W.empty("You are all caught up", "Nothing needs you right now. New results and requests appear here.");
}
function render() {
  const rows = data.items;
  if (!rows.length) { const e = EMPTY[view] || ["Nothing here", ""];
    document.getElementById("qtable").innerHTML = document.getElementById("search").value ? W.empty("No match", "No open case matches your search.") : W.empty(e[0], e[1]); return; }
  const from = data.offset + 1, to = data.offset + rows.length;
  const risk = r => r.risk_score == null ? '<span class="muted small">–</span>' : `<span class="risk" title="${W.esc("Why: " + (r.risk_rationale || "close to the typical case") + " - orders within the same urgency only")}"><span class="risk-bar" aria-hidden="true"><i style="width:${Math.round(r.risk_score * 100)}%"></i></span>${Math.round(r.risk_score * 100)}%</span>`;
  const owner = r => `${r.owner_is_pool ? '<span class="chip pool">Team pool</span> ' : ""}${W.esc(r.owner.split(" (")[0])}`;
  document.getElementById("qtable").innerHTML = `<div class="table-scroll q-table"><table class="striped"><caption class="sr-only">Open cases, most urgent first</caption><thead><tr><th>Urgency</th><th class="w-patient">Patient</th><th>Screening</th><th>Next step</th><th>Deadline</th><th>Owner</th><th title="Model estimate of the chance of missing the next step - orders outreach within the same urgency only">Outreach priority</th></tr></thead><tbody>` +
    rows.map(r => `<tr class="clickable" data-href="/cases/${r.id}">
      <td>${W.tier(r.tier)}</td>
      <td><a href="/cases/${r.id}"><b>${W.esc(r.patient)}</b></a><span class="sub">#${r.id} · ${W.esc(r.mrn)} · speaks ${W.esc(W.langName(r.language))}</span></td>
      <td>${W.prog(r.programme)}</td><td>${W.esc(r.state_label)}<div class="chips">${flags(r)}</div></td>
      <td>${W.sla(r.sla_status, r.sla_fraction)}<span class="sub">${W.leftHtml(r.due_at, r.sla_status)}</span></td>
      <td class="small">${owner(r)}</td><td>${risk(r)}</td></tr>`).join("") +
    `</tbody></table></div><ul class="q-cards" aria-label="Open cases, most urgent first">${rows.map(r => `<li><a class="q-card" href="/cases/${r.id}">
      <span class="chips">${W.tier(r.tier)} ${W.sla(r.sla_status, r.sla_fraction)} ${flags(r)}</span>
      <span class="q-name">${W.esc(r.patient)}</span>
      <span class="q-line">${W.progText(r.programme)} · next: <b>${W.esc(r.state_label)}</b></span>
      <span class="q-line">${W.leftHtml(r.due_at, r.sla_status)} · ${owner(r)}${r.risk_score == null ? "" : ` · priority ${Math.round(r.risk_score * 100)}%`}</span></a></li>`).join("")}</ul>` +
    `<nav class="pager" aria-label="Queue pages"><span class="small muted">Showing ${from}–${to} of ${data.total}</span><span class="btns">
     <button class="secondary" ${data.offset ? "" : "disabled"} data-click="page" data-args="[-1]">Previous</button>
     <button class="secondary" ${to < data.total ? "" : "disabled"} data-click="page" data-args="[1]">Next</button></span></nav>`;
  document.querySelectorAll("tr.clickable").forEach(tr => tr.onclick = e => { if (!e.target.closest("a,button")) location.href = tr.dataset.href; });
}
// ---- patient appointment requests (coordinators confirm or decline; other roles do not see this panel)
const RQ_KIND = { screening: "Screening", recall: "Next screening (recall due)", follow_up: "Follow-up time" };
const RQ_DAY = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri" }, RQ_TIME = { morning: "morning", afternoon: "afternoon", evening: "evening" };
const RQ_BARRIER = { work: "work or study", transport: "transport", childcare: "childcare or caring", language: "language", cost: "cost", worry: "worried about the test", other: "other" };
const RQ_DECLINE = { no_capacity: "No appointments at that site or time", not_due: "Not due yet", already_booked: "Already booked", clinical_review: "Needs a clinician to review first", other: "Other reason" };
let RQ = null;
const localInput = iso => { if (!iso) return ""; const d = new Date(iso.endsWith("Z") ? iso : iso + "Z"); return new Date(d.getTime() + 4 * 36e5).toISOString().slice(0, 16); };
async function loadRequests() {
  const box = document.getElementById("preq");
  if (!W.me || W.me.role !== "coordinator") return;
  RQ = await W.api("/api/patient-requests?status=open");
  box.hidden = false;
  document.getElementById("preq-count").textContent = RQ.open ? `${RQ.open} waiting${RQ.overdue ? ` · ${RQ.overdue} past answer-by time` : ""}` : "none waiting";
  const L = document.getElementById("preq-list");
  if (!RQ.items.length) { L.innerHTML = '<p class="small muted" data-preq-empty>No requests are waiting.</p>'; box.hidden = true; return; }  // nothing to do: keep the page short
  const asked = r => r.chosen_start ? `Chose <b>${W.esc(W.dt(r.chosen_start))}</b>` : r.kind === "follow_up"
    ? `Asked for another time${r.barrier_code ? ` (${W.esc(RQ_BARRIER[r.barrier_code] || r.barrier_code)})` : ""}${r.preferred_text ? `: “${W.esc(r.preferred_text)}”` : ""}`
    : `Prefers ${W.esc(r.days.map(d => RQ_DAY[d]).join(", ") || "any day")}, ${W.esc(r.times.map(t => RQ_TIME[t]).join(", ") || "any time")}${r.site ? ` at ${W.esc(r.site)}` : ""}`;
  L.innerHTML = `<ul class="preq-list">${RQ.items.map(r => `<li class="preq-item" data-request="${r.id}" data-kind="${r.kind}">
    <div class="chips">${r.kind === "follow_up" ? W.tier(r.tier) : `<span class="chip">${W.esc(r.tier_label)}</span>`} ${W.sla(r.rag, r.elapsed)} <span class="small muted">reply due ${W.ago(r.due_at)}</span></div>
    <div><b>${W.esc(r.patient.name)}</b> <span class="sub">${W.esc(r.patient.mrn)} · speaks ${W.esc(W.langName(r.patient.language))}${r.case_id ? ` · <a href="/cases/${r.case_id}">case #${r.case_id}</a>` : ""}</span></div>
    <div class="small">${W.esc(RQ_KIND[r.kind])} · ${W.progText(r.programme)}</div><div class="small">${asked(r)}</div>
    ${r.note ? `<div class="small muted">Patient note: ${W.esc(r.note)}</div>` : ""}
    <div class="tb-actions"><button type="button" class="sm" data-rq-confirm="${r.id}">Confirm…</button><button type="button" class="secondary outline sm" data-rq-decline="${r.id}">Decline…</button></div></li>`).join("")}</ul>`;
  L.querySelectorAll("[data-rq-confirm]").forEach(b => b.onclick = () => openRq(+b.dataset.rqConfirm, "confirm"));
  L.querySelectorAll("[data-rq-decline]").forEach(b => b.onclick = () => openRq(+b.dataset.rqDecline, "decline"));
}
async function loadResets() {
  if (!W.me || W.me.role !== "coordinator") return;
  const R = await W.api("/api/signin-help?audience=patient&status=open"), box = document.getElementById("preset");
  box.hidden = !R.items.length;  // shown only when a patient is waiting
  document.getElementById("preset-count").textContent = R.open ? `${R.open} waiting${R.overdue ? ` · ${R.overdue} past reply-due time` : ""}` : "none waiting";
  const L = document.getElementById("preset-list");
  L.innerHTML = R.items.length ? `<ul class="preq-list">${R.items.map(r => `<li class="preq-item" data-reset-request="${r.id}">
    <div class="chips">${W.sla(r.rag, r.elapsed)} <span class="small muted">reply due ${W.ago(r.due_at)}</span></div>
    <div><b>${W.esc(r.patient ? r.patient.name : "–")}</b> <span class="sub">${W.esc(r.patient ? r.patient.mrn : "")} · asked ${W.dt(r.created_at)} · page in ${W.esc(r.language || "en")}</span></div>
    <div class="tb-actions"><a role="button" class="sm" href="/patients/${r.patient ? r.patient.id : ""}#sec-account">Open the patient record…</a></div></li>`).join("")}</ul>`
    : '<p class="small muted" data-preset-empty>No password resets are waiting.</p>';
}
function openRq(id, mode) {
  const r = RQ.items.find(x => x.id === id), dlg = document.getElementById("preq-dlg"), f = document.getElementById("preq-form");
  const err = f.querySelector(".form-error"); err.hidden = true; err.textContent = "";
  document.getElementById("preq-dlg-h").textContent = `${mode === "confirm" ? "Confirm" : "Decline"}: ${r.patient.name} · ${RQ_KIND[r.kind]}`;
  const sites = RQ.sites.map(o => `<option value="${o.id}" ${o.id === r.preferred_org_id ? "selected" : ""}>${W.esc(o.name)}${o.region ? " – " + W.esc(o.region) : ""}</option>`).join("");
  let body;
  if (mode === "confirm") {
    body = r.kind === "follow_up" && r.chosen_start ? `<p>Book the time the patient chose: <b>${W.esc(W.dt(r.chosen_start))}</b>.</p>`
      : `<label>Appointment date and time (UAE)<input type="datetime-local" name="start" required value="${localInput(r.chosen_start)}"></label>` +
        (r.kind === "follow_up" ? `<label>If this is after the deadline, why? <input name="beyond" maxlength="500"></label>` : `<label>Screening site<select name="org" required>${sites}</select></label>`);
    body += `<label>Note (optional)<input name="note" maxlength="1000"></label><p class="small muted">The patient gets a confirmation message in their language.</p>`;
    document.getElementById("preq-ok").textContent = r.kind === "follow_up" ? "Confirm and book" : "Confirm";
    document.getElementById("preq-ok").className = "";
  } else {
    body = `<label>Reason<select name="reason" required><option value="">Choose…</option>${(Array.isArray(RQ.decline_reasons) ? RQ.decline_reasons : Object.keys(RQ.decline_reasons)).map(k => `<option value="${k}">${W.esc(RQ_DECLINE[k] || k)}</option>`).join("")}</select></label>
      <label>What happens next (at least 5 characters) <textarea name="note" rows="3" maxlength="1000" required></textarea></label><p class="small muted">The patient is told the reason in their language.</p>`;
    document.getElementById("preq-ok").textContent = "Decline request";
    document.getElementById("preq-ok").className = "danger";
  }
  document.getElementById("preq-dlg-body").innerHTML = body;
  document.getElementById("preq-cancel").onclick = () => dlg.close();
  f.onsubmit = async ev => {
    ev.preventDefault(); const g = n => f.querySelector(`[name="${n}"]`), bad = [];
    f.querySelectorAll("[aria-invalid]").forEach(x => x.removeAttribute("aria-invalid"));
    const mark = (n, m) => { const el = g(n); if (el) el.setAttribute("aria-invalid", "true"); bad.push(m); };
    let url, payload;
    if (mode === "confirm") {
      payload = { note: g("note").value.trim() || null };
      if (g("start")) { if (!g("start").value) mark("start", "Choose the appointment date and time."); else payload.start = g("start").value + ":00+04:00"; }
      if (g("org")) { if (!g("org").value) mark("org", "Choose the screening site."); else payload.org_id = +g("org").value; }
      if (g("beyond") && g("beyond").value.trim()) payload.beyond_target_reason = g("beyond").value.trim();
      url = `/api/patient-requests/${id}/confirm`;
    } else {
      if (!g("reason").value) mark("reason", "Choose a reason.");
      if (g("note").value.trim().length < 5) mark("note", "Say what happens next (at least 5 characters); the patient is told the reason.");
      payload = { reason_code: g("reason").value, note: g("note").value.trim() }; url = `/api/patient-requests/${id}/decline`;
    }
    if (bad.length) { err.textContent = bad.join(" "); err.hidden = false; f.querySelector('[aria-invalid="true"]').focus(); return; }
    const b = document.getElementById("preq-ok"); W.busy(b, true);
    try { await W.api(url, { method: "POST", body: payload }); dlg.close(); W.toast(mode === "confirm" ? "Request confirmed; the patient has been told." : "Request declined; the patient has been told why.", "ok"); await loadRequests(); }
    catch (e) { err.textContent = e.message; err.hidden = false; } finally { W.busy(b, false); }
  };
  dlg.showModal(); const first = f.querySelector("input, select, textarea"); (first || document.getElementById("preq-ok")).focus();
}
window.page = d => { offset = Math.max(0, offset + d * PAGE); load().then(() => document.getElementById("all-h").scrollIntoView()); };
document.addEventListener("DOMContentLoaded", async () => {
  await W.ready; if (!W.requireLogin()) return;
  const qs0 = new URLSearchParams(location.search);
  if (qs0.get("programme")) document.getElementById("prog").value = qs0.get("programme");
  if (qs0.get("q")) document.getElementById("search").value = qs0.get("q");
  document.getElementById("search").oninput = () => { clearTimeout(timer); timer = setTimeout(() => { offset = 0; load(); }, 250); };
  document.getElementById("prog").onchange = () => { offset = 0; load(); };
  document.getElementById("view").onchange = e => setView(e.target.value);
  loadResets().catch(e => { document.getElementById("preset").hidden = false; document.getElementById("preset-list").innerHTML = W.empty("Could not load password reset requests", W.esc(e.message)); });
  loadRequests().catch(e => { const L = document.getElementById("preq-list"); document.getElementById("preq").hidden = false; L.innerHTML = W.empty("Could not load patient requests", W.esc(e.message)); });
  loadNow().catch(e => { document.getElementById("now-list").innerHTML = W.empty("Could not load your list", W.esc(e.message), '<button data-click="reloadPage">Try again</button>'); });
  try { await load(); } catch (e) { document.getElementById("qtable").innerHTML = W.empty("Could not load the queue", W.esc(e.message), '<button data-click="reloadPage">Try again</button>'); }
});
