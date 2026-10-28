"""Patient-facing API (the patient's own case only), in the patient's chosen language."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..comms import service as comms
from ..comms.content import (INTERPRETER_NOTICE, PROGRAMME_TEXT, programme_text, RTL_LANGUAGES, STATE_LABELS_I18N, SUPPORTED_LANGUAGES, TEMPLATES, TIMEFRAME,
                             UI_TEXT)
from ..db import get_db
from ..models import Case, Communication, ConsultRequest, Patient, Practitioner, QuestionnaireResponse, User
from ..security import Actor
from ..services import patient_answers as pa
from ..services import workflow as wf
from ..services.errors import Forbidden, NotFound
from ..statemachine import LABELS, S
from . import schemas as sch
from .deps import require
from .serializers import journey

router = APIRouter(prefix="/api/me", tags=["patient"])
URGENCY = {"en": {"critical": "Please contact us today", "urgent": "Please act soon", "high": "Important - please act within 2 weeks", "routine": "Please act within a month"},
           "ar": {"critical": "يرجى التواصل معنا اليوم", "urgent": "يرجى التصرف قريباً", "high": "مهم - يرجى التصرف خلال أسبوعين", "routine": "يرجى التصرف خلال شهر"},
           "hi": {"critical": "कृपया आज ही हमसे संपर्क करें", "urgent": "कृपया जल्दी कदम उठाएं", "high": "ज़रूरी - कृपया 2 हफ़्तों में कदम उठाएं", "routine": "कृपया एक महीने में कदम उठाएं"},
           "ur": {"critical": "براہ کرم آج ہی ہم سے رابطہ کریں", "urgent": "براہ کرم جلد قدم اٹھائیں", "high": "اہم - براہ کرم 2 ہفتوں میں قدم اٹھائیں", "routine": "براہ کرم ایک مہینے میں قدم اٹھائیں"}}


def _my_case(db: Session, actor: Actor) -> Case:
    if actor.patient_id is None:
        raise Forbidden("not a patient account")
    c = db.scalar(select(Case).where(Case.patient_id == actor.patient_id).order_by((Case.state == "closed").asc(),
                                                                                   Case.id.desc()).limit(1))
    if c is None:
        raise NotFound("no case found")
    return c


@router.get("/case")
def my_case(lang: str | None = Query(None, max_length=5), actor: Actor = Depends(require("self:read")),
            db: Session = Depends(get_db)) -> dict:
    if actor.patient_id is None:
        raise Forbidden("not a patient account")
    u = db.get(User, actor.user_id) if actor.user_id else None
    saved = u.ui_language if u is not None and u.ui_language in SUPPORTED_LANGUAGES else None
    c = db.scalar(select(Case).where(Case.patient_id == actor.patient_id).order_by((Case.state == "closed").asc(),
                                                                                   Case.id.desc()).limit(1))
    if c is None:  # a newly registered patient: the account works before any result needs follow-up
        p0 = db.get(Patient, actor.patient_id)
        lang = lang if lang in SUPPORTED_LANGUAGES else (saved or comms.comm_language(p0))
        return {"display_language_saved": saved, "case_id": None, "language": lang, "rtl": lang in RTL_LANGUAGES,
                "languages": SUPPORTED_LANGUAGES, "ui": UI_TEXT[lang],
                "label": "KareShafaa prototype - please check anything important with your care team",
                "patient": {"given_name": p0.given_name, "preferred_language": p0.language}}
    p: Patient = c.patient
    lang = lang if lang in SUPPORTED_LANGUAGES else (saved or comms.comm_language(p))
    prog = programme_text(lang, c)
    coord = db.get(Practitioner, c.owner_id)
    tb = db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == c.id)
                    .order_by(QuestionnaireResponse.id.desc())).first()
    msgs = db.scalars(select(Communication).where(Communication.case_id == c.id, Communication.patient_id == p.id,
                                                  Communication.status == "completed").order_by(Communication.id)).all()
    consults = db.scalars(select(ConsultRequest).where(ConsultRequest.case_id == c.id,
                                                       ConsultRequest.requester_type == "patient")).all()
    variant = "standard"
    lock = pa.status(db, c, actor)
    return {
        "display_language_saved": saved,
        "label": "KareShafaa prototype - please check anything important with your care team", "case_id": c.id, "language": lang, "rtl": lang in RTL_LANGUAGES,
        "languages": SUPPORTED_LANGUAGES, "ui": UI_TEXT[lang],
        "patient": {"given_name": p.given_name, "preferred_language": p.language},
        "interpreter_notice": INTERPRETER_NOTICE.get(p.language) or (
            "This explanation is not yet available in your language. A navigator will call you with an interpreter."
            if prog.get("translation_pending") else None),
        "programme": prog["name"], "meaning": prog["meaning"], "next_step": prog["next_step"],
        "timeframe": TIMEFRAME[lang][c.tier], "urgency": URGENCY[lang][c.tier],
        "reassurance": TEMPLATES[lang]["reassurance"][variant],
        "care_team": {"owner": coord.name.split(" (")[0] if coord and not coord.is_pool else "Care coordination team",
                      "role": (coord.role if coord else "coordinator")},
        "state": c.state, "state_label": STATE_LABELS_I18N[lang].get(c.state, LABELS.get(c.state)),
        "journey": [{**j, "label": STATE_LABELS_I18N[lang].get(j["state"], j["label"])} for j in journey(db, c)],
        # once answers are sent the form is locked; it opens again only after this patient asks to change them
        "can_teach_back": (S(c.state) == S.PATIENT_NOTIFIED and not lock["submitted"]) or lock["reopened"],
        "answers_lock": lock,
        "teach_back": comms.teach_back_form(c, lang),
        "last_teach_back": {"passed": tb.passed, "score": tb.score, "total": tb.total} if tb else None,
        "messages": [{"channel": m.channel, "sent_at": m.sent_at.isoformat(), "content": m.content,
                      "category": m.category} for m in msgs[-10:]],
        "consults": [{"question": q.question, "status": q.status, "response": q.response} for q in consults],
        "can_decline": S(c.state) in (S.PATIENT_NOTIFIED, S.ACKNOWLEDGED, S.CONFIRMATORY_TEST, S.CONSULTATION, S.NO_SHOW),
    }


@router.post("/teach-back")
def my_teach_back(body: sch.TeachBackIn, actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    c = _my_case(db, actor)
    ro = pa.guard_submit(db, c, actor)  # a repeat needs an explicit, recent "Change my answers" by this patient
    qr = comms.submit_teach_back(db, c, actor, dict(body.answers), confidence=body.confidence, language=body.language)
    pa.finalize_submit(db, c, actor, qr, ro)
    db.commit()
    return {"ok": True, "passed": qr.passed, "score": qr.score, "total": qr.total, "state": c.state,
            "changed": ro is not None}


@router.post("/teach-back/reopen")
def my_teach_back_reopen(body: sch.ReopenAnswersIn, actor: Actor = Depends(require("self:respond")),
                         db: Session = Depends(get_db)) -> dict:
    """The patient explicitly asked to change the answers they already sent (confirmed in the page)."""
    c = _my_case(db, actor)
    ro = pa.request_reopen(db, c, actor)
    db.commit()
    return {"ok": True, "reopen_id": ro.id, "expires_at": ro.expires_at.isoformat()}


@router.post("/teach-back/reopen/cancel")
def my_teach_back_reopen_cancel(actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    c = _my_case(db, actor)
    done = pa.cancel_reopen(db, c, actor)
    db.commit()
    return {"ok": True, "cancelled": done}


@router.post("/consult")
def my_consult(body: sch.ConsultIn, actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    c = _my_case(db, actor)
    cr = wf.create_consult(db, c, actor, body.question)
    db.commit()
    return {"ok": True, "consult_id": cr.id}


@router.post("/decline")
def my_decline(body: sch.NoteIn, actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    c = _my_case(db, actor)
    wf.record_decline(db, c, actor, body.note)
    body_text, lang, _ = comms.render_message("decline_ack", comms.comm_language(c.patient), c)
    db.add(Communication(case_id=c.id, patient_id=c.patient_id, category="decline_ack", channel="app", language=lang,
                         template_key="decline_ack", content=comms.SIM_PREFIX + body_text,
                         status="completed", simulated=True))
    db.commit()
    return {"ok": True, "state": c.state}


@router.post("/consent")
def my_consent(body: sch.ConsentIn, actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    p = db.get(Patient, actor.patient_id)
    wf.record_consent(db, p, actor, body.scope, body.decision, body.note)
    db.commit()
    return {"ok": True}


@router.post("/language")
def my_language(body: sch.LanguageIn, actor: Actor = Depends(require("self:respond")), db: Session = Depends(get_db)) -> dict:
    p = db.get(Patient, actor.patient_id)
    old = p.language
    p.language = body.language
    audit.record(db, actor, "U", "patient.language_changed", entity_type="Patient", entity_id=p.id,
                 detail={"from": old, "to": body.language})
    db.commit()
    return {"ok": True, "language": p.language}


@router.post("/display-language")
def my_display_language(body: sch.LanguageIn, actor: Actor = Depends(require("self:read")),
                        db: Session = Depends(get_db)) -> dict:
    """The language the patient chose for this page (kept for their next visit). Messages and calls keep using
    the patient's recorded language and interpreter needs - this only changes what the page shows."""
    u = db.get(User, actor.user_id) if actor.user_id else None
    if u is None:
        raise Forbidden("not a patient account")
    old = u.ui_language
    if old != body.language:
        u.ui_language = body.language
        audit.record(db, actor, "U", "patient.display_language_changed", entity_type="User", entity_id=u.id,
                     detail={"from": old, "to": body.language})
    db.commit()
    return {"ok": True, "language": u.ui_language}
