"""Patient side of the screening-to-care loop (the patient app, /patient).

* ``loop_view``: where the person is in the loop for each programme, as a simple step tracker written for the
  patient (screening done -> result ready -> follow-up check -> appointment booked -> appointment attended ->
  care started / follow-up complete -> next screening due) plus the one next step. Worded to reassure, never with
  raw clinical values; an actionable result shows only "your care team is looking at it" until the team has told
  the patient (same rule as the result history).
* Appointment answers: the patient can confirm a booked appointment or ask to change it, saying what gets in the
  way (a barrier code, the same list staff use for no-shows and changes) and when would suit better. A change
  request NEVER moves the appointment: it is recorded (``appointment_requests``), counted as a lesson (barrier),
  shown to the care team on the case and in the queue, and closed when staff move / cancel the appointment or mark
  it handled. Every step is audited.
* ``messages_view``: messages and notifications sent to the patient (all SIMULATED in this prototype, labelled so);
  staff call notes and activation codes are never shown.
* ``consent_view``: the patient's contact consents (app / SMS / WhatsApp / phone) and care-coordination consent;
  changes go through workflow.record_consent (audited ``consent.recorded``; withdrawing care coordination sends
  every open case to a clinician, as before).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..models import (Appointment, AppointmentRequest, CarePlan, Case, Communication, Consent, DiagnosticReport,
                      Organization, Patient, Recall, utcnow)
from ..security import Actor
from ..statemachine import S
from .errors import DomainError, Forbidden, Invalid, NotFound

# How far ahead the next screening date is shown as "due soon" on the patient's tracker.
RECALL_SOON_DAYS = 60  # CONFIRM (clinical lead): window for the "due soon" wording of the next screening date

STAGES = ("screened", "result", "follow_up", "appointment", "attended", "care", "recall")
NOT_TOLD = (S.ISSUED, S.TRIAGED)
ARRANGING = (S.ACKNOWLEDGED, S.CONFIRMATORY_TEST, S.CONSULTATION, S.REFERRAL_SENT, S.REFERRAL_ACCEPTED,
             S.REFERRAL_REJECTED)
# barrier codes a patient can choose when asking to change an appointment (subset of workflow.NO_SHOW_BARRIERS;
# "forgot" makes no sense before the appointment, "unknown" is shown as "another reason")
PATIENT_BARRIERS = ("work", "transport", "childcare", "cost", "fear_anxiety", "language", "ramadan_hours", "unknown")
CONTACT_SCOPES = ("comm_app", "comm_sms", "comm_whatsapp", "comm_phone", "comm_email")
PATIENT_SCOPES = CONTACT_SCOPES + ("care_coordination",)
HIDDEN_CATEGORIES = ("escalation",)


def _me(db: Session, actor: Actor) -> Patient:
    if actor.role != "patient" or actor.patient_id is None:
        raise Forbidden("not a patient account")
    p = db.get(Patient, actor.patient_id)
    if p is None:
        raise Forbidden("not a patient account")
    return p


def _iso(v) -> str | None:
    return v.isoformat() if v is not None else None


# ------------------------------------------------------------------ appointments: confirm / ask to change
def _own_booked(db: Session, actor: Actor, appt_id: int, now: datetime) -> tuple[Appointment, Case]:
    p = _me(db, actor)
    a = db.get(Appointment, appt_id)
    c = db.get(Case, a.case_id) if a is not None else None
    if a is None or c is None or c.patient_id != p.id:
        raise NotFound("appointment not found")  # never reveal another patient's appointment
    if a.status != "booked" or S(c.state) != S.APPOINTMENT_BOOKED:
        raise DomainError("this appointment is no longer booked - please check the latest one")
    if a.start < now:
        raise DomainError("this appointment time has passed - your care team will contact you")
    return a, c


def requests_for(db: Session, appointment_ids: list[int]) -> dict[int, list[AppointmentRequest]]:
    out: dict[int, list[AppointmentRequest]] = {}
    if not appointment_ids:
        return out
    for r in db.scalars(select(AppointmentRequest).where(AppointmentRequest.appointment_id.in_(appointment_ids))
                        .order_by(AppointmentRequest.id)):
        out.setdefault(r.appointment_id, []).append(r)
    return out


def confirm_appointment(db: Session, actor: Actor, appt_id: int, now: datetime | None = None) -> AppointmentRequest:
    now = now or utcnow()
    a, c = _own_booked(db, actor, appt_id, now)
    prev = requests_for(db, [a.id]).get(a.id, [])
    if any(r.kind == "change" and r.status == "open" for r in prev):
        raise DomainError("you already asked to change this appointment - your care team will contact you")
    done = next((r for r in prev if r.kind == "confirm"), None)
    if done is not None:
        return done  # confirming twice changes nothing
    r = AppointmentRequest(appointment_id=a.id, case_id=c.id, patient_id=c.patient_id, user_id=actor.user_id,
                           kind="confirm", status="noted", created_at=now)
    db.add(r)
    db.flush()
    audit.record(db, actor, "C", "appointment.patient_confirmed", entity_type="Appointment", entity_id=a.id,
                 case_id=c.id, ts=now, detail={"request_id": r.id, "start": a.start.isoformat()})
    return r


def request_change(db: Session, actor: Actor, appt_id: int, barrier_code: str, note: str | None = None,
                   preferred_times: str | None = None, now: datetime | None = None) -> AppointmentRequest:
    from . import workflow as wf

    now = now or utcnow()
    if barrier_code not in PATIENT_BARRIERS:
        raise Invalid(f"barrier_code must be one of {list(PATIENT_BARRIERS)}")
    note = (note or "").strip() or None
    preferred_times = (preferred_times or "").strip() or None
    if note and len(note) > 1000:
        raise Invalid("note is too long (1000 characters at most)")
    if preferred_times and len(preferred_times) > 200:
        raise Invalid("preferred times are too long (200 characters at most)")
    a, c = _own_booked(db, actor, appt_id, now)
    if any(r.kind == "change" and r.status == "open" for r in requests_for(db, [a.id]).get(a.id, [])):
        raise DomainError("you already asked to change this appointment - your care team will contact you")
    r = AppointmentRequest(appointment_id=a.id, case_id=c.id, patient_id=c.patient_id, user_id=actor.user_id,
                           kind="change", barrier_code=barrier_code, note=note, preferred_times=preferred_times,
                           status="open", created_at=now)
    db.add(r)
    db.flush()
    # what got in the way is a lesson (feeds the improvement proposals), as for staff-recorded changes
    wf.add_lesson(db, c, "appointment_change_request", barrier_code, actor, now=now,
                  detail="patient asked to change the appointment" + (f": {note}" if note else "")
                         + (f" (suits better: {preferred_times})" if preferred_times else ""))
    audit.record(db, actor, "C", "appointment.change_requested", entity_type="Appointment", entity_id=a.id,
                 case_id=c.id, ts=now, detail={"request_id": r.id, "barrier": barrier_code, "note": note,
                                               "preferred_times": preferred_times, "start": a.start.isoformat()})
    return r


def close_open_requests(db: Session, appt: Appointment, actor: Actor, how: str, now: datetime) -> int:
    """Staff moved, cancelled or recorded the appointment: open change requests for it are handled."""
    n = 0
    for r in db.scalars(select(AppointmentRequest).where(AppointmentRequest.appointment_id == appt.id,
                                                         AppointmentRequest.status == "open")):
        r.status, r.handled_at, r.handled_by, r.handled_note = "handled", now, actor.display, how
        n += 1
    return n


def resolve_request(db: Session, case: Case, actor: Actor, request_id: int, note: str,
                    now: datetime | None = None) -> AppointmentRequest:
    """Staff: the patient's change request was dealt with another way (e.g. phoned, the patient keeps the time)."""
    now = now or utcnow()
    if not actor.can("appointment:write"):
        raise Forbidden("role lacks appointment:write")
    r = db.get(AppointmentRequest, request_id)
    if r is None or r.case_id != case.id:
        raise NotFound("request not found")
    if r.kind != "change" or r.status != "open":
        raise DomainError("this request is already handled")
    note = (note or "").strip()
    if len(note) < 5:
        raise Invalid("say what was done (at least 5 characters)")
    r.status, r.handled_at, r.handled_by, r.handled_note = "handled", now, actor.display, note[:1000]
    audit.record(db, actor, "U", "appointment.change_request_handled", entity_type="Appointment",
                 entity_id=r.appointment_id, case_id=case.id, ts=now, detail={"request_id": r.id, "note": note[:1000]})
    return r


def staff_view(db: Session, case: Case) -> list[dict]:
    """Patient answers to appointments of this case, for the case screen (newest first)."""
    rows = db.scalars(select(AppointmentRequest).where(AppointmentRequest.case_id == case.id)
                      .order_by(AppointmentRequest.id.desc())).all()
    return [{"id": r.id, "appointment_id": r.appointment_id, "kind": r.kind, "barrier_code": r.barrier_code,
             "note": r.note, "preferred_times": r.preferred_times, "status": r.status, "at": _iso(r.created_at),
             "handled_at": _iso(r.handled_at), "handled_by": r.handled_by, "handled_note": r.handled_note} for r in rows]


# ------------------------------------------------------------------ where am I in the loop
def _stage(key: str, status: str, on: Any = None) -> dict:
    return {"key": key, "status": status, "date": _iso(on)}


def _next_recall(db: Session, p: Patient, programme: str) -> Recall | None:
    return db.scalars(select(Recall).where(Recall.patient_id == p.id, Recall.programme == programme,
                                           Recall.status.in_(("due", "invited"))).order_by(Recall.due_on).limit(1)).first()


def _visited(c: Case) -> set[str]:
    return {s.state for s in c.steps}


def _programme_loop(db: Session, p: Patient, programme: str, c: Case | None, rep: DiagnosticReport | None,
                    now: datetime) -> dict:
    """One programme: the stages and the situation key that picks the plain-language next step."""
    today = today_local(now)
    rec = _next_recall(db, p, programme)
    recall = None
    if rec is not None:
        recall = {"due_on": rec.due_on.isoformat(), "status": rec.status, "reason": rec.reason,
                  "soon": rec.due_on <= today + timedelta(days=RECALL_SOON_DAYS), "overdue": rec.due_on < today}
    screened_on = (rep.specimen_collected_at or rep.issued_at) if rep is not None else None
    st: dict[str, dict] = {k: _stage(k, "todo") for k in STAGES}
    if rep is not None:
        st["screened"] = _stage("screened", "done", screened_on)
    appt = None
    newer_normal = (c is not None and c.state == S.CLOSED and rep is not None and rep.case_id != c.id
                    and rep.category == "screening" and rep.conclusion == "negative" and rep.issued_at >= (c.closed_at or rep.issued_at))
    if c is None or newer_normal:
        # newest result needed no follow-up (normal), or no case at all
        if rep is not None and rep.conclusion == "negative":
            st["result"] = _stage("result", "done", rep.issued_at)
            for k in ("follow_up", "appointment", "attended", "care"):
                st[k] = _stage(k, "skipped")
            situation = "normal"
        elif rep is not None:  # not yet looked at by a clinician (no case opened yet)
            st["result"] = _stage("result", "current", rep.issued_at)
            situation = "reviewing"
        else:
            situation = "none"
    else:
        state = S(c.state)
        seen = _visited(c)
        appts = db.scalars(select(Appointment).where(Appointment.case_id == c.id).order_by(Appointment.id)).all()
        live = next((a for a in reversed(appts) if a.status == "booked"), None)
        fulfilled = next((a for a in reversed(appts) if a.status == "fulfilled"), None)
        plan = db.scalars(select(CarePlan).where(CarePlan.case_id == c.id).order_by(CarePlan.id.desc())).first()
        told = state not in NOT_TOLD
        st["result"] = _stage("result", "done" if told else "current", c.report.issued_at if c.report else None)
        if not told:
            situation = "reviewing"
        else:
            st["follow_up"] = _stage("follow_up", "current" if state in (S.PATIENT_NOTIFIED, S.UNREACHABLE,
                                                                         S.DECLINED_REVIEW) + ARRANGING else "done")
            if state in (S.APPOINTMENT_BOOKED, S.NO_SHOW):
                st["appointment"] = _stage("appointment", "current" if state == S.APPOINTMENT_BOOKED else "attention",
                                           live.start if live else None)
                st["follow_up"] = _stage("follow_up", "done")
            if fulfilled is not None or S.APPOINTMENT_ATTENDED.value in seen:
                st["appointment"] = _stage("appointment", "done", fulfilled.start if fulfilled else None)
                st["attended"] = _stage("attended", "done", fulfilled.start if fulfilled else None)
                if state == S.APPOINTMENT_ATTENDED:
                    st["care"] = _stage("care", "current")
            if state in (S.TREATMENT_STARTED, S.CLOSED):
                st["follow_up"] = _stage("follow_up", "done")
                if fulfilled is None and S.APPOINTMENT_ATTENDED.value not in seen:
                    st["appointment"] = _stage("appointment", "skipped")
                    st["attended"] = _stage("attended", "skipped")
                st["care"] = _stage("care", "done" if state == S.CLOSED else "current",
                                    c.closed_at if state == S.CLOSED else (plan.created_at if plan else c.care_reached_at))
            situation = {S.PATIENT_NOTIFIED: "answer", S.UNREACHABLE: "unreachable", S.DECLINED_REVIEW: "declined",
                         S.APPOINTMENT_BOOKED: "booked", S.NO_SHOW: "missed", S.APPOINTMENT_ATTENDED: "attended",
                         S.TREATMENT_STARTED: "care", S.CLOSED: "closed"}.get(state, "arranging")
            if state == S.CLOSED and c.closure_type == "authorised_decision":
                situation = "closed_other"  # closed by a named clinician's decision (e.g. moved abroad, declined)
            appt = live if state == S.APPOINTMENT_BOOKED else None
    if recall is not None:
        st["recall"] = _stage("recall", "attention" if recall["overdue"] else ("current" if recall["soon"] and situation
                              in ("normal", "closed", "closed_other", "none") else "todo"), rec.due_on)
    elif situation in ("normal", "closed", "closed_other"):
        st["recall"] = _stage("recall", "todo")
    current = next((s["key"] for s in st.values() if s["status"] in ("current", "attention")), None)
    out = {"programme": programme, "case_id": c.id if c is not None else None, "situation": situation,
           "stages": [st[k] for k in STAGES], "current": current, "recall": recall, "appointment": None,
           "open": c is not None and c.state != S.CLOSED}
    if appt is not None:
        org = db.get(Organization, appt.org_id)
        mine = requests_for(db, [appt.id]).get(appt.id, [])
        out["appointment"] = {"id": appt.id, "start": appt.start.isoformat(), "place": org.name if org else None,
                              "confirmed": any(r.kind == "confirm" for r in mine),
                              "change_requested": any(r.kind == "change" and r.status == "open" for r in mine)}
        if out["appointment"]["change_requested"]:
            out["situation"] = "booked_change"
        elif out["appointment"]["confirmed"]:
            out["situation"] = "booked_confirmed"
    return out


def loop_view(db: Session, actor: Actor, now: datetime | None = None) -> list[dict]:
    """Every programme this person has a result, case or recall for; the open case first, then newest."""
    now = now or utcnow()
    p = _me(db, actor)
    cases = db.scalars(select(Case).where(Case.patient_id == p.id).order_by(Case.id.desc())).all()
    reports = db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == p.id,
                                                        DiagnosticReport.status.notin_(("entered-in-error", "cancelled")))
                         .order_by(DiagnosticReport.issued_at.desc(), DiagnosticReport.id.desc())).all()
    recalls = db.scalars(select(Recall.programme).where(Recall.patient_id == p.id, Recall.status.in_(("due", "invited")))).all()
    progs: list[str] = []
    for prog in [c.programme for c in cases if c.state != S.CLOSED] + [r.programme for r in reports] + \
            [c.programme for c in cases] + list(recalls):
        if prog not in progs:
            progs.append(prog)
    out = []
    for prog in progs:
        c = next((x for x in cases if x.programme == prog and x.state != S.CLOSED), None) or \
            next((x for x in cases if x.programme == prog), None)
        rep = next((r for r in reports if r.programme == prog), None)
        out.append(_programme_loop(db, p, prog, c, rep, now))
    return out


# ------------------------------------------------------------------ messages and consent
def messages_view(db: Session, actor: Actor, limit: int = 40) -> list[dict]:
    p = _me(db, actor)
    rows = db.scalars(select(Communication).where(Communication.patient_id == p.id,
                                                  Communication.recipient_practitioner_id.is_(None),
                                                  Communication.direction == "outbound",
                                                  Communication.status == "completed",
                                                  Communication.category.notin_(HIDDEN_CATEGORIES),
                                                  Communication.channel.in_(("app", "sms", "whatsapp", "phone", "email")))
                      .order_by(Communication.sent_at.desc(), Communication.id.desc()).limit(limit)).all()
    from ..comms.service import SIM_PREFIX

    out = []
    for m in rows:
        text = m.content or ""
        if text.startswith(SIM_PREFIX):
            text = text[len(SIM_PREFIX):]
        elif text.startswith("[SIMULATED] "):
            text = text[len("[SIMULATED] "):]
        kind = "message"
        if m.channel == "phone":
            kind, text = "call", None  # a call record holds the staff member's note: never shown to the patient
        elif m.category == "account_invite":
            kind, text = "account", None  # the activation code is not repeated here
        out.append({"id": m.id, "at": _iso(m.sent_at), "channel": m.channel, "category": m.category, "kind": kind,
                    "text": text, "language": m.language, "simulated": bool(m.simulated)})
    return out


def consent_view(db: Session, actor: Actor) -> dict:
    p = _me(db, actor)
    out = {}
    for scope in PATIENT_SCOPES:
        row = db.scalars(select(Consent).where(Consent.patient_id == p.id, Consent.scope == scope,
                                               Consent.status == "active", Consent.case_id.is_(None))
                         .order_by(Consent.id.desc()).limit(1)).first()
        out[scope] = {"decision": row.decision if row else None, "since": _iso(row.recorded_at) if row else None,
                      "by_patient": bool(row and row.recorded_by_user_id is not None and row.recorded_by_user_id == actor.user_id)}
    return out


def record_own_consent(db: Session, actor: Actor, scope: str, decision: str, note: str | None = None,
                       now: datetime | None = None) -> Consent:
    from . import workflow as wf

    if scope not in PATIENT_SCOPES:
        raise Invalid(f"scope must be one of {list(PATIENT_SCOPES)}")
    p = _me(db, actor)
    return wf.record_consent(db, p, actor, scope, decision, note, now=now)


def today_local(now: datetime | None = None) -> date:
    from ..calendar_uae import to_local

    return to_local(now or utcnow()).date()
