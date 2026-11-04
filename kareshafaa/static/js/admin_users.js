// admin_users.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
let U = null;
const PD = KS.read_only;
const status = u => !u.active ? '<span class="chip sla-none">✕ Deactivated</span>' : u.locked ? '<span class="chip sla-red">▲ Locked (too many attempts)</span>' : u.must_change_password ? '<span class="chip sla-amber">● Must set password</span>' : u.has_password ? '<span class="chip sla-green">✓ Active</span>' : '<span class="chip sla-none">– No password yet</span>';
function render() {
  const q = document.getElementById("uq").value.trim().toLowerCase(), r = document.getElementById("ur").value, st = document.getElementById("us").value;
  const rows = U.items.filter(u => (!q || (u.display_name + " " + u.username).toLowerCase().includes(q)) && (!r || u.role === r) && (!st || (st === "active") === u.active));
  document.getElementById("users").innerHTML = rows.length ? `<p class="small muted">${rows.length} of ${U.items.length} accounts</p><table class="compact"><thead><tr><th>Name</th><th>Role</th><th>Site / team</th><th>Status</th><th>Last sign-in</th><th>Actions</th></tr></thead><tbody>${rows.map(u => `<tr>
    <td><b>${W.esc(u.display_name.split(" (")[0])}</b><span class="sub">${W.esc(u.username)}</span></td>
    <td>${W.esc(u.role_label)}</td><td class="small">${W.esc(u.site || "–")}${u.team ? `<span class="sub">${W.esc(u.team)}</span>` : ""}</td><td>${status(u)}${u.status_reason ? `<span class="sub">${u.active ? "Reactivated" : "Deactivated"}${u.status_changed_by ? " by " + W.esc(u.status_changed_by) : ""}${u.status_changed_at ? " · " + W.dt(u.status_changed_at) : ""}: ${W.esc(u.status_reason)}</span>` : ""}</td>
    <td class="small">${u.last_login_at ? W.dt(u.last_login_at) : "Never"}</td>
    <td>${PD ? '<span class="small muted">Read-only</span>' : `<div class="row">${u.role !== "patient" && u.id !== W.me.user_id ? `<button class="secondary sm" data-click="changeRole" data-args="[${u.id}]">Change role</button>` : ""}${u.role === "patient" ? "" : `<button class="secondary sm" data-click="resetPw" data-args="[${u.id}]">Reset password</button>`}${u.id === W.me.user_id ? "" : u.active ? `<button class="danger secondary sm" data-click="deact" data-args="[${u.id}]">Deactivate</button>` : `<button class="secondary sm" data-click="act" data-args="[${u.id}]">Reactivate</button>`}</div>`}</td></tr>`).join("")}</tbody></table>` : W.empty("No users match", "Try a different name or filter.");
}
async function load() { U = await W.api("/api/admin/users"); render(); }
async function storageNote() {  // one-time notice (per browser) when data is not stored permanently
  if (localStorage.getItem("ks.storage-note.dismissed")) return;
  try { const s = await W.api("/api/admin/system"); if (s.storage_persistent) return;
    const el = document.getElementById("storage-note");
    el.innerHTML = `<div class="card notice" role="note"><b>Data is not stored permanently on this server.</b> <span class="small">${W.esc(s.storage_note)} See <a href="/admin/system">System</a>.</span> <button class="ghost sm" id="storage-ok">Got it</button></div>`;
    document.getElementById("storage-ok").onclick = () => { localStorage.setItem("ks.storage-note.dismissed", "1"); el.innerHTML = ""; };
  } catch (e) { /* the System page shows the same information */ } }
const byId = id => U.items.find(u => u.id === id);
// ---- sign-in help requests from staff (admins only) ----
let H = null, HELP_FOR = null;
const NEED = { username: "Forgot username", password: "Needs a new password" };
async function loadHelp() {
  H = await W.api("/api/signin-help?audience=staff&status=open");
  const box = document.getElementById("help-reqs"); box.hidden = false;
  document.getElementById("help-count").textContent = H.open ? `${H.open} waiting${H.overdue ? ` · ${H.overdue} past reply-due time` : ""}` : "none waiting";
  const roleLabel = k => (U && (U.roles || []).find(r => r.key === k) || {}).label || (k === "other" ? "Other" : k);
  document.getElementById("help-list").innerHTML = H.items.length ? `<ul class="preq-list">${H.items.map(r => `<li class="preq-item" data-help-request="${r.id}">
    <div class="chips">${W.sla(r.rag, r.elapsed)} <span class="small muted">reply due ${W.ago(r.due_at)}</span> <span class="chip">${W.esc(NEED[r.kind] || r.kind)}</span>${HELP_FOR === r.id ? ' <span class="chip flag">Selected for reset</span>' : ""}</div>
    <div><b>${W.esc(r.full_name)}</b> <span class="sub">${W.esc(roleLabel(r.role))} · ${W.esc(r.org)} · asked ${W.dt(r.created_at)}</span></div>
    <div class="small">Contact: <span dir="ltr">${W.esc(r.contact)}</span></div>${r.note ? `<div class="small muted">Note: ${W.esc(r.note)}</div>` : ""}
    <div class="tb-actions">${PD ? "" : `<button type="button" class="secondary sm" data-help-find="${r.id}">Find in the list</button><button type="button" class="secondary sm" data-help-close="${r.id}">Mark handled…</button>`}</div></li>`).join("")}</ul>`
    : '<p class="small muted" data-help-empty>No sign-in help requests are waiting.</p>';
  document.querySelectorAll("[data-help-find]").forEach(b => b.onclick = () => { const r = H.items.find(x => x.id === +b.dataset.helpFind); HELP_FOR = r.id;
    const uq = document.getElementById("uq"); uq.value = r.full_name.split(" ")[0]; document.getElementById("us").value = ""; render(); loadHelp();
    W.toast("Check the matches below, then use Reset password on the right row", "ok"); document.getElementById("users").scrollIntoView({ block: "start" }); });
  document.querySelectorAll("[data-help-close]").forEach(b => b.onclick = async () => { const r = H.items.find(x => x.id === +b.dataset.helpClose);
    await W.ask(`Mark the request from ${r.full_name} as handled?`, "What did you do? (kept in the audit trail)", { ok: "Mark handled", min: 5, placeholder: "e.g. called back on the ward number; told them their username",
      save: async note => { await W.api(`/api/signin-help/${r.id}/close`, { method: "POST", body: { outcome: "done", note } }); if (HELP_FOR === r.id) HELP_FOR = null; W.toast("Request closed", "ok"); await loadHelp(); } }); });
}
// ---- staff directory record (clinical roles only): link an existing record or create a new one ----
const isClinical = r => !!(U.staff_rules || {})[r];
const freeFor = r => U.unlinked_practitioners.filter(p => p.role === r);
function staffBlock(px, role, { current = null } = {}) {  // px: id prefix so two forms never share ids
  const rule = U.staff_rules[role], label = (U.roles.find(x => x.key === role) || {}).label || role, noun = rule ? rule.noun : label;
  if (!rule) return `<p class="small muted" id="${px}-none">${W.esc(label)} accounts have no staff directory record (only care coordinators, GPs, specialists and clinical leads have one, so cases can be assigned to them).</p>`;
  if (current && current.role === role) return `<p class="small muted" id="${px}-keep">Keeps the staff directory record: ${W.esc(current.name)}.</p>`;
  const free = freeFor(role), orgs = U.organizations.filter(o => rule.org_kinds.includes(o.kind));
  const opt = (v, t, sel) => `<option value="${W.esc(v)}" ${sel ? "selected" : ""}>${W.esc(t)}</option>`;
  const orgOpts = (orgs.length === 1 ? "" : opt("", `Choose the ${rule.org_noun}…`)) + orgs.map(o => opt(o.id, o.name + (o.region ? ` (${o.region})` : ""), orgs.length === 1)).join("");
  const progOpts = (rule.programme === "required" ? opt("", "Choose the programme…") : opt("", "All programmes (programme medical director)")) + U.programmes.map(p => opt(p.key, p.label)).join("");
  const specOpts = opt("", "Choose the specialty…") + U.specialties.map(x => opt(x.key, x.label)).join("");
  const pracOpts = opt("", `Choose a ${noun} record…`) + free.map(p => opt(p.id, p.name + (p.org ? ` – ${p.org}` : ""))).join("");
  return `<fieldset class="staff-rec" id="${px}-rec"><legend>Staff directory record</legend>
    <p class="small muted" style="margin:0">Needed so cases can be assigned to this ${W.esc(noun)}; it also sets which ${rule.org_kinds.includes("primary_care") ? "practice's" : rule.org_kinds.includes("hospital") ? "hospital's" : "programme's"} patients they see.</p>
    <div class="sr-mode" role="radiogroup" aria-label="Staff directory record">
      <label><input type="radio" name="${px}-mode" value="new" ${free.length ? "" : "checked"}> Create a new staff record</label>
      <label class="${free.length ? "" : "off"}"><input type="radio" name="${px}-mode" value="link" ${free.length ? "checked" : "disabled"}> Link an existing record ${free.length ? `(${free.length} without an account)` : "(none without an account)"}</label></div>
    <div id="${px}-link" ${free.length ? "" : "hidden"}><label for="${px}-prac">Existing ${W.esc(noun)} record<select id="${px}-prac" data-field="practitioner_id" data-what="existing ${W.esc(noun)} record" required>${pracOpts}</select></label></div>
    <div id="${px}-new" class="grid-2" style="gap:var(--s-2) var(--s-3)" ${free.length ? "hidden" : ""}>
      <label for="${px}-org">${W.esc(rule.org_label)}<select id="${px}-org" data-field="org_id" data-what="${W.esc(rule.org_noun)}" required>${orgOpts}</select></label>
      ${rule.specialty ? `<label for="${px}-spec">Specialty<select id="${px}-spec" data-field="specialty" data-what="specialty" required>${specOpts}</select></label>` : ""}
      ${rule.programme ? `<label for="${px}-prog">Screening programme${rule.programme === "optional" ? " (optional)" : ""}<select id="${px}-prog" data-field="programme" data-what="screening programme" ${rule.programme === "required" ? "required" : ""}>${progOpts}</select></label>` : ""}
    </div></fieldset>`;
}
function wireStaff(root, px) {  // show the fields of the chosen mode; hidden fields are disabled (not sent, not validated)
  const set = () => { const m = (root.querySelector(`input[name="${px}-mode"]:checked`) || {}).value;
    [["link", `#${px}-link`], ["new", `#${px}-new`]].forEach(([k, sel]) => { const el = root.querySelector(sel); if (!el) return;
      el.hidden = m !== k; el.querySelectorAll("select").forEach(x => { x.disabled = m !== k; x.removeAttribute("aria-invalid"); }); });
    clearFieldErrs(root); };
  root.querySelectorAll(`input[name="${px}-mode"]`).forEach(r => r.addEventListener("change", set)); set();
}
function staffBody(root, px) {  // -> {practitioner_id} | {staff_record} | {}
  const m = (root.querySelector(`input[name="${px}-mode"]:checked`) || {}).value, v = id => { const el = root.querySelector("#" + px + "-" + id); return el && !el.disabled ? el.value : ""; };
  if (!root.querySelector(`#${px}-rec`)) return {};
  if (m === "link") return { practitioner_id: v("prac") ? +v("prac") : null };
  return { staff_record: { org_id: v("org") ? +v("org") : null, specialty: v("spec") || null, programme: v("prog") || null } };
}
function clearFieldErrs(root) { root.querySelectorAll(".field-err[data-for]").forEach(e => e.remove()); root.querySelectorAll("[aria-invalid]").forEach(e => e.removeAttribute("aria-invalid")); }
function fieldErr(root, field, msg) {  // message under the field the server (or the check below) named
  const el = [...root.querySelectorAll(`[data-field="${field}"], [name="${field}"]`)].find(x => !x.disabled && !x.closest("[hidden]"));
  if (!el) return false; el.setAttribute("aria-invalid", "true");
  const m = document.createElement("span"); m.className = "field-err"; m.dataset.for = field; m.setAttribute("role", "alert"); m.textContent = msg;
  (el.closest("label") || el).appendChild(m); return true;
}
function checkStaff(root, px) {  // clear messages before anything is sent
  let first = null;
  root.querySelectorAll(`#${px}-rec select[required]`).forEach(x => { if (!x.disabled && !x.value) {
    fieldErr(root, x.dataset.field, `Choose the ${x.dataset.what}.`); first = first || x; } });
  if (first) { first.focus(); return false; } return true;
}
function showTemp(u, pw, what) {
  document.getElementById("created").innerHTML = `<div class="card ok" role="status"><h2 style="font-size:var(--fs-lg)">${what} for ${W.esc(u.display_name)}</h2>
   <p>Temporary password (shown once - give it to them privately; they must change it when they sign in):</p>
   <p><code style="font-size:var(--fs-lg)" id="tmp-pw">${W.esc(pw)}</code></p><div class="row"><button class="secondary sm" data-click="copyTmpPw">Copy</button><button class="ghost sm" data-click="clearCreated">Done</button></div></div>`;
  document.getElementById("created").scrollIntoView({ behavior: "smooth" });
}
window.resetPw = async id => { const u = byId(id), hr = HELP_FOR && H ? H.items.find(x => x.id === HELP_FOR) : null;
  if (!(await W.confirm(`Reset the password for ${u.display_name}?`, `They will be signed out everywhere and must choose a new password at next sign-in.${hr ? ` This also closes the sign-in help request from ${W.esc(hr.full_name)}.` : ""}`, { ok: "Reset password" }))) return;
  try { const r = await W.api(`/api/admin/users/${id}/reset-password`, { method: "POST", body: hr ? { help_request_id: hr.id } : {} }); showTemp(u, r.temporary_password, "Password reset");
    if (hr) { HELP_FOR = null; loadHelp().catch(() => {}); } load(); } catch (e) { W.toast(e.message, "err"); } };
window.deact = async id => { const u = byId(id);
  await W.ask(`Deactivate ${u.display_name}?`, "Reason (shown on the user list and kept in the audit trail)", { ok: "Deactivate", danger: true, min: 3, body: "They are signed out at once and can no longer sign in. Their history is kept.",
    save: async why => { await W.api(`/api/admin/users/${id}/deactivate`, { method: "POST", body: { reason: why } }); W.toast("Account deactivated", "ok"); await load(); } }); };
window.act = async id => { const u = byId(id);
  await W.ask(`Reactivate ${u.display_name}?`, "Reason (optional)", { ok: "Reactivate", optional: true,
    save: async why => { await W.api(`/api/admin/users/${id}/activate`, { method: "POST", body: { reason: why || null } }); W.toast("Account reactivated", "ok"); await load(); } }); };
window.changeRole = async id => { const u = byId(id);
  const opts = U.roles.map(r => `<option value="${r.key}" ${r.key === u.role ? "selected" : ""}>${W.esc(r.label)}</option>`).join("");
  const cur = u.practitioner_id ? (U.practitioners_by_id || {})[u.practitioner_id] || { role: u.role, name: "linked record" } : null;
  const pr = W._dialog(`<h2 id="dlg-title">Change role for ${W.esc(u.display_name)}</h2><label for="dr">Role<select id="dr">${opts}</select></label>
    <div id="dr-staff-wrap"></div>
    <label for="ds">Site<input id="ds" value="${W.esc(u.site || "")}" list="f-site-list"></label><label for="dt">Team<input id="dt" value="${W.esc(u.team || "")}"></label>
    <p class="small muted">A role change signs them out; the new permissions apply at their next sign-in.</p>
    <div class="dlg-buttons"><button type="button" class="secondary" data-cancel>Cancel</button><button type="submit" data-ok>Save</button></div>`,
    f => { clearFieldErrs(f); if (!checkStaff(f, "dr")) return false;
      return { role: f.querySelector("#dr").value, site: f.querySelector("#ds").value.trim(), team: f.querySelector("#dt").value.trim(), ...staffBody(f, "dr") }; }, "",
    async body => { try { await W.api(`/api/admin/users/${id}/role`, { method: "POST", body }); }
      catch (e) { const f = document.querySelector("dialog.dlg[open] form"); if (f && e.data && e.data.field) fieldErr(f, e.data.field, e.message); throw e; }
      W.toast("Saved", "ok"); await load(); });
  const f = document.querySelector("dialog.dlg[open] form"), wrap = f.querySelector("#dr-staff-wrap"); f.noValidate = true;
  const draw = () => { const r = f.querySelector("#dr").value; wrap.innerHTML = r === u.role && !isClinical(r) ? "" : staffBlock("dr", r, { current: r === u.role ? cur : (cur && cur.role === r ? cur : null) }); wireStaff(f, "dr"); };
  f.querySelector("#dr").addEventListener("change", draw); draw();
  return pr; };
document.addEventListener("DOMContentLoaded", async () => {
  await W.ready; if (!W.requireLogin()) return;
  ["uq", "ur", "us"].forEach(i => document.getElementById(i).addEventListener("input", () => U && render()));
  storageNote(); try { await load(); loadHelp().catch(e => { document.getElementById("help-reqs").hidden = false; document.getElementById("help-list").innerHTML = W.empty("Could not load sign-in help requests", W.esc(e.message)); }); } catch (e) { document.getElementById("users").innerHTML = W.empty("Could not load users", W.esc(e.message)); return; }
  const dlg = document.getElementById("add-dlg"), f = document.getElementById("add-form");
  const grp = (t, rs) => `<optgroup label="${W.esc(t)}">` + rs.map(r => `<option value="${r.key}">${W.esc(r.label)}</option>`).join("") + "</optgroup>";
  document.getElementById("f-role").innerHTML = `<option value="">Choose a role…</option>` + grp("Clinical staff (staff directory record)", U.roles.filter(r => r.clinical)) + grp("Other staff", U.roles.filter(r => !r.clinical));
  document.getElementById("f-site-list").innerHTML = U.organizations.map(o => `<option value="${W.esc(o.name)}"></option>`).join("");
  const drawStaff = () => { const r = document.getElementById("f-role").value;
    document.getElementById("f-staff").innerHTML = r ? staffBlock("f", r) : "";
    document.getElementById("f-site-h").textContent = isClinical(r) ? "Leave empty to use the practice, hospital or programme office of their staff record." : "Shown on the user list.";
    wireStaff(f, "f"); };
  document.getElementById("f-role").addEventListener("change", () => { clearFieldErrs(f); drawStaff(); });
  dlg.querySelector("[data-close]").onclick = () => dlg.close();
  document.getElementById("add").onclick = () => { if (!PD) { f.reset(); clearFieldErrs(f); W.formError(f, ""); drawStaff(); dlg.showModal(); document.getElementById("f-name").focus(); } };
  f.onsubmit = async ev => { ev.preventDefault(); const d = Object.fromEntries(new FormData(f)), sb = f.querySelector("button[type=submit]");
    clearFieldErrs(f); W.formError(f, "");
    const need = [["display_name", "Enter their full name (at least 2 characters).", d.display_name.trim().length < 2],
                  ["username", "Enter a username: at least 3 lower-case letters, numbers, dots or dashes, starting with a letter or number.", !/^[a-z0-9][a-z0-9._-]{2,79}$/.test(d.username.trim().toLowerCase())],
                  ["role", "Choose a role.", !d.role]].filter(x => x[2]);
    need.forEach(([k, m]) => fieldErr(f, k, m));
    if (need.length) { f.querySelector(`[name="${need[0][0]}"]`).focus(); return; }
    if (!checkStaff(f, "f")) return;
    const body = { username: d.username.trim().toLowerCase(), display_name: d.display_name.trim(), role: d.role, site: d.site || null, team: d.team || null, ...staffBody(f, "f") };
    W.busy(sb, true);
    try { const r = await W.api("/api/admin/users", { method: "POST", body }); dlg.close(); showTemp(r.user, r.temporary_password, "Account created"); await load(); }
    catch (e) { if (!(e.data && e.data.field && fieldErr(f, e.data.field, e.message))) W.formError(f, e.message); else W.formError(f, "Please fix the field marked above."); W.toast(e.message, "err"); } finally { W.busy(sb, false); } };
});
window.copyTmpPw = () => { const el = document.getElementById("tmp-pw");
  if (el && navigator.clipboard) navigator.clipboard.writeText(el.textContent).then(() => W.toast("Copied", "ok")); };
window.clearCreated = () => { document.getElementById("created").innerHTML = ""; };
