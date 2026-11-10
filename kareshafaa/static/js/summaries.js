// Care summaries inbox (GP): read the summary written at care start / case closure, acknowledge with an optional note.
let VIEW = new URLSearchParams(location.search).get("view") === "all" ? "all" : "unread";
const KIND = { care_started: "Care started", case_closed: "Case closed" };
function card(s) {
  const c = s.content || {}, r = c.result || {}, o = c.outcome;
  const li = a => (a || []).length ? `<ul class="small">${a.map(x => `<li>${W.esc(x)}</li>`).join("")}</ul>` : `<p class="small muted">None recorded</p>`;
  return `<article class="card sum-card${s.acknowledged_at ? "" : " unread"}" id="s-${s.id}">
    <div class="row" style="justify-content:space-between;align-items:flex-start"><div><h2 class="sec-title" style="margin:0">${W.esc(s.title)}</h2>
      <p class="small muted" style="margin:2px 0 0">${W.dt(s.created_at)} · from ${W.esc(s.created_by)} · to ${W.esc(s.to || "–")}${s.addressed_to_me === false ? " (your practice)" : ""}</p></div>
      <div>${s.acknowledged_at ? `<span class="chip sla-green">Acknowledged</span>` : (s.overdue ? `<span class="chip sla-red">Not acknowledged · overdue</span>` : `<span class="chip sla-amber">${KIND[s.kind] || ""} · to acknowledge</span>`)}</div></div>
    <dl class="facts"><div><dt>Patient</dt><dd><a href="/patients/${s.patient_id}">${W.esc((c.patient || {}).name || "")}</a> <span class="small muted">${W.esc((c.patient || {}).mrn || "")}</span></dd></div>
      <div><dt>Screening result</dt><dd>${W.esc(r.test || "–")} ${r.date ? "· " + W.esc(r.date) : ""}<div class="small">${W.esc((r.values || []).join("; "))}</div></dd></div>
      ${o ? `<div><dt>Closed</dt><dd>${W.esc(o.closed_on || "")} · ${W.esc(o.reason || o.how || "")}${o.note ? `<div class="small">${W.esc(o.note)}</div>` : ""}</dd></div>` : ""}</dl>
    <h3 class="small">Pathway</h3>${li(c.pathway)}
    ${(c.care_plans || []).length ? `<h3 class="small">Care plan</h3><ul class="small">${c.care_plans.map(p => `<li><b>${W.esc(p.kind)}</b> (${W.esc(p.date || "")}): ${W.esc(p.description)}${p.review_due_on ? ` · review due ${W.esc(p.review_due_on)}` : ""}</li>`).join("")}</ul>` : ""}
    <h3 class="small">Next steps</h3>${li(c.next_steps)}
    <p class="small"><a href="/cases/${s.case_id}">Open case #${s.case_id}</a></p>
    ${s.acknowledged_at ? `<p class="small muted">Acknowledged ${W.dt(s.acknowledged_at)} by ${W.esc(s.acknowledged_by || "")}${s.ack_note ? `: ${W.esc(s.ack_note)}` : ""}</p>`
      : (s.can_ack ? `<form class="ack-form" data-id="${s.id}" novalidate><label>Note (optional)<textarea name="note" rows="2" maxlength="1000" placeholder="e.g. reviewed; will see at next diabetes clinic"></textarea></label><button type="submit" class="sm">Acknowledge</button></form>` : "")}
  </article>`;
}
async function load() {
  document.querySelectorAll(".tabs [data-v]").forEach(b => { const on = b.dataset.v === VIEW; b.setAttribute("aria-selected", on); b.classList.toggle("secondary", !on); });
  history.replaceState(null, "", VIEW === "all" ? "?view=all" : location.pathname);
  const el = document.getElementById("slist"); el.innerHTML = W.loading();
  try { const d = await W.api("/api/gp-summaries?view=" + VIEW);
    document.getElementById("n-unread").textContent = d.unread ? `(${d.unread})` : "";
    el.innerHTML = d.items.length ? `<p class="small muted">Please acknowledge within ${d.ack_days} days.</p>` + d.items.map(card).join("")
      : W.empty(VIEW === "unread" ? "Nothing to acknowledge" : "No summaries yet", "", "", "inbox");
    el.querySelectorAll("form.ack-form").forEach(f => f.onsubmit = async ev => { ev.preventDefault(); const b = f.querySelector("button"); W.busy(b, true);
      try { await W.api(`/api/gp-summaries/${f.dataset.id}/ack`, { method: "POST", body: { note: f.note.value.trim() || null } }); W.toast("Summary acknowledged", "ok"); await load(); }
      catch (e) { W.busy(b, false); W.formError(f, e.message); } });
  } catch (e) { el.innerHTML = W.empty("Could not load summaries", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll(".tabs [data-v]").forEach(b => b.onclick = () => { VIEW = b.dataset.v; load(); });
  load(); });
