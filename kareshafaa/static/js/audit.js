// audit.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
async function load() {
  const ev = document.getElementById("ev").value.trim(), cid = document.getElementById("cid").value.trim();
  const d = await W.api(`/api/audit?limit=300${ev ? "&event=" + encodeURIComponent(ev) : ""}${cid ? "&case_id=" + cid : ""}`);
  document.getElementById("atable").innerHTML = d.items.length ? `<table class="compact"><thead><tr><th>#</th><th>When</th><th>Who</th><th>Event</th><th>Page or record</th><th>Case</th><th>Outcome</th><th>Hash</th></tr></thead><tbody>${d.items.map(e => `<tr><td>${e.id}</td><td class="small nowrap">${W.dt(e.ts)}</td><td class="small">${W.esc(e.actor)}<span class="sub">${W.esc(W.role(e.role))}</span></td><td class="small">${W.esc(e.event)}</td><td class="small">${W.esc(e.path || e.entity)}</td><td>${e.case_id ? `<a href="/cases/${e.case_id}">#${e.case_id}</a>` : ""}</td><td><span class="chip ${e.outcome === "success" ? "sla-green" : "sla-red"}">${e.outcome === "success" ? "✓" : "✕"} ${e.outcome}</span></td><td class="xs"><code>${e.hash.slice(0, 12)}…</code></td></tr>`).join("")}</tbody></table>` : W.empty("No matching events");
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.getElementById("af").onsubmit = e => { e.preventDefault(); load().catch(x => W.toast(x.message, "err")); };
  const vb = document.getElementById("verify");
  if (vb) vb.onclick = async ev => { const b = ev.currentTarget; b.setAttribute("aria-busy", "true");
    try { const r = await W.api("/api/audit/verify");
      document.getElementById("vres").innerHTML = `<div class="card ${r.valid ? "ok" : "alert"}" role="status">${r.valid ? `✓ The log is intact: ${r.checked} events verified.` : `✕ The chain is broken at event #${r.broken_at_id}.`}</div>`; }
    catch (e) { W.toast(e.message, "err"); } b.removeAttribute("aria-busy"); };
  try { await load(); } catch (e) { document.getElementById("atable").innerHTML = W.empty("Could not load", W.esc(e.message)); } });
