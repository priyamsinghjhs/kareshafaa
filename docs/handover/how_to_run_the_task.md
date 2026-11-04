# 3. How to run the task: input / output agreement

The task: **an abnormal screening result goes in; a tracked, owned, timed case comes out, and stays visible until
the loop is closed with evidence.** All examples below use synthetic data.

## Inputs

| Channel | Endpoint | Permission | Example file |
|---|---|---|---|
| HL7 v2.5.1 ORU^R01 (pipe-delimited, `\r` segment separator, UTF-8, ≤ size limit) | `POST /api/ingest/hl7v2` | `case:create` | `examples/input_oru_r01_fit_positive.hl7` |
| FHIR R4 Bundle (JSON; `message` or `collection`) | `POST /api/ingest/fhir` | `case:create` | `examples/input_fhir_bundle_hba1c.json` |
| FHIR R4 history Bundle (Encounter, Medication*, AllergyIntolerance, Condition) for an existing patient | `POST /api/ingest/fhir-history` | `record:write` | – |
| Manual entry by a coordinator | `POST /api/results` | `case:create` | – |

### HL7 v2 fields read (`kareshafaa/services/ingest.py: parse_hl7v2`)

| Segment-field | Use |
|---|---|
| MSH-1/MSH-2 | Delimiters (read from the message, never assumed) |
| MSH-9 | Must be `ORU^R01` |
| MSH-10 | Message control id – idempotency key, echoed in the ACK |
| PID-3 (all repetitions) | Patient identifiers (first = primary; others = alternates). Matching is on identifiers only |
| PID-5, PID-7, PID-8, PID-13 | Name, date of birth, sex, phone – a birth-year mismatch sends the message to human match review; demographics only pre-fill a new registration, never overwrite |
| OBR / ORC-12 / OBR-16, ORC-21 / PV1-3 | Specimen time, ordering clinician, screening site |
| OBX-2, OBX-3, OBX-5, OBX-6, OBX-11 | Value type, LOINC code, value (incl. `SN` comparators), unit, result status (P/F/C) |

### FHIR fields read (`parse_fhir_bundle`)

`Bundle.identifier.value` (idempotency key) · `Patient.identifier[].value`, `Patient.birthDate` ·
`Observation.code.coding[].code` (LOINC), `Observation.valueQuantity.value/unit` or `valueString` /
`valueCodeableConcept.text`, `Observation.status` · `ServiceRequest.requester` / `locationReference`.

### Recognised LOINC codes

| Code | Programme / test |
|---|---|
| 29771-3 | Bowel (colorectal) – FIT |
| 77379-6 / 10524-7 | Cervical – HPV / cytology |
| 24606-6 | Breast – mammography (BI-RADS) |
| 4548-4, 59261-8 | Diabetes – HbA1c (%, mmol/mol) |
| 1558-6, 14771-0 | Diabetes – fasting plasma glucose |

Unknown codes, malformed values, unit mismatches, preliminary results and identity doubts are **never dropped**:
they are kept and routed to a human review queue (fail-safe; see `docs/clinical_safety_case.md`).

## Outputs

| Output | Where | Notes |
|---|---|---|
| **ACK** (HL7) | response `ack` | `MSA|AA` accepted, `MSA|AE` kept for review (with the reason) |
| **Ingest status** | response `status` | `processed`, `resolved` (after human review), `needs_match_review`, `needs_review` |
| **Alert decision / case** | response `case`, `GET /api/cases/{id}` | rule id, programme, **tier** (T0 critical, T1 urgent, T2 high, T3 routine), pathway, pathway target date, human-review flag and reasons, "why this priority" explanation |
| **SLA timers** | case `steps[]`, `sla` | Each state has its own clock per tier (`kareshafaa/sla.py`, e.g. *issued*: 1 / 4 / 24 / 48 h for T0–T3; illustrative defaults, CONFIRM). T2/T3 count UAE working days |
| **Escalation alerts** | `escalations` table, queue, lead view | L1 reminder at 50 % of the step window; L2 supervisor at breach; L3 clinical lead at 1.5×; L4 director at 2× (T0/T1 only); one consolidated alert per level when the pathway target is missed (`kareshafaa/alert_policy.py`) |
| **FHIR R4 resources** | `GET /api/cases/{id}/fhir` (Bundle `collection`) | Patient, Organization, Practitioner(Role), DiagnosticReport, Observation, Task (case and steps), Communication, Consent, AuditEvent; capability at `/fhir/metadata`. Validated in tests with `fhir.resources` |
| **Audit** | `GET /api/audit`, `GET /api/audit/verify` | Every request and action; hash chain verified end to end |
| **Reports** | `GET /api/analytics/impact` (JSON/CSV) | Loop closure, time to specialist by tier, lost to follow-up, recall uptake, equity, "why loops fail" barriers – small cells suppressed |

## Worked example (produced by `scripts/handover_example.sh`)

* Inputs: `examples/input_oru_r01_fit_positive.hl7`, `examples/input_fhir_bundle_hba1c.json` (the script points
  the sample MRNs at two freshly seeded synthetic patients).
* Run record: `examples/example_run_record.md` (human-readable) and `examples/example_run_record.json`.
* FHIR output of one case: `examples/example_output_fhir_bundle_case.json`.

In that run: FIT 250 µg/g → rule `CRC-FIT-POS`, tier high, colonoscopy pathway with a 31-day target, ACK
`MSA|AA`; HbA1c 8.1 % → rule `DM-A1C-DIAB`, tier high, confirm-then-GP pathway. The sweep at +1 day raised L1 and
L2 (step breached); at +40 days a consolidated pathway-target alert. 25 FHIR resources validated; audit chain
valid; 20,000 rule evaluations agreed with the independent reference (0 safety-critical disagreements).
