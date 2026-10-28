"""Section 7: appendices."""


def part_f(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))

    h1("7. Appendices")
    h2("Appendix A. Urgency tiers")
    table(["Tier", "Meaning", "First action (triage)", "Clock", "Triage by", "Default target", "Patient wording"], [
        ["T0 Critical", "Same-day clinician action", "1 hour", "24/7", "Clinician", "1 day", "Please contact us today"],
        ["T1 Urgent", "Suspected cancer, high-grade abnormality or unsafe data", "4 hours", "24/7", "Clinician", "14 days", "Please act soon"],
        ["T2 Soon", "Positive screen needing a diagnostic test", "1 working day", "UAE working days", "Rule (automatic)", "31 days", "Important – please act within 2 weeks"],
        ["T3 Routine", "Surveillance, prevention, repeat sample", "2 working days", "UAE working days", "Rule (automatic)", "90 days", "Please act within a month"],
    ], [2.0, 3.6, 2.3, 1.9, 1.8, 2.0, 3.4], title="Urgency tiers. The default target applies when a pathway has none, or when a case was escalated above its clinical tier.", size=8.6, first_col_bold=True)
    para("Examples from the rules: T0 – HbA1c of 15 % or more, or FPG of 500 mg/dL or more; T1 – BI-RADS 4 or 5, FIT of 400 µg/g or more, high-grade cervical cytology; T2 – FIT positive, HPV 16/18, BI-RADS 0, HbA1c of 10 % or more; T3 – HbA1c in the diabetes or prediabetes range, repeat samples.", size=9.5)

    h2("Appendix B. Step deadlines (SLA)")
    table(["Step (state)", "T0", "T1", "T2", "T3"], [
        ["Result issued (to triage)", "1 h", "4 h", "1 wd", "2 wd"],
        ["Triaged (to patient notified)", "4 h", "24 h", "2 wd", "5 wd"],
        ["Patient notified (to teach-back)", "24 h", "48 h", "5 wd", "10 wd"],
        ["Acknowledged (to next step)", "24 h", "72 h", "7 wd", "14 wd"],
        ["Confirmatory test", "48 h", "7 days", "14 wd", "28 wd"],
        ["Consultation", "24 h", "72 h", "7 wd", "14 wd"],
        ["Referral sent (to decision)", "24 h", "48 h", "5 wd", "10 wd"],
        ["Referral rejected (to re-route)", "8 h", "24 h", "2 wd", "5 wd"],
        ["Referral accepted (to booking)", "24 h", "72 h", "10 wd", "20 wd"],
        ["Appointment booked (to attendance)", "7 days", "14 days", "30 wd", "60 wd"],
        ["Appointment attended (to care plan)", "48 h", "5 days", "10 wd", "20 wd"],
        ["Treatment / surveillance started (to closure)", "48 h", "72 h", "7 wd", "14 wd"],
        ["No-show (to rebooking)", "8 h", "24 h", "2 wd", "5 wd"],
        ["Unreachable (to review)", "24 h", "72 h", "7 wd", "14 wd"],
        ["Declined – clinician review", "24 h", "48 h", "5 wd", "10 wd"],
    ], [7.4, 2.4, 2.4, 2.4, 2.4], title="Default step deadlines (wd = UAE working day; configuration, not clinical guidance)", center_cols=(1, 2, 3, 4), first_col_bold=True)
    para("Deadlines are stored as configuration (visible in *Deadlines & settings*) and change only through a proposal approved by a clinical lead. A step's deadline never runs past the pathway target. UAE working days exclude weekends and public holidays; Islamic holiday and Ramadan dates are approximate.", size=9.5)

    h2("Appendix C. Pathways and targets")
    table(["Pathway", "Rule IDs (examples, tier)", "Next step", "Target (calendar days from result)"], [
        ["colonoscopy", "CRC-FIT-POS (T2); CRC-FIT-URGENT (T1)", "Colonoscopy", "31 (FIT of 400 µg/g or more: 14)"],
        ["repeat_fit", "CRC-FIT-REPEAT (T3)", "New FIT kit", "28"],
        ["colposcopy", "CX-HPV1618, CX-HPVOTHER-LG (T2)", "Colposcopy with biopsy", "42"],
        ["colposcopy_urgent", "CX-HIGH-GRADE, CX-SUSP-INVASIVE (T1)", "Urgent colposcopy or gynae-oncology", "14"],
        ["reflex_cytology", "CX-HPVOTHER-REFLEX (T3)", "Reflex cytology on the same sample", "14"],
        ["reflex_hpv", "CX-CYTO-LG-NOHPV (T3)", "Reflex HPV test", "14"],
        ["surveillance_1y", "CX-HPVOTHER-NILM (T3)", "Surveillance plan; repeat HPV at 12 months", "28 (plan documented)"],
        ["repeat_sample", "CX-UNSAT (T3)", "Repeat sample", "120"],
        ["assessment_imaging", "BR-0 (T2)", "Recall for assessment", "7"],
        ["biopsy", "BR-4, BR-4A/B/C, BR-5 (T1)", "Triple assessment and core biopsy", "7"],
        ["same_day_review", "DM-A1C-CRIT, DM-FPG-CRIT (T0)", "Same-day clinician contact", "1"],
        ["confirm_then_gp", "DM-A1C-DIAB, DM-FPG-DIAB (T3); DM-A1C-HIGH, DM-FPG-HIGH (T2)", "Confirmatory test, then GP visit and care plan", "28 (high range: 14)"],
        ["prediabetes_gp", "DM-A1C-PRE, DM-FPG-PRE (T3)", "GP advice; retest within 12 months", "90"],
        ["human_review", "Any review reason (for example CRC-ERR, BR-3, CX-ERR)", "Clinician review", "Tier default (Appendix A)"],
        ["routine_recall", "Negative results", "No case; routine screening", "–"],
    ], [3.3, 5.3, 4.9, 3.5], title="Pathways and end-to-end targets in the rules engine (targets marked CONFIRM await clinical-lead sign-off)", size=8.6)
    para("When a case is escalated above its clinical tier (for example by a review floor), the more urgent of the pathway target and the tier default applies.", size=9.5)

    h2("Appendix D. Reason codes")
    table(["List", "Codes"], [
        ["Authorised closure reasons", "informed_decline · unable_to_contact · deceased · moved_abroad · care_elsewhere_verified · clinically_not_indicated · duplicate"],
        ["Closure evidence types", "appointment_fulfilled · care_plan · negative_confirmatory_result"],
        ["Referral rejection reasons", "wrong_service · capacity · incomplete_info · out_of_network · not_clinically_indicated"],
        ["No-show barriers", "transport · work · fear_anxiety · cost · language · forgot · childcare · ramadan_hours · unknown"],
        ["Delay reasons (lessons)", "patient_not_responding · contact_details_wrong · language_barrier · capacity · referral_incomplete · awaiting_result · patient_travel · patient_anxiety · staff_absence · system_issue · other"],
        ["Call outcomes", "reached · no_answer · wrong_number"],
        ["Lesson types", "overdue · drop_off · incident · no_show · rejected_referral · unreachable · declined"],
    ], [4.4, 12.6], title="Reason codes used in forms", first_col_bold=True)

    h2("Appendix E. Glossary")
    table(["Term", "Meaning"], [
        ["Advanced", "System administrator page with the audit trail and chain check, model internals, the deadline check, interoperability samples, the API reference, load-test notes and a health check."],
        ["Approvals", "Clinical lead page (analyst: *Models & rules*, read-only) with the outreach model, proposed rule changes, lessons learned, deadlines and settings, and patient texts."],
        ["Channel ladder", "App, then SMS, then WhatsApp, then a navigator phone call; reminders, appointment and no-show messages use at most the first three rungs."],
        ["Closure guard", "Code rule that blocks closure without evidence or an authorised, reasoned human decision."],
        ["Deadline check (SLA sweep)", "Background job, every 5 minutes, that updates clocks, raises escalations and sends due reminders; the system administrator can also run it by hand."],
        ["E-consult (Ask a specialist)", "A clinician-to-clinician question about a case, answered by the addressed clinician or a clinical lead, with a response time by urgency."],
        ["Escalation", "A named alert raised when a step or pathway clock passes a ladder threshold."],
        ["Hash chain", "Each audit event's hash covers the previous event, so any edit or deletion is detectable."],
        ["Lesson", "A recorded delay, drop-off, no-show, rejection, decline or incident reason that feeds outcome analysis and change proposals."],
        ["Model card", "Summary of a model version: intended and prohibited use, gate results, fairness and approval history."],
        ["Navigator", "The staff member (normally the care coordinator) who calls patients and records calls and teach-backs."],
        ["Outreach priority", "ML estimate of the chance of missing the next step; orders work only within the same deadline status and tier."],
        ["Pathway", "The care route chosen by the rule, for example colonoscopy or confirm_then_gp."],
        ["Production mode", "Operating mode of the hosted service: full workflows, password sign-in only, approval gates, write limit, data never reset."],
        ["Review reasons", "Codes explaining why a result needs a clinician, for example amended_result, missing_unit, sex_mismatch, pregnant, stale_result."],
        ["Team pool", "Programme-level owner used when no named owner is available; listed under *No named owner*."],
        ["Two-key transfer", "Ownership hand-over that completes only when the receiving side accepts."],
    ], [4.2, 12.8], title="Glossary", first_col_bold=True)
