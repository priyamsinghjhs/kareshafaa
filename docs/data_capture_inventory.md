# Data-capture inventory (23 Oct 2026)

Every data-entry control in the staff and patient UI, the endpoint it posts to, the column that stores it, and the
result of the browser check (`scripts/ui_capture_check.py`, desktop 1280 px + mobile 390 px, Unicode tokens, value
checked in the database AND on the reloaded page) and the API test (`tests/test_zz_data_capture.py`).
Status: **OK** = stored and shown back before this audit; **FIXED** = was lost or not shown, now stored and shown.

## Case page (`/cases/{id}`, `static/js/case.js`) – action forms

| Form (action) | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Clinical review | urgency, note | `POST /review` | `cases.tier`, **`cases.review_log`** (new), `step_tasks.note` | FIXED: when the case was already past "issued" the note went only to the audit log; now kept per review and shown in the timeline |
| Triage | note | `POST /transition` | `step_tasks.note` | OK |
| Notify patient | (button) | `POST /notify` | `communications` | OK |
| Call outcome | outcome, note | `POST /call` | `call_tasks.outcome`, `communications.content` | OK |
| Teach-back (navigator) | q1–q3, confidence, language | `POST /teach-back` | `questionnaire_responses` | FIXED: confidence showed 1 instead of the default 3 |
| Reminder / unreachable | note | `POST /transition` | `step_tasks.note` | OK |
| Decline | note | `POST /decline` | `consents.note`, `lessons.detail` | OK (validation error now inline, text kept) |
| Reconsider | note | `POST /reconsider` | `step_tasks.note` | OK |
| Consultation step | note | `POST /transition` | `step_tasks.note` | OK |
| Ask a specialist | specialist, urgency, question | `POST /consults` | `consult_requests` | OK |
| Refer | organisation, specialist, summary | `POST /referral` | `service_requests` | OK |
| Accept / reject referral | decision, reason code, note | `POST /referral/decision` | `service_requests.reason_code/reason_note` | FIXED: accept note stored but not shown |
| Book appointment | start (Dubai local → UTC), reason after target | `POST /appointment` | `appointments.start`, **`appointments.booking_note`** (new) | FIXED: reason was only in the audit log |
| Attendance | attended, barrier | `POST /attendance` | `appointments.status/barrier_code` | OK |
| Start care | kind, description | `POST /care-plan` | `care_plans` | OK |
| Close with evidence | evidence, note | `POST /close` | `cases.closure_note`, `step_tasks.note` | OK |
| Authorised closure | reason, note | `POST /close` | `cases.closure_reason/closure_note` | OK |
| Order / record confirmatory test | organisation, result | `POST /confirmatory[/result]` | `service_requests` | OK |
| Assign owner | owner, reason | `POST /assign` | `step_tasks.note` | OK |
| Transfer (two-key) + decision | owner, note, accept, note | `POST /transfer[/decision]` | `step_tasks.note` | OK |
| Lesson / delay reason | kind, reason code, detail | `POST /lessons` | `lessons` | FIXED: stored but never shown; now in the timeline |
| Consent | scope, decision, note | `POST /consent` | `consents` | FIXED: note not shown |
| Record death | source note | `POST /patient-deceased` | **`patients.deceased_note/_recorded_at/_recorded_by`** (new) | FIXED: note only in the audit log |
| Correct death record | reason | `POST /patient-deceased/revoke` | **`patients.deceased_corrected_*`** (new) | FIXED: as above |
| Re-open | note | `POST /reopen` | `step_tasks.note` | OK |
| Acknowledge escalation | action taken | `POST /api/escalations/{id}/ack` | `escalations.note` | OK |

## Case page – clinical record

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Consultation note | kind, date, reason, notes, outcome | `POST /encounters` | `encounters` | OK |
| Add medicine | name, dose, frequency, route, start, **end date**, status, reason, note | `POST /medications` | `medication_requests` | FIXED: no end-date field; note stored but not shown |
| Stop medicine | reason | `POST /medications/{id}/stop` | `medication_requests.stop_reason` | OK |

## Other staff pages

| Page | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Results inbox (`/ingest`) | HL7 message, resolve match | `POST /api/ingest/hl7`, `/resolve` | `inbound_messages`, `diagnostic_reports`, `cases` | OK (buttons now disabled while sending) |
| Ask a specialist (`/consults`) | answer | `POST /api/consults/{id}/respond` | `consult_requests.response` | OK (inline validation) |
| Queue (`/queue`) | view, programme, search | – | URL | FIXED: filters lost on reload |
| Users: add | username, name, role, site, team, temporary password | `POST /api/admin/users` | `users` | OK |
| Users: change role | role, site, team | `POST /api/admin/users/{id}/role` | `users` | FIXED: button did nothing in Chrome ("role is not a function": inline handler name clashed with the button's ARIA `role` property) |
| Users: deactivate / reactivate | reason | `POST .../deactivate`, `.../activate` | **`users.status_reason/_changed_at/_changed_by`** (new) | FIXED: reason only in the audit log |
| Users: reset password | temporary password | `POST .../reset-password` | `users` | OK (handler renamed for the same clash risk) |
| Governance: content sign-off (lead, translator) | note | `POST /api/governance/content/signoff` | `content_approvals.note` | FIXED: note not shown; minimum length in the UI (3) did not match the API (5) |
| Governance: model approve / reject / rollback, proposal decide | note | `/api/governance/...` | `model_versions.decision_note`, `rule_proposals.decision_note` | FIXED: note not shown; same length mismatch |

## Patients (`/patients`, `/patients/new`, `/patients/{id}`; added 29 Oct 2026)

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Register a patient | given / family name, date of birth, sex, Emirates ID, nationality, language, mobile, email, WhatsApp, smartphone, preferred channel, address, city, emirate, region, health centre, GP, MRN, consent to be contacted + note | `POST /api/patients` | **`patients.birth_date/nationality/phone/email/address_line/city/emirate/preferred_channel/registration_source/registered_at/registered_by`** (new), `patients.*`, `consents` | NEW (API test + browser check) |
| Possible duplicate: create anyway | reason (≥ 10 characters) | `POST /api/patients` (`create_anyway_reason`) | **`patients.duplicate_override_reason`** (new), audit | NEW |
| Register from an unmatched result | as above, pre-filled from the message | `GET /api/patients/prefill`, `POST /api/patients` (`from_message_id`) | **`ingest_messages.patient_demographics`** (new), `patients`, `diagnostic_reports`, `cases` | NEW |
| Edit personal details | names, date of birth, sex, Emirates ID, nationality, language | `POST /api/patients/{id}/demographics` | `patients` (+ `updated_at/updated_by`) | NEW |
| Edit contact details | mobile, email, WhatsApp, smartphone, preferred channel, address, city, emirate, region | `POST /api/patients/{id}/contact` | `patients` | NEW |
| Edit health centre / GP | health centre, GP | `POST /api/patients/{id}/care` | `patients.managing_org_id/gp_id` | NEW |
| Patient app account: send code | channel (SMS / in person) | `POST /api/patients/{id}/account-invite` | **`patient_invites`** (new: hashes, last 4, channel, expiry), `users` (patient role, no password), `communications` (simulated SMS, code masked) | NEW |
| Cancel an activation code | reason | `POST /api/patients/{id}/account-invite/revoke` | **`patient_invites.revoke_reason/revoked_at/revoked_by`** | NEW |
| Activate account (`/activate`, en / ar / hi / ur) | code or link, date of birth, password ×2 | `POST /activate` (pre-login CSRF) | `users.password_hash`, `patient_invites.used_at/failed_attempts` | NEW (code and date kept on error, password never echoed) |

## Recall and care summaries (`/recalls`, `/summaries`, case screen; added 29 Oct 2026)

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Surveillance care plan: review / re-test due | date (default from `SURVEILLANCE_MONTHS`, must be in the future) | `POST /api/cases/{id}/care-plan` (`review_due_on`) | **`care_plans.review_due_on`** (new) | NEW |
| Case closure → next screening due | (derived from closure + rule) | `POST /api/cases/{id}/close` | **`cases.next_screening_due`** (new), **`recalls`** (new table) | NEW |
| Send recall invitation (one / selected) | – | `POST /api/recalls/{id}/invite`, `POST /api/recalls/invite` | `recalls.status/invited_at/invite_count/last_invite_channel`, `communications` (simulated) | NEW |
| Care summary to the GP (automatic) | – (written at care start / closure) | `POST /api/cases/{id}/care-plan`, `POST /api/cases/{id}/close` | **`gp_summaries`** (new table: content JSON + text) | NEW |
| Acknowledge a care summary (`/summaries`, case timeline) | note (optional, ≤ 1000) | `POST /api/gp-summaries/{id}/ack` | **`gp_summaries.acknowledged_at/acknowledged_by/ack_note`** | NEW |
| Cancel a recall | reason (≥ 5 characters) | `POST /api/recalls/{id}/cancel` | **`recalls.closed_note/closed_by`** | NEW |

## Patient app (`/patient`, en / ar / hi / ur)

| Field | Endpoint | Stored in | Status |
|---|---|---|---|
| Display language | **`POST /api/me/display-language`** (new) | **`users.ui_language`** (new) | FIXED: choice lost on next visit (clinical comms language `patients.language` unchanged) |
| Question for the clinician | `POST /api/me/consult` | `consult_requests.question` | FIXED: draft lost on language switch; too-short text gave no message |
| Teach-back answers, confidence | `POST /api/me/teach-back` | `questionnaire_responses` | FIXED: answers lost on language switch |
| Decline + optional reason | `POST /api/me/decline` | `consents.note`, `lessons.detail` | OK (busy state, inline error) |

## Read-only roles
Analyst: no data-changing controls offered (checked in the browser). Patients never see clinician notes.

## Cross-cutting fixes
Dialogs keep typed values on a failed save and show the error inline; every form shows a friendly field message
("Please check note: at least 10 characters"), disables its button while saving (no double submits), and the new
columns are audited and added by the safe start-up migration (verified on PostgreSQL).
