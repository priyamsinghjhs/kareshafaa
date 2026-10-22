"""FastAPI application factory."""
from __future__ import annotations

import asyncio

import logging
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
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from . import __version__, audit, pwa
from .api import auth, cases, demo_api, governance, ingest, me, ops
from .api.deps import resolve_actor
from .config import get_settings
from .db import init_db, session_factory, session_scope
from .pwa import PUBLIC_PATHS as PWA_PUBLIC_PATHS
from .security import Actor
from .services.escalation import run_sweep
from .services.errors import DomainError, ServiceBusy
from .statemachine import TransitionError

log = logging.getLogger("kareshafaa")
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
CASE_RE = re.compile(r"^/(?:api/)?cases/(\d+)")
ANON = Actor(user_id=None, role="anonymous", display="anonymous", is_human=True)
METHOD_ACTION = {"GET": "R", "HEAD": "R", "POST": "E", "PUT": "U", "PATCH": "U", "DELETE": "D"}


_LAST_SWEEP_AT = None  # process-local wall-clock guard


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


def _elect_sweeper() -> bool:
    """With several uvicorn workers only one process runs the SLA job (non-blocking flock election)."""
    global _SWEEP_LOCK_FD
    try:
        import fcntl
        import os
        from sqlalchemy.engine import make_url

        from .audit import lock_key
        u = make_url(get_settings().database_url)
        ident = os.path.realpath(u.database) if u.database and u.database != ":memory:" else str(u)
        # one sweeper per DATABASE (round 21: a shared lock let a server on another database "own" the sweep)
        fd = os.open(str(get_settings().data_dir / f".sla-sweeper-{lock_key(ident)}.lock"), os.O_CREAT | os.O_RDWR,
                     0o600)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        _SWEEP_LOCK_FD = fd  # held for the life of the process
        return True
    except BlockingIOError:
        return False
    except ImportError:  # pragma: no cover
        return True


_SWEEP_LOCK_FD: int | None = None


async def _sweep_loop(interval: int) -> None:
    """Background SLA job: evaluates every open step clock and raises the escalation ladder.
    Runs in a worker thread so request handling is never blocked; failures are logged, never fatal."""
    if not _elect_sweeper():
        log.info("another worker owns the background SLA sweep")
        return
    log.info("background SLA sweep every %ss", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            t = time.time()
            out = await asyncio.to_thread(_sweep_once)
            log.info("background SLA sweep: %s in %.0f ms", out, (time.time() - t) * 1000)
        except Exception:  # noqa: BLE001
            log.exception("background SLA sweep failed")


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
        except Exception:  # noqa: BLE001 - the request log still records the 503
            log.warning("could not audit a request timeout", exc_info=True)
        resp = JSONResponse({"detail": "the request took too long and was stopped - nothing was saved; please retry"},
                            status_code=503, headers={"Retry-After": str(cfg.retry_after_s)})
        await resp(scope, receive, send)


def create_app() -> FastAPI:
    s = get_settings()
    if not logging.getLogger().handlers:
        logging.basicConfig(level=logging.INFO, format="%(levelname)s:     %(name)s %(message)s")
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        # sqlite3 releases and re-acquires the GIL around every row fetched; with the default 5 ms switch interval
        # a thread fetching ~1,500 queue rows while other request threads run can wait 5 ms per row (convoy
        # effect, measured p95 1.3 s at 10 concurrent clients). A shorter interval bounds that wait.
        sys.setswitchinterval(s.gil_switch_interval_ms / 1000)
        task = asyncio.create_task(_sweep_loop(s.sweep_interval_seconds)) if s.sweep_interval_seconds > 0 else None
        hb = asyncio.create_task(_heartbeat_loop()) if os.environ.get("KARESHAFAA_HEARTBEAT_DIR") else None
        yield
        if task:
            task.cancel()
        if hb:
            hb.cancel()

    app = FastAPI(title="KareShafaa - Closing the Screening-to-Care Loop", version=__version__, lifespan=lifespan,
                  description="Prototype on SYNTHETIC data only. Not a medical device. No real messages are sent.")

    inflight = {"n": 0, "shed": 0}

    # Round 23: per-request time budget, a pure ASGI layer INSIDE the audit middleware (so a cut-off request is
    # still logged as 503). It answers without waiting for the stuck thread (kareshafaa/request_timeout.py).
    app.add_middleware(TimeBudgetMiddleware)

    @app.middleware("http")
    async def audit_every_request(request: Request, call_next):
        """Append-only audit of EVERY API read and write (and page view), including denied attempts."""
        start = time.time()
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static") or path in ("/favicon.ico", "/healthz") or path in PWA_PUBLIC_PATHS:
            return response
        state_actor = getattr(request.state, "actor", None)
        shed, inflight["shed"] = inflight["shed"], 0
        try:
            # sync DB work runs in a worker thread so the event loop is never blocked
            items = await asyncio.to_thread(_prepare_request_audit, request, state_actor, path,
                                            response.status_code, start, shed)
            await asyncio.wrap_future(_BATCHER.submit(items))  # committed before the response leaves
        except Exception as e:  # noqa: BLE001
            inflight["shed"] += shed  # keep the shed count for the next audited request
            if request.method in ("GET", "HEAD") and response.status_code < 400:
                # round 21: never hand out data whose access could not be logged - the read is refused (503)
                busy = isinstance(e, (ServiceBusy, SATimeoutError, OperationalError))
                (log.warning if busy else log.exception)("request audit failed; read refused (503): %r", e)
                return JSONResponse({"detail": "the service is busy - please retry shortly"}, status_code=503,
                                    headers={"Retry-After": str(s.retry_after_s)})
            log.exception("request audit failed")  # a committed write is still reported as done
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

    @app.exception_handler(DomainError)
    async def _domain(_req: Request, exc: DomainError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

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

    for r in (auth.router, cases.router, me.router, ops.router, governance.router, ingest.router, demo_api.router,
              pwa.router):
        app.include_router(r)
    app.mount("/static", StaticFiles(directory=str(HERE / "static")), name="static")

    @app.get("/favicon.ico", include_in_schema=False)
    def favicon() -> RedirectResponse:
        return RedirectResponse("/static/favicon.svg", status_code=308)

    @app.get("/healthz")
    def healthz() -> dict:
        db = session_factory()()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        from .request_timeout import TIMEOUTS
        return {"status": "ok", "version": __version__, "request_timeouts": TIMEOUTS["count"],
                "slow_committing_requests": TIMEOUTS["slow_committing"]}

    def page(name: str, request: Request, **ctx) -> HTMLResponse:
        return templates.TemplateResponse(request, name, {"demo_logins": s.demo_logins, "version": __version__, **ctx})

    @app.get("/", response_class=HTMLResponse)
    def home(request: Request):
        return page("home.html", request, title="Home")

    @app.get("/queue", response_class=HTMLResponse)
    def queue_page(request: Request):
        return page("queue.html", request, title="Work queue")

    @app.get("/cases/{case_id}", response_class=HTMLResponse)
    def case_page(case_id: int, request: Request):
        return page("case.html", request, title=f"Case #{case_id}", case_id=case_id)

    @app.get("/patient", response_class=HTMLResponse)
    def patient_page(request: Request):
        return page("patient.html", request, title="My results")

    @app.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(request: Request):
        return page("dashboard.html", request, title="KPI dashboard")

    @app.get("/governance", response_class=HTMLResponse)
    def governance_page(request: Request):
        return page("governance.html", request, title="Governance")

    @app.get("/audit", response_class=HTMLResponse)
    def audit_page(request: Request):
        return page("audit.html", request, title="Audit trail")

    @app.get("/demo", response_class=HTMLResponse)
    def demo_page(request: Request):
        return page("demo.html", request, title="Demo scenarios")

    @app.get("/ingest", response_class=HTMLResponse)
    def ingest_page(request: Request):
        return page("ingest.html", request, title="Results inbox")

    @app.get("/consults", response_class=HTMLResponse)
    def consults_page(request: Request):
        return page("consults.html", request, title="Consultations")

    @app.get("/login")
    def login_redirect():
        return RedirectResponse("/")

    return app


app = create_app()
