# Fail-closed review of clinical write paths

Date: 5 Nov 2026 (hardening round). Tests: `tests/test_fail_closed.py`.

## How every write path is built

* **One transaction per request.** Each API request gets one database session (`db.get_db`). Handlers change the
  session and call `db.commit()` once, at the end (`_done` in `api/cases.py`, or an explicit commit). No clinical
  service commits part-way (checked: the only service-level commits are the rejection audit of a blocked
  registration, the failed-activation audit for patient sign-in codes, and retention/maintenance jobs). If anything
  raises before the commit, the session is closed without a commit and the database rolls everything back.
* **Domain audit in the same transaction.** `audit.record` adds the event to the same session, so a change and its
  audit event are saved together or not at all.
* **Derived data in the same transaction.** The work queue's stored SLA thresholds (`queue_store`, `after_flush`
  hook) are written in the flush of the same transaction; a failure there fails the whole request.
* **Checks before changes.** Permission, scope, state-machine, closure-evidence, consent and validation checks raise
  a `DomainError` / `TransitionError` (403 / 404 / 409 / 422) and the request is rolled back.
* **Audit trail of the refusal or failure.** The request audit middleware records every API request with its status
  and outcome (`denied` for 401 / 403, `failure` for other errors). If that write fails it is retried once and then
  kept in the durable fallback (`audit_fallback`). A timed-out request is cut off, rolled back and audited
  (`http.request_timeout`).

## Fault injection

For each path the tests inject a fault (1) in the middle of the operation: the first domain audit write, after its
first changes are in the session; (2) at the end, inside the final commit, after every change has been flushed to the
database transaction; (3) in the message gateway, for paths that message the patient; and (4) in the work-queue SLA
refresh (flush hook). After each fault they check that:

* the request failed (500),
* no table's row count changed and no row of the case or patient changed,
* there is an audit trail of the failed request,
* the same operation then succeeds when retried (nothing half-done blocks it).

Refusals (no closure evidence, an invalid transition, a referral summary that is too short, booking in the wrong
state) are checked the same way: 4xx, no change, and an audit trail.

## Paths and results

| Path | Route | Faults tested | Result |
|---|---|---|---|
| Result notification (communication ladder, call task) | `POST /api/cases/{id}/notify` | middle, commit, gateway, queue | all-or-nothing |
| Call outcome | `POST /api/cases/{id}/call` | middle, commit | all-or-nothing |
| Teach-back | `POST /api/cases/{id}/teach-back` | middle, commit, queue | all-or-nothing |
| Workflow transition | `POST /api/cases/{id}/transition` | middle, commit, queue; invalid target refused | all-or-nothing |
| Referral sent | `POST /api/cases/{id}/referral` | middle, commit, queue; short summary refused | all-or-nothing |
| Referral decision | `POST /api/cases/{id}/referral/decision` | middle, commit, queue | all-or-nothing |
| Appointment book / rebook | `POST /api/cases/{id}/appointment` | middle, commit, gateway, queue; wrong state refused | all-or-nothing |
| Appointment reschedule (+ patient message) | `.../appointment/reschedule` | middle, commit, gateway, queue | all-or-nothing |
| Appointment cancel (+ patient message) | `.../appointment/cancel` | middle, commit, gateway, queue | all-or-nothing |
| Attendance | `POST /api/cases/{id}/attendance` | middle, commit, queue | all-or-nothing |
| Care plan | `POST /api/cases/{id}/care-plan` | middle, commit, queue | all-or-nothing |
| Closure with evidence | `POST /api/cases/{id}/close` | middle, commit, queue; no evidence refused | all-or-nothing |
| Authorised closure | `POST /api/cases/{id}/close` | middle, commit | all-or-nothing |
| Reopen | `POST /api/cases/{id}/reopen` | middle, commit, queue | all-or-nothing |
| Critical-result read-back (fast lane) | `POST /api/cases/{id}/critical-read-back` | middle, commit, queue; triage without read-back refused | all-or-nothing |
| Consent change (care coordination, channel) | `POST /api/cases/{id}/consent` | middle, commit | all-or-nothing |
| Patient decline | `POST /api/cases/{id}/decline` | middle, commit | all-or-nothing |
| Reassign owner | `POST /api/cases/{id}/assign` | middle, commit | all-or-nothing |
| Medication | `POST /api/cases/{id}/medications` | middle, commit | all-or-nothing |
| Contact details edit | `POST /api/patients/{id}/contact` | middle, commit | all-or-nothing |
| Insurance edit | `POST /api/patients/{id}/insurance` | middle, commit | all-or-nothing |
| Result entry | `POST /api/results` | middle, commit | all-or-nothing |
| Lab message ingestion (HL7 v2) | `POST /api/ingest/hl7v2` | middle, commit, queue | all-or-nothing (the retry loop only re-runs on a unique-index conflict, after a rollback) |
| Recall invitation | `POST /api/recalls/{id}/invite` | middle, commit | all-or-nothing |
| AI kill switch (Admin → System) | `POST /api/admin/system/ml-switch` | middle, commit | all-or-nothing (config row, cleared scores, audit event) |
| Reconciliation: open a case for a result missing from the loop | `POST /api/ingest/reconciliation/{id}/resolve` | middle, commit, queue | all-or-nothing |
| SLA sweep (escalations, reminders, queue thresholds) | `POST /api/sla/sweep` | middle, commit, queue | all-or-nothing |

Not fault-tested one by one, but built the same way (one commit at the end of the request, audit in the same
session, failures audited by the middleware): transfers, confirmatory tests, encounters, consults, lessons,
escalation acknowledgement, GP summary acknowledgement, patient self-service (`/api/me/...`), patient requests,
registration and the other record sections, account invitations.

## Gaps found and fixed

1. **A failed write left no audit trail.** An unexpected error inside a request (anything that is not a handled
   refusal) escaped past the request audit middleware: the transaction was rolled back (nothing was saved), but the
   failed attempt was not logged anywhere. The middleware now catches it, logs the error, answers 500 ("this action
   was not completed - reload the page...") and audits the request with outcome `failure` (or keeps it in the
   fallback). Found by the fault-injection tests.

No partial-mutation gap was found: every injected fault rolled back completely, including the stored work-queue
thresholds and the message ladder (call tasks, communication rows).

## Notes

* The simulated message gateway never really sends anything. With a real gateway, a message could be delivered and
  the transaction then fail; the patient would get the message with no record of it. Before a real gateway is used,
  messages should be queued in the transaction and sent after the commit (outbox pattern). CONFIRM: when a real
  gateway is planned.
* A blocked registration commits its `patient.register_blocked` audit event on purpose before refusing (the refusal is
  the record). No patient row is written.
