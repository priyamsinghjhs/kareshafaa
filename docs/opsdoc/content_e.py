"""Section 6: exceptions, escalation, administration and support."""


def part_e(g):
    h1, h2, h3, para, bullets, steps, table, figure, callout = (g[k] for k in (
        "h1", "h2", "h3", "para", "bullets", "steps", "table", "figure", "callout"))
    GREY = g["GREY"]

    h1("6. Exceptions, escalation and support")
    h2("6.1 Exception handling")
    table(["Exception", "How it is detected", "What to do", "Who"], [
        ["Overdue step", "Step clock red; *Overdue* view; L2–L4 alerts", "Act on the step, then acknowledge the escalation with what was done", "Owner; supervisor (L2); clinical lead (L3); medical director (L4, T0/T1)"],
        ["Pathway target missed", "Consolidated pathway alert from L2", "Expedite care; any booking beyond the target needs a reason", "Owner; clinical lead"],
        ["Unreachable patient", "Digital ladder fails; call task; at least 2 failed calls", "Mark *Unreachable* (with confirmation). When reached, record the call: the case returns to *Patient notified*. Otherwise a clinician may close as *unable to contact*", "Coordinator; GP or clinical lead for closure"],
        ["Teach-back not passed", "Not all 3 answers correct", "Call the patient (interpreter if flagged) and record a navigator teach-back", "Coordinator"],
        ["Patient declines care", "Patient presses decline, or staff record it (at least 10 characters)", "A clinician talks to the patient and documents an informed decline (authorised closure), or chooses **Patient reconsidered – resume**", "GP"],
        ["No-show", "Attendance recorded as no-show with a barrier", "Address the barrier and rebook", "Coordinator or specialist"],
        ["Referral rejected", "*Referral rejected* view", "Re-route to another provider, complete the information, or go back to consultation", "Referring clinician; coordinator"],
        ["Death recorded", "A clinician records the death (at least 10 characters)", "All messaging stops; call tasks and queued reminders are cancelled; open cases go to review. Close each with the authorised reason *deceased*", "GP, specialist or clinical lead"],
        ["Death recorded in error", "Wrong patient identified", "Clinical lead corrects the record (at least 20 characters); open cases are flagged for review and cases closed as *deceased* are listed for re-opening", "Clinical lead"],
        ["Consent withdrawn", "Patient or staff record a withdrawal", "Automated outreach stops; a clinician documents an informed decline or agrees another route", "GP"],
        ["New result on an open case", "Linked result; review flag (duplicate, conflicting or more urgent)", "Clinician reviews; the tier is never lowered automatically", "GP or clinical lead"],
        ["Result after closure (within 180 days)", "New case flagged for review", "Clinician reviews with the earlier history", "Clinical lead"],
        ["Unmatched or unreadable message", "Results inbox; ACK AE", "Resolve the match, or check the message with the laboratory", "Coordinator or GP"],
        ["Owner deactivated or inactive", "Detected by the deadline check", "Case moves to the team pool automatically; the coordinator assigns a named owner", "Coordinator"],
        ["Transfer declined", "Receiver declines with a reason", "The original owner stays accountable and agrees another route", "Original owner"],
        ["Service busy or offline", "Busy message or offline page", "Nothing was saved; repeat the action", "Any user"],
    ], [3.3, 3.9, 6.2, 3.6], title="Exceptions and what to do", size=8.4, first_col_bold=True)
    h2("6.2 Escalation contacts and incident reporting")
    table(["Level", "Raised to", "Expected response"], [
        ["L1 (reminder point, default 50 %)", "Accountable owner", "Complete the step"],
        ["L2 (100 %, breach)", "Owner's supervisor, or the programme coordinator supervisor", "Unblock or reassign; acknowledge with a note"],
        ["L3 (150 %; T0 at breach)", "Programme clinical lead", "Clinical decision; acknowledge with a note"],
        ["L4 (200 %, T0/T1 only)", "Medical director or duty clinician (a clinical lead without a programme)", "Direct intervention; acknowledge with a note"],
    ], [4.6, 7.0, 5.4], title="Escalation contacts", first_col_bold=True)
    para("Each alert is recorded on the case and as an internal message (delivery simulated). Report any safety concern to the programme clinical lead and record it on the case as a lesson of type *Incident / near miss*. Technical incidents (sign-in, storage, errors) go to the system administrator.")

    h2("6.3 Administration and support operations")
    para("All procedures in this section are for the **system administrator** unless stated otherwise.", size=9.5, color=GREY)
    para("**Create a user**", after=2)
    steps(["Open **Users** → **Add a user**.", "Enter username, full name, role, site and team. Clinical roles (coordinator, GP, specialist, clinical lead) must be linked to a staff directory record, and each record can have only one account.",
           "Set a temporary password that meets the policy, or leave it blank for a generated 16-character password shown once. The user must change it at first sign-in.",
           "Save. The creation is audited (`user.create`). Patient accounts are created from the patient record, not from this page."])
    para("**Change role, site or team**", after=2)
    steps(["Open **Users**, find the person and choose **Change role**.", "Select the new role, site and team and save (`user.role_change`). You cannot change your own role."])
    para("**Reset a password or unlock an account**", after=2)
    steps(["Find the user and choose **Reset password**.", "Enter a temporary password, or leave it blank to let the system generate a 16-character one that is shown once.",
           "Give it to the user through a separate, secure channel. Their sessions end, any lockout is cleared and they must change it at the next sign-in (`user.password_reset`).",
           "A lockout also clears by itself after 15 minutes."])
    para("**Deactivate or reactivate a user**", after=2)
    steps(["Find the user and choose **Deactivate**; enter the reason (for example *left the organisation*).", "All their sessions end at once. Their open cases move to the programme team pool at the next deadline check, so the coordinator can assign a named owner.",
           "Accounts are never deleted, so the audit trail stays complete. **Activate** restores access, also with a reason. You cannot deactivate your own account."])
    para("**Check system status**", after=2)
    steps(["Open **System**. Check version, mode (production), database and the storage line: it must read *Data is stored permanently (PostgreSQL)* with the database host.",
           "If it says data is not stored permanently, the database connection is missing and data would be lost on restart. Raise it with the system owner at once.",
           "For a quick technical check use the health check on the Advanced page; it reports status, version, storage and timed-out requests."])
    para("**Run the deadline check now**", after=2)
    steps(["Open **Advanced** → *Deadline check (SLA sweep)* → **Run the check now**, and confirm.",
           "The check runs as the rules engine: due reminders and escalations are raised for every open case. The result shows the number of cases checked, reminders and escalations.",
           "Your request is audited as `sla.sweep_requested`. The check also runs automatically every 5 minutes, so use this only after an outage or a configuration change."])
    para("**Train a candidate outreach model**", after=2)
    steps(["Open **Advanced** → *Outreach model internals* → **Open model registry**, and choose **Retrain candidate** (gradient boosting or logistic regression). The proposal engine has a similar button, **Run proposal engine now**, on the *Proposed rule changes* tab.",
           "Read the gate results. A candidate never goes live by itself: tell the clinical lead, who approves or rejects it in **Approvals**."])
    para("**Verify the audit chain**", after=2)
    steps(["Open **Advanced** → *Audit trail* (or the audit page) and press **Check the log is intact**.",
           "If the check fails, do not change anything; record the time and result and report it to the system owner and the clinical lead as an incident."])
    para("**Hosting notes**", after=2)
    bullets([
        "The service runs as one container on Render (free web service) with permanent data in PostgreSQL on Neon. Restarts and redeploys never reset or overwrite data.",
        "Staff accounts receive a first password from a hosting secret only if they have no password yet; a password changed by a user or administrator is never overwritten. Passwords are never in the code or on the site.",
        "Neon Free keeps 6 hours of restore history. Take a regular database dump to storage you control for anything important.",
    ])
