"""Case API: list, detail, timeline, guarded actions, FHIR export."""
from __future__ import annotations

from ..programmes import CLINICIAN_CONFIRMED_TIERS

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_, select
from sqlalchemy.orm import Session, joinedload

from .. import audit
from ..comms import service as comms
from ..db import get_db
from ..fhir import export_case
from ..models import Case, ConsultRequest, Escalation, Patient
from ..security import Actor
from ..services import workflow as wf
from ..services.access import assert_can_view, can_view_case, case_visible
from ..services.errors import Forbidden, Invalid, NotFound
from ..services.escalation import acknowledge_escalation
from ..statemachine import AUTHORISED_CLOSURE_REASONS, LABELS, S
from . import schemas as sch
from .actions import available_actions
from .deps import current_actor, require
from .serializers import case_detail, case_summary

router = APIRouter(prefix="/api", tags=["cases"])


def _load(db: Session, actor: Actor, case_id: int) -> Case:
    c = wf.get_case(db, case_id)
    assert_can_view(db, actor, c)
    return c


def _done(db: Session, c: Case, actor: Actor, **extra) -> dict:
    db.commit()
    db.refresh(c)
    return {"ok": True, "case": case_summary(db, c), **extra}


@router.get("/cases")
def list_cases(state: str | None = None, programme: str | None = None, q: str | None = Query(None, max_length=80),
               limit: int = Query(100, ge=1, le=500), actor: Actor = Depends(require("case:read")),
               db: Session = Depends(get_db)) -> dict:
    stmt = select(Case).join(Patient).options(joinedload(Case.patient), joinedload(Case.owner)).order_by(Case.id.desc())
    if state:
        stmt = stmt.where(Case.state == state)
    if programme:
        stmt = stmt.where(Case.programme == programme)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Patient.mrn.ilike(like), Patient.family_name.ilike(like), Patient.given_name.ilike(like)))
    out = []
    for c in db.scalars(stmt.limit(2000)).unique():
        if case_visible(db, actor, c):  # organisation involvement AND the patient's sharing consent
            out.append(case_summary(db, c))
        if len(out) >= limit:
            break
    return {"items": out, "count": len(out)}


@router.get("/cases/{case_id}")
def get_case(case_id: int, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    if actor.role == "patient":
        raise Forbidden("patients use /api/me")
    c = _load(db, actor, case_id)
    d = case_detail(db, c, actor)
    d["actions"] = available_actions(db, actor, c)
    return d


@router.get("/cases/{case_id}/actions")
def case_actions(case_id: int, actor: Actor = Depends(require("case:read")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    from ..statemachine import NEVER_FOR_CRITICAL

    reasons = {k: v for k, v in AUTHORISED_CLOSURE_REASONS.items()
               if not (c.tier == "critical" and k in NEVER_FOR_CRITICAL)}
    return {"actions": available_actions(db, actor, c), "closure_reasons": reasons,
            "reject_reasons": wf.REJECT_REASONS, "barriers": wf.NO_SHOW_BARRIERS, "appointment_change_reasons": wf.APPOINTMENT_CHANGE_REASONS, "delay_reasons": wf.DELAY_REASONS}


@router.get("/cases/{case_id}/fhir")
def fhir_export(case_id: int, actor: Actor = Depends(require("fhir:export")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    bundle = export_case(db, c, actor=actor)
    audit.record(db, actor, "E", "fhir.export", entity_type="Bundle", entity_id=c.id, case_id=c.id,
                 detail={"entries": len(bundle["entry"])})
    db.commit()
    return bundle


@router.post("/cases/{case_id}/transition")
def transition(case_id: int, body: sch.TransitionIn, actor: Actor = Depends(require("case:read")),
               db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    if body.to_state not in LABELS:
        raise Invalid("unknown state")
    wf.transition(db, c, body.to_state, actor, owner_id=body.owner_id, note=body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/close")
def close(case_id: int, body: sch.CloseIn, actor: Actor = Depends(require("case:write")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.close_case(db, c, actor, body.closure_type, evidence_type=body.evidence_type, evidence_id=body.evidence_id,
                  reason=body.reason, note=body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/critical-read-back")
def critical_read_back(case_id: int, body: sch.ReadBackIn, actor: Actor = Depends(require("critical:readback")),
                       db: Session = Depends(get_db)) -> dict:
    """Critical-result fast lane: record the direct clinician contact and read-back (services/critical.py)."""
    from ..calendar_uae import as_utc_naive
    from ..services.critical import record_read_back

    c = _load(db, actor, case_id)
    record_read_back(db, c, actor, method=body.method, reported_by=body.reported_by,
                     read_back_confirmed=body.read_back_confirmed,
                     received_by_practitioner_id=body.received_by_practitioner_id,
                     contacted_at=as_utc_naive(body.contacted_at) if body.contacted_at else None)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/review")
def review(case_id: int, body: sch.ReviewIn, actor: Actor = Depends(require("case:triage_urgent")),
           db: Session = Depends(get_db)) -> dict:
    """Clinician resolves the human-review flag (unknown/conflicting/amended result, context exception)."""
    c = _load(db, actor, case_id)
    wf.resolve_review(db, c, actor, note=body.note, tier=body.tier)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/reopen")
def reopen(case_id: int, body: sch.NoteIn, actor: Actor = Depends(require("case:reopen")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.transition(db, c, S.TRIAGED, actor, note=body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/assign")
def assign(case_id: int, body: sch.AssignIn, actor: Actor = Depends(require("case:assign")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.assign(db, c, body.owner_id, actor, body.reason)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/transfer")
def transfer(case_id: int, body: sch.TransferIn, actor: Actor = Depends(require("case:transfer")),
             db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.request_transfer(db, c, body.to_owner_id, actor, body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/transfer/decision")
def transfer_decision(case_id: int, body: sch.DecisionIn, actor: Actor = Depends(require("case:read")),
                      db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.decide_transfer(db, c, actor, body.accept, body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/notify")
def notify(case_id: int, body: sch.NotifyIn, actor: Actor = Depends(require("comm:send")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    res = comms.send_with_ladder(db, c, body.category, actor)
    return _done(db, c, actor, result=res)


@router.post("/cases/{case_id}/call")
def call(case_id: int, body: sch.CallIn, actor: Actor = Depends(require("call:record")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    comms.record_call(db, c, actor, body.outcome, note=body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/teach-back")
def navigator_teach_back(case_id: int, body: sch.TeachBackIn, actor: Actor = Depends(require("call:record")),
                         db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    qr = comms.submit_teach_back(db, c, actor, dict(body.answers), confidence=body.confidence, language=body.language)
    return _done(db, c, actor, passed=qr.passed, score=qr.score, total=qr.total)


@router.get("/cases/{case_id}/teach-back-form")
def teach_back_form(case_id: int, lang: str = "en", actor: Actor = Depends(require("case:read")),
                    db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    return comms.teach_back_form(c, lang)


@router.post("/cases/{case_id}/confirmatory")
def order_confirmatory(case_id: int, body: sch.ConfirmOrderIn, actor: Actor = Depends(require("confirm:order")),
                       db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.order_confirmatory(db, c, actor, body.performer_org_id)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/confirmatory/result")
def confirmatory_result(case_id: int, body: sch.ConfirmResultIn, actor: Actor = Depends(require("confirm:result")),
                        db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.record_confirmatory_result(db, c, actor, body.result)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/referral")
def referral(case_id: int, body: sch.ReferralIn, actor: Actor = Depends(require("referral:create")),
             db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    sr = wf.create_referral(db, c, actor, body.performer_org_id, performer_id=body.performer_id, summary=body.summary)
    return _done(db, c, actor, service_request_id=sr.id)


@router.post("/cases/{case_id}/referral/decision")
def referral_decision(case_id: int, body: sch.ReferralDecisionIn, actor: Actor = Depends(require("referral:decide")),
                      db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.decide_referral(db, c, actor, body.accept, reason_code=body.reason_code, note=body.note, performer_id=body.performer_id)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/appointment")
def appointment(case_id: int, body: sch.AppointmentIn, actor: Actor = Depends(require("appointment:write")),
                db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    from ..calendar_uae import as_utc_naive, local_display

    start = as_utc_naive(body.start)  # an offset is CONVERTED (was dropped: +04:00 input shifted by 4 h)
    a = wf.book_appointment(db, c, actor, start, org_id=body.org_id, practitioner_id=body.practitioner_id,
                            beyond_target_reason=body.beyond_target_reason)
    # the patient sees UAE local time (was UTC: a 09:00 appointment was confirmed as 05:00)
    res = comms.send_with_ladder(db, c, "appointment_confirmation", actor, date=local_display(start), max_channels=3)
    return _done(db, c, actor, appointment_id=a.id, result=res)


@router.post("/cases/{case_id}/appointment/reschedule")
def appointment_reschedule(case_id: int, body: sch.RescheduleIn, actor: Actor = Depends(require("appointment:write")),
                           db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    from ..calendar_uae import as_utc_naive, local_display

    start = as_utc_naive(body.start)
    a = wf.reschedule_appointment(db, c, actor, start, body.reason_code, barrier_code=body.barrier_code or None,
                                  note=body.note, beyond_target_reason=body.beyond_target_reason)
    res = comms.send_with_ladder(db, c, "appointment_changed", actor, date=local_display(start), max_channels=3)
    return _done(db, c, actor, appointment_id=a.id, result=res)


@router.post("/cases/{case_id}/appointment/cancel")
def appointment_cancel(case_id: int, body: sch.AppointmentChangeIn, actor: Actor = Depends(require("appointment:write")),
                       db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    from ..calendar_uae import local_display

    a = wf.cancel_appointment(db, c, actor, body.reason_code, barrier_code=body.barrier_code or None, note=body.note)
    res = comms.send_with_ladder(db, c, "appointment_cancelled", actor, date=local_display(a.start), max_channels=3)
    return _done(db, c, actor, appointment_id=a.id, result=res)


@router.post("/cases/{case_id}/appointment/remind")
def appointment_remind(case_id: int, actor: Actor = Depends(require("comm:send")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    from ..services.escalation import remind_appointment

    res = remind_appointment(db, c, actor)
    return _done(db, c, actor, result=res)


@router.post("/cases/{case_id}/appointment/requests/{request_id}/resolve")
def appointment_request_resolve(case_id: int, request_id: int, body: sch.ReasonIn,
                                actor: Actor = Depends(require("appointment:write")), db: Session = Depends(get_db)) -> dict:
    """The patient's 'please change my appointment' request was dealt with without moving it (e.g. phoned)."""
    from ..services.patient_loop import resolve_request

    c = _load(db, actor, case_id)
    resolve_request(db, c, actor, request_id, body.reason)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/attendance")
def attendance(case_id: int, body: sch.AttendanceIn, actor: Actor = Depends(require("appointment:write")),
               db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.record_attendance(db, c, actor, body.attended, barrier_code=body.barrier_code)
    if not body.attended:
        comms.send_with_ladder(db, c, "no_show", actor, max_channels=3)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/care-plan")
def care_plan(case_id: int, body: sch.CarePlanIn, actor: Actor = Depends(require("careplan:write")),
              db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    cp = wf.start_care(db, c, actor, body.kind, body.description, review_due_on=body.review_due_on)
    return _done(db, c, actor, care_plan_id=cp.id)


@router.get("/cases/{case_id}/record")
def clinical_record(case_id: int, actor: Actor = Depends(require("case:read")), db: Session = Depends(get_db)) -> dict:
    """Complete clinical record of the case's patient for the doctor's screen (staff only)."""
    from ..services.clinical_record import build_record
    c = _load(db, actor, case_id)  # every request is already audited by the request middleware
    return build_record(db, c, actor)


@router.post("/cases/{case_id}/encounters")
def add_encounter(case_id: int, body: sch.EncounterIn, actor: Actor = Depends(require("record:write")),
                  db: Session = Depends(get_db)) -> dict:
    from ..calendar_uae import as_utc_naive
    from ..services.clinical_record import add_encounter as _add
    c = _load(db, actor, case_id)
    e = _add(db, c, actor, kind=body.kind, start=as_utc_naive(body.start), notes=body.notes, reason=body.reason,
             outcome=body.outcome)
    db.commit()
    return {"ok": True, "encounter_id": e.id}


@router.post("/cases/{case_id}/medications")
def add_medication(case_id: int, body: sch.MedicationIn, actor: Actor = Depends(require("record:write")),
                   db: Session = Depends(get_db)) -> dict:
    from ..calendar_uae import as_utc_naive
    from ..services.clinical_record import add_medication as _add
    c = _load(db, actor, case_id)
    m = _add(db, c, actor, name=body.name, dose=body.dose, frequency=body.frequency, route=body.route,
             start_date=as_utc_naive(body.start_date) if body.start_date else None,
             end_date=as_utc_naive(body.end_date) if body.end_date else None, status=body.status,
             reason=body.reason, note=body.note)
    db.commit()
    return {"ok": True, "medication_id": m.id}


@router.post("/cases/{case_id}/medications/{med_id}/stop")
def stop_medication(case_id: int, med_id: int, body: sch.MedicationStopIn,
                    actor: Actor = Depends(require("record:write")), db: Session = Depends(get_db)) -> dict:
    from ..services.clinical_record import stop_medication as _stop
    c = _load(db, actor, case_id)
    _stop(db, c, actor, med_id, body.reason)
    db.commit()
    return {"ok": True}


@router.post("/cases/{case_id}/decline")
def decline(case_id: int, body: sch.NoteIn, actor: Actor = Depends(require("consent:write")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.record_decline(db, c, actor, body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/reconsider")
def reconsider(case_id: int, body: sch.NoteIn, actor: Actor = Depends(require("case:write")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.reconsider(db, c, actor, body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/consent")
def consent(case_id: int, body: sch.ConsentIn, actor: Actor = Depends(require("consent:write")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    wf.record_consent(db, c.patient, actor, body.scope, body.decision, body.note)
    return _done(db, c, actor)


@router.post("/cases/{case_id}/patient-deceased")
def patient_deceased(case_id: int, body: sch.NoteIn, actor: Actor = Depends(require("closure:authorise")),
                     db: Session = Depends(get_db)) -> dict:
    """Record the patient's death (named clinician, rationale/source required). Stops all outreach; open cases
    go to clinician review to be closed with an authorised decision."""
    c = _load(db, actor, case_id)
    flagged = wf.record_death(db, c.patient, actor, body.note)
    return _done(db, c, actor, flagged_cases=flagged)


@router.post("/cases/{case_id}/patient-deceased/revoke")
def patient_deceased_revoke(case_id: int, body: sch.NoteIn, actor: Actor = Depends(require("case:reopen")),
                            db: Session = Depends(get_db)) -> dict:
    """Correct a death recorded in error (clinical lead). Open cases are re-reviewed; closed-as-deceased are listed."""
    c = _load(db, actor, case_id)
    out = wf.revoke_death(db, c.patient, actor, body.note)
    return _done(db, c, actor, **out)


@router.post("/cases/{case_id}/consults")
def consult(case_id: int, body: sch.ConsultIn, actor: Actor = Depends(require("consult:create")),
            db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    cr = wf.create_consult(db, c, actor, body.question, target_practitioner_id=body.target_practitioner_id, urgency=body.urgency)
    return _done(db, c, actor, consult_id=cr.id)


@router.post("/consults/{consult_id}/respond")
def consult_respond(consult_id: int, body: sch.ConsultResponseIn, actor: Actor = Depends(require("consult:respond")),
                    db: Session = Depends(get_db)) -> dict:
    cr = db.get(ConsultRequest, consult_id)
    if cr is None:
        raise NotFound("consult not found")
    c = _load(db, actor, cr.case_id)
    wf.respond_consult(db, cr, actor, body.response)
    return _done(db, c, actor)


@router.get("/consults")
def my_consults(actor: Actor = Depends(require("consult:respond")), db: Session = Depends(get_db)) -> dict:
    rows = db.scalars(select(ConsultRequest).where(ConsultRequest.target_practitioner_id == actor.practitioner_id)
                      .order_by(ConsultRequest.status.desc(), ConsultRequest.due_at)).all()
    return {"items": [{"id": r.id, "case_id": r.case_id, "type": r.requester_type, "question": r.question,
                       "urgency": r.urgency, "status": r.status, "due_at": r.due_at.isoformat() if r.due_at else None,
                       "response": r.response} for r in rows]}


@router.post("/cases/{case_id}/lessons")
def lesson(case_id: int, body: sch.LessonIn, actor: Actor = Depends(require("lesson:write")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    les = wf.add_lesson(db, c, body.kind, body.reason_code, actor, detail=body.detail)
    return _done(db, c, actor, lesson_id=les.id)


@router.post("/escalations/{esc_id}/ack")
def ack_escalation(esc_id: int, body: sch.NoteIn, actor: Actor = Depends(require("escalation:ack")),
                   db: Session = Depends(get_db)) -> dict:
    esc = db.get(Escalation, esc_id)
    if esc is None:
        raise NotFound("escalation not found")
    c = _load(db, actor, esc.case_id)
    acknowledge_escalation(db, esc, actor, body.note)
    return _done(db, c, actor)


@router.post("/results")
def ingest_result(body: sch.ResultIn, actor: Actor = Depends(require("case:create")), db: Session = Depends(get_db)) -> dict:
    p = db.get(Patient, body.patient_id)
    from ..services.patients import can_view_patient

    if p is None or not can_view_patient(db, actor, p):  # same answer for both: never confirm another clinic's patient
        raise NotFound("patient not found")
    from ..models import Organization

    from sqlalchemy.exc import IntegrityError

    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    pid, org = p.id, (lab.id if lab else p.managing_org_id)
    from ..services.results_meta import check_result_meta

    meta, extra = check_result_meta(body.ordering_clinician, body.screening_site, body.specimen_collected_at)
    for attempt in range(3):  # a concurrent result for the same patient/programme: re-run and link, never duplicate
        try:
            rep, c = wf.ingest_result(db, db.get(Patient, pid), body.programme, value=body.value,
                                      value_text=body.value_text, unit=body.unit, test=body.test, status=body.status,
                                      cytology=body.cytology, performer_org_id=org, actor=actor,
                                      extra_reasons=extra or None, **meta)
            if c is not None and c.tier not in CLINICIAN_CONFIRMED_TIERS and S(c.state) == S.ISSUED \
                    and not c.review_required:
                wf.transition(db, c, S.TRIAGED, wf.SYSTEM, note="auto-triaged by deterministic programme rule")
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if attempt == 2:
                raise
    return {"ok": True, "report_id": rep.id, "actionable": c is not None, "case": case_summary(db, c) if c else None}
