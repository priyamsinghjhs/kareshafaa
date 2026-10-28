const KLABEL = { median_days_to_notification: "Median days to notification", median_days_to_acknowledgement: "Median days to understood (acknowledged)",
  median_days_to_confirmation: "Median days to confirmation", median_days_to_consultation: "Median days to consultation", median_days_to_care: "Median days to care",
  pct_notified: "% notified", pct_understood: "% understood (teach-back)", pct_referral_completed: "% referrals completed", pct_appointment_attended: "% appointments attended",
  pct_care_reached_or_resolved: "% treatment / surveillance started or resolved", pct_documented_closure: "% documented closure", pct_open_unresolved_at_horizon: "% open & unresolved at day 120",
  mean_confidence_1to5: "Mean patient confidence (1–5)" };
const tabs = {
  async sim(el) {
    const s = await W.api("/api/analytics/simulation");
    if (!s.available) { el.innerHTML = `<div class="empty">No simulation run yet.</div>`; return; }
    const k = Object.fromEntries(s.kpis.map(x => [x.kpi, x]));
    const pick = keys => keys.filter(x => k[x]).map(x => ({ label: KLABEL[x] || x, a: k[x].baseline, b: k[x].with_system }));
    el.innerHTML = `<div class="card notice"><b>${W.esc(s.label)}</b> · ${s.n_patients} modelled patients × ${s.replications} replications, ${s.horizon_days}-day horizon, same cohort and random stream in both arms. Ranges are min–max across replications.</div>
     <div class="grid-4">${["median_days_to_care", "pct_care_reached_or_resolved", "pct_documented_closure", "pct_open_unresolved_at_horizon"].map(x => `<div class="card kpi"><div class="l">${KLABEL[x]}</div><div class="v">${k[x].baseline} → ${k[x].with_system}</div><div class="l">baseline → with KareShafaa</div></div>`).join("")}</div>
     <div class="grid-2"><div class="card"><h4>Time to each step (median days, lower is better)</h4><div class="bars" id="b-time"></div></div>
     <div class="card"><h4>Completion (% of cohort, higher is better)</h4><div class="bars" id="b-pct"></div></div></div>
     <div class="grid-2"><div class="card"><h4>Care reached by language (equity, simulated)</h4><div class="bars" id="b-lang"></div></div>
     <div class="card"><h4>Why cases stay open at day 120 (simulated)</h4><div class="bars" id="b-reasons"></div></div></div>
     <div class="card"><h4>All KPIs</h4><table class="compact"><thead><tr><th>KPI</th><th>Baseline (range)</th><th>With KareShafaa (range)</th></tr></thead><tbody>${s.kpis.map(x => `<tr><td>${KLABEL[x.kpi] || x.kpi}</td><td>${x.baseline} <span class="small muted">(${x.baseline_range ? x.baseline_range.join("–") : ""})</span></td><td><b>${x.with_system}</b> <span class="small muted">(${x.with_system_range ? x.with_system_range.join("–") : ""})</span></td></tr>`).join("")}</tbody></table>
     <details><summary class="small">Assumptions (parameters)</summary><pre class="card-md">${W.esc(JSON.stringify(s.assumptions, null, 1))}</pre></details></div>`;
    W.bars(document.getElementById("b-time"), pick(["median_days_to_notification", "median_days_to_acknowledgement", "median_days_to_confirmation", "median_days_to_consultation", "median_days_to_care"]));
    W.bars(document.getElementById("b-pct"), pick(["pct_notified", "pct_understood", "pct_referral_completed", "pct_appointment_attended", "pct_care_reached_or_resolved", "pct_documented_closure"]));
    const bl = s.baseline_detail.by_language, wl = s.with_system_detail.by_language;
    W.bars(document.getElementById("b-lang"), Object.keys(bl).map(l => ({ label: `${l} (n=${bl[l].n})`, a: bl[l].pct_care_reached, b: wl[l].pct_care_reached })), undefined, v => v + "%");
    const rb = s.baseline_detail.open_reasons, rw = s.with_system_detail.open_reasons;
    W.bars(document.getElementById("b-reasons"), Object.keys(rb).map(r => ({ label: r.replaceAll("_", " "), a: rb[r], b: rw[r] || 0 })));
  },
  async live(el) {
    const k = await W.api("/api/analytics/kpis");
    const card = (l, v) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v ?? "–"}</div></div>`;
    el.innerHTML = `<div class="card notice"><b>${W.esc(k.label)}</b> – figures from the cases recorded in this system.</div>
     <div class="grid-4">${card("Cases", k.total_cases)}${card("Open", k.open_cases)}${card("Open steps overdue", k.open_overdue_steps)}${card("Closure rate", W.pct(k.closure_rate))}
     ${card("Closed with evidence", k.closed_with_evidence)}${card("Closed by authorised decision", k.closed_authorised_decision)}${card("Median days to notification", k.median_days_to_notification)}${card("Median days to care", k.median_days_to_care)}
     ${card("Median days to understood", k.median_days_to_acknowledgement)}${card("Median days to confirmation", k.median_days_to_confirmation)}${card("Referral acceptance", W.pct(k.referral_acceptance_rate))}${card("Appointment attendance", W.pct(k.appointment_attendance_rate))}
     ${card("Treatment / surveillance initiation", W.pct(k.treatment_initiation_rate))}${card("Teach-back pass (1st attempt)", W.pct(k.teach_back_pass_rate_first_attempt))}${card("Mean confidence (1–5)", k.mean_confidence_1to5)}${card("No-show events", k.no_show_events)}
     ${card(`Recall uptake (screened within ${k.recall_uptake_window_days || 180} days of due)`, W.pct(k.recall_uptake_rate))}${card("Recalls due now", k.recalls_due_now)}</div>
     <div class="grid-2"><div class="card"><h4>Open cases by state</h4><table class="compact">${Object.entries(k.open_by_state).map(([s, n]) => `<tr><td>${W.esc(s)}</td><td>${n}</td></tr>`).join("")}</table></div>
     <div class="card"><h4>Top delay / drop-off reasons (lessons learned)</h4><table class="compact">${Object.entries(k.delay_and_dropoff_reasons).map(([s, n]) => `<tr><td>${W.esc(s)}</td><td>${n}</td></tr>`).join("")}</table></div></div>`;
  },
  async fair(el) {
    const f = await W.api("/api/analytics/fairness");
    el.innerHTML = `<p class="small muted">Outcome rates per group. Look for gaps: e.g. lower teach-back pass or care-reached rates for languages without a template. n&lt;${f.suppression_threshold} suppressed.</p>` +
      Object.entries(f.slices).map(([sl, rows]) => `<div class="card"><h4>${W.esc(sl.replace("_", " "))}</h4><table class="compact"><thead><tr><th>Group</th><th>n</th><th>Notified</th><th>Teach-back pass</th><th>Care reached</th><th>Closed</th><th>Median days to care</th></tr></thead><tbody>${rows.map(r =>
        `<tr><td>${W.esc(r.group)}</td><td>${r.n}</td><td>${W.pct(r.notified_rate)}</td><td>${W.pct(r.teach_back_pass_rate)}</td><td>${W.pct(r.care_reached_rate)}</td><td>${W.pct(r.closure_rate)}</td><td>${r.median_days_to_care ?? "–"}</td></tr>`).join("")}</tbody></table></div>`).join("");
  },
  async lessons(el) {
    const l = await W.api("/api/analytics/lessons");
    el.innerHTML = `<div class="grid-2"><div class="card"><h4>Lessons by kind (${l.total})</h4><table class="compact">${Object.entries(l.by_kind).map(([k, n]) => `<tr><td>${W.esc(k)}</td><td>${n}</td></tr>`).join("")}</table></div>
      <div class="card"><h4>Top reason codes</h4><table class="compact">${l.top_reasons.map(r => `<tr><td>${W.esc(r.kind)}</td><td>${W.esc(r.reason_code)}</td><td>${r.n}</td></tr>`).join("")}</table></div></div>
      <div class="card"><h4>By language</h4><table class="compact">${l.by_language.map(r => `<tr><td>${W.esc(r.kind)}</td><td>${W.esc(r.language)}</td><td>${r.n}</td></tr>`).join("")}</table></div>`;
  },
};
async function show(t) {
  document.querySelectorAll(".tabs button").forEach(b => b.className = b.dataset.t === t ? "" : "secondary outline");
  const el = document.getElementById("dash"); el.innerHTML = W.loading();
  try { await tabs[t](el); } catch (e) { el.innerHTML = W.empty("Could not load", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => show(b.dataset.t)); show("sim"); });
