// Admin > System: where the data lives, and the audit-trail fallback alert (admin role only; the API checks it too).
function auditAlert(a) {
  if (!a) return "";
  if (a.unreconciled == null) return `<div class="card notice" role="alert" data-audit-fallback="unknown"><b>▲ Could not check the audit fallback.</b><p class="small">Reload this page. If it keeps happening, check the server log.</p></div>`;
  const lost = a.not_stored ? `<p class="small"><b>${a.not_stored} event(s) could not be kept anywhere and were written to the server error log only.</b> Copy them from the log into the incident record.</p>` : "";
  if (!a.unreconciled) return lost ? `<div class="card notice" role="alert" data-audit-fallback="lost">${lost}</div>`
    : `<p class="small" data-audit-fallback="0"><span class="chip sla-green">✓ Audit trail complete</span> No audit events are waiting to be added.${a.last_reconciled_at ? ` Last late events added ${W.esc(W.dt(a.last_reconciled_at))}.` : ""}</p>`;
  return `<div class="card notice" role="alert" data-audit-fallback="${a.unreconciled}">
    <b>▲ ${a.unreconciled} audit event${a.unreconciled === 1 ? " is" : "s are"} waiting to be added to the audit trail.</b>
    <p class="small">The change${a.unreconciled === 1 ? " was" : "s were"} saved, but writing the audit record failed at the time (the database was busy or unavailable). The events are kept safely${a.in_file ? ` (${a.in_file} in the server's fallback file)` : ""} and are added automatically after the next deadline check, with their original times and marked “added late”.${a.oldest_at ? ` Oldest: ${W.esc(W.dt(a.oldest_at))}.` : ""}</p>${lost}
    <div class="row"><button type="button" id="reconcile-audit">Add them to the audit trail now</button></div></div>`;
}

// basic monitoring: timed-job health, reconciliation, waiting work (counts only; admin role only)
function monitoring(m) {
  if (!m) return "";
  const when = v => v ? W.dt(v) : "–";
  const chip = j => j.ok == null ? '<span class="chip">Not run yet</span>' : j.ok ? '<span class="chip sla-green">✓ OK</span>' : `<span class="chip sla-red">▲ Failed (${W.esc(j.last_error || "error")})</span>`;
  const jobs = (m.jobs || []).map(j => `<tr data-job="${W.esc(j.name)}"><td>${W.esc(j.label)}</td><td>${chip(j)}</td><td class="small nowrap">${when(j.last_ok_at)}</td><td class="small">${j.failures ? j.failures + " / " + j.runs : j.runs}</td></tr>`).join("");
  const r = m.result_reconciliation || {};
  const tiles = [["mon-unmatched", "Results without a case", r.open, r.open ? "danger" : "ok", "alert"],
    ["mon-inbox", "Inbox waiting", m.results_inbox_waiting, m.results_inbox_waiting ? "warning" : "ok", "inbox"],
    ["mon-esc", "Open escalations", m.open_escalations, "", "flag"]];
  return `<section class="card monitoring" aria-labelledby="mon-h"><h2 id="mon-h" class="card-title">${W.ic("pulse")}Monitoring</h2>
    <div class="stats">${tiles.map(([id, l, v, cls, ic]) => W.stat("", v, l, cls, ic, `id="${id}"`)).join("")}</div>
    <table class="compact"><thead><tr><th>Job</th><th>Status</th><th>Last success</th><th>Runs</th></tr></thead><tbody>${jobs}</tbody></table>
    <p class="small muted">Timed jobs every ${W.esc(String(m.sweep_interval_seconds))} s.</p>
    <div class="row"><button type="button" class="secondary" id="run-result-check">Check results now</button></div></section>`;
}

// draft-text suggestions on staff note fields: follow the same switch and need KARESHAFAA_SLM_API_KEY (no buttons here)
function drafts(d) {
  if (!d) return "";
  const t = d.enabled ? `On · ${W.esc(d.model || "")}` : d.reason === "not_configured" ? "Not set up (no API key)" : d.reason === "off" ? "Off with the switch" : "Unknown";
  return `<p class="small"><b>Note drafts:</b> ${t}</p>`;
}

// AI kill switch (advisory outreach-priority model and note drafts)
function aiSwitch(a) {
  if (!a) return "";
  const on = a.enabled === true;
  const state = a.enabled == null ? '<span class="chip">Unknown</span>'
    : on ? '<span class="chip sla-green">✓ On</span>' : '<span class="chip sla-amber">■ Off</span>';
  const locked = !a.deployment_enabled ? '<p class="small muted">Off in the server settings.</p>' : "";
  return `<section class="card ai-switch" aria-labelledby="ai-h" data-ai-enabled="${on}"><h2 id="ai-h" class="card-title"><span class="ai-switch-icon" aria-hidden="true">${W.ic("gauge")}</span>AI suggestions ${state}</h2>
    <p class="small muted">Queue order and note drafts. Off: deadline order, no drafts.</p>${drafts(a.drafts)}${locked}
    ${a.deployment_enabled ? `<div class="row"><label class="grow">Reason<input id="ai-reason" maxlength="300" minlength="10" autocomplete="off"></label>
    <button type="button" class="${on ? "danger" : "secondary"}" id="ai-toggle" data-on="${on ? "0" : "1"}">${on ? "Switch off" : "Switch on"}</button></div>` : ""}</section>`;
}

async function loadSystem() {
  try {
    const s = await W.api("/api/admin/system");
    document.getElementById("storage").innerHTML = (s.storage_persistent
      ? `<div class="card ok" role="status"><b>✓ Data is stored permanently</b> <span class="small">(${W.esc(s.database)}${s.database_host ? " · " + W.esc(s.database_host) : ""}).</span></div>`
      : `<div class="card notice" role="note"><b>▲ Data is not stored permanently.</b><p class="small">${W.esc(s.storage_note)}</p></div>`) + auditAlert(s.audit_fallback);
    const rows = [["Version", s.version], ["Database", s.database + (s.database_host ? " · " + s.database_host : "")],
      ["Active accounts", s.users_active], ["Patients", s.patients], ["Cases", s.cases],
      ["Sign-in sessions end after", s.session_idle_minutes + " minutes without activity"]];
    if (s.clinical_policy) rows.push(["Clinical policy", `${s.clinical_policy.version} · ${s.clinical_policy.note} · profile ${s.clinical_profile}`]);
    if (s.write_limit_per_minute) rows.push(["Change limit (abuse protection)", s.write_limit_per_minute + " changes per minute per user"]);
    document.getElementById("sys").innerHTML = `<article class="card"><dl class="facts">${rows.map(([k, v]) => `<div><dt>${W.esc(k)}</dt><dd>${W.esc(String(v))}</dd></div>`).join("")}</dl>
      <div class="row row-gap-top"><a role="button" class="secondary" href="/admin/users">Manage users</a><a role="button" class="ghost" href="/healthz">Health check (JSON)</a></div></article>` + monitoring(s.monitoring) + aiSwitch(s.ai);
    const at = document.getElementById("ai-toggle");
    if (at) at.addEventListener("click", async () => {
      const reason = (document.getElementById("ai-reason").value || "").trim();
      if (reason.length < 10) { W.toast("Give a reason (at least 10 characters)", "err"); return; }
      W.busy(at, true);
      try { const r = await W.api("/api/admin/system/ml-switch", { method: "POST", body: { enabled: at.dataset.on === "1", reason } });
        W.toast(r.enabled ? "AI suggestions on" : "AI suggestions off", "ok"); await loadSystem(); }
      catch (e) { W.busy(at, false); W.toast(e.message, "err"); }
    });
    const rc = document.getElementById("run-result-check");
    if (rc) rc.addEventListener("click", async () => {
      W.busy(rc, true);
      try { const r = await W.api("/api/admin/system/result-reconciliation/run", { method: "POST" });
        W.toast(r.open ? `${r.open} result(s) without a case – listed in the Results inbox` : "Every result has a case", r.open ? "" : "ok"); await loadSystem(); }
      catch (e) { W.busy(rc, false); W.toast(e.message, "err"); }
    });
    const b = document.getElementById("reconcile-audit");
    if (b) b.addEventListener("click", async () => {
      W.busy(b, true);
      try { const r = await W.api("/api/admin/system/audit-fallback/reconcile", { method: "POST" });
        W.toast(`${r.reconciled} event(s) added to the audit trail` + (r.remaining ? `, ${r.remaining} still waiting` : ""), r.remaining ? "" : "ok");
        await loadSystem(); }
      catch (e) { W.busy(b, false); W.toast(e.message, "err"); }
    });
  } catch (e) { document.getElementById("sys").innerHTML = W.empty("Could not load", W.esc(e.message)); }
}

document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return; await loadSystem(); });
