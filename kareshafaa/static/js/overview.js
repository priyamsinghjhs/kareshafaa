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
  // AI & impact strip for judges and clinical leads: model purpose, key outcome, links to model card and equity
  const ai = document.getElementById("ai-impact");
  if (ai) {
    const closure = k ? W.pct(k.closure_rate) : "–";
    const median = k && k.median_days_to_care != null ? k.median_days_to_care + " d" : "–";
    let arms = null;
    try { const imp = await W.api("/api/analytics/impact"); arms = imp.synthetic_arms; } catch (e) { /* ignore */ }
    const armsHtml = arms && arms.measures && arms.measures.length ? `<div class="card notice" style="grid-column:1/-1;margin-top:.5rem"><b>Synthetic before/after</b> <span class="chip">Synthetic</span><p class="small">${W.esc(arms.statement || "")} Example: ${arms.measures[0].label} ${W.pct(arms.measures[0].baseline.rate)} → ${W.pct(arms.measures[0].loop.rate)}.</p></div>` : (arms ? `<div class="card notice" style="grid-column:1/-1;margin-top:.5rem"><b>Synthetic before/after</b> <span class="chip">Synthetic</span><p class="small">${W.esc(arms.statement || "Simulated baseline vs with-system.")}</p></div>` : "");
    const pilot = `<div class="card notice" style="grid-column:1/-1;margin-top:.5rem"><b>30-day pilot path</b><p class="small">Owner: clinical lead + coordinator team. Next integration: planned Malaffi/HIE adapter (read-only result feed). Measures: loop-closure rate, median days to first appointment, no-show rate, and equity by language. Human approval required for every state change.</p></div>`;
    ai.innerHTML =
      stat("/governance?tab=models", "Outreach risk", "Calibrated model · orders queue only", "", "gauge") +
      stat("/dashboard?tab=impact", closure, "Loop closed (mature cases)", "", "ok") +
      stat("/dashboard?tab=sim", median, "Median days to care", "", "clock") +
      stat("/dashboard?tab=fair", "Equity", "Fairness slices by group", "", "users") +
      armsHtml + pilot;
  }
});
