"""Work queue, analytics (aggregates only), directory, admin (sweep, audit)."""
from __future__ import annotations

import threading

from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..models import AuditEvent, Organization, Practitioner, SimulationRun, utcnow
from ..services.errors import Forbidden, Invalid
from ..security import SYSTEM, Actor
from ..services import analytics
from ..services.escalation import run_sweep
from ..services.improve import lesson_summary
from ..services.queue import VIEWS, work_queue
from ..synth.simulator import run_simulation
from . import schemas as sch
from .deps import require

router = APIRouter(prefix="/api", tags=["operations"])


# At most 2 queue computations at a time per process. Each one fetches every open case row; sqlite3 hands the GIL
# back and forth on every row, so several at once in one process convoy (measured p95 1.1-1.4 s at 10 clients).
# Waiting for a slot is far cheaper than convoying.
_QUEUE_SLOTS = threading.BoundedSemaphore(2)


@router.get("/queue")
def queue(view: str = Query("all"), programme: str | None = None, limit: int = Query(50, ge=1, le=1000),
          offset: int = Query(0, ge=0), q: str | None = Query(None, max_length=80),
          sort: str = Query("priority"), actor: Actor = Depends(require("queue:read")), db: Session = Depends(get_db)) -> dict:
    if view not in VIEWS:
        view = "all"
    sort = "deadline" if sort == "deadline" else "priority"
    with _QUEUE_SLOTS:
        return work_queue(db, actor, view, limit=limit, programme=programme, offset=offset, search=q, sort=sort)


@router.post("/sla/sweep")
def sweep(body: sch.SweepIn | None = None, actor: Actor = Depends(require("sweep:run")), db: Session = Depends(get_db)) -> dict:
    now = None
    if body and body.hours_ahead:
        cfg = get_settings()
        if cfg.env == "prod" or not cfg.demo_logins:
            raise Forbidden("fast-forwarding the clock is switched off on this server")
        if not body.case_ids or len(body.case_ids) != 1:
            raise Invalid("the clock can only be fast-forwarded for a single case")
        now = utcnow() + timedelta(hours=body.hours_ahead)
        audit.record(db, actor, "E", "demo.clock_fast_forward", case_id=body.case_ids[0],
                     detail={"hours_ahead": body.hours_ahead, "simulated": True})
    # an administrator (no clinical messaging rights) triggers the same rules-engine run as the scheduler;
    # who asked for it is audited separately
    run_as = actor if actor.can("comm:send") else SYSTEM
    if run_as is SYSTEM:
        audit.record(db, actor, "E", "sla.sweep_requested", detail={"case_ids": body.case_ids if body else None})
    out = run_sweep(db, now=now, actor=run_as, case_ids=body.case_ids if body else None)
    out["simulated_clock_hours_ahead"] = body.hours_ahead if body else 0
    db.commit()
    return out


@router.get("/analytics/kpis")
def kpis(actor: Actor = Depends(require("analytics:read")), db: Session = Depends(get_db)) -> dict:
    return analytics.cached("kpis", db, lambda: analytics.kpis(db))


@router.get("/analytics/impact")
def impact(programme: str | None = Query(None, max_length=40), format: str = Query("json", pattern="^(json|csv)$"),
           actor: Actor = Depends(require("analytics:read")), db: Session = Depends(get_db)):
    """Loop-closure impact report (aggregates only, small cells suppressed). CSV export is audited."""
    from ..services.impact import PROGRAMME_LABEL, impact_csv, impact_report

    if programme and programme not in PROGRAMME_LABEL:
        raise Invalid("unknown programme")
    rep = analytics.cached(f"impact:{programme or 'all'}", db, lambda: impact_report(db, programme))
    if format == "csv":
        audit.record(db, actor, "E", "analytics.impact_export", detail={"programme": programme, "format": "csv"})
        db.commit()
        name = f"kareshafaa_impact_{programme or 'all'}_{rep['generated_at'][:10]}.csv"
        return Response(impact_csv(rep), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})
    return rep


@router.get("/analytics/fairness")
def fairness(actor: Actor = Depends(require("analytics:read")), db: Session = Depends(get_db)) -> dict:
    return {"label": "Live figures from the records in this system", "suppression_threshold": get_settings().suppression_threshold,
            "slices": analytics.cached("fairness", db, lambda: analytics.fairness_slices(db)),
            "score_bands": analytics.cached("score_bands", db, lambda: analytics.score_band_equity(db))}


@router.get("/analytics/lessons")
def lessons(actor: Actor = Depends(require("lesson:read")), db: Session = Depends(get_db)) -> dict:
    return lesson_summary(db, get_settings().suppression_threshold)


@router.get("/analytics/simulation")
def simulation(actor: Actor = Depends(require("analytics:read")), db: Session = Depends(get_db)) -> dict:
    run = db.scalar(select(SimulationRun).order_by(SimulationRun.id.desc()).limit(1))
    if run is None:
        return {"label": "SIMULATED", "available": False}
    return {"available": True, "run_id": run.id, "created_at": run.created_at.isoformat(), **run.results}


@router.post("/analytics/simulation/run")
def simulation_run(n: int = Query(2000, ge=100, le=20000), seed: int | None = None,
                   actor: Actor = Depends(require("simulation:run")), db: Session = Depends(get_db)) -> dict:
    res = run_simulation(n=n, seed=seed or get_settings().seed)
    run = SimulationRun(params={"n": n, "seed": seed}, results=res)
    db.add(run)
    db.flush()
    audit.record(db, actor, "C", "simulation.run", entity_type="SimulationRun", entity_id=run.id, detail={"n": n})
    db.commit()
    return {"ok": True, "run_id": run.id}


@router.get("/directory/practitioners")
def practitioners(role: str | None = None, org_id: int | None = None, actor: Actor = Depends(require("case:read")),
                  db: Session = Depends(get_db)) -> dict:
    q = select(Practitioner).where(Practitioner.active.is_(True))
    if role:
        q = q.where(Practitioner.role == role)
    if org_id:
        q = q.where(Practitioner.org_id == org_id)
    return {"items": [{"id": p.id, "name": p.name, "role": p.role, "org_id": p.org_id, "specialty": p.specialty,
                       "programme": p.programme, "is_pool": p.is_pool} for p in db.scalars(q.order_by(Practitioner.name))]}


@router.get("/directory/organizations")
def organizations(actor: Actor = Depends(require("case:read")), db: Session = Depends(get_db)) -> dict:
    return {"items": [{"id": o.id, "name": o.name, "kind": o.kind, "region": o.region}
                      for o in db.scalars(select(Organization).order_by(Organization.kind, Organization.name))]}


@router.get("/audit")
def audit_log(case_id: int | None = None, actor_role: str | None = None, event: str | None = Query(None, max_length=80),
              limit: int = Query(200, ge=1, le=2000), before_id: int | None = None,
              actor: Actor = Depends(require("audit:read")), db: Session = Depends(get_db)) -> dict:
    q = select(AuditEvent).order_by(AuditEvent.id.desc())
    if case_id:
        q = q.where(AuditEvent.case_id == case_id)
    if actor_role:
        q = q.where(AuditEvent.actor_role == actor_role)
    if event:
        q = q.where(AuditEvent.event.like(f"{event}%"))
    if before_id:
        q = q.where(AuditEvent.id < before_id)
    rows = db.scalars(q.limit(limit)).all()
    return {"items": [{"id": e.id, "ts": e.ts.isoformat(), "actor": e.actor_display, "role": e.actor_role, "action": e.action,
                       "event": e.event, "entity": f"{e.entity_type or ''}/{e.entity_id or ''}", "case_id": e.case_id,
                       "outcome": e.outcome, "path": e.path, "detail": e.detail, "hash": e.hash[:16]} for e in rows]}


@router.get("/audit/verify")
def audit_verify(actor: Actor = Depends(require("audit:verify")), db: Session = Depends(get_db)) -> dict:
    return audit.verify_chain(db)
