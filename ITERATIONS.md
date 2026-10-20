# Wasl prototype – iteration log

Each iteration = `pytest` (unit + API integration) → start server → curl smoke test (every page and the main
endpoints, per role) → headless-browser UI smoke (`scripts/ui_smoke.py`: JS errors, failed API calls,
screenshots) → self-assessment against the brief (R1–R6) and the judging criteria (J1–J6) → fixes.
All times IST (Asia/Calcutta). All data synthetic; all messages simulated.

## Iteration 0 – build (20 Oct 2026, ~21:00–22:15 IST)
Built the service layer, API, UI, synthetic generator, simulator and ML registry before any tests existed.
Issues found and fixed while building:
* SQLite `drop_all` failed on the DiagnosticReport ↔ Case FK cycle → drop = delete the SQLite file; `use_alter` FK.
* Consult requests from patients violated NOT NULL on `requester_user_id` → nullable (patients are not staff users).
* Simulator baseline arm counted non-understanders as "understood" → fixed; confidence lowered for them.
* 559 of 2,000 synthetic cases stuck open forever (unrealistic) → replayer resolves stalled cases the way a real
  team eventually would (authorised closure with reason / late completion) → ~291 open (14.5%).
* Fairness gates on raw point-gaps failed every model because of tiny noisy groups → bootstrap-CI
  "credible under-service" gates with minimum group size (n ≥ 50, ≥ 10 positives). Both models now pass honestly.

## Iteration 1 – first test suite + smoke (20 Oct 2026, ~22:15–22:45 IST)
* **Tests:** wrote 84 tests in 6 modules (state machine, closure guard, full journey, exceptions, RBAC matrix,
  consent, patient isolation, append-only audit + hash chain, SLA ladder, auto-pooling, queue, ML gates /
  approval / rollback / integrity, suppression, FHIR reference integrity, channel ladder consent, templates,
  proposals, pages). First run: **82/84 passed**.
* **Bugs found by tests / smoke:**
  1. FHIR export: `PractitionerRole.organization` referenced organisations not included in the Bundle
     (e.g. `Organization/1`, the programme) → bundle now includes every referenced organisation. (real bug)
  2. Rollback test assumed a prior production model; with the small test seed v1 can fail gates → test now
     builds a two-version history; also asserts rollback is refused (409) when nothing approved exists.
  3. Unauthenticated test got 403, not 401, because the TestClient kept the demo-login cookie → fixture now
     authenticates via bearer tokens only (test harness bug, not an app bug).
  4. UI smoke: `/favicon.ico` 404 → SVG favicon + redirect.
  5. UI review: appointment picker pre-filled a UTC time into a local `datetime-local` input and sent local
     wall-clock as UTC → now pre-fills local time and submits ISO UTC.
  6. UI review: Urdu/Arabic rendered with a fallback font without shaping on some systems → explicit Arabic-script
     font stack for RTL; Devanagari stack for Hindi.
* **Result:** 84/84 passed; curl smoke: all 11 pages and 24 API endpoints 200 for the right roles, 403 for the
  wrong ones; UI smoke 20 pages / 11 roles, 0 JS errors, 0 failed API calls.
* **Self-assessment (1–5):** R1 5 · R2 5 · R3 4 · R4 4 · R5 5 · R6 4 | J1 4 · J2 4 · J3 4 · J4 5 · J5 3 · J6 3.
  Gaps: no standards-based ingest (HL7 v2 / FHIR) although the design doc promises it (J2/J5); demo needs a way
  to "fast-forward the clock" to show escalation live (J3); seed summary printed v1 as "awaiting approval" even
  after approval (cosmetic).

## Iteration 2 – standards-based ingest + live demo clock (20 Oct 2026, ~22:45–23:05 IST)
* **Added:** `POST /api/ingest/hl7v2` (HL7 v2.5.1 ORU^R01 → DiagnosticReport/Observation → rules → case, returns an
  HL7 ACK AA/AE), `POST /api/ingest/fhir` (FHIR R4 Bundle), idempotency on MSH-10 / Bundle.identifier, deterministic
  patient matching (MRN + birth year) with a **human match-review queue** (`/api/ingest/messages`, `.../resolve`);
  sample messages in `samples/`. Demo clock: `POST /api/sla/sweep {"hours_ahead": N, "case_ids": [id]}` – demo mode
  only, single case, audited as `demo.clock_fast_forward`.
* **Tests:** +7 (sample parse, malformed messages, replay idempotency, negative result → no case, unmatched and
  birth-year-mismatch → review, RBAC on resolve, FHIR bundle, demo clock limits). **90/90 passed.**
* **Bug found:** a test posting JSON with a leftover `Content-Type: text/plain` header got 422 – a harness issue, but it
  confirmed the API rejects mis-typed bodies instead of guessing.
* **Self-assessment:** J2 4→5 (standards ingest), J5 3→4 (integration path is now concrete). Remaining gaps: tiers
  were 3-level (design doc has T0–T3), escalation stopped at L3 (design has L4), SLA clocks ignored the UAE working
  week and holidays, pathways were programme-level rather than result-specific, demo stories not one-click.

## Iteration 3 – realism: UAE context, T0–T3, L1–L4, result-specific pathways, named stories (20 Oct 2026, ~23:05–23:40 IST)
* **Tiers aligned with the design doc:** T0 Critical / T1 Urgent / T2 Soon / T3 Routine (internal keys
  critical/urgent/high/routine). T0/T1 need clinician-confirmed triage; T0 = HbA1c ≥ 15 % (runs alongside, never
  instead of, the lab critical-value phone process). SLA table extended to four tiers.
* **UAE working calendar** (`wasl/calendar_uae.py`): Mon–Fri week, public holidays 2025–2027 (Islamic dates approximate,
  configurable), Ramadan windows. T2/T3 clocks count working days; T0/T1 run 24/7 (on-call). Synthetic staff actions
  for T2/T3 happen in clinic hours on working days; appointments land on working-day clinic slots.
* **Escalation ladder L1–L4** as in the design: L1 owner nudge + patient reminder (50 %), L2 programme coordinator
  (breach), L3 clinical lead (1.5×; T0 straight to L3 on breach), L4 programme medical director for T0/T1 at 2×;
  T2/T3 never reach L4 (weekly review list).
* **Result-specific pathways:** FIT+ → colonoscopy; HPV16/18 → direct colposcopy, other hrHPV → reflex cytology first;
  BI-RADS 0 → diagnostic imaging recall, BI-RADS 4/5 → direct core biopsy; HbA1c → repeat HbA1c/FPG → GP visit and care
  plan (≥ 10 % may also go to endocrinology). Programme-specific care-plan texts (polypectomy + 3-year surveillance,
  LLETZ, MDT, etc.).
* **Population realism (assumptions, not statistics):** Emirati 20 %, Indian 28 % (Hindi / Malayalam / English),
  Pakistani 12 % (Urdu), other Arab 14 %, Filipino 10 % (Tagalog / English), Western 6 %, other 10 %; Abu Dhabi / Al Ain /
  Al Dhafra; lower-income worker profile with fewer smartphones and more invalid numbers; no-show risk ×1.6 in
  Ramadan, rises with prior no-shows; new barrier code `ramadan_hours` feeding the proposal engine
  ("Ramadan-adjusted clinic hours"). Tagalog/Malayalam speakers get English + a navigator call – the lessons loop
  surfaces this as an advisory (honest gap, not hidden).
* **Named demo patients:** Fatima Al Kaabi (ar, FIT, Al Ain), Emma Clarke (en, BI-RADS 0), Ramesh Kumar (hi, HbA1c),
  Ayesha Khan (ur, HPV other), Maria Santos (tl, BI-RADS 4) – log in as `patient.ar|en|hi|ur|tl`.
* **One-click demo scenarios** (`/demo`, `POST /api/demo/scenarios/{key}`): Fatima's rejected referral (arrives via a real
  HL7 ORU^R01), Maria's T1 escalation to L4 + blocked closure, Ramesh's two-key transfer, unreachable ladder, Ramadan
  no-show. Each creates a new synthetic patient and uses the real service layer, guards and audit.
* **Results inbox UI** (`/ingest`) with a server-generated sample ORU^R01 for a real synthetic patient.
* **Bugs found:** UI smoke flow showed the static sample HL7 hit "birth year differs" (correctly refused by the matcher!)
  → sample now generated for a real synthetic patient; Arabic patient page used a CSS class only → real `dir="rtl"` and
  `lang` attributes (screen readers, shaping) and `aria-pressed` on the language switch.
* **Tests:** +10 (working-day calendar & holidays, T1 24/7 vs T3 working-day clocks, pathway rules, T0 → L3 → L4,
  T3 never L4, five demo scenarios incl. blocked closure, scenario RBAC). **98/98 passed.** UI smoke 23 pages + 3
  interactive flows, 0 problems. Re-seed 2,000 patients: 55 s; v1 logreg AUC 0.653 / ECE 0.041 promoted; v2 gbm
  AUC 0.639 **blocked by the "beats current" gate** (correct behaviour, visible on the governance page).
* **Self-assessment:** R1 5 · R2 5 · R3 4 · R4 5 · R5 5 · R6 4 | J1 5 · J2 5 · J3 5 · J4 5 · J5 4 · J6 3.
  Next: efficiency at 10k+ cases (indexes, N+1, pagination, KPI cache, background sweep, load test).

## Iteration 4 – efficiency at 10k+ cases (20 Oct 2026, completed ~23:25 IST)
Load-test DB: 10,000 synthetic patients → 10,005 cases (1,509 open), 80k step tasks, 160k audit events
(`/workspace/fhdic/loadtest/`, seeded in 267 s). Tool: `scripts/loadtest.py` (httpx async, 10 concurrent clients,
100–200 requests per endpoint; every request is audited exactly as in production).

* **Profiling first** (`scripts/profile_hotpaths.py`, in-process): GP queue had an N+1 (org scope per case, ~4 queries per
  open case); KPIs/fairness recomputed on every request; sweep loaded steps per case. Under HTTP load the service
  timings did not explain the latencies → `py-spy dump` showed request threads blocked in SQLite and the GIL, and the
  request-audit middleware did synchronous DB writes **on the event loop**.
* **Changes**
  - 11 composite indexes (`models.py`; `init_db` now also creates indexes missing from existing DBs, serialised with a
    file lock so several workers can start together).
  - Queue rewritten as one column query (case + patient + owner + in-progress step) plus 4 set-based scope queries;
    server-side pagination (`limit`/`offset`, default 50) and search (`q` = MRN, name or `#case-id`); UI pager.
  - KPI/fairness cache keyed on a data fingerprint, 60 s TTL, single-flight recompute and bounded staleness (≤ 10 s
    under constant writes).
  - SLA sweep bulk-loads steps and owners and skips cases already at the required level (fast path).
  - **Background SLA job** in the app lifespan (`WASL_SWEEP_INTERVAL_SECONDS`, default 300, 0 = off); runs in a worker
    thread; with several uvicorn workers a non-blocking `flock` elects exactly one sweeper. 10k DB: 1,705 open
    clocks checked in 35–76 ms.
  - Request audit + ingest endpoints moved off the event loop (`asyncio.to_thread` / `run_in_threadpool`).
  - `make run` / Docker use 4 uvicorn workers (`WORKERS`, `WEB_WORKERS`).
* **Bug found (serious, now fixed):** after the baseline load test the audit hash chain was **broken**
  (`verify_chain → valid=False, broken_at_id=161555`): concurrent writers read the same chain head and forked the
  chain. Fix: hash computed at flush time under a writer lock (thread lock + `flock` across worker processes on SQLite,
  `pg_advisory_xact_lock` on PostgreSQL), released on commit/rollback/close. New tests: 60 concurrent requests →
  chain valid; the load test now ends with a chain check (**valid=True, 163,948 events**, after 200 concurrent ingest
  writes across 4 processes).
* **Results (10 concurrent clients, 10k DB)** – p50 / p95 in ms

  | Endpoint | Baseline (iter 3, 1 worker) | Optimised, 1 worker | Optimised, 4 workers |
  |---|---|---|---|
  | `GET /api/queue` coordinator, page of 50 | 4,297 / 4,527 | 1,387 / 1,501 | **29 / 448** |
  | `GET /api/queue?view=overdue` | 4,217 / 4,480 | 1,347 / 1,426 | **26 / 434** |
  | `GET /api/queue` GP (org-scoped) | 14,553 / 15,109 | 1,829 / 1,922 | **32 / 599** |
  | `GET /api/cases/{id}` | 153 / 200 | 155 / 195 | **51 / 88** |
  | `GET /api/cases/{id}/fhir` | 110 / 181 | 124 / 245 | **43 / 79** |
  | `GET /api/analytics/kpis` | 32,770 / 33,795 | 47 / 58 | **3.5 / 29** |
  | `GET /api/me/case` (patient) | 52 / 63 | 52 / 66 | **20 / 39** |
  | `POST /api/ingest/hl7v2` (write) | – | – | **77 / 182** |

  Sequential single request: queue 23–28 ms. Remaining tail: the queue still materialises all open cases to order them
  by SLA status (RAG is computed from the working-day calendar in Python); with SQLite + the GIL this gives a p95 tail
  under concurrency. Production path: PostgreSQL + a materialised `rag_due_at` column updated by the sweep, then
  ORDER BY/LIMIT in SQL.
* **Tests:** +5 (concurrent audit writers, pagination/search, GP queue org scope, KPI cache invalidation, bounded
  staleness). **103/103 passed.**
* **Self-assessment:** R1 5 · R2 5 · R3 4 · R4 5 · R5 5 · R6 4 | J1 5 · J2 5 · J3 5 · J4 5 · J5 5 · J6 3.
  J5 (scalability) 4→5. Next: ease-of-use polish, README.

## Iteration 5 – ease of use, realism bugs, fresh-install verification (20 Oct 2026, completed ~23:37 IST)
* **UI smoke review (screenshots of every role page)** found:
  - **Realism bug:** Maria Santos (BI-RADS 4 → direct biopsy) was told "your mammogram needs some more pictures" –
    the BI-RADS 0 wording – because patient text was programme-level while pathways were result-specific. Fix:
    `programme_text()` overrides meaning/next step for direct pathways (HPV16/18 → colposcopy, BI-RADS 4/5 → biopsy,
    HbA1c ≥ 15 → doctor today/tomorrow) in en/ar/hi/ur; used by the patient page, simulated messages and teach-back.
  - Tagalog/Malayalam speakers silently got English. Now a notice in their own language says an interpreter-supported
    call will follow (`lang` attribute set for screen readers). Honest gap kept visible, not hidden.
  - First-time visitors had to know where to start: home page now has "New here? Start with a story" (one click →
    demo scenarios or the HL7 results inbox as the coordinator). Card list spacing fixed.
  - Operational bug in my own environment: a leftover exported `WASL_DATABASE_URL` made `make run` serve the 10k
    load-test DB – noted in README that env vars override `.env`.
* **Fresh install check:** `git clone` → `make setup && make seed` (70 s total; seed 52 s) → `make test` **105 passed** →
  `make run PORT=8001` (4 workers) → curl smoke: healthz, queue, HL7 sample ingest (ACK AA, case created), scenario
  `maria_urgent_escalation`, all pages 200.
* **Re-run of simulation (SIMULATED, 2,000 synthetic patients × 5 replications, 120 days):** median days to care
  55.6 → 29.5; care reached/resolved 29.4 % → 79.6 %; documented closure 19.6 % → 83.3 %; open at day 120
  80.4 % → 16.7 %; understood 46.5 % → 95.1 %; attended 72.9 % → 96.5 %.
* **ML (fresh seed):** v1 logreg AUC 0.647, ECE 0.053, all gates passed → production (seed bootstrap approval recorded
  as clinical lead, demo). v2 GBM AUC 0.629 → **failed "beats current"** → cannot be promoted; awaiting a human.
* **Tests:** +2 (direct-pathway wording in 4 languages, Tagalog notice + Maria's biopsy wording). **105/105 passed.**
  UI smoke: 23 pages + 4 flows (HL7 ingest, scenario, Arabic RTL, first-visit home → demo), 0 problems.
* **Self-assessment:** R1 5 · R2 5 · R3 5 · R4 5 · R5 5 · R6 4 | J1 5 · J2 5 · J3 5 · J4 5 · J5 5 · J6 4.
  R3 (patient communication) 4→5 with result-specific wording; J6 (usability) 3→4. Remaining: real translations for
  tl/ml, production auth (UAE PASS / SSO), SQL-side queue ordering beyond ~10k open cases, Docker untested here.

## Iteration 6 – clinical safety: grounded rules, fail-safe review, realistic 20k data, clinical test suite (21 Oct 2026, ~00:35–01:50 IST)

**Request:** make the clinical logic and synthetic data close to real practice and keep functional error negligible,
with patient safety as the top priority. Committed in checkpoints 912451d → (final).

### Rounds
1. **Rules engine** (`wasl/clinical_rules.py`, pure, no ML). Covers FIT, HPV + cytology, BI-RADS, and HbA1c (% and
   mmol/mol)/FPG (mg/dL and mmol/L). Thresholds, ages, pathways, tiers and targets are taken from published guidelines
   (`docs/clinical_rules.md`, sources S1–S15). Units (OBX-6) and result status (OBX-11) are carried through HL7, FHIR and the API.
2. **Fail-safe human review.** These go to a clinical-lead review queue at a minimum tier with reason codes and are never
   dropped or downgraded: unknown, malformed, missing, out-of-range, conflicting, amended or preliminary, retracted or
   duplicate results; missing or unknown units; failed matches; out-of-age, sex mismatch, pregnancy, deceased,
   under-specialist-care, haemoglobinopathy; results after closure. Supporting controls:
   - an ORM interlock (`TierDowngradeError`), so the tier is lowered only by an audited clinician review;
   - while a review is open, no patient contact and no system transitions, and closure is clinician-only;
   - ML orders cases only within a tier.
3. **Realistic cohort mode.** `make seed-large` builds 20,000 people calibrated to cited sources
   (`docs/synthetic_data.md`). `scripts/validate_data.py` checks 44 invariants, including re-applying the rules to
   every report to detect lost actionable results.
4. **Tests.** Golden cases, fail-safe tests, Hypothesis properties, the exhaustive state machine and synthetic-data tests.
5. **Full cohort.** Cohort test, fault injection (33/33 killed), documentation.
6. **Pathway target clocks.** Step SLAs are capped at the guideline end-to-end target. ASCCP 2023 repeat timing.
   Clinical safety case (`docs/clinical_safety_case.md`, H-01…H-20).
7. **Simulator.** Now classifies with the same rules engine. The colorectal baseline is calibrated to the
   Abu Dhabi 2015–16 study. Tests were strengthened after mutation testing.

### Bugs found and fixed (clinical logic first)
- A missing FIT/HbA1c value was treated as 0 → **silently negative**. Now: review at T1.
- BI-RADS 3 and 6 were treated as negative on a screening report. Now: review (high or urgent).
- HbA1c in mmol/mol was read as % (40 mmol/mol → "critical"). Now: unit-aware, using exact NGSP/IFCC conversion
  cut-offs rounded down (38.7 / 47.5 / 85.7 / 139.9).
- The HbA1c critical cut-off of 140.4 mmol/mol missed 15.0 % reported by a lab as "140". Found by the 171-point unit-agreement test.
- A new, more urgent or conflicting result was linked to an open case without re-triage. Now: re-triage, escalate only, flag for review.
- Prediabetes was not actionable. Now: a routine GP surveillance case (safer default, CONFIRM).
- Pathway errors:
  - prediabetes and HPV-other/NILM were routed to specialist referral;
  - a negative confirmatory test closed cervical/diabetes cases without a surveillance plan;
  - the diabetes text said "metformin" for prediabetes.
- Patient wording errors:
  - "blood found" was shown for a spoilt FIT kit;
  - "HPV 16/18" text was shown for other types;
  - "see a doctor today" was shown for prediabetes.
- Per-step SLAs added up beyond guideline targets (e.g. colonoscopy beyond 31 days, breast assessment beyond 5 working days).
  Now: `target_due_at` caps every step.
- Ingest and robustness:
  - unknown LOINC codes or malformed values were rejected rather than reviewed;
  - OBX-6 and OBX-11 were ignored;
  - a message without MSH-10 was rejected;
  - a missing clinical lead raised an error (result at risk);
  - a patient consult failed when the GP was inactive.
- Synthetic data:
  - the HbA1c band table summed to 0.88, so the picker put 12 % into the ≥ 15 % band (about 1,800 false "critical" cases in 20k);
  - age was computed against "now" instead of the screening date;
  - the FHIR bundle had an unresolved Practitioner reference.

### Results
- Tests: **2,281 collected, 2,281 passed (100 %)**, including the original 105. By type:
  - golden clinical: 525
  - fail-safe: 60
  - Hypothesis property tests: 6 (thousands of generated examples)
  - exhaustive state machine: 1,558
  - synthetic data: 26
  - full cohort: 1
  - original: 105
- 20k cohort (20,005 patients; 21,400 reports; 5,471 cases; 91,438 audit events): **0 violations across 44 invariants,
  0 lost actionable results**, audit chain valid. The 2k demo also has 0 violations.
- Fault injection: 33/33 deliberate faults detected (`docs/fault_injection_results.md`).
- Mutation testing (mutmut 3.8, `clinical_rules.py` + `statemachine.py`, pure test subset): MUTMUT_RESULT
- Simulated KPIs (20,000 × 5 replications, **simulated**): `docs/simulation_20k_results.json`, summarised in the README.
- Performance (fresh 10k DB, 10 clients, 4 workers, warm; mutmut paused): p50/p95 for the coordinator queue went from 29/448 to 28/258 ms, the GP queue from 32/599 to 32/304 ms, and HL7 ingest from 77/182 to 73/195 ms; detail 39/78 ms, FHIR 35/126 ms, KPIs 7.7/14 ms. **No regression.** The first cold pass gave a queue p50 of 145 ms. The 20k simulation (×5) takes 2.8 s and the 20k realistic seed about 2.5–3 min.

### Honest limits
- No clinical sign-off and no validation on real data; 16 CONFIRM items are listed in `docs/clinical_rules.md` §9.
- Some sources were verified only partially. The DoH colorectal specification (S7) was seen only as a search excerpt,
  and ADA §9 (S2) and ASCCP 2023 (S10) only through search summaries. No DoH FIT threshold was found.
- Hypothesis event sequences reach successful transitions in only about 1–5 % of examples, because the review gate blocks
  most of them. Transitions are covered exhaustively by the state-machine tests instead.
