"""Appointment REQUESTS from the patient app. Patients never book directly.

* screening: the patient picks a programme, a preferred screening site and preferred days / times of day.
* recall: the same, started from a due (or soon due) recall with 'Book your next screening'; linked to the recall.
* follow_up: an open case whose follow-up appointment can be booked (clinic accepted the referral, or after a missed
  appointment). The patient sees only slots before the case's deadline (pathway target, else the tier window
  below) and picks one, or asks for another time saying what gets in the way (barrier, recorded as a lesson).

Every request lands in the care coordinators' work queue with an answer-by time (SLA clock by tier). A coordinator
confirms it (follow-up: books the appointment through workflow.book_appointment, so every booking rule applies;
screening / recall: records the booked time and place) or declines it with a reason. The patient is told by a
simulated message in their language (en / ar / hi / ur). Every step is audited (patient_request.*)."""
from __future__ import annotations

from datetime import datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..calendar_uae import is_working_day, to_local, to_utc
from ..models import (Appointment, Case, Communication, Organization, Patient, PatientRequest, Recall,
                      ServiceRequest, utcnow)
from ..programmes import PROGRAMMES
from ..security import Actor
from ..sla import TIER_INDEX, due_at, rag_status
from ..statemachine import S
from .errors import DomainError, Forbidden, Invalid, NotFound

KINDS = ("screening", "recall", "follow_up")
DAYS = ("mon", "tue", "wed", "thu", "fri")  # clinic days (UAE working week); weekends and public holidays have no slots
TIMES = ("morning", "afternoon", "evening")
DECLINE_REASONS = {"no_capacity": "No capacity at that site / time", "not_due": "Not due / not eligible yet",
                   "already_booked": "Already booked or requested", "clinical_review": "Needs clinical review first",
                   "other": "Other (explained in the note)"}
SITE_KINDS = ("primary_care", "hospital")  # where a screening can be done
BOOKABLE = (S.REFERRAL_ACCEPTED, S.NO_SHOW)  # follow-up states in which the appointment is the next step

# ---- timing (ILLUSTRATIVE prototype defaults - configuration, not clinical guidance) ----
# Slot window by tier when the case has no pathway target date (T0 critical, T1 urgent, T2 high, T3 routine).
SLOT_WINDOW_DAYS = {"critical": 2, "urgent": 7, "high": 14, "routine": 28}  # CONFIRM (clinical lead)
# Earliest offered slot: not sooner than this after the request, so the clinic can prepare.
SLOT_MIN_LEAD_HOURS = {"critical": 2, "urgent": 12, "high": 24, "routine": 24}  # CONFIRM (clinical lead / clinics)
SLOT_TIMES_LOCAL = ((8, 30), (10, 0), (11, 30), (13, 30), (15, 0))  # CONFIRM (clinics): offered start times, UAE time
MAX_SLOTS = 12  # CONFIRM (product): how many times the patient is offered at once
# Coordinator answer-by time (hours; T2/T3 and screening count UAE working days, like the case SLA clocks).
RESPONSE_HOURS = {"critical": 2, "urgent": 8, "high": 24, "routine": 48}  # CONFIRM (programme manager)
SCREENING_RESPONSE_TIER = "routine"  # CONFIRM (programme manager): screening / recall requests use the routine clock
RECALL_BOOK_AHEAD_DAYS = 60  # CONFIRM (clinical lead): 'Book your next screening' shows this long before the due date


def _iso(v) -> str | None:
    if v is None:
        return None
    return v.isoformat() + ("Z" if isinstance(v, datetime) and v.tzinfo is None else "")


def _me(db: Session, actor: Actor) -> Patient:
    """Server-side rule: a request is always for the signed-in patient themselves."""
    if actor.role != "patient" or actor.patient_id is None:
        raise Forbidden("only a patient can request an appointment from the patient app")
    p = db.get(Patient, actor.patient_id)
    if p is None:
        raise Forbidden("not a patient account")
    return p


def _coordinator(actor: Actor) -> None:
    if actor.role != "coordinator" or not actor.can("appointment:write"):
        raise Forbidden("appointment requests are confirmed or declined by a care coordinator")


def _lang(p: Patient, lang: str | None = None) -> str:
    from ..comms.request_text import REQ_MSG

    lang = lang or p.language
    return lang if lang in REQ_MSG else "en"


def _open_for(db: Session, p: Patient, *, programme: str | None = None, case_id: int | None = None) -> PatientRequest | None:
    q = select(PatientRequest).where(PatientRequest.patient_id == p.id, PatientRequest.status == "open")
    if case_id is not None:
        q = q.where(PatientRequest.case_id == case_id)
    elif programme is not None:
        q = q.where(PatientRequest.programme == programme, PatientRequest.kind.in_(("screening", "recall")))
    return db.scalars(q.order_by(PatientRequest.id.desc())).first()


def _open_screening_request(db: Session, p: Patient) -> PatientRequest | None:
    """Any waiting screening or next-screening request (one at a time, any programme)."""
    return db.scalars(select(PatientRequest).where(
        PatientRequest.patient_id == p.id, PatientRequest.status == "open",
        PatientRequest.kind.in_(("screening", "recall"))).order_by(PatientRequest.id.desc())).first()


def _upcoming_confirmed_screening(db: Session, p: Patient, now: datetime) -> PatientRequest | None:
    """A confirmed screening / next-screening whose booked time has not yet started (UTC naive)."""
    return db.scalars(select(PatientRequest).where(
        PatientRequest.patient_id == p.id, PatientRequest.status == "confirmed",
        PatientRequest.kind.in_(("screening", "recall")), PatientRequest.confirmed_start.is_not(None),
        PatientRequest.confirmed_start >= now).order_by(PatientRequest.confirmed_start)).first()


def _refuse_duplicate_screening(db: Session, p: Patient, lang: str, now: datetime) -> None:
    """Patients may have only one waiting screening request, or one upcoming booked screening, at a time."""
    from ..calendar_uae import local_display
    from ..comms.request_text import REQ_TEXT

    T = REQ_TEXT[lang if lang in REQ_TEXT else "en"]
    if _open_screening_request(db, p) is not None:
        raise DomainError(T["already_requested"])
    booked = _upcoming_confirmed_screening(db, p, now)
    if booked is not None:
        org = db.get(Organization, booked.confirmed_org_id) if booked.confirmed_org_id else None
        raise DomainError(T["already_booked"].format(
            when=local_display(booked.confirmed_start), place=org.name if org else ""))


def _answer_by(kind: str, tier: str, now: datetime) -> datetime | None:
    t = tier if kind == "follow_up" else SCREENING_RESPONSE_TIER
    return due_at(now, RESPONSE_HOURS.get(t, RESPONSE_HOURS["routine"]), t)


# ------------------------------------------------------------------ follow-up slots
def _deadline(case: Case, now: datetime) -> datetime:
    if case.target_due_at is not None:
        return case.target_due_at
    return now + timedelta(days=SLOT_WINDOW_DAYS.get(case.tier, SLOT_WINDOW_DAYS["routine"]))


def _follow_up_org(db: Session, case: Case) -> int | None:
    ref = db.scalars(select(ServiceRequest).where(ServiceRequest.case_id == case.id, ServiceRequest.kind == "referral",
                                                  ServiceRequest.status == "accepted").order_by(ServiceRequest.id.desc())).first()
    return ref.performer_org_id if ref is not None else None


def follow_up_slots(db: Session, case: Case, now: datetime | None = None) -> dict:
    """Times the patient may pick: clinic days, offered start times, after the lead time, before the deadline, not
    already taken at that clinic. Returns {deadline, slots: [utc naive iso], place}."""
    now = now or utcnow()
    deadline = _deadline(case, now)
    org_id = _follow_up_org(db, case)
    org = db.get(Organization, org_id) if org_id else None
    earliest = now + timedelta(hours=SLOT_MIN_LEAD_HOURS.get(case.tier, 24))
    taken = set()
    if org_id:
        taken = {a.start.replace(second=0, microsecond=0) for a in db.scalars(select(Appointment).where(
            Appointment.org_id == org_id, Appointment.status == "booked", Appointment.start >= now,
            Appointment.start <= deadline))}
    slots: list[datetime] = []
    d = to_local(earliest).date()
    last = to_local(deadline).date()
    while d <= last and len(slots) < MAX_SLOTS:
        if is_working_day(d):
            for h, m in SLOT_TIMES_LOCAL:
                t = to_utc(datetime.combine(d, time(h, m)))
                if earliest <= t <= deadline and t not in taken:
                    slots.append(t)
                    if len(slots) >= MAX_SLOTS:
                        break
        d += timedelta(days=1)
    return {"deadline": deadline, "slots": slots, "place": org.name if org else None, "org_id": org_id}


def can_choose_time(case: Case | None) -> bool:
    return case is not None and S(case.state) in BOOKABLE


# ------------------------------------------------------------------ patient side
def _notify(db: Session, req: PatientRequest, p: Patient, key: str, actor: Actor, now: datetime, **fmt) -> Communication:
    """Simulated message to the patient in this app (never really sent in this prototype)."""
    from ..comms.request_text import REQ_MSG
    from ..comms.service import SIM_PREFIX
    from .self_service import _prog_name

    lang = req.language or _lang(p)
    text = REQ_MSG[lang][key].format(programme=_prog_name(lang, req.programme), **{k: v or "" for k, v in fmt.items()})
    m = Communication(case_id=req.case_id, patient_id=p.id, direction="outbound", category="appointment_request",
                      channel="app", language=lang, template_key=f"appointment_request.{key}", content=SIM_PREFIX + text,
                      status="completed", simulated=True, sent_at=now)
    db.add(m)
    db.flush()
    audit.record(db, actor, "C", "comm.patient_request_message", entity_type="Communication", entity_id=m.id,
                 case_id=req.case_id, ts=now, detail={"request_id": req.id, "message": key, "language": lang, "simulated": True})
    return m


def request_screening(db: Session, actor: Actor, programme: str, site_org_id: int | None, days: list[str],
                      times: list[str], note: str | None = None, recall_id: int | None = None,
                      lang: str | None = None, now: datetime | None = None) -> PatientRequest:
    now = now or utcnow()
    p = _me(db, actor)
    if programme not in PROGRAMMES:
        raise Invalid(f"programme must be one of {list(PROGRAMMES)}")
    org = db.get(Organization, site_org_id) if site_org_id else None
    if org is None or not org.active or org.kind not in SITE_KINDS:
        raise Invalid("choose a screening site from the list")
    days = list(dict.fromkeys(days or []))
    times = list(dict.fromkeys(times or []))
    if not days or any(d not in DAYS for d in days):
        raise Invalid(f"choose at least one day ({', '.join(DAYS)})")
    if not times or any(t not in TIMES for t in times):
        raise Invalid(f"choose at least one time of day ({', '.join(TIMES)})")
    note = (note or "").strip() or None
    if note and len(note) > 1000:
        raise Invalid("note is too long (1000 characters at most)")
    rec = None
    if recall_id is not None:
        rec = db.get(Recall, recall_id)
        if rec is None or rec.patient_id != p.id:
            raise NotFound("recall not found")  # never reveal another patient's recall
        if rec.status not in ("due", "invited"):
            raise DomainError("this screening reminder is no longer open")
        if rec.programme != programme:
            raise Invalid("the screening must match the reminder")
    _refuse_duplicate_screening(db, p, _lang(p, lang), now)
    kind = "recall" if rec is not None else "screening"
    r = PatientRequest(patient_id=p.id, user_id=actor.user_id, kind=kind, programme=programme,
                       recall_id=rec.id if rec else None, tier=SCREENING_RESPONSE_TIER, preferred_org_id=org.id,
                       preferred_days=days, preferred_times=times, note=note, language=_lang(p, lang), status="open",
                       created_at=now, due_at=_answer_by(kind, SCREENING_RESPONSE_TIER, now))
    db.add(r)
    db.flush()
    audit.record(db, actor, "C", "patient_request.created", entity_type="PatientRequest", entity_id=r.id, ts=now,
                 detail={"kind": kind, "programme": programme, "site_org_id": org.id, "days": days, "times": times,
                         "recall_id": r.recall_id, "note": note})
    _notify(db, r, p, "received", actor, now)
    return r


def request_follow_up(db: Session, actor: Actor, case_id: int, *, slot: datetime | None = None,
                      barrier_code: str | None = None, preferred_text: str | None = None, note: str | None = None,
                      lang: str | None = None, now: datetime | None = None) -> PatientRequest:
    from . import workflow as wf
    from .patient_loop import PATIENT_BARRIERS

    now = now or utcnow()
    p = _me(db, actor)
    c = db.get(Case, case_id)
    if c is None or c.patient_id != p.id:
        raise NotFound("follow-up not found")  # never reveal another patient's case
    if not can_choose_time(c):
        raise DomainError("your care team is still arranging this follow-up - they will contact you")
    if _open_for(db, p, case_id=c.id) is not None:
        raise DomainError("you already sent a request for this follow-up - your care team will contact you")
    note = (note or "").strip() or None
    preferred_text = (preferred_text or "").strip() or None
    if (slot is None) == (barrier_code is None):
        raise Invalid("choose one of the offered times, or ask for another time and say what gets in the way")
    if slot is not None:
        offered = follow_up_slots(db, c, now)
        s = slot.replace(tzinfo=None, second=0, microsecond=0)
        if s not in offered["slots"]:
            raise Invalid("that time is not available any more - please choose one of the times shown")
        slot = s
    elif barrier_code not in PATIENT_BARRIERS:
        raise Invalid(f"barrier_code must be one of {list(PATIENT_BARRIERS)}")
    r = PatientRequest(patient_id=p.id, user_id=actor.user_id, kind="follow_up", programme=c.programme, case_id=c.id,
                       tier=c.tier, chosen_start=slot, barrier_code=barrier_code, preferred_text=preferred_text,
                       note=note, language=_lang(p, lang), status="open", created_at=now,
                       due_at=_answer_by("follow_up", c.tier, now))
    db.add(r)
    db.flush()
    if barrier_code is not None:  # what got in the way feeds the improvement proposals, as for other changes
        wf.add_lesson(db, c, "appointment_change_request", barrier_code, actor, now=now,
                      detail="patient asked for another follow-up time" + (f": {note}" if note else "")
                             + (f" (suits better: {preferred_text})" if preferred_text else ""))
    audit.record(db, actor, "C", "patient_request.created", entity_type="PatientRequest", entity_id=r.id, case_id=c.id,
                 ts=now, detail={"kind": "follow_up", "slot": slot.isoformat() if slot else None, "barrier": barrier_code,
                                 "preferred": preferred_text, "note": note, "tier": c.tier})
    _notify(db, r, p, "received", actor, now)
    return r


def withdraw(db: Session, actor: Actor, request_id: int, now: datetime | None = None) -> PatientRequest:
    now = now or utcnow()
    p = _me(db, actor)
    r = db.get(PatientRequest, request_id)
    if r is None or r.patient_id != p.id:
        raise NotFound("request not found")
    if r.status != "open":
        raise DomainError("this request has already been answered")
    r.status, r.decided_at, r.decided_by, r.decided_by_user_id = "withdrawn", now, actor.display, actor.user_id
    audit.record(db, actor, "U", "patient_request.withdrawn", entity_type="PatientRequest", entity_id=r.id,
                 case_id=r.case_id, ts=now, detail={})
    return r


def mine(db: Session, actor: Actor, lang: str) -> list[dict]:
    p = _me(db, actor)
    rows = db.scalars(select(PatientRequest).where(PatientRequest.patient_id == p.id)
                      .order_by(PatientRequest.id.desc()).limit(20)).all()
    orgs = {o.id: o.name for o in db.scalars(select(Organization))}
    return [{"id": r.id, "kind": r.kind, "programme": r.programme, "case_id": r.case_id, "recall_id": r.recall_id,
             "status": r.status, "at": _iso(r.created_at), "site": orgs.get(r.preferred_org_id),
             "days": r.preferred_days or [], "times": r.preferred_times or [], "chosen_start": _iso(r.chosen_start),
             "barrier_code": r.barrier_code, "preferred_text": r.preferred_text,
             "confirmed_start": _iso(r.confirmed_start), "confirmed_place": orgs.get(r.confirmed_org_id),
             "decline_reason": r.decline_reason} for r in rows]


def options(db: Session) -> dict:
    sites = db.scalars(select(Organization).where(Organization.active.is_(True), Organization.kind.in_(SITE_KINDS))
                       .order_by(Organization.kind.desc(), Organization.region, Organization.name)).all()
    return {"programmes": list(PROGRAMMES), "days": list(DAYS), "times": list(TIMES),
            "sites": [{"id": o.id, "name": o.name, "region": o.region, "kind": o.kind} for o in sites]}


def screening_availability(db: Session, actor: Actor, now: datetime | None = None) -> dict:
    """Whether the patient app may offer 'Request an appointment' (and why not, if not)."""
    now = now or utcnow()
    p = _me(db, actor)
    open_req = _open_screening_request(db, p)
    booked = _upcoming_confirmed_screening(db, p, now)
    if open_req is not None:
        return {"can_request": False, "block_reason": "open_request", "booked": None}
    if booked is not None:
        org = db.get(Organization, booked.confirmed_org_id) if booked.confirmed_org_id else None
        return {"can_request": False, "block_reason": "booked",
                "booked": {"start": _iso(booked.confirmed_start), "place": org.name if org else None,
                           "programme": booked.programme}}
    return {"can_request": True, "block_reason": None, "booked": None}


def loop_extras(db: Session, actor: Actor, loop: list[dict], now: datetime | None = None) -> None:
    """Add to each programme of the patient's tracker: the follow-up times they may choose, the due recall they may
    book, and their open request (so no second request button is shown). A waiting screening request or an upcoming
    booked screening also hides 'Book your next screening' on every programme."""
    now = now or utcnow()
    p = _me(db, actor)
    screening_blocked = (_open_screening_request(db, p) is not None
                         or _upcoming_confirmed_screening(db, p, now) is not None)
    for x in loop:
        c = db.get(Case, x["case_id"]) if x.get("case_id") else None
        open_req = _open_for(db, p, case_id=c.id) if c is not None and x.get("open") else None
        if open_req is None:
            open_req = _open_for(db, p, programme=x["programme"]) if not x.get("open") else None
        x["open_request"] = {"id": open_req.id, "kind": open_req.kind} if open_req is not None else None
        x["choose_time"] = None
        if c is not None and can_choose_time(c) and open_req is None:
            fs = follow_up_slots(db, c, now)
            x["choose_time"] = {"deadline": _iso(fs["deadline"]), "slots": [_iso(s) for s in fs["slots"]],
                                "place": fs["place"], "tier": c.tier, "tier_label": f"T{TIER_INDEX.get(c.tier, 3)}"}
        x["book_recall"] = None
        rec = db.scalars(select(Recall).where(Recall.patient_id == p.id, Recall.programme == x["programme"],
                                              Recall.status.in_(("due", "invited"))).order_by(Recall.due_on)).first()
        if rec is not None and open_req is None and not x.get("open") and not screening_blocked \
                and rec.due_on <= (to_local(now).date() + timedelta(days=RECALL_BOOK_AHEAD_DAYS)):
            x["book_recall"] = {"recall_id": rec.id, "due_on": rec.due_on.isoformat()}


# ------------------------------------------------------------------ coordinator side
def _row(db: Session, r: PatientRequest, now: datetime, orgs: dict[int, str]) -> dict:
    p = db.get(Patient, r.patient_id)
    started = r.created_at
    rag, frac = rag_status(started, r.due_at, now) if r.status == "open" else ("none", 0.0)
    return {"id": r.id, "kind": r.kind, "programme": r.programme, "programme_name": PROGRAMMES[r.programme].name,
            "status": r.status, "tier": r.tier, "tier_label": f"T{TIER_INDEX.get(r.tier, 3)}",
            "patient": {"id": p.id, "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "language": p.language},
            "case_id": r.case_id, "recall_id": r.recall_id, "site": orgs.get(r.preferred_org_id),
            "preferred_org_id": r.preferred_org_id, "days": r.preferred_days or [], "times": r.preferred_times or [],
            "chosen_start": _iso(r.chosen_start), "barrier_code": r.barrier_code, "preferred_text": r.preferred_text,
            "note": r.note, "at": _iso(r.created_at), "due_at": _iso(r.due_at), "rag": rag, "elapsed": round(frac, 2),
            "decided_at": _iso(r.decided_at), "decided_by": r.decided_by, "decline_reason": r.decline_reason,
            "decision_note": r.decision_note, "confirmed_start": _iso(r.confirmed_start),
            "confirmed_place": orgs.get(r.confirmed_org_id), "appointment_id": r.appointment_id}


def work_list(db: Session, actor: Actor, status: str = "open", programme: str | None = None,
              now: datetime | None = None, limit: int = 100) -> dict:
    _coordinator(actor)
    now = now or utcnow()
    q = select(PatientRequest)
    if status != "all":
        q = q.where(PatientRequest.status == status)
    if programme:
        q = q.where(PatientRequest.programme == programme)
    rows = db.scalars(q.order_by(PatientRequest.due_at.is_(None), PatientRequest.due_at, PatientRequest.id).limit(limit)).all()
    orgs = {o.id: o.name for o in db.scalars(select(Organization))}
    items = [_row(db, r, now, orgs) for r in rows]
    return {"items": items, "decline_reasons": DECLINE_REASONS,
            "sites": options(db)["sites"], "open": sum(1 for i in items if i["status"] == "open"),
            "overdue": sum(1 for i in items if i["rag"] == "red")}


def _get_open(db: Session, request_id: int) -> PatientRequest:
    r = db.get(PatientRequest, request_id)
    if r is None:
        raise NotFound("request not found")
    if r.status != "open":
        raise DomainError(f"this request is already {r.status}")
    return r


def confirm(db: Session, actor: Actor, request_id: int, *, start: datetime | None = None, org_id: int | None = None,
            note: str | None = None, beyond_target_reason: str | None = None, now: datetime | None = None) -> PatientRequest:
    from ..calendar_uae import local_display
    from . import workflow as wf

    _coordinator(actor)
    now = now or utcnow()
    r = _get_open(db, request_id)
    p = db.get(Patient, r.patient_id)
    start = start.replace(tzinfo=None) if start is not None else None
    if r.kind == "follow_up":
        c = db.get(Case, r.case_id)
        start = start or r.chosen_start
        if start is None:
            raise Invalid("choose the appointment time (the patient asked for another time)")
        appt = wf.book_appointment(db, c, actor, start, beyond_target_reason=beyond_target_reason, now=now)
        r.appointment_id, r.confirmed_start, r.confirmed_org_id = appt.id, appt.start, appt.org_id
    else:
        if start is None:
            raise Invalid("choose the appointment date and time")
        if start < now - timedelta(minutes=5):
            raise Invalid("the appointment time is in the past")
        if start > now + timedelta(days=wf.MAX_BOOKING_AHEAD_DAYS):
            raise Invalid(f"the appointment is more than {wf.MAX_BOOKING_AHEAD_DAYS} days ahead: check the date")
        org = db.get(Organization, org_id or r.preferred_org_id)
        if org is None or org.kind not in SITE_KINDS:
            raise Invalid("choose the screening site")
        r.confirmed_start, r.confirmed_org_id = start, org.id
        if r.recall_id:
            rec = db.get(Recall, r.recall_id)
            if rec is not None and rec.status in ("due", "invited"):
                rec.status, rec.invited_at, rec.invited_by = "invited", now, actor.display
                rec.invite_count = (rec.invite_count or 0) + 1
                rec.last_invite_channel = "app"
    r.status, r.decided_at, r.decided_by, r.decided_by_user_id = "confirmed", now, actor.display, actor.user_id
    r.decision_note = (note or "").strip() or None
    place = db.get(Organization, r.confirmed_org_id)
    audit.record(db, actor, "U", "patient_request.confirmed", entity_type="PatientRequest", entity_id=r.id,
                 case_id=r.case_id, ts=now, detail={"kind": r.kind, "start": r.confirmed_start.isoformat(),
                                                    "org_id": r.confirmed_org_id, "appointment_id": r.appointment_id,
                                                    "recall_id": r.recall_id, "note": r.decision_note,
                                                    "answered_late": bool(r.due_at and now > r.due_at)})
    m = _notify(db, r, p, "confirmed", actor, now, when=local_display(r.confirmed_start), place=place.name if place else "")
    r.notified_comm_id = m.id
    return r


def decline(db: Session, actor: Actor, request_id: int, reason_code: str, note: str,
            now: datetime | None = None) -> PatientRequest:
    from ..comms.request_text import REQ_TEXT

    _coordinator(actor)
    now = now or utcnow()
    if reason_code not in DECLINE_REASONS:
        raise Invalid(f"reason must be one of {list(DECLINE_REASONS)}")
    note = (note or "").strip()
    if len(note) < 5:
        raise Invalid("say why the request cannot be booked (at least 5 characters); the patient is told the reason")
    r = _get_open(db, request_id)
    p = db.get(Patient, r.patient_id)
    r.status, r.decided_at, r.decided_by, r.decided_by_user_id = "declined", now, actor.display, actor.user_id
    r.decline_reason, r.decision_note = reason_code, note[:1000]
    audit.record(db, actor, "U", "patient_request.declined", entity_type="PatientRequest", entity_id=r.id,
                 case_id=r.case_id, ts=now, detail={"kind": r.kind, "reason": reason_code, "note": r.decision_note,
                                                    "answered_late": bool(r.due_at and now > r.due_at)})
    lang = r.language or _lang(p)
    m = _notify(db, r, p, "declined", actor, now, reason=REQ_TEXT[lang]["decline_reason"][reason_code])
    r.notified_comm_id = m.id
    return r


def open_count(db: Session) -> int:
    from sqlalchemy import func

    return db.scalar(select(func.count(PatientRequest.id)).where(PatientRequest.status == "open")) or 0
