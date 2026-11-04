# 5. Pilot plan (condensed)

Full plan with sources: [`docs/pilot_plan.md`](../pilot_plan.md). Status: **proposal** – no partner has agreed
yet and no organisation named there has endorsed KareShafaa. Values marked ASSUMPTION or CONFIRM must be agreed.

## Design

90 days in one Abu Dhabi primary care cluster, alongside current practice (staff keep existing referral routes;
KareShafaa adds ownership, deadlines, patient messages and evidence of closure). Bowel (FIT) first; cervical and
breast from day 45 if the go/no-go gate is passed. UAE-hosted only; free public hosting is never used for real data.

## Participants

| Group | Who (roles, not named people) |
|---|---|
| Review lead | **Programme Clinical Lead (to be named)** – owns the safety huddle, the gate and sign-off |
| Clinical users | Screening coordinators, family doctors and the receiving endoscopy / breast imaging units at the pilot sites |
| Patients | Adults with an abnormal screening result at the pilot sites (eligibility per programme specification; to confirm with the cluster). Volume **ASSUMPTION:** 300–600 abnormal results in 90 days |
| Governance | Regulator and programme owner contacts (DoH, ADPHC), the cluster's data protection and information security officers (to be named) |
| Technical | KareShafaa product lead and engineer (to be named); cluster IT for the laboratory HL7 feed |
| Patient voice | Patient and community representatives reviewing messages in Arabic, English, Hindi and Urdu |

## Success criteria at day 90 (from `docs/pilot_plan.md`)

1. No serious safety incident attributable to KareShafaa.
2. Loop closure and within-target rates better than baseline for bowel screening, with the 90 % CI reported.
3. No equity group more than 10 points (CONFIRM) below the overall rate without an explanation and action.
4. Coordinators and clinicians would keep using it (short survey, ≥ 70 % agree, ASSUMPTION).
5. Security assessment passed and audit chain intact for the whole pilot.

KPI targets (loop closure +20 points, ≥ 80 % within target for critical/urgent, lost to follow-up halved) are
**ASSUMPTIONS** until agreed.

## Review dates (relative to pilot start, day 1 = first live result)

| When | Review | Led by |
|---|---|---|
| Day −56 to −1 | Readiness: data sharing agreement, approvals, ADHICS assessment, clinical sign-off of rules/SLAs/messages, HL7 test feed | Programme Clinical Lead (to be named) |
| Days 1–14 | Baseline extract (last 12 months); staff training | Clinical lead + cluster data team |
| Weekly from day 15 | Safety huddle: every critical/urgent case and every human override | Programme Clinical Lead (to be named) |
| Day 30 | First KPI and equity review | Clinical lead + product lead |
| **Day 45** | **Go/no-go gate** for cervical and breast | Programme Clinical Lead (to be named) + steering group |
| Day 60 | Equity review by language, nationality group and region | Clinical lead |
| Day 90 | Final report to the steering group; continue / extend / stop | Steering group |
