# KareShafaa: one-page summary for judges

**The problem.** Screening finds abnormal results, but people get lost before the specialist visit. In Abu Dhabi's
bowel screening programme (2015–16), only 30.5 % of people with a positive stool test (FIT) had the follow-up
colonoscopy (BMC Cancer 2021, https://link.springer.com/article/10.1186/s12885-021-08623-9). Waiting more than 12
months after a positive FIT is linked to higher odds of advanced-stage cancer (OR 3.22; JAMA 2017,
https://jamanetwork.com/journals/jama/fullarticle/2620087).

**What KareShafaa does.** Every abnormal result (bowel, cervical, breast, diabetes) becomes a case with one
accountable owner, a clinical urgency tier set by signed-off rules, a pathway target date and escalation when a step
is late. Patients get messages in Arabic, English, Hindi or Urdu with a check that they understood. A case closes only
when the specialist visit is recorded or a clinician signs a decision.

**Who uses it.** Care coordinators (work queue), family doctors and specialists (review and outcomes), the clinical
lead (loop impact, approvals, audit), administrators (users, system, About) and patients (installable app).

**Safe by design.**
* A person makes every clinical decision. The rules engine is deterministic and every case shows *why* it has its
  priority; only a clinician can lower a tier, with a written reason, and every override is listed.
* The machine-learning model only orders calls within a tier; it is approved by the clinical lead, checked for
  fairness and can be rolled back.
* Role separation, tamper-evident audit trail, small-group suppression in reports, and a data-protection mapping to
  UAE Federal Law No. 2 of 2019, PDPL, ADHICS and DoH HIE standards.

**Built and tested.** FastAPI + PostgreSQL, HL7 v2 and FHIR R4 in, FHIR R4 out, CapabilityStatement at
`/fhir/metadata`, readiness probe, JSON logs, Docker and docker-compose. 3,600+ automated tests (88 % line coverage) run in CI with dependency and code
security scans; FHIR output validated against R4B models; WCAG 2.1 AA automated check with 0 problems in four
languages; browser checks on five screen sizes; mobile pages 34–61 KB on first load; Locust load test: 100 concurrent users, p95 69 ms on one 8-CPU machine.

**Measuring impact.** Outcomes → Loop impact shows loop closure, days to first specialist visit by tier against
target, lost to follow-up, recall uptake and equity gaps by language, nationality group and region, exportable as
CSV or PDF, plus "why loops fail": what patients said got in the way and where the service itself stalled.
Today these figures come from synthetic records and are labelled so.

**Path to pilot.** 90 days in one Abu Dhabi primary care cluster, bowel screening first, with a go/no-go gate at day
45 and success criteria on safety, loop closure, time to colonoscopy and equity (`docs/pilot_plan.md`). Pricing per
screened patient, per case or per facility, with an ROI method based on published cost-by-stage data
(`docs/business_model.md`).

**Honest limits.** Synthetic data only; the free hosting is outside the UAE; no connection to Malaffi yet; message
delivery is simulated; rule thresholds marked CONFIRM await clinical sign-off; no partner has agreed to a pilot yet.

| Look at | Where |
|---|---|
| Loop impact | Outcomes → Loop impact (clinical lead) |
| Why a case has its priority | any case → "Why this priority" |
| Rule card, overrides, data protection | Approvals → How decisions are made / Human overrides / Data protection |
| Architecture, integrations, Malaffi readiness | Admin → About |
| Why loops fail (barriers) | Outcomes → Loop impact, bottom section |
| Security, performance | `docs/security.md`, `docs/performance.md` |
| Handover package | `docs/handover/README.md`, `docs/handover/KareShafaa_Handover_Package.pdf` |
| Run it yourself (one command) | `bash scripts/handover_example.sh` |
| Self-assessment | `docs/judging_audit.md` |
