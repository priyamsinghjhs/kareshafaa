"""Coordinator work queue: overdue / unassigned / rejected / escalated / calls / transfers / consults."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import CallTask, Case, ConsultRequest, Escalation, Practitioner, StepTask, utcnow
from ..security import PROGRAMME_WIDE_ROLES, Actor
from ..sla import SlaLookup, rag_status
from ..statemachine import LABELS, S
from .access import involved_org_ids

RAG_RANK = {"red": 0, "amber": 1, "green": 2, "none": 3}
TIER_RANK = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}
VIEWS = ("all", "overdue", "unassigned", "rejected", "escalated", "calls", "transfers", "consults", "mine")


def case_row(case: Case, step: StepTask | None, now: datetime, sla: SlaLookup, flags: dict) -> dict:
    rag, frac = ("none", 0.0)
    if step is not None:
        rag, frac = rag_status(step.started_at, step.due_at, now, sla.reminder_fraction(step.state, case.tier))
    p = case.patient
    return {
        "id": case.id, "patient": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "language": p.language,
        "programme": case.programme, "tier": case.tier, "state": case.state, "state_label": LABELS.get(case.state, case.state),
        "owner_id": case.owner_id, "owner": case.owner.name, "owner_is_pool": case.owner.is_pool,
        "due_at": case.due_at.isoformat() if case.due_at else None, "sla_status": rag, "sla_fraction": round(frac, 2),
        "escalation_level": case.escalation_level,
        "risk_score": None if case.risk_score is None else round(case.risk_score, 3),
        "risk_model_version": case.risk_model_version,
        "transfer_pending": case.transfer_pending_owner_id is not None, **flags,
    }


def sort_key(row: dict) -> tuple:
    # 1) SLA status (clinical safety first)  2) urgency tier  3) ML outreach priority  4) due time
    return (RAG_RANK[row["sla_status"]], TIER_RANK.get(row["tier"], 9), -(row["risk_score"] or 0.0), row["due_at"] or "9")


def work_queue(db: Session, actor: Actor, view: str = "all", now: datetime | None = None, limit: int = 200,
               programme: str | None = None) -> dict:
    now = now or utcnow()
    sla = SlaLookup.load(db)
    q = select(Case).where(Case.state != S.CLOSED).options(joinedload(Case.patient), joinedload(Case.owner))
    if programme:
        q = q.where(Case.programme == programme)
    cases = db.scalars(q).unique().all()
    steps = {s.case_id: s for s in db.scalars(select(StepTask).where(StepTask.status == "in-progress"))}
    open_calls = set(db.scalars(select(CallTask.case_id).where(CallTask.status == "requested")))
    open_esc = set(db.scalars(select(Escalation.case_id).where(Escalation.acknowledged_at.is_(None))))
    open_consults = set(db.scalars(select(ConsultRequest.case_id).where(ConsultRequest.status == "requested")))
    inactive = set(db.scalars(select(Practitioner.id).where(Practitioner.active.is_(False))))
    counts = {v: 0 for v in VIEWS}
    rows = []
    for c in cases:
        if actor.role not in PROGRAMME_WIDE_ROLES and actor.org_id not in involved_org_ids(db, c):
            continue
        flags = {"call_task": c.id in open_calls, "open_escalation": c.id in open_esc,
                 "open_consult": c.id in open_consults,
                 "unassigned": c.owner.is_pool or c.owner_id in inactive}
        row = case_row(c, steps.get(c.id), now, sla, flags)
        member = {
            "all": True, "overdue": row["sla_status"] == "red", "unassigned": flags["unassigned"],
            "rejected": c.state == S.REFERRAL_REJECTED, "escalated": flags["open_escalation"],
            "calls": flags["call_task"], "transfers": row["transfer_pending"], "consults": flags["open_consult"],
            "mine": actor.practitioner_id == c.owner_id,
        }
        for v, m in member.items():
            counts[v] += int(m)
        if member.get(view, False):
            rows.append(row)
    rows.sort(key=sort_key)
    return {"view": view, "counts": counts, "total": len(rows), "items": rows[:limit],
            "ordering": "SLA status, then urgency tier, then ML outreach priority (prioritisation only), then due time"}
