"""SLA sweep and escalation ladder (Wasl design 2.5):
L1 nudge to owner + patient reminder (50 % of SLA) -> L2 programme coordinator/supervisor (breach)
-> L3 programme clinical lead (1.5x SLA; T0 goes straight here on breach)
-> L4 programme medical director / duty clinician (2x SLA, T0/T1 only; T2/T3 go to the weekly review list).

Escalations alert named humans. They never change a case's clinical state or close a case.
The only automatic ownership change is the safety net that moves a case whose owner has become
inactive to the programme's team pool, so that a case is never without an accountable owner.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import Case, Communication, Escalation, OwnershipEvent, Practitioner, StepTask, utcnow
from ..security import SYSTEM, Actor
from ..sla import SlaLookup, rag_status
from ..statemachine import S
from .workflow import add_lesson, programme_clinical_lead, programme_director, programme_pool

PATIENT_FACING = {S.PATIENT_NOTIFIED, S.NO_SHOW, S.APPOINTMENT_BOOKED, S.ACKNOWLEDGED}
BREACH_REASON = {
    "issued": "triage_delay", "triaged": "notification_delay", "patient_notified": "patient_not_responding",
    "acknowledged": "awaiting_next_step", "confirmatory_test": "awaiting_result", "consultation": "consultation_delay",
    "referral_sent": "referral_response_delay", "referral_rejected": "rerouting_delay",
    "referral_accepted": "booking_delay", "appointment_booked": "appointment_wait",
    "appointment_attended": "care_plan_delay", "treatment_or_surveillance_started": "closure_documentation_delay",
    "no_show": "rebooking_delay", "unreachable": "unreachable_review_delay",
    "declined_pending_review": "decline_review_delay",
}
LEVEL_NAMES = {1: "L1 reminder to owner", 2: "L2 programme coordinator", 3: "L3 clinical lead", 4: "L4 medical director"}


def _supervisor(db: Session, owner: Practitioner) -> Practitioner:
    if owner.supervisor_id:
        sup = db.get(Practitioner, owner.supervisor_id)
        if sup and sup.active:
            return sup
    sup = db.scalar(select(Practitioner).where(Practitioner.role == "coordinator", Practitioner.specialty == "supervisor",
                                               Practitioner.active.is_(True)))
    return sup or owner


def required_level(case: Case, step: StepTask, now: datetime, sla: SlaLookup) -> tuple[int, str, float]:
    cfg = get_settings()
    rag, frac = rag_status(step.started_at, step.due_at, now, sla.reminder_fraction(step.state, case.tier))
    if rag == "none":
        return 0, rag, frac
    level = 0
    if rag in ("amber", "red"):
        level = 1
    if frac >= 1:
        level = 3 if case.tier == "critical" else 2
    if frac >= 1 + cfg.clinical_lead_after_breach_fraction:
        level = 3
    if frac >= cfg.director_after_fraction and case.tier in ("critical", "urgent"):
        level = 4
    return level, rag, frac


def run_sweep(db: Session, now: datetime | None = None, actor: Actor = SYSTEM, case_ids: list[int] | None = None) -> dict:
    now = now or utcnow()
    sla = SlaLookup.load(db)
    q = select(Case).where(Case.state != S.CLOSED)
    if case_ids:
        q = q.where(Case.id.in_(case_ids))
    out = {"checked": 0, "reminders": 0, "supervisor": 0, "clinical_lead": 0, "director": 0, "auto_pooled": 0, "breaches": 0}
    cases = db.scalars(q).all()
    # bulk-load (no per-case queries): in-progress steps for the open cases and the small practitioner table
    sq = select(StepTask).where(StepTask.status == "in-progress")
    if case_ids:
        sq = sq.where(StepTask.case_id.in_(case_ids))
    steps = {s.case_id: s for s in db.scalars(sq)}
    pracs = {p.id: p for p in db.scalars(select(Practitioner))}
    for case in cases:
        out["checked"] += 1
        step = steps.get(case.id)
        if step is None:
            continue
        owner = pracs.get(case.owner_id)
        if owner is not None and owner.active:
            level, _rag, _frac = required_level(case, step, now, sla)
            if level <= case.escalation_level and (level < 2 or step.breached):
                continue  # fast path: nothing new for this case
        if owner is None or not owner.active:
            pool = programme_pool(db, case.programme)
            db.add(OwnershipEvent(case_id=case.id, from_owner_id=case.owner_id, to_owner_id=pool.id, kind="auto_pool",
                                  reason="owner inactive - moved to team pool (unassigned queue)", at=now))
            audit.record(db, actor, "U", "case.auto_pooled", entity_type="Task", entity_id=case.id, case_id=case.id,
                         ts=now, detail={"from_owner": case.owner_id, "to_pool": pool.id})
            case.owner_id = pool.id
            step.owner_id = pool.id
            owner = pool
            out["auto_pooled"] += 1
        level, rag, frac = required_level(case, step, now, sla)
        if level >= 2 and not step.breached:
            step.breached = True
            out["breaches"] += 1
            add_lesson(db, case, "overdue", BREACH_REASON.get(step.state, "sla_breach"), actor,
                       detail=f"SLA breached in {step.state} ({frac:.0%} of {step.sla_hours}h)", now=now)
        for lvl in range(case.escalation_level + 1, level + 1):
            if lvl == 1:
                target = owner
            elif lvl == 2:
                target = _supervisor(db, owner)
            else:
                target = programme_clinical_lead(db, case.programme)
            if lvl == 4:
                target = programme_director(db)
            esc = Escalation(case_id=case.id, step_task_id=step.id, level=lvl, target_practitioner_id=target.id,
                             created_at=now, note=f"{LEVEL_NAMES[lvl]}: {step.state} at {frac:.0%} of SLA")
            db.add(esc)
            db.add(Communication(case_id=case.id, recipient_practitioner_id=target.id, category="escalation",
                                 channel="internal", content=f"[SIMULATED internal alert] {esc.note} - case #{case.id}",
                                 status="completed", simulated=True, sent_at=now))
            db.flush()
            audit.record(db, actor, "C", f"escalation.level{lvl}", entity_type="Escalation", entity_id=esc.id,
                         case_id=case.id, ts=now, detail={"target": target.id, "state": step.state,
                                                          "fraction": round(frac, 3)})
            out[{1: "reminders", 2: "supervisor", 3: "clinical_lead", 4: "director"}[lvl]] += 1
            if lvl == 1 and S(case.state) in PATIENT_FACING and not case.patient.deceased:
                from ..comms.service import send_with_ladder

                send_with_ladder(db, case, "reminder", actor, now=now, max_channels=3)
        case.escalation_level = max(case.escalation_level, level)
    db.flush()
    audit.record(db, actor, "E", "sla.sweep", ts=now, detail=out)
    return out


def acknowledge_escalation(db: Session, esc: Escalation, actor: Actor, note: str, now: datetime | None = None) -> Escalation:
    from .errors import DomainError, Forbidden

    if not actor.can("escalation:ack"):
        raise Forbidden("role lacks escalation:ack")
    if esc.acknowledged_at:
        raise DomainError("already acknowledged")
    esc.acknowledged_at = now or utcnow()
    esc.acknowledged_by_user_id = actor.user_id
    esc.note = (esc.note or "") + f" | ack: {note}"
    audit.record(db, actor, "U", "escalation.acknowledged", entity_type="Escalation", entity_id=esc.id,
                 case_id=esc.case_id, ts=esc.acknowledged_at)
    return esc
