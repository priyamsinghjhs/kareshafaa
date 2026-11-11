"""Coordinator work queue: overdue / unassigned / rejected / escalated / calls / transfers / consults."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from sqlalchemy import Numeric, case as sa_case, cast, false, func, literal, or_, select, true
from sqlalchemy.orm import Session, aliased

from ..models import (Appointment, AppointmentRequest, CallTask, Case, ConsultRequest, Escalation, Patient, Practitioner, ServiceRequest,
                      StepTask, utcnow)
from ..security import PROGRAMME_WIDE_ROLES, Actor
from .access import sharing_denied_select
from ..sla import SlaLookup, rag_reminder_fraction, rag_status
from ..statemachine import LABELS, S

# GP and specialist work queues are action lists: hide cases whose next step has nothing for
# this role (same rule as the case page "Nothing for your role now"). Coordinator and clinical
# lead keep a programme-wide overview of every in-scope open case, including ones waiting on
# another role.
ACTION_QUEUE_ROLES = frozenset({"gp", "specialist"})

RAG_RANK = {"red": 0, "amber": 1, "green": 2, "none": 3}
TIER_RANK = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}
VIEWS = ("all", "review", "overdue", "unassigned", "rejected", "escalated", "calls", "transfers", "consults", "mine",
         "critical")  # critical = critical-result fast lane: open T0 cases with no direct read-back recorded yet


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



def case_number_query(search: str | None) -> int | None:
    """Exact case-number lookup (digits, optional leading #), or None."""
    if not search:
        return None
    s = search.strip().lstrip("#")
    return int(s) if s.isdigit() else None


def action_queue_case_ids(db: Session, actor: Actor, case_ids: list[int], search: str | None = None) -> set[int]:
    """Ids a GP/specialist should see in the work queue. Oversight roles keep every given id.

    Hidden cases still appear when the search is that case's number (and the case is already in
    the given, already-scoped set). Name or MRN search does not reveal them.
    """
    if actor.role not in ACTION_QUEUE_ROLES:
        return set(case_ids)
    from ..api.actions import primary_action_case_ids
    keep = primary_action_case_ids(db, actor, case_ids)
    reveal = case_number_query(search)
    if reveal is not None and reveal in set(case_ids):
        keep.add(reveal)
    return keep


def work_queue(db: Session, actor: Actor, view: str = "all", now: datetime | None = None, limit: int = 200,
               programme: str | None = None, offset: int = 0, search: str | None = None) -> dict:
    """Filtering, counts, ordering and paging in SQL (SQLite and PostgreSQL alike).

    RAG comes from the stored per-case thresholds (sla_amber_at / sla_red_at) compared with `now`, so only the page
    is transferred and decoded: 4 statements whatever the number of open cases. For GP and specialist queues, cases
    with no primary action for that role are dropped first (same rule as the case page), and counts follow the
    filtered list; a case-number search still returns a hidden case. Same visible result as the former Python path
    (tests/queue_oracle.py, tests/test_queue_sql.py); exact ties are broken by case id."""
    now = now or utcnow()
    owner = aliased(Practitioner)
    pend = aliased(Practitioner)
    # uncorrelated IN (sub-select evaluated once) rather than a correlated EXISTS per open case
    esc = Case.id.in_(select(Escalation.case_id).where(Escalation.acknowledged_at.is_(None)))
    calls = Case.id.in_(select(CallTask.case_id).where(CallTask.status == "requested"))
    consults = Case.id.in_(select(ConsultRequest.case_id).where(ConsultRequest.status == "requested"))
    appt_req = Case.id.in_(select(AppointmentRequest.case_id).where(AppointmentRequest.kind == "change",
                                                                    AppointmentRequest.status == "open"))
    unassigned = or_(owner.is_pool.is_(True), owner.active.is_not(True))
    from .critical import missing_select
    read_back_needed = (Case.tier == "critical") & Case.id.not_in(missing_select())
    rag = _rag_rank(now)
    conds = {
        "all": true(), "review": Case.review_required.is_(True), "overdue": rag == 0, "unassigned": unassigned,
        "rejected": Case.state == S.REFERRAL_REJECTED, "escalated": esc, "calls": calls,
        "transfers": Case.transfer_pending_owner_id.is_not(None), "consults": consults,
        "mine": Case.owner_id == actor.practitioner_id if actor.practitioner_id is not None else false(),
        "critical": read_back_needed,
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
                Case.id.in_(select(ServiceRequest.case_id).where(ServiceRequest.performer_org_id == org)),
                Case.id.in_(select(Appointment.case_id).where(Appointment.org_id == org)),
                Case.id.in_(select(ConsultRequest.case_id).join(Practitioner,
                                                                Practitioner.id == ConsultRequest.target_practitioner_id)
                            .where(Practitioner.org_id == org))))
            # other organisations only while the patient allows sharing (same rule as opening the case)
            where.append(or_(Patient.managing_org_id == org, Patient.id.not_in(sharing_denied_select())))

    def base(*cols):
        return (select(*cols).select_from(Case).join(Patient, Patient.id == Case.patient_id)
                .join(owner, owner.id == Case.owner_id).outerjoin(pend, pend.id == Case.transfer_pending_owner_id)
                .where(*where))

    if actor.role in ACTION_QUEUE_ROLES:
        candidates = list(db.scalars(base(Case.id)))
        keep = action_queue_case_ids(db, actor, candidates, search)
        where.append(Case.id.in_(keep) if keep else false())

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
        # 1) the page's ids: sort keys only (the sort runs over every matching open case, so keep its rows narrow)
        page_ids = list(db.scalars(base(Case.id).where(conds[view])
                                   .order_by(rag, tier_rank, review_key, risk_key.desc(), due_null, Case.due_at, Case.id)
                                   .offset(offset).limit(limit)))
        # 2) everything shown for those rows only
        q = (select(Case.id, Case.programme, Case.tier, Case.state, Case.owner_id, Case.due_at,
                    sa_case((Case.target_alert_level > Case.escalation_level, Case.target_alert_level),
                            else_=Case.escalation_level),
                    Case.risk_score, Case.risk_model_version, Case.transfer_pending_owner_id, Case.review_required,
                    Patient.given_name, Patient.family_name, Patient.mrn, Patient.language,
                    owner.name, owner.is_pool, owner.active, StepTask.started_at, StepTask.due_at, StepTask.state,
                    rag, calls, esc, consults, appt_req, Case.review_reasons, Case.risk_rationale, read_back_needed)
             .select_from(Case).join(Patient, Patient.id == Case.patient_id).join(owner, owner.id == Case.owner_id)
             .outerjoin(StepTask, (StepTask.case_id == Case.id) & (StepTask.status == "in-progress"))
             .where(Case.id.in_(page_ids)))
        pos = {cid: i for i, cid in enumerate(page_ids)}
        sla = SlaLookup.load(db)
        for (cid, prog, tier, state, owner_id, due, lvl, risk, risk_v, pending, review, given, family, mrn, lang,
             oname, opool, oactive, s_start, s_due, s_state, rank, has_call, has_esc, has_consult, has_appt_req,
             rr, why, rb_needed) in sorted(db.execute(q).all(), key=lambda r: pos[r[0]]) if page_ids else ():
            frac = 0.0
            if s_start is not None:
                frac = rag_status(s_start, s_due, now, rag_reminder_fraction(s_state, sla.reminder_fraction(s_state, tier)))[1]
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
                "read_back_needed": bool(rb_needed),
                "risk_rationale": "; ".join(f"{w['text']} ({w['direction']})" for w in (why or [])),
            })
    return {"view": view, "counts": counts, "total": total, "offset": offset, "limit": limit, "items": items,
            "ordering": "SLA status, then urgency tier, then clinical-review flag, then ML outreach priority (within tier only), then due time"}
