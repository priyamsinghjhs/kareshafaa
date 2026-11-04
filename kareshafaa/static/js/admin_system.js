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

async function loadSystem() {
  try {
    const s = await W.api("/api/admin/system");
    document.getElementById("storage").innerHTML = (s.storage_persistent
      ? `<div class="card ok" role="status"><b>✓ Data is stored permanently</b> <span class="small">(${W.esc(s.database)}${s.database_host ? " · " + W.esc(s.database_host) : ""}).</span></div>`
      : `<div class="card notice" role="note"><b>▲ Data is not stored permanently.</b><p class="small">${W.esc(s.storage_note)}</p></div>`) + auditAlert(s.audit_fallback);
    const rows = [["Version", s.version], ["Database", s.database + (s.database_host ? " · " + s.database_host : "")],
      ["Active accounts", s.users_active], ["Patients", s.patients], ["Cases", s.cases],
      ["Sign-in sessions end after", s.session_idle_minutes + " minutes without activity"]];
    if (s.write_limit_per_minute) rows.push(["Change limit (abuse protection)", s.write_limit_per_minute + " changes per minute per user"]);
    document.getElementById("sys").innerHTML = `<article class="card"><dl class="facts">${rows.map(([k, v]) => `<div><dt>${W.esc(k)}</dt><dd>${W.esc(String(v))}</dd></div>`).join("")}</dl>
      <div class="row row-gap-top"><a role="button" class="secondary" href="/admin/users">Manage users</a><a role="button" class="ghost" href="/healthz">Health check (JSON)</a></div></article>`;
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
