# Self-assessment against the Future Health Challenge 05 judging criteria (3 Nov 2026)

Honest 1–5 scores by the team, before and after this round of work. Evidence is in the repository; nothing here has
been reviewed by the judges or by any health authority.

| # | Criterion | Before | After | Main evidence after | Gaps that remain |
|---|---|---|---|---|---|
| 1 | Health impact and clinical relevance | 4 | 4.5 | Loop impact report for the clinical lead: loop closure, days to first specialist visit by tier against target, lost to follow-up, recall uptake, equity by language, nationality group and region; CSV and PDF export (`services/impact.py`, Outcomes → Loop impact). Problem sized with published Abu Dhabi data (`docs/pilot_plan.md`) | All figures come from synthetic records; no real-world outcome yet. Rule thresholds still need clinical sign-off (CONFIRM items in `docs/clinical_rules.md`) |
| 2 | Technical depth and architecture | 4 | 4.5 | FHIR R4 CapabilityStatement at `/fhir/metadata`; HL7 v2 and FHIR inbound with status counts; architecture in `docs/architecture.md` and Admin → About; public API docs switched off, admin-only API reference and OpenAPI download | No live connection to Malaffi or a real laboratory; FHIR profile not validated against a UAE implementation guide |
| 3 | Working solution and product quality | 4 | 4.5 | Every role has a short getting-started guide on its home page; new pages share the same loading, empty and error states; checked on 5 screen sizes with no overflow or small tap targets; Arabic right-to-left patient app | Patient messages are simulated (no SMS/WhatsApp gateway); single sign-on not built |
| 4 | Responsible AI, safety and data governance | 4 | 4.5 | "Why this priority" on every case (rule, source, target, clinician changes); rule card and model card; register of human overrides with reasons; fairness gates on the model; data-protection mapping to Federal Law No. 2 of 2019, PDPL, ADHICS and DoH HIE standards; retention table and purge of sign-in data only; audit chain check | Mapping is the team's reading, not a legal opinion; no external audit or ADHICS assessment; ADHICS controls only partly met |
| 5 | Scalability and deployment readiness | 3 | 4 | Dockerfile and `docker-compose.yml` (app + PostgreSQL); `/healthz` and `/readyz`; JSON logs; background job design (`docs/background_jobs.md`); Locust results (100 users, 4 workers, p95 69 ms, `docs/loadtest_locust.md`); emirate profiles; UAE cloud and on-premises guide (`docs/deployment_uae.md`) | Free hosting is outside the UAE (synthetic data only); no multi-server run tested; emirates other than Abu Dhabi listed but not configured |
| 6 | Commercial viability and path to pilot | 1.5 | 3.5 | 90-day pilot plan for one Abu Dhabi primary care cluster with KPIs, gate and stakeholders (`docs/pilot_plan.md`); pricing options, cost to run and ROI method with sourced stage costs (`docs/business_model.md`); one-page summary (`docs/judge_summary.md`) | No partner, letter of intent or price validation; prices and pilot targets are assumptions; no UAE cost-by-stage data found |

## Round 2 (4 Nov 2026): optimisation on all fronts

Same scale as below (5 needs outside evidence, which no round of prototype work can supply). "R1" is the score
after round 1; "R2" counts only what is built, tested and committed in round 2.

| # | Criterion | R1 | R2 | Round 2 evidence | Still missing for a 5 |
|---|---|---|---|---|---|
| 1 | Health impact | 4.5 | 4.5 | "Why loops fail" on Loop impact: patient-reported barriers, patient-side vs service-side failure events, suppressed, CSV (`services/impact.py`); safety-case change review (`docs/clinical_safety_case.md` §6); handover test results state which gains are simulated assumptions | Real-world outcome data; clinical sign-off of CONFIRM values |
| 2 | Technical depth | 4.5 | 4.5 | FHIR R4B model validation of every exported resource + required status value sets; HL7 conformance tests; CI workflow (tests + coverage 88 %, pip-audit, bandit, axe); 11 additive indexes; N+1 removed (recalls 206 → 7 SQL statements on Postgres) | Validation against a UAE/Malaffi implementation guide; live laboratory feed |
| 3 | Working solution and product quality | 4.5 | 4.5 | WCAG 2.1 AA axe-core check on 28 page views in en/ar/hi/ur, 26 → 0 problems, keyboard focus check, in CI; translation completeness test (22 tables); mobile cold load 100–234 KB → 34–61 KB, LCP on the case page 1.9 s → 1.1 s (slow 4G, 4× CPU); start-up 3.4 s → 2.4 s; cached "starting" page for slow wake-ups | Usability testing with real coordinators and patients; real messaging gateway |
| 4 | Responsible AI, safety and governance | 4.5 | 4.5 | CSP, HSTS, Permissions-Policy, COOP/CORP on every response; bandit high/medium fixed; pip-audit clean; STRIDE threat model (`docs/security.md`) | External penetration test and ADHICS assessment; CSP still needs `'unsafe-inline'` for scripts |
| 5 | Scalability and deployment readiness | 4 | 4.5 | Query profiling on PostgreSQL 16; gzip and immutable caching; lazy ML import; CI; one-command reproducible example verified from a clean clone (`scripts/handover_example.sh`) | Multi-server run; UAE-region hosting |
| 6 | Commercial viability and path to pilot | 3.5 | 3.5 | Handover package (`docs/handover/`, PDF) with participants, review lead role, review dates relative to start, 60-minute handover agenda and sign-off table | Partner, letter of intent, validated pricing |


## Before: what was missing

1. **Impact:** outcomes showed simulated before/after and live KPIs, but no programme-level loop report with time to
   specialist against target, lost to follow-up or an equity view in one place.
2. **Technical depth:** no FHIR CapabilityStatement; FastAPI `/docs` open to the public; no architecture document;
   no integration status or Malaffi readiness view.
3. **Product:** first-run guidance only through the guided walkthrough, which is off on the hosted (production) service.
4. **Responsible AI:** the rules engine was deterministic but the case screen did not say which rule set the tier;
   no rule card, no override register, no data-protection mapping, no retention settings.
5. **Scalability:** Dockerfile and Render only; no compose file, readiness probe, structured logs, job design note,
   emirate configuration or UAE deployment guide.
6. **Commercial:** no pilot plan, business model or judge-facing summary.

## How the scores were set

* 5 = complete for a pilot with outside evidence; 4 = complete in the prototype with tests; 3 = partly built or
  documented only; 2 = idea only; 1 = absent.
* "After" counts only what is built, tested and committed in this repository.
