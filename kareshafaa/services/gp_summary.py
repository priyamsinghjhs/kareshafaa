"""Care summary to the registered GP: written automatically when care starts (treatment / surveillance plan) and
when a case closes; shown on the case timeline and in the GP's "Care summaries" inbox; the GP acknowledges it.

Recipient: the patient's registered GP (patients.gp_id); if none, the case owner when that is a GP. When the
recipient is the person who closed the case / started care, the summary is filed as already read (no self-ack).
Not a patient message: internal clinical correspondence, never SIMULATED-prefixed, never sent outside the system."""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..calendar_uae import to_local
from ..clinical_rules import GP_SUMMARY_ACK_DAYS
from ..models import (Appointment, CarePlan, Case, DiagnosticReport, GpSummary, Organization, Patient, Practitioner,
                      Recall, ServiceRequest, utcnow)
from ..security import Actor
from ..statemachine import AUTHORISED_CLOSURE_REASONS
from .errors import Forbidden, Invalid, NotFound

KIND_TITLE = {"care_started": "Care started", "case_closed": "Case closed"}
EVIDENCE_TEXT = {"appointment_fulfilled": "Diagnostic appointment attended", "care_plan": "Treatment / surveillance plan in place",
                 "negative_confirmatory_result": "Confirmatory test negative"}
PROG = {"colorectal_fit": "Bowel screening (FIT)", "cervical": "Cervical screening (HPV)", "breast": "Breast screening (mammogram)",
        "diabetes_hba1c": "Diabetes screening (HbA1c)"}


def _d(x) -> str | None:
    """Date for the GP letter on the UAE calendar: stored datetimes are naive UTC (20:00-24:00 UTC is the next UAE day);
    a plain date is already a calendar date."""
    if not x:
        return None
    return (to_local(x) if isinstance(x, datetime) else x).strftime("%d %b %Y")


def recipient(db: Session, case: Case) -> Practitioner | None:
    p = db.get(Patient, case.patient_id)
    gp = db.get(Practitioner, p.gp_id) if p and p.gp_id else None
    if gp is not None and gp.role == "gp" and gp.active and not gp.is_pool:
        return gp
    own = db.get(Practitioner, case.owner_id)
    return own if own is not None and own.role == "gp" and own.active and not own.is_pool else None


def build(db: Session, case: Case, kind: str, now: datetime) -> tuple[str, dict, str]:
    p = db.get(Patient, case.patient_id)
    rep = db.get(DiagnosticReport, case.report_id)
    obs = [f"{o.display}: {o.value if o.value is not None else (o.value_text or '')} {o.unit or ''}".strip()
           for o in (rep.observations if rep else [])]
    result = {"test": rep.display if rep else None, "date": _d(rep.issued_at) if rep else None, "values": obs,
              "urgency": case.tier}
    pathway = []
    for s in db.scalars(select(ServiceRequest).where(ServiceRequest.case_id == case.id).order_by(ServiceRequest.id)):
        org = db.get(Organization, s.performer_org_id)
        line = f"{s.kind.replace('_', ' ').capitalize()} to {org.name if org else 'another service'} ({_d(s.created_at)}): {s.status}"
        if s.result:
            line += f", result {s.result}"
        pathway.append(line)
    for a in db.scalars(select(Appointment).where(Appointment.case_id == case.id).order_by(Appointment.start)):
        org = db.get(Organization, a.org_id)
        pathway.append(f"Appointment {_d(a.start)} at {org.name if org else '-'}: "
                       + {"fulfilled": "attended", "noshow": "did not attend", "booked": "booked"}.get(a.status, a.status)
                       + (f" (barrier: {a.barrier_code.replace('_', ' ')})" if a.barrier_code else ""))
    plans = [{"kind": cp.kind, "description": cp.description, "review_due_on": _d(cp.review_due_on), "date": _d(cp.created_at)}
             for cp in db.scalars(select(CarePlan).where(CarePlan.case_id == case.id).order_by(CarePlan.id))]
    outcome = None
    if kind == "case_closed":
        why = (AUTHORISED_CLOSURE_REASONS.get(case.closure_reason or "") if case.closure_type == "authorised_decision"
               else EVIDENCE_TEXT.get(case.closure_evidence_type or "", case.closure_evidence_type))
        outcome = {"closed_on": _d(case.closed_at or now), "how": "Authorised decision" if case.closure_type == "authorised_decision" else "With evidence",
                   "reason": why, "note": case.closure_note}
    nxt = []
    r = db.scalar(select(Recall).where(Recall.source_case_id == case.id, Recall.status.in_(("due", "invited"))).order_by(Recall.id.desc()))
    if r is not None:
        nxt.append(f"Next {'surveillance review' if r.reason == 'surveillance' else 'routine screen'} due {_d(r.due_on)} "
                   "(recall set; the coordinator invites the patient)")
    elif kind == "case_closed":
        nxt.append("No automatic recall: follow-up is with the treating team, or the patient left the programme")
    for cp in plans:
        if cp["kind"] == "treatment":
            nxt.append("Treatment follow-up as set by the treating team; please continue routine care")
    if kind == "care_started":
        nxt.append("The case stays open until the outcome is confirmed; you will receive the closure summary")
    title = f"{KIND_TITLE[kind]}: {PROG.get(case.programme, case.programme)} - {p.given_name} {p.family_name} ({p.mrn})"
    content = {"patient": {"name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "birth_year": p.birth_year,
                           "birth_date": p.birth_date.isoformat() if p.birth_date else None},
               "programme": PROG.get(case.programme, case.programme), "case_id": case.id, "result": result,
               "pathway": pathway, "care_plans": plans, "outcome": outcome, "next_steps": nxt}
    lines = [title, "", f"Screening result: {result['test'] or '-'} on {result['date'] or '-'}"
             + (f" - {'; '.join(obs)}" if obs else "") + f" (urgency {case.tier})"]
    if pathway:
        lines += ["", "Pathway:"] + [f"- {x}" for x in pathway]
    if plans:
        lines += ["", "Care plan:"] + [f"- {c['kind'].capitalize()} ({c['date']}): {c['description']}"
                                       + (f" Review due {c['review_due_on']}." if c["review_due_on"] else "") for c in plans]
    if outcome:
        lines += ["", f"Closed {outcome['closed_on']}: {outcome['how'].lower()} - {outcome['reason'] or '-'}"
                  + (f". Note: {outcome['note']}" if outcome["note"] else "")]
    lines += ["", "Next steps:"] + [f"- {x}" for x in nxt]
    return title[:200], content, "\n".join(lines)


def write(db: Session, case: Case, kind: str, actor: Actor, now: datetime | None = None) -> GpSummary | None:
    now = now or utcnow()
    to = recipient(db, case)
    if to is None:
        audit.record(db, actor, "E", "gp_summary.no_recipient", entity_type="Task", entity_id=case.id, case_id=case.id,
                     ts=now, detail={"kind": kind, "why": "patient has no registered GP and the case owner is not a GP"})
        return None
    title, content, text = build(db, case, kind, now)
    s = GpSummary(case_id=case.id, patient_id=case.patient_id, kind=kind, to_practitioner_id=to.id, to_org_id=to.org_id,
                  title=title, content=content, text=text, created_at=now, created_by=actor.display)
    if actor.is_human and actor.practitioner_id == to.id:  # the GP wrote it themselves: nothing to acknowledge
        s.acknowledged_at, s.acknowledged_by, s.acknowledged_by_user_id = now, actor.display, actor.user_id
        s.ack_note = "Filed as read: you recorded this step yourself"
    db.add(s)
    db.flush()
    audit.record(db, actor, "C", "gp_summary.created", entity_type="GpSummary", entity_id=s.id, case_id=case.id, ts=now,
                 detail={"kind": kind, "to_practitioner_id": to.id, "auto_filed": s.acknowledged_at is not None})
    return s


def _may_read(db: Session, actor: Actor, s: GpSummary) -> bool:
    if actor.role == "gp":
        if actor.practitioner_id == s.to_practitioner_id or (actor.org_id is not None and actor.org_id == s.to_org_id):
            return True
    from .access import can_view_case

    c = db.get(Case, s.case_id)
    return c is not None and can_view_case(db, actor, c)


def get(db: Session, actor: Actor, sid: int) -> GpSummary:
    s = db.get(GpSummary, sid)
    if s is None or not actor.can("case:read") or not _may_read(db, actor, s):
        raise NotFound("summary not found")
    return s


def acknowledge(db: Session, actor: Actor, s: GpSummary, note: str | None, now: datetime | None = None) -> GpSummary:
    now = now or utcnow()
    if not (actor.is_human and actor.can("summary:ack")):
        raise Forbidden("only the patient's GP acknowledges a care summary")
    if not (actor.practitioner_id == s.to_practitioner_id or (actor.org_id is not None and actor.org_id == s.to_org_id)):
        raise Forbidden("this summary is addressed to another GP practice")
    if s.acknowledged_at is not None:
        raise Invalid("this summary is already acknowledged")
    note = (note or "").strip() or None
    if note and len(note) > 1000:
        raise Invalid("note is too long (1000 characters at most)")
    s.acknowledged_at, s.acknowledged_by, s.acknowledged_by_user_id, s.ack_note = now, actor.display, actor.user_id, note
    audit.record(db, actor, "U", "gp_summary.acknowledged", entity_type="GpSummary", entity_id=s.id, case_id=s.case_id,
                 ts=now, detail={"note": note, "days_after": round((now - s.created_at).total_seconds() / 86400, 1)})
    return s


def row(db: Session, s: GpSummary, now: datetime, full: bool = False) -> dict:
    to = db.get(Practitioner, s.to_practitioner_id)
    due = s.created_at + timedelta(days=GP_SUMMARY_ACK_DAYS)
    d = {"id": s.id, "case_id": s.case_id, "patient_id": s.patient_id, "kind": s.kind, "title": s.title,
         "to": to.name if to else None, "to_practitioner_id": s.to_practitioner_id,
         "created_at": s.created_at.isoformat(), "created_by": s.created_by,
         "acknowledged_at": s.acknowledged_at.isoformat() if s.acknowledged_at else None,
         "acknowledged_by": s.acknowledged_by, "ack_note": s.ack_note, "ack_due_at": due.isoformat(),
         "overdue": s.acknowledged_at is None and now > due}
    if full:
        d.update({"content": s.content, "text": s.text})
    return d


def for_case(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> list[dict]:
    now = now or utcnow()
    out = []
    for s in db.scalars(select(GpSummary).where(GpSummary.case_id == case.id).order_by(GpSummary.id)):
        r = row(db, s, now, full=True)
        r["can_ack"] = (s.acknowledged_at is None and actor.is_human and actor.can("summary:ack")
                        and (actor.practitioner_id == s.to_practitioner_id or (actor.org_id is not None and actor.org_id == s.to_org_id)))
        out.append(r)
    return out


def inbox(db: Session, actor: Actor, view: str = "unread", now: datetime | None = None, limit: int = 100) -> dict:
    now = now or utcnow()
    if actor.role != "gp" or not actor.can("summary:ack"):
        raise Forbidden("the care summary inbox is for GPs")
    mine = or_(GpSummary.to_practitioner_id == actor.practitioner_id,
               GpSummary.to_org_id == actor.org_id) if actor.org_id is not None else GpSummary.to_practitioner_id == actor.practitioner_id
    q = select(GpSummary).where(mine)
    unread_q = q.where(GpSummary.acknowledged_at.is_(None))
    unread = len(db.scalars(unread_q.with_only_columns(GpSummary.id)).all())
    q = unread_q if view == "unread" else q
    rows = db.scalars(q.order_by(GpSummary.created_at.desc(), GpSummary.id.desc()).limit(limit)).all()
    items = []
    for s in rows:
        r = row(db, s, now, full=True)
        r["addressed_to_me"] = s.to_practitioner_id == actor.practitioner_id
        r["can_ack"] = s.acknowledged_at is None
        items.append(r)
    return {"items": items, "unread": unread, "ack_days": GP_SUMMARY_ACK_DAYS}
