# 4. Test results

All results are on **synthetic data**. Every figure below is copied from a generated report in `docs/` or from a
run made on 4 Nov 2026 (IST); the source is named next to each table. Nothing here is real-world evidence.

## Automated test suite

| Item | Result | Source |
|---|---|---|
| Test files | 56 (`tests/test_*.py`) | repository |
| Tests run / passed | **3,606 / 3,606** (0 failed) | `pytest -q --cov=kareshafaa`, 4 Nov 2026 |
| Line coverage | **88 %** of `kareshafaa/` (12,783 statements); `statemachine.py` 100 %, `clinical_rules.py` 99 %, `alert_policy.py` 98 %, `services/ingest.py` 95 %, `fhir.py` 93 %, `services/escalation.py` 92 % | same run |
| FHIR conformance | Exported case Bundles and every entry validated against FHIR R4B models (`fhir.resources` 8.3) plus required status value sets; CapabilityStatement valid; sample input Bundle valid | `tests/test_fhir_conformance.py` |
| HL7 v2 | ORU^R01 parse (MSH/PID/OBR/OBX), ACK echoes the control id, ingest refused without permission, adversarial and malformed messages | `tests/test_fhir_conformance.py`, `test_ingest*.py` |
| Translation completeness | 22 patient-facing text tables: same keys, no empty strings, same placeholders in en/ar/hi/ur | `tests/test_translation_completeness.py` |
| Accessibility | axe-core 4.14 WCAG 2.1 A/AA on 28 page views (staff roles, patient app in en/ar/hi/ur) + keyboard focus check: **0 problems** (26 before round 2) | `scripts/a11y_check.py`, `scripts/ci_a11y.sh` |
| Security scans | `pip-audit`: no known vulnerabilities; `bandit`: 0 high, 0 medium after fixes (48 low reviewed) | `docs/security.md` |
| Example run from a fresh clone | `scripts/handover_example.sh` passes from `git clone` in a temp dir | see `examples/example_run_record.md` |

## Baseline comparisons

### Rules engine vs an independent reference implementation

`docs/differential_results.md`: 10,000,000 randomised results (boundary-weighted, all four programmes, malformed
input, units) – **0 safety-critical disagreements** (one-sided 95 % upper bound 3.0 × 10⁻⁷), 0 non-critical.
Re-run by the handover script on 20,000 results with seed 20262020: 0 disagreements.

### Alerting: earlier policy vs current policy

`docs/alert_precision.md` (5,930-case cohort, truth labelled independently of the alert code):

| Metric | Earlier policy (Iteration 6) | Current policy |
|---|---|---|
| True breaches detected within one sweep | 2,803 of 3,120 (recall 89.84 %) | 3,120 of 3,120 (100 %) |
| Alerts L2+ / false positives | 6,115 / 1,355 (precision 77.84 %) | 5,104 / 0 (100 %) |
| Escalations reaching the expected level | 476 of 841 | 841 of 841 |

### No system (manual follow-up) vs KareShafaa – simulation

`docs/simulation_20k_results.json`: discrete-event simulation, 20,000 synthetic patients, 5 replications, seed
20261120, 120-day horizon. Values are the mean with the range across replications.

> **Read this carefully.** The "with KareShafaa" arm uses *assumed* parameters (for example notification median
> 1 day vs 6, no-show 11 % vs 18 %, closure documented 97 % vs 55 %; full list in the JSON `assumptions`).
> The difference between the arms is therefore an **assumption carried through a model, not a measured effect**.
> It shows what the assumptions imply end to end; the pilot must measure the real effect.

| KPI | No system (baseline) | With KareShafaa |
|---|---|---|
| Median days to patient notified | 8.0 (7.9–8.1) | 1.3 (1.3–1.3) |
| Median days to patient understood | 12.1 (12.0–12.1) | 4.9 (4.9–4.9) |
| Median days to care reached | 48.3 (47.7–48.7) | 24.6 (24.4–24.7) |
| % notified | 82.3 (81.9–82.6) | 98.5 (98.4–98.5) |
| % understood (teach-back) | 44.1 (43.9–44.3) | 95.0 (94.8–95.2) |
| % appointment attended | 73.2 (72.8–73.7) | 95.9 (95.8–96.3) |
| % care reached or resolved | 34.8 (34.6–35.2) | 82.3 (81.7–82.8) |
| % loop closed with documentation | 20.4 (20.3–20.6) | 85.1 (84.6–85.4) |
| % still open/unresolved at 120 days | 79.6 (79.4–79.7) | 14.9 (14.6–15.4) |
| % FIT-positive reaching colonoscopy | 29.3 (27.9–30.2) | 88.0 (87.2–89.0) |
| Median days FIT-positive to colonoscopy | 72.9 (70.5–77.1) | 28.5 (28.2–28.8) |
| % review-flagged results actioned | 80.2 (79.4–80.8) | 100.0 (100.0–100.0) |

### ML outreach-priority model

`docs/ml_calibration.md` (temporal hold-out, 461 cases): logistic regression AUC **0.684**, ECE 0.036, calibration
slope 1.007, 10/10 reliability bins within the 95 % CI, no fairness gate failed. The model only orders cases
*within* an urgency tier; it never sets a tier or a decision. AUC 0.68 is modest – it is a work-ordering aid.

### Robustness

| Check | Result | Source |
|---|---|---|
| Edge-case cohort | 20,000 planned people, stratified by programme × severity × scenario (missing/unknown values, unit mismatch, amended, duplicate identity, death mid-pathway, consent withdrawn, Ramadan, malformed HL7 …); every realised cell recorded, no result lost; 0 coverage gaps listed | `docs/edge_case_coverage.md` |
| Fault injection | 54 deliberate faults in thresholds, boundaries and workflow – **54 caught** (100 %) | `docs/fault_injection_results.md` |
| Mutation testing | `clinical_rules.py` + `statemachine.py`: 1,560 mutants, **91.0 % killed**; the 12 real test gaps found were closed; the rest classified by hand | `docs/mutation_testing.md` |
| Load | Locust, 100 users, 4 workers: 3,502 requests, p95 69 ms, 1 failure | `docs/loadtest_locust.md` |

## Errors found and fixed (round 2)

* N+1 queries: recalls list 206 → 7 SQL statements; patient search 66 → 8; case detail 51 → 33 (Postgres 16).
* Accessibility: account menu accessible name did not match its visible text (every signed-in page); date
  inputs without a visible focus ring; one scrollable table not reachable by keyboard.
* Security: no CSP / HSTS / Permissions-Policy; bandit high (SHA-1 flag) and medium (f-string SQL in bootstrap).
* Clean-clone setup: the handover example needed `httpx` installed explicitly.

## Known limits (honest)

* All data synthetic; no real-world outcome, accuracy or time-saving has been measured.
* Simulation gains come from assumed parameters (see above).
* Clinical thresholds and SLA hours are illustrative defaults marked **CONFIRM** until clinical sign-off
  (`docs/clinical_rules.md`, `kareshafaa/sla.py`).
* FHIR validated against base R4B models, not a UAE/Malaffi implementation guide; required value sets checked only
  for status fields.
* `style-src 'unsafe-inline'` remains in the CSP (inline `style=""` attributes). Scripts are `'self'` only since the hardening round (no inline scripts or handlers; `tests/test_csp.py`).
* Patient messages are simulated (no SMS/WhatsApp gateway); no MFA or SSO.
* Free hosting is outside the UAE and sleeps when idle (cold start); only synthetic data is ever hosted there.
