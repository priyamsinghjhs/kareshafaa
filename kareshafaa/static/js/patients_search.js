// patients.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
let timer = null;
async function find() {
  const q = document.getElementById("pq").value.trim(), el = document.getElementById("plist");
  history.replaceState(null, "", q ? "?q=" + encodeURIComponent(q) : location.pathname);
  if (q.length < 2) { el.innerHTML = `<p class="small muted">Type at least 2 characters to search.</p>`; return; }
  el.innerHTML = W.loading("Searching…");
  try { const d = await W.api("/api/patients?q=" + encodeURIComponent(q));
    el.innerHTML = d.items.length ? `<p class="small muted">${d.count} patient${d.count === 1 ? "" : "s"} found${d.count >= 30 ? " (first 30 – type more to narrow)" : ""}.</p><table class="compact"><thead><tr><th>Name</th><th>MRN</th><th>Date of birth</th><th>ID document</th><th>Phone</th><th>Health centre</th><th>Open cases</th></tr></thead><tbody>${d.items.map(p => `<tr>
      <td><a href="/patients/${p.id}"><b>${W.esc(p.name)}</b></a>${p.deceased ? ' <span class="chip sla-none">Deceased</span>' : ""}${p.sample ? ' <span class="chip">Sample record</span>' : ""}</td><td class="nowrap">${W.esc(p.mrn)}</td>
      <td class="nowrap">${W.esc(p.birth_date || (p.birth_year ? p.birth_year + " (year only)" : "–"))}</td><td class="nowrap">${p.document ? `<span class="sub small muted">${W.esc(p.document.label)}</span>${W.esc(p.document.number)}` : "–"}</td><td class="nowrap">${W.esc(p.phone || "–")}</td>
      <td>${W.esc(p.health_centre || "–")}</td><td>${p.open_cases}</td></tr>`).join("")}</tbody></table>`
      : W.empty("No patient found", W.can("patient:register") ? `<a href="/patients/new">Register a new patient</a>` : "", "", "users");
  } catch (e) { el.innerHTML = W.empty("Search failed", W.esc(e.message)); }
}
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  document.getElementById("reg-actions").hidden = !W.can("patient:register");
  const inp = document.getElementById("pq"); inp.value = new URLSearchParams(location.search).get("q") || "";
  inp.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(find, 300); });
  if (inp.value) find(); inp.focus(); });
