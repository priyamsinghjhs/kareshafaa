"""FastAPI application factory."""
from __future__ import annotations

import asyncio

import logging
import threading
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.exc import TimeoutError as SATimeoutError
from sqlalchemy.orm.exc import StaleDataError
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from . import __version__, accounts, audit, public_demo, pwa, ui
from .api import about, admin_users, assist, auth, cases, demo_api, governance, ingest, me, ops, patient_requests, patients, recalls, gp_summaries, signin_help
from .api.deps import COOKIE, resolve_actor, resolve_user
from .assets import asset, cache_control
from .config import get_settings, storage_is_persistent
from .db import init_db, session_factory, session_scope
from .pwa import PUBLIC_PATHS as PWA_PUBLIC_PATHS
from .security import Actor
from .services.escalation import run_sweep
from .services.errors import DomainError, ServiceBusy
from .statemachine import TransitionError

log = logging.getLogger("kareshafaa")
FOOTER = ui.FOOTER


def storage_warning() -> None:
    """Loud start-up warning when the data would not survive a restart (e.g. SQLite on Render's free disk)."""
    s = get_settings()
    if s.database_url.startswith("sqlite") and s.mode == "production":
        log.warning("DATABASE_URL is not set: using SQLite on this server's local disk%s. Set DATABASE_URL to a "
                    "PostgreSQL database (for example a free Neon project) to keep data across restarts and "
                    "redeploys.", "" if storage_is_persistent(s) else
                    " - THIS DISK IS TEMPORARY: all data is lost on restart/redeploy")
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
templates.env.globals["asset"] = asset
CASE_RE = re.compile(r"^/(?:api/)?cases/(\d+)")
ANON = Actor(user_id=None, role="anonymous", display="anonymous", is_human=True)
METHOD_ACTION = {"GET": "R", "HEAD": "R", "POST": "E", "PUT": "U", "PATCH": "U", "DELETE": "D"}


_LAST_SWEEP_AT = None  # process-local wall-clock guard


def _purge_once() -> dict:
    from .services.retention import purge_operational

    db = session_factory()()
    try:
        return purge_operational(db)
    finally:
        db.close()


def _reconcile_audit_once() -> dict:
    """Timed job: append audit events kept in the fallback (table / JSONL file) to the hash-chained trail."""
    from . import audit_fallback

    db = session_factory()()
    try:
        return audit_fallback.reconcile(db)
    finally:
        db.close()


def _reconcile_results_once() -> dict:
    """Timed job: compare source results with cases; results missing from the loop go to the owned dead-letter list."""
    from .services import reconcile

    with session_scope() as db:
        return reconcile.run(db)


def _sweep_once() -> dict:
    global _LAST_SWEEP_AT
    from datetime import timedelta

    from .models import utcnow

    now = utcnow()
    if _LAST_SWEEP_AT is not None and now < _LAST_SWEEP_AT - timedelta(minutes=1):
        # the server clock went backwards (NTP step, VM restore): never escalate or stamp events on a rewound clock
        log.error("SLA sweep skipped: system clock moved backwards from %s to %s", _LAST_SWEEP_AT, now)
        return {"skipped": "clock_moved_backwards"}
    _LAST_SWEEP_AT = max(now, _LAST_SWEEP_AT or now)
    # a person acting on a case while the sweep runs makes the sweep's UPDATE stale (row_version): re-run the
    # whole sweep on fresh data rather than skip escalations until the next interval
    for attempt in range(3):
        try:
            with session_scope() as db:
                return run_sweep(db)
        except StaleDataError:
            if attempt == 2:
                raise
            log.warning("SLA sweep met a concurrent case change; re-running")
    return {}


_TIMED_JOBS_LOCK = None  # worker_lock.PgAdvisoryLock (PostgreSQL) or FileLock (SQLite), held while elected


def _elect_sweeper() -> bool:
    """Only ONE process runs the timed jobs: across every container on PostgreSQL (session advisory lock on a
    dedicated connection), across the uvicorn workers of this host on SQLite (flock). Non-blocking; True = elected.
    Called again before every run: the holder re-checks (and if its session was lost, re-takes) the lock, and a
    process that lost the election retries, so another container takes over within one interval."""
    global _TIMED_JOBS_LOCK
    from .worker_lock import timed_jobs_lock

    try:
        if _TIMED_JOBS_LOCK is None:
            _TIMED_JOBS_LOCK = timed_jobs_lock()
        return _TIMED_JOBS_LOCK.renew()
    except Exception:  # noqa: BLE001 - never fatal: not elected this time, retried next interval
        log.exception("timed-jobs election failed")
        return False


def _release_sweeper() -> None:
    global _TIMED_JOBS_LOCK
    if _TIMED_JOBS_LOCK is not None:
        try:
            _TIMED_JOBS_LOCK.release()
        except Exception:  # noqa: BLE001
            log.warning("could not release the timed-jobs lock", exc_info=True)
        _TIMED_JOBS_LOCK = None


async def _run_timed_jobs() -> None:
    from .services.job_health import track  # last run / success / failure per job, for Admin -> System

    try:
        t = time.time()
        out = await asyncio.to_thread(track, "sla_sweep", _sweep_once)
        log.info("background SLA sweep: %s in %.0f ms", out, (time.time() - t) * 1000)
    except Exception:  # noqa: BLE001
        log.exception("background SLA sweep failed")
    try:
        await asyncio.to_thread(track, "retention_purge", _purge_once)
    except Exception:  # noqa: BLE001
        log.exception("retention purge failed")
    try:
        out = await asyncio.to_thread(track, "audit_reconcile", _reconcile_audit_once)
        if out.get("reconciled") or out.get("remaining"):
            log.warning("audit fallback reconciliation: %s", out)
    except Exception:  # noqa: BLE001
        log.exception("audit fallback reconciliation failed (retried at the next interval)")
    try:
        out = await asyncio.to_thread(track, "result_reconcile", _reconcile_results_once)
        if out.get("new") or out.get("open"):
            log.warning("result-to-case reconciliation: %s", out)
    except Exception:  # noqa: BLE001
        log.exception("result-to-case reconciliation failed (retried at the next interval)")


async def _sweep_loop(interval: int) -> None:
    """Background timed jobs: SLA sweep (step clocks + escalation ladder), retention purge, audit-fallback
    reconciliation. Run in worker threads so request handling is never blocked; failures are logged, never fatal.
    The election is re-checked before every run (see _elect_sweeper); the lock is released when the loop stops."""
    try:
        elected = await asyncio.to_thread(_elect_sweeper)
        log.info("background timed jobs every %ss: %s", interval,
                 "this process runs them" if elected else "another process runs them (re-checked every interval)")
        while True:
            await asyncio.sleep(interval)
            now_elected = await asyncio.to_thread(_elect_sweeper)  # holder: still held? others: free now?
            if now_elected != elected:
                (log.info if now_elected else log.warning)("this process %s the background timed jobs",
                                                           "now runs" if now_elected else "no longer runs")
                elected = now_elected
            if elected:
                await _run_timed_jobs()
    finally:
        _release_sweeper()


async def _data_steps_task(stop: "threading.Event", delay: float = 20.0) -> None:
    """One-time SYN- sample data steps after start-up (small batches in a worker thread; never blocks serving)."""
    await asyncio.sleep(delay)
    try:
        from .services.sample_data import run_in_background
        t = time.time()
        out = await asyncio.to_thread(run_in_background, stop)
        if out is not None:
            log.info("one-time data steps: %s in %.0f s", {k: (v if isinstance(v, str) else "done") for k, v in out.items()},
                     time.time() - t)
    except Exception:  # noqa: BLE001
        log.exception("one-time data steps failed (retried at the next start)")


async def _name_keys_task(stop: "threading.Event", delay: float = 15.0) -> None:
    """Build duplicate-check name keys for patients that have none yet (older databases, copied-in records). Small
    batches in a worker thread; idempotent; never changes patient data (services/name_keys.py)."""
    await asyncio.sleep(delay)
    try:
        from .services.name_keys import ensure_all
        n = await asyncio.to_thread(ensure_all, session_factory(), 500, stop)
        if n:
            log.info("duplicate-check name keys built for %d patient record(s)", n)
    except Exception:  # noqa: BLE001
        log.exception("building duplicate-check name keys failed (retried at the next start)")


async def _heartbeat_loop() -> None:
    """Round 22 watchdog heartbeat (see kareshafaa/serve.py). Written from the event loop AFTER a no-op has passed through
    the request thread pool, so a blocked loop or an exhausted/wedged thread pool both stop the heartbeat."""
    import anyio

    path = os.path.join(os.environ["KARESHAFAA_HEARTBEAT_DIR"], str(os.getpid()))
    interval = float(os.environ.get("KARESHAFAA_HEARTBEAT_INTERVAL_S", "2"))
    while True:
        try:
            await anyio.to_thread.run_sync(lambda: None)
            with open(path, "w") as f:
                f.write(str(time.time()))
        except Exception:  # noqa: BLE001 - a failed beat is simply a missed beat
            log.warning("heartbeat write failed", exc_info=True)
        await asyncio.sleep(interval)


def _prepare_request_audit(request: Request, state_actor, path: str, code: int, start: float, shed: int = 0) -> list:
    """Resolve the actor (read only) and build the request-log event(s); the write happens in _BATCHER."""
    from .models import utcnow

    items = []
    now = utcnow()
    if shed:
        items.append((ANON, "E", "http.load_shed", dict(entity_type="http", entity_id=None, outcome="failure",
                                                        detail={"requests_shed": shed, "status": 503}, ts=now)))
    actor = state_actor
    if actor is None:
        db = session_factory()()
        try:
            actor = resolve_actor(request, db) or ANON
        finally:
            db.close()
    m = CASE_RE.match(path)
    outcome = "success" if code < 400 else "denied" if code in (401, 403) else "failure"
    items.append((actor, METHOD_ACTION.get(request.method, "E"), f"http.{request.method.lower()}",
                  dict(entity_type="http", entity_id=None, case_id=int(m.group(1)) if m else None, outcome=outcome,
                       path=path[:300], detail={"status": code, "ms": int((time.time() - start) * 1000),
                                                "query": str(request.url.query)[:200] or None}, ts=now)))
    return items


async def _audit_retry_or_fallback(request: Request, items, state_actor, path: str, code: int, start: float,
                                   shed: int, first: BaseException) -> None:
    """After a failed request-audit write for a request whose effect already happened: retry once; if that fails
    too, store the event(s) in the durable fallback (audit_fallback.store: table, else JSONL file) for the
    reconciliation job. Never raises: the user's response is always returned."""
    from . import audit_fallback

    err: BaseException = first
    try:
        if items is None:  # building the event failed (e.g. actor lookup): build it again, falling back to anonymous
            try:
                items = await asyncio.to_thread(_prepare_request_audit, request, state_actor, path, code, start, shed)
            except Exception:  # noqa: BLE001
                items = _prepare_request_audit(request, state_actor or ANON, path, code, start, shed)
        await asyncio.sleep(0.05)
        await asyncio.wrap_future(_BATCHER.submit(items))
        log.warning("request audit write failed once (%r) and succeeded on retry", first)
        return
    except Exception as e:  # noqa: BLE001
        err = e
    if items is None:  # pragma: no cover - cannot even describe the request
        log.critical("request audit failed and the event could not be built: %s %s -> %s (%r)",
                     request.method, path, code, err)
        return
    where = await asyncio.to_thread(audit_fallback.store, items, err)
    log.error("request audit failed twice for %s %s (%r); event kept in fallback: %s", request.method, path, err, where)


class _AuditBatcher:
    """Round 21 group commit for the per-request log. Each request still gets its audit event written and
    committed BEFORE its response is sent (the middleware awaits it), but one writer thread per process commits
    the events of all concurrent requests in one transaction, so the cross-process writer lock is taken once per
    batch instead of once per request (it was the bottleneck: every GET is an audited write)."""

    MAX_BATCH = 256

    def __init__(self) -> None:
        import queue
        import threading

        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._thread = None

    def submit(self, items: list):
        import concurrent.futures
        import threading

        fut: concurrent.futures.Future = concurrent.futures.Future()
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(target=self._run, name="kareshafaa-audit-writer", daemon=True)
                self._thread.start()
        self._q.put((items, fut))
        return fut

    @staticmethod
    def _write(batch: list) -> None:
        db = session_factory()()
        # bounded wait for the writer lock: a long transaction elsewhere (SLA sweep) sends the event to the fallback
        db.info["writer_lock_timeout_s"] = get_settings().request_audit_lock_timeout_s
        try:
            # take the pooled connection BEFORE the cross-process writer lock (taken at flush): holding the lock
            # while waiting for a connection stalled every worker (round 21 ramp: uniform ~6 s at 80 users)
            db.connection()
            for items, _f in batch:
                for actor, action, event, kw in items:
                    audit.record(db, actor, action, event, **kw)
            db.commit()
        finally:
            db.close()

    def _run(self) -> None:
        import queue

        while True:
            batch = [self._q.get()]
            while len(batch) < self.MAX_BATCH:
                try:
                    batch.append(self._q.get_nowait())
                except queue.Empty:
                    break
            try:
                self._write(batch)
                for _i, f in batch:
                    f.set_result(None)
            except Exception as e:  # noqa: BLE001
                from sqlalchemy.exc import OperationalError as _OE, TimeoutError as _TO

                from .services.errors import ServiceBusy as _SB
                if isinstance(e, (_SB, _TO, _OE)):  # saturated: fail fast, no serial per-request retries
                    for _i, f in batch:
                        f.set_exception(e)
                    continue
                for one in batch:  # any other error: isolate the bad event, one transaction per request
                    try:
                        self._write([one])
                        one[1].set_result(None)
                    except Exception as e:  # noqa: BLE001
                        one[1].set_exception(e)


_BATCHER = _AuditBatcher()
_BACKGROUND: set = set()  # strong references to fire-and-forget tasks (asyncio keeps only weak ones)


class TimeBudgetMiddleware:
    """Cut off a request that runs past settings.request_timeout_s: 503 + Retry-After at once, its transaction
    rolled back, any later SQL/commit from it refused, stack logged, counted (/healthz) and audited. A request
    that has started a commit is never cut off (it is allowed to finish), so "nothing was saved" is always true."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send):
        import contextvars

        from . import request_timeout as rt

        cfg = get_settings()
        path = scope.get("path", "")
        budget = cfg.request_timeout_s
        if scope["type"] != "http" or budget <= 0 or not path.startswith("/api/") \
                or path.startswith(cfg.request_timeout_exempt):
            return await self.app(scope, receive, send)
        st = rt.RequestState(path)
        started = {"v": False, "detached": False}

        async def guarded_send(msg):
            if started["detached"]:
                return  # the client already got the 503: the stuck request's late response is discarded
            if msg["type"] == "http.response.start":
                started["v"] = True
            await send(msg)

        token = rt.CURRENT.set(st)
        try:
            task = asyncio.ensure_future(self.app(scope, receive, guarded_send))
        finally:
            rt.CURRENT.reset(token)
        waited = 0.0
        while True:
            done, _ = await asyncio.wait({task}, timeout=budget)
            waited += budget
            if done:
                return task.result()
            if started["v"] or not rt.try_cut_off(st):
                rt.TIMEOUTS["slow_committing"] += 1  # committing or already streaming: let it finish
                log.warning("request %s over its time budget (%.0f s) but committing/streaming: allowed to finish",
                            path, waited)
                budget = 5.0
                continue
            break
        started["detached"] = True
        rt.TIMEOUTS["count"] += 1
        log.error("request timed out after %.1f s: %s %s - cut off, transaction rolled back; stack of the stuck "
                  "request:\n%s", waited, scope.get("method"), path, rt.stacks(st))
        await asyncio.to_thread(contextvars.Context().run, rt.abort_transactions, st)
        task.add_done_callback(lambda t: t.cancelled() or t.exception())  # the stuck work is discarded
        items = [(ANON, "E", "http.request_timeout", dict(entity_type="http", entity_id=None, outcome="failure",
                                                          path=path[:300], detail={"seconds": round(waited, 1),
                                                                                   "method": scope.get("method"),
                                                                                   "status": 503, "saved": False}))]
        try:
            await asyncio.wrap_future(_BATCHER.submit(items))
        except Exception as e:  # noqa: BLE001 - kept in the durable fallback for reconciliation
            log.warning("could not audit a request timeout; keeping it in the audit fallback", exc_info=True)
            from . import audit_fallback
            await asyncio.to_thread(audit_fallback.store, items, e)
        resp = JSONResponse({"detail": "the request took too long and was stopped - nothing was saved; please retry"},
                            status_code=503, headers={"Retry-After": str(cfg.retry_after_s)})
        await resp(scope, receive, send)


# Round 2 security headers. Every script, style, image and fetch is same-origin. Scripts: 'self' only - there are
# no inline <script> blocks or on* handlers (page data travels in <script type="application/json"> blocks, which
# are never executed, and clicks go through data-click delegation in common.js; tests/test_csp.py enforces this).
# style-src keeps 'unsafe-inline' because templates and the JS-built markup use many style="" attributes; style
# injection cannot run script, and this is documented in docs/security.md. No plugins, no framing, no foreign form
# posts or base-URL hijack.
CSP = ("default-src 'self'; script-src 'self'; script-src-attr 'none'; style-src 'self' 'unsafe-inline'; "
       "img-src 'self' data: blob:; font-src 'self' data:; connect-src 'self'; worker-src 'self'; "
       "manifest-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def create_app() -> FastAPI:
    s = get_settings()
    if not logging.getLogger().handlers:
        from .logjson import configure_logging
        configure_logging()
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        storage_warning()
        await asyncio.to_thread(accounts.provision_on_startup)  # demo passwords from env (never from the repo)
        # sqlite3 releases and re-acquires the GIL around every row fetched; with the default 5 ms switch interval
        # a thread fetching ~1,500 queue rows while other request threads run can wait 5 ms per row (convoy
        # effect, measured p95 1.3 s at 10 concurrent clients). A shorter interval bounds that wait.
        sys.setswitchinterval(s.gil_switch_interval_ms / 1000)
        task = asyncio.create_task(_sweep_loop(s.sweep_interval_seconds)) if s.sweep_interval_seconds > 0 else None
        hb = asyncio.create_task(_heartbeat_loop()) if os.environ.get("KARESHAFAA_HEARTBEAT_DIR") else None
        nightly = asyncio.create_task(public_demo.nightly_reset_loop()) if s.public_demo else None
        steps_stop = threading.Event()
        steps = asyncio.create_task(_data_steps_task(steps_stop)) if s.data_steps == "background" else None
        keys = asyncio.create_task(_name_keys_task(steps_stop)) if s.data_steps != "off" else None
        yield
        steps_stop.set()
        if steps:
            steps.cancel()
        if keys:
            keys.cancel()
        if nightly:
            nightly.cancel()
        if task:
            task.cancel()  # its finally releases the timed-jobs lock (advisory lock / flock)
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if hb:
            hb.cancel()

    # API reference is not public: administrators read it in the app (About the system) or download the OpenAPI file
    app = FastAPI(title="KareShafaa - Closing the Screening-to-Care Loop", version=__version__, lifespan=lifespan,
                  description=FOOTER, docs_url=None, redoc_url=None, openapi_url=None)

    inflight = {"n": 0, "shed": 0}

    # Round 23: per-request time budget, a pure ASGI layer INSIDE the audit middleware (so a cut-off request is
    # still logged as 503). It answers without waiting for the stuck thread (kareshafaa/request_timeout.py).
    app.add_middleware(TimeBudgetMiddleware)

    CSRF_SELF_CHECKED = ("/login", "/logout", "/account/password", "/api/auth/login", "/activate", "/reset-password",
                         "/forgot-username", "/forgot-password", "/staff-reset-password", "/patient-reset-password")

    @app.middleware("http")
    async def csrf_and_session_clock(request: Request, call_next):
        """Every cookie-authenticated write must present the session's CSRF token (X-CSRF-Token header). Bearer-
        token clients (scripts, tests) are not cookie-based and are exempt. The HTML form endpoints check the token
        in their form body themselves. Also slides the session idle timeout (at most one write a minute)."""
        method, path = request.method, request.url.path
        tok = request.cookies.get(COOKIE)
        bearer = request.headers.get("authorization", "").lower().startswith("bearer ")
        if (method in ("POST", "PUT", "PATCH", "DELETE") and tok and not bearer and path not in CSRF_SELF_CHECKED
                and not accounts.csrf_ok(tok, request.headers.get("x-csrf-token"))):
            return JSONResponse({"detail": "security check failed (CSRF) - reload the page and try again; nothing "
                                           "was saved"}, status_code=403)
        response = await call_next(request)
        if tok and not bearer and tok.startswith(accounts.SESSION_PREFIX) and response.status_code < 400 \
                and not path.startswith("/static") and path not in PWA_PUBLIC_PATHS:
            # best effort and OFF the response path: on SQLite this UPDATE can wait for the database write lock
            # (e.g. during an SLA sweep), which used to delay the response by up to the busy timeout
            t = asyncio.ensure_future(asyncio.to_thread(accounts.touch_session, session_factory, tok))
            _BACKGROUND.add(t)
            t.add_done_callback(_BACKGROUND.discard)
        if not path.startswith("/static"):
            response.headers.setdefault("Cache-Control", "no-store")  # pages and API data are never cached
        else:  # versioned files (?v=<hash>) are immutable; others are re-checked after 5 minutes
            response.headers["Cache-Control"] = cache_control(request.url.query)
        return response

    if s.mode == "production":
        write_limiter = public_demo.WriteRateLimiter(s.write_limit_per_minute, 60.0)

        @app.middleware("http")
        async def write_rate_limit(request: Request, call_next):
            """Production abuse limit: API writes per signed-in user session (or client IP) per minute, per worker.
            Declared inside the audit middleware, so refused attempts are still audited."""
            if request.url.path.startswith("/api/") and request.method in public_demo.WRITE_METHODS:
                key = public_demo.client_key(request.headers, request.cookies,
                                             request.client.host if request.client else None, COOKIE)
                wait = write_limiter.check(key)
                if wait:
                    return JSONResponse({"detail": "too many changes in a short time - nothing was saved; please "
                                                   "wait a moment and try again"}, status_code=429,
                                        headers={"Retry-After": str(int(wait) + 1)})
            return await call_next(request)

    if s.public_demo:
        limiter = public_demo.WriteRateLimiter(s.demo_write_limit, s.demo_write_window_s)

        @app.middleware("http")
        async def public_demo_guard(request: Request, call_next):
            """KARESHAFAA_PUBLIC_DEMO=1: refuse admin-only/destructive actions and rate-limit writes per session.
            Declared inside the audit middleware, so refused attempts are still audited."""
            path, method = request.url.path, request.method
            if path.startswith("/api/") and public_demo.is_blocked(method, path):
                return JSONResponse({"detail": public_demo.BLOCKED_DETAIL}, status_code=403)
            if path.startswith("/api/") and method in public_demo.WRITE_METHODS:
                key = public_demo.client_key(request.headers, request.cookies,
                                             request.client.host if request.client else None, COOKIE)
                wait = limiter.check(key)
                if wait:
                    return JSONResponse({"detail": "too many changes in a short time - nothing was saved; please "
                                                   "wait a moment"}, status_code=429,
                                        headers={"Retry-After": str(int(wait) + 1)})
            return await call_next(request)

    @app.middleware("http")
    async def audit_every_request(request: Request, call_next):
        """Append-only audit of EVERY API read and write (and page view), including denied attempts."""
        start = time.time()
        try:
            response = await call_next(request)
        except Exception:  # noqa: BLE001
            # fail closed AND leave a trail: an unexpected error mid-request rolls its transaction back (the request
            # session is closed without a commit) and is audited below as a failed request (status 500), instead of
            # escaping past this layer unlogged
            log.exception("unhandled error in %s %s", request.method, request.url.path)
            response = JSONResponse({"detail": "something went wrong and this action was not completed - reload "
                                               "the page to see the current record before trying again"},
                                    status_code=500)
        path = request.url.path
        if path.startswith("/static") or path in ("/favicon.ico", "/healthz") or path in PWA_PUBLIC_PATHS:
            return response
        state_actor = getattr(request.state, "actor", None)
        shed, inflight["shed"] = inflight["shed"], 0
        items = None
        try:
            # sync DB work runs in a worker thread so the event loop is never blocked
            items = await asyncio.to_thread(_prepare_request_audit, request, state_actor, path,
                                            response.status_code, start, shed)
            await asyncio.wrap_future(_BATCHER.submit(items))  # committed before the response leaves
        except Exception as e:  # noqa: BLE001
            if request.method in ("GET", "HEAD") and response.status_code < 400:
                # never hand out data whose access is not logged: a read is served only once its audit event is
                # kept durably. A slow or failed chain write (e.g. the writer lock held by a long SLA sweep) sends
                # the event straight to the fallback file (no database wait); 503 only if that fails too.
                busy = isinstance(e, (ServiceBusy, SATimeoutError, OperationalError))
                where = "log"
                if items is not None:
                    from . import audit_fallback
                    # SQLite: the database write lock may be the thing that is held, so the file first (no wait);
                    # PostgreSQL: the table insert does not need the audit writer lock and survives a restart
                    prefer = "file" if s.database_url.startswith("sqlite") else "table"
                    where = await asyncio.to_thread(audit_fallback.store, items, e, prefer)
                if where == "log":
                    inflight["shed"] += shed  # keep the shed count for the next audited request
                    (log.warning if busy else log.exception)("request audit failed; read refused (503): %r", e)
                    return JSONResponse({"detail": "the service is busy - please retry shortly"}, status_code=503,
                                        headers={"Retry-After": str(s.retry_after_s)})
                (log.warning if busy else log.error)("request audit for a read deferred to the fallback (%s): %r",
                                                     where, e)
                response.headers["X-Content-Type-Options"] = "nosniff"
                response.headers["X-Frame-Options"] = "DENY"
                response.headers["Referrer-Policy"] = "no-referrer"
                return response
            # a state-changing request (or a refused one) has already happened: the user still gets their answer,
            # but its audit event is never dropped - retry once, then keep it durably for reconciliation
            await _audit_retry_or_fallback(request, items, state_actor, path, response.status_code, start, shed, e)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.middleware("http")
    async def admission_control(request: Request, call_next):
        """Declared AFTER the audit middleware so it is the OUTERMOST layer: a shed request costs no DB work. Round 21 graceful degradation: beyond max_inflight_requests per worker, answer 503 + Retry-After at once
        (nothing read or saved) instead of queueing into 30 s connection waits. Shed requests are counted and
        written to the audit log as one aggregated `http.load_shed` event with the next audited request."""
        path = request.url.path
        if path.startswith("/static") or path in ("/favicon.ico", "/healthz") or path in PWA_PUBLIC_PATHS:
            return await call_next(request)
        if inflight["n"] >= s.max_inflight_requests:
            inflight["shed"] += 1
            return JSONResponse({"detail": "the service is busy - nothing was read or saved; please retry shortly"},
                                status_code=503, headers={"Retry-After": str(s.retry_after_s)})
        inflight["n"] += 1
        try:
            return await call_next(request)
        finally:
            inflight["n"] -= 1

    from fastapi.exception_handlers import http_exception_handler as _default_http
    from starlette.exceptions import HTTPException as _StarletteHTTP

    def _wants_page(req: Request) -> bool:
        path = req.url.path
        return req.method == "GET" and "text/html" in req.headers.get("accept", "") and \
            not path.startswith(("/api", "/fhir", "/static"))

    _ERR_PAGES = {404: ("Page not found", "This page does not exist or has moved. Check the address, or start again from the home page."),
                  405: ("Page not found", "This address cannot be opened in the browser.")}

    @app.exception_handler(_StarletteHTTP)
    async def _http_err(req: Request, exc: _StarletteHTTP):
        # browser page loads get a branded page; API/FHIR callers keep the JSON body
        if exc.status_code in _ERR_PAGES and _wants_page(req):
            t, m = _ERR_PAGES[exc.status_code]
            return templates.TemplateResponse(req, "error.html", {"title": t, "message": m}, status_code=exc.status_code)
        return await _default_http(req, exc)

    @app.exception_handler(DomainError)
    async def _domain(_req: Request, exc: DomainError):
        body = {"detail": exc.message}
        if getattr(exc, "code", None):
            body["code"] = exc.code  # machine-readable reason (e.g. answers_locked) so pages can show their own wording
        if getattr(exc, "extra", None):
            body.update(exc.extra)  # e.g. possible duplicate records for the registration form
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(ServiceBusy)
    async def _busy(_req: Request, exc: ServiceBusy):
        return JSONResponse({"detail": exc.message}, status_code=503, headers={"Retry-After": str(s.retry_after_s)})

    @app.exception_handler(SATimeoutError)
    async def _pool(_req: Request, exc: SATimeoutError):
        return JSONResponse({"detail": "the service is busy (no database connection free) - nothing was saved; "
                                       "please retry shortly"}, status_code=503,
                            headers={"Retry-After": str(s.retry_after_s)})

    @app.exception_handler(OperationalError)
    async def _locked(_req: Request, exc: OperationalError):
        if "locked" in str(exc.orig).lower() or "busy" in str(exc.orig).lower():
            return JSONResponse({"detail": "the database is busy - nothing was saved; please retry shortly"},
                                status_code=503, headers={"Retry-After": str(s.retry_after_s)})
        raise exc

    @app.exception_handler(TransitionError)
    async def _transition(_req: Request, exc: TransitionError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(StaleDataError)
    async def _stale(_req: Request, exc: StaleDataError):
        return JSONResponse({"detail": "this case was changed by someone else at the same time - reload it and try "
                                       "again (nothing was saved)"}, status_code=409)

    @app.exception_handler(IntegrityError)
    async def _integrity(_req: Request, exc: IntegrityError):
        return JSONResponse({"detail": "a concurrent change conflicted with this request - reload and try again "
                                       "(nothing was saved)"}, status_code=409)

    @app.exception_handler(PermissionError)
    async def _perm(_req: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": "invalid input", "errors": errs}, status_code=422)

    for r in (auth.router, admin_users.router, admin_users.system_router, cases.router, me.router, ops.router, governance.router, ingest.router, demo_api.router,
              pwa.router, patients.router, recalls.router, gp_summaries.router, patient_requests.router, signin_help.router,
              about.router, assist.router):
        app.include_router(r)
    # gzip text responses over 1 KB (pages, CSS, JS, JSON): the outermost layer, so it also covers refusals
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Round 2: CSP, Permissions-Policy, COOP/CORP and nosniff/DENY on EVERY response (static, health and
        shed responses included); HSTS only when the request reached us over https (Render terminates TLS)."""
        response = await call_next(request)
        for k, v in SECURITY_HEADERS.items():
            response.headers.setdefault(k, v)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        if request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https":
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        return response

    app.add_middleware(GZipMiddleware, minimum_size=1024, compresslevel=6)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> FileResponse:
        return FileResponse(HERE / "static" / "favicon.ico", media_type="image/x-icon", headers={"Cache-Control": "public, max-age=86400"})

    def _mail_health() -> dict:
        from .comms import mail
        try:
            return {"configured": mail.configured(), "from": mail.from_address() or None}  # never the password
        except Exception:  # noqa: BLE001 - liveness must not fail on mail settings
            return {"configured": None}

    @app.get("/healthz")
    def healthz() -> dict:
        db = session_factory()()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        from .request_timeout import TIMEOUTS
        from .policy import POLICY_STATUS_NOTE, POLICY_VERSION
        return {"status": "ok", "version": __version__, "mode": s.mode, "clinical_profile": s.clinical_profile,
                "clinical_policy": {"version": POLICY_VERSION, "status": POLICY_STATUS_NOTE},
                "storage": "persistent" if storage_is_persistent(s) else "temporary",
                "request_timeouts": TIMEOUTS["count"],
                "slow_committing_requests": TIMEOUTS["slow_committing"], "mail": _mail_health()}

    @app.get("/readyz")
    def readyz() -> JSONResponse:
        """Readiness: the database answers and the initial records are in place (load balancers send traffic only
        then). /healthz stays the liveness check."""
        from sqlalchemy import func as _f, select as _sel

        from .models import User
        checks = {}
        db = session_factory()()
        try:
            db.execute(text("SELECT 1"))
            checks["database"] = "ok"
            checks["accounts"] = "ok" if (db.scalar(_sel(_f.count(User.id))) or 0) > 0 else "not bootstrapped"
        except Exception as e:  # noqa: BLE001
            checks["database"] = f"error: {type(e).__name__}"
        finally:
            db.close()
        ready = all(v == "ok" for v in checks.values())
        return JSONResponse({"status": "ready" if ready else "not ready", "version": __version__, "checks": checks,
                             "storage": "persistent" if storage_is_persistent(s) else "temporary"},
                            status_code=200 if ready else 503)

    @app.get("/fhir/metadata")
    def fhir_metadata(request: Request) -> JSONResponse:
        from .integration import capability_statement
        return JSONResponse(capability_statement(str(request.base_url)), media_type="application/fhir+json")

    from .api.auth import (do_login, do_logout, do_password_change, ensure_login_csrf, login_csrf_ok,
                           start_session)

    def _safe_next(nxt: str | None) -> str | None:
        if nxt and nxt.startswith("/") and not nxt.startswith("//") and "\\" not in nxt and not nxt.startswith("/api"):
            return nxt[:300]
        return None

    def page(name: str, request: Request, user=None, status_code: int = 200, **ctx) -> HTMLResponse:
        tok = request.cookies.get(COOKIE)
        role = user.role if user is not None else None
        here = request.url.path + (("?" + request.url.query) if request.url.query else "")
        nav = ui.nav_for(role) if role else []
        # "current" nav entry: exact URL match first, else the entry for the same path (case pages -> queue)
        path_only = request.url.path if not request.url.path.startswith("/cases/") else "/queue"
        best = next((n for n in nav if n["href"] == here), None) or \
            next((n for n in nav if n["href"] == path_only), None) or \
            next((n for n in nav if n["href"].split("?")[0] == path_only), None)
        resp = templates.TemplateResponse(request, name, {
            "walkthrough": s.demo_logins, "version": __version__, "read_only_site": s.public_demo,
            "footer": FOOTER, "user": user, "role_label": ui.role_label(role) if role else "",
            "nav": nav, "current_nav": best["href"] if best else None, "home_href": ui.home_for(role) if role else "/login",
            "csrf": accounts.csrf_for(tok) if (tok and user is not None) else "", "tiers": ui.tier_help(),
            "role_labels": accounts.ROLE_LABELS, **ctx}, status_code=status_code)
        resp.headers["Cache-Control"] = "no-store"
        return resp

    def guarded(name: str, key: str, title: str, request: Request, **ctx):
        """Server-side page guard: not signed in -> /login?next=...; wrong role -> 403 page."""
        db = session_factory()()
        try:
            u = resolve_user(request, db)
            if u is not None:
                db.expunge(u)
        finally:
            db.close()
        if u is None:
            from urllib.parse import quote
            here = request.url.path + (("?" + request.url.query) if request.url.query else "")
            return RedirectResponse("/login?next=" + quote(here, safe=""), status_code=303)
        if u.must_change_password and key != "/account/password":
            return RedirectResponse("/account/password", status_code=303)
        if not ui.can_open(u.role, key):
            return page("forbidden.html", request, user=u, title="No access", status_code=403)
        return page(name, request, user=u, title=title, **ctx)

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        db = session_factory()()
        try:
            u = resolve_user(request, db)
        finally:
            db.close()
        if u is None:
            return login_page(request)
        return RedirectResponse("/account/password" if u.must_change_password else ui.home_for(u.role), status_code=303)

    def _page_lang(raw) -> str:
        from .comms.signin_text import LANGS

        raw = str(raw or "")[:5]
        return raw if raw in LANGS else "en"

    def login_page(request: Request, error: str | None = None, status_code: int = 200, username: str = "",
                   lang: str | None = None, nxt: str | None = None):
        """error: a key of LOGIN_TEXT (err_generic, err_locked, err_expired, err_missing)."""
        from .comms.content import RTL_LANGUAGES
        from .comms.signin_text import LANGS, LOGIN_TEXT

        lang = lang or _page_lang(request.query_params.get("lang"))
        t = LOGIN_TEXT[lang]
        tok = auth.login_csrf_token(request)
        resp = page("login.html", request, title=t["title"], status_code=status_code,
                    error=t.get(error, error) if error else None, username=username,
                    next=nxt if nxt is not None else (_safe_next(request.query_params.get("next")) or ""), login_csrf=tok,
                    signed_out=request.query_params.get("signed_out") == "1", t=t, lang=lang,
                    rtl=lang in RTL_LANGUAGES, langs=LANGS)
        ensure_login_csrf(request, resp, tok)
        return resp

    @app.get("/login", response_class=HTMLResponse)
    def login_get(request: Request):
        db = session_factory()()
        try:
            u = resolve_user(request, db)
        finally:
            db.close()
        if u is not None:
            return RedirectResponse(_safe_next(request.query_params.get("next")) or ui.home_for(u.role), status_code=303)
        return login_page(request)

    @app.post("/login", response_class=HTMLResponse)
    async def login_post(request: Request):
        form = await request.form()
        username, password = str(form.get("username", ""))[:80], str(form.get("password", ""))[:256]
        lang, nxt0 = _page_lang(form.get("lang")), _safe_next(str(form.get("next", ""))) or ""
        if not login_csrf_ok(request, str(form.get("csrf_token", ""))):
            return login_page(request, "err_expired", 403, username, lang, nxt0)
        if not username.strip() or not password:
            return login_page(request, "err_missing", 400, username, lang, nxt0)

        def _work():
            db = session_factory()()
            try:
                res = do_login(db, request, username, password)
                if not res.ok:
                    return res, None, None
                resp = RedirectResponse("/", status_code=303)
                start_session(db, request, resp, res.user)
                return res, resp, (res.user.must_change_password, res.user.role)
            finally:
                db.close()

        res, resp, info = await asyncio.to_thread(_work)
        if resp is None:
            locked = res.reason in ("locked", "rate_limited")
            return login_page(request, "err_locked" if locked else "err_generic", 429 if locked else 401, username,
                              lang, nxt0)
        must_change, role = info
        nxt = _safe_next(str(form.get("next", "")))
        resp.headers["location"] = "/account/password" if must_change else (nxt or ui.home_for(role))
        resp.delete_cookie(auth.CSRF_COOKIE, path="/")
        return resp

    # ---- patient account activation (one-time code from the care team; kareshafaa/services/invites.py) ----
    def activate_page(request: Request, lang: str, token: str | None, status_code: int = 200, mode: str = "activate",
                      **ctx):
        from .comms.content import RTL_LANGUAGES
        from .comms.signin_text import reset_text
        from .services import invites as inv

        t = reset_text(lang) if mode == "reset" else inv.ACTIVATE_TEXT[lang]
        if "error_reason" in ctx:
            r = ctx.pop("error_reason")
            ctx["error"] = (t["err"]["weak"] + ((" " + r[5:]) if lang == "en" else "")) if r.startswith("weak:") \
                else t["err"].get(r, t["err"]["invalid"])
        tok = auth.login_csrf_token(request)
        resp = page("activate.html", request, title=t["title"], status_code=status_code,
                    t=t, lang=lang, rtl=lang in RTL_LANGUAGES, langs=inv.ACTIVATE_LANGS, mode=mode,
                    action="/reset-password" if mode == "reset" else "/activate",
                    token=token or "", login_csrf=tok, code=ctx.pop("code", ""), birth_date=ctx.pop("birth_date", ""),
                    username=ctx.pop("username", ""), **ctx)
        resp.headers["Referrer-Policy"] = "no-referrer"  # the link token never leaks to other sites
        ensure_login_csrf(request, resp, tok)
        return resp

    def _clean_token(v) -> str | None:
        v = str(v or "")[:64]
        return v if re.fullmatch(r"[A-Za-z0-9_-]{20,64}", v) else None

    def _code_page_get(request: Request, mode: str):
        from .services import invites as inv

        token = _clean_token(request.query_params.get("t"))
        db = session_factory()()
        try:
            lang = inv.page_language(db, token, request.query_params.get("lang"))
        finally:
            db.close()
        return activate_page(request, lang, token, mode=mode)

    @app.get("/activate", response_class=HTMLResponse)
    def activate_get(request: Request):
        return _code_page_get(request, "activate")

    @app.get("/reset-password", response_class=HTMLResponse)
    def reset_get(request: Request):
        """Patient password reset with the one-time code a care coordinator sent (after an identity check)."""
        return _code_page_get(request, "reset")

    @app.post("/activate", response_class=HTMLResponse)
    async def activate_post(request: Request):
        return await _code_page_post(request, "activate")

    @app.post("/reset-password", response_class=HTMLResponse)
    async def reset_post(request: Request):
        return await _code_page_post(request, "reset")

    async def _code_page_post(request: Request, mode: str):
        from datetime import date as _date

        from .services import invites as inv

        form = await request.form()
        lang = str(form.get("lang", "en"))
        lang = lang if lang in inv.ACTIVATE_TEXT else "en"
        token = _clean_token(form.get("t"))
        code = str(form.get("code", ""))[:20]
        dob_raw = str(form.get("birth_date", ""))[:10]
        pw, confirm = str(form.get("password", ""))[:256], str(form.get("confirm", ""))[:256]
        username = str(form.get("username", ""))[:80]
        keep = {"code": code, "birth_date": dob_raw, "username": username}
        if not login_csrf_ok(request, str(form.get("csrf_token", ""))):
            return activate_page(request, lang, token, 403, mode=mode, error=inv.error_text(lang, "expired_page"), **keep)
        try:
            dob = _date.fromisoformat(dob_raw)
        except ValueError:
            dob = None
        if (not token and not code.strip()) or dob is None or not pw or not confirm:
            return activate_page(request, lang, token, 400, mode=mode, error=inv.error_text(lang, "missing"),
                                 error_field="code" if not token and not code.strip() else ("dob" if dob is None else "pw"), **keep)

        def _work():
            db = session_factory()()
            try:
                u, reason = inv.activate(db, code=code, token=token, birth_date=dob, password=pw, confirm=confirm,
                                         ip=auth.client_ip(request), purpose="reset" if mode == "reset" else None,
                                         username=None if mode == "reset" else username)
                if u is None:
                    db.rollback() if reason in ("mismatch",) or reason.startswith("weak:") else None
                    return reason, None, None
                resp = activate_page(request, lang, None, 200, mode=mode, done=True, username=u.username)
                start_session(db, request, resp, u)  # commits the activation and the new session together
                return "ok", resp, u.username
            finally:
                db.close()

        reason, resp, _u = await asyncio.to_thread(_work)
        if resp is None:
            field = {"wrong_dob": "dob", "mismatch": "pw", "username_taken": "username",
                     "username_invalid": "username"}.get(
                reason, "pw" if reason.startswith("weak:") else "code")
            status = 429 if reason == "rate_limited" else (422 if field in ("pw", "username") else 400)
            return activate_page(request, lang, token, status, mode=mode, error_reason=reason, error_field=field, **keep)
        resp.delete_cookie(auth.CSRF_COOKIE, path="/")
        return resp

    # ---- sign-in help: forgot username / password (kareshafaa/services/signin_help.py) ----
    def help_page(request: Request, mode: str, lang: str, who: str, status_code: int = 200, **ctx):
        from .comms.content import RTL_LANGUAGES
        from .comms.signin_text import HELP_TEXT, LANGS, LOGIN_TEXT

        tok = auth.login_csrf_token(request)
        t = HELP_TEXT[lang]
        ctx.setdefault("via", "")
        resp = page("signin_help.html", request, title=t["title_" + mode], status_code=status_code, t=t,
                    lt=LOGIN_TEXT[lang], mode=mode, who=who, lang=lang, rtl=lang in RTL_LANGUAGES, langs=LANGS,
                    login_csrf=tok, v=ctx.pop("v", {}), bad=ctx.pop("bad", []), **ctx)
        resp.headers["Referrer-Policy"] = "no-referrer"
        ensure_login_csrf(request, resp, tok)
        return resp

    def _help_get(request: Request, mode: str):
        who = request.query_params.get("who")
        via_raw = request.query_params.get("via")
        via = via_raw if via_raw in ("admin", "details") else ""
        return help_page(request, mode, _page_lang(request.query_params.get("lang")),
                         who if who in ("patient", "staff") else "patient", via=via)

    @app.get("/forgot-username", response_class=HTMLResponse)
    def forgot_username_get(request: Request):
        return _help_get(request, "username")

    @app.get("/forgot-password", response_class=HTMLResponse)
    def forgot_password_get(request: Request):
        return _help_get(request, "password")

    async def _help_post(request: Request, mode: str):
        from datetime import date as _date

        from .comms.signin_text import HELP_TEXT
        from .services import signin_help as sh

        form = await request.form()
        lang = _page_lang(form.get("lang"))
        who = "staff" if form.get("who") == "staff" else "patient"
        t = HELP_TEXT[lang]
        g = lambda k, n=200: str(form.get(k, "") or "")[:n]  # noqa: E731
        ip = auth.client_ip(request)
        if who == "staff":
            via_admin = g("via", 10) == "admin" or mode != "password"
            if not via_admin:
                v = {"username": g("username", 80)}
                if not login_csrf_ok(request, g("csrf_token", 100)):
                    return help_page(request, mode, lang, who, 403, error=t["expired_page"], v=v, via="")
                if not v["username"].strip():
                    return help_page(request, mode, lang, who, 400, error=t["s_err_username"], bad=["username"], v=v, via="")
                from .api.patients import base_url as public_base_url

                base = public_base_url(request)

                def _work_staff_reset():
                    db = session_factory()()
                    try:
                        st = sh.staff_forgot_password(db, v["username"], ip, lang, base)
                        db.commit()
                        return st
                    finally:
                        db.close()

                st = await asyncio.to_thread(_work_staff_reset)
                if st == "paused":
                    return help_page(request, mode, lang, who, 429, error=t["s_paused"], v=v, via="")
                return help_page(request, mode, lang, who, 200, staff_reset_sent=True, via="")
            v = {"full_name": g("full_name"), "role": g("role", 40), "org": g("org"), "contact": g("contact"),
                 "need": "username" if g("need", 10) == "username" else ("password" if g("need", 10) == "password" else mode),
                 "note": g("note", 1000)}
            if not login_csrf_ok(request, g("csrf_token", 100)):
                return help_page(request, mode, lang, who, 403, error=t["expired_page"], v=v, via="admin")

            def _work_staff():
                db = session_factory()()
                try:
                    st, bad = sh.staff_request(db, full_name=v["full_name"], role=v["role"], org=v["org"],
                                               contact=v["contact"], need=v["need"], note=v["note"], ip=ip, lang=lang)
                    db.commit()
                    return st, bad
                finally:
                    db.close()

            st, bad = await asyncio.to_thread(_work_staff)
            if st == "invalid":
                msg = " ".join(t["s_err_" + f] for f in bad)
                return help_page(request, mode, lang, who, 422, error=msg, bad=bad, v=v, via="admin")
            if st == "paused":
                return help_page(request, mode, lang, who, 429, error=t["s_paused"], v=v, via="admin")
            return help_page(request, mode, lang, who, 200, staff_sent=True, via="admin")
        via_raw = g("via", 10)
        via = via_raw if via_raw in ("admin", "details") else ""
        if mode == "password" and via == "":
            v = {"username": g("username", 80)}
            if not login_csrf_ok(request, g("csrf_token", 100)):
                return help_page(request, mode, lang, who, 403, error=t["expired_page"], v=v, via="")
            if not v["username"].strip():
                return help_page(request, mode, lang, who, 400, error=t["p_err_username"], bad=["username"], v=v, via="")
            from .api.patients import base_url as public_base_url

            base = public_base_url(request)

            def _work_patient_reset():
                db = session_factory()()
                try:
                    st = sh.patient_forgot_password_by_username(db, v["username"], ip, lang, base)
                    db.commit()
                    return st
                finally:
                    db.close()

            st = await asyncio.to_thread(_work_patient_reset)
            if st == "paused":
                return help_page(request, mode, lang, who, 429, error=t["paused"], v=v, via="")
            return help_page(request, mode, lang, who, 200, patient_reset_sent=True, via="")
        v = {"birth_date": g("birth_date", 10), "id_type": "passport" if g("id_type", 20) == "passport" else "emirates_id",
             "id_last4": g("id_last4", 12), "phone": g("phone", 30)}
        if not login_csrf_ok(request, g("csrf_token", 100)):
            return help_page(request, mode, lang, who, 403, error=t["expired_page"], v=v, via=via)
        try:
            dob = _date.fromisoformat(v["birth_date"])
        except ValueError:
            dob = None
        bad = [k for k, ok in (("birth_date", dob is not None), ("id_last4", bool(v["id_last4"].strip())),
                               ("phone", bool(v["phone"].strip()))) if not ok]
        if bad:
            return help_page(request, mode, lang, who, 400, error=t["missing"], bad=bad, v=v, via=via)
        if sh._last4(v["id_last4"]) is None:
            return help_page(request, mode, lang, who, 400, error=t["bad_last4"], bad=["id_last4"], v=v, via=via)
        details_base = ""
        if mode == "password" and via == "details":
            from .api.patients import base_url as public_base_url

            details_base = public_base_url(request)

        def _work_patient():
            db = session_factory()()
            try:
                if mode == "username":
                    st, uname = sh.forgot_username(db, dob, v["id_last4"], v["phone"], ip, lang)
                elif via == "details":
                    st, uname = sh.patient_forgot_password_by_details(
                        db, dob, v["id_last4"], v["phone"], ip, lang, details_base), None
                else:
                    st, uname = sh.forgot_password(db, dob, v["id_last4"], v["phone"], ip, lang), None
                db.commit()
                return st, uname
            finally:
                db.close()

        st, uname = await asyncio.to_thread(_work_patient)
        if st == "paused":
            return help_page(request, mode, lang, who, 429, error=t["paused"], v=v, via=via)
        if mode == "username":
            if st == "ok":
                return help_page(request, mode, lang, who, 200, found=uname)
            return help_page(request, mode, lang, who, 200, notice=t["generic_username"], v=v)
        if via == "details":
            return help_page(request, mode, lang, who, 200, patient_reset_sent=True, via="details")
        return help_page(request, mode, lang, who, 200, sent=True, via="admin")

    @app.post("/forgot-username", response_class=HTMLResponse)
    async def forgot_username_post(request: Request):
        return await _help_post(request, "username")

    @app.post("/forgot-password", response_class=HTMLResponse)
    async def forgot_password_post(request: Request):
        return await _help_post(request, "password")

    def staff_reset_page(request: Request, lang: str, token: str | None, status_code: int = 200, **ctx):
        from .comms.content import RTL_LANGUAGES
        from .comms.signin_text import LANGS, STAFF_RESET_TEXT

        t = STAFF_RESET_TEXT[lang if lang in STAFF_RESET_TEXT else "en"]
        if "error_reason" in ctx:
            r = ctx.pop("error_reason")
            ctx["error"] = (t["err"]["weak"] + ((" " + r[5:]) if lang == "en" else "")) if r.startswith("weak:") \
                else t["err"].get(r, t["err"]["invalid"])
        tok = auth.login_csrf_token(request)
        resp = page("staff_reset.html", request, title=t["title"], status_code=status_code,
                    t=t, lang=lang, rtl=lang in RTL_LANGUAGES, langs=LANGS, token=token or "",
                    login_csrf=tok, **ctx)
        resp.headers["Referrer-Policy"] = "no-referrer"
        ensure_login_csrf(request, resp, tok)
        return resp

    @app.get("/staff-reset-password", response_class=HTMLResponse)
    def staff_reset_get(request: Request):
        from .services import signin_help as sh

        token = _clean_token(request.query_params.get("t"))
        lang = _page_lang(request.query_params.get("lang"))
        db = session_factory()()
        try:
            status = sh.staff_reset_status(db, token)
        finally:
            db.close()
        t_err = None
        if status != "ok":
            from .comms.signin_text import STAFF_RESET_TEXT
            t = STAFF_RESET_TEXT[lang]
            key = "invalid" if status in ("missing", "invalid") else status
            t_err = t["err"].get(key, t["err"]["invalid"])
        return staff_reset_page(request, lang, token, 200, link_error=t_err)

    @app.post("/staff-reset-password", response_class=HTMLResponse)
    async def staff_reset_post(request: Request):
        from .comms.signin_text import STAFF_RESET_TEXT
        from .services import signin_help as sh

        form = await request.form()
        lang = _page_lang(form.get("lang"))
        token = _clean_token(form.get("t"))
        pw, confirm = str(form.get("password", ""))[:256], str(form.get("confirm", ""))[:256]
        t = STAFF_RESET_TEXT[lang]
        if not login_csrf_ok(request, str(form.get("csrf_token", ""))):
            return staff_reset_page(request, lang, token, 403, error=t["err"]["expired_page"])
        if not token or not pw or not confirm:
            return staff_reset_page(request, lang, token, 400, error=t["err"]["missing"],
                                    error_field="pw" if token else "code")

        def _work():
            db = session_factory()()
            try:
                u, reason = sh.complete_staff_reset(db, token=token, password=pw, confirm=confirm,
                                                    ip=auth.client_ip(request), lang=lang)
                db.commit()
                return u.username if u is not None else None, reason
            finally:
                db.close()

        username, reason = await asyncio.to_thread(_work)
        if username is None:
            field = "pw" if reason == "mismatch" or reason.startswith("weak:") else "code"
            status = 429 if reason == "rate_limited" else (422 if field == "pw" else 400)
            return staff_reset_page(request, lang, token, status, error_reason=reason, error_field=field)
        return staff_reset_page(request, lang, None, 200, done=True, username=username)

    def patient_reset_page(request: Request, lang: str, token: str | None, status_code: int = 200, **ctx):
        from .comms.content import RTL_LANGUAGES
        from .comms.signin_text import LANGS, PATIENT_RESET_TEXT

        t = PATIENT_RESET_TEXT[lang if lang in PATIENT_RESET_TEXT else "en"]
        if "error_reason" in ctx:
            r = ctx.pop("error_reason")
            ctx["error"] = (t["err"]["weak"] + ((" " + r[5:]) if lang == "en" else "")) if r.startswith("weak:") \
                else t["err"].get(r, t["err"]["invalid"])
        tok = auth.login_csrf_token(request)
        resp = page("patient_reset.html", request, title=t["title"], status_code=status_code,
                    t=t, lang=lang, rtl=lang in RTL_LANGUAGES, langs=LANGS, token=token or "",
                    login_csrf=tok, **ctx)
        resp.headers["Referrer-Policy"] = "no-referrer"
        ensure_login_csrf(request, resp, tok)
        return resp

    @app.get("/patient-reset-password", response_class=HTMLResponse)
    def patient_reset_get(request: Request):
        from .services import signin_help as sh

        token = _clean_token(request.query_params.get("t"))
        lang = _page_lang(request.query_params.get("lang"))
        db = session_factory()()
        try:
            status = sh.patient_reset_status(db, token)
        finally:
            db.close()
        t_err = None
        if status != "ok":
            from .comms.signin_text import PATIENT_RESET_TEXT
            t = PATIENT_RESET_TEXT[lang]
            key = "invalid" if status in ("missing", "invalid") else status
            t_err = t["err"].get(key, t["err"]["invalid"])
        return patient_reset_page(request, lang, token, 200, link_error=t_err)

    @app.post("/patient-reset-password", response_class=HTMLResponse)
    async def patient_reset_post(request: Request):
        from .comms.signin_text import PATIENT_RESET_TEXT
        from .services import signin_help as sh

        form = await request.form()
        lang = _page_lang(form.get("lang"))
        token = _clean_token(form.get("t"))
        pw, confirm = str(form.get("password", ""))[:256], str(form.get("confirm", ""))[:256]
        t = PATIENT_RESET_TEXT[lang]
        if not login_csrf_ok(request, str(form.get("csrf_token", ""))):
            return patient_reset_page(request, lang, token, 403, error=t["err"]["expired_page"])
        if not token or not pw or not confirm:
            return patient_reset_page(request, lang, token, 400, error=t["err"]["missing"],
                                      error_field="pw" if token else "code")

        def _work():
            db = session_factory()()
            try:
                u, reason = sh.complete_patient_reset(db, token=token, password=pw, confirm=confirm,
                                                      ip=auth.client_ip(request), lang=lang)
                db.commit()
                return u.username if u is not None else None, reason
            finally:
                db.close()

        username, reason = await asyncio.to_thread(_work)
        if username is None:
            field = "pw" if reason == "mismatch" or reason.startswith("weak:") else "code"
            status = 429 if reason == "rate_limited" else (422 if field == "pw" else 400)
            return patient_reset_page(request, lang, token, status, error_reason=reason, error_field=field)
        return patient_reset_page(request, lang, None, 200, done=True, username=username)

    @app.post("/logout")
    async def logout_post(request: Request):
        form = await request.form()
        tok = request.cookies.get(COOKIE)
        resp = RedirectResponse("/login?signed_out=1", status_code=303)
        if tok and not accounts.csrf_ok(tok, str(form.get("csrf_token", ""))):
            return JSONResponse({"detail": "security check failed (CSRF) - reload the page and try again"}, 403)

        def _work():
            db = session_factory()()
            try:
                do_logout(db, request, resp)
            finally:
                db.close()

        await asyncio.to_thread(_work)
        return resp

    @app.get("/account/password", response_class=HTMLResponse)
    def password_page(request: Request):
        return guarded("password.html", "/account/password", "Change password", request)

    @app.post("/account/password", response_class=HTMLResponse)
    async def password_post(request: Request):
        form = await request.form()
        tok = request.cookies.get(COOKIE)
        if not tok or not accounts.csrf_ok(tok, str(form.get("csrf_token", ""))):
            return RedirectResponse("/login", status_code=303)
        cur, new, again = (str(form.get(k, ""))[:256] for k in ("current_password", "new_password", "confirm_password"))

        def _work():
            db = session_factory()()
            try:
                u = resolve_user(request, db)
                if u is None:
                    return None, None
                from .api.deps import actor_from_user
                if new != again:
                    probs = ["The two new passwords do not match."]
                else:
                    probs = do_password_change(db, request, actor_from_user(u), cur, new)
                db.refresh(u)
                db.expunge(u)
                return u, probs
            finally:
                db.close()

        u, probs = await asyncio.to_thread(_work)
        if u is None:
            return RedirectResponse("/login", status_code=303)
        if probs:
            return page("password.html", request, user=u, title="Change password", errors=probs, status_code=422)
        return page("password.html", request, user=u, title="Change password", success=True,
                    next_href=ui.home_for(u.role))

    PAGES = [  # path, template, guard key, title
        ("/queue", "queue.html", "/queue", "Work queue"), ("/patient", "patient.html", "/patient", "My results"),
        ("/dashboard", "dashboard.html", "/dashboard", "Outcomes"),
        ("/governance", "governance.html", "/governance", "Approvals and models"),
        ("/audit", "audit.html", "/audit", "Audit trail"),
        ("/walkthrough", "walkthrough.html", "/walkthrough", "Guided walkthrough"),
        ("/ingest", "ingest.html", "/ingest", "Results inbox"), ("/consults", "consults.html", "/consults", "Consultations"),
        ("/overview", "overview.html", "/overview", "Overview"), ("/advanced", "advanced.html", "/advanced", "Advanced"),
        ("/admin/users", "admin_users.html", "/admin/users", "Users"),
        ("/admin/system", "admin_system.html", "/admin/system", "System"),
        ("/admin/about", "admin_about.html", "/admin/about", "About the system"),
        ("/patients", "patients.html", "/patients", "Patients"),
        ("/patients/new", "patient_register.html", "/patients/new", "Register a patient"),
        ("/recalls", "recalls.html", "/recalls", "Due for recall"),
        ("/summaries", "summaries.html", "/summaries", "Care summaries"),
    ]

    def _mk(tpl: str, key: str, title: str):
        def _route(request: Request):
            return guarded(tpl, key, title, request)
        return _route

    for path, tpl, key, title in PAGES:
        app.add_api_route(path, _mk(tpl, key, title), methods=["GET"], response_class=HTMLResponse,
                          include_in_schema=False)

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(case_id: int, request: Request):
        return guarded("case.html", "/cases", f"Case #{case_id}", request, case_id=case_id)

    @app.get("/patients/{patient_id}", response_class=HTMLResponse)
    def patient_record_page(patient_id: int, request: Request):
        return guarded("patient_record.html", "/patients", "Patient record", request, patient_id=patient_id)

    return app


app = create_app()
