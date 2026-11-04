// overview.html page script (moved out of the template: no inline scripts, CSP script-src 'self').
const stat = (href, v, l, cls = "") => `<a class="stat ${cls}" href="${href}"><span class="v">${v ?? "–"}</span><span class="l">${l}</span></a>`;
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
  const card = (title, n, text, href, tab) => `<article class="card"><h3>${title}</h3><p class="stat-inline"><b style="font-size:var(--fs-2xl)">${n ?? "–"}</b> <span class="muted small">${text}</span></p><a role="button" class="${n ? "secondary" : "secondary outline"}" href="${href}">${n ? "Review" : "Open"}</a></article>`;
  document.getElementById("approvals").innerHTML =
    card("Outreach model updates", awaiting.length, "candidate model(s) passed the safety gates and need approval", "/governance?tab=models") +
    card("Rule and deadline changes", pending.length, "proposed change(s) from lessons learned", "/governance?tab=proposals") +
    card("Patient texts", texts, "governed text(s) not live yet (need clinical + translator sign-off)", "/governance?tab=content");
  const c = q ? q.counts : {};
  // escalations first (safety), then the most urgent overdue steps; each with one button that opens the case
  let urgent = q ? q.items : [];
  if (q && c.escalated) { const e = await W.api("/api/queue?view=escalated&limit=5").catch(() => null); if (e && e.items.length) urgent = e.items; }
  document.getElementById("now-list").innerHTML = !q ? W.empty("Could not load the queue", "Open the work queue to see cases.")
    : urgent.length ? W.nowList(urgent) : W.empty("Nothing urgent", "No escalations or overdue steps right now.");
  document.getElementById("safety").innerHTML = stat("/queue?view=overdue", c.overdue, "Steps overdue", c.overdue ? "danger" : "ok") +
    stat("/queue?view=escalated", c.escalated, "Escalations not acknowledged", c.escalated ? "danger" : "ok") +
    stat("/queue?view=review", c.review, "Results needing clinical review", c.review ? "warning" : "ok") +
    stat("/queue?view=rejected", c.rejected, "Referrals rejected, to re-route", c.rejected ? "warning" : "ok");
  document.getElementById("kpis").innerHTML = k ? stat("/dashboard", k.open_cases, "Open cases") + stat("/dashboard", W.pct(k.closure_rate), "Closure rate") +
    stat("/dashboard", k.median_days_to_care, "Median days to care") + stat("/dashboard", W.pct(k.teach_back_pass_rate_first_attempt), "Understood first time (teach-back)") : W.empty("Figures unavailable");
});
