# Operating modes, clinical use stages and data classification

Three separate things are easy to mix up. This page keeps them apart.

| Setting | Values | What it controls |
|---|---|---|
| `KARESHAFAA_MODE` (software mode) | `standard` (local development and tests), `production` (Render default: password sign-in only, the role-switcher sign-in and test token API off, write limits on), `public_demo` (legacy read-mostly showcase) | how the software runs |
| `KARESHAFAA_CLINICAL_PROFILE` | `synthetic` (default), `live` | whether real clinical use is claimed. `live` is **refused at startup** while the clinical policy is *placeholder, verify* (`kareshafaa/policy.py`) |
| Clinical use stage (governance, below) | shadow → assisted → live | what clinicians may rely on, decided by the programme, not by a setting |

The role-switcher sign-in (`POST /api/auth/demo-login`, `GET /api/auth/demo-users`) exists for development and
automated tests only; it is off whenever `KARESHAFAA_MODE=production`, and the app will not start with it on in `KARESHAFAA_ENV=prod`.

## 1. Data classification

| Class | Examples in KareShafaa | Where it may live today | Controls |
|---|---|---|---|
| **Restricted – patient identifiable health data** | patient name, MRN, Emirates ID, phone, results, case notes, messages, appointment details | **Nowhere real yet**: synthetic (`SYN-`) records only on the current hosting | role-based access per organisation, audit of every read and change, small-cell suppression in reports, analytics opt-out, retention purge |
| **Confidential – pseudonymous operational data** | case ids, tiers, states, step times, escalation levels, audit event payloads (pseudonymous by design), job health counts | application database | same as above; event payloads carry ids and counts, not names |
| **Confidential – security material** | password hashes, session tokens, secret key, database URL | environment variables and database only | never logged, never shown (Admin → System shows host name only) |
| **Internal – aggregate figures** | impact report, equity figures (suppressed below the threshold), synthetic arms | analysts and clinical lead | suppression; synthetic figures labelled |
| **Public** | source code docs, rule documents, synthetic sample files | repository | no patient data, ever |

## 2. Clinical use stages and stop rules

Values below are **placeholder, verify**; the programme's clinical governance sets the real ones.

| Stage | What happens | Who relies on KareShafaa | Entry criteria | Stop rules (any one → back one stage, review) |
|---|---|---|---|---|
| **Shadow** | real results copied in alongside current practice; KareShafaa sends **no** patient messages; staff act from current systems | nobody; outputs compared with what actually happened | UAE-region hosting, DPIA approved, policy approved (`clinical_profile=live` possible), downtime runbook and drill passed, usability fixes done | a result needing action that KareShafaa did not show (missed by reconciliation); a rule disagreeing with clinician decisions above the agreed rate; any data leak |
| **Assisted** | coordinators use the work queue and escalations; patient messages after content sign-off; current safety nets stay | coordinators, as an extra net | shadow concordance agreed; two drills passed in a row; Monitoring shows jobs healthy for 4 weeks | a critical result handled without read-back; an escalation not raised within 2 sweep intervals; a case closed without evidence or authorised decision; a patient contacted after stopping contact; two failed drills in a row |
| **Live** | KareShafaa is the system of record for follow-up of abnormal screening results | the programme | assisted-stage review signed by the clinical lead and the Clinical Safety Officer (names recorded in `kareshafaa/policy.py` and the safety case) | as assisted, plus any serious incident attributable to the system |

Switching off the advisory model (AI kill switch, Admin → System) is always allowed and is not a stage change.

## 3. Residency and data protection: to confirm before any real data

| Item | Status |
|---|---|
| Hosting in a UAE region (Federal Law No. 2 of 2019, Art. 13) | **to confirm** – current Render/Neon hosting has no UAE region (`docs/deployment_uae.md`) |
| Data protection impact assessment (DPIA) with the provider's DPO | **to do** |
| Lawful basis and patient information for care-coordination messages; consent wording in en/ar/hi/ur | **to confirm** with the provider |
| ADHICS / DoH controls for the chosen hosting | **to confirm** |
| Retention periods (cases, audit, messages, sign-in records) | prototype values in code; **to confirm** |
| Malaffi / HIE integration agreement | **not started** |
| Messaging providers (SMS, WhatsApp) and their data location | **not chosen**; delivery is simulated today |
| Sub-processors list and data-processing agreement | **to do** |
