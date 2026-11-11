// tabs the role may not use are removed (moved from governance.html: no inline scripts)
document.addEventListener("DOMContentLoaded", async () => { await W.ready; document.querySelectorAll(".tabs [data-perm]").forEach(b => { if (!W.can(b.dataset.perm)) b.remove(); }); });
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
    <p class="mt-3"><a href="/api/governance/models/${m.version}/card" target="_blank">Open model card (Markdown)</a> · SHA-256 <code class="small">${m.artifact_sha256.slice(0, 16)}…</code></p></div>`;
  W.bars(document.getElementById("calib"), m.metrics.calibration_curve.map(c => ({ label: `bin ${c.bin} (n=${c.n < 5 ? "<5" : c.n})`, a: c.mean_pred, b: c.observed })), ["Mean predicted", "Observed"], v => v);
};
const tabsG = {
  async models(el) {
    const [d, drift] = await Promise.all([W.api("/api/governance/models"), W.api("/api/governance/drift")]);
    el.innerHTML = `<div class="card info small"><b>Use statement:</b> ${W.esc(d.use_statement)}</div>
     <div class="row mb-2">${can("model:train") ? `<button data-click="train" data-args='["gbm"]'>Retrain candidate (gradient boosting)</button><button class="secondary" data-click="train" data-args='["logreg"]'>Retrain candidate (logistic regression)</button>` : ""}
     ${can("model:rollback") ? `<button class="secondary" data-click="rollback">Roll back production model</button>` : ""}</div>
     <table class="compact"><thead><tr><th>Version</th><th>Algorithm</th><th>Status</th><th>AUC</th><th>ECE</th><th>Recall@top</th><th>n train / valid</th><th>Gates</th><th>Created</th><th></th></tr></thead><tbody>${d.items.map(m => `<tr>
       <td><a href="#" data-click="detail" data-args="[${m.version}]">v${m.version}</a></td><td>${W.esc(m.algorithm)}</td><td>${statusChip(m.status)}${m.decision_note ? `<div class="small muted">${W.esc(m.decision_note)}</div>` : ""}</td><td>${m.metrics.auc}</td><td>${m.metrics.ece}</td><td>${m.metrics.recall_at_top}</td><td>${m.trained_on_n} / ${m.validated_on_n}</td>
       <td class="${m.gates_passed ? "gate-pass" : "gate-fail"}">${m.gates_passed ? "all passed" : "failed"}</td><td class="small">${W.dt(m.created_at)}</td>
       <td>${m.status === "awaiting_approval" && can("model:approve") ? `<button data-click="approve" data-args="[${m.version}]">Approve</button> <button class="secondary" data-click="rejectM" data-args="[${m.version}]">Reject</button>` : ""}</td></tr>`).join("")}</tbody></table>
     <div class="card"><h4>Drift monitoring ${drift.status ? statusChip(drift.status) : ""}</h4>${drift.features ? `<p class="small muted">${W.esc(drift.note)} PSI warn &gt; ${drift.thresholds.warn}, alert &gt; ${drift.thresholds.alert}. Score PSI: <b>${drift.score_psi}</b> (n=${drift.n_current}).</p><table class="compact">${Object.entries(drift.features).map(([f, v]) => `<tr><td>${W.esc(f)}</td><td>${v.psi}</td><td>${statusChip(v.status)}</td></tr>`).join("")}</table>` : `<p class="muted">${W.esc(drift.status)}</p>`}</div>
     <div id="mdetail"></div>`;
    const top = d.items.find(m => m.status === "awaiting_approval") || d.items[0]; if (top) detail(top.version);
  },
  async proposals(el) {
    const d = await W.api("/api/governance/proposals");
    el.innerHTML = `<p class="small muted">Generated from lessons learned and step-level SLA performance. Approval applies the change (new versioned SLA rule / config entry); nothing changes without a clinical lead. Advisory items record a decision only.</p>
     ${can("proposal:generate") ? `<button data-click="genProposals">Run proposal engine now</button>` : ""}
     <table class="compact"><thead><tr><th>#</th><th>Type</th><th>Target</th><th>Current → proposed</th><th>Rationale &amp; evidence</th><th>Status</th><th></th></tr></thead><tbody>${d.items.map(p => `<tr>
      <td>${p.id}</td><td>${W.esc(p.kind.replace("_", " "))}</td><td class="small">${W.esc(JSON.stringify(p.target))}</td><td class="small">${W.esc(JSON.stringify(p.current))} → <b>${W.esc(JSON.stringify(p.proposed))}</b></td>
      <td class="small">${W.esc(p.rationale)}<div class="muted">${W.esc(JSON.stringify(p.evidence))}</div></td><td>${statusChip(p.status)}${p.decision_note ? `<div class="small muted">${W.esc(p.decision_note)}</div>` : ""}</td>
      <td>${p.status === "pending" && can("proposal:decide") ? `<button data-click="decide" data-args="[${p.id},true]">Approve</button> <button class="secondary" data-click="decide" data-args="[${p.id},false]">Reject</button>` : ""}</td></tr>`).join("")}</tbody></table>`;
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
  const so = (x, k) => { const v = x[k + "_signoff"]; return v ? `<span class="chip sla-green" title="${W.esc(W.dt(v.at))}">${W.esc(v.by)}</span>${v.note ? `<div class="small muted">${W.esc(v.note)}</div>` : ""}` : (k === "translation" && x.lang === "en" ? '<span class="small muted">n/a</span>' : (can(k === "clinical" ? "content:approve" : "content:translation_review") ? `<button class="secondary" data-click="signoff" data-args="${W.esc(JSON.stringify([x.content_id, k]))}">Sign off</button>` : '<span class="chip sla-amber">pending</span>')); };
  el.innerHTML = `<div class="card"><p class="small">${s.items} patient-facing texts · ${s.readability_ok} pass reading-level &amp; tone checks · ${s.gated_live}/${s.gated} governed pathway texts live. A sign-off is bound to the exact wording (sha256): any edit needs a new sign-off. Arabic, Hindi and Urdu need a <b>professional translator</b> and a <b>clinician</b> (two different people); until then patients get English plus an interpreter-supported call.</p>
  <table class="compact"><thead><tr><th>Content</th><th>Lang</th><th>Text</th><th>Reading level / tone</th><th>Clinical</th><th>Translator</th><th>Live</th></tr></thead><tbody>${rows.map(x => `<tr><td class="small">${W.esc(x.group + ":" + x.key)}${x.gated ? "" : ' <span class="small muted">(not yet gated)</span>'}</td><td>${x.lang}</td><td class="small maxw-text" dir="auto">${W.esc(x.text.replace(/[{}"]/g, " ").slice(0, 220))}${x.translation_status ? `<div><span class="chip sla-amber">${W.esc(x.translation_status)}</span></div>` : ""}</td><td>${rd(x)}</td><td>${so(x, "clinical")}</td><td>${so(x, "translation")}</td><td>${x.live ? '<span class="chip sla-green">live</span>' : (x.gated ? '<span class="chip sla-red">not used</span>' : '<span class="small muted">–</span>')}</td></tr>`).join("")}</tbody></table></div>`;
};
tabsG.decisions = async el => {
  const r = await W.api("/api/governance/rule-cards"), e = r.engine, m = r.model;
  const li = a => `<ul class="small">${a.map(x => `<li>${W.esc(x)}</li>`).join("")}</ul>`;
  el.innerHTML = `<div class="card ok" role="note" id="human-in-loop"><b>A person decides.</b> ${W.esc(r.human_in_loop)}</div>
   <div class="grid-2"><section class="card" id="rule-card" aria-labelledby="rc-h"><h3 id="rc-h">Rule card: ${W.esc(e.name)}</h3>
     <dl class="facts"><div><dt>Type</dt><dd>${W.esc(e.kind)}</dd></div><div><dt>Version</dt><dd>state machine ${W.esc(e.state_machine_version)} · rules in ${W.esc(e.rules_document)}</dd></div><div><dt>Status</dt><dd>${W.esc(e.status)}</dd></div></dl>
     <h4>Purpose</h4><p class="small">${W.esc(r.purpose)}</p>
     <h4>Inputs</h4>${li(r.inputs)}<p class="small"><b>Never used:</b> ${r.not_inputs.map(W.esc).join(", ")}.</p>
     <h4>Limits</h4>${li(r.limits)}<h4>Known biases</h4>${li(r.known_biases)}
     <h4>Human override</h4><p class="small">${W.esc(r.human_override)}</p>
     <h4>End-to-end targets when the pathway has none</h4><p class="small">${Object.entries(r.tier_targets_days).map(([t, d]) => `${W.esc((KS.tiers[t] || {}).name ? `${KS.tiers[t].name} (${KS.tiers[t].code})` : t)}: ${d} day${d === 1 ? "" : "s"}`).join(" · ")}</p></section>
   <section class="card" aria-labelledby="mc-h"><h3 id="mc-h">Model card: outreach priority</h3>${m ? `<dl class="facts"><div><dt>Version</dt><dd>v${m.version} · ${W.esc(m.algorithm)} · ${W.esc(m.status)}</dd></div></dl>
     <h4>Purpose</h4><p class="small">${W.esc(m.purpose)}</p><h4>Never</h4><p class="small">${W.esc(m.never)}</p>
     <h4>Monitored for fairness, never model inputs</h4><p class="small">${m.monitored_not_used.map(W.esc).join(", ")}</p>
     <h4>How a version goes live</h4><p class="small">${W.esc(m.approval)}</p><p class="small"><a href="?tab=models" data-go="models">Metrics, fairness gates and drift</a></p>` : W.empty("No model in production", "Cases are ordered by tier and deadline only.")}</section></div>
   <h3>Every rule, with its source</h3>
   ${r.families.map(f => `<details class="card" data-family="${W.esc(f.code)}"><summary><b>${W.esc(f.programme)}</b> <span class="small muted">${f.rules.length} rules</span></summary><div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact"><thead><tr><th>Rule</th><th>When</th><th>Decision</th><th>Tier</th><th>Pathway</th><th>Target</th><th>Source</th></tr></thead><tbody>
     ${f.rules.map(x => `<tr id="rule-${W.esc(x.id)}"><td><code>${W.esc(x.id)}</code></td><td class="small">${W.esc(x.input)}</td><td class="small">${W.esc(x.decision)}</td><td class="small">${W.esc(x.tier)}</td><td class="small">${W.esc(x.pathway.replaceAll("_", " "))}</td><td class="small">${W.esc(x.target)}</td><td class="small">${W.esc(x.source)}</td></tr>`).join("")}</tbody></table></div></details>`).join("")}`;
  el.querySelectorAll("[data-go]").forEach(a => a.onclick = ev => { ev.preventDefault(); show(a.dataset.go); });
  const h = location.hash.slice(1); if (h.startsWith("rule-")) { const row = document.getElementById(h); if (row) { row.closest("details").open = true; row.classList.add("row-flag"); row.scrollIntoView({ block: "center" }); } }
};
tabsG.overrides = async el => {
  const r = await W.api("/api/governance/overrides");
  const n = Object.values(r.counts).reduce((a, b) => a + b, 0);
  const PAGE = 25; let shown = PAGE, filt = "";
  const row = x => `<tr data-override="${x.id}" ${x.lowered_tier ? 'class="row-flag"' : ""}><td class="small">${W.dt(x.at)}</td><td class="small">${W.esc(x.by)}</td><td class="small"><b>${W.esc(x.label)}</b>${x.what ? `<br>${W.esc(x.what)}` : ""}</td><td class="small">${W.esc(x.reason.replaceAll("_", " ")) || '<span class="muted">–</span>'}</td><td>${x.case_id ? `<a href="/cases/${x.case_id}">#${x.case_id}</a>` : "–"}</td></tr>`;
  const pick = () => r.items.filter(x => !filt || (filt === "lowered" ? x.lowered_tier : x.event === filt));
  const draw = () => { const rows = pick(); const tb = el.querySelector("#override-list tbody"); if (!tb) return;
    tb.innerHTML = rows.slice(0, shown).map(row).join("") || `<tr><td colspan="5" class="muted small">Nothing of this kind in the latest ${r.listed} entries.</td></tr>`;
    el.querySelector("#override-count").textContent = `Showing ${Math.min(shown, rows.length)} of ${rows.length} (latest ${r.listed} entries)`;
    el.querySelector("#override-more").hidden = shown >= rows.length; };
  el.innerHTML = `<div class="card info small" role="note">${W.esc(r.note)}</div>
   <div class="grid-4">${Object.entries(r.counts).map(([k, v]) => `<div class="card kpi"><div class="l">${W.esc(r.labels[k])}</div><div class="v">${v}</div></div>`).join("")}<div class="card kpi"><div class="l">Of which a tier was lowered</div><div class="v">${r.lowered_tiers}</div></div></div>
   ${n ? `<div class="row"><label for="override-kind">Show<select id="override-kind"><option value="">All overrides</option><option value="lowered">Lowered tiers only</option>${Object.entries(r.labels).map(([k, l]) => `<option value="${k}">${W.esc(l)}</option>`).join("")}</select></label></div>
     <p class="small muted" id="override-count" aria-live="polite"></p>
     <div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact" id="override-list"><thead><tr><th>When</th><th>Who</th><th>What</th><th>Reason given</th><th>Case</th></tr></thead><tbody></tbody></table></div>
     <button type="button" class="secondary sm" id="override-more">Show ${PAGE} more</button>`
     : W.empty("No overrides yet", "", "", "shield")}`;
  if (!n) return;
  el.querySelector("#override-kind").onchange = e => { filt = e.target.value; shown = PAGE; draw(); };
  el.querySelector("#override-more").onclick = () => { shown += PAGE; draw(); };
  draw();
};
tabsG.privacy = async el => {
  const r = await W.api("/api/governance/data-protection");
  el.innerHTML = `<div class="card notice small" role="note"><b>${W.esc(r.hosting.residency)}</b> Database: ${W.esc(r.hosting.database)}. ${W.esc(r.disclaimer)} Sources checked ${W.esc(r.checked)}.</div>
   <h3>How the system maps to UAE and Abu Dhabi rules</h3>
   ${r.frameworks.map(f => `<section class="card" data-framework><h4><a href="${W.esc(f.url)}" target="_blank" rel="noopener">${W.esc(f.framework)}</a> <span class="chip ${f.status === "met" ? "sla-green" : "sla-amber"}">${f.status === "met" ? "✓ met" : "● partly – pilot steps listed"}</span></h4>
     <dl class="facts"><div><dt>What it asks</dt><dd>${W.esc(f.asks)}</dd></div><div><dt>In this system now</dt><dd>${W.esc(f.prototype)}</dd></div><div><dt>Before a pilot</dt><dd>${W.esc(f.pilot)}</dd></div></dl></section>`).join("")}
   <h3>Data retention</h3><div class="table-scroll" tabindex="0" role="region" aria-label="Table, scrolls sideways"><table class="compact" id="retention"><thead><tr><th>Data</th><th>Kept for</th><th>How</th><th>Basis</th><th>Automatic</th></tr></thead><tbody>${r.retention.map(x => `<tr><td class="small">${W.esc(x.data)}</td><td class="small">${W.esc(x.keep)}</td><td class="small">${W.esc(x.how)}</td><td class="small">${W.esc(x.basis)}</td><td>${x.automatic ? "Yes" : "No"}</td></tr>`).join("")}</tbody></table></div>
   <p class="small muted">The audit chain can be checked end to end by an administrator (Audit trail → “Check the log is intact”).</p>`;
};
let current = "decisions";
async function show(t) {
  current = t; document.querySelectorAll(".tabs button").forEach(b => b.className = b.dataset.t === t ? "" : "secondary outline");
  const el = document.getElementById("gov"); el.innerHTML = W.loading();
  try { await tabsG[t](el); } catch (e) { el.innerHTML = W.empty("Could not load", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => show(b.dataset.t)); const qt = new URLSearchParams(location.search).get("tab"); show(qt && tabsG[qt] ? qt : (!can("model:read") && can("content:read") ? "content" : "decisions")); });
