// overview.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
const stat = (href, v, l, cls = "", ic = "") => W.stat(href, v, l, cls, ic);
document.addEventListener("DOMContentLoaded", async () => {
  await W.ready; if (!W.requireLogin()) return;
  const [q, models, props, content, k] = await Promise.all([
    W.api("/api/queue?view=all&limit=5").catch(() => null), W.api("/api/governance/models").catch(() => null),
    W.api("/api/governance/proposals").catch(() => null), W.api("/api/governance/content").catch(() => null),
    W.api("/api/analytics/kpis").catch(() => null)]);
  const awaiting = models ? models.items.filter(m => m.status === "awaiting_approval") : [];
  const pending = props ? props.items.filter(p => p.status === "pending") : [];
  const texts = content ? content.summary.gated - content.summary.gated_live : null;
  const total = awaiting.length + pending.length + (texts || 0);
  document.getElementById("go-approvals").textContent = total ? `Review ${total} approval${total === 1 ? "" : "s"}` : "Open approvals";
  // number-first approval tiles: figure, short label, icon; the full explanation sits on the Approvals page
  const card = (title, n, ic, href, hint) => W.stat(href, n, title, n ? "warning" : "ok", ic, `title="${W.esc(hint)}"`);
  document.getElementById("approvals").innerHTML =
    card("Model updates", awaiting.length, "gauge", "/governance?tab=models", "Candidate outreach models that passed the safety gates") +
    card("Rule changes", pending.length, "route", "/governance?tab=proposals", "Proposed rule and deadline changes") +
    card("Patient texts", texts, "chat", "/governance?tab=content", "Governed texts not live yet (clinical and translator sign-off)");
  const c = q ? q.counts : {};
  // escalations first (safety), then the most urgent overdue steps; each with one button that opens the case
  let urgent = q ? q.items : [];
  if (q && c.escalated) { const e = await W.api("/api/queue?view=escalated&limit=5").catch(() => null); if (e && e.items.length) urgent = e.items; }
  document.getElementById("now-list").innerHTML = !q ? W.empty("Could not load the queue")
    : urgent.length ? W.nowList(urgent) : W.empty("Nothing urgent");
  document.getElementById("safety").innerHTML = stat("/queue?view=overdue", c.overdue, "Overdue", c.overdue ? "danger" : "ok", "clock") +
    stat("/queue?view=escalated", c.escalated, "Escalations", c.escalated ? "danger" : "ok", "flag") +
    stat("/queue?view=review", c.review, "Need review", c.review ? "warning" : "ok", "eye") +
    stat("/queue?view=rejected", c.rejected, "Referrals rejected", c.rejected ? "warning" : "ok", "route");
  document.getElementById("kpis").innerHTML = k ? stat("/dashboard", k.open_cases, "Open cases", "", "list") + stat("/dashboard", W.pct(k.closure_rate), "Closure rate", "", "ok") +
    stat("/dashboard", k.median_days_to_care, "Median days to care", "", "clock") + stat("/dashboard", W.pct(k.teach_back_pass_rate_first_attempt), "Understood first time", "", "chat") : W.empty("Figures unavailable", "", "", "chart");
});
