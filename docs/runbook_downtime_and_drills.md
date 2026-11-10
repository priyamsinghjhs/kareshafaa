# Runbook: downtime and the critical-result drill

Owner of this runbook: **programme operations lead** (role; name to be filled in by the deploying organisation).
Policy values in this runbook are **placeholder, verify** (clinical policy `2026.11-p1`, `kareshafaa/policy.py`).

## 1. What the current hosting can and cannot do (be honest about it)

| Fact | Consequence |
|---|---|
| Render **free** web service, one instance, **sleeps after 15 minutes idle**; first request takes ~30–60 s | Timed jobs (deadline check, escalations, reconciliation) **do not run while it sleeps**. Overdue steps are found on the first sweep after wake-up, not on time. |
| Render free disk is **ephemeral** | `DATA_DIR/audit-fallback.jsonl` (the last-resort audit fallback file) is **lost on every restart or deploy**. Only events already in the `audit_fallback_events` table survive. |
| Neon **free** PostgreSQL, compute suspends when idle | First query after idle is slow; the timed-jobs lock is re-taken automatically (`docs/background_jobs.md`). |
| No paging, SMS or e-mail provider is connected | Escalations "by phone" are tasks and notes in the app. **No one is woken up by the system.** |
| Hosting is outside the UAE | Synthetic records only (`docs/deployment_uae.md`). |

So on this hosting KareShafaa is a **demonstration of the loop**, not a safety net. A pilot needs an always-on
instance in a UAE region, persistent disk (or no fallback file), and an alerting channel (section 5).

## 2. Signs of trouble (where to look)

* **Admin → System → Monitoring**: each timed job's last success and failures; "Results without a case";
  "Waiting in results inbox"; "Open escalations". A job that has not succeeded for 2 × the sweep interval
  (default 300 s) means escalations are not being raised.
* **Admin → System** audit-fallback alert: audit events waiting to be added.
* `/healthz` (liveness, policy version) and `/readyz` (database and initial records).

## 3. Downtime procedure (application or database unavailable)

1. **Declare it.** Operations lead records start time and scope (whole app, results inbox only, database).
2. **Switch to paper / phone for critical results.** The lab phones critical results directly to the ordering
   clinician or the on-call clinician as it would without KareShafaa. KareShafaa is never the only route for a
   critical result.
3. **Keep a downtime log** (paper or spreadsheet held locally, not in a shared drive outside the organisation):
   time, patient identifier, result, who was told, read-back done (yes/no), next step, owner.
4. **Results inbox.** Interface messages that arrive while the app is down stay with the sender's retry queue. After
   recovery, check the results inbox and run **Results inbox → Results not in the loop → Check now**.
5. **Recover.** When `/readyz` is green: on Admin → System run **Check results now** and, if shown, **Add them to
   the audit trail now**. Check Monitoring: every job OK.
6. **Back-enter** the downtime log: each critical contact as a read-back on its case (**Record read-back** with the
   real contact time), other contacts as case notes. The original time is kept; entry time is audited.
7. **Close the downtime** in the log with end time; review it at the next safety huddle (section 6).

## 4. Critical-result drill (monthly, 15 minutes, synthetic patient only)

Purpose: prove the fast lane works end to end and people know it. Run on a **synthetic** record only.

| Step | Who | Pass when |
|---|---|---|
| 1. File a critical synthetic result (results inbox or sample HL7) | coordinator | a case appears in **Critical: read-back needed** on the work queue within one sweep |
| 2. Try to triage without a read-back | GP or specialist | refused with the fast-lane message |
| 3. Phone the "receiving clinician" (role-play), record **read-back**: method, who reported, who received, time, confirmed | GP or specialist | banner clears; triage becomes available |
| 4. Leave a step overdue (or advance the clock in a test copy) | coordinator | escalation from level 2 is marked **by phone**; phone task created |
| 5. Try to close as "unable to contact" | clinical lead | the reason is not offered and the API refuses it |
| 6. Read the audit trail for the case | clinical lead | `critical.read_back_recorded`, escalation and refusal events present |

Record: date, participants (roles), time from step 1 to step 3, failures, actions. Two failed drills in a row
stop any move from shadow to assisted mode (`docs/operating_modes.md`).

## 5. Before a pilot (gaps to close)

* Always-on instance in a UAE region; health check alerting to an on-call phone (provider's free tier or the
  organisation's own monitoring).
* Persistent storage for the audit fallback file, or rely on the table only.
* A real paging route for level-3/4 escalations, tested in the drill.
* Named owners for this runbook and the drill.

## 6. Review

After every downtime or failed drill: what happened, what the downtime log showed, whether any result needed
action and was late, and the action owner, written in the organisation's incident record. Check **Outcomes → Why cases are delayed** for
the affected cases.
