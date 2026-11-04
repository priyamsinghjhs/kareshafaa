"""Coordinator work queue: overdue / unassigned / rejected / escalated / calls / transfers / consults."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import Numeric, case as sa_case, cast, exists, false, func, literal, or_, select, true
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


def _rag_rank(now: datetime):
    """RAG from the stored thresholds (kareshafaa/queue_store.py) at `now`: 0 red, 1 amber, 2 green, 3 none."""
    return sa_case((Case.sla_red_at.is_(None), 3), (Case.sla_red_at <= now, 0), (Case.sla_amber_at <= now, 1),
                   else_=2)


_RAG_LABEL = {0: "red", 1: "amber", 2: "green", 3: "none"}


def work_queue(db: Session, actor: Actor, view: str = "all", now: datetime | None = None, limit: int = 200,
               programme: str | None = None, offset: int = 0, search: str | None = None) -> dict:
    """Filtering, counts, ordering and paging in SQL (SQLite and PostgreSQL alike).

    RAG comes from the stored per-case thresholds (sla_amber_at / sla_red_at) compared with `now`, so only the page
    is transferred and decoded: 3 statements whatever the number of open cases. Same visible result as the former
    Python path (tests/queue_oracle.py, tests/test_queue_sql.py); exact ties are broken by case id."""
    now = now or utcnow()
    owner = aliased(Practitioner)
    pend = aliased(Practitioner)
    esc = exists().where(Escalation.case_id == Case.id, Escalation.acknowledged_at.is_(None))
    calls = exists().where(CallTask.case_id == Case.id, CallTask.status == "requested")
    consults = exists().where(ConsultRequest.case_id == Case.id, ConsultRequest.status == "requested")
    appt_req = exists().where(AppointmentRequest.case_id == Case.id, AppointmentRequest.kind == "change",
                              AppointmentRequest.status == "open")
    unassigned = or_(owner.is_pool.is_(True), owner.active.is_not(True))
    rag = _rag_rank(now)
    conds = {
        "all": true(), "review": Case.review_required.is_(True), "overdue": rag == 0, "unassigned": unassigned,
        "rejected": Case.state == S.REFERRAL_REJECTED, "escalated": esc, "calls": calls,
        "transfers": Case.transfer_pending_owner_id.is_not(None), "consults": consults,
        "mine": Case.owner_id == actor.practitioner_id if actor.practitioner_id is not None else false(),
    }
    where = [Case.state != S.CLOSED]
    if programme:
        where.append(Case.programme == programme)
    if search:
        like = f"%{search.strip()}%"
        cond = or_(Patient.mrn.ilike(like), Patient.given_name.ilike(like), Patient.family_name.ilike(like),
                   (Patient.given_name + " " + Patient.family_name).ilike(like))
        if search.strip().lstrip("#").isdigit():
            cond = or_(cond, Case.id == int(search.strip().lstrip("#")))
        where.append(cond)
    if actor.role not in PROGRAMME_WIDE_ROLES:  # organisations involved in the case (same set as _org_scope)
        org = actor.org_id
        if org is None:
            where.append(false())
        else:
            where.append(or_(
                Case.managing_org_id == org, owner.org_id == org, pend.org_id == org,
                exists().where(ServiceRequest.case_id == Case.id, ServiceRequest.performer_org_id == org),
                exists().where(Appointment.case_id == Case.id, Appointment.org_id == org),
                exists().where(ConsultRequest.case_id == Case.id,
                               ConsultRequest.target_practitioner_id == Practitioner.id, Practitioner.org_id == org)))

    def base(*cols):
        return (select(*cols).select_from(Case).join(Patient, Patient.id == Case.patient_id)
                .join(owner, owner.id == Case.owner_id).outerjoin(pend, pend.id == Case.transfer_pending_owner_id)
                .where(*where))

    one = literal(1)
    count_row = db.execute(base(*[func.coalesce(func.sum(sa_case((c, one), else_=0)), 0).label(v)
                                  for v, c in conds.items()])).one()
    counts = {v: int(count_row._mapping[v]) for v in VIEWS}
    total = counts[view] if view in conds else 0
    items: list[dict] = []
    if total and view in conds:
        tier_rank = sa_case(TIER_RANK, value=Case.tier, else_=9)
        review_key = sa_case((Case.review_required.is_(True), 0), else_=1)
        risk_key = func.round(cast(func.coalesce(Case.risk_score, 0.0), Numeric), 3)
        due_null = sa_case((Case.due_at.is_(None), 1), else_=0)
        q = (base(Case.id, Case.programme, Case.tier, Case.state, Case.owner_id, Case.due_at,
                  sa_case((Case.target_alert_level > Case.escalation_level, Case.target_alert_level),
                          else_=Case.escalation_level),
                  Case.risk_score, Case.risk_model_version, Case.transfer_pending_owner_id, Case.review_required,
                  Patient.given_name, Patient.family_name, Patient.mrn, Patient.language,
                  owner.name, owner.is_pool, owner.active, StepTask.started_at, StepTask.due_at, StepTask.state,
                  rag, calls, esc, consults, appt_req, Case.review_reasons, Case.risk_rationale)
             .outerjoin(StepTask, (StepTask.case_id == Case.id) & (StepTask.status == "in-progress"))
             .where(conds[view])
             .order_by(rag, tier_rank, review_key, risk_key.desc(), due_null, Case.due_at, Case.id)
             .offset(offset).limit(limit))
        sla = SlaLookup.load(db)
        for (cid, prog, tier, state, owner_id, due, lvl, risk, risk_v, pending, review, given, family, mrn, lang,
             oname, opool, oactive, s_start, s_due, s_state, rank, has_call, has_esc, has_consult, has_appt_req,
             rr, why) in db.execute(q):
            frac = 0.0
            if s_start is not None:
                frac = rag_status(s_start, s_due, now, sla.reminder_fraction(s_state, tier))[1]
            items.append({
                "id": cid, "patient": f"{given} {family}", "mrn": mrn, "language": lang, "programme": prog,
                "tier": tier, "state": state, "state_label": LABELS.get(state, state), "owner_id": owner_id,
                "owner": oname, "owner_is_pool": bool(opool), "due_at": due.isoformat() if due else None,
                "sla_status": _RAG_LABEL[rank], "sla_fraction": round(frac, 2), "escalation_level": lvl,
                "risk_score": None if risk is None else round(risk, 3), "risk_model_version": risk_v,
                "transfer_pending": pending is not None, "call_task": bool(has_call),
                "open_escalation": bool(has_esc), "open_consult": bool(has_consult),
                "unassigned": bool(opool) or not oactive, "review_required": bool(review),
                "patient_change_request": bool(has_appt_req), "review_reasons": rr or [],
                "risk_rationale": "; ".join(f"{w['text']} ({w['direction']})" for w in (why or [])),
            })
    return {"view": view, "counts": counts, "total": total, "offset": offset, "limit": limit, "items": items,
            "ordering": "SLA status, then urgency tier, then clinical-review flag, then ML outreach priority (within tier only), then due time"}
