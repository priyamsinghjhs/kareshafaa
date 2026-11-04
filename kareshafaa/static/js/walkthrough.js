// walkthrough.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
async function load() {
  const d = await W.api("/api/demo/scenarios");
  const canRun = W.can("case:create") || W.can("sweep:run");
  document.getElementById("sc").innerHTML = d.items.map(s => `<article class="card"><div><h2 style="font-size:var(--fs-lg)">${W.esc(s.title)}</h2>
    <p class="small muted">${W.esc(s.requirements)}</p>
    <p class="small">Best viewed as: <code>${W.esc(s.login)}</code></p></div>
    <button ${canRun ? "" : "disabled"} data-click="run" data-args="${W.esc(JSON.stringify([s.key, "$el"]))}">Start this walkthrough</button></article>`).join("");
}
window.run = async (key, btn) => {
  btn.setAttribute("aria-busy", "true"); btn.disabled = true;
  try {
    const r = await W.api(`/api/demo/scenarios/${key}`, { method: "POST", body: {} });
    document.getElementById("result").innerHTML = `<div class="card ok" role="status"><h2 style="font-size:var(--fs-lg)">Ready: case #${r.case_id} (${W.esc(r.state)})</h2>
      <ol class="small">${r.next.map(n => `<li>${W.esc(n)}</li>`).join("")}</ol>
      <div class="row"><a role="button" href="/cases/${r.case_id}">Open case #${r.case_id}</a><span class="small muted">To continue as <code>${W.esc(r.login)}</code>, sign out and sign in with that account.</span></div></div>`;
    document.getElementById("result").scrollIntoView({ behavior: "smooth" }); W.toast("Walkthrough ready", "ok");
  } catch (e) { W.toast(e.message, "err"); }
  btn.removeAttribute("aria-busy"); btn.disabled = false;
};
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return; load().catch(e => document.getElementById("sc").innerHTML = W.empty("Could not load", W.esc(e.message))); });
