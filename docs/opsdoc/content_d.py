"""Section 5: safety and governance."""


def part_d(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    FIG, GREY = g["FIG"], g["GREY"]

    h1("5. Safety and governance")
    h2("5.1 Human decisions and the limits of ML")
    bullets([
        "The ML model (scikit-learn) estimates the chance that a patient misses the next step and is used **only** to order outreach. The queue sorts by deadline status first, then tier, then review flag, and only then by the ML score; the score can never move a case across tiers.",
        "The ML score is never read by the state machine, triage or closure. The case page labels it “Outreach priority (ordering only)” with a *Why this estimate* explanation.",
        "Gender, nationality, age band and language are monitored for fairness and are **never model inputs**; the need for an interpreter is an input.",
        "The **system administrator** trains candidate models. A candidate is promoted only if it passes the gates (AUC of at least 0.60 and better than the current model, calibration error at most 0.10, calibration slope 0.7–1.3, calibration-in-the-large within 0.05, and the fairness gates) **and** a **clinical lead** approves it with a note. Rollback needs a previous approved version.",
        "The lessons-learned engine (run by the system administrator) only **proposes** changes to deadlines, reminder timing, message variants or channel order; a clinical lead must approve each one before it applies.",
    ])
    h2("5.2 Clinical rules safety")
    bullets([
        "Rules are deterministic and versioned, with a rule ID on every case. Where a guideline is ambiguous the safer option is used and marked CONFIRM for clinical-lead sign-off.",
        "Fail-safe: uncertain results become review cases at the most urgent plausible tier; tiers only go up automatically (a data-layer interlock refuses any downgrade that is not an authorised clinician decision); a review case cannot progress or message the patient before a clinician resolves it.",
        "T0 and T1 cases need clinician-confirmed triage.",
    ])
    h2("5.3 Actions that need a named person's sign-off")
    table(["Action", "Who", "Written rationale"], [
        ["Authorised closure", "GP, specialist, clinical lead", "Reason code and at least 10 characters"],
        ["Close with evidence", "Coordinator, GP, specialist, clinical lead", "Evidence checked against the case state"],
        ["Re-open a case", "Clinical lead", "At least 10 characters"],
        ["Resolve a clinical review (may lower the tier)", "GP, specialist, clinical lead", "At least 10 characters"],
        ["Reassign owner", "Coordinator, GP, clinical lead", "At least 3 characters"],
        ["Request / decide a transfer", "Coordinator, GP, specialist, clinical lead / the receiver", "At least 5 characters; a reason to decline"],
        ["Decide a referral", "Specialist (own organisation), clinical lead", "Rejection: reason code and note"],
        ["Start a care plan", "GP, specialist, clinical lead", "At least 5 characters"],
        ["Acknowledge an escalation", "Coordinator, GP, specialist, clinical lead", "At least 3 characters"],
        ["Record a death", "GP, specialist, clinical lead", "At least 10 characters"],
        ["Correct a death record", "Clinical lead", "At least 20 characters"],
        ["Record consent or a decline on the patient's behalf", "Coordinator, GP, specialist, clinical lead", "At least 10 characters"],
        ["Resolve a patient match", "Coordinator, GP", "Patient chosen from the record"],
        ["Approve, reject or roll back a model; decide a proposal", "Clinical lead", "At least 5 characters"],
        ["Patient wording sign-off (clinical / translation)", "Clinical lead / medical translator", "At least 5 characters"],
        ["Deactivate or reactivate a user", "System administrator", "Reason recorded"],
    ], [6.4, 5.8, 4.8], title="High-impact actions. Automated actors (rules engine, deadline check, ML service) are always refused.", first_col_bold=False)
    h2("5.4 Closure guard")
    para("A case closes only with recorded evidence of the right type for its state, or an authorised human decision with a reason code and a note (Section 3.4.11). Only a human can close a case. A flagged case can be closed only by an authorised clinician decision, which also resolves the flag.")
    h2("5.5 Audit log")
    bullets([
        "Every read and write, sign-in, failed sign-in, sign-out, refused attempt and account change is recorded with the actor, role, time, action, record and outcome.",
        "The log is **append-only** (database triggers and an application guard) and **tamper-evident**: each event's SHA-256 hash includes the previous event's hash.",
        "The **clinical lead** has a read-only **Audit trail** for clinical governance. The **system administrator** has the same search plus **Check the log is intact**, which verifies the whole chain.",
        "If a request cannot be logged it is refused rather than served unlogged.",
    ])
    h2("5.6 Data protection")
    bullets([
        "Least-privilege access by role and organisation, plus the patient's consent to share outside the managing organisation. The system administrator has no access to cases.",
        "Consent is checked before every message and channel. Withdrawal of care-coordination consent stops automated messaging and sends open cases to review.",
        "Analysts receive aggregates only; any group smaller than 5 is suppressed.",
        "The installable app never caches patient data or signed-in pages. Sessions expire after 30 minutes without activity.",
        "Passwords are stored as argon2id hashes; they are never shown on the site or kept in the code.",
        "All records are fictional (SYN- MRNs). The free hosting tier is **not suitable for real health data**: a pilot needs hosting inside the UAE, an agreement with the health authority, backups and monitoring.",
    ])
    h2("5.7 What patients can see, and the answer lock")
    bullets([
        "Patients see only their own case, in plain language: meaning, urgency, next step, timeframe, care-team name, journey, their messages and their own questions with answers. **Clinician notes are never shown.**",
        "The teach-back form is **locked** once answers are sent. Sending again without **Change my answers** is refused and nothing is saved.",
        "**Change my answers** must be confirmed by the same patient login, lasts 30 minutes and is used by one submission only. Every version stays in the record; the request, any cancellation and the new answers are audited with the previous answers.",
        "Teach-backs recorded by a navigator do not lock the patient's form.",
    ])
    figure(FIG / "patient_locked.png", "Patient page after answers are sent: the saved answers are shown locked, with *Change my answers* (phone).", width_cm=6.5)
    h2("5.8 Patient wording governance")
    bullets([
        "Patient text goes live only after a **clinical sign-off** by a clinical lead and, for Arabic, Hindi and Urdu, a **translation sign-off** by a medical translator: two different people. The English source must itself be signed off in its current wording.",
        "Each text passes automatic reading-level and tone checks before clinical sign-off. A sign-off is bound to the exact wording (hash); any edit needs a new sign-off.",
    ])
    h2("5.9 Separation of clinical and system duties")
    table(["Clinical lead (clinical approvals and oversight)", "System administrator (system operation)"], [[
        "Approve, reject or roll back outreach models\nDecide rule and deadline change proposals\nClinical sign-off of patient texts\nL3 escalations and overdue oversight\nCase actions, re-open, death-record correction\nAnswer any e-consult\nOutcomes and Deadlines & settings (read)\nRead-only audit trail",
        "User accounts, roles, password resets, deactivation\nSystem status and storage\nAudit trail with the chain check\nAdvanced tools page and health check\nRun the deadline check now (as the rules engine)\nTrain candidate models (approval stays with the lead)\nRun the proposal engine and simulations\nNo queue, case pages, approvals or escalations",
    ]], [8.5, 8.5], size=9, zebra=False, valign="top", title="Who does what since the role separation (code aea0d72 and aac2f3d)")
    para("Both rules are enforced on the server (the API refuses the action) and in the screens (the menu and page access); automated tests check every rule.", size=9.5, color=GREY)
