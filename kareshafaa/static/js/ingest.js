// ingest.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
async function sample() {
  try { const t = await W.api("/api/ingest/sample"); document.getElementById("msg").value = t.split("\r").join("\n"); } catch (e) { W.toast(e.message, "err"); }
}
async function send(b) {
  const raw = document.getElementById("msg").value.trim().split(/\r?\n/).join("\r");
  if (!raw) { W.toast("Paste a message or fill in the sample first.", "err"); return; }
  W.busy(b, true);
  try {
    const r = await W.api("/api/ingest/hl7v2", { method: "POST", raw, headers: { "Content-Type": "text/plain" } });
    const a = document.getElementById("ack"); a.hidden = false;
    a.textContent = r.ack.replaceAll("\r", "\n") + `\n\nstatus: ${r.status}` + (r.case ? ` · case #${r.case.id} (${r.case.state})` : "") + (r.reason ? ` · ${r.reason}` : "");
    W.toast(r.case ? `Case #${r.case.id} created or updated` : r.status, r.ok ? "ok" : "err"); load();
  } catch (e) { W.toast(e.message, "err"); }
  W.busy(b, false);
}
async function load() {
  const d = await W.api("/api/ingest/messages");
  const pend = d.items.filter(m => m.status === "needs_match_review");
  document.getElementById("review").innerHTML = pend.length ? pend.map(m => `<div class="card flat"><p class="small"><b>${W.esc(m.message_id)}</b> · ${W.esc(m.patient_identifier || "no patient ID")} · ${W.prog(m.programme)}<br><span class="muted">${W.esc(m.reason)}</span></p>
    <div class="row"><label for="pid${m.id}" class="grow" style="margin:0">Confirmed patient number<input id="pid${m.id}" inputmode="numeric" pattern="[0-9]*"></label><button style="align-self:end" data-click="resolve" data-args='[${m.id},"$el"]'>Confirm match</button></div>
    ${W.can("patient:register") || W.can("patient:read") ? `<p class="small" style="margin:var(--s-2) 0 0">${m.patient_name ? `Name in the message: <b>${W.esc(m.patient_name)}</b>${m.patient_birth_date ? ", born " + W.esc(m.patient_birth_date) : ""}. ` : ""}${W.can("patient:read") ? `<a href="/patients?q=${encodeURIComponent(m.patient_name || m.patient_identifier || "")}" target="_blank" rel="noopener">Search patients</a>` : ""}${W.can("patient:register") ? ` · Not registered yet? <a href="/patients/new?from_message=${m.id}">Register a new patient from this result</a>` : ""}</p>` : ""}</div>`).join("") : `<p class="small muted" style="margin:0">✓ Nothing waiting – every result was matched to a patient.</p>`;
  document.getElementById("recent").innerHTML = d.items.length ? `<table class="compact"><thead><tr><th>Received</th><th>Source</th><th>Message</th><th>Programme</th><th>Status</th><th>Case</th></tr></thead><tbody>${d.items.slice(0, 30).map(m => `<tr><td class="small nowrap">${W.dt(m.received_at)}</td><td>${W.esc(m.source)}</td><td class="small">${W.esc(m.message_id)}</td><td>${W.prog(m.programme)}</td><td><span class="chip ${m.status === "needs_match_review" ? "sla-amber" : "sla-green"}">${m.status === "needs_match_review" ? "● Needs match" : "✓ " + W.esc(m.status.replace(/_/g, " "))}</span></td><td>${m.case_id ? `<a href="/cases/${m.case_id}">Case #${m.case_id}</a>` : "–"}</td></tr>`).join("")}</tbody></table>` : W.empty("No results yet", "Add the sample result below to see a case appear with an owner and a deadline.");
}
window.resolve = async (id, b) => { const v = +document.getElementById("pid" + id).value; if (!v) { W.toast("Enter the patient number first.", "err"); return; }
  if (!(await W.confirm("Confirm this patient match?", "The result will be filed to patient #" + v + ". Check the identity first.", { ok: "Confirm match" }))) return;
  W.busy(b, true);
  try { const r = await W.api(`/api/ingest/messages/${id}/resolve`, { method: "POST", body: { patient_id: v } }); W.toast("Matched – case " + (r.case ? "#" + r.case.id : "n/a"), "ok"); await load(); } catch (e) { W.toast(e.message, "err"); W.busy(b, false); } };
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return; load().catch(e => document.getElementById("review").innerHTML = W.empty("Could not load", W.esc(e.message))); });
