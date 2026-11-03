// First-run guide: a short, dismissible "getting started" card on each role's home page. Shown once per role on this
// device (localStorage); "Show the guide" in the page footer or ?guide=1 brings it back. No data is sent anywhere.
(function () {
  const GUIDES = {
    coordinator: { home: "/queue", title: "Your work queue", steps: [
      ["Start at the top", "Cases are sorted by clinical tier, then by deadline. Red means a deadline has passed."],
      ["Open a case", "“Why this priority” shows the rule that set the tier and what happens next."],
      ["Record every contact", "Calls, messages and bookings go on the case so the clock and the audit stay right."],
      ["Close only with evidence", "A case closes when the specialist visit is recorded or a clinician signs a decision."]] },
    gp: { home: "/queue", title: "Cases that need your review", steps: [
      ["Needs review", "Results the rules could not place safely wait here for a clinician."],
      ["Change a tier with a reason", "Your decision and reason are kept and shown on the case and in the audit."],
      ["Care summaries", "Specialist outcomes come back to you to sign off."]] },
    specialist: { home: "/queue", title: "Your referrals", steps: [
      ["Needs review", "Cases waiting for your decision are listed first."],
      ["Record the outcome", "Your visit outcome closes the loop and goes back to the family doctor."],
      ["Consult requests", "Questions from family doctors arrive in Consult requests."]] },
    clinical_lead: { home: "/overview", title: "Running the programme", steps: [
      ["Overview", "See overdue cases and escalations across your sites."],
      ["Outcomes → Loop impact", "Loop closure, time to specialist against target, lost to follow-up and equity gaps; export as CSV or PDF."],
      ["Approvals", "How decisions are made, human overrides, model approvals and data protection."],
      ["Audit trail", "Every view and change, in a tamper-evident log."]] },
    analyst: { home: "/dashboard", title: "Outcomes and models", steps: [
      ["Loop impact", "Programme results with small groups hidden to protect privacy."],
      ["Models & rules", "Rule cards, model cards and fairness checks. You can review but not approve."]] },
    admin: { home: "/admin/users", title: "Setting up KareShafaa", steps: [
      ["Users", "Add staff, set roles and reset sign-in. Roles decide what each person can see."],
      ["System", "Data store, background jobs and service checks."],
      ["About", "Architecture, integrations, Malaffi readiness, the API reference and the audit chain check."]] },
    translator: { home: "/governance", title: "Message sign-off", steps: [
      ["Content sign-off", "Check each patient message for meaning and reading level before it can be sent."]] },
  };
  const key = r => `ks-guide-${r}-v1`;
  function render(g, role, force) {
    const host = document.querySelector(".content-inner"); if (!host || document.getElementById("first-run")) return;
    const el = document.createElement("section");
    el.id = "first-run"; el.className = "card first-run"; el.setAttribute("aria-labelledby", "first-run-h");
    el.innerHTML = `<div class="first-run-head"><h2 id="first-run-h">Getting started: ${W.esc(g.title)}</h2>
      <button type="button" class="ghost sm" id="first-run-close">Got it</button></div>
      <ol class="first-run-steps">${g.steps.map(([t, d]) => `<li><b>${W.esc(t)}</b><span>${W.esc(d)}</span></li>`).join("")}</ol>`;
    const head = host.querySelector(".page-head");  // below the page title, above the work
    if (head) head.after(el); else host.insertBefore(el, host.firstChild);
    el.querySelector("#first-run-close").onclick = () => { localStorage.setItem(key(role), "1"); el.remove(); };
    if (force) el.scrollIntoView({ block: "start" });
  }
  function footLink(g, role) {
    const foot = document.querySelector(".site-foot"); if (!foot || document.getElementById("show-guide")) return;
    const a = document.createElement("button"); a.type = "button"; a.id = "show-guide"; a.className = "link-button small"; a.textContent = "Show the guide";
    a.onclick = () => { localStorage.removeItem(key(role)); render(g, role, true); };
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
