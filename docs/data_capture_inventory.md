# Data-capture inventory (23 Oct 2026, updated 3 Nov 2026)

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
| Result: ordering clinician, screening site, specimen collected (added 29 Oct 2026) | HL7 ORC-12 (else OBR-16), ORC-21 (else PV1-3), OBR-7 / OBX-14; FHIR ServiceRequest.requester (else Practitioner), ServiceRequest.locationReference (else Encounter), Specimen.collection (else Observation.effective); typed-in `ordering_clinician`, `screening_site`, `specimen_collected_at` | `POST /api/ingest/hl7v2`, `/api/ingest/fhir`, `/resolve`, `POST /api/results` | **`diagnostic_reports.ordering_clinician/screening_site/specimen_collected_at`** (new), **`ingest_messages.*`** (same, kept through match review) | NEW (shown on the case screen patient card, the clinical record test table and the patient record; API + capture test + browser check) |
| Ask a specialist (`/consults`) | answer | `POST /api/consults/{id}/respond` | `consult_requests.response` | OK (inline validation) |
| Queue (`/queue`) | view, programme, search | – | URL | FIXED: filters lost on reload |
| Users: add | username, name, role, site, team, temporary password | `POST /api/admin/users` | `users` | OK |
| Users: change role | role, site, team | `POST /api/admin/users/{id}/role` | `users` | FIXED: button did nothing in Chrome ("role is not a function": inline handler name clashed with the button's ARIA `role` property) |
| Users: deactivate / reactivate | reason | `POST .../deactivate`, `.../activate` | **`users.status_reason/_changed_at/_changed_by`** (new) | FIXED: reason only in the audit log |
| Users: reset password (staff only; added 3 Nov 2026) | – (one-time temporary password generated, shown once), optional sign-in help request id | `POST .../reset-password` | `users.password_hash/must_change_password/password_changed_at`, all sessions ended, audit `user.password_reset` (no password), help request → done | UPDATED: refuses patient accounts (patients reset through their coordinator); button hidden on patient rows |
| Sign-in help requests panel (admin only; added 3 Nov 2026) | close note (≥ 5 characters) | `GET /api/signin-help?audience=staff`, `POST /api/signin-help/{id}/close` | **`signin_help_requests.status/handled_at/handled_by/outcome_note`** (new table) | NEW (API + capture test + browser check) |
| Governance: content sign-off (lead, translator) | note | `POST /api/governance/content/signoff` | `content_approvals.note` | FIXED: note not shown; minimum length in the UI (3) did not match the API (5) |
| Governance: model approve / reject / rollback, proposal decide | note | `/api/governance/...` | `model_versions.decision_note`, `rule_proposals.decision_note` | FIXED: note not shown; same length mismatch |
| Outcomes → Loop impact (lead, analyst, admin; added 3 Nov 2026) | programme filter; Download CSV; Print or save as PDF | `GET /api/analytics/impact?programme=&format=json\|csv` | nothing stored (filter in the URL); each CSV export is recorded in the audit log as `analytics.impact_export` | NEW, read-only (API test: labels, suppression, access, export audit) |
| Case screen → Why this priority (added 3 Nov 2026) | – (read-only explanation of rule, tier, target and clinician changes) | part of `GET /api/cases/{id}` (`why`) | read from `cases.rule_id/tier/review_log` and `docs/clinical_rules.md` | NEW, read-only; the tier change itself is the existing clinician review form (`POST /api/cases/{id}/review`, reason required) |
| Governance → How decisions are made, Human overrides, Data protection (added 3 Nov 2026) | – | `GET /api/governance/rule-cards`, `/overrides`, `/data-protection` | read from the audit log and configuration | NEW, read-only |
| Admin → About the system (added 3 Nov 2026) | – (OpenAPI download) | `GET /api/admin/about`, `/api/admin/about/openapi.json` | – | NEW, read-only, administrators only |
| Getting-started guide (all staff roles; added 3 Nov 2026) | "Got it", "Show the guide" | – | browser `localStorage` only (`ks-guide-<role>-v1`), nothing sent to the server | NEW |

## Patients (`/patients`, `/patients/new`, `/patients/{id}`; added 29 Oct 2026)

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Register a patient | given / family name, date of birth, sex, identity document (type, number, issuing country), nationality, language, mobile, email, WhatsApp, smartphone, preferred channel, address, city, emirate, region, health centre, GP, MRN, consent to be contacted + note | `POST /api/patients` | **`patients.birth_date/nationality/phone/email/address_line/city/emirate/preferred_channel/registration_source/registered_at/registered_by`** (new), `patients.*`, `consents` | NEW (API test + browser check) |
| Possible duplicate: create anyway | reason (≥ 10 characters) | `POST /api/patients` (`create_anyway_reason`) | **`patients.duplicate_override_reason`** (new), audit | NEW |
| Register from an unmatched result | as above, pre-filled from the message | `GET /api/patients/prefill`, `POST /api/patients` (`from_message_id`) | **`ingest_messages.patient_demographics`** (new), `patients`, `diagnostic_reports`, `cases` | NEW |
| Edit personal details | names, date of birth, sex, identity document (type, number, issuing country), nationality, language | `POST /api/patients/{id}/demographics` | `patients` (+ `updated_at/updated_by`) | NEW |
| Edit contact details | mobile, email, WhatsApp, smartphone, preferred channel, address, city, emirate, region | `POST /api/patients/{id}/contact` | `patients` | NEW |
| Edit health centre / GP | health centre, GP | `POST /api/patients/{id}/care` | `patients.managing_org_id/gp_id` | NEW |
| Patient app account: send code | channel (SMS / in person) | `POST /api/patients/{id}/account-invite` | **`patient_invites`** (new: hashes, last 4, channel, expiry), `users` (patient role, no password), `communications` (simulated SMS, code masked) | NEW |
| Cancel an activation code | reason | `POST /api/patients/{id}/account-invite/revoke` | **`patient_invites.revoke_reason/revoked_at/revoked_by`** | NEW |
| Insurance (record, and optional at registration) | insurer, plan / network, member / card number, cover end date (self-pay clears the policy fields) | `POST /api/patients/{id}/insurance`, `POST /api/patients` | **`patients.insurance_payer/insurance_plan/insurance_member_id/insurance_valid_to`** (new) | NEW (API + capture test + browser check; eligibility warning on record and case screen) |
| Next of kin and guardian (record, and optional at registration) | name, relationship, phone, legal guardian, proxy consent (may we discuss care with them?) + how it was given (≥ 10 characters) | `POST /api/patients/{id}/contacts`, `POST /api/patients` | **`patients.nok_name/nok_relationship/nok_phone/nok_is_guardian/proxy_note`** (new), `consents` (scope `proxy_contact`) | NEW (API + capture test + browser check) |
| Activate account (`/activate`, en / ar / hi / ur) | code or link, date of birth, password ×2 | `POST /activate` (pre-login CSRF) | `users.password_hash`, `patient_invites.used_at/failed_attempts` | NEW (code and date kept on error, password never echoed) |
| Sign-in page links (`/login`, en / ar / hi / ur; added 3 Nov 2026) | language, “Forgot username?”, “Forgot password?” | `GET /login?lang=` | – | NEW (RTL for ar / ur; error messages translated) |
| Forgot username (`/forgot-username`, patient) | date of birth, document (Emirates ID / passport) last 4, registered mobile | `POST /forgot-username` (pre-login CSRF) | nothing typed is stored; **`login_attempts`** (device and record counters), audit `signin_help.patient_username` (outcome and reason only) | NEW (same answer when nothing matches; username shown only on a full match – CONFIRM) |
| Forgot password (`/forgot-password`, patient) | as above | `POST /forgot-password` | **`signin_help_requests`** (new table: patient, language, due time), audit `signin_help.reset_requested` | NEW (always the same answer; request shows in the coordinator queue panel and on the patient record) |
| Coordinator: password reset code (patient record, account section) | how the patient was verified (in person with ID / phone call with checks / video call with ID), channel | `POST /api/patients/{id}/account-invite` (`verify_method`; active account → reset code) | `patient_invites` (purpose `reset`, hashes only), **`signin_help_requests.verify_method/invite_id/status`**, `communications` (simulated SMS, code masked) | NEW (coordinators only; GP keeps activation codes only) |
| Coordinator: close a reset request without a code | note (≥ 5 characters) | `POST /api/signin-help/{id}/close` | `signin_help_requests.outcome_note/handled_*` | NEW |
| Reset password (`/reset-password`, en / ar / hi / ur) | code or link, date of birth, password ×2 | `POST /reset-password` (pre-login CSRF) | `users.password_hash/password_changed_at`, `patient_invites.used_at/failed_attempts`, all sessions ended, audit `patient.password_reset` | NEW (single use, expiry, 5 wrong tries revoke; activation codes refused here) |
| Forgot password (staff, knows username) | username | `POST /forgot-password` (`who=staff`) | **`staff_password_resets`** (hashed token only; one active per user), outbound email (raw token not stored), audit `signin_help.staff_reset_requested` (no typed values) | NEW (always the same generic answer; 30-minute single-use link; @example.com skipped) |
| Staff set a new password from the email link | new password ×2 | `POST /staff-reset-password` (pre-login CSRF) | `users.password_hash/password_changed_at/must_change_password`, all sessions ended, `staff_password_resets.used_at`, audit `user.password_reset` (`method=email_link`) | NEW (password policy; optional confirmation email; no forced change) |
| Forgot username / password (staff, cannot use email) | name, role, organisation, contact, what is needed, note | `POST /forgot-username`, `POST /forgot-password` (`who=staff&via=admin`) | **`signin_help_requests.full_name/role_text/org_text/contact/kind/note`** | NEW (validation inline; per-device hourly limit; admins only see it) |

## Recall and care summaries (`/recalls`, `/summaries`, case screen; added 29 Oct 2026)

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Surveillance care plan: review / re-test due | date (default from `SURVEILLANCE_MONTHS`, must be in the future) | `POST /api/cases/{id}/care-plan` (`review_due_on`) | **`care_plans.review_due_on`** (new) | NEW |
| Case closure → next screening due | (derived from closure + rule) | `POST /api/cases/{id}/close` | **`cases.next_screening_due`** (new), **`recalls`** (new table) | NEW |
| Send recall invitation (one / selected) | – | `POST /api/recalls/{id}/invite`, `POST /api/recalls/invite` | `recalls.status/invited_at/invite_count/last_invite_channel`, `communications` (simulated) | NEW |
| Care summary to the GP (automatic) | – (written at care start / closure) | `POST /api/cases/{id}/care-plan`, `POST /api/cases/{id}/close` | **`gp_summaries`** (new table: content JSON + text) | NEW |
| Acknowledge a care summary (`/summaries`, case timeline) | note (optional, ≤ 1000) | `POST /api/gp-summaries/{id}/ack` | **`gp_summaries.acknowledged_at/acknowledged_by/ack_note`** | NEW |
| Move appointment | new time, reason, barrier (if patient asked), note, beyond-target reason | `POST /api/cases/{id}/appointment/reschedule` | **`appointments.cancel_* / rescheduled_from_id`** (new), new booked row, `communications` | NEW |
| Cancel appointment | reason, barrier (if patient asked), note | `POST /api/cases/{id}/appointment/cancel` | **`appointments.cancel_*`**, case → referral_accepted | NEW |
| Appointment reminder (manual / sweep) | – | `POST /api/cases/{id}/appointment/remind`, sweep | **`appointments.reminder_sent_at`**, `communications` | NEW |
| Cancel a recall | reason (≥ 5 characters) | `POST /api/recalls/{id}/cancel` | **`recalls.closed_note/closed_by`** | NEW |

## Patient app (`/patient`, en / ar / hi / ur)

| Field | Endpoint | Stored in | Status |
|---|---|---|---|
| Display language | **`POST /api/me/display-language`** (new) | **`users.ui_language`** (new) | FIXED: choice lost on next visit (clinical comms language `patients.language` unchanged) |
| Question for the clinician | `POST /api/me/consult` | `consult_requests.question` | FIXED: draft lost on language switch; too-short text gave no message |
| Teach-back answers, confidence | `POST /api/me/teach-back` | `questionnaire_responses` | FIXED: answers lost on language switch |
| My contact details: mobile, WhatsApp, email, preferred channel, address, city, emirate, region (added 29 Oct 2026) | **`POST /api/me/contact`** (new; own record only, same rules as the staff form), `GET /api/me/profile` | `patients.phone/email/address_line/city/emirate/region/preferred_channel/whatsapp`, audit `patient.self_contact_updated` | NEW (draft kept on error and on language switch; API + capture test + browser check in ar) |
| My appointments and results (read only) | **`GET /api/me/profile`** (new) | from `appointments`, `diagnostic_reports` | NEW (patient wording in en/ar/hi/ur; an actionable result shows only after the team has told the patient) |
| Decline + optional reason | `POST /api/me/decline` | `consents.note`, `lessons.detail` | OK (busy state, inline error) |
| Ask for a screening / recall / follow-up appointment: site, days, times, preferences, note | `POST /api/me/requests/screening`, `POST /api/me/requests/follow-up`, `POST /api/me/requests/{id}/withdraw` | `patient_requests.preferred_org_id/preferred_days/preferred_times/preferred_text/note/language` | NEW (API + capture test; shown back on `/api/me/profile` and in the coordinator work list) |
| Ask to change an appointment: reason, preferences | `POST /api/me/appointments/{id}/change-request` | `patient_requests` | NEW |
| Coordinator: confirm / decline a patient request (reason, note) | `POST /api/patient-requests/{id}/confirm`, `.../decline` | `patient_requests.status/decided_*/decline_reason/decision_note/confirmed_start` | NEW (capture test: decline note shown back) |

## Read-only roles
Analyst: no data-changing controls offered (checked in the browser). Patients never see clinician notes.

## Cross-cutting fixes
Dialogs keep typed values on a failed save and show the error inline; every form shows a friendly field message
("Please check note: at least 10 characters"), disables its button while saving (no double submits), and the new
columns are audited and added by the safe start-up migration (verified on PostgreSQL).

### Identity documents (passport, GCC national ID, other)

| Form | Fields | Endpoint | Stored in | Status |
|---|---|---|---|---|
| Register a patient / Edit personal details: **Identity document** | document type (Emirates ID, Passport, GCC national ID, Other), number, issuing country | `POST /api/patients`, `POST /api/patients/{id}/demographics` | Emirates ID → `patients.national_id` (check digit validated, hard duplicate block); passport / GCC / other → **`patients.id_type/id_number/id_country`** (new, nullable; stored upper-case without spaces or dashes, 5–20 letters and digits) | NEW (`tests/test_identity_documents.py`, `test_zz_data_capture`, browser check `id_type_passport`) |

Rules (server and form match): the ID stays optional. The Emirates ID field uses a numeric keypad and inserts the dashes while typing (784-YYYY-NNNNNNN-C). The other types use a text keyboard with capitals. A passport's issuing country defaults to the nationality. A GCC national ID must come from Saudi Arabia, Kuwait, Bahrain, Qatar or Oman. If another record has the same type, country and number, registration shows a strong possible duplicate and needs the audited "create anyway" reason (`patient.duplicate_override`). An edit that creates the same clash is saved with a warning and audited as `patient.duplicate_document`. Search matches the exact document number. The FHIR Patient gives MRN, Emirates ID (NI), passport (PPN) or GCC ID (NI) identifiers, with the issuing country as the assigner. Mobile numbers accept international `+` formats (e.g. +62 812 3456 7890).

Phone check (`scripts/eid_mobile_e2e.py`, Chrome on Android emulation, 412×915, against a local copy, never the live site). It covers:
- typing an Emirates ID slowly and fast;
- backspacing over a dash;
- editing or inserting digits in the middle;
- pasting with or without dashes and spaces;
- the check-digit error shown under the field, with values kept;
- a full registration, then the record, search with and without dashes, and the audit entry;
- refusal of a duplicate Emirates ID;
- editing the Emirates ID;
- fields and hints staying clear of the bottom nav.

`tests/test_eid_input.py` runs the same form helpers in node. It also checks that every `pattern` attribute compiles with the `v` flag that Chrome uses.

