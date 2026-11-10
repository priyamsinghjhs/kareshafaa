// consults.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
async function load() {
  const d = await W.api("/api/consults");
  const open = d.items.filter(c => c.status !== "answered").length;
  document.getElementById("ctable").innerHTML = d.items.length ? `<p class="small muted">${open} waiting for an answer.</p><table class="compact"><thead><tr><th>Case</th><th>From</th><th>Urgency</th><th>Question</th><th>Answer by</th><th>Your answer</th></tr></thead><tbody>${d.items.map(c => `<tr>
    <td><a href="/cases/${c.case_id}">Case #${c.case_id}</a></td><td>${c.type === "patient" ? "Patient" : "Clinician (e-consult)"}</td><td>${W.tier(c.urgency)}</td><td>${W.esc(c.question)}</td><td class="small">${W.ago(c.due_at)}</td>
    <td>${c.status === "answered" ? `<span class="chip sla-green">✓ Answered</span><div class="small">${W.esc(c.response)}</div>` : `<label for="r${c.id}" class="sr-only">Answer to case ${c.case_id}</label><textarea id="r${c.id}" rows="2" placeholder="Type your answer…" data-suggest-case="${c.case_id}" data-suggest-action="consult_answer" data-suggest-field="answer" data-suggest-consult="${c.id}"></textarea><button class="sm" data-click="answer" data-args='[${c.id},"$el"]'>Send answer</button>`}</td></tr>`).join("")}</tbody></table>` : W.empty("No questions waiting", "", "", "chat");
}
window.answer = async (id, b) => { const ta = document.getElementById("r" + id), v = ta.value.trim(), box = ta.parentElement;
  if (v.length < 2) { W.formError(box, "Write your answer first (at least 2 characters)."); ta.focus(); return; }
  W.busy(b, true); W.formError(box, "");
  try { await W.api(`/api/consults/${id}/respond`, { method: "POST", body: { response: v } }); W.toast("Answer sent", "ok"); await load(); } catch (e) { W.formError(box, e.message); W.toast(e.message, "err"); W.busy(b, false); } };
document.addEventListener("DOMContentLoaded", async () => { await W.ready; if (!W.requireLogin()) return;
  try { await load(); } catch (e) { document.getElementById("ctable").innerHTML = W.empty("Could not load", W.esc(e.message)); } });
