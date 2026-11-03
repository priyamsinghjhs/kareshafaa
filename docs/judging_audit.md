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
