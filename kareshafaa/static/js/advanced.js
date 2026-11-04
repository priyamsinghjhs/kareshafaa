// advanced.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
document.addEventListener("DOMContentLoaded", async () => {
  await W.ready; if (!W.requireLogin()) return;
  document.querySelectorAll("[data-perm]").forEach(c => { if (!W.can(c.dataset.perm)) c.remove(); });
  const sw = document.getElementById("sweep");
  if (sw) sw.onclick = async () => { if (!(await W.confirm("Run the deadline check now?", "Due reminders and escalations will be raised for every open case.", { ok: "Run now" }))) return;
    sw.setAttribute("aria-busy", "true");
    try { const r = await W.api("/api/sla/sweep", { method: "POST", body: {} });
      W.toast(`Checked ${r.checked} cases: ${r.reminders} reminders, ${r.supervisor + r.clinical_lead + (r.director || 0)} escalations`, "ok"); } catch (e) { W.toast(e.message, "err"); }
    sw.removeAttribute("aria-busy"); };
  try { const h = await fetch("/healthz").then(r => r.json());
    document.getElementById("sys").textContent = `Status ${h.status} · version ${h.version} · ${h.storage === "persistent" ? "data stored permanently" : "temporary storage"} · timed-out requests ${h.request_timeouts}`; }
  catch (e) { document.getElementById("sys").textContent = "Health check unavailable."; }
});
