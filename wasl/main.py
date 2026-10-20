"""FastAPI application factory."""
from __future__ import annotations

import asyncio

import logging
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import text

from . import __version__, audit
from .api import auth, cases, demo_api, governance, ingest, me, ops
from .api.deps import resolve_actor
from .config import get_settings
from .db import init_db, session_factory, session_scope
from .security import Actor
from .services.escalation import run_sweep
from .services.errors import DomainError
from .statemachine import TransitionError

log = logging.getLogger("wasl")
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
CASE_RE = re.compile(r"^/(?:api/)?cases/(\d+)")
ANON = Actor(user_id=None, role="anonymous", display="anonymous", is_human=True)
METHOD_ACTION = {"GET": "R", "HEAD": "R", "POST": "E", "PUT": "U", "PATCH": "U", "DELETE": "D"}


def _sweep_once() -> dict:
    with session_scope() as db:
        return run_sweep(db)


def _elect_sweeper() -> bool:
    """With several uvicorn workers only one process runs the SLA job (non-blocking flock election)."""
    global _SWEEP_LOCK_FD
    try:
        import fcntl
        import os
        fd = os.open(str(get_settings().data_dir / ".sla-sweeper.lock"), os.O_CREAT | os.O_RDWR, 0o600)
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


def _audit_request(request: Request, state_actor, path: str, code: int, start: float) -> None:
    db = session_factory()()
    try:
        actor = state_actor or resolve_actor(request, db) or ANON
        m = CASE_RE.match(path)
        outcome = "success" if code < 400 else "denied" if code in (401, 403) else "failure"
        audit.record(db, actor, METHOD_ACTION.get(request.method, "E"), f"http.{request.method.lower()}",
                     entity_type="http", entity_id=None, case_id=int(m.group(1)) if m else None, outcome=outcome,
                     path=path[:300], detail={"status": code, "ms": int((time.time() - start) * 1000),
                                              "query": str(request.url.query)[:200] or None})
        db.commit()
    finally:
        db.close()


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
        yield
        if task:
            task.cancel()

    app = FastAPI(title="Wasl - Closing the Screening-to-Care Loop", version=__version__, lifespan=lifespan,
                  description="Prototype on SYNTHETIC data only. Not a medical device. No real messages are sent.")

    @app.middleware("http")
    async def audit_every_request(request: Request, call_next):
        """Append-only audit of EVERY API read and write (and page view), including denied attempts."""
        start = time.time()
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/static") or path in ("/favicon.ico", "/healthz"):
            return response
        state_actor = getattr(request.state, "actor", None)
        try:
            # sync DB work runs in a worker thread so the event loop is never blocked
            await asyncio.to_thread(_audit_request, request, state_actor, path, response.status_code, start)
        except Exception:  # noqa: BLE001 - never break a response because of the request log
            log.exception("request audit failed")
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        return response

    @app.exception_handler(DomainError)
    async def _domain(_req: Request, exc: DomainError):
        return JSONResponse({"detail": exc.message}, status_code=exc.status_code)

    @app.exception_handler(TransitionError)
    async def _transition(_req: Request, exc: TransitionError):
        return JSONResponse({"detail": str(exc)}, status_code=409)

    @app.exception_handler(PermissionError)
    async def _perm(_req: Request, exc: PermissionError):
        return JSONResponse({"detail": str(exc)}, status_code=403)

    @app.exception_handler(RequestValidationError)
    async def _validation(_req: Request, exc: RequestValidationError):
        errs = [{"loc": list(e.get("loc", [])), "msg": e.get("msg")} for e in exc.errors()]
        return JSONResponse({"detail": "invalid input", "errors": errs}, status_code=422)

    for r in (auth.router, cases.router, me.router, ops.router, governance.router, ingest.router, demo_api.router):
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
        return {"status": "ok", "version": __version__}

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
