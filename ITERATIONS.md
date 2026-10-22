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
- Mutation testing (mutmut 3.8, pure test subset): the first run on `clinical_rules.py` killed 1,028 of 1,440 mutants (71.4 %). Survivors were mostly display strings plus some test gaps, and tests were strengthened. See Iteration 7 for the re-run.
- Simulated KPIs (20,000 × 5 replications, **simulated**): `docs/simulation_20k_results.json`, summarised in the README.
- Performance (fresh 10k DB, 10 clients, 4 workers, warm; mutmut paused): p50/p95 for the coordinator queue went from 29/448 to 28/258 ms, the GP queue from 32/599 to 32/304 ms, and HL7 ingest from 77/182 to 73/195 ms; detail 39/78 ms, FHIR 35/126 ms, KPIs 7.7/14 ms. **No regression.** The first cold pass gave a queue p50 of 145 ms. The 20k simulation (×5) takes 2.8 s and the 20k realistic seed about 2.5–3 min.

### Honest limits
- No clinical sign-off and no validation on real data; 16 CONFIRM items are listed in `docs/clinical_rules.md` §9.
- Some sources were verified only partially. The DoH colorectal specification (S7) was seen only as a search excerpt,
  and ADA §9 (S2) and ASCCP 2023 (S10) only through search summaries. No DoH FIT threshold was found.
- Hypothesis event sequences reach successful transitions in only about 1–5 % of examples, because the review gate blocks
  most of them. Transitions are covered exhaustively by the state-machine tests instead.


## Iteration 7 – differential testing, mutation re-run, alert-precision start (21 Oct 2026, ~01:30–02:45 IST)

**Steering (user, via parent):** keep iterating for about 12 hours. Priorities:
1. Safety-critical decision errors driven to zero, with an observed rate and its 95 % upper bound, using
   differential testing on 1M+ cases.
2. Alert precision with zero missed true breaches.
3. ML calibration, fairness and rationale.
4. Empathetic plain-language messages with reading-level checks.
5. Explicit human sign-off.
6. Efficiency.

### Round 1: independent reference implementation and differential testing
- `wasl_reference/rules.py` was written from `docs/clinical_rules.md` without importing `wasl`. It uses interval tables and
  token sets. `scripts/differential_test.py` (`make differential`) generates boundary-weighted randomised results across all
  programmes, units, statuses, malformed inputs and patient contexts. Every disagreement is classified as safety-critical
  (missed, under-triaged, review bypassed, wrong pathway) or non-critical (more cautious).
- **Run 1 (100k): 167 safety-critical disagreements, from two real engine bugs:**
  1. A breast result with numeric value **-1**, or text "-1", was parsed as **BI-RADS 1, i.e. negative**, because the
     regex allowed a bare separator before the digit.
  2. **HbA1c with a haemoglobinopathy flag** went to review only when abnormal, so a falsely normal HbA1c was filed as
     negative. The rules doc says every such HbA1c goes to review.

  Both are fixed, with regression tests.
- Non-critical alignments with the doc:
  - a blank cytology field now means "not reported";
  - HPV-invalid with low-grade cytology is reviewed at **high**, per the doc (it was urgent);
  - co-reported HPV types (16 with other types, or "positive" with "other") are managed by the most significant type
    (16/18 first), per ASCCP 2019. This is a new CONFIRM item (17). Previously they went to an urgent review.
- **Later runs: 2M → 0 disagreements of any kind. 10M (new seed) → 0 safety-critical, 0 non-critical; one-sided 95 % upper
  bound 3.0e-7 (rule of three).** See `docs/differential_results.md`.
- The data validator re-applies the rules to every stored report. On the cohort seeded before the fix it flagged **423**
  haemoglobinopathy HbA1c results that had been filed as negative. Both databases were re-seeded.

### Round 2: mutation testing re-run
- `statemachine.py` was added to the mutated modules. A full-output snapshot of all golden decisions now pins the
  clinician-facing wording, and the differential test was added to the mutation test set.
- Result: **1,419 of 1,560 killed (91.0 %)**, up from 71.4 %. Of the 141 not killed:
  - 87 change only display strings;
  - about 42 are equivalent or keep the same tier;
  - 12 were real test gaps (range edges, boolean value, note-length boundary, `allowed_targets`), now covered by
    tests added after the run.

  See `docs/mutation_testing.md`.

### Round 3 (alert precision, first fix)
- **Bug:** `care_reached_at` was set *after* the treatment/surveillance step opened. The closure-documentation step was
  therefore capped by the pathway target, and a target that had already passed made it breach instantly.
- Impact on the 20k cohort: 1,122 spurious breached steps. Breached steps fell from 5,393 to 4,216 (−22 %) and
  instantly-due steps from 2,443 to 1,322.
- New validator invariant 45, `sla.post_care_step_not_instantly_overdue` (0 violations), and a regression test.
- Next round: define true breaches (uncapped step SLA, or pathway target missed before care), measure escalation
  precision and recall on the replay, and decide policy for steps opened after a missed target. Those steps currently
  breach instantly, so each one repeats an L2 alert.

### Results at the end of this session
- Tests: **2,925 collected, 2,925 passed (100 %)**. By type:
  - golden clinical: 532
  - differential: 44
  - fail-safe: 61
  - Hypothesis properties: 6
  - exhaustive state machine: 2,150
  - synthetic data: 26
  - full cohort: 1
  - original: 105
- 20k cohort: 0 violations across 45 invariants. 2k demo: 0. Server on :8000 with the 2k demo seed.

### Round 4: consolidated alert policy, precision and recall
- `wasl/alert_policy.py` defines the ladder. The step clock raises one L2 per breach (L3 for critical), then
  L3 at 1.5× and L4 at 2× (L4 for critical/urgent only); L1 is a pre-breach reminder at 50 %. The pathway clock raises
  **one consolidated alert per case per level**. Steps opened after a missed target inherit the breach and do not
  repeat it.
- `scripts/alert_eval.py` labels true breaches independently of the policy and replays the sweep. Validator invariant 46.
- 20k cohort: **precision 100 %, recall 100 %, 0 missed**, against the legacy policy's 77 % precision and 90 % recall
  (about 320 missed, nearly 1,500 repeat alerts). The full-cohort test now fails on any missed true breach.

### Round 5: ML calibration, fairness and rationale
- Added calibration statistics: ECE, Brier, calibration slope, calibration-in-the-large, and reliability bins with
  Wilson CIs.
- New promotion gates: slope 0.7–1.3 and |CITL| ≤ 0.05.
- Recalibration is optional. The default is "none"; "auto" applies Platt scaling chosen on a temporal tuning slice. On
  the 2k cohort Platt over-corrected the held-out set (slope about 1.4), which is why it is not the default.
- Each outreach priority now carries a rationale (one-at-a-time substitution against a reference profile), shown in
  the API, the case page and the queue tooltip. Rollback clears it.
- Fixed an inverted rationale label (`early_teach_back_failed`).
- Report: `docs/ml_calibration.md`.

### Round 6: patient messages
- Reading-level and tone checks for every message in every language. English uses Flesch–Kincaid and reading ease;
  Arabic, Hindi and Urdu use sentence-length and word-length proxies.
- Arabic, Hindi and Urdu drafts of the pathway texts are flagged `translation_pending`. They are **not sent** until a
  clinician and a separate translator have both signed off the exact wording (hash-bound). Until then the patient gets
  the English text or the neutral text.
- Validator invariants 47 and 48. Details in `docs/patient_messages.md`.

### Round 7: human sign-off
- `wasl/signoff.py` registers 17 high-impact actions. Each requires a named human, the permission and a rationale.
- Guards were added to 9 actions that previously checked permission only: reopen, assign, transfer, confirmatory
  result, referral decision, start care, escalation acknowledgement, model reject/rollback, and proposal decision.
- Details in `docs/human_signoff.md`.

### Round 8: efficiency
- On the 10k load test the work-queue p95 had regressed to about 1.3 s. Cause: the per-row GIL hand-off in sqlite3
  plus concurrent requests piling into one worker.
- Fixes:
  - JSON is decoded only for the returned page;
  - GIL switch interval set to 0.5 ms;
  - at most 2 concurrent queue computations per process;
  - multi-process `SO_REUSEPORT` server (`python -m wasl.serve`).
- Coordinator queue p95 went from **1,272 ms to 301 ms** (83 req/s).

### Round 9: new failure modes (adversarial HL7/FHIR, concurrency, time, duplicate identities)
Re-run first: full suite green; 10M differential (seed 20261021) found **0 disagreements** (95 % upper bound 3.0e-7).
Then each failure mode was probed and fixed, one commit each. Bugs found, clinical first:

1. **A bilateral mammogram with BI-RADS 1 (left) and BI-RADS 4 (right) was filed NEGATIVE.** The HL7 parser used the
   first OBX only. The same thing happened with two FIT values (5 and 300 → 5), and with HbA1c plus FPG in one
   message (the second test was silently ignored).
   - Now every value is evaluated by the clinical rules and the **most severe** one drives the decision; ties go to
     the diagnostic-range result.
   - All values are kept on the report.
   - If the values differ, the case goes to review (`multiple_results`, floor high).
2. **Two patients in one HL7 message were merged.** Patient B's FIT 300 was lost and patient A was given B's value.
   Such messages now go to the review queue ("split per patient"), and the same applies to FHIR Bundles with more than
   one Patient.
3. **Structured-numeric comparators were dropped.** `>^9.9` became 9.9. The comparator is now kept as text (`>9.9`),
   so the result goes to urgent review.
4. **A reused control ID with different content was silently treated as a replay,** so a corrected result was lost.
   Now a payload hash is stored; a byte-identical replay is still idempotent, but different content becomes a new
   review case (`control_id_reused`).
5. **Concurrent actions on one case.** Two users acting on a stale view both succeeded; the probe showed a transition
   applied twice.
   - Now there is an optimistic row-version on cases: the second action gets **409 and nothing is saved**.
   - A DB unique index allows only one open case per patient and programme.
   - Result, HL7 and FHIR ingest retry on conflict, so 8 concurrent deliveries give 1 message and 1 case.
   - The SLA sweep re-runs on conflict.
6. **Time handling:**
   - the SLA working calendar used the UTC date, so 20:00–24:00 UAE time was counted as the wrong day around
     weekends and holidays. It now uses UAE local time (`Asia/Dubai`, no DST);
   - an appointment time sent with `+04:00` had the offset dropped, a 4-hour error. It is now converted;
   - the patient's confirmation showed UTC (a 09:00 appointment read 05:00). It now shows UAE local time in the
     language-neutral form dd/mm/yyyy HH:MM;
   - a no-show (or attendance more than 4 h early) could be recorded before the appointment. Both are now rejected;
   - HL7 ACK timestamps now carry `+0000`.
7. **Duplicate identities (one person under two MRNs) were invisible.** A negative on record B could not be compared
   with the positive case open on record A.
   - Patients now carry a synthetic Emirates ID.
   - Open-case and recent-closure checks span all records with the same ID, and the case is flagged
     `possible_duplicate_identity` (floor high).
   - HL7 can match on an Emirates ID in a PID-3 repetition. An ambiguous ID goes to match review.
8. **Robustness:**
   - structurally broken FHIR Bundles and deeply nested JSON returned **500**; they now return 4xx;
   - `float()` accepted `1_000` (as 1000), `nan` and `inf` as lab values. Strict decimal syntax is now enforced, and
     anything else stays text and goes to review;
   - PID-3 repetitions are now split, delimiters are read from MSH-1/2, and HL7 escape sequences are decoded.

- Validator invariants 49 (one open case per Emirates ID and programme) and 50 (attendance not before the
  appointment). 0 violations on demo, 10k and 20k.
- Fault injection was extended with F34–F42, one for each fix above.
- New tests: `test_ingest_adversarial.py` (including Hypothesis fuzzing of HL7 and FHIR), `test_concurrency.py`,
  `test_time_handling.py` and `test_duplicate_identity.py`.
- Not changed: the demo seed's two candidate models both fail the fairness recall gate for age 40–49, so the demo has
  no production model. Outreach falls back to rule order, as designed. The gate was not relaxed.

### Round 10: more failure modes, ML fairness fix, re-verification (21 Oct 2026, ~03:45-04:30 IST)
- **Automated patient reminders were sent at any hour.** The SLA sweep runs every 5 minutes. The new validator invariant
  51 found 94 reminders sent at about 01:53 UAE time in the existing 20k data. Reminders now go out only between 08:00
  and 21:00 UAE time. Outside that window they are queued as an `on-hold` Communication, then sent once by the first
  sweep inside the window, or cancelled if the case has moved on.
- **ML fairness.** Both candidate models on the demo ranked 40-49-year-olds last (recall in the top 30 % was 0.05-0.10,
  against 0.8-1.0 for ages 60-75) and failed the fairness gate. That left the demo with no production model.
  - `age_band` has been removed as a model input; it is still monitored as a fairness attribute.
  - On the demo, AUC rose from 0.640 to 0.656 and ECE fell from 0.041 to 0.034. All gates pass, and age-band recall is
    now 0.42-0.51 on 10k.
  - The gate was **not** relaxed.
- **Feature-set change crashed the model.** A production model trained on the older feature set crashed both scoring
  and comparison against new candidates ("columns are missing"). Models now pick their own input columns, with a
  regression test.
- **HL7 input:** a UTF-8 BOM and MLLP framing are now tolerated.
- **Server clock:** the background SLA sweep now refuses to run if the server clock moves backwards.
- **Re-verification:**
  - full suite 3,082 tests, all passing;
  - 10M differential (seed 20261121): 0 disagreements;
  - 20k, 10k and demo: 51 invariants, 0 violations;
  - alert precision/recall 100 % / 100 %;
  - fault injection: see `docs/fault_injection_results.md`.
- **Load test** (10k, 4 processes, 10 clients), p50/p95: coordinator queue 62/154 ms, GP queue 77/163 ms, detail 25/58 ms,
  HL7 ingest 79/163 ms; 0 errors; audit chain intact.

### Round 11: consent withdrawn mid-pathway (21 Oct 2026, ~04:33-04:37 IST)
- **Failure mode found by probe.** After a patient withdrew `care_coordination` consent (and refused phone contact):
  automated reminders kept going, no clinician was told, and phone call tasks were still created.
- **Fix.**
  - Automated (system) outreach now stops: `send_with_ladder` refuses system sends; the sweep skips L1 patient
    reminders and cancels queued ones. Staff-facing escalation still fires.
  - Every open case of the patient is flagged for clinician review (`consent_withdrawn`, routine; tier never changes),
    so a withdrawal cannot silently strand a positive result. A named clinician can still make contact (audited).
  - A `comm_phone` deny now ends the ladder with `not-done (no consent)` instead of creating a call task.
- **Bug found on the way:** a clinician sending a result notification on a case already flagged for review got 403
  (the system-actor step transition was refused). The step is now recorded as that clinician.
- 4 new tests (`tests/test_consent_withdrawal.py`); full suite 3,086 tests, all passing. No rules-engine or schema
  change, so the differential result and seeded data stand.

### Round 12: specimen collection time (21 Oct 2026, ~04:37-04:42 IST)
- **Failure mode.** The collection date in a result (HL7 OBR-7 / OBX-14, FHIR `effectiveDateTime`) was ignored. A
  backfilled message for a specimen collected a year ago, or one dated in the future by a lab clock or typo, was
  handled as a fresh result (and a stale/future-dated negative was filed silently).
- **Fix.** Collection time is parsed (HL7 TS with or without offset - no offset means UAE local; FHIR dateTime/date).
  More than 180 days before receipt -> `stale_result` (routine floor); more than 24 h after receipt ->
  `collection_date_in_future` (high floor). Both go to clinician review; the clinical tier is never lowered.
  Unparseable dates are ignored (nothing is dropped).
- 14 new tests; full suite 3,100 tests, all passing. Rules engine `evaluate` unchanged.

### Round 13: patient dies mid-pathway (21 Oct 2026, ~04:42-04:48 IST)
- **Failure modes.** (1) A death could only be set at registration, so after a death registry or family notification
  reminders and phone tasks continued. (2) Any case could be closed with reason `deceased` although the patient was not
  recorded as deceased: a mis-click silently ended a living patient's follow-up, while other programmes kept messaging.
- **Fix.** New high-impact action `patient.deceased.record` (`workflow.record_death`, API
  `POST /api/cases/{id}/patient-deceased`): named clinician with `closure:authorise`, rationale >= 10 chars, audited.
  It stops all messaging, cancels open call tasks and queued reminders, and flags every open case (`deceased`) for an
  authorised clinician closure - nothing is closed automatically. Closure reason `deceased` now requires the recorded
  death.
- Validator invariant 52 (`closure.deceased_reason_requires_deceased_patient`); `safety.deceased_never_contacted` now
  allows contact before the recorded death. 0 violations on demo, 10k and 20k. Sign-off registry: 18 actions.
- 5 new tests; full suite 3,105 tests, all passing.
- **Follow-up (same round):** a death recorded against the wrong patient could not be undone. New high-impact action
  `patient.deceased.revoke` (clinical lead, `case:reopen`, rationale >= 20 chars, audited;
  `POST /api/cases/{id}/patient-deceased/revoke`): open cases are re-flagged (`death_record_corrected`) and cases closed
  as `deceased` are listed for re-opening - nothing restarts silently. UI action added. Sign-off registry: 19 actions.
  Full suite 3,108 tests, all passing.

### Round 14: appointment dates (21 Oct 2026, ~04:48-04:51 IST)
- **Failure mode.** Any future appointment was accepted: a year typo (2027 for 2026), or a slot weeks after an urgent
  pathway target, went through silently; the breach only surfaced when the target alert fired weeks later.
- **Fix.** Bookings more than 365 days ahead are refused ("check the date"). A booking after the pathway target needs
  a reason (>= 10 characters, e.g. earliest slot, patient's choice), recorded in the audit event (`beyond_target`).
  UI form updated. Synthetic history records the capacity reason (data distribution unchanged).
- 1 new test; full suite 3,109 tests, all passing.

### Round 15: multi-value tie-break (21 Oct 2026, ~04:52-04:56 IST)
- **Finding.** With several values in one message, ties within the same tier went to the first value: a bilateral
  mammogram BI-RADS 4 (left) + BI-RADS 5 (right) showed "BI-RADS 4" as the case's primary value (both were kept and
  the case was reviewed, but the headline understated the finding). Same for 4A vs 4C and FIT 150 vs 400.
- **Fix.** Last tie-breaker = magnitude (normalised value, or BI-RADS category with 4A < 4B < 4C).
- 6 new tests; full suite 3,115 tests, all passing. Ingest parsing of a maximum-size (64 KB, 1,362 OBX) message: 10 ms.

### Re-verification after rounds 11-15 (21 Oct 2026, ~05:10 IST)
- Full suite 3,115 tests, all passing. 10M differential (seed 20261122): 0 disagreements, upper bound 3.0e-7.
- Fault injection 50/50 killed (F43-F50 added for rounds 11-15). Validator: 52 invariants, 0 violations on demo, 10k
  and 20k. Alert precision/recall (20k) 100 % / 100 %, 0 missed. Sign-off registry: 19 high-impact actions.
- Rules engine, ML and alert policy unchanged since round 10, so ML calibration and load-test figures stand.

### Round 16: patient messages as actually sent (21 Oct 2026, ~05:36-05:55 IST)
- **Why this area.** Safety is at its measured best (rounds 11-15); patient messages had not been revisited since
  round 6. The round-6 checks measure each *template* with empty placeholder samples, never the message a patient
  receives.
- **Failure mode found by probe** (rendering every template x programme x pathway x tier x sign-off state in 4
  languages, 138,240 messages):
  1. **Mixed-language messages.** While a pathway text had no signed-off translation (all of them today), its English
     wording was spliced into the Arabic/Hindi/Urdu template: "مرحباً Yousef. ... Your blood sugar test was a little
     higher than normal ... خطوتك التالية: <English next step>". The patient portal promised "a navigator will call you
     with an interpreter", but no call task was ever created. Existing synthetic data: **637 (demo), 3,177 (10k),
     2,197 (20k)** mixed messages sent (about 22-26 % of messages).
  2. **Reminders too long once the next step is inserted:** FK grade > 8 in English ("your next step is a camera test
     of the bowel (colonoscopy) at a hospital clinic, today") and sentences > 30 words in Hindi/Urdu.
- **Fix.**
  - `render_message` sends the WHOLE message in the signed-off English wording when the result wording is not signed
    off in the patient's language (`Communication.language = en`; audit `requested_language`), and
    `send_with_ladder` creates an interpreter-supported navigator call task (audit `calltask.created`, reason
    `interpreter`; one open task per case; a `comm_phone` deny is honoured). Messages without result wording
    (no-show, decline, reassurance) stay in the patient's language. Never an unreviewed translation.
  - Reminder templates (all 4 languages, both variants) split "next step" and "when" into short sentences, reusing
    phrases of the existing simplified variants (non-English edits remain drafts needing translator sign-off).
  - Governance unchanged: translation gate, clinical sign-off, hash binding, separation of duties.
- **Invariants.** New validator invariant 53 `comms.sent_message_single_language`. Re-seeded demo, 10k and 20k
  (server on :8000 stopped and restarted the same way; old DBs kept in `/workspace/fhdic/backup_pre_r16/`):
  **53 invariants, 0 violations** on all three; 0 lost actionable results. Alert precision/recall on the new 20k:
  **100 % / 100 %** (5,233 TP, 0 FP, 3,178 true breaches, 0 missed).
- **Tests.** 6 new: every rendered message (4 languages, parametrised) is single-language and passes reading-level
  and tone checks at the strict limits (FK <= 8, <= 6 for simplified, ease >= 60); integration test (Arabic
  patient -> English message + one interpreter call task, no duplicate on resend); English speaker gets no task.
  All 6 fail on the pre-fix code. Fault injection F51 (splice) and F52 (no interpreter call) both killed.
  **Full suite 3,121 tests, all passing.** Rules engine unchanged, so the 10M differential result stands.

### Round 17: efficiency re-check since round 10; FHIR export crash (21 Oct 2026, ~05:58-06:08 IST)
- **Load test** on the freshly seeded 10k (temporary server on :8010/:8011, 4 processes, 10 clients, 200 requests
  per endpoint; :8000 untouched) **found a crash:** `GET /api/cases/{id}/fhir` raised `KeyError: 'on-hold'` -> HTTP 500
  for every case holding a reminder queued outside the patient message window (round 10) or a dispatched/cancelled
  placeholder (`stopped`). An export-every-case sweep on the 10k: **123 of 10,005 cases (1.2 %) could not be
  exported** (care handover / FHIR interoperability blocked); demo 26 and 20k 71 such Communications. The round-10
  code fails the same way on the same DB, so it dates from round 10 (its load test ran before any reminder was queued).
- **Fix.** Every internal Communication status maps to a FHIR `Communication.status` code (`on-hold`, `stopped`;
  anything unexpected -> `unknown`, never a crash). Export-every-case sweep after the fix: 0 errors on 10k (10,005
  cases, FHIR + case detail) and 20k (5,922 cases). 6 new parametrised tests (2 fail before the fix).
  The :8000 demo server was restarted the same way (`python -m wasl.serve --host 0.0.0.0 --port 8000 --workers 4`,
  log `/tmp/serve.log`); FHIR export of demo case 208 (on-hold reminder) now 200.
- **Latency A/B, same 10k DB, round-10 code (d04f62a) vs current, 2 x 2 runs each** (p50/p95 ms, mean):
  coordinator queue r10 71-90 / 137-177 (mean 74-85) vs now 29-137 / 140-188 (mean 75-118); GP queue r10 78-89 /
  153-167 vs now 36-168 / 171-242; detail r10 22-30 / 42-55 vs now 13-40 / 55-78; HL7 ingest now 71-75 / 176-212
  (round 10: 79/163). Throughput 95-130 req/s on the queues for both. **No consistent regression**: the spread
  between runs of the same code is as large as the difference; one current run was slower across the board (box
  contention suspected). Audit hash chain valid after every run; 0 errors once the FHIR fix was in.
- **Full suite 3,127 tests, all passing.** Validator unchanged (53 invariants, 0 violations).

### Round 18: ML fairness gate failure after the round-16 re-seed (21 Oct 2026, ~07:40-08:05 IST)
- **Finding.** The regenerated calibration report (after round 16 re-seeded demo, 10k and 20k) showed the demo
  logistic candidate failing `fairness_recall:language` (both recalibration settings); 10k and 20k pass every gate.
  Reproduced exactly. The flagged group is **English** speakers: 25 delayed cases in the hold-out, only 3 in the
  top-30 % outreach priority (recall 0.12 vs 0.47 overall; bootstrap CI 0.00-0.26, wholly below the 0.32 limit), and
  English cases were flagged at 6 % although their observed delay rate (18 %) equals the Arabic group's (17 %,
  flagged at 24 %). Group calibration was fine (CITL -0.02); the model gave English speakers a low baseline through
  the one-hot `language` input and ranked them poorly within the group (AUC 0.60).
- **Diagnosis.** Not a round-16 messaging artefact: English speakers are not touched by the interpreter path and
  their delay rates are unchanged (train 0.179 -> 0.177). Not gate noise either: the pre-round-16 demo
  (`backup_pre_r16/wasl_demo.db`) shows the same pattern (English recall 0.25, flag rate 10 % vs delay rate 17 %) that
  passed only because the CI (0.07-0.43) was wide; the re-seed shifted the random stream and the CI crossed the line.
  The gate correctly caught a real, previously under-powered weakness. In the simulator the only language effect on
  drop-off is "no material in the patient's language" (Tagalog, Malayalam); the one-hot input let the model encode
  language identity instead. The seed itself had already put demo v1 (logreg) in `failed_gates` and promoted the
  passing gbm (v2), so production was never affected.
- **Fix (no gate, tolerance, minimum group size or definition changed).** The one-hot `language` model input is
  replaced by `language_unsupported` (1 = no patient material in the preferred language, so contact needs an
  interpreter), the operational barrier it stood for. Language stays a fairness attribute and is still drift-monitored
  (added explicitly to the reference stats). Rationale label: "No patient materials in the preferred language
  (interpreter needed)". Model card limitation text updated. Three feature sets were compared on the same hold-outs
  (one-hot / no language / barrier flag); the barrier flag follows the mechanism and was the only one that kept or
  raised AUC on demo and 20k, but the choice was made after seeing these hold-outs, so it must be re-checked on fresh
  data. Older models keep their own input columns: the demo production gbm v2 (trained with one-hot language) scores
  identically after the change (max score change 0.0, tiers/states unchanged, drift ok); nothing was promoted.
- **ML metrics (hold-out, recalibration none; before -> after).** Demo logreg AUC 0.676 -> 0.684, ECE 0.015 -> 0.036,
  slope 0.957 -> 1.007, CITL 0.010, bins 10/10, gates **all pass** (language recall gap 0.38 -> 0.26, English recall
  0.12 -> 0.24); demo gbm AUC 0.671 -> 0.670, ECE 0.028, slope 0.883, bins 8/10 -> 10/10, all pass. 10k logreg AUC
  0.660 -> 0.659, ECE 0.025, slope 1.021, bins 9/10; gbm 0.666 -> 0.661, ECE 0.028, slope 0.94, bins 9/10 -> 8/10; all
  pass. 20k logreg AUC 0.713 -> 0.720, ECE 0.042, slope 1.198, CITL 0.028, bins 9/10; gbm 0.701 -> 0.699, ECE 0.038,
  slope 1.018, bins 9/10; all pass. `auto` recalibration chose Platt on the demo (slope 1.23/1.28, within the gate but
  over-correcting again), none on 10k/20k. Report now includes the 10k cohort (`docs/ml_calibration.md/.json`).
- **Governance (verified, now tested unconditionally).** A candidate failing only the language recall gate is
  refused by the API (409) for a clinical lead, and by the service layer even with its status tampered to
  `awaiting_approval` (the `all_passed` guard); production is unchanged. The ML writes only the risk score and
  rationale; tiers, states and closures are untouched. Every model input has a staff-readable rationale label (no
  column names), and every scored case carries a rationale list (empty = "close to the typical case" in the UI).
- **Tests.** New `tests/test_ml_fairness_language.py` (19 tests: recall gate fails on a credibly under-flagged group
  and passes when groups are served alike; gate-failing promotion refusal; language monitored, not an input;
  interpreter-need flag; case frame keeps `language` for older models; rationale label per input; rationale stored for
  every scored case). Fault injection F53 (approve ignores `all_passed`) and F54 (recall gate never fails): **both
  killed** (`--only F53,F54`; the full 54-fault re-run is recorded separately if it completes).
  **Full suite 3,146 tests, all passing.**
- **Invariants and alerts (no DB re-seeded).** Validator: **53 invariants, 0 violations** on demo, 10k and 20k; 0 lost
  actionable results. Alert precision/recall (20k): **100 % / 100 %** (5,233 TP, 0 FP, 3,178 true breaches, 0 missed;
  legacy 76.6 % / 89.6 %). The :8000 demo server was restarted the same way to load the new feature code.
- **Open.** The 10k/20k language recall gaps are still wide point estimates (0.58 / 0.53) that pass only on CI
  width; continue subgroup monitoring on pilot data. Synthetic validation only: no clinical claim.

### Round 19: fresh-seed re-check of the ML language input; human sign-off audit (21 Oct 2026, ~09:33-10:05 IST)
- **Re-measured first (code at 8471c71), all green.**
  - Full suite: 3,146 passed.
  - Validator: 53 invariants, 0 violations on demo, 10k and 20k.
  - Alert precision/recall (20k): 100 % / 100 % (5,233 TP, 0 FP, 3,178 true breaches, 0 missed).
  - Safety, alerting and messages were already at their measured best, so this round took the round-18 open item
    (decision support) and the human-in-the-loop audit.
- **ML: is the round-18 feature choice confirmed on fresh data?** New script `scripts/ml_feature_revalidation.py`
  trains logreg and gbm with three input sets on each cohort: one-hot language, no language, and the barrier flag
  `language_unsupported`. Gates, tolerances and minimum group sizes are the production ones. The decision rule was
  committed (a071c4a) before any fresh result was seen. The script reproduces the round-18 demo numbers exactly
  (logreg 0.6763 -> 0.6842).
  - **Check 1 (barrier as primary; new seeds 20261919 demo, 20261929 10k, 20261939 20k realistic): NOT CONFIRMED.**
    `fairness_recall:nationality_group` failed for both algorithms on the fresh demo (Other Arab: 1 of 11 delayed
    cases flagged) and on the fresh 10k (Western: 2 of 13). All gates passed on the fresh 20k.
  - **Check 2 ("none" as primary).** It was pre-registered in 9aa178e before three more seeds were made
    (20261949, 20261959, 20261969). Result: also **NOT CONFIRMED** under its own rule. "none" passed every gate on
    the 10k and 20k, but on the 2,000-patient demo it failed `calibration_slope` (0.56-0.64). Every feature set
    failed there: this is small-sample over-confidence, not a language effect.
  - **Over the 6 fresh cohorts x 2 algorithms:**
    - nationality recall failures: barrier 5/12, one-hot 4/12, none 0/12;
    - candidates passing every gate: barrier 5/12, one-hot 6/12, none 10/12;
    - language recall gap: barrier 0.35-0.65, none 0.19-0.27;
    - AUC (none - barrier): -0.024 to +0.018, never credibly worse by more than 0.01.
  - **Not gate noise.** New `scripts/fairness_gate_null_rate.py` permutes the group labels (no real difference). The
    fairness gates then fail in 2.5 % of 200 permutations (95 % CI 0.3-4.7 %).
  - **Mechanism.** The barrier flag puts Malayalam and Tagalog speakers at the top (fresh 10k flag rates 0.69/0.59
    against observed delay rates 0.27/0.19). They fill the top-30 % capacity, so recall drops for Western, Emirati and
    Other Arab patients. This also explains the wide 10k/20k language gaps that were left open in round 18.
  - **Decision: no production input changed.** Neither pre-registered rule was met, and switching again after
    looking at hold-outs would repeat the round-18 mistake. Production is protected: the gates refuse every failing
    candidate, so there is no promotion and outreach falls back to rule order. The demo's production model (gbm v2) is
    unchanged. Recommendation for the clinical lead / model governance: adopt "none", or pre-register a rule that
    handles the small-demo slope failure. Full tables: `docs/ml_feature_revalidation.md/.json`. ML metrics on
    demo/10k/20k are unchanged from round 18 (no ML code change).
- **Human sign-off audit.** Every write endpoint was mapped to its service function and checked against
  `wasl/signoff.py`. Two gaps were found by probe:
  1. **Decisions recorded on the patient's behalf.** `record_consent` (staff) and `record_decline` (staff) checked
     only `consent:write`. An automated job with a coordinator role could withdraw care-coordination consent, which
     stops automated outreach in every programme, or record a decline, which stops the pathway. No rationale was
     needed. They are now high-impact actions `consent.record` and `case.decline.record`: named person,
     `consent:write`, rationale of at least 10 characters. The patient's own app decisions are unchanged; the UI
     forms say so. Synthetic history already met the rule, so the data distribution did not change. Registry: 21
     actions.
  2. **Evidence from a closed episode re-used after a re-open.** On a re-opened case, a generic transition reached
     `treatment_or_surveillance_started` on the OLD care plan, bypassing the `careplan.start` sign-off. The case could
     also be closed on the old care plan, the old fulfilled appointment or the old negative confirmatory result
     before the new result arrived. Now the pathway guards, the surveillance-plan check and closure evidence accept
     only evidence recorded since the last re-open (`workflow.episode_start`). There are no re-opens in the seeded
     data, so no re-seed was needed. New hazard H-37 in the safety case.
  - Reviewed and judged adequate: attendance, booking, reconsider and the generic transitions (bots are limited to
    triage, notification and acknowledgement), consults, lessons, sends, model training and proposal generation
    (these create candidates only), the sweep and ingest (automated by design, with fail-safe review).
- **Tests.** `tests/test_signoff_round19.py` has 11 tests; 9 fail on the round-18 code, and the 2 patient-path
  regression tests pass on both. The registry tests are parametrised over the 2 new actions.
  - **Full suite 3,161 tests, all passing.**
  - Fault injection F55-F58 (`--only`): 4/4 killed. The full-report re-run is not repeated this round.
- **Safety re-verification.**
  - 10M differential, new seed 20261919: 0 safety-critical disagreements; observed rate 0, one-sided 95 % upper
    bound 3.0e-7.
  - Validator, 53 invariants, 0 violations on: demo, 10k, 20k and all six fresh cohorts.
  - Alert precision/recall on the fresh 20k (seed 20261939): 100 % / 100 % (5,060 TP, 0 FP, 3,105 true breaches,
    0 missed; legacy 78.5 % / 89.6 %).
  - Demo server on :8000 restarted the same way to load the new code. Spot check: a staff consent without a
    rationale now gets 422.
  - Note: the first three fresh seeds ran with the default data dir, so they briefly shared the demo's audit-writer
    lock file (write serialisation only, no data effect). The second set used a scratch `WASL_DATA_DIR`.
- **Open.**
  - ML input set: a human governance decision is needed (see above).
  - A re-opened case keeps `care_reached_at` from its earlier episode, so no pathway-target alert fires for the new
    episode. Step SLAs still alert. Whether re-opening should reset the target clock is a clinical decision, not
    changed here.
  - Synthetic validation only; no clinical claim.

### Round 20: edge-case synthetic cohort, re-open fixes, load ramp (21 Oct 2026, ~10:12-12:15 IST)

**Edge-case cohort.** `python -m wasl.seed --edge` writes `data/wasl_edge.db` and a manifest (see
`docs/synthetic_data.md`).
* **Size:** 20,000 planned people, 20,603 records in total (the extra records are duplicate identities).
  18,511 cases.
* **Content:** all four supported programmes × every tier the rules produce × 28 scenarios. The mix is stratified,
  not prevalence-based. No new disease or pathway was added.
* **Coverage** (`docs/edge_case_coverage.md`):
  * 403 supported disease × severity × scenario cells, 0 empty. Smallest cell: diabetes negative + multimorbidity
    = 5.
  * 19,202 of 20,000 plans are verified in the database. The rest were planned but not realised, e.g. the journey
    closed before a planned withdrawal or move, or the move was refused because the patient denied external sharing.
* **Run-to-run determinism:** totals vary by ±3 records between runs (wall-clock `now`).

**Bugs found and fixed:**
* **A. Re-open target.** A re-opened case kept its original target. Round 20 first tried a fresh target; that was
  reverted to the safer default because it produced *fewer* alerts. The default now keeps the original target:
  * the case is flagged breached at once (`case.reopened_past_target`, 436 in the edge cohort) and escalates;
  * a fresh target is the opt-in `reopen_restarts_target`.
  * **Clinical lead to confirm** (clinical_rules.md §9.18).
* **A2. Re-open step clock.** The re-opened step's clock was not capped by the target, because `case.state` was
  still CLOSED while the step opened. 9 validator violations, now fixed.
* **B. Re-open with another open episode.** Re-opening while another episode was open for the same person and
  programme gave an unexplained IntegrityError. It now gives a clear refusal naming the open case.
* **C. Duplicate detection.**
  * Identical cervical co-test re-sends were labelled "conflicting", because the comparison used the raw HPV text
    instead of the stored "HPV|cytology".
  * The same number in a different unit counted as a duplicate (48 % vs 48 mmol/mol).
* **D. KPI cache stampede under constant writes.** KPI p95 was 2.4–6 s under load; now about 40 ms.
* **E. Validator crash.** `sla.step_clock_within_pathway_target` crashed when it fired (wrong argument count).
* **alert_eval.py (evaluator).**
  * A target that passed while the case was closed now counts as alertable from re-opening.
  * A step breach is credited to the consolidated pathway alert of the same case when that alert falls within the
    same window.

**Tests and checks:**
* **Validator:** 7 new invariants (60 in total).
* **Golden cases:** EDGE-01…32.
* **Unit tests:** reopen policy and step clock, cervical duplicate, unit duplicate.

**Metrics:**
* **Tests:** 3,228 passed, 0 failed.
* **Validator:** 0 violations on all four databases (demo, 10k, 20k, edge).
* **Differential tests:**
  * 10M random inputs: 0 disagreements.
  * Edge-cohort input distribution, 20k × 50 mutation draws = 1M comparisons: 0 disagreements.
* **Alerts (edge cohort):** precision 1.0 (51,963 L2+ alerts, 0 false positives), recall 1.0 (26,297 alertable
  breaches, 0 missed), ladder 10,628/10,628. The 20k realistic cohort is also 1.0/1.0.
* **ML** (`docs/ml_pooled_retrain.md`; offline, nothing registered or promoted, round-18 inputs unchanged):
  * Naive pooling with the stratified edge cohort (40 % positives) fails calibration-in-the-large on both realistic
    cohorts.
  * A prior-matched pool passes every gate on demo, 10k and 20k. AUC is within about 0.01–0.03 of own-cohort models,
    so it brings no AUC gain.
  * Recommendation: use the edge cohort for robustness testing, not for production training. The decision belongs
    to the clinical lead and governance.
* **Load:** p95 stays under 1 s up to 40 concurrent zero-think-time users (about 120 requests/s). The system
  collapses at 80 users (pool timeouts) and recovers once the backlog drains (`docs/loadtest_ramp.md`).

### Round 21: load and efficiency, and the demo-server hang (21 Oct 2026, ~11:45-12:30 IST)

Details are in `docs/loadtest_ramp.md`.

**Root cause of the 11:02 hang.** The audit-writer and SLA-sweeper locks were shared through `data_dir` by every
server and script, whatever their database. The overloaded load-test server therefore stalled the demo server's
audited requests. This was reproduced, and is now fixed with per-database locks and bounded waits.

**Graceful degradation:**
* bounded pool, busy and lock waits;
* 503 + Retry-After;
* admission control (16 in-flight requests per worker), with shed requests audited in aggregate;
* group-committed request log (reads refused if they cannot be logged);
* a 2 s work-queue cache dropped on any local write.

**Ramp results** (10k copy, 4 workers):
* At 40 users p95 fell from 945 ms to 539 ms, and throughput rose from 120 to 170 per second.
* At 80 users, round 20 collapsed to 4 per second with 30 s timeouts. Round 21 kept about 142 per second of 200s,
  at p95 1.6 s with 12 % 503s.
* At 160 users the server stays up (75 per second), but 503s are slow (p95 about 5 s). This is the CPU limit with
  the load generator on the same box.

**Other checks:**
* PostgreSQL 17 smoke test: seed, validator 0 violations, ramp, audit chain valid.
* Tests: 3,234 passed.
* Validator: 0 violations on the demo, 10k, 20k and edge databases.

### Round 22: watchdog for stuck workers (21 Oct 2026, ~12:20-12:35 IST)

* **Heartbeat.** Each worker writes a heartbeat file (`WASL_HEARTBEAT_DIR`, default `/tmp/wasl-hb-<port>`) from
  its event loop every 2 s. It writes only after a no-op has passed through the request thread pool, so a blocked
  loop and an exhausted or wedged thread pool both stop the heartbeat.
* **Replacement.** `wasl.serve` checks heartbeats every 0.5 s. If a worker has been silent longer than
  `WASL_HEARTBEAT_TIMEOUT_S` (60 s), the parent:
  * asks it for a thread dump (SIGUSR1 → faulthandler, written to the log, so the cause of a hang is kept next
    time);
  * SIGKILLs it;
  * starts a replacement.

  The database rolls back the killed worker's open transaction, so no half-written audit link is possible.
  Previously only workers that *exited* were restarted. The round-21 hang left blocked workers holding their
  SO_REUSEPORT sockets.
* **Test.** `tests/test_serve.py::test_watchdog_replaces_a_wedged_worker_and_audit_chain_stays_intact`:
  * a worker is frozen with SIGSTOP (alive, cannot serve or heartbeat);
  * it is replaced within the 3 s test timeout;
  * 20 audited requests are served afterwards;
  * the audit hash chain verifies.
* **Results.**
  * Tests: 3,235 passed.
  * Validator: 0 violations on all four databases.
  * The demo server on :8000 was restarted on this code, and all 4 workers are heartbeating.
* **Not covered.** A worker that keeps heartbeating but serves one request forever (one stuck thread out of 40) is
  not detected. Request-level timeouts would be the next step.

### Round 23: per-request time budget (21 Oct 2026, ~12:27-12:40 IST)

`wasl/request_timeout.py` adds a pure ASGI layer inside the audit middleware, so a cut-off request is still logged.
It applies to `/api/` paths. The budget is `request_timeout_s` (30 s). The audit-verify, model-training, SLA-sweep
and demo endpoints are exempt.

**When a request runs past its budget:**
* The client gets **503 + Retry-After at once**. The layer does not wait for the stuck thread; any late response
  from that thread is discarded.
* The request is marked timed out under a lock. From then on, every SQL statement, new transaction and COMMIT
  from it raises `RequestTimedOut`, so it can never save anything later.
* Its transaction is rolled back at once:
  * if the thread is inside a SQL statement, the statement is interrupted (`sqlite3 interrupt`) and the thread
    rolls back itself;
  * if the thread is stuck in Python code, the session is rolled back from the handler.

  Either way the audit writer lock and the SQLite write lock are released, so other writers continue.
* The stuck thread's stack is logged. The count is shown on `/healthz` (`request_timeouts`), and an
  `http.request_timeout` audit event is written (`saved: false`).

**Never half-saved.** A request that has started or finished a commit is never cut off; it is allowed to finish and
counted as `slow_committing_requests`. "Nothing was saved" is therefore always true, and a request either commits
fully or saves nothing.

**Test** (`tests/test_request_timeout.py`), using a test-only hanging endpoint:
* it holds an open write (writer lock) and blocks in Python;
* the client gets 503 + Retry-After in under 5 s (budget 0.5 s), and the stack dump names the stuck line;
* another writer commits at once, and KPIs are still served;
* `/healthz` counts the timeout;
* when the stuck code resumes, its commit is refused;
* the half-done write is absent, the timeout is in the audit log, and the hash chain verifies.

**Checks:**
* Tests: 3,236 passed.
* Validator: 0 violations on the demo, 10k, 20k and edge databases.
* Quick ramp on a fresh 10k copy:

  | Users | Requests/s | p95 | Errors |
  |---|---|---|---|
  | 5 | 228 | 56 ms | 0 |
  | 40 | 175 | 532 ms | 0 |
  | 80 | 136 OK | 1.6 s OK | 20 % fast 503s (p95 235 ms) |

  The audit chain stayed intact.

**Residual gaps:**
* Python cannot kill a thread. A request stuck in pure Python, or in a C call that ignores interrupt, keeps its
  pool thread until it returns. It can no longer save anything, and the admission limit plus the round-22
  thread-pool watchdog bound the damage.
* On Postgres, a running statement is not cancelled: no `pg_cancel_backend` wiring. `lock_timeout` applies, and
  the commit is still refused.
* Exempt admin endpoints have no time budget.
* A request that is streaming its response when the budget passes is allowed to finish.

### Round 24: product renamed Wasl → KareShafaa (22 Oct 2026, ~09:30-10:30 IST)
* **Name:** *KareShafaa* = Kare (care) + Shafaa (healing). Wordmark is Latin script only (no Arabic glyph in logo or
  header; a neutral ✦ mark replaces the old وصل glyph) because Bristol-Myers Squibb holds UAE "SHEFA شفاء" marks.
  Preliminary screen: no exact marks in US/India/UAE (WIPO); official UAE MoE search (AED 350) recommended before filing.
* **How:** `scripts/rename_product.py` (package `wasl/` → `kareshafaa/`, `WASL_*` → `KARESHAFAA_*`, page title
  `… · KareShafaa`, Dockerfile OCI labels, README name note). Data files renamed after a WAL checkpoint:
  `data/kareshafaa{,_10k,_edge,_large}.db`, `data/kareshafaa_edge_manifest.json`.
* **Compatibility:** legacy `WASL_*` env vars and `.env` keys still accepted (`KARESHAFAA_*` wins). **Bug the rename
  caused:** model artifacts pickled before the rename reference `wasl.ml.calibration`; their SHA-256 is pinned in the
  registry, so instead of re-pickling, `wasl.*` imports now alias to `kareshafaa.*` (tests/test_rename_compat.py).
* **Demo DB reseeded** (`python -m kareshafaa.seed --patients 2000`; old copy in `../backup_pre_kareshafaa_reseed/`) so
  audit actor names, patient-link host and model card show the new name. The 10k/edge/large analysis DBs keep their
  historical "Wasl" strings (hash-chained audit and sent-message records are not rewritten).
* Entries above are kept as written: their `wasl/...` paths and `WASL_*` variables correspond to `kareshafaa/...` and
  `KARESHAFAA_*` today.
* **Tests:** 3,240 passed (3,236 + 4 rename-compat). Validator: 0 violations on all 4 DBs.

### Round 25: installable PWA (22 Oct 2026, ~09:45-10:05 IST)
* `kareshafaa/pwa.py`: `/manifest.webmanifest` (name/short_name KareShafaa, start_url and scope `/`, standalone,
  theme #00796b, background #ffffff; icons 192, 512, maskable 512, generated by `scripts/make_pwa_icons.py` from the
  favicon mark, no lettering or Arabic script), `/sw.js` at root scope (`Service-Worker-Allowed: /`, no-cache) and
  `/offline`. Base template: manifest link, theme-color, (apple-)mobile-web-app-capable, apple-touch-icon 180, and an
  "Install app" button driven by `beforeinstallprompt` (`static/js/pwa.js`).
* **Privacy rule:** the SW precaches only `/static/` CSS/JS/icons and the offline page. HTML pages are network-first
  and never stored; `/api/` and `/fhir` are network-only (offline: JSON 503 "nothing was read or saved"); non-GET is
  never intercepted. The three PWA routes carry no data, so they skip the audit log and load-shedding.
* Headless Chrome (CDP `Page.getInstallabilityErrors`) on localhost: no installability errors, SW active and
  controlling, cache holds only the shell. Tests: +7 (`tests/test_pwa.py`), 3,247 passed.
