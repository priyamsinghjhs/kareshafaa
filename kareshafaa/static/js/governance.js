const can = p => W.me && W.me.permissions.includes(p) && !(KS.read_only && ["model:train", "model:rollback", "proposal:generate"].includes(p));  // hidden on read-only sites (refused there anyway)
const statusChip = s => `<span class="chip ${{ production: "sla-green", awaiting_approval: "sla-amber", failed_gates: "sla-red", rejected: "sla-none", retired: "sla-none", rolled_back: "sla-red", pending: "sla-amber", approved: "sla-green" }[s] || "sla-none"}">${W.esc(s.replace("_", " "))}</span>`;
async function act(path, body, msg) { try { await W.api(path, { method: "POST", body }); W.toast(msg, "ok"); show(current); } catch (e) { W.toast(e.message, "err"); } }
// notes: the dialog stays open until the server accepts the note (errors shown in the dialog, text kept)
const saveTo = (path, key, msg, extra = {}) => async n => { await W.api(path, { method: "POST", body: { ...extra, [key]: n } }); W.toast(msg, "ok"); show(current); };
window.approve = v => W.ask(`Approve model v${v} for production?`, "Your clinical review note", { ok: "Approve for production", min: 5, body: "The model only orders outreach within the same urgency. Your approval and note are recorded.", save: saveTo(`/api/governance/models/${v}/approve`, "note", `v${v} promoted to production`) });
window.rejectM = v => W.ask(`Reject candidate model v${v}?`, "Reason", { ok: "Reject", danger: true, min: 5, save: saveTo(`/api/governance/models/${v}/reject`, "note", `v${v} rejected`) });
window.rollback = () => W.ask("Roll back the production model?", "Reason", { ok: "Roll back", danger: true, min: 5, body: "Outreach ordering returns to the previous approved version.", save: saveTo(`/api/governance/models/rollback`, "note", "Rolled back") });
window.train = async alg => { if (await W.confirm(`Train a new candidate model (${alg})?`, "It will not be used until a clinical lead approves it.", { ok: "Train candidate" })) act(`/api/governance/models/train`, { algorithm: alg }, `Candidate (${alg}) trained on accumulated outcomes – see gates`); };
window.decide = (id, ok) => W.ask(`${ok ? "Approve" : "Reject"} proposed change #${id}?`, "Decision note", { ok: ok ? "Approve and apply" : "Reject", danger: !ok, min: 5, save: saveTo(`/api/governance/proposals/${id}/decide`, "note", ok ? "Approved and applied" : "Rejected", { approve: ok }) });
window.genProposals = () => act(`/api/governance/proposals/generate`, null, "Proposal engine run");
window.signoff = (cid, kind) => W.ask(`${kind === "clinical" ? "Clinical" : "Translation"} sign-off for ${cid}`, "What did you check?", { ok: "Sign off", min: 5, save: saveTo(`/api/governance/content/signoff`, "note", "Sign-off recorded (bound to this exact wording)", { content_id: cid, kind }) });
window.detail = async v => {
  const m = await W.api(`/api/governance/models/${v}`), el = document.getElementById("mdetail");
  const gate = (k, g) => `<tr><td class="${g.passed ? "gate-pass" : "gate-fail"}">${g.passed ? "PASS" : "FAIL"}</td><td>${W.esc(k)}</td><td>${W.esc(Array.isArray(g.value) ? g.value.join(", ") : g.value)}</td><td class="small muted">${W.esc(g.threshold ?? "")}${g.gap != null ? " · point gap " + g.gap : ""}</td></tr>`;
  el.innerHTML = `<div class="card"><h4>Model v${m.version} (${W.esc(m.algorithm)}) ${statusChip(m.status)}</h4>
    <div class="grid-4">${[["AUC (held-out)", m.metrics.auc], ["Brier", m.metrics.brier], ["Calibration error (ECE)", m.metrics.ece], ["Recall @ top " + Math.round(m.metrics.top_fraction * 100) + "%", m.metrics.recall_at_top]].map(([l, v]) => `<div class="card kpi"><div class="l">${l}</div><div class="v">${v}</div></div>`).join("")}</div>
    <p class="small">Trained on ${m.trained_on_n} cases, validated on the most recent ${m.validated_on_n} (temporal hold-out). ${m.metrics.current_production_auc_same_holdout != null ? `Current production model on the same hold-out: AUC <b>${m.metrics.current_production_auc_same_holdout}</b>.` : ""} Inputs: ${m.features.join(", ")}. Gender and nationality are excluded from inputs and used only for fairness checks.</p>
    <div class="grid-2"><div><h5>Promotion gates</h5><table class="compact">${Object.entries(m.gates).filter(([k]) => k !== "all_passed").map(([k, g]) => gate(k, g)).join("")}</table></div>
    <div><h5>Calibration (predicted vs observed)</h5><div class="bars" id="calib"></div></div></div>
    <h5>Fairness by group (held-out)</h5>${Object.entries(m.fairness).map(([a, f]) => `<details><summary>${W.esc(a)} – AUC gap ${f.auc_gap}, recall gap ${f.tpr_gap}${f.underserved_auc.length || f.underserved_tpr.length || f.miscalibrated.length ? ' <span class="gate-fail">flags</span>' : ' <span class="gate-pass">no credible under-service</span>'}</summary>
      <table class="compact"><thead><tr><th>Group</th><th>n</th><th>positives</th><th>AUC (95% CI)</th><th>Recall@top (95% CI)</th><th>Flag rate</th><th>Observed</th><th>Mean predicted</th><th>Gated?</th></tr></thead><tbody>${f.groups.map(g => `<tr><td>${W.esc(g.group)}</td><td>${g.n}</td><td>${g.n_pos}</td><td>${g.auc ?? "–"} ${g.auc_ci ? `<span class="small muted">(${g.auc_ci.join("–")})</span>` : ""}</td><td>${g.tpr_at_top ?? "–"} ${g.tpr_ci ? `<span class="small muted">(${g.tpr_ci.join("–")})</span>` : ""}</td><td>${g.flag_rate}</td><td>${g.observed_rate}</td><td>${g.mean_pred}</td><td>${g.eligible_for_gate ? "yes" : "<span class='muted'>too small</span>"}</td></tr>`).join("")}</tbody></table></details>`).join("")}
    <p style="margin-top:.6rem"><a href="/api/governance/models/${m.version}/card" target="_blank">Open model card (Markdown)</a> · SHA-256 <code class="small">${m.artifact_sha256.slice(0, 16)}…</code></p></div>`;
  W.bars(document.getElementById("calib"), m.metrics.calibration_curve.map(c => ({ label: `bin ${c.bin} (n=${c.n < 5 ? "<5" : c.n})`, a: c.mean_pred, b: c.observed })), ["Mean predicted", "Observed"], v => v);
};
const tabsG = {
  async models(el) {
    const [d, drift] = await Promise.all([W.api("/api/governance/models"), W.api("/api/governance/drift")]);
    el.innerHTML = `<div class="card info small"><b>Use statement:</b> ${W.esc(d.use_statement)}</div>
     <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-bottom:.6rem">${can("model:train") ? `<button onclick="train('gbm')">Retrain candidate (gradient boosting)</button><button class="secondary" onclick="train('logreg')">Retrain candidate (logistic regression)</button>` : ""}
     ${can("model:rollback") ? `<button class="secondary" onclick="rollback()">Roll back production model</button>` : ""}</div>
     <table class="compact"><thead><tr><th>Version</th><th>Algorithm</th><th>Status</th><th>AUC</th><th>ECE</th><th>Recall@top</th><th>n train / valid</th><th>Gates</th><th>Created</th><th></th></tr></thead><tbody>${d.items.map(m => `<tr>
       <td><a href="#" onclick="detail(${m.version});return false">v${m.version}</a></td><td>${W.esc(m.algorithm)}</td><td>${statusChip(m.status)}${m.decision_note ? `<div class="small muted">${W.esc(m.decision_note)}</div>` : ""}</td><td>${m.metrics.auc}</td><td>${m.metrics.ece}</td><td>${m.metrics.recall_at_top}</td><td>${m.trained_on_n} / ${m.validated_on_n}</td>
       <td class="${m.gates_passed ? "gate-pass" : "gate-fail"}">${m.gates_passed ? "all passed" : "failed"}</td><td class="small">${W.dt(m.created_at)}</td>
       <td>${m.status === "awaiting_approval" && can("model:approve") ? `<button onclick="approve(${m.version})">Approve</button> <button class="secondary" onclick="rejectM(${m.version})">Reject</button>` : ""}</td></tr>`).join("")}</tbody></table>
     <div class="card"><h4>Drift monitoring ${drift.status ? statusChip(drift.status) : ""}</h4>${drift.features ? `<p class="small muted">${W.esc(drift.note)} PSI warn &gt; ${drift.thresholds.warn}, alert &gt; ${drift.thresholds.alert}. Score PSI: <b>${drift.score_psi}</b> (n=${drift.n_current}).</p><table class="compact">${Object.entries(drift.features).map(([f, v]) => `<tr><td>${W.esc(f)}</td><td>${v.psi}</td><td>${statusChip(v.status)}</td></tr>`).join("")}</table>` : `<p class="muted">${W.esc(drift.status)}</p>`}</div>
     <div id="mdetail"></div>`;
    const top = d.items.find(m => m.status === "awaiting_approval") || d.items[0]; if (top) detail(top.version);
  },
  async proposals(el) {
    const d = await W.api("/api/governance/proposals");
    el.innerHTML = `<p class="small muted">Generated from lessons learned and step-level SLA performance. Approval applies the change (new versioned SLA rule / config entry); nothing changes without a clinical lead. Advisory items record a decision only.</p>
     ${can("proposal:generate") ? `<button onclick="genProposals()">Run proposal engine now</button>` : ""}
     <table class="compact"><thead><tr><th>#</th><th>Type</th><th>Target</th><th>Current → proposed</th><th>Rationale &amp; evidence</th><th>Status</th><th></th></tr></thead><tbody>${d.items.map(p => `<tr>
      <td>${p.id}</td><td>${W.esc(p.kind.replace("_", " "))}</td><td class="small">${W.esc(JSON.stringify(p.target))}</td><td class="small">${W.esc(JSON.stringify(p.current))} → <b>${W.esc(JSON.stringify(p.proposed))}</b></td>
      <td class="small">${W.esc(p.rationale)}<div class="muted">${W.esc(JSON.stringify(p.evidence))}</div></td><td>${statusChip(p.status)}${p.decision_note ? `<div class="small muted">${W.esc(p.decision_note)}</div>` : ""}</td>
      <td>${p.status === "pending" && can("proposal:decide") ? `<button onclick="decide(${p.id},true)">Approve</button> <button class="secondary" onclick="decide(${p.id},false)">Reject</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
  },
  async lessons(el) {
    const l = await W.api("/api/analytics/lessons");
    el.innerHTML = `<p class="small muted">Every overdue step, drop-off, no-show, rejected referral, failed contact, decline and failed teach-back is recorded with a reason code (${l.total} lessons). Counts &lt;5 suppressed.</p><div class="grid-3">
      <div class="card"><h4>By kind</h4><table class="compact">${Object.entries(l.by_kind).map(([k, n]) => `<tr><td>${W.esc(k)}</td><td>${n}</td></tr>`).join("")}</table></div>
      <div class="card"><h4>Top reasons</h4><table class="compact">${l.top_reasons.map(r => `<tr><td class="small">${W.esc(r.kind)}</td><td class="small">${W.esc(r.reason_code)}</td><td>${r.n}</td></tr>`).join("")}</table></div>
      <div class="card"><h4>By step</h4><table class="compact">${l.by_state.map(r => `<tr><td class="small">${W.esc(r.kind)}</td><td class="small">${W.esc(r.state)}</td><td>${r.n}</td></tr>`).join("")}</table></div></div>`;
  },
  async rules(el) {
    const [r, c] = await Promise.all([W.api("/api/governance/sla-rules"), W.api("/api/governance/config")]);
    const states = [...new Set(r.items.map(x => x.state))];
    const get = (s, t) => r.items.find(x => x.state === s && x.tier === t);
    el.innerHTML = `<div class="grid-2"><div class="card"><h4>SLA hours by step and urgency (reminder at % of SLA)</h4><table class="compact"><thead><tr><th>Step</th><th>urgent</th><th>high</th><th>routine</th></tr></thead><tbody>${states.map(s => `<tr><td>${W.esc(s)}</td>${["urgent", "high", "routine"].map(t => { const x = get(s, t); return `<td>${x.hours}h <span class="small muted">@${Math.round(x.reminder_fraction * 100)}%</span>${x.version > 1 ? ` <span class="chip flag" title="${W.esc(x.source)}">v${x.version}</span>` : ""}</td>`; }).join("")}</tr>`).join("")}</tbody></table>
      <p class="small muted">Escalation ladder: L1 reminder to owner at the reminder point → L2 supervisor at breach (critical cases go straight to L3) → L3 clinical lead at 150% of SLA → L4 director at 200% (critical/urgent). After a missed pathway target, one consolidated alert per case per level (no repeat per step). Escalations alert people; they never change the clinical plan.</p></div>
      <div class="card"><h4>Active configuration</h4><table class="compact">${c.items.map(x => `<tr><td class="small">${W.esc(x.key)}</td><td class="small">${W.esc(JSON.stringify(x.value))}</td><td class="small muted">v${x.version} · ${W.esc(x.source)}</td></tr>`).join("")}</table></div></div>`;
  },
};
tabsG.content = async el => {
  const r = await W.api("/api/governance/content"), s = r.summary;
  const rows = r.items.filter(x => x.gated || !x.readability.ok || x.lang !== "en" || x.group !== "template");
  const rd = x => { const d = x.readability; return `${d.ok ? '<span class="gate-pass">OK</span>' : '<span class="gate-fail">FIX</span>'} <span class="small muted">${d.fk_grade != null ? "FK " + d.fk_grade + " · ease " + d.reading_ease + " · " : ""}${d.avg_words_per_sentence} w/sent · long ${Math.round(d.long_word_share * 100)}%</span>${d.problems.length ? `<div class="small">${d.problems.map(W.esc).join("; ")}</div>` : ""}`; };
  const so = (x, k) => { const v = x[k + "_signoff"]; return v ? `<span class="chip sla-green" title="${W.esc(W.dt(v.at))}">${W.esc(v.by)}</span>${v.note ? `<div class="small muted">${W.esc(v.note)}</div>` : ""}` : (k === "translation" && x.lang === "en" ? '<span class="small muted">n/a</span>' : (can(k === "clinical" ? "content:approve" : "content:translation_review") ? `<button class="secondary" onclick="signoff('${x.content_id}','${k}')">Sign off</button>` : '<span class="chip sla-amber">pending</span>')); };
  el.innerHTML = `<div class="card"><p class="small">${s.items} patient-facing texts · ${s.readability_ok} pass reading-level &amp; tone checks · ${s.gated_live}/${s.gated} governed pathway texts live. A sign-off is bound to the exact wording (sha256): any edit needs a new sign-off. Arabic, Hindi and Urdu need a <b>professional translator</b> and a <b>clinician</b> (two different people); until then patients get English plus an interpreter-supported call.</p>
  <table class="compact"><thead><tr><th>Content</th><th>Lang</th><th>Text</th><th>Reading level / tone</th><th>Clinical</th><th>Translator</th><th>Live</th></tr></thead><tbody>${rows.map(x => `<tr><td class="small">${W.esc(x.group + ":" + x.key)}${x.gated ? "" : ' <span class="small muted">(not yet gated)</span>'}</td><td>${x.lang}</td><td class="small" dir="auto" style="max-width:28rem">${W.esc(x.text.replace(/[{}"]/g, " ").slice(0, 220))}${x.translation_status ? `<div><span class="chip sla-amber">${W.esc(x.translation_status)}</span></div>` : ""}</td><td>${rd(x)}</td><td>${so(x, "clinical")}</td><td>${so(x, "translation")}</td><td>${x.live ? '<span class="chip sla-green">live</span>' : (x.gated ? '<span class="chip sla-red">not used</span>' : '<span class="small muted">–</span>')}</td></tr>`).join("")}</tbody></table></div>`;
};
let current = "models";
async function show(t) {
  current = t; document.querySelectorAll(".tabs button").forEach(b => b.className = b.dataset.t === t ? "" : "secondary outline");
  const el = document.getElementById("gov"); el.innerHTML = W.loading();
  try { await tabsG[t](el); } catch (e) { el.innerHTML = W.empty("Could not load", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => show(b.dataset.t)); const qt = new URLSearchParams(location.search).get("tab"); show(qt && tabsG[qt] ? qt : (!can("model:read") && can("content:read") ? "content" : "models")); });
