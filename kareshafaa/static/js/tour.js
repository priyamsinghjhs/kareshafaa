// First-run guide: a closed "How this page works" disclosure on each role's home page until it has been opened once on
// this device (localStorage); "Show the guide" in the page footer or ?guide=1 opens it again. No data is sent anywhere.
(function () {
  const GUIDES = {
    coordinator: { home: "/queue", title: "Your work queue", steps: [
      ["Start with “Needs you now”", "The most urgent cases, with the time left and one button for the next step. Red means a deadline has passed."],
      ["Open a case", "“Why this priority” shows the rule that set the tier and what happens next."],
      ["Record every contact", "Calls, messages and bookings go on the case so the clock and the audit stay right."],
      ["Close only with evidence", "A case closes when the specialist visit is recorded or a clinician signs a decision."]] },
    gp: { home: "/queue", title: "Cases that need you", steps: [
      ["Needs me", "Your most urgent cases first, then results the rules could not place safely."],
      ["Change a tier with a reason", "Your decision and reason are kept and shown on the case and in the audit."],
      ["Care summaries", "Specialist outcomes come back to you to sign off."]] },
    specialist: { home: "/queue", title: "Your referrals", steps: [
      ["Needs me", "Cases waiting for your decision are listed first, with the time left."],
      ["Record the outcome", "Your visit outcome closes the loop and goes back to the family doctor."],
      ["Consult requests", "Questions from family doctors arrive in Consult requests."]] },
    clinical_lead: { home: "/overview", title: "Running the programme", steps: [
      ["Overview", "Escalations and overdue cases first, then the decisions waiting for you."],
      ["Outcomes → Loop impact", "Loop closure, time to specialist against target, lost to follow-up and equity gaps; export as CSV or PDF."],
      ["Approvals", "How decisions are made, human overrides, model approvals and data protection."],
      ["More → Audit trail", "Every view and change, in a tamper-evident log."]] },
    analyst: { home: "/dashboard", title: "Outcomes and models", steps: [
      ["Loop impact", "Programme results with small groups hidden to protect privacy."],
      ["Models & rules", "Rule cards, model cards and fairness checks. You can review but not approve."]] },
    admin: { home: "/admin/users", title: "Setting up KareShafaa", steps: [
      ["Users", "Add staff, set roles and reset sign-in. Roles decide what each person can see."],
      ["System", "Data store, background jobs and service checks."],
      ["About", "Architecture, integrations, Malaffi readiness, the API reference and the audit chain check."],
      ["More", "Outcomes and advanced settings."]] },
    translator: { home: "/governance", title: "Message sign-off", steps: [
      ["Content sign-off", "Check each patient message for meaning and reading level before it can be sent."]] },
  };
  const key = r => `ks-guide-${r}-v1`;
  // a closed "How this page works" line under the page title: one tap to read, never in the way of the work
  function render(g, role, force) {
    const host = document.querySelector(".content-inner"); if (!host || document.getElementById("first-run")) return;
    const el = document.createElement("details");
    el.id = "first-run"; el.className = "card first-run"; if (force) el.open = true;
    el.innerHTML = `<summary id="first-run-h">How this page works: ${W.esc(g.title)}</summary>
      <ol class="first-run-steps">${g.steps.map(([t, d]) => `<li><b>${W.esc(t)}</b><span>${W.esc(d)}</span></li>`).join("")}</ol>`;
    const head = host.querySelector(".page-head");  // below the page title, above the work
    if (head) head.after(el); else host.insertBefore(el, host.firstChild);
    el.addEventListener("toggle", () => { if (el.open) localStorage.setItem(key(role), "1"); });
    if (force) el.scrollIntoView({ block: "start" });
  }
  function footLink(g, role) {
    const foot = document.querySelector(".site-foot"); if (!foot || document.getElementById("show-guide")) return;
    const a = document.createElement("button"); a.type = "button"; a.id = "show-guide"; a.className = "link-button small"; a.textContent = "Show the guide";
    a.onclick = () => { const ex = document.getElementById("first-run"); if (ex) { ex.open = true; ex.scrollIntoView({ block: "start" }); } else render(g, role, true); };
    foot.append(" · ", a);
  }
  document.addEventListener("DOMContentLoaded", async () => {
    const me = await (W.ready || Promise.resolve(null)); if (!me) return;
    const g = GUIDES[me.role]; if (!g) return;
    footLink(g, me.role);
    const force = new URLSearchParams(location.search).get("guide") === "1";
    if (location.pathname === g.home && (force || !localStorage.getItem(key(me.role)))) render(g, me.role, force);
  });
})();
