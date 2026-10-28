"""Section 4.1-4.3: role summary, permissions matrix (from kareshafaa/security.py, ui.py, api/actions.py and
tests/test_role_separation.py at aac2f3d) and RACI."""
Y, N = "✓", "–"


def part_c(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    GREY = g["GREY"]

    h1("4. Roles and responsibilities")
    para("KareShafaa has seven staff roles and a patient role. Each role has a fixed set of permissions in the code. Every action is checked on the server, and page access is checked as well, so a role cannot open another role's screens even by typing the address. Clinical decisions and system administration are deliberately separated: the clinical lead makes clinical approvals, the system administrator runs the system and makes no clinical decisions.")
    h2("4.1 Role summary")
    table(["Role", "Purpose", "Which cases are visible"], [
        ["Care coordinator", "Keeps every case moving: queue, results inbox, patient contact and calls, bookings, re-routing", "All cases in the programme"],
        ["Family doctor (GP)", "Clinical review, triage of urgent results, confirmatory tests, consultation, referral, care plans, authorised closure; results inbox", "Cases their organisation is involved in"],
        ["Specialist doctor", "Referral decisions, appointments, attendance, care plans, e-consult answers, authorised closure", "Cases their organisation is involved in"],
        ["Clinical lead", "Owns flagged results; approves models, rule and deadline changes and patient wording; escalations and overdue oversight; re-opening; read-only audit trail", "All cases in the programme"],
        ["Programme analyst", "Monitors outcomes and fairness; read-only view of models, rules and content", "No individual cases: aggregates only, groups below 5 hidden"],
        ["System administrator", "User accounts, system status, audit-chain check, deadline-check runner, model retraining, proposal engine, simulation runs", "No cases: queue and case pages are refused"],
        ["Medical translator", "Attests that translated patient wording says the same as the signed-off English", "No cases"],
        ["Patient", "Reads their result, completes the teach-back, asks questions, declines or changes consent", "Their own case only"],
    ], [3.4, 8.6, 5.0], title="Roles at a glance", first_col_bold=True)
    para("*Involved* means the organisation manages the patient, owns the case, receives a referral or appointment, is asked an e-consult question, or is the target of a pending transfer. Organisations other than the managing organisation also need the patient's consent to share.", size=9.5, color=GREY)

    h2("4.2 Permissions matrix")
    para("✓ = allowed; – = not allowed. Coord = care coordinator, Spec = specialist, Lead = clinical lead, Admin = system administrator, Transl = medical translator. Numbers refer to the notes below the table.")
    rows = [
        ["Open the work queue and case pages", Y, Y, Y, Y, N, N, N, "Own case"],
        ["Results inbox: load an HL7 / FHIR result, resolve a patient match", Y, Y, N, N, N, N, N, N],
        ["Confirm triage of a T2/T3 result", Y, Y, N, Y, N, N, N, N],
        ["Confirm triage of a T0/T1 result", N, Y, "– ¹", Y, N, N, N, N],
        ["Resolve a clinical review (flagged result)", N, Y, Y, Y, N, N, N, N],
        ["Send result notification or reminder (channel ladder)", Y, Y, "✓ ²", Y, N, N, N, N],
        ["Record call outcome, navigator teach-back, mark unreachable", Y, Y, N, Y, N, N, N, N],
        ["Order a confirmatory test", N, Y, N, Y, N, N, N, N],
        ["Record a confirmatory result", N, Y, Y, Y, N, N, N, N],
        ["Move to consultation; record a decline; resume after reconsidering", Y, Y, Y, Y, N, N, N, "Decline own"],
        ["Consultation notes and medicines (clinical record)", N, Y, Y, Y, N, N, N, N],
        ["Send or re-route a referral", Y, Y, N, Y, N, N, N, N],
        ["Accept or reject a referral (own organisation)", N, N, Y, Y, N, N, N, N],
        ["Book appointment; record attendance or no-show", Y, Y, Y, Y, N, N, N, N],
        ["Record a treatment or surveillance plan", N, Y, Y, Y, N, N, N, N],
        ["Close a case with evidence", Y, Y, Y, Y, N, N, N, N],
        ["Authorised closure; record a death", N, Y, Y, Y, N, N, N, N],
        ["Re-open a case; correct a death recorded in error", N, N, N, Y, N, N, N, N],
        ["Reassign owner", Y, Y, N, Y, N, N, N, N],
        ["Request a two-key transfer", Y, Y, Y, Y, N, N, N, N],
        ["Record consent", Y, Y, Y, Y, N, N, N, "Own"],
        ["Ask a specialist (e-consult)", Y, Y, Y, Y, N, N, N, "Ask care team"],
        ["Answer an e-consult", "✓ ³", "✓ ³", "✓ ³", Y, N, N, N, N],
        ["Acknowledge escalation; record delay reason; export FHIR", Y, Y, Y, Y, N, N, N, N],
        ["Outcomes dashboard (aggregates)", N, N, N, Y, Y, Y, N, N],
        ["View models, lessons, deadlines and settings", N, N, N, Y, Y, Y, N, N],
        ["Approve, reject or roll back a model; decide rule and deadline changes", N, N, N, Y, N, N, N, N],
        ["Train a candidate model; run the proposal engine; run simulations", N, N, N, N, N, Y, N, N],
        ["Clinical sign-off of patient wording", N, N, N, Y, N, N, N, N],
        ["Translation sign-off", N, N, N, N, N, N, Y, N],
        ["View patient wording and sign-off status", N, N, N, Y, Y, N, Y, N],
        ["Read the audit trail", N, N, N, "✓ ⁴", N, Y, N, N],
        ["Check the audit chain is intact", N, N, N, N, N, Y, N, N],
        ["Advanced tools page; run the deadline check now", "– ⁵", N, N, N, N, Y, N, N],
        ["Manage users; System page", N, N, N, N, N, Y, N, N],
        ["Own result, teach-back, questions, page language", N, N, N, N, N, N, N, Y],
    ]
    table(["Action", "Coord", "GP", "Spec", "Lead", "Analyst", "Admin", "Transl", "Patient"], rows,
          [5.9, 1.25, 1.15, 1.25, 1.2, 1.4, 1.3, 1.35, 2.2], title="Permissions matrix (code at aac2f3d)", size=8.2,
          center_cols=(1, 2, 3, 4, 5, 6, 7, 8))
    para("**Notes.** ¹ The specialist holds the urgent-triage permission and the screen offers *Confirm triage (clinician)*, but the server also requires the general triage permission, which the specialist does not have, so the action is refused. ² A specialist can send messages, but moving a case from *Triaged* to *Patient notified* needs the call-recording permission, so a specialist-sent result notification is refused at that step. ³ Only the clinician the question is addressed to, or a clinical lead, can answer. ⁴ Read-only: the lead's audit page has no chain check and no link to Advanced. ⁵ The coordinator role still holds the deadline-check permission in the code, but the Advanced page that has the button is open only to the system administrator.", size=8.8, color=GREY)

    h2("4.3 RACI for the core flow")
    para("R = does the work; A = accountable (default owner in the code); C = consulted; I = informed. Derived from the default owner of each state and the permissions above. The analyst is informed through Outcomes. The system administrator supports accounts and the system and has no clinical part.")
    table(["Activity", "Coord", "GP", "Spec", "Lead", "Admin", "Patient", "System"], [
        ["Result intake and patient match", "A / R", "R", "–", "I", "–", "–", "R"],
        ["Clinical review of flagged results", "I", "R", "R", "A / R", "–", "–", "R (flag)"],
        ["Triage", "A / R (T2/T3)", "R", "–", "R", "–", "–", "R (T2/T3)"],
        ["Notify patient and outreach", "A / R", "R", "C", "C", "–", "I", "R (ladder)"],
        ["Teach-back", "R (phone)", "I", "–", "–", "–", "R", "R (scoring)"],
        ["Confirmatory test and consultation", "I", "A / R", "R (result)", "C", "–", "I", "–"],
        ["Referral sent / re-routed", "R", "A / R", "I", "C", "–", "I", "–"],
        ["Referral decision", "I", "I", "A / R", "R", "–", "–", "–"],
        ["Appointment and attendance", "R", "R", "A / R", "–", "–", "I", "R (messages)"],
        ["Treatment or surveillance plan", "–", "R", "A / R", "R", "–", "I", "–"],
        ["Closure with evidence", "R", "R", "A / R", "C", "–", "–", "R (guard)"],
        ["Authorised closure", "–", "R", "R", "A / R", "–", "–", "R (guard)"],
        ["Re-open", "I", "I", "I", "A / R", "–", "–", "–"],
        ["Escalation response", "R (L2)", "R", "R", "A (L3/L4)", "–", "–", "R (alerts)"],
        ["Deadline check run by hand", "–", "–", "–", "I", "A / R", "–", "R"],
        ["Patient wording sign-off", "–", "–", "–", "A / R", "–", "–", "R (checks)"],
        ["Model and rule changes", "–", "–", "–", "A (approve)", "R (train, propose)", "–", "R (gates)"],
        ["User accounts and audit-chain check", "–", "–", "–", "I", "A / R", "–", "–"],
    ], [4.6, 1.75, 1.35, 1.6, 1.6, 2.0, 1.45, 2.05], title="RACI for the core flow", size=8.2, center_cols=(1, 2, 3, 4, 5, 6, 7))
