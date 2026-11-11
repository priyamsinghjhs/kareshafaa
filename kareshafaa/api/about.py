"""About the system (administrators): architecture, integrations, Malaffi readiness, deployment profiles, background
jobs and the API reference. Nothing here is clinical data."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from .. import __version__
from ..config import get_settings
from ..db import get_db
from ..integration import EMIRATE_PROFILES, MALAFFI_READINESS, integration_status
from ..security import Actor
from .deps import require

router = APIRouter(prefix="/api/admin/about", tags=["admin"])

COMPONENTS = [
    {"name": "Results inbox", "does": "HL7 v2 ORU^R01 and FHIR R4 results in, idempotent, acknowledgement back, human match review", "code": "kareshafaa/services/ingest.py"},
    {"name": "Clinical rules engine", "does": "Deterministic: is the result actionable, tier T0-T3, pathway, end-to-end target; unclear results go to a clinician", "code": "kareshafaa/clinical_rules.py"},
    {"name": "Loop engine", "does": "Case state machine with one accountable owner, closure only with evidence or a signed decision", "code": "kareshafaa/statemachine.py, services/workflow.py"},
    {"name": "Deadlines and escalation", "does": "Step clocks per state and tier on the UAE working-day calendar, L1-L4 escalation ladder", "code": "kareshafaa/sla.py, services/escalation.py"},
    {"name": "Patient communication", "does": "Approved templates in English, Arabic, Hindi and Urdu, teach-back check, channel fallback (simulated delivery)", "code": "kareshafaa/comms/"},
    {"name": "Outreach model", "does": "scikit-learn missed-appointment risk (Low/Medium/High), fairness gates, approval, rollback; deterministic queue priority; matching suggestions; contact-time hint; coordinator copilot (LangGraph)", "code": "kareshafaa/ml/"},
    {"name": "Audit trail", "does": "Append-only, SHA-256 hash-chained log of every read and write", "code": "kareshafaa/audit.py"},
    {"name": "Web app and patient app", "does": "Server-rendered pages, installable PWA, role-based navigation, right-to-left layouts", "code": "kareshafaa/templates, static/"},
    {"name": "Database", "does": "PostgreSQL (Neon) in hosting, SQLite locally; FHIR-shaped tables", "code": "kareshafaa/models.py"},
]
JOBS = [
    {"name": "Deadline check (SLA sweep)", "runs": "every KARESHAFAA_SWEEP_INTERVAL_SECONDS (300 s on hosting)", "how": "one elected worker per host; re-runs on a conflicting edit; skips if the clock moves backwards"},
    {"name": "Retention purge", "runs": "with the deadline check, at most hourly", "how": "removes sign-in attempts after 90 days and ended sessions after 30 days; clinical data is never deleted"},
    {"name": "Audit writer", "runs": "continuously", "how": "writes request audit rows to the hash-chained log in small batches"},
    {"name": "One-time data steps", "runs": "once after start-up", "how": "small batches in a worker thread; never blocks serving"},
]


def _api_reference(request: Request) -> list[dict]:
    spec = request.app.openapi()
    groups: dict[str, list] = {}
    for path, ops in spec.get("paths", {}).items():
        if "demo" in path:  # test-only tools, switched off on hosted servers
            continue
        for method, op in ops.items():
            tag = (op.get("tags") or ["other"])[0]
            groups.setdefault(tag, []).append({"method": method.upper(), "path": path,
                                               "summary": op.get("summary") or "", "doc": (op.get("description") or "").split("\n")[0][:200]})
    return [{"tag": t, "endpoints": sorted(v, key=lambda e: (e["path"], e["method"]))} for t, v in sorted(groups.items())]


@router.get("")
def about(request: Request, actor: Actor = Depends(require("config:read")), db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    return {"version": __version__, "mode": s.mode, "components": COMPONENTS, "jobs": JOBS,
            "integrations": integration_status(db), "malaffi": MALAFFI_READINESS, "emirates": EMIRATE_PROFILES,
            "api": _api_reference(request),
            "notes": {"malaffi": "No connection to Malaffi or partnership with Abu Dhabi Health Data Services is claimed. "
                                 "Requirements are taken from malaffi.ae and doh.gov.ae (checked 3 Nov 2026)."}}


@router.get("/openapi.json")
def openapi(request: Request, actor: Actor = Depends(require("config:read"))) -> JSONResponse:
    spec = dict(request.app.openapi())
    spec["paths"] = {k: v for k, v in spec.get("paths", {}).items() if "demo" not in k}
    return JSONResponse(spec, headers={"Content-Disposition": 'attachment; filename="kareshafaa-openapi.json"'})
