# Wasl (وصل) – Closing the Screening-to-Care Loop

> **Prototype on synthetic data only.** Not a medical device and not validated for clinical use. No real person,
> result or message exists in this system. Every patient message is **simulated and never sent**. Every KPI on the
> dashboard that compares "baseline vs with Wasl" comes from a **simulation** with assumed parameters. It is not real-world
> evidence.

Wasl turns every actionable screening result into a **tracked case** that carries it to care, across four programmes:
bowel (FIT), cervical (HPV), breast (mammogram) and diabetes (HbA1c). Built for Future Health Digital Innovation
Challenge 05 and aligned with `../Challenge05_Solution_Design.md`.

* **Exactly one accountable owner** for every open case at all times, enforced in the DB and the service layer.
* **Deterministic state machine** with SLA clocks per state and tier (T0–T3), a UAE working-day calendar and an
  **L1–L4 escalation ladder**. Covers no-shows, rejected referrals, unreachable patients, transfers and declines.
* **Closure guard**: a case closes only with evidence (care plan, procedure or negative confirmation) or an
  authorised human decision with a reason. Enforced in code and tested.
* **Patient communication** in English, Arabic (RTL), Hindi and Urdu, with result-specific wording, a teach-back
  check and a channel fallback ladder (app → SMS → WhatsApp → navigator phone call; order is governed config per language). Tagalog and Malayalam
  speakers get English plus an interpreter-supported call, and a notice in their own language says so.
* **Clinically grounded rules engine** (`wasl/clinical_rules.py`, documented with sources in `docs/clinical_rules.md`):
  FIT, HPV/cytology (ASCCP), BI-RADS (ACR) and HbA1c/FPG (ADA, % and mmol/mol). It is deterministic with no ML. Anything
  unknown, malformed, out of range, conflicting, amended, duplicated, out of age range, pregnant, deceased, under specialist
  care or arriving after closure goes to a **clinical review queue** at a safe minimum tier with a reason. It is never
  dropped or downgraded. The ML can only reorder cases within a tier. An ORM interlock blocks any tier downgrade.
  Safety case: `docs/clinical_safety_case.md`. Synthetic data calibration: `docs/synthetic_data.md`.
* **Interoperability**: HL7 v2 ORU^R01 and FHIR R4 Bundle ingest (idempotent, ACK AA/AE, human match review) and FHIR R4
  export per case.
* **Governed ML** (scikit-learn) used **only** for outreach priority and queue order. A model registry with AUC,
  calibration and fairness gates. Promotion needs a better candidate **and** clinical-lead approval. Drift monitoring,
  rollback and model cards. A lessons-learned engine proposes rule changes that a human must approve.
* **Security**: RBAC with 7 roles, consent checks, an **append-only, hash-chained audit log** of every read and write,
  and suppression of analyst cells below 5.

## Quickstart

Requires Python 3.11+ (tested on 3.13) and `make`. Everything is free and open source.

```bash
make setup          # venv + dependencies (~1 min)
make seed           # 2,000 synthetic patients, 6 months of history, train + register models (~55 s)
make run            # http://localhost:8000  (python -m wasl.serve: 4 SO_REUSEPORT worker processes; WORKERS=1 for one)
make test           # 3,082 pytest tests (~60 s; the 20k cohort test is skipped until `make seed-large`)
```

Clinical-safety targets (Iteration 6):

```bash
make seed-large      # 20,000 realistic synthetic people -> data/wasl_large.db (~3 min, no model training)
make validate        # 51 data invariants on the demo DB (exit 1 on any violation)
make validate-large  # same on the 20k cohort
make test-clinical   # golden cases, fail-safe, Hypothesis properties, exhaustive state machine
make test-cohort     # full-cohort run on the 20k DB (needs seed-large)
make fault-injection # 42 deliberate faults; writes docs/fault_injection_results.md
make differential    # 2M randomised results: rules engine vs independent reference (docs/differential_results.md)
```

Open http://localhost:8000. To start, click **“Open demo scenarios”** or pick a demo user in the role switcher.
Demo logins have no password and can be turned off with `WASL_DEMO_LOGINS=false`.

| Demo user | Role | Try |
|---|---|---|
| `aisha.rahman` | Care coordinator | Work queue, outreach, calls, demo scenarios, results inbox (HL7) |
| `omar.haddad` | GP | Confirmatory tests, referrals, e-consults; try to close a case without evidence |
| `vikram.mehta` | Specialist | Accept or reject referrals with a reason; booking, attendance, care plan |
| `fatima.al.mansoori`, `layla.al.hashimi` | Clinical lead / medical director (L4) | Escalations, model and rule approvals, KPIs |
| `analyst` | Analyst | Aggregates only, small cells suppressed |
| `admin` | Admin | Audit log and chain verification, model training |
| `patient.ar`, `.en`, `.hi`, `.ur`, `.tl` | Named demo patients | Fatima Al Kaabi (FIT, Al Ain), Emma Clarke (BI-RADS 0), Ramesh Kumar (HbA1c), Ayesha Khan (HPV), Maria Santos (BI-RADS 4, Tagalog) |

**One-click scenarios** (`/demo`, coordinator, clinical lead or admin, demo mode only). Each one creates a new synthetic
patient and runs through the real service layer, guards and audit:
1. Fatima: result arrives as HL7 ORU → Arabic teach-back → GP referral → hospital rejects (capacity) → re-route.
2. Maria: T1, no smartphone, digital ladder fails → navigator task → the demo clock fast-forwards → L1→L2→L3→L4; closure is blocked.
3. Ramesh: confirmed HbA1c → two-key transfer to another GP.
4. Unreachable-patient ladder. 5. Ramadan no-show with the `ramadan_hours` barrier.

**HL7 sample:** `samples/oru_r01_fit_positive.hl7`. The results inbox (`/ingest`) also has a *Load sample* button
that builds an ORU for a real synthetic patient, because the deterministic matcher correctly refuses a birth-year
mismatch.

Configuration: environment variables (prefix `WASL_`) override `.env`; see `.env.example`.

Docker: `docker build -t wasl . && docker run -p 8000:8000 wasl`. It runs as a non-root user and seeds on first start.

## Architecture

```
Browser (Jinja + vanilla JS + Pico.css; RTL aware)          HL7 v2 / FHIR R4 senders
        │  bearer token or HttpOnly cookie                          │
FastAPI ─┬─ api/        auth, cases, actions, queue/ops, me (patient), governance, ingest, demo
         ├─ services/   workflow (state machine + guards), escalation (SLA sweep), queue, ingest,
         │              analytics (+cache, suppression), improve (lessons → proposals), access (RBAC scope)
         ├─ comms/      templates en/ar/hi/ur, teach-back, channel ladder (SIMULATED only)
         ├─ ml/         features, registry (gates, approval, drift, rollback, model cards)
         ├─ synth/      cohort (UAE mix), provider directory, history replay, baseline-vs-Wasl simulator
         ├─ audit.py    append-only hash chain (DB triggers + ORM guard + writer lock)
         └─ fhir.py     FHIR R4 export (Patient, DiagnosticReport, Task, ServiceRequest, Appointment, CarePlan…)
SQLAlchemy 2 → SQLite (WAL, default) or PostgreSQL (WASL_DATABASE_URL; add `psycopg[binary]`)
Background: one elected worker runs the SLA/escalation sweep every WASL_SWEEP_INTERVAL_SECONDS (default 300)
```

**Data model (FHIR-shaped):** Patient, Organization, Practitioner, DiagnosticReport, Case (≈ EpisodeOfCare), StepTask
(≈ Task with SLA), ServiceRequest, Appointment, CarePlan, Communication, QuestionnaireResponse (teach-back), Consent,
ConsultRequest, Escalation, AuditEvent, ModelVersion, Lesson / Proposal, IngestMessage.

**Tiers and SLAs** (`wasl/sla.py`, overridable by JSON config). T0 Critical (HbA1c ≥ 15 %), T1 Urgent (BI-RADS 4/5,
FIT ≥ 400), T2 Soon (e.g. FIT+, HPV16/18, BI-RADS 0, HbA1c ≥ 10 %) and T3 Routine. T0/T1 clocks run 24/7 and need clinician-confirmed triage. T2/T3
clocks count UAE working days (Mon–Fri, public holidays, Ramadan windows; Islamic dates approximate).
**Ladder:** L1 owner nudge plus patient reminder at 50 % of the SLA, L2 programme coordinator at breach, L3 clinical
lead at 1.5× (T0 goes straight to L3), L4 medical director at 2× (T0/T1 only). Inactive owners are auto-pooled.

**Pathways** (`wasl/programmes.py`):
* FIT+ goes straight to colonoscopy.
* HPV16/18 goes to direct colposcopy; other high-risk HPV gets reflex cytology first.
* BI-RADS 0 gets diagnostic recall; BI-RADS 4/5 goes to direct biopsy.
* HbA1c gets a repeat HbA1c or FPG, then a GP visit and care plan.

## Performance (10,005 cases, 1,509 open; 160k audit events; 10 concurrent clients)

| Endpoint | p50 / p95 before (iteration 3) | iteration 4 (4 workers) | iteration 6 (rules engine, review gate; warm) | iteration 7 (wasl.serve, row-version locks) |
|---|---|---|---|---|
| Work queue, page of 50 (coordinator) | 4,297 / 4,527 ms | **29 / 448 ms** | 28 / 258 ms | 62 / 154 ms |
| Work queue, GP (org-scoped) | 14,553 / 15,109 ms | **32 / 599 ms** | 32 / 304 ms | 77 / 163 ms |
| Case detail | 153 / 200 ms | **51 / 88 ms** | 39 / 78 ms | 25 / 58 ms |
| Case FHIR export | 110 / 181 ms | **43 / 79 ms** | 35 / 126 ms | 27 / 52 ms |
| KPIs (cached) | 32,770 / 33,795 ms | **3.5 / 29 ms** | 7.7 / 14 ms | 9.8 / 18 ms |
| HL7 ingest (write) | – | **77 / 182 ms** | 73 / 195 ms | 79 / 163 ms |

Iteration 6 run: a fresh 10k DB (10,155 cases, 1,250 open, 160k audit events). Detail, FHIR and KPI figures come from the first, cold pass; the cold-pass queue p50 was 145 ms. Audit chain verified intact after the load test. Reproduce with `scripts/loadtest.py` (see ITERATIONS.md, iteration 4).
Background sweep over 1,705 open clocks: 35–76 ms.
Iteration 7 run: fresh 10k DB (1,215 open cases, 155k audit events), 10 concurrent clients, 0 errors, audit chain intact. In an earlier Iteration 7 run the queue p95 had regressed to 1.3 s (sqlite per-row GIL convoy); the fixes are in ITERATIONS.md (round 8).

## Simulated KPIs (SIMULATED – 20,000 synthetic people × 5 replications, same rules engine)

| KPI | Baseline (no Wasl) | With Wasl |
|---|---|---|
| Median days result → patient notified | 8.0 | 1.3 |
| Median days result → care | 48.3 | 24.6 |
| Patient understood result (teach-back) | 44.1 % | 95.0 % |
| Care reached or resolved | 34.8 % | 82.3 % |
| Documented closure | 20.4 % | 85.1 % |
| FIT-positive reaching colonoscopy | 29.3 % (Abu Dhabi 2015–16 reference: 30.5 %) | 88.0 % |
| Median days FIT+ → colonoscopy | 72.9 (reference 72) | 28.5 |
| Review-queue results actioned | 80.2 % | 100 % |

These numbers come from model assumptions (`docs/synthetic_data.md`, `docs/simulation_20k_results.json`). They are not evidence of real-world effect.

## Governance – alignment, not certification

Wasl is designed to **align with** the frameworks below. It is **not certified** against any of them, and a real
deployment would need formal assessment.

| Framework | How the prototype aligns |
|---|---|
| **WHO** guidance on ethics & governance of AI for health; WHO screening principles | Human oversight; AI never makes clinical decisions; equity monitoring by language, region and nationality; transparency through model cards |
| **NIST AI RMF 1.0** (Govern / Map / Measure / Manage) | Intended and prohibited uses on the model card (Map); AUC, calibration and fairness gates with CIs plus drift (Measure); human-approved promotion, rollback and audit (Manage); roles and approvals (Govern) |
| **ISO/IEC 42001** (AI management system) | Lifecycle records: registry, gate results, approvals with notes, change proposals with human decision, audit trail |
| **UAE data protection**: Federal Decree-Law 45/2021 (PDPL), Federal Law 2/2019 (ICT in health fields, data localisation), Abu Dhabi DoH healthcare-data and AI policies | Consent checks before outreach; least-privilege RBAC with org scoping; full read/write audit; aggregate-only analytics with suppression; synthetic data only; designed for in-country hosting (no external calls) |

**Safety invariants enforced in code and tests:**
* The ML score is never read by the state machine, triage or closure.
* SLA status and tier always sort ahead of the score.
* A model cannot be promoted without passing the gates and getting a clinical lead's approval; rollback needs a previous version.
* Rule and template changes from the lessons loop only apply after human approval.
* A case cannot close without evidence or an authorised decision with a reason.
* The audit log is append-only (DB triggers) and tamper-evident (hash chain; `GET /api/audit/verify`).

## Assumptions

* Population mix, channel availability, no-show and unreachable rates, Ramadan and holiday effects are **illustrative
  assumptions**, not official statistics. They are documented in `wasl/synth/cohort.py` and `history.py`.
* Thresholds, pathways and targets follow the published guidelines cited in `docs/clinical_rules.md`. Items marked
  **clinical lead to confirm** default to the safer option. **Programme clinical leads must sign off every rule.**
* All patient-facing text is prototype wording. It needs clinical review and professional translation and patient testing.
* Organisations and practitioners are fictional. HL7/FHIR profiles are simplified (no national profile, no MPI).

## Known limitations

* Synthetic data only. The ML model (AUC ≈ 0.66 on the 2k demo, 0.72 on the 20k cohort) and the simulator say nothing about real-world performance.
* Fairness gates use bootstrap CIs on a ~445-case holdout, so small groups (e.g. hi, ur, tl, n < 50 positives) are
  *not eligible* for gating and are only monitored. A real deployment needs larger validation sets.
* Channels are simulated. There is no SMS, WhatsApp or email integration, and no real telephony.
* Authentication is demo-grade: HMAC tokens with role-switcher logins. Production needs OIDC/SSO (UAE PASS for patients), MFA and secret management.
* The queue still orders all open cases in Python (SLA status depends on the working-day calendar). It is fine at 10k
  cases with ~1.5k open, but beyond that it should use a materialised due/RAG column with SQL ORDER BY/LIMIT.
* SQLite is for the demo. Multi-host deployment needs PostgreSQL; the audit writer lock already uses `pg_advisory_xact_lock` there.
* The Islamic holiday and Ramadan dates are approximate. Tagalog and Malayalam content is not translated yet.
* Docker build is written but could not be tested on the build box (no Docker daemon available).

## Iteration 7 evidence (docs/)

* `alert_precision.md`: consolidated alert policy. On the 20k cohort, precision is 100 % and recall is 100 % with 0 missed
  true breaches; the legacy policy scored 77.8 % / 89.8 %.
* `ml_calibration.md`: discrimination, ECE, slope, CITL and reliability bins with Wilson CIs, plus the fairness gates.
* `patient_messages.md`: reading-level and tone checks, and the translator plus clinician sign-off for ar/hi/ur.
* `human_signoff.md`: 19 high-impact actions that need a named human, the permission and a rationale.
* `differential_results.md`: 10M results checked against the independent reference implementation.
* `fault_injection_results.md`: the deliberately injected faults and which tests caught them.
* `clinical_safety_case.md`: hazards H-01 to H-30.

## Repository map

`wasl/` app · `tests/` pytest (3,082) · `wasl_reference/` independent reference rules · `docs/` clinical rules, safety case, synthetic data, fault injection · `scripts/validate_data.py` · `scripts/fault_injection.py` · `samples/` HL7 and FHIR examples · `scripts/loadtest.py` · `scripts/profile_hotpaths.py` ·
`scripts/ui_smoke.py` (optional Playwright) · `ITERATIONS.md` build log with self-assessments · `Makefile` · `Dockerfile` · `.env.example`
