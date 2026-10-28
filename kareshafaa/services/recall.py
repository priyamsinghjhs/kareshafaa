"""Recall: the next routine screen (or surveillance review) after a negative result, a closed case or a
surveillance care plan. Intervals: kareshafaa/clinical_rules.py (all CONFIRM).

* One open recall per person and programme: a newer one supersedes the older (kept, status superseded).
* The next screening result for that person and programme completes the open recall - the new result starts the
  new screening episode (a case if it is actionable, a fresh routine recall if it is negative).
* Coordinators see who is due (/recalls), send a SIMULATED invitation (patient-level message, the person's
  language, consent respected) or cancel a recall with a reason. Every step is audited.
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timedelta

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..clinical_rules import (ELIGIBLE_AGE, ELIGIBLE_SEX, RECALL_AFTER_CLOSURE_MONTHS, RECALL_INTERVAL_MONTHS,
                              RECALL_LIST_AHEAD_DAYS, RECALL_UPTAKE_WINDOW_DAYS, SURVEILLANCE_MONTHS)
from ..models import CarePlan, Case, Communication, DiagnosticReport, Organization, Patient, Recall, utcnow
from ..security import SYSTEM, Actor
from .access import consent_status, has_consent
from .errors import DomainError, Forbidden, Invalid, NotFound

OPEN = ("due", "invited")
REASON_TEXT = {"routine_after_negative": "Routine re-screen after a negative result", "after_closure": "Next routine screen after the case closed",
               "surveillance": "Surveillance review (care plan)"}

# SIMULATED patient message (one language per message; no case - the person is not in a pathway)
RECALL_TEXT = {
    "en": "Hello {name}, you are due for your {programme} from {due}. Please book with your health centre ({centre}) or reply to this message and we will help. This is a routine invitation.",
    "ar": "مرحباً {name}، حان موعد {programme} اعتباراً من {due}. يرجى الحجز لدى مركزك الصحي ({centre}) أو الرد على هذه الرسالة وسنساعدك. هذه دعوة روتينية.",
    "hi": "नमस्ते {name}, आपकी {programme} {due} से होनी है। कृपया अपने स्वास्थ्य केंद्र ({centre}) में समय लें या इस संदेश का जवाब दें, हम मदद करेंगे। यह एक नियमित निमंत्रण है।",
    "ur": "السلام علیکم {name}، آپ کی {programme} {due} سے ہونی ہے۔ براہ کرم اپنے مرکز صحت ({centre}) میں وقت لیں یا اس پیغام کا جواب دیں، ہم مدد کریں گے۔ یہ ایک معمول کی دعوت ہے۔",
}
CENTRE_FALLBACK = {"en": "your health centre", "ar": "مركزك الصحي", "hi": "आपका स्वास्थ्य केंद्र", "ur": "آپ کا مرکز صحت"}


def add_months(d: date, months: int) -> date:
    y, m = divmod(d.month - 1 + months, 12)
    y, m = d.year + y, m + 1
    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _age_at(p: Patient, on: date) -> int:
    if p.birth_date:
        b = p.birth_date
        return on.year - b.year - ((on.month, on.day) < (b.month, b.day))
    return on.year - (p.birth_year or on.year)


def eligible_on(p: Patient, programme: str, on: date) -> bool:
    lo, hi = ELIGIBLE_AGE.get(programme, (0, 200))
    sexes = ELIGIBLE_SEX.get(programme)
    return (not sexes or p.gender in sexes) and _age_at(p, on) <= hi


def open_recall(db: Session, patient_id: int, programme: str) -> Recall | None:
    return db.scalar(select(Recall).where(Recall.patient_id == patient_id, Recall.programme == programme,
                                          Recall.status.in_(OPEN)).order_by(Recall.id.desc()).limit(1))


def schedule(db: Session, p: Patient, programme: str, due_on: date, reason: str, actor: Actor = SYSTEM, *,
             now: datetime | None = None, interval_months: int | None = None, source_report_id: int | None = None,
             source_case_id: int | None = None, care_plan_id: int | None = None) -> Recall | None:
    now = now or utcnow()
    if p.deceased:
        return None
    if reason != "surveillance" and not eligible_on(p, programme, due_on):
        audit.record(db, actor, "E", "recall.not_scheduled", entity_type="Patient", entity_id=p.id, ts=now,
                     case_id=source_case_id, detail={"programme": programme, "due_on": due_on.isoformat(),
                                                     "why": "outside the programme's age range on the due date"})
        return None
    for old in db.scalars(select(Recall).where(Recall.patient_id == p.id, Recall.programme == programme,
                                               Recall.status.in_(OPEN))):
        old.status, old.closed_note, old.closed_by = "superseded", "replaced by a newer recall", actor.display
    r = Recall(patient_id=p.id, programme=programme, due_on=due_on, reason=reason, interval_months=interval_months,
               source_report_id=source_report_id, source_case_id=source_case_id, care_plan_id=care_plan_id,
               status="due", created_at=now, created_by=actor.display, invite_count=0)
    db.add(r)
    db.flush()
    audit.record(db, actor, "C", "recall.scheduled", entity_type="Recall", entity_id=r.id, case_id=source_case_id, ts=now,
                 detail={"patient_id": p.id, "programme": programme, "due_on": due_on.isoformat(), "reason": reason,
                         "interval_months": interval_months})
    return r


def after_negative(db: Session, p: Patient, rep: DiagnosticReport, actor: Actor = SYSTEM,
                   now: datetime | None = None) -> Recall | None:
    months = RECALL_INTERVAL_MONTHS.get(rep.programme)
    if not months:
        return None
    return schedule(db, p, rep.programme, add_months((rep.issued_at or now or utcnow()).date(), months),
                    "routine_after_negative", actor, now=now, interval_months=months, source_report_id=rep.id)


def closure_key(db: Session, case: Case) -> tuple[str, CarePlan | None]:
    if case.closure_type == "evidence":
        if case.closure_evidence_type == "care_plan":
            cp = db.get(CarePlan, case.closure_evidence_id) if case.closure_evidence_id else None
            cp = cp or db.scalar(select(CarePlan).where(CarePlan.case_id == case.id).order_by(CarePlan.id.desc()).limit(1))
            return f"care_plan:{cp.kind if cp else 'treatment'}", cp
        if case.closure_evidence_type == "appointment_fulfilled":
            cp = db.scalar(select(CarePlan).where(CarePlan.case_id == case.id).order_by(CarePlan.id.desc()).limit(1))
            if cp is not None:
                return f"care_plan:{cp.kind}", cp
        return case.closure_evidence_type or "", None
    return case.closure_reason or "", None


def after_closure(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> Recall | None:
    """Set the case's next screening due date from how it was closed, and schedule the recall."""
    now = now or utcnow()
    key, cp = closure_key(db, case)
    rule = RECALL_AFTER_CLOSURE_MONTHS.get(key)
    p = case.patient
    due, months, reason = None, None, "after_closure"
    if rule == "routine":
        months = RECALL_INTERVAL_MONTHS.get(case.programme)
        due = add_months((case.closed_at or now).date(), months) if months else None
    elif rule == "plan" and cp is not None:
        due = cp.review_due_on or add_months(cp.created_at.date(), SURVEILLANCE_MONTHS.get(case.programme, 12))
        reason = "surveillance"
    case.next_screening_due = due
    if due is None:
        audit.record(db, actor, "E", "recall.not_scheduled", entity_type="Task", entity_id=case.id, case_id=case.id,
                     ts=now, detail={"why": f"no automatic recall after closure '{key}'"})
        return None
    r = schedule(db, p, case.programme, due, reason, actor, now=now, interval_months=months, source_case_id=case.id,
                 care_plan_id=cp.id if cp is not None and reason == "surveillance" else None)
    if r is None:
        case.next_screening_due = None
    return r


def on_new_result(db: Session, p: Patient, rep: DiagnosticReport, now: datetime | None = None) -> Recall | None:
    """A new screening result for this person and programme completes their open recall (new episode)."""
    now = now or utcnow()
    from .workflow import identity_group

    ids = identity_group(db, p) or [p.id]
    r = db.scalar(select(Recall).where(Recall.patient_id.in_(ids), Recall.programme == rep.programme,
                                       Recall.status.in_(OPEN)).order_by(Recall.id.desc()).limit(1))
    if r is None:
        return None
    r.status, r.completed_at, r.completed_report_id = "completed", now, rep.id
    audit.record(db, SYSTEM, "U", "recall.completed", entity_type="Recall", entity_id=r.id, ts=now,
                 detail={"report_id": rep.id, "due_on": r.due_on.isoformat(), "days_from_due": (now.date() - r.due_on).days})
    return r


def link_case(db: Session, rep: DiagnosticReport, case: Case | None) -> None:
    if case is None:
        return
    r = db.scalar(select(Recall).where(Recall.completed_report_id == rep.id))
    if r is not None and r.completed_case_id is None:
        r.completed_case_id = case.id


# ------------------------------------------------------------------------------------------ coordinator list
def _can_manage(actor: Actor) -> bool:
    return actor.is_human and actor.can("recall:manage")


def row(db: Session, r: Recall, p: Patient, today: date) -> dict:
    from ..programmes import PROGRAMMES

    org = db.get(Organization, p.managing_org_id)
    return {"id": r.id, "patient_id": p.id, "patient": f"{p.given_name} {p.family_name}", "mrn": p.mrn,
            "programme": r.programme, "programme_name": PROGRAMMES[r.programme].name, "due_on": r.due_on.isoformat(),
            "days_overdue": (today - r.due_on).days, "reason": r.reason, "reason_text": REASON_TEXT.get(r.reason, r.reason),
            "status": r.status, "invite_count": r.invite_count, "invited_at": r.invited_at.isoformat() if r.invited_at else None,
            "invited_by": r.invited_by, "last_invite_channel": r.last_invite_channel, "language": p.language,
            "health_centre": org.name if org else None, "phone": bool(p.phone_valid), "has_smartphone": bool(p.has_smartphone),
            "source_case_id": r.source_case_id, "completed_at": r.completed_at.isoformat() if r.completed_at else None,
            "completed_case_id": r.completed_case_id, "closed_note": r.closed_note, "closed_by": r.closed_by,
            "sample": (p.mrn or "").startswith("SYN-")}


def list_recalls(db: Session, actor: Actor, *, view: str = "due", programme: str | None = None,
                 q: str | None = None, limit: int = 200, now: datetime | None = None) -> dict:
    from .patients import can_view_patient

    if not (actor.can("recall:manage") or actor.can("recall:read")):
        raise Forbidden("role cannot see recalls")
    now = now or utcnow()
    today = now.date()
    qy = select(Recall, Patient).join(Patient, Patient.id == Recall.patient_id).where(Patient.deceased.is_(False))
    if view == "due":
        qy = qy.where(Recall.status.in_(OPEN), Recall.due_on <= today + timedelta(days=RECALL_LIST_AHEAD_DAYS))
    elif view == "overdue":
        qy = qy.where(Recall.status.in_(OPEN), Recall.due_on < today)
    elif view == "invited":
        qy = qy.where(Recall.status == "invited")
    elif view == "upcoming":
        qy = qy.where(Recall.status.in_(OPEN), Recall.due_on > today + timedelta(days=RECALL_LIST_AHEAD_DAYS))
    elif view == "completed":
        qy = qy.where(Recall.status == "completed")
    else:
        raise Invalid("unknown view")
    if programme:
        qy = qy.where(Recall.programme == programme)
    if q and len(q.strip()) >= 2:
        like = f"%{q.strip()}%"
        qy = qy.where(or_(Patient.mrn.ilike(like), Patient.given_name.ilike(like), Patient.family_name.ilike(like)))
    order = Recall.completed_at.desc() if view == "completed" else Recall.due_on.asc()
    items, total = [], 0
    for r, p in db.execute(qy.order_by(order, Recall.id).limit(2000)):
        if not can_view_patient(db, actor, p):
            continue
        total += 1
        if len(items) < limit:
            items.append(row(db, r, p, today))
    return {"items": items, "count": total, "shown": len(items), "ahead_days": RECALL_LIST_AHEAD_DAYS,
            "can_manage": _can_manage(actor)}


def get_recall(db: Session, actor: Actor, recall_id: int) -> tuple[Recall, Patient]:
    from .patients import can_view_patient

    r = db.get(Recall, recall_id)
    if r is None:
        raise NotFound("recall not found")
    p = db.get(Patient, r.patient_id)
    if not can_view_patient(db, actor, p):
        raise NotFound("recall not found")
    return r, p


def _channel(db: Session, p: Patient) -> str | None:
    order = [p.preferred_channel] if p.preferred_channel in ("app", "sms", "whatsapp") else []
    order += ["sms", "whatsapp", "app"]
    for ch in order:
        if ch in ("sms",) and not p.phone_valid:
            continue
        if ch == "whatsapp" and not (p.phone_valid and p.whatsapp):
            continue
        if ch == "app" and not p.has_smartphone:
            continue
        if has_consent(db, p.id, f"comm_{ch}"):
            return ch
    return None


def send_invite(db: Session, actor: Actor, r: Recall, p: Patient, now: datetime | None = None) -> Communication:
    from ..comms.content import PROGRAMME_TEXT
    from ..comms.service import SIM_PREFIX, comm_language

    now = now or utcnow()
    if not _can_manage(actor) or not actor.can("comm:send"):
        raise Forbidden("your role cannot send recall invitations")
    if r.status not in OPEN:
        raise Invalid("this recall is no longer open")
    if p.deceased:
        raise DomainError("patient is recorded as deceased: all patient messaging is stopped")
    if consent_status(db, p.id, "care_coordination") == "deny":
        raise DomainError("the patient does not agree to be contacted by the screening team")
    ch = _channel(db, p)
    if ch is None:
        raise DomainError("no digital channel for this patient (no valid mobile, WhatsApp or app) - phone them "
                          "and record the outcome, or update their contact details")
    lang = comm_language(p)
    lang = lang if lang in RECALL_TEXT else "en"
    org = db.get(Organization, p.managing_org_id)
    centre = (org.name if org else None) if lang == "en" else CENTRE_FALLBACK[lang]
    body = RECALL_TEXT[lang].format(name=p.given_name, programme=PROGRAMME_TEXT[lang][r.programme]["name"],
                                    due=r.due_on.strftime("%d/%m/%Y"), centre=centre or CENTRE_FALLBACK[lang])
    com = Communication(case_id=None, patient_id=p.id, category="recall_invite", channel=ch, language=lang,
                        template_key="recall_invite", template_variant="standard", content=SIM_PREFIX + body,
                        status="completed", simulated=True, attempt_no=r.invite_count + 1, sent_at=now)
    db.add(com)
    r.status, r.invited_at, r.invited_by, r.last_invite_channel = "invited", now, actor.display, ch
    r.invite_count = (r.invite_count or 0) + 1
    db.flush()
    audit.record(db, actor, "C", "recall.invited", entity_type="Recall", entity_id=r.id, ts=now,
                 detail={"patient_id": p.id, "channel": ch, "language": lang, "communication_id": com.id,
                         "invite_no": r.invite_count})
    return com


def cancel(db: Session, actor: Actor, r: Recall, reason: str, now: datetime | None = None) -> Recall:
    now = now or utcnow()
    if not _can_manage(actor):
        raise Forbidden("your role cannot change recalls")
    if r.status not in OPEN:
        raise Invalid("this recall is no longer open")
    if len((reason or "").strip()) < 5:
        raise Invalid("a reason is required (at least 5 characters)")
    r.status, r.closed_note, r.closed_by = "cancelled", reason.strip()[:500], actor.display
    audit.record(db, actor, "U", "recall.cancelled", entity_type="Recall", entity_id=r.id, ts=now,
                 detail={"patient_id": r.patient_id, "reason": r.closed_note})
    return r


def patient_recalls(db: Session, p: Patient) -> list[dict]:
    today = utcnow().date()
    return [row(db, r, p, today) for r in db.scalars(select(Recall).where(Recall.patient_id == p.id)
                                                       .order_by(Recall.id.desc()).limit(20))]


# ------------------------------------------------------------------------------------------------ KPI
def uptake(db: Session, now: datetime | None = None, actor: Actor | None = None) -> dict:
    """Recall uptake: of recalls due at least RECALL_UPTAKE_WINDOW_DAYS ago (so everyone had the full window),
    the share completed by a screening result within the window. Cancelled / superseded recalls are excluded.
    With an actor outside the programme-wide roles, counts are limited to their own centre / registered list."""
    from .patients import PROGRAMME_WIDE_ROLES

    now = now or utcnow()
    cutoff = now.date() - timedelta(days=RECALL_UPTAKE_WINDOW_DAYS)
    today = now.date()
    scope = []
    if actor is not None and actor.role not in PROGRAMME_WIDE_ROLES:
        conds = []
        if actor.org_id is not None:
            conds.append(Patient.managing_org_id == actor.org_id)
        if actor.practitioner_id is not None:
            conds.append(Patient.gp_id == actor.practitioner_id)
        pids = select(Patient.id).where(or_(*conds)) if conds else select(Patient.id).where(Patient.id == -1)
        scope = [Recall.patient_id.in_(pids)]
    den = db.scalar(select(func.count(Recall.id)).where(Recall.due_on <= cutoff,
                                                       Recall.status.in_(("due", "invited", "completed")), *scope)) or 0
    rows = db.execute(select(Recall.due_on, Recall.completed_at).where(Recall.due_on <= cutoff, Recall.status == "completed", *scope)).all()
    num = sum(1 for due, done in rows if done is not None and (done.date() - due).days <= RECALL_UPTAKE_WINDOW_DAYS)
    open_due = db.scalar(select(func.count(Recall.id)).where(Recall.status.in_(OPEN), Recall.due_on <= today, *scope)) or 0
    invited = db.scalar(select(func.count(Recall.id)).where(Recall.status == "invited", *scope)) or 0
    return {"denominator": den, "completed_in_window": num, "open_due_now": open_due, "invited_open": invited,
            "window_days": RECALL_UPTAKE_WINDOW_DAYS, "scope": "programme" if not scope else "yours"}


# ------------------------------------------------------------------------------------ SYN- sample backfill
def backfill_sample_recalls(db: Session, now: datetime | None = None) -> dict:
    """One-time: SYN- sample patients get the recall their existing history implies (latest negative result,
    or latest closed case). Real records are never touched; nothing is overwritten (skips people with a recall)."""
    now = now or utcnow()
    have = {(pid, prog) for pid, prog in db.execute(select(Recall.patient_id, Recall.programme))}
    patients = {p.id: p for p in db.scalars(select(Patient).where(Patient.mrn.like("SYN-%"), Patient.synthetic.is_(True),
                                                                  Patient.deceased.is_(False)))}
    actor = Actor(user_id=None, role="system", display="system (one-time recall backfill, sample records)", is_human=False)
    latest_rep: dict[tuple[int, str], DiagnosticReport] = {}
    for rep in db.scalars(select(DiagnosticReport).where(DiagnosticReport.category == "screening",
                                                         DiagnosticReport.patient_id.in_(list(patients)))
                          .order_by(DiagnosticReport.issued_at)):
        latest_rep[(rep.patient_id, rep.programme)] = rep
    open_cases = {(c.patient_id, c.programme) for c in db.scalars(select(Case).where(Case.state != "closed"))}
    n = {"routine_after_negative": 0, "after_closure": 0, "surveillance": 0, "none": 0}
    for (pid, prog), rep in latest_rep.items():
        if (pid, prog) in have or (pid, prog) in open_cases:
            continue
        p = patients[pid]
        r = None
        if rep.conclusion == "negative" and rep.case_id is None:
            r = after_negative(db, p, rep, actor, now)
        elif rep.case_id is not None:
            c = db.get(Case, rep.case_id)
            if c is not None and c.state == "closed":
                r = after_closure(db, c, actor, now)
        n[r.reason if r is not None else "none"] += 1
    return {"recalls": dict(n), "patients_considered": len(patients)}
