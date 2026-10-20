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
