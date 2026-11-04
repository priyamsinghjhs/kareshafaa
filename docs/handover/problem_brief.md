# 1. Problem brief – Challenge 05: closing the loop on abnormal screening results

## The problem

When a screening test (bowel FIT, cervical HPV/cytology, breast mammography, diabetes HbA1c/fasting glucose) comes
back abnormal, the patient must be told, understand it, and reach the next step (confirmatory test, specialist,
treatment). Each hand-off can fail silently. In Abu Dhabi's colorectal screening programme (2015–16), only
**30.5 % of people with a positive FIT had a colonoscopy** (BMC Cancer 2021,
https://link.springer.com/article/10.1186/s12885-021-08623-9). Longer delay after a positive FIT is linked to
higher odds of cancer and of advanced-stage disease (Corley et al., JAMA 2017,
https://jamanetwork.com/journals/jama/fullarticle/2620087). Sources and the pilot sizing are in `docs/pilot_plan.md`.

## Users

| User | What they need from KareShafaa |
|---|---|
| Screening coordinator | One work queue of abnormal results, ordered by urgency, with an owner, a deadline (SLA timer) and the next action for each |
| Family doctor (GP) | Their patients' abnormal results, what was decided, what is still open |
| Specialist / receiving service | Referrals to accept or reject with a reason; appointment outcomes |
| Programme clinical lead | Alerts when steps or pathway targets are missed; loop-closure, timeliness and equity reports; rule and model governance |
| Administrator | Accounts, roles, integrations status, audit chain |
| Patient | Their result explained in Arabic, English, Hindi or Urdu; confirm or ask to change an appointment; say what gets in the way |

## The task the system performs

Take an abnormal screening result (HL7 v2 ORU^R01 or FHIR R4 Bundle), match it to the right patient, apply the
clinical rules to set an urgency tier and pathway with a target date, open a tracked case with an owner and a
step-by-step SLA clock, escalate when a step or the pathway target is missed, record every action in a
hash-chained audit log, and close the loop only with evidence (attended visit or a signed clinical decision).

## Scope

**In:** the four screening programmes above; inbound HL7 v2 and FHIR; rules engine with human review; case
workflow and SLA escalation ladder (L1 reminder to L4 director); patient app in four languages; FHIR R4 export;
outcome and equity reporting; ML outreach-priority model that only orders cases *within* a tier; RBAC and audit.

**Out (not built, or simulated):** real SMS/WhatsApp gateway (messages are simulated); live Malaffi or laboratory
connection; single sign-on and MFA; billing; diagnosis or treatment decisions (always made by clinicians); real
patient data (all data in this repository is synthetic).

## Intended outcome

More abnormal results reach their next clinical step, sooner and within target, with no group left behind and
with evidence for every closure. Pilot measures and targets are in `pilot_plan.md` (targets are labelled
ASSUMPTION until agreed with the partner).
