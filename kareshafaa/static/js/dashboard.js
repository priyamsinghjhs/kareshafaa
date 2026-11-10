const barrierRows = (rows, supp, label = (x) => x.label) => rows.length
  ? `<div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact"><thead><tr><th>Reason</th><th>Events</th><th>Share</th></tr></thead><tbody>${rows.map(x => `<tr><td>${W.esc(label(x))}</td><td>${supp(x.events)}</td><td>${W.pct(supp(x.share))}</td></tr>`).join("")}</tbody></table></div>`
  : `<p class="small muted">None recorded yet.</p>`;
const barrierCard = (b, supp) => `<section class="card" id="impact-barriers" aria-labelledby="barriers-h"><h4 id="barriers-h">Why loops fail</h4>
  <p class="small">${supp(b.split.system_side_events)} of the recorded failure events sit with the service (${W.pct(supp(b.split.system_share))}) and ${supp(b.split.patient_side_events)} with the patient's situation.</p>
  <div class="grid-3"><div><h5>What patients told us</h5>${barrierRows(b.patient_reported, supp)}</div>
  <div><h5>Patient side</h5>${barrierRows(b.patient_side, supp)}</div>
  <div><h5>Service side</h5>${barrierRows(b.system_side, supp, x => `${x.kind_label}: ${x.label}`)}</div></div>
  <p class="small muted">${W.esc(b.note)}</p></section>`;
const KLABEL = { median_days_to_notification: "Median days to notification", median_days_to_acknowledgement: "Median days to understood (acknowledged)",
  median_days_to_confirmation: "Median days to confirmation", median_days_to_consultation: "Median days to consultation", median_days_to_care: "Median days to care",
  pct_notified: "% notified", pct_understood: "% understood (teach-back)", pct_referral_completed: "% referrals completed", pct_appointment_attended: "% appointments attended",
  pct_care_reached_or_resolved: "% treatment / surveillance started or resolved", pct_documented_closure: "% documented closure", pct_open_unresolved_at_horizon: "% open & unresolved at day 120",
  mean_confidence_1to5: "Mean patient confidence (1–5)" };
const TIERWORD = { critical: "T0", urgent: "T1", high: "T2", routine: "T3" };
function impactCard(l, v, hint) { return `<div class="card kpi"><div class="l">${l}</div><div class="v">${v ?? "–"}</div>${hint ? `<div class="l">${hint}</div>` : ""}</div>`; }
const supp = v => v === "suppressed" ? '<span class="muted" title="fewer than 5 – hidden to protect privacy">&lt;5</span>' : v;
const ci = (lo, hi) => lo == null ? "" : ` · 95% CI ${W.pct(lo)}–${W.pct(hi)}`;
// baseline vs loop arms from the latest simulation (synthetic only, kept apart from the live figures)
function armsCard(a) {
  const pct = x => W.pct(x.rate) + ` <span class="small muted">${W.pct(x.ci_low)}–${W.pct(x.ci_high)}</span>`;
  const g = a.equity_gap_care_reached_by_language || {};
  const gap = x => x ? `${x.gap_pp} pts` : "–";
  return `<section class="card synthetic-arms" id="impact-arms" aria-labelledby="arms-h"><h4 id="arms-h"><span class="chip">Synthetic</span> Baseline vs loop</h4>
    <p class="small muted">${W.esc(a.statement)}</p>
    <div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact"><thead><tr><th>Measure</th><th>Baseline</th><th>Loop</th><th>Difference</th></tr></thead><tbody>
    ${a.measures.map(m => `<tr data-measure="${W.esc(m.measure)}"><td>${W.esc(m.label)}</td><td>${pct(m.baseline)}</td><td>${pct(m.loop)}</td><td>${m.difference_pp > 0 ? "+" : ""}${m.difference_pp} pts <span class="small muted">${m.difference_ci_pp.join(" to ")}</span></td></tr>`).join("")}
    <tr><td>Best–worst gap by language</td><td>${gap(g.baseline)}</td><td>${gap(g.loop)}</td><td></td></tr></tbody></table></div>
    <p class="small muted">${a.n_patients} modelled patients per arm · run ${W.dt(a.created_at)}</p></section>`;
}
const tabs = {
  async impact(el) {
    const prog = new URLSearchParams(location.search).get("programme") || "";
    const r = await W.api("/api/analytics/impact" + (prog ? `?programme=${encodeURIComponent(prog)}` : ""));
    const h = r.headline || {}, d = r.definitions, st = r.settings;
    document.getElementById("impact-csv").href = "/api/analytics/impact?format=csv" + (prog ? `&programme=${encodeURIComponent(prog)}` : "");
    const sel = `<label for="imp-prog" class="small" style="max-width:20rem">Screening programme<select id="imp-prog"><option value="">All programmes</option>${Object.entries(r.programmes).map(([k, l]) => `<option value="${k}" ${k === prog ? "selected" : ""}>${W.esc(l)}</option>`).join("")}</select></label>`;
    if (!h.mature_cases && h.mature_cases !== "suppressed") { el.innerHTML = sel + W.empty("No cases yet", "The report fills in as results arrive and cases move through their pathway."); wireProg(); return; }
    el.innerHTML = `<div class="card notice" id="impact-data-label" role="note"><b>${W.esc(r.data.label)}.</b> <span class="small">Generated ${W.dt(r.generated_at)}. Groups smaller than ${st.suppression_threshold} are hidden. Patients who opted out of analytics are not counted.</span></div>
     ${sel}
     <div class="grid-4" id="impact-headline">
      ${impactCard("Loop closed", W.pct(supp(h.loop_closure_rate)), `${supp(h.loop_closed)} of ${supp(h.mature_cases)} cases ≥${st.loop_maturity_days} days old${ci(h.loop_closure_ci_low, h.loop_closure_ci_high)}`)}
      ${impactCard("Lost to follow-up", W.pct(supp(h.lost_to_follow_up_rate)), `${supp(h.lost_to_follow_up_now)} open cases lost now`)}
      ${impactCard("First specialist contact", h.median_days_to_specialist == null ? "–" : h.median_days_to_specialist + " days", "median of cases seen so far")}
      ${impactCard("Seen within pathway target", W.pct(supp(h.within_target_rate_all)), `all mature cases, unfinished = not met${ci(h.within_target_all_ci_low, h.within_target_all_ci_high)}`)}
      ${impactCard("Closed by clinician decision", W.pct(supp(h.closed_by_decision_rate)), "informed decline, care elsewhere and similar")}
      ${impactCard("Recall uptake", W.pct(supp(h.recall_uptake_rate)), `screened within ${h.recall_uptake_window_days} days of the due date`)}</div>
     <div class="card"><h4>Time to first specialist contact, by urgency tier</h4><div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact" id="impact-tiers"><thead><tr><th>Tier</th><th>Cases</th><th>Seen</th><th>Median days</th><th>90th percentile</th><th>Default target</th><th>Seen within target</th><th>Waiting past target</th></tr></thead><tbody>
      ${r.by_tier.map(t => `<tr data-tier="${t.tier}"><td><span class="chip">${W.esc(t.label)}</span></td><td>${supp(t.cases)}</td><td>${supp(t.seen)}</td><td>${t.median_days ?? "–"}</td><td>${t.p90_days ?? "–"}</td><td>${t.target_days_default} ${t.target_days_default === 1 ? "day" : "days"}</td><td>${W.pct(supp(t.within_target_rate))}</td><td>${supp(t.waiting_past_target)}</td></tr>`).join("")}</tbody></table></div>
      <p class="small muted">Each case has its own end-to-end target from its pathway (for example colonoscopy within 31 days of a positive FIT); the default column shows the fallback per tier.</p></div>
     <div class="card"><h4>By screening programme</h4><div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact"><thead><tr><th>Programme</th><th>Cases</th><th>Loop closed</th><th>Lost to follow-up</th><th>Median days to specialist</th></tr></thead><tbody>
      ${r.by_programme.map(p => `<tr><td>${W.esc(p.label)}</td><td>${supp(p.cases)}</td><td>${W.pct(supp(p.loop_closure_rate))}</td><td>${W.pct(supp(p.lost_rate))}</td><td>${p.median_days_to_specialist ?? "–"}</td></tr>`).join("")}</tbody></table></div></div>
     <div class="grid-3" id="impact-equity">${Object.entries(r.equity).map(([k, e]) => `<div class="card"><h4>Equity by ${W.esc(e.label.toLowerCase())}</h4><div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact"><thead><tr><th>Group</th><th>n</th><th>Loop closed</th><th>95% CI</th><th>Gap</th></tr></thead><tbody>
      ${e.rows.map(g => `<tr ${g.flag ? 'class="row-flag"' : ""}><td>${W.esc(g.group)}</td><td>${supp(g.n)}</td><td>${W.pct(supp(g.loop_closure_rate))}</td><td class="small nowrap">${g.ci_low == null ? "–" : W.pct(g.ci_low) + "–" + W.pct(g.ci_high)}</td><td>${g.gap_pp == null ? "–" : (g.gap_pp > 0 ? `${g.gap_pp} pts below` : `${Math.abs(g.gap_pp)} pts above`)}${g.flag ? ' <span class="chip sla-red">▲ check</span>' : ""}</td></tr>`).join("")}</tbody></table></div>${e.gap ? `<p class="equity-gap small" data-gap="${e.gap.gap_pp}"><b>${e.gap.gap_pp} pts</b> best–worst (${W.esc(e.gap.best_group)} vs ${W.esc(e.gap.worst_group)})</p>` : ""}</div>`).join("")}</div>
     <p class="small muted">A group is marked “check” when its loop-closure rate is ${st.equity_gap_pp} or more percentage points below the overall rate.</p>
     ${r.barriers ? barrierCard(r.barriers, supp) : ""}
     ${r.synthetic_arms ? armsCard(r.synthetic_arms) : ""}
     <details class="card"><summary>How each figure is defined</summary><dl class="facts">${Object.entries(d).map(([k, v]) => `<div><dt>${W.esc(k.replaceAll("_", " "))}</dt><dd>${W.esc(v)}</dd></div>`).join("")}</dl></details>`;
    wireProg();
  },
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
function wireProg() { const s = document.getElementById("imp-prog"); if (s) s.onchange = () => { const u = new URL(location.href); s.value ? u.searchParams.set("programme", s.value) : u.searchParams.delete("programme"); history.replaceState(null, "", u); show("impact"); }; }
async function show(t) {
  document.getElementById("impact-actions").hidden = t !== "impact";
  document.querySelectorAll(".tabs button").forEach(b => b.className = b.dataset.t === t ? "" : "secondary outline");
  const el = document.getElementById("dash"); el.innerHTML = W.loading();
  try { await tabs[t](el); } catch (e) { el.innerHTML = W.empty("Could not load", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => show(b.dataset.t)); show("impact");
  document.getElementById("impact-print").onclick = () => window.print(); });
