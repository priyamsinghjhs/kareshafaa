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
| Audit | every request audited; hash-chained, append-only log with `verify_chain`; a request whose chain write fails or waits too long (`request_audit_lock_timeout_s`, 2 s) is answered only once its event is kept in the durable fallback (writes are retried once first), and reconciled into the chain later; a read is refused (503) only if the event cannot be kept anywhere (see below) | `audit.py`, `audit_fallback.py`, `main.py` |
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

### When the audit write fails

The per-request audit event is written after the request's own transaction has committed, so for a state-changing
request the change has already happened when the audit write runs. What happens if that write fails:

1. The user still receives the normal response (the change is saved; telling them otherwise would invite a
   duplicate action).
2. The write is retried once. The chain write waits at most `request_audit_lock_timeout_s` (2 s) for the audit
   writer lock, so a long transaction elsewhere (for example an SLA sweep) cannot hold requests for the full
   5 s writer timeout.
3. If the retry fails, the event is stored in the `audit_fallback_events` table (a plain insert that does not need
   the audit writer lock). If the database itself cannot be reached, it is appended as one JSON line to
   `DATA_DIR/audit-fallback.jsonl` (append-only, `fsync`ed, file mode 0600). If even that fails, the full event is
   written to the error log at CRITICAL level and counted, so it can be recovered from the logs.
4. Admin → System (admin role only) shows how many events are waiting, the oldest one's time, and any that reached
   only the log.
5. The reconciliation job runs after every deadline check, and an admin can run it from the System page. It
   appends each waiting event to the hash-chained trail **with its original timestamp** and
   `detail.reconciled_late = true` (plus `fallback_id`, when it was stored, where, the write error and when it was
   reconciled), and records one `audit.fallback_reconciled` summary event.

Honest limits: until reconciliation runs, the chain does not contain these events (the System page says so). A
reconciled event sits in the chain at the position it was appended, so ordering by `id` differs from ordering by
`ts` for these rows; the `reconciled_late` flag makes that explicit. The fallback table is not itself hash-chained
(its rows are marked reconciled once appended), and on Render's free plan the JSONL file lives on the temporary disk:
an event that only reached the file is lost if the instance restarts before the database is back. An event that
reached only the error log needs manual reconciliation from the logs.

Reads: data is never handed out unlogged. A read whose chain write fails or times out is not retried (that would
double its wait); its event goes straight to the fallback - on SQLite to the JSONL file first, because the
database write lock may be what is held, on PostgreSQL to the table - and the read is served once the event is
kept. Only if neither the file nor the table accepts it is the read refused (503 + Retry-After). Before this
change a read was refused whenever the chain write failed, and a long SLA sweep could turn ordinary page loads into
503s (found by the load test: the sweep held the audit writer lock while it evaluated every open case). The sweep
now evaluates all open cases read-only first and takes the lock only for the cases that change, and the session
idle-clock update no longer runs on the response path.

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
