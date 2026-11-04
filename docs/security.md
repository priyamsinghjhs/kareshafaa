# KareShafaa security overview and STRIDE threat model

Round 2. This covers the web application in this repository (FastAPI, SQLite locally, Postgres in hosting).
Facts below are taken from the code; file references are given so a reviewer can check them.

## Controls in place

| Area | Control | Where |
|---|---|---|
| Passwords | argon2id hashes only; no API returns a hash | `kareshafaa/accounts.py` |
| Login abuse | lockout after `login_max_failures` (5) failures in the window, for `login_lockout_minutes` (15) | `kareshafaa/config.py`, `api/auth.py` |
| Sessions | server-side sessions, 30-minute idle expiry plus an absolute TTL | `config.py` (`session_idle_minutes`, `token_ttl_minutes`) |
| Cookies | session cookie `HttpOnly; SameSite=Lax`; CSRF cookie `HttpOnly; SameSite=Strict`; `Secure` over https / in production | `api/auth.py` (`cookie_secure`) |
| CSRF | per-session token checked on every state-changing request | `main.py` (`csrf_and_session_clock`) |
| Authorisation | role permissions (`security.PERMISSIONS`) on every API route; page access (`ui.PAGE_PERMS`); organisation scoping of patients and cases | `security.py`, `ui.py`, `services/queue.py` |
| Case privacy | patient-level views (patient record, case clinical record, FHIR export) omit every case the reader may not open, and rows recorded under such a case; only a neutral "other care episodes exist" flag remains (patient record) or a count (case record) | `services/access.py` (`case_visible`), `services/patients.py`, `services/clinical_record.py`, `fhir.py`, `tests/test_case_privacy.py` |
| Audit | every request audited; hash-chained, append-only log with `verify_chain`; a read whose audit write fails is refused (503) | `audit.py`, `main.py` |
| Browser headers (new) | CSP, Permissions-Policy, COOP, CORP, X-Frame-Options DENY, nosniff, Referrer-Policy no-referrer; HSTS (1 year) over https | `main.py` (`security_headers`), `tests/test_security_headers.py` |
| Caching | `Cache-Control: no-store` on every non-static response, so patient data is never cached by browsers or proxies | `main.py` |
| API surface | OpenAPI / Swagger / ReDoc disabled in the app | `main.py` (`docs_url=None`) |

### Content-Security-Policy

```
default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline';
img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self';
manifest-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'
```

Every resource is same-origin, so no third-party origin is allowed. **Known gap:** templates still contain
inline `<script>` blocks (18 templates) and inline event handlers (36), so `script-src` keeps `'unsafe-inline'`.
All dynamic HTML goes through `W.esc` and Jinja autoescaping. Moving the inline code into static files and
switching to nonces is listed as follow-up work.

## Scan results (round 2, 4 Nov 2026)

| Tool | Result before | Fix | Result after |
|---|---|---|---|
| `pip-audit -r requirements.txt` | No known vulnerabilities | – | No known vulnerabilities |
| `bandit -r kareshafaa` High | 1: B324 SHA-1 in `audit.lock_key` | It is a lock-file name, not a security use; marked `usedforsecurity=False` | 0 |
| `bandit` Medium | 1: B608 f-string SQL in `bootstrap.py` sequence reset | Table names come from our own metadata; now quoted through the dialect's identifier preparer and the sequence name bound as a parameter | 0 |
| `bandit` Low | 48: B105 (33, string constants named like "password"/"token", e.g. reason codes), B311 (10, `random` used only for synthetic data and jitter), B101 (3 asserts), B110 (2 try/except/pass in best-effort cleanup) | Reviewed; none is a real secret or a security use of randomness | 48 (accepted, reviewed) |

CI runs both scans on every push (`ci/github-actions-ci.yml (copy to .github/workflows/ci.yml to enable GitHub Actions)`).

## STRIDE threat model

Assets: patient identity and results, alert/case state, the audit trail, staff accounts.
Trust boundaries: browser ↔ app (internet), lab/EHR feed ↔ ingest API, app ↔ database, staff roles ↔ each other.

| Threat | Example | Mitigation | Residual risk |
|---|---|---|---|
| **S**poofing | Password guessing; stolen session cookie; a forged lab message | argon2id, lockout, HttpOnly/Secure cookies, idle expiry; ingest requires an authenticated role with the ingest permission | No MFA yet (pilot item); SMS/phone channels can be impersonated outside the app |
| **T**ampering | Editing a case or the audit log; CSRF from another site; altered HL7 in transit | CSRF tokens + SameSite; RBAC on writes; hash-chained audit (`verify_chain`) detects edits; additive-only migrations | Integrity of the feed before it reaches us depends on the sending lab's transport (TLS by the host) |
| **R**epudiation | "I never closed that case" | Every request and every clinical action is audited with actor, time and outcome; human overrides recorded with a reason in the override register | DB admins with direct access could rewrite the whole chain; mitigation is an external chain-head copy (pilot item) |
| **I**nformation disclosure | One clinic seeing another's patients; data in browser caches; clickjacking; referrer leaks of link tokens | Organisation scoping (`visible_patient_ids`, `can_view_patient`), case-level filtering of patient views (`case_visible`), `no-store`, CSP `frame-ancestors 'none'`, X-Frame-Options DENY, Referrer-Policy no-referrer, retention purge | `'unsafe-inline'` scripts mean an XSS bug would not be contained by CSP; escaping is the primary defence |
| **D**enial of service | Request floods; slow DB | Admission control sheds load beyond `max_inflight_requests` with 503 + Retry-After; login rate limits; load tested (docs/loadtest_*.md) | Free hosting tier: cold starts and single instance |
| **E**levation of privilege | A patient or GP calling coordinator/admin APIs | Server-side permission check on every route, page permissions, role-separation tests in the suite; admin-only account management | Mis-configured role grants by an admin; reviewed in the access review (pilot plan) |

## Follow-up (not done in round 2)

* Remove inline scripts/handlers and adopt CSP nonces (drop `'unsafe-inline'`).
* MFA for staff accounts.
* Publish the audit chain head daily to separate storage.
* External penetration test before a live pilot (CONFIRM: owner and budget).
