"""Section 3: end-to-end flow."""


def part_b(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    FIG, DIAG, GREY = g["FIG"], g["DIAG"], g["GREY"]

    h1("3. End-to-end flow")
    para("This section describes the flow of a case from the moment a result arrives to its closure. Section 3.1 shows the flow as a diagram, Section 3.3 gives the procedure as a 17-step table, and Section 3.4 gives the instructions for each stage. The numbers in the diagram match the step numbers in the table.")
    h2("3.1 Flow diagram")
    figure(DIAG / "flow.png", "End-to-end flow of a KareShafaa case. Solid arrows are the main path; dashed amber arrows are exception loops.", width_cm=17.0)
    h2("3.2 Case states")
    para("A case is always in exactly one state. The state machine (version 1.1.0) allows only the moves listed below; closure goes through the closure guard. The default owner is set by the code when the case enters the state.")
    table(["State shown on screen", "Meaning", "Default owner"], [
        ["Result issued", "Result received; waiting for triage (or for clinical review if flagged)", "Programme care coordinator; clinical lead if flagged"],
        ["Triaged", "Urgency confirmed; patient to be told", "Programme care coordinator"],
        ["Patient notified", "Message delivered; waiting for the teach-back", "Programme care coordinator"],
        ["Acknowledged (teach-back passed)", "Patient understood; next clinical step due", "Patient's family doctor (GP)"],
        ["Confirmatory test", "Confirmatory test ordered; waiting for the result", "Family doctor (GP)"],
        ["Consultation", "Clinician consultation and decision on referral", "Family doctor (GP)"],
        ["Referral sent", "Waiting for the receiving service to decide", "Family doctor (GP)"],
        ["Referral rejected", "Rejected with a reason; to be re-routed", "The referring clinician"],
        ["Referral accepted", "Appointment to be booked", "Accepting specialist"],
        ["Appointment booked", "Waiting for the appointment", "Accepting specialist"],
        ["Appointment attended", "Care plan to be recorded", "Accepting specialist"],
        ["Treatment / surveillance started", "Care plan in place; closure to be documented", "Accepting specialist (else GP)"],
        ["Closed", "Closed with evidence or an authorised decision", "–"],
        ["Unreachable (side state)", "Patient not reached after the full contact ladder", "Programme care coordinator"],
        ["No-show (side state)", "Patient missed the appointment; to be rebooked", "Programme care coordinator"],
        ["Declined – clinician review (side state)", "Patient declined; a clinician must review", "Family doctor (GP)"],
    ], [5.0, 7.0, 5.0], title="Case states and default owners", size=8.8, first_col_bold=True)
    para("If the default owner is inactive or missing, the case goes to the programme team pool and appears under *No named owner* until someone is assigned.", size=9.5, color=GREY)

    h2("3.3 Step-by-step procedure")
    para("Deadlines are the default step SLAs for T0 / T1 / T2 / T3 (h = hours, d = days, wd = UAE working days). The pathway target also applies (Appendix C), and a step deadline never runs past it.")
    table(["#", "Step", "Who owns it", "System action", "Deadline", "What is recorded"], [
        ["1", "Result received and matched", "System; care coordinator or GP for unmatched messages", "Parses HL7 v2 / FHIR R4; answers ACK AA or AE; ignores exact duplicates; holds uncertain matches in the Results inbox", "On arrival", "Inbound message, result, patient match, ACK"],
        ["2", "Rules applied, case created", "Rules engine", "Sets tier, pathway, target date and rule ID; flags anything unsafe for review; assigns the owner", "On arrival", "Case, tier, rule ID, pathway, target, owner"],
        ["3", "Clinical review of a flagged result", "Clinical lead, GP or specialist", "Blocks every other step and all patient messages until resolved; the tier can only be lowered by a clinician with a note", "As step 4", "Review decision and note (review log), new tier"],
        ["4", "Triage", "T2/T3: rules engine; T0/T1: GP or clinical lead", "T2/T3 move to *Triaged* automatically; T0/T1 need a clinician", "1 h / 4 h / 1 wd / 2 wd", "Triage step, actor, note"],
        ["5", "Patient notified", "Care coordinator", "Runs the channel ladder (app → SMS → WhatsApp) within consent; creates a phone-call task if no channel works", "4 h / 24 h / 2 wd / 5 wd", "Each message attempt, channel, language, delivery status (simulated)"],
        ["6", "Navigator phone call", "Care coordinator", "Call task for no digital channel, interpreter need or a failed teach-back", "Within the step 5 / 7 deadline", "Call outcome (reached, no answer, wrong number) and note"],
        ["7", "Teach-back and acknowledgement", "Patient (app) or care coordinator (phone)", "Scores 3 questions; a pass moves the case to *Acknowledged*; a fail creates a call task; answers lock after sending", "24 h / 48 h / 5 wd / 10 wd", "Answers, score, confidence (1–5), language, any changed answers"],
        ["8", "Confirmatory test ordered", "GP", "Offered only where the pathway needs confirmation", "24 h / 72 h / 7 wd / 14 wd (from acknowledgement)", "Test order (service request)"],
        ["9", "Confirmatory result", "GP or specialist", "Positive → consultation; negative → closure (FIT, breast) or surveillance plan (cervical, diabetes)", "48 h / 7 d / 14 wd / 28 wd", "Result on the test order"],
        ["10", "Consultation", "GP", "Clinical record with consultation notes and medicines", "24 h / 72 h / 7 wd / 14 wd", "Consultation note, medicines, step note"],
        ["11", "Referral sent", "GP (care coordinator or clinical lead may also send)", "Checks consent to share; offers only valid providers", "24 h / 48 h / 5 wd / 10 wd (for the decision)", "Referral: organisation, specialist, summary"],
        ["12", "Referral decision", "Receiving specialist", "Accept, or reject with a reason code; rejection returns the case to the referrer", "Rejected: re-route in 8 h / 24 h / 2 wd / 5 wd", "Decision, reason code, note"],
        ["13", "Appointment booked", "Specialist or care coordinator", "Sends the appointment message (app, SMS, WhatsApp); asks for a reason if the date is beyond the pathway target", "Book in 24 h / 72 h / 10 wd / 20 wd", "Appointment time, booking note"],
        ["14", "Attendance or no-show", "Specialist (care coordinator may record)", "No-show sends a rebooking message and opens the *No-show* step", "Attend in 7 d / 14 d / 30 wd / 60 wd", "Attendance, barrier code"],
        ["15", "Treatment or surveillance plan", "Specialist or GP", "Records the care plan; care is now reached", "48 h / 5 d / 10 wd / 20 wd", "Care plan type and description"],
        ["16", "Closure", "Any clinical role with evidence; GP, specialist or clinical lead for an authorised decision", "Closure guard checks the evidence or the reason code and note", "48 h / 72 h / 7 wd / 14 wd", "Closure type, evidence or reason, note, actor"],
        ["17", "Deadline check and escalation", "System; then owner, supervisor, clinical lead, medical director", "Every 5 minutes: updates clocks, raises L1–L4 alerts, sends due patient reminders, moves cases of inactive owners to the team pool", "Continuous", "Escalations, reminders, acknowledgements"],
    ], [0.6, 2.6, 3.0, 5.0, 2.6, 3.2], title="Step-by-step procedure (17 steps)", size=7.9, center_cols=(0,))

    h2("3.4 Stage-by-stage procedures")
    h3("3.4.1 Result intake (steps 1–2)")
    bullets([
        "Laboratory systems post HL7 v2 ORU^R01 or FHIR R4 Bundles to the ingest interface. Care coordinators and GPs can paste an HL7 message in the **Results inbox** (`/ingest`).",
        "Each message is answered with **ACK AA** (accepted) or **AE** (error). A message with the same control ID and the same content is ignored as a duplicate; the same ID with different content is never silently treated as a replay.",
        "Patients are matched deterministically by identifier and birth year. If the match is uncertain the message waits in **Waiting for a patient match**; a coordinator or GP resolves it.",
        "The rules engine then creates the case (Appendix C) or, for a negative result, returns the person to routine screening without a case.",
    ])
    figure(FIG / "coord_inbox.png", "Results inbox: messages waiting for a patient match and recent results (care coordinator, laptop).", width_cm=15.0)
    h3("3.4.2 Clinical review of flagged results (step 3)")
    para("A result is flagged when it is unknown, malformed, out of range, conflicting, amended, duplicated, outside the screening age, or when the patient is pregnant, deceased or already under specialist care, or the programme was closed in the last 180 days. A flagged case is placed at the safest plausible tier, owned by the clinical lead, and nothing else happens until a GP, specialist or clinical lead chooses **Resolve clinical review** and records a decision and note. Every review note is kept on the case and shown in the timeline.")
    h3("3.4.3 Triage (step 4)")
    para("T2 and T3 results are triaged automatically by the deterministic rule. T0 and T1 results must be confirmed by a clinician: open the case and choose **Confirm triage (clinician)**. The T0 first-action deadline is 1 hour, day or night.")
    h3("3.4.4 Notifying the patient (step 5)")
    steps(["Open the case in *Triaged* and choose **Send result notification (channel ladder)**.",
           "The system tries the app, then SMS, then WhatsApp, in the order configured for the patient's language and only on channels the patient consented to.",
           "If no digital channel succeeds, or an interpreter is needed, a **phone-call task** is created for the care coordinator.",
           "When a message is delivered the case moves to *Patient notified*."])
    callout("Messages are simulated", [
        "SMS and WhatsApp messages are **recorded in the case but not sent to phones** until a messaging provider is connected. Each recorded message is marked as simulated. Do not rely on these messages to reach real patients.",
    ], fill="FFF7E6", edge="B7791F")
    h3("3.4.5 Navigator calls (step 6)")
    para("Work the *Phone calls to make* filter. Record each call as **reached**, **no answer** or **wrong number** with a note. Use an interpreter when the case shows the interpreter flag. After at least two failed calls, and when the digital ladder has failed, mark the case **Unreachable** (Section 6.1).")
    h3("3.4.6 Teach-back and acknowledgement (step 7)")
    bullets([
        "The patient answers three questions on the patient page (what the result means, the next step, how soon) and rates confidence from 1 to 5. All three must be correct to pass.",
        "A pass moves the case to *Acknowledged*. A fail creates a navigator call task; the coordinator explains the result and can record a **navigator teach-back** by phone.",
        "Once sent, the patient's answers are **locked**. To change them the patient must choose **Change my answers**, confirm, and send again within 30 minutes (Section 5.7).",
    ])
    h3("3.4.7 Confirmatory test (steps 8–9)")
    para("If the pathway needs confirmation (for example a repeat HbA1c), the GP chooses **Order confirmatory test**. When the result arrives, a GP or specialist chooses **Record confirmatory result**. A positive result moves to consultation. A negative FIT or breast result may be closed with evidence; a negative cervical or diabetes result needs a surveillance care plan. If no confirmation is needed the case goes straight to consultation.")
    h3("3.4.8 Consultation (step 10)")
    para("The GP reviews the full clinical record on the case page: patient summary, tests and investigations, consultations, medicines, referrals and appointments, and outreach. Use **Add consultation note** and **Add medicine** to record the consultation; then refer, or record a treatment or surveillance plan in primary care.")
    h3("3.4.9 Referral (steps 11–12)")
    steps(["Choose **Send referral**, select the organisation and specialist, and write a clinical summary. A referral to an outside organisation needs the patient's consent to share.",
           "The receiving specialist chooses **Accept / reject referral**. A rejection needs a reason code and a note.",
           "A rejected referral returns to the referrer, who re-routes it to another provider, completes the missing information, or chooses **Alternative plan: back to consultation**."])
    h3("3.4.10 Appointment and attendance (steps 13–14)")
    para("Book the appointment inside the pathway target; a later date needs a reason, which is stored as the booking note. The patient receives the appointment message. After the appointment record **attended** or **no-show** with a barrier code (for example transport, work, cost, language, Ramadan hours). A no-show sends a rebooking message and opens the *No-show* step for rebooking.")
    h3("3.4.11 Care plan and closure (steps 15–16)")
    table(["Closure type", "Allowed when", "Who"], [
        ["Evidence: appointment fulfilled", "Appointment attended, or treatment / surveillance started", "Any clinical role (coordinator, GP, specialist, clinical lead)"],
        ["Evidence: care plan", "Treatment / surveillance started, or appointment attended", "Any clinical role"],
        ["Evidence: negative confirmatory result", "Confirmatory test, FIT and breast programmes only", "Any clinical role"],
        ["Authorised decision", "Any open state; reason code + note of at least 10 characters", "GP, specialist or clinical lead"],
    ], [5.0, 7.0, 5.0], title="Closure guard", first_col_bold=True)
    para("Only a human can close a case. Evidence recorded before a re-open belongs to the earlier episode and cannot close the re-opened case.", size=9.5, color=GREY)
    h3("3.4.12 Re-opening")
    para("Only a clinical lead can re-open a closed case (**Re-open case**, note of at least 10 characters). The case returns to *Triaged*. If the original pathway target has already passed, the case is overdue at once.")
    h3("3.4.13 Ownership, reassignment and transfer")
    bullets([
        "**Reassign owner** (coordinator, GP, clinical lead) with a reason of at least 3 characters.",
        "**Transfer to another provider (two-key)** (coordinator, GP, specialist, clinical lead): the receiver must accept. Until then the current owner stays accountable; the case shows *Transfer waiting*. A declined transfer needs a reason.",
    ])
    h3("3.4.14 Deadlines and escalation (step 17)")
    para("Every open step has a deadline. The deadline check runs every 5 minutes and raises alerts on the ladder shown below. Once the pathway target is missed, the pathway clock raises one consolidated alert per case per level, from L2. Patient reminders are sent only between 08:00 and 21:00 UAE time. A case whose owner is inactive moves to the team pool automatically.")
    figure(DIAG / "escalation.png", "Escalation ladder for a step deadline. L1 uses the reminder point set per state and tier (default 50 %).", width_cm=17.0)
    para("Acknowledge each escalation on the case with what you did. The system administrator can also run the deadline check by hand (Advanced → *Run the check now*); due reminders are then sent as the rules engine and the request is audited.", size=9.5, color=GREY)
    h3("3.4.15 The case page")
    para("Every case page shows the patient's clinical record first, then the current step: the step, accountable owner, step deadline and urgency, a step tracker, the clinical rule and pathway target, and the **Next step** card with the main action and *More actions*. The tabs below show the timeline, messages and events, and the case audit.")
    figure(FIG / "gp_case_step.png", "Current step and Next step card on a case (family doctor, laptop).", width_cm=15.5)
