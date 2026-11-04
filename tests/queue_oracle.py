"""TEST ORACLE ONLY: the work queue as computed before stored SLA thresholds (Python RAG + sort over every open
case). Kept verbatim (apart from the cache) so tests can prove the SQL path in kareshafaa/services/queue.py returns
the same ordering, filters, counts and per-role contents. Never imported by the application."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import case as sa_case, or_, select
from sqlalchemy.orm import Session, aliased

from kareshafaa.models import AppointmentRequest, CallTask, Case, ConsultRequest, Escalation, Patient, Practitioner, StepTask, utcnow
from kareshafaa.security import PROGRAMME_WIDE_ROLES, Actor
from kareshafaa.services.queue import VIEWS, _org_scope, sort_key
from kareshafaa.sla import SlaLookup, rag_status
from kareshafaa.statemachine import LABELS, S


def work_queue_python(db: Session, actor: Actor, view: str = "all", now: datetime | None = None, limit: int = 200,
               programme: str | None = None, offset: int = 0, search: str | None = None) -> dict:
    """One pass over open cases using column queries (no ORM object hydration, no N+1).

    Cost is O(open cases) with ~8 set-based queries regardless of size; closed cases are never touched
    (index on cases.state)."""
    now = now or utcnow()
    sla = SlaLookup.load(db)
    owner = aliased(Practitioner)
    q = (select(Case.id, Case.programme, Case.tier, Case.state, Case.owner_id, Case.due_at, sa_case((Case.target_alert_level > Case.escalation_level, Case.target_alert_level), else_=Case.escalation_level),
                Case.risk_score, Case.risk_model_version, Case.transfer_pending_owner_id, Case.review_required,
                Patient.given_name, Patient.family_name, Patient.mrn, Patient.language,
                owner.name, owner.is_pool, owner.active, StepTask.started_at, StepTask.due_at, StepTask.state)
         .join(Patient, Patient.id == Case.patient_id).join(owner, owner.id == Case.owner_id)
         .outerjoin(StepTask, (StepTask.case_id == Case.id) & (StepTask.status == "in-progress"))
         .where(Case.state != S.CLOSED))
    if programme:
        q = q.where(Case.programme == programme)
    if search:
        like = f"%{search.strip()}%"
        cond = or_(Patient.mrn.ilike(like), Patient.given_name.ilike(like), Patient.family_name.ilike(like),
                   (Patient.given_name + " " + Patient.family_name).ilike(like))
        if search.strip().lstrip("#").isdigit():
            cond = or_(cond, Case.id == int(search.strip().lstrip("#")))
        q = q.where(cond)
    scoped_role = actor.role not in PROGRAMME_WIDE_ROLES

    def load():
        rows = db.execute(q).all()
        ids_ = [r[0] for r in rows]
        return (rows, _org_scope(db, ids_),
                set(db.scalars(select(CallTask.case_id).where(CallTask.status == "requested"))),
                set(db.scalars(select(Escalation.case_id).where(Escalation.acknowledged_at.is_(None)))),
                set(db.scalars(select(ConsultRequest.case_id).where(ConsultRequest.status == "requested"))),
                set(db.scalars(select(AppointmentRequest.case_id).where(AppointmentRequest.kind == "change",
                                                                        AppointmentRequest.status == "open"))))

    rows_db, orgs, open_calls, open_esc, open_consults, open_appt_req = load()
    ids = [r[0] for r in rows_db]
    scoped = None
    if scoped_role:
        scoped = {cid for cid in ids if actor.org_id in orgs.get(cid, ())}
    counts = {v: 0 for v in VIEWS}
    rows = []
    for (cid, prog, tier, state, owner_id, due, esc, risk, risk_v, pending, review, given, family, mrn, lang,
         oname, opool, oactive, s_start, s_due, s_state) in rows_db:
        if scoped is not None and cid not in scoped:
            continue
        rag, frac = ("none", 0.0)
        if s_start is not None:
            rag, frac = rag_status(s_start, s_due, now, sla.reminder_fraction(s_state, tier))
        unassigned = bool(opool) or not oactive
        row = {
            "id": cid, "patient": f"{given} {family}", "mrn": mrn, "language": lang, "programme": prog, "tier": tier,
            "state": state, "state_label": LABELS.get(state, state), "owner_id": owner_id, "owner": oname,
            "owner_is_pool": bool(opool), "due_at": due.isoformat() if due else None, "sla_status": rag,
            "sla_fraction": round(frac, 2), "escalation_level": esc,
            "risk_score": None if risk is None else round(risk, 3), "risk_model_version": risk_v,
            "transfer_pending": pending is not None, "call_task": cid in open_calls,
            "open_escalation": cid in open_esc, "open_consult": cid in open_consults, "unassigned": unassigned,
            "review_required": bool(review), "patient_change_request": cid in open_appt_req,
        }
        member = {
            "all": True, "review": bool(review), "overdue": rag == "red", "unassigned": unassigned, "rejected": state == S.REFERRAL_REJECTED,
            "escalated": row["open_escalation"], "calls": row["call_task"], "transfers": row["transfer_pending"],
            "consults": row["open_consult"], "mine": actor.practitioner_id == owner_id,
        }
        for v, m in member.items():
            counts[v] += int(m)
        if member.get(view, False):
            rows.append(row)
    rows.sort(key=sort_key)
    page = rows[offset:offset + limit]
    # JSON columns are decoded only for the returned page (was: for every open case on every request)
    extra = {cid: (rr, why) for cid, rr, why in db.execute(
        select(Case.id, Case.review_reasons, Case.risk_rationale).where(Case.id.in_([r["id"] for r in page])))}
    for r in page:
        rr, why = extra.get(r["id"], (None, None))
        r["review_reasons"] = rr or []
        r["risk_rationale"] = "; ".join(f"{w['text']} ({w['direction']})" for w in (why or []))
    return {"view": view, "counts": counts, "total": len(rows), "offset": offset, "limit": limit,
            "items": page,
            "ordering": "SLA status, then urgency tier, then clinical-review flag, then ML outreach priority (within tier only), then due time"}
