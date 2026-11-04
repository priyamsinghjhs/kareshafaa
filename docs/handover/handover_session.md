# 6. Handover session (60 minutes)

**Attendees:** Programme Clinical Lead (to be named), receiving technical owner (to be named), product lead, one
coordinator representative. **Pre-read:** `README.md`, `test_results.md`, `examples/example_run_record.md`.

## Agenda

| Time | Item | Material |
|---|---|---|
| 0–5 | Purpose, scope in / out | `problem_brief.md` |
| 5–20 | **Results review**: test suite, rules vs reference, alert precision, simulation (and its assumptions), a11y and security, known limits | `test_results.md` |
| 20–35 | **Walkthrough**: run `scripts/handover_example.sh` live; then the app – coordinator queue, a case with "why this priority" and SLA timer, lead alerts, Loop impact incl. "Why loops fail", patient app in Arabic | `runnable_project.md`, `how_to_run_the_task.md` |
| 35–48 | **Q&A** (checklist below) | – |
| 48–55 | **Decisions to agree** | below |
| 55–60 | **What happens next** and sign-off | below |

## Q&A checklist

- [ ] Can the receiving team run the example from a clean clone without help?
- [ ] Where do secrets live, and who rotates `KARESHAFAA_SECRET_KEY`?
- [ ] Which values are CONFIRM (thresholds, SLA hours, equity flag level) and who signs them?
- [ ] How is a result with an unknown code or identity doubt handled? (human review queue)
- [ ] How is the audit chain verified, and how often?
- [ ] What happens when the app is overloaded or the database is slow? (503 + Retry-After; nothing half-saved)
- [ ] What does the ML model do and not do? (orders within a tier only)
- [ ] Which data protection mappings are the team's reading rather than legal advice?
- [ ] What is simulated (patient messaging, simulation gains)?

## Decisions to agree

| # | Decision | Options | Agreed |
|---|---|---|---|
| 1 | Pilot site cluster and start date | – | |
| 2 | Sign-off owner for rules, SLA hours and patient messages | Programme Clinical Lead (to be named) | |
| 3 | KPI targets (replace ASSUMPTIONS) | as proposed / revised | |
| 4 | Hosting for the pilot | UAE cloud region / cluster data centre | |
| 5 | Messaging gateway (SMS / WhatsApp) | – | |
| 6 | Follow-up work priority: CSP nonces, MFA, external pen test, Malaffi | order | |

## What happens next

1. Receiving team reruns the example and the full test suite on their own machine (target: within 1 week).
2. Clinical sign-off workshop for CONFIRM values.
3. Readiness steps in `pilot_plan.md` (day −56 to −1).
4. Agree the date of the first review (day 30 after go-live).

## Sign-off

| Role | Name | Accepted (yes / with conditions) | Date | Signature |
|---|---|---|---|---|
| Programme Clinical Lead (to be named) | | | | |
| Receiving technical owner (to be named) | | | | |
| KareShafaa product lead | | | | |
