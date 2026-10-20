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
from ..services.access import assert_can_view, can_view_case
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
        if can_view_case(db, actor, c):
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
    return {"actions": available_actions(db, actor, c), "closure_reasons": AUTHORISED_CLOSURE_REASONS,
            "reject_reasons": wf.REJECT_REASONS, "barriers": wf.NO_SHOW_BARRIERS, "delay_reasons": wf.DELAY_REASONS}


@router.get("/cases/{case_id}/fhir")
def fhir_export(case_id: int, actor: Actor = Depends(require("fhir:export")), db: Session = Depends(get_db)) -> dict:
    c = _load(db, actor, case_id)
    bundle = export_case(db, c)
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
    return _done(db, c, actor, passed=qr.passed, score=qr.score)


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
    start = body.start.replace(tzinfo=None) if body.start.tzinfo else body.start
    a = wf.book_appointment(db, c, actor, start, org_id=body.org_id, practitioner_id=body.practitioner_id)
    comms.send_with_ladder(db, c, "appointment_confirmation", actor, date=start.strftime("%d %b %Y %H:%M"), max_channels=3)
    return _done(db, c, actor, appointment_id=a.id)


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
    cp = wf.start_care(db, c, actor, body.kind, body.description)
    return _done(db, c, actor, care_plan_id=cp.id)


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
    if p is None:
        raise NotFound("patient not found")
    from ..models import Organization

    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    rep, c = wf.ingest_result(db, p, body.programme, value=body.value, value_text=body.value_text, unit=body.unit,
                              test=body.test, status=body.status, cytology=body.cytology,
                              performer_org_id=lab.id if lab else p.managing_org_id, actor=actor)
    if c is not None and c.tier not in CLINICIAN_CONFIRMED_TIERS and S(c.state) == S.ISSUED and not c.review_required:
        wf.transition(db, c, S.TRIAGED, wf.SYSTEM, note="auto-triaged by deterministic programme rule")
    db.commit()
    return {"ok": True, "report_id": rep.id, "actionable": c is not None, "case": case_summary(db, c) if c else None}
