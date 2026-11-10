<p><img src="kareshafaa/static/brand/logo-horizontal.svg" alt="KareShafaa" width="320"></p>

# KareShafaa – Closing the Screening-to-Care Loop

**Live:** https://kareshafaa.onrender.com (Render free plan; the first request after idle can take ~30–60 s while it wakes up)

KareShafaa turns every actionable screening result into a **tracked case** with exactly one accountable owner, an
SLA clock and an escalation path, and keeps it visible until the loop is closed with evidence. Built for the
Hack2skill **Future Health Challenge 05 – Closing the Screening-to-Care Loop**, covering four programmes: bowel (FIT),
cervical (HPV), breast (mammogram, BI-RADS) and diabetes (HbA1c).

**The problem.** Abnormal screening results often never become care: the patient is not told or does not understand,
the referral is rejected or lost, the appointment is missed, and nobody owns the gap. KareShafaa closes that loop:
result in → owned, timed case → patient contacted and understood → referral, booking and attendance → documented
closure.

> Not a certified medical device. Clinical decisions stay with the treating clinician. All records are fictional
> synthetic data, and the messaging gateway is not connected (messages are recorded, not delivered to phones).

## Features

**Focused screens (care, empathy, timely decisions, clarity)**
* Every clinical home starts with **“Needs you now”**: the five most urgent cases in your scope (overdue first, then
  T0→T3), each with the time left in plain words (“Overdue by 3 days”, “5 h left”), the patient's name and preferred
  language, and **one button** for the next step (e.g. “Tell the patient their result”, “Book appointment”). The button
  opens the case; every existing check and confirmation still applies.
* The case screen opens with a **“Now:” bar** — current step, time left, owner and the one next action — above the
  clinical record. Escalations, critical tiers and clinical-review alerts stay visible; nothing safety-relevant is folded away.
* Navigation keeps the 3–4 screens each role uses daily; secondary screens (e.g. audit trail, recall list, “How decisions
  are made”, admin Outcomes/Advanced) sit under **More** (sidebar and phone bottom bar). Long help texts are one tap
  away (“How this page works”, “How requests work”), and empty request panels are hidden.
* Patient app: the **next step comes first** in large type with one clear button, the full step list is one tap away,
  and an always-visible **“Need help?”** row offers “Send a question” and “Change my appointment” (en/ar/hi/ur, RTL).

* **Minimal interface (Nov 2026):** less instructional copy and a clearer visual hierarchy. Page introductions moved
  into one-tap “Why this page” disclosures; stat tiles are number-first (big figure, 1–2 word label, icon); empty states
  are one short line with an icon; headings use the brand navy and mint marks “on track”, while overdue / critical keep
  their semantic colours, always with an icon and a label. The sign-in screen is the logo, one line and the form. The
  case screen adds a five-stage loop strip (Result → Notified → Understood → Referred → Closed, mapped from the existing
  case states; no new workflow states). The patient result card is a visual pathway: urgency badge and timeframe, what
  it means, the next step (emphasised), who is looking after you (name and role), and a one-line reassurance with
  “More” (en/ar/hi/ur, RTL). Icons come from the inline SVG sprite (`templates/_icons.html`); no webfonts or icon CDNs.
  The footer is one line: KareShafaa is not a certified medical device; clinical decisions stay with the treating clinician.

**Patient summary: contact details and insurance**
* The case screen's **Patient summary** shows phone (tap to call, WhatsApp flag), email, address, preferred language,
  contact preference and **contact consent** (care-coordination consent plus any refused channels) to the roles that
  contact patients (coordinator, GP, specialist, clinical lead). Analysts and admins have no patient views at all.
* **Insurance tile** (FHIR `Coverage`, beneficiary = Patient, payor = payer Organization): insurer, plan, member number,
  valid until, coverage status (active / ends soon / expired / unknown / self-pay) and whether **pre-authorisation** is
  needed; a clean “No insurance on file” state otherwise. The member number is shown in full to coordinators and GPs
  (who book and deal with payers) and the patient; specialists and clinical leads see only the last 4 characters.
  Pre-authorisation is recorded on the patient record (additive column `insurance_preauth_required`).
* Patient app: a read-only **“My insurance”** card next to “My contact details” (en/ar/hi/ur).
* FHIR export per case now includes `Patient.telecom`, the full `Patient.address`, a `Coverage` resource and the payer
  `Organization` (validated against FHIR R4B in the tests).
* Sample data: a one-time data step (`backfill_sample_contact_insurance_v1`) fills **empty** contact and insurance
  fields of `SYN-` synthetic patients only with plausible UAE values (UAE mobiles, `example.com` emails, Abu Dhabi /
  Al Ain / Al Dhafra addresses; Thiqa for Emirati patients, otherwise Daman Enhanced/Basic, ADNIC or GIG/AXA). Plans and
  member numbers are marked synthetic; nothing a person entered is overwritten and non-sample records are never touched.
* **“Has insurance?” Yes / No** (additive nullable column `has_insurance`) on the staff patient form (registration and
  the record's Insurance section). **Yes** shows every policy field (insurer, plan, member number, cover end date,
  pre-authorisation); **No (self-pay)** removes them from the screen, switched instantly without a reload (radio
  buttons in a fieldset, `aria-controls`, the fields use the `hidden` attribute; keyboard and RTL friendly). The server
  applies the same rule whatever is sent: No stores self-pay and **strips** every policy field; Yes **requires** an
  insurer and a member number; not answered clears everything. The summary tile, the record and the patient app's
  “My insurance” card show the full policy for Yes, a compact **“No insurance · Self-pay”** for No and **“Insurance not
  recorded”** when never answered (en/ar/hi/ur). FHIR: Yes → `Coverage` + payer `Organization`; No → a self-pay
  `Coverage` with no policy details; not recorded → no `Coverage`. Older records are read from the insurer
  (insurer → Yes, self-pay → No).
* **Email is editable** by the roles that edit phone and address (coordinator, GP) in the record's Contact details
  (email format checked in the browser and on the server, stored lower-case, change audited as
  `patient.contact_updated` with the field name only), and by patients in “My contact details”. It shows on the case
  summary (with an **Edit** / **Add email** link for those roles) and in FHIR `Patient.telecom`.
* Data step `backfill_sample_email_insurance_answer_v1` (`SYN-` synthetic patients only, empty fields only, audited):
  the first step gave only smartphone users an email, so some sample patients showed “None recorded”; this fills every
  empty sample email with an `example.com` address and records the Yes/No answer (insurer → Yes, self-pay → No).

**Case loop**
* Exactly one accountable owner per open case (enforced in the DB and service layer).
* Deterministic state machine with **SLA tiers T0–T3** (T0 critical, T1 urgent, T2 soon, T3 routine; T0/T1 clocks
  run 24/7, T2/T3 count UAE working days) and an L1–L4 escalation ladder. Handles no-shows, rejected referrals,
  unreachable patients, transfers (two-key), declines and deaths.
* **Critical-result fast lane (T0):** a critical result cannot be triaged until a clinician records the **direct
  contact and read-back** (phone, in person or paged secure message; who gave it, who received it, when; table
  `critical_read_backs`, `POST /api/cases/{id}/critical-read-back`, permission `critical:readback` for GP, specialist
  and clinical lead). Open critical cases without a read-back have their own worklist (**Critical: read-back needed**,
  `GET /api/queue?view=critical`). From the first breach (level 2+), escalations on a critical case go **by phone**
  (`escalations.channel = phone`, additive column), and a critical case can never be closed as `unable_to_contact`.
  No new clinical timeframe: the existing T0 clocks apply (placeholders, see `docs/clinical_rules.md`).
  Staff enter the contact time on the UAE clinic clock (see **Dates and times** below); the page sends it as UTC
  (more than 5 minutes ahead is refused).
* **Dates and times – one clinic clock:** every clinical date/time is **stored as naive UTC** in the database and
  sent by the API as naive UTC ISO text (no offset; an offset sent *to* the API is converted). Everywhere a person
  reads or types one – staff pages, the patient app, SMS/message content, server errors and GP summaries – it is on
  the **UAE clock (Asia/Dubai, UTC+4, no daylight saving)**, whatever time zone the browser or server is in. Staff
  date-time fields are labelled “(UAE)”: 21:00 typed on the case or queue page is 21:00 UAE = 17:00 UTC in the
  database, and the patient is told `dd/mm/yyyy 21:00`. Calendar-date fields (medicine start/end, review dates, date
  of birth) are UAE calendar dates and never shift a day. The pathway target is shown as the UAE date `dd/mm/yyyy` on
  the booking form, exactly as in the server's error; booking after it always needs a reason (≥ 10 characters), and
  once the target has passed the message says so instead of “book earlier”. Shared helpers: `W.utcIsoFromLocalInput`,
  `W.localInputFromUtc`, `W.localInputNow`, `W.dt`, `W.dmy` in `static/js/common.js`; server side
  `kareshafaa/calendar_uae.py`; tests in `tests/test_time_handling.py`.
* **Closure guard:** a case closes only with evidence (care plan, procedure, negative confirmation) or an authorised
  human decision with a reason.
* Clinical rules engine (`kareshafaa/clinical_rules.py`, sources in `docs/clinical_rules.md`): FIT, HPV/cytology,
  BI-RADS, HbA1c/FPG. Anything unknown, conflicting or out of range goes to a clinical review queue at a safe tier.
* Patient records and registration, recall lists, GP care summaries, specialist e-consults, medications and encounters.
* **Recall invitations (Due for recall):** a recall invitation cannot be sent again for 24 hours. Staff who try see a
  popup asking them to wait (“Please wait 24 hours before sending the invite again.”); the server refuses the send too,
  and “Invite selected” skips anyone invited in the last 24 hours without blocking the others.
* **Recorded teach-back stays visible:** once a teach-back is saved it is shown in a card at the top of the case
  (score, understood or not yet clear, by the patient or with a navigator, language, time and the answers). A passed
  check is never offered again, and the server refuses another staff entry (“Teach-back already recorded: the patient
  understood.”); the patient's own “Change my answers” path is unchanged. After a failed check the case shows the last
  result and offers an explicit “Try teach-back again” (every attempt is kept in the history), and the save message says
  whether the patient understood.

**Patient journey** (`/patient`)
* Step tracker showing where the patient is in the loop.
* Confirm an appointment or ask to change it; messages with replies; teach-back check; contact consent.
* **Appointment requests:** patients can ask for a screening, a follow-up or their next screening from a due recall.
  Nothing is booked automatically — requests land in the **coordinator queue**, where they are confirmed or declined
  with an answer-by deadline.
* Patient messages in English, Arabic, Hindi and Urdu (signed-off wording only; otherwise English plus an
  interpreter-supported call).

**Sign-in and accounts**
* Username + password (argon2id), server-side sessions (HttpOnly, SameSite, Secure on HTTPS), CSRF tokens, idle
  timeout, rate limit and lockout, generic errors.
* **Sign-in help:** `/forgot-username`, `/forgot-password` and `/reset-password`; requests appear in the admin
  **sign-in help panel** on Admin → Users. Admins can set a **temporary password**, which must be changed at the
  next sign-in (`/account/password`).
* Admin user management: create, role, site/team, reset, deactivate/reactivate (never hard-deleted).

**Impact and responsible AI**
* Outcomes dashboard and **loop impact report**, including **“Why loops fail”** (patient-reported vs service-side
  barriers, small cells suppressed, CSV export). **Equity figures** by language, nationality group, region and age
  band carry numerator, denominator and a **Wilson 95% interval**; a suppressed cell shows no rate and no interval;
  unfinished cases count as not closed / not within target; each slice shows the **best-to-worst gap**; the median
  days to specialist is labelled as conditional on being seen (`kareshafaa/services/impact.py`).
  The report also shows the latest **synthetic baseline-vs-loop arms** (same generated cohort; per-arm rate with
  Wilson interval, difference in points with an interval, best-to-worst language gap per arm), kept apart from the
  live figures and labelled: differences come only from the generator's assumptions, not observed care
  (`kareshafaa/synth/simulator.py` `arm_comparison`; also in the CSV).
* Governed ML (scikit-learn) used only to order outreach within a tier — never for triage, tier or closure. The
  model actor is refused by the workflow for every case or patient action (move, close, file a result, contact),
  checked by the property test. **AI kill switch:** `KARESHAFAA_ML_ENABLED=false` at deployment, or **Admin → System →
  AI suggestions → Switch off** at runtime (`POST /api/admin/system/ml-switch`, permission `ml:switch`, admin only,
  reason required, audited `ml.switched_off` / `ml.switched_on`). Off clears every open case's score at once and the
  work queue falls back to deadline order; clinical work is not affected.
  Registry with AUC, calibration and fairness gates; promotion needs clinical-lead approval; drift and rollback.
* **Note drafts (advisory):** staff clinical note fields (close with evidence, authorised-closure rationale, care-plan
  description, clinical review, call notes, decline / reconsider / transfer notes, referral summary, consult question
  and answer, consultation notes, escalation and summary acknowledgements, patient-request decline notes) can show a
  short draft from a free hosted healthcare small language model (default InclusionAI Ling 3.0 Flash Sante on
  OpenRouter), run as a small LangGraph graph (build prompt → generate → post-check; `kareshafaa/services/assist.py`,
  `POST /api/assist/suggest`, staff with `case:read` who can open the case; patients 403). The draft appears under
  the field with **Use** (inserts it, editable) or **Dismiss**; nothing is inserted or submitted on its own, and the
  model cannot close, move, assign or message a case. Not offered for patient-app fields, names, contact details,
  IDs, dates, results or medicine name/dose/frequency/route. Only a minimised case context is sent (programme,
  result, urgency, step, the form and its evidence, the typed draft); patient name, MRN, Emirates ID, phone, email,
  address, birth year and staff names are stripped. Off without `KARESHAFAA_SLM_API_KEY`, and off with the AI kill
  switch (deployment or Admin → System); a failing or slow model just shows no draft. Requests are audited
  (`assist.suggestion_requested`: case, form, model, whether a draft came back; never the prompt or the text) and
  limited to 20 per user per minute. Drafts are not clinical advice: check them against the record.
* Transparency pages: **why this priority** (per case), **rule and model cards**, **override register**,
  **data protection** and **data retention** (Governance).
* Lessons-learned engine proposes rule changes that a human must approve.

**Platform**
* HL7 v2 ORU^R01 and FHIR R4 Bundle ingest (idempotent, ACKs, match review); FHIR R4 export per case;
  capability statement at `/fhir/metadata`.
* `/healthz` (liveness and stats) and `/readyz` (readiness); admin **About** page (`/admin/about`) with system
  details and the API reference.
* UI in **English and Arabic (RTL)**; responsive layout for phone, tablet and desktop; installable PWA.
* Security headers on every response (CSP, HSTS, Permissions-Policy, COOP/CORP), append-only **hash-chained audit
  log** of every read and write (`GET /api/audit/verify`), consent checks, organisation-scoped access.

**Privacy, audit and reliability safeguards**
* **Case privacy on patient-level views.** The patient record (`GET /api/patients/{id}`), the case screen's clinical
  record and the per-case FHIR export list only the cases the reader may open (organisation involvement plus the
  patient's sharing consent). Cases outside the reader's access are left out completely (no id, dates, programme,
  step, tier or clinician), as are results, consultations, medicines and investigations recorded under them; a neutral
  line says “Other care episodes exist for this patient. Contact the care team if you need them.”
* **Audit write failures are never silent.** If writing the per-request audit event fails after a change was saved,
  the user still gets their answer; the write is retried once, then the event is kept durably (table
  `audit_fallback_events`, or the append-only file `DATA_DIR/audit-fallback.jsonl` if the database is unreachable).
  **Admin → System** shows an alert with the number of events waiting (admin only), and they are appended to the
  hash-chained trail with their **original timestamps**, marked `reconciled_late`, after every deadline check or at once
  with **Add them to the audit trail now** (`POST /api/admin/system/audit-fallback/reconcile`, permission `user:admin`).
  Reads are never served unlogged: a read whose chain write fails or waits more than 2 s
  (`KARESHAFAA_REQUEST_AUDIT_LOCK_TIMEOUT_S`) is served once its event is kept in the fallback, and refused (503) only
  if it cannot be kept anywhere. The SLA sweep checks open cases read-only first and holds the audit writer lock only
  while it changes cases, so a long sweep no longer turns page loads into 503s.
* **Result-to-case reconciliation (nothing fails silently).** A timed job (after every deadline check) compares the
  source results with the cases: every screening result that needs action must be linked to a case, and every
  processed lab message must have filed a result. Anything missing goes to an owned dead-letter list (table
  `result_reconciliation_items`, owner: care coordination) shown as **Results not in the loop** in the Results inbox
  (`GET /api/ingest/reconciliation`, `POST /api/ingest/reconciliation/run`, `POST
  /api/ingest/reconciliation/{id}/resolve`, permission `results:inbox`). A result is never dismissed: the coordinator
  opens a **clinician-review case** for it (reason `missing_from_loop`, urgent floor, placeholder) or it is closed
  automatically once found linked to a case; a message without a result needs a written note. This is separate from
  the audit-trail fallback reconciliation.
* **Monitoring on Admin → System** (admin only, counts only): last success / failure / run count of each timed job
  (deadline check, retention purge, audit fallback and result reconciliation; table `background_job_status`), results
  without a case, results waiting for a patient match, open escalations, and **Check results now**
  (`POST /api/admin/system/result-reconciliation/run`, detection only). No clinical buttons.
* **One timed-jobs worker across containers.** On PostgreSQL the deadline check, retention purge and audit
  reconciliation run in exactly one process, elected by a session advisory lock (`pg_try_advisory_lock`, key 727001)
  held on a dedicated connection, re-checked before every run and released at shutdown; another container takes over
  within one interval if the holder stops. SQLite keeps the per-host file lock. Needs the **direct (unpooled)**
  connection: a Neon `-pooler` host is mapped to the direct host automatically, other poolers need
  `KARESHAFAA_WORKER_LOCK_DATABASE_URL` (see `docs/background_jobs.md`).
* **Duplicate check at registration without a candidate cap.** Candidates are narrowed in SQL by strong identifiers
  (same Emirates ID blocks; same passport/GCC/other document, phone or email) and by the date of birth (or birth year on
  older records) together with a normalised/phonetic **name key** (additive indexed table `patient_name_keys`;
  Arabic transliteration aware: Mohammed/Muhammad/Mohamed, Al-/El-/Al, Abdul/Abd al, hyphens, spaces, case, accents,
  Arabic letter forms), then ranked by name similarity (also with given and family names swapped). The old check looked
  at only the first 5,000 records of a birth year. The same name with a different date of birth is never a blocking
  match: if it also shares a phone or email it is shown as a warning after saving. Keys are kept in step with name
  changes and built for older records in small background batches after start-up (idempotent; no patient field is
  changed); records without keys are still checked.
* **Strict script policy (CSP).** `script-src 'self'` with no `'unsafe-inline'` (and `script-src-attr 'none'`): every
  page script is a static file under `kareshafaa/static/js/`, server values reach the page in non-executable
  `<script type="application/json">` blocks (`ks-config`, `page-strings`), and buttons in generated markup use
  `data-click="function"` / `data-args` handled by one delegated listener in `common.js` instead of `onclick=`.
  `tests/test_csp.py` fails on any new inline script or `on*=` handler. `style-src` still allows inline styles
  (`style=""` attributes; see `docs/security.md`).

## Roles

Permissions are defined in `kareshafaa/security.py` (`PERMISSIONS`) and page access in `kareshafaa/ui.py`. Example
usernames come from the staff directory (`kareshafaa/synth/directory.py`, `kareshafaa/seed.py`).

| Role | Example username | What they do |
|---|---|---|
| Admin | `admin` | Menu: Users · System · About; **More:** Outcomes, Advanced. Users and sign-in help, System status, Outcomes (read), Advanced tools (deadline-check runner, model training, proposal engine, simulation, configuration), audit-chain verification, About. **No clinical actions.** |
| Clinical lead | `fatima.al.mansoori`, `layla.al.hashimi` | Menu: Overview (escalations and overdue first) · Work queue · Approvals · Outcomes; **More:** Audit trail, Recall list, How decisions are made. Overview, work queue and every clinical action incl. reopening cases; approvals for models, rules and content; outcomes; audit trail (read). **No admin tools** (users, system, training, configuration). |
| Care coordinator | `aisha.rahman` | Menu: Today's queue · Patients · Due for recall · Results inbox. Today's queue (“Needs you now” first) incl. patient appointment requests, outreach and calls, triage/assign/transfer, referrals, patient registration and invites, recall list, results inbox (HL7/FHIR) incl. **Results not in the loop** (reconciliation list). |
| GP (family doctor) | `omar.haddad` | Menu: Needs me · Patients · Care summaries · Ask a specialist; **More:** All my cases, Due for recall. Needs-review queue, confirmatory tests, referrals, care plans, authorised closure, care summaries, patients and recalls, ask a specialist. |
| Specialist | `vikram.mehta` | Menu: Needs me · My cases · Patients · Consult requests. Accept or reject referrals with a reason, booking and attendance, care plans, closure, consult replies. |
| Programme analyst | `analyst` | Aggregate outcomes only (small cells suppressed), read-only models and rules. |
| Medical translator | `translator` | Content sign-off: attests that a translation matches the signed-off English. |
| Patient | `patient.en` (also `patient.ar`, `.hi`, `.ur`, `.tl`) | Own results, journey, appointments, requests, messages and consent. |

Coordinators and clinical leads see programme-wide; other clinical roles see cases their organisation is involved in.
The results inbox (raw lab messages, unmatched results, patient matching) belongs to care coordination
(`results:inbox`); GPs add a result only for a patient they may see, and import clinical history only into such a
record. The guided walkthrough is an administrator tool, on the page and at the API. Work queue, case list, patient
search and the patient record follow the same rule as opening a case: another organisation sees a patient only while
the patient allows sharing.

**Access tests.** `tests/test_rbac_matrix.py` reads every page and API route from the app's router and checks it
against the intended policy in `tests/rbac_routes.py` for all 8 roles and for a visitor who is not signed in, so a new
route fails until it is classified. `tests/test_rbac_edges.py` covers IDOR on every id-taking route (out-of-scope ids
for organisation-scoped roles), a GP without an organisation, patients reaching other patients' data, automated
actors, organisation and role changes mid-session, deactivated users with a live session, and admin / clinical lead
crossing into each other's duties.

**Safety suite ("must never happen").** `tests/safety/` is a small, fast suite marked `safety`; run it on its own with
`TZ=UTC pytest -m safety` (about 30 s; it is also part of the full run). It checks that: no duplicate patient identity
is created silently (concurrent registrations of the same person included - registration holds the audit writer lock
so the duplicate check and insert are serialised); scoped users never list or open cases/patients outside their care
(work queue, case list, search, recalls, single reads, GP without an organisation, withdrawn sharing consent); random
valid and invalid action sequences by random roles never leave any case in a contradictory state (closure data, step
clocks, one open case per patient and programme, stored work-queue SLA thresholds current); every successful change
has a request audit event (or a durable fallback event) and a domain audit event when the case changed; no message is
sent on a channel the patient refused, no automated contact follows a care-coordination withdrawal, and no sign-in code
is texted to a patient who refused SMS; and the audit hash chain verifies and detects a forged row. The global scans
in `tests/safety/invariants.py` run over every row in the database, not just the test's own records.

**Fail-closed writes.** Every clinical write is one transaction: a failure part-way rolls everything back, a refusal
changes nothing, and both leave an audit trail (an unexpected error is audited as a failed request). See
[docs/fail_closed_review.md](docs/fail_closed_review.md); `tests/test_fail_closed.py` injects faults mid-operation,
at commit, in the message gateway and in the work-queue refresh.

## Architecture

* **Backend:** Python 3.11+ (CI on 3.13), FastAPI, SQLAlchemy; SQLite locally, PostgreSQL (Neon) in production via
  psycopg 3. Server: `python -m kareshafaa.serve` (multi-process, watchdog, per-request time budget).
* **Frontend:** server-rendered Jinja templates + vanilla JS, service worker, no build step.
* **Interop:** HL7 v2, FHIR R4 (R4B model validation in tests).
* **ML:** scikit-learn, lazy-loaded; model files also stored in the DB (`model_artifacts`).
* **Note drafts:** LangGraph + langchain-core, calling an OpenAI-compatible chat-completions API with httpx (no local
  model weights, no GPU).
* Details: `docs/architecture.md`, `docs/background_jobs.md`, `docs/performance.md`.

```
kareshafaa/            app: main.py, ui.py, security.py (RBAC), accounts.py, sla.py, statemachine.py,
                       clinical_rules.py, fhir.py, api/ (routes), services/, ml/, comms/, synth/ (synthetic data),
                       templates/, static/
kareshafaa_reference/  independent reference rules engine (differential testing)
tests/                 pytest suite
scripts/               handover example, load tests, Locust, UI/a11y/perf checks, validators, fault injection
samples/               HL7 and FHIR example messages
docs/                  clinical rules, safety case, security, performance, pilot plan, business model, handover/
ci/                    github-actions-ci.yml
deploy/, Dockerfile, docker-compose.yml, render.yaml, DEPLOY.md, Makefile
```

## Run locally

```bash
make setup     # .venv + dependencies
make seed      # synthetic patients, history, trained models (SQLite in data/)
make run       # http://localhost:8000
make test      # pytest
```

Locally (standard mode, no password env var set) random first passwords for staff accounts are written to a
gitignored file outside the repo; set `KARESHAFAA_DEMO_PASSWORD` to choose one instead. Other targets: `make help`
(`seed-large`, `validate`, `test-clinical`, `differential`, `fault-injection`, `ui-smoke`, …).

**Handover example** (fresh synthetic DB, HL7 + FHIR inputs, alerts/SLA/FHIR/audit, fixed-seed evaluation; never
touches `data/kareshafaa.db`):

```bash
bash scripts/handover_example.sh [OUT_DIR]    # default ./handover_run
```

**Docker + PostgreSQL:** put `KARESHAFAA_SECRET_KEY`, `KARESHAFAA_DEMO_PASSWORD` and `POSTGRES_PASSWORD` in a `.env`
file (never committed), then `docker compose up --build` → http://localhost:8000.

**Load test (Locust):** `locust -f scripts/locustfile.py --host http://127.0.0.1:8000 --headless -u 20 -r 5 -t 60s`
(see `docs/loadtest_locust.md`).

**Scale load test (baseline / after):** `bash scripts/loadtest/run.sh` seeds throw-away SQLite databases with
500, 5,000 and 20,000 open synthetic cases in `/tmp/lt` and measures the work queue (1, 10 and 25 users), case page,
dashboard, patient search, recalls and proposal generation (latency, SQL statements, memory); see
[`scripts/loadtest/README.md`](scripts/loadtest/README.md) and results in [`docs/loadtest_scale.md`](docs/loadtest_scale.md).

**Work-queue performance (stored SLA thresholds):** each open case stores the absolute times its current step turns
amber (`sla_amber_at`) and red (`sla_red_at`); the work queue compares them with the current time and filters, counts,
orders and pages in SQL (SQLite and PostgreSQL), so only the requested page is read. The columns are derived data: a
database flush hook keeps them current on every case, step and SLA-rule change, and start-up recomputes them
idempotently (additive migration). Proposal generation reads pending proposals and configuration once per run.
`scripts/loadtest/queue_equivalence.py` checks the SQL queue against the former Python ordering on any database.

**Migration rehearsal (before every deploy that adds tables or columns):** `python scripts/migration_rehearsal.py
--base-ref master` seeds a throw-away SQLite database with the code at that ref (synthetic data), upgrades it with
the current `init_db` twice and fails if any table, column, row or append-only trigger is lost, the audit chain
breaks, or the second run changes anything; `--db-copy FILE` rehearses on a copy of an existing SQLite file. For
PostgreSQL, `--postgres-env NAME --wipe-postgres` uses the URL in the environment variable `NAME` (a throwaway
database only: refused if it equals `DATABASE_URL`; the URL is never printed).

**CI:** the workflow (tests + coverage, pip-audit, bandit, axe accessibility) is kept at `ci/github-actions-ci.yml`;
copy it to `.github/workflows/` to enable it on GitHub.

## Deployment

Render web service (`render.yaml`, Docker) auto-deploys from `master`; data lives in Neon Postgres. Step-by-step
guide: [`DEPLOY.md`](DEPLOY.md). Environment variables (set in the Render dashboard; values are never committed):

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | Neon PostgreSQL connection string (secret). Without it the app falls back to SQLite on a temporary disk and warns in Admin → System. |
| `KARESHAFAA_SECRET_KEY` | Session/token signing key (secret, required in production). |
| `KARESHAFAA_DEMO_PASSWORD` | First password for staff accounts that have none yet (secret; never overwrites a changed password). |
| `KARESHAFAA_MODE` | `production` on Render. |
| `KARESHAFAA_CLINICAL_PROFILE` | `synthetic` (default). `live` is **refused at startup** while the clinical policy (`kareshafaa/policy.py`, version `2026.11-p1`, status *placeholder, verify*) has no named approver and approval reference. Separate from `KARESHAFAA_MODE`. |
| `KARESHAFAA_ML_ENABLED` | `false` switches the advisory outreach-priority model **and the note drafts** off at deployment (AI kill switch). The runtime switch on Admin → System does the same. |
| `KARESHAFAA_SLM_API_KEY` | API key for the note-draft model provider (secret; default provider OpenRouter, free model). Unset = note drafts off. |
| `KARESHAFAA_SLM_BASE_URL`, `KARESHAFAA_SLM_MODEL` | Optional: OpenAI-compatible base URL (default `https://openrouter.ai/api/v1`) and model (default `inclusionai/ling-3.0-flash-sante:free`). `KARESHAFAA_SLM_TIMEOUT_S` (default 12) and `KARESHAFAA_SLM_RATE_PER_MINUTE` (default 20 per user) are also optional. |
| `WEB_WORKERS`, `KARESHAFAA_SWEEP_INTERVAL_SECONDS` | Worker count and SLA-sweep interval. |
| `KARESHAFAA_WORKER_LOCK_DATABASE_URL` | Optional, PostgreSQL only: direct (unpooled) URL for the timed-jobs lock session when `DATABASE_URL` goes through PgBouncer or another pooler (secret). Neon `-pooler` hosts are mapped automatically. |

Neon note: use the **unpooled (direct) connection string** from Neon's *Connect* dialog: the timed-jobs election
uses a session-level advisory lock, which a transaction pooler (PgBouncer) cannot keep for one client.
On first start against an empty database, `python -m kareshafaa.bootstrap` creates the schema and copies in the initial records once; existing data is never reset.
Start-up (`kareshafaa.bootstrap`) takes its **own** advisory lock (key 727003, on a dedicated direct connection that waits up to 120 s), never the timed-jobs lock (727001), and prepares the schema under it. A new instance can therefore finish start-up while the previous instance is still running and holding 727001; sharing that key was why Render deploys failed with `pg_advisory_lock(727001)` / lock timeout.

## Documentation

* Handover package: [`docs/handover/`](docs/handover/README.md) — problem brief, runnable project, task I/O
  agreement, test results, pilot plan, handover session (PDF/DOCX included)
* [`docs/security.md`](docs/security.md) · [`docs/performance.md`](docs/performance.md) ·
  [`docs/loadtest_scale.md`](docs/loadtest_scale.md) ·
  [`docs/pilot_plan.md`](docs/pilot_plan.md) · [`docs/business_model.md`](docs/business_model.md)
* [`docs/clinical_rules.md`](docs/clinical_rules.md) · [`docs/clinical_safety_case.md`](docs/clinical_safety_case.md) ·
  [`docs/human_signoff.md`](docs/human_signoff.md) · [`docs/patient_messages.md`](docs/patient_messages.md)
* [`docs/architecture.md`](docs/architecture.md) · [`docs/synthetic_data.md`](docs/synthetic_data.md) ·
  [`docs/alert_precision.md`](docs/alert_precision.md) · [`docs/ml_calibration.md`](docs/ml_calibration.md)
* [`docs/deployment_uae.md`](docs/deployment_uae.md) · [`docs/BRAND.md`](docs/BRAND.md) · [`ITERATIONS.md`](ITERATIONS.md) (build log)
* [`docs/operating_modes.md`](docs/operating_modes.md) (software modes, shadow → assisted → live with stop rules, data
  classification, residency/DPIA items to confirm) · [`docs/runbook_downtime_and_drills.md`](docs/runbook_downtime_and_drills.md)
  (downtime procedure, monthly critical-result drill, honest free-hosting limits) ·
  [`docs/usability_test_plan.md`](docs/usability_test_plan.md) (English and Arabic; planned, not run)

## Limits and intellectual property

* **Limits:** synthetic records only; free hosting outside the UAE that sleeps when idle (timed escalations pause;
  the audit fallback file is lost on restart); no paging route ("by phone" escalation is a task); message delivery
  simulated; clinical policy `2026.11-p1` is *placeholder, verify* with no named approver (`live` profile refused);
  no Clinical Safety Officer, no accepted residual risk (`docs/clinical_safety_case.md` §3.1); usability test not
  run; baseline-vs-loop figures are synthetic.
* **Intellectual property:** written for KareShafaa by its authors; ownership and licence terms are for the
  repository owner to confirm. There is no LICENSE file, so no open-source licence is granted. Third-party packages
  and the Figtree font (SIL OFL 1.1) keep their own licences. No real or de-identified patient data is in the repo.

## Keeping this README current

**Update this README in the same commit as any change to features, roles/permissions, routes or setup.**
