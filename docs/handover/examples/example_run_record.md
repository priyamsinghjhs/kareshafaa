# KareShafaa handover example: run record

Synthetic data only. Seed 2026, 300 synthetic patients, commit `2a6e098`, started 2026-11-04T02:48:18Z, 12.3 s.

## Input: HL7 v2 ORU^R01 (`docs/handover/examples/input_oru_r01_fit_positive.hl7`)

* Ingest status: **processed**; actionable: **True**; message id `LAB-20261020-000123`
* Alert: case 306, Bowel (colorectal) screening - FIT, tier **high**, rule `CRC-FIT-POS`
* Pathway: colonoscopy; target Colonoscopy within 31 days of referral (DoH) (due 2026-12-05T02:48:18.998507)
* State now: Result issued; human review required: True
* SLA timer on the current step: {"status": "green", "fraction": 0.0, "due_at": "2026-11-05T02:48:18.998507", "escalation_level": 0}
* Sweep at +1 days: {"checked": 1, "reminders": 1, "supervisor": 1, "breaches": 1}; escalation levels so far [1, 2]
* Sweep at +40 days: {"checked": 1, "supervisor": 1, "target_breach_alerts": 1}; escalation levels so far [1, 2, 2]
* FHIR export: `case_306_fhir_bundle.json`

HL7 ACK returned:

```
MSH|^~\&|KARESHAFAA|PROGRAMME|LAB|LAB|20261104024819+0000||ACK^R01|ACKLAB-20261020-000123|P|2.5.1
MSA|AA|LAB-20261020-000123|accepted
```

## Input: FHIR R4 Bundle (`docs/handover/examples/input_fhir_bundle_hba1c.json`)

* Ingest status: **processed**; actionable: **True**; message id `FHIR-LAB-20261020-000777`
* Alert: case 307, Diabetes screening - HbA1c / fasting glucose, tier **high**, rule `DM-A1C-DIAB`
* Pathway: confirm_then_gp; target Confirmatory test within 4 weeks (CONFIRM) (due 2026-12-02T02:48:19.033506)
* State now: Result issued; human review required: True
* SLA timer on the current step: {"status": "green", "fraction": 0.0, "due_at": "2026-11-05T02:48:19.033506", "escalation_level": 0}
* Sweep at +1 days: {"checked": 1, "reminders": 1, "supervisor": 1, "breaches": 1}; escalation levels so far [1, 2]
* Sweep at +40 days: {"checked": 1, "supervisor": 1, "target_breach_alerts": 1}; escalation levels so far [1, 2, 2]
* FHIR export: `case_307_fhir_bundle.json`

## Checks

* FHIR: 50 resources validated (AuditEvent, Communication, Consent, DiagnosticReport, Observation, Organization, Patient, Practitioner, PractitionerRole, Task) - valid: **True**
* Audit chain: {"valid": true, "checked": 5617, "head": "35ed6fed439dfc6d8e23475c2b091296d3ce191ef2959a6ddfa98a191f96908e"}
* Rules engine vs independent reference: 20,000 randomised results (seed 20262020): agree 20,000, safety-critical disagreements **0** (95 % upper bound 1.5e-04)
