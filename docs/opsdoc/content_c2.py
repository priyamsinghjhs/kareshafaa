"""Section 4.4-4.11: one part per role."""


def part_c2(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    FIG, GREY = g["FIG"], g["GREY"]

    def role(title, resp, screens, can, cannot, checklist, procedures, figs=()):
        h2(title)
        h3("Responsibilities"); bullets(resp)
        h3("What you see"); bullets(screens)
        table(["You can", "You cannot"], [[can, cannot]], [8.5, 8.5], size=9, zebra=False, valign="top")
        h3("Daily checklist"); g["checks"](checklist)
        if procedures:
            h3("Key procedures")
        for ptitle, pst in procedures:
            para(f"**{ptitle}**", after=2); steps(pst)
        for f in figs:
            figure(*f)

    role("4.4 Care coordinator",
         ["Keep every case in the programme moving and inside its deadline; you are the default owner from result to notification, and for unreachable and no-show cases.",
          "Contact patients: run the channel ladder, make navigator calls (with an interpreter where flagged), record call outcomes and navigator teach-backs.",
          "Resolve patient matches and load results in the **Results inbox**.",
          "Book appointments, record attendance and no-shows, and re-route rejected referrals.",
          "Act on L2 escalations as programme coordinator or supervisor."],
         ["**Today's queue**: most urgent first – overdue and due-soon steps, then tier T0 to T3, then flagged results, then outreach priority, then deadline. Counters for *Overdue*, *Need review*, *No named owner* and *Escalated*; a programme filter and a search by name, MRN or case number.",
          "Queue views: All open cases, Overdue, Needs clinical review, Assigned to me, No named owner, Escalated, Referral rejected, Phone calls to make, Transfers waiting, Open consultations.",
          "**Results inbox**: messages waiting for a patient match, recent results, and an HL7 v2 entry box.",
          "**Case page**: clinical record, current step, owner, deadline, urgency, timeline, messages and events, case audit, and the *Next step* card."],
         "Confirm triage of T2/T3 results; send notifications and reminders; record calls, navigator teach-backs and unreachable; send and re-route referrals; book appointments and record attendance; close with evidence; reassign; request transfers; record consent or a decline on the patient's behalf; ask a specialist; acknowledge escalations; record delay reasons; export FHIR.",
         "Confirm T0/T1 triage or resolve a clinical review; order or record confirmatory tests; decide referrals; write consultation notes, medicines or care plans; authorised closure or record a death; re-open; open Outcomes, Approvals, the audit trail or Advanced.",
         ["Open **Today's queue** and press **Open the most urgent case**; work down from the red (overdue) items.",
          "Check **No named owner** and assign every pooled case.",
          "Work **Phone calls to make** and record each outcome the same day.",
          "Check **Referral rejected** and re-route each case.",
          "Check **Results inbox** for messages waiting for a patient match.",
          "Acknowledge your **Escalated** cases with what you did.",
          "Before leaving, check **Transfers waiting** and amber items due overnight."],
         [("Notify a patient", ["Open the case in *Triaged*.", "Press **Send result notification (channel ladder)**.", "Read the outcome: the delivered channel, or a phone-call task if no digital channel succeeded.", "If a call task was created, call the patient (with an interpreter if flagged) and record the outcome."]),
          ("Record a navigator teach-back during a call", ["Open the case in *Patient notified*.", "Choose **Record navigator teach-back**; ask the three questions in the patient's language.", "Record the answers and the patient's confidence (1–5). A pass moves the case to *Acknowledged*."]),
          ("Resolve a patient match", ["Open **Results inbox** → *Waiting for a patient match*.", "Compare identifier, name and birth year with the patient record.", "Choose the correct patient. The result then creates or updates the case as normal."])],
         [(FIG / "coord_queue_pair.png", "Care coordinator: Today's queue on a laptop (left) and a phone (right).", 15.5)])

    role("4.5 Family doctor (GP)",
         ["Review flagged results and confirm the urgency of T0/T1 results.",
          "Order and record confirmatory tests; hold the consultation; refer, or manage in primary care.",
          "Record consultation notes, medicines and care plans in the clinical record.",
          "Sign off authorised closures; record a death when confirmed.",
          "Use the **Results inbox** for results that arrive at the practice; answer patient questions routed to you."],
         ["**Needs review**: your patients whose results need a clinician.", "**All my cases**: open cases your organisation is involved in.", "**Ask a specialist**: your e-consults and questions addressed to you.",
          "**Case page**: patient summary (MRN, age, sex, language, contact preference, family doctor, allergies, key conditions), tests and investigations, consultations and encounters, medicines, referrals and appointments, outreach and patient responses."],
         "Confirm triage including T0/T1; resolve clinical reviews; order and record confirmatory tests; consultation notes and medicines; send referrals; book appointments; record care plans; close with evidence or by authorised decision; record a death; reassign; transfer; ask a specialist; results inbox.",
         "Decide referrals (specialist or clinical lead); re-open closed cases or correct a death record (clinical lead); approve models, rules or patient wording; see Outcomes or the audit trail.",
         ["Open **Needs review** and resolve every flagged result, T0 first.", "Check **Ask a specialist** for answers and patient questions.",
          "Review cases in *Acknowledged*, *Confirmatory test* and *Consultation* in **All my cases**.",
          "Act on declined cases: talk to the patient and document an informed decline or a new plan.", "Acknowledge escalations on your cases."],
         [("Confirm and act on a T1 result", ["Open the case from **Needs review**.", "Check the rule, pathway and target shown on the case.", "Choose **Confirm triage (clinician)**, or resolve the review with a decision and note.", "Send the result notification, or leave it to the coordinator, who owns the next step."]),
          ("Authorised closure", ["Open the case → **More actions** → **Authorised closure (reason required)**.", "Choose the reason code and write the rationale (at least 10 characters).", "Confirm. The guard checks the reason against the case, for example two failed calls for *unable to contact*."])],
         [(FIG / "clinician_case_pair.png", "Clinician case view: family doctor on a laptop (left) and specialist on a phone (right). The clinical record is shown above the current step.", 15.5)])

    role("4.6 Specialist doctor",
         ["Accept or reject referrals sent to your organisation, with reasons.", "Book appointments and record attendance and no-shows.",
          "Record confirmatory results, consultation notes, medicines and care plans; sign off authorised closures.", "Resolve clinical reviews on your cases; answer e-consult questions addressed to you."],
         ["**Needs review**: cases needing your review.", "**My cases**: cases your organisation is involved in.", "**Consult requests**: questions from GPs and patients addressed to you, with the time to answer."],
         "Resolve clinical reviews; decide referrals for your organisation; book appointments and record attendance; record confirmatory results and care plans; consultation notes and medicines; close with evidence or by authorised decision; record a death; request transfers; answer e-consults addressed to you.",
         "Triage results (see note ¹ to the matrix); record call outcomes; reassign owners or send referrals; order confirmatory tests; decide referrals addressed to another organisation; re-open cases; governance, Outcomes or the audit trail.",
         ["Decide every referral in *Referral sent* for your organisation.", "Book accepted referrals inside the pathway target.", "Record attendance or no-show after each clinic.",
          "Record care plans after attended appointments.", "Answer **Consult requests** within the response time (T0 1 h, T1 4 h, T2 24 h, T3 72 h)."],
         [("Reject a referral", ["Open the case in *Referral sent* → **Accept / reject referral**.", "Choose *Reject*, pick the reason code (wrong service, capacity, incomplete information, out of network, not clinically indicated) and write a note.", "Save. The case returns to the referring clinician for re-routing."]),
          ("Record a no-show", ["After the appointment time, open the case → **Record attendance / no-show** → *No-show*.", "Choose the barrier code.", "Save. The patient receives a rebooking message and the case moves to *No-show* for rebooking."])],
         [(FIG / "clinician_lists.png", "Family doctor's *Needs review* list (left) and specialist's *Questions for you* (right), on phones.", 11.0)])

    role("4.7 Clinical lead",
         ["Own and resolve flagged results for the programme.",
          "Approve or reject candidate outreach models with a note, and roll back; decide proposed rule and deadline changes.",
          "Give the clinical sign-off for patient wording.",
          "Respond to L3 escalations and watch overdue cases; lead accounts without a programme act as medical director for L4.",
          "Re-open closed cases and correct death records recorded in error.",
          "Review clinical governance with the read-only **Audit trail** and **Outcomes**."],
         ["**Overview**: decisions waiting (outreach model updates, rule and deadline changes, patient texts), patient safety (overdue, escalations not acknowledged, needs clinical review, referrals rejected) and programme results.",
          "**Work queue** and case pages for the whole programme.",
          "**Approvals**: tabs *Outreach model*, *Proposed rule changes*, *Lessons learned*, *Deadlines & settings* and *Patient texts*.",
          "**Outcomes** (aggregate KPIs and fairness) and **Audit trail** (read-only: search events; no chain check)."],
         "Everything a GP can do in a case, plus: decide referrals, re-open cases, correct a death record, answer any e-consult, approve or reject models and roll back, decide rule and deadline proposals, clinical sign-off of patient wording, Outcomes, read-only audit trail.",
         "Run the deadline check by hand, retrain models, run the proposal engine or simulations, use the Results inbox, verify the audit chain, open Advanced or the guided walkthrough, manage users (all system administrator or front-line tasks); give the translation sign-off.",
         ["Open **Overview** and clear *Waiting for your decision*.", "Review *Patient safety*: unacknowledged escalations, overdue cases, flagged results and rejected referrals.",
          "Resolve flagged results you own.", "Weekly: review T2/T3 cases past 200 % of their deadline and the lessons-learned list."],
         [("Approve or reject a candidate model", ["Open **Approvals** → *Outreach model*; read the gate results (AUC, calibration, fairness) and the model card.", "Approve only if the candidate passed every gate and is better than the current model.", "Write a decision note (at least 5 characters) and confirm. **Roll back production model** returns to the previous approved version, also with a note."]),
          ("Decide a rule or deadline change", ["Open **Approvals** → *Proposed rule changes*; read the evidence from lessons learned.", "Check the current values in *Deadlines & settings*.", "Approve or reject with a note. Only approved proposals change the configuration."]),
          ("Re-open a closed case", ["Open the closed case → **Re-open case**.", "Write the reason (at least 10 characters) and confirm.", "Check the step deadline: if the original target has passed, the case is overdue at once."])],
         [(FIG / "lead_overview_pair.png", "Clinical lead: Overview on a laptop (left) and a phone (right).", 15.5),
          (FIG / "lead_models.png", "Approvals → Outreach model: candidate models with gate results, and rollback (clinical lead, laptop).", 15.0),
          (FIG / "lead_rules.png", "Approvals → Deadlines & settings: step deadlines by state and tier with the reminder point (clinical lead, laptop).", 15.0)])

    role("4.8 Programme analyst",
         ["Monitor programme performance and equity: time to notification and to care, closure, teach-back, drop-off reasons and fairness slices.", "Review model, rule and content information (read-only) and share findings with the clinical lead."],
         ["**Outcomes**: KPI dashboard with *Before / after*, *Live figures* and *Equity by group*. The before and with-KareShafaa comparison comes from a simulation with stated assumptions and is not real-world evidence.",
          "**Models & rules**: model registry, lessons learned, deadlines and settings, and patient text status (read-only)."],
         "Read aggregate outcomes, fairness, lessons, models, deadlines and settings, and patient texts.",
         "Open any case or patient record; change anything (no data-changing controls are offered). Groups with fewer than 5 people are hidden.",
         ["Check outcome trends and fairness gaps by language, region and nationality.", "Share findings with the clinical lead before any change proposal."], [],
         [(FIG / "analyst_pair.png", "Outcomes dashboard for the programme analyst on a laptop (left) and a phone (right); aggregates only.", 15.5)])

    role("4.9 System administrator",
         ["Create and manage user accounts; reset passwords; deactivate leavers.", "Check system status and storage.",
          "Verify the audit chain (**Check the log is intact**) and investigate with the audit trail.",
          "Run the technical tools: deadline check now, model retraining (candidates go to the clinical lead for approval), proposal engine, simulation runs.",
          "Support users with sign-in problems."],
         ["**Users** (`/admin/users`): list, search, filter by role and status; add a user; change role, site and team; reset password; deactivate and reactivate.",
          "**System** (`/admin/system`): version, mode, database, whether data is stored permanently, number of accounts, patients and cases, write limit and session timeout.",
          "**Outcomes**, and **Advanced**: audit trail with chain check, outreach model internals (registry, retraining), deadline check (*Run the check now*), interoperability samples, API reference, load-test notes and a health check."],
         "Manage accounts; read the audit trail and verify the chain; Outcomes; view and retrain models; run the proposal engine, simulations and the deadline check (reminders are then sent as the rules engine).",
         "Open the work queue or case pages (refused by the server); approve, reject or roll back models; decide proposals; sign off patient wording; acknowledge escalations; use the Results inbox; change your own role or deactivate your own account; delete any account (accounts are never deleted).",
         ["Check **System** shows *Data is stored permanently (PostgreSQL)*.", "Process joiners, movers and leavers the same day.",
          "Run **Check the log is intact** in the audit trail weekly and after any incident."], [],
         [(FIG / "admin_users_pair.png", "System administrator: Users page on a laptop (left) and a phone (right).", 15.5),
          (FIG / "admin_advanced.png", "Advanced tools page, open only to the system administrator (laptop).", 14.5),
          (FIG / "audit_phones.png", "Audit trail on a phone: clinical lead's read-only view (left) and administrator's view with *Check the log is intact* (right).", 10.5)])

    role("4.10 Medical translator",
         ["Attest that each Arabic, Hindi or Urdu text says the same as the signed-off English source."],
         ["**Content sign-off**: every patient-facing text with its language, reading-level and tone checks, and its clinical and translation sign-off status."],
         "Give the translation sign-off with a note of what was checked (at least 5 characters).",
         "Sign off English source text; give the clinical sign-off; sign a translation you also signed clinically; open cases.",
         ["Review new or changed translations in **Content sign-off**.", "Compare against the current English source before signing."], [],
         [(FIG / "translator_content.png", "Patient content sign-off page used by the medical translator (laptop).", 14.5)])

    role("4.11 Patient",
         ["Read the result, complete the understanding check, ask questions and take the next step within the timeframe."],
         ["**My results**: what the result means, how urgent it is, the next step and timeframe, who is looking after you, the understanding check, a journey tracker, a question box and recent messages.",
          "A language switch (English, Arabic, Hindi, Urdu) at the top. The page is labelled as a prototype and asks the patient to check anything important with the care team."],
         "Answer the teach-back once (change only through **Change my answers**); ask the care team a question; decline the follow-up with an optional reason; record consent; choose the page language.",
         "See clinician notes or other patients' data; move a case other than by acknowledging or declining; close a case.",
         ["Open **My results** when a message arrives.", "Answer the three questions and rate your confidence.", "Use **I'd like to talk to a clinician** if anything is unclear."],
         [("Change answers already sent", ["Choose **Change my answers** and confirm.", "Answer again within 30 minutes and send. Earlier answers stay in the record."])],
         [(FIG / "patient_phones.png", "Patient page on phones in English, Arabic (right to left) and Hindi.", 15.0)])
