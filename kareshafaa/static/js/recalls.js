// Due for recall: list, simulated invitations (single and selected), cancel with a reason.
const P = new URLSearchParams(location.search);
let D = null, timer = null;
const CH = { sms: "SMS", whatsapp: "WhatsApp", app: "App" };
const ST = { due: ["Due", "sla-amber"], invited: ["Invited", "sla-green"], completed: ["Screened", "sla-green"], cancelled: ["Cancelled", "sla-none"], superseded: ["Replaced", "sla-none"] };
const day = s => s ? new Date(s + "T12:00:00").toLocaleDateString(undefined, { day: "2-digit", month: "short", year: "numeric" }) : "–";
function sel() { return [...document.querySelectorAll("#rlist input[data-sel]:checked")].map(x => +x.dataset.sel); }
function updSel() { const n = sel().length; document.getElementById("selcount").textContent = n ? `${n} selected` : ""; document.getElementById("inv-sel").disabled = !n; }
async function summary() {
  try { const s = await W.api("/api/recalls/summary");
    const rate = s.denominator ? Math.round(100 * s.completed_in_window / s.denominator) + "%" : "–";
    document.getElementById("rsum").innerHTML = W.stat("", s.open_due_now, "Due now", s.open_due_now ? "warning" : "ok", "calendar") + W.stat("", s.invited_open, "Invited, waiting", "", "chat") + W.stat("", rate, `Uptake (${s.window_days} d)`, "", "ok", `title="Screened within ${s.window_days} days of the due date"`);
  } catch (e) { document.getElementById("rsum").innerHTML = ""; }
}
async function load() {
  const v = document.getElementById("rv").value, pg = document.getElementById("rp").value, q = document.getElementById("rq").value.trim();
  const qs = new URLSearchParams({ view: v }); if (pg) qs.set("programme", pg); if (q.length >= 2) qs.set("q", q);
  history.replaceState(null, "", "?" + qs);
  const el = document.getElementById("rlist"); el.innerHTML = W.loading();
  try { D = await W.api("/api/recalls?" + qs);
    const can = D.can_manage && ["due", "overdue", "invited", "upcoming"].includes(v);
    document.getElementById("rbulk").hidden = !can || !D.items.length;
    el.innerHTML = D.items.length ? `<p class="small muted">${D.count} ${D.count === 1 ? "person" : "people"}${D.shown < D.count ? ` (first ${D.shown} shown – filter to narrow)` : ""}.</p>
      <table class="compact"><thead><tr>${can ? '<th><span class="sr-only">Select</span></th>' : ""}<th>Person</th><th>Programme</th><th>Due</th><th>Why</th><th>Status</th><th>Health centre</th>${can ? "<th>Actions</th>" : ""}</tr></thead><tbody>${D.items.map(r => { const [sl, sc] = ST[r.status] || [r.status, ""];
        return `<tr>${can ? `<td><input type="checkbox" data-sel="${r.id}" aria-label="Select ${W.esc(r.patient)}" ${r.phone || r.has_smartphone ? "" : "disabled"}></td>` : ""}
        <td><a href="/patients/${r.patient_id}"><b>${W.esc(r.patient)}</b></a><span class="sub">${W.esc(r.mrn)}${r.sample ? " · sample record" : ""}</span></td>
        <td>${W.prog(r.programme)}</td><td class="nowrap">${day(r.due_on)}${r.days_overdue > 0 && r.status !== "completed" ? `<span class="sub overdue-text">${r.days_overdue} days overdue</span>` : ""}</td>
        <td class="small">${W.esc(r.reason_text)}${r.source_case_id ? ` · <a href="/cases/${r.source_case_id}">case #${r.source_case_id}</a>` : ""}</td>
        <td><span class="chip ${sc}">${sl}</span>${r.invited_at ? `<span class="sub">${r.invite_count}× · last ${W.dt(r.invited_at)} (${CH[r.last_invite_channel] || r.last_invite_channel})</span>` : ""}${r.completed_case_id ? `<span class="sub"><a href="/cases/${r.completed_case_id}">new case #${r.completed_case_id}</a></span>` : ""}${r.closed_note ? `<span class="sub">${W.esc(r.closed_note)}</span>` : ""}</td>
        <td class="small">${W.esc(r.health_centre || "–")}</td>
        ${can ? `<td><div class="row"><button class="sm" data-inv="${r.id}" ${r.phone || r.has_smartphone ? "" : 'disabled title="No mobile number or app – phone the patient"'}>${r.invite_count ? "Invite again" : "Send invitation"}</button><button class="secondary sm" data-cancel="${r.id}">Cancel</button></div>${r.phone || r.has_smartphone ? "" : '<span class="sub small">No mobile or app – phone them</span>'}</td>` : ""}</tr>`; }).join("")}</tbody></table>`
      : W.empty(v === "due" ? "No one due in the next 60 days" : "Nobody in this list", "", "", "calendar");
    el.querySelectorAll("[data-inv]").forEach(b => b.onclick = async () => { W.busy(b, true);
      try { const r = await W.api(`/api/recalls/${b.dataset.inv}/invite`, { method: "POST" }); W.toast(`Invitation sent by ${CH[r.channel] || r.channel} (simulated)`, "ok"); await load(); summary(); }
      catch (e) { W.busy(b, false); W.toast(e.message, "err"); } });
    el.querySelectorAll("[data-cancel]").forEach(b => b.onclick = async () => {
      const why = await W.ask("Cancel this recall?", "Reason", { ok: "Cancel recall", min: 5, danger: true, placeholder: "e.g. screened privately abroad; moved to another emirate" });
      if (!why) return;
      try { await W.api(`/api/recalls/${b.dataset.cancel}/cancel`, { method: "POST", body: { reason: why } }); W.toast("Recall cancelled", "ok"); await load(); summary(); }
      catch (e) { W.toast(e.message, "err"); } });
    el.querySelectorAll("[data-sel]").forEach(c => c.onchange = updSel); updSel();
  } catch (e) { el.innerHTML = W.empty("Could not load recalls", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.getElementById("rv").value = P.get("view") || "due"; document.getElementById("rp").value = P.get("programme") || ""; document.getElementById("rq").value = P.get("q") || "";
  ["rv", "rp"].forEach(i => document.getElementById(i).onchange = load);
  document.getElementById("rq").oninput = () => { clearTimeout(timer); timer = setTimeout(load, 300); };
  document.getElementById("inv-sel").onclick = async ev => { const ids = sel(); if (!ids.length) return; const b = ev.target; W.busy(b, true);
    try { const r = await W.api("/api/recalls/invite", { method: "POST", body: { ids } });
      W.toast(`${r.sent.length} invitation${r.sent.length === 1 ? "" : "s"} sent (simulated)` + (r.failed.length ? ` · ${r.failed.length} not sent: ${r.failed[0].reason}` : ""), r.failed.length ? "" : "ok"); }
    catch (e) { W.toast(e.message, "err"); }
    W.busy(b, false); await load(); summary(); };
  summary(); load(); });
