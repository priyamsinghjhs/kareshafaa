"""FastAPI application factory."""
from __future__ import annotations

import logging
import re
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
from .db import init_db, session_factory
from .security import Actor
from .services.errors import DomainError
from .statemachine import TransitionError

log = logging.getLogger("wasl")
HERE = Path(__file__).resolve().parent
templates = Jinja2Templates(directory=str(HERE / "templates"))
CASE_RE = re.compile(r"^/(?:api/)?cases/(\d+)")
ANON = Actor(user_id=None, role="anonymous", display="anonymous", is_human=True)
METHOD_ACTION = {"GET": "R", "HEAD": "R", "POST": "E", "PUT": "U", "PATCH": "U", "DELETE": "D"}


def create_app() -> FastAPI:
    s = get_settings()
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        init_db()
        yield

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
        try:
            db = session_factory()()
            try:
                actor = getattr(request.state, "actor", None) or resolve_actor(request, db) or ANON
                m = CASE_RE.match(path)
                code = response.status_code
                outcome = "success" if code < 400 else "denied" if code in (401, 403) else "failure"
                audit.record(db, actor, METHOD_ACTION.get(request.method, "E"), f"http.{request.method.lower()}",
                             entity_type="http", entity_id=None, case_id=int(m.group(1)) if m else None, outcome=outcome,
                             path=path[:300], detail={"status": code, "ms": int((time.time() - start) * 1000),
                                                      "query": str(request.url.query)[:200] or None})
                db.commit()
            finally:
                db.close()
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
