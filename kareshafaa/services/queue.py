"""Coordinator work queue: overdue / unassigned / rejected / escalated / calls / transfers / consults."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import case as sa_case, or_, select
from sqlalchemy.orm import Session, aliased

from ..models import (Appointment, AppointmentRequest, CallTask, Case, ConsultRequest, Escalation, Patient, Practitioner, ServiceRequest,
                      StepTask, utcnow)
from ..security import PROGRAMME_WIDE_ROLES, Actor
from ..sla import SlaLookup, rag_status
from ..statemachine import LABELS, S

RAG_RANK = {"red": 0, "amber": 1, "green": 2, "none": 3}
TIER_RANK = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}
VIEWS = ("all", "review", "overdue", "unassigned", "rejected", "escalated", "calls", "transfers", "consults", "mine")


def _org_scope(db: Session, case_ids: list[int]) -> dict[int, set[int]]:
    """Organisations involved in each case, computed in 4 set-based queries (no per-case N+1)."""
    out: dict[int, set[int]] = defaultdict(set)
    if not case_ids:
        return out
    owner = aliased(Practitioner)
    pend = aliased(Practitioner)
    for cid, morg, oorg, porg in db.execute(
            select(Case.id, Case.managing_org_id, owner.org_id, pend.org_id)
            .join(owner, owner.id == Case.owner_id).outerjoin(pend, pend.id == Case.transfer_pending_owner_id)
            .where(Case.id.in_(case_ids))):
        out[cid].update(x for x in (morg, oorg, porg) if x is not None)
    for cid, org in db.execute(select(ServiceRequest.case_id, ServiceRequest.performer_org_id)
                               .where(ServiceRequest.case_id.in_(case_ids)).distinct()):
        out[cid].add(org)
    for cid, org in db.execute(select(Appointment.case_id, Appointment.org_id)
                               .where(Appointment.case_id.in_(case_ids)).distinct()):
        out[cid].add(org)
    for cid, org in db.execute(select(ConsultRequest.case_id, Practitioner.org_id)
                               .join(Practitioner, Practitioner.id == ConsultRequest.target_practitioner_id)
                               .where(ConsultRequest.case_id.in_(case_ids)).distinct()):
        out[cid].add(org)
    return out


class _BaseCache:
    """Round 21 read-path cache for the work queue's base data (open cases + flags), per process.

    Bounded staleness: at most QUEUE_CACHE_TTL_S (2 s) old, and dropped at once when THIS process commits any
    clinical/domain change (after_commit hook below; the per-request access log does not count). RAG status and
    sorting are still computed per request at the current time, the per-user org scope is applied per request, and
    every action re-checks the case's row_version (409 on a stale view), so a cached list can never cause a wrong
    write. Single-flight: concurrent misses compute once."""

    def __init__(self) -> None:
        import threading

        self._d: dict = {}
        self._gen = 0
        self._lock = threading.Lock()

    def bump(self) -> None:
        self._gen += 1

    def get(self, key, load):
        import time

        from ..config import get_settings
        ttl = get_settings().queue_cache_ttl_s
        if ttl <= 0:
            return load()
        hit = self._d.get(key)
        now = time.monotonic()
        if hit and hit[0] == self._gen and now - hit[1] < ttl:
            return hit[2]
        with self._lock:
            hit = self._d.get(key)
            if hit and hit[0] == self._gen and time.monotonic() - hit[1] < ttl:
                return hit[2]
            gen = self._gen
            val = load()
            if len(self._d) > 256:
                self._d.clear()
            self._d[key] = (gen, time.monotonic(), val)
            return val


_BASE_CACHE = _BaseCache()


def _mark_domain_write(session, _ctx, _instances) -> None:
    from ..models import AuditEvent
    if any(not isinstance(o, AuditEvent) for o in (*session.new, *session.dirty, *session.deleted)):
        session.info["domain_write"] = True


def _after_commit(session) -> None:
    if session.info.pop("domain_write", False):
        _BASE_CACHE.bump()


def _after_rollback(session) -> None:
    session.info.pop("domain_write", None)


from sqlalchemy import event as _event  # noqa: E402

_event.listen(Session, "before_flush", _mark_domain_write)
_event.listen(Session, "after_commit", _after_commit)
_event.listen(Session, "after_rollback", _after_rollback)


def sort_key(row: dict) -> tuple:
    # 1) SLA status (clinical safety first)  2) urgency tier  3) ML outreach priority  4) due time
    # The ML score only orders cases WITHIN the same SLA status and tier; it can never move a case across tiers.
    return (RAG_RANK[row["sla_status"]], TIER_RANK.get(row["tier"], 9), not row.get("review_required"),
            -(row["risk_score"] or 0.0), row["due_at"] or "9")


def work_queue(db: Session, actor: Actor, view: str = "all", now: datetime | None = None, limit: int = 200,
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

    rows_db, orgs, open_calls, open_esc, open_consults, open_appt_req = _BASE_CACHE.get((programme, search), load)
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
