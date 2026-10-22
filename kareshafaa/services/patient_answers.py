"""Patient answer lock: once a patient has sent their teach-back answers from the patient page, the same form
cannot be sent again unless that patient explicitly asks to change them ("Change my answers").

Rules (enforced here, server-side; the patient page only mirrors them):
- first self-submission for a case: allowed (the clinical rules in comms.submit_teach_back are unchanged);
- any later self-submission needs an open reopen request made by the SAME patient login, for the SAME case,
  within REOPEN_WINDOW; it is consumed by exactly one submission (atomic UPDATE, so two parallel posts cannot
  both use it);
- every answer stays in history (one QuestionnaireResponse row per submission - nothing is overwritten) and the
  reopen request, its cancellation and the new submission are written to the hash-chained audit log together
  with the previous answers.
Navigator-recorded teach-backs (staff, mode="navigator") are not affected and do not lock the patient.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from .. import audit
from ..models import AnswerReopen, Case, QuestionnaireResponse, utcnow
from ..security import Actor
from ..statemachine import S
from .errors import DomainError, Forbidden

FORM = "teach_back"
REOPEN_WINDOW = timedelta(minutes=30)
# states in which comms.submit_teach_back accepts a submission (kept in step with it)
SUBMIT_STATES = (S.PATIENT_NOTIFIED, S.DECLINED_REVIEW, S.ACKNOWLEDGED)

LOCKED_MSG = ("your answers are already saved. To change them, choose 'Change my answers' first "
              "(nothing was saved)")


class AnswersLocked(DomainError):
    status_code = 409
    code = "answers_locked"


def _own(actor: Actor, case: Case) -> None:
    if actor.role != "patient" or actor.patient_id is None or actor.patient_id != case.patient_id:
        raise Forbidden("not your case")


def self_responses(db: Session, case: Case) -> list[QuestionnaireResponse]:
    return list(db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == case.id,
                                                               QuestionnaireResponse.mode == "self")
                           .order_by(QuestionnaireResponse.id)))


def active_reopen(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> AnswerReopen | None:
    now = now or utcnow()
    q = select(AnswerReopen).where(AnswerReopen.case_id == case.id, AnswerReopen.patient_id == actor.patient_id,
                                   AnswerReopen.form == FORM, AnswerReopen.used_at.is_(None),
                                   AnswerReopen.cancelled_at.is_(None), AnswerReopen.expires_at > now)
    q = q.where(AnswerReopen.user_id.is_(None) if actor.user_id is None else AnswerReopen.user_id == actor.user_id)
    return db.scalars(q.order_by(AnswerReopen.id.desc()).limit(1)).first()


def _snapshot(qr: QuestionnaireResponse | None) -> dict | None:
    if qr is None:
        return None
    return {"response_id": qr.id, "answers": dict(qr.answers or {}), "confidence": qr.confidence, "score": qr.score,
            "total": qr.total, "passed": qr.passed, "language": qr.language,
            "submitted_at": qr.created_at.isoformat() if qr.created_at else None}


def status(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> dict:
    """What the patient page needs: is the form locked, what was sent and when, can it be changed."""
    now = now or utcnow()
    mine = self_responses(db, case)
    last = mine[-1] if mine else None
    ro = active_reopen(db, case, actor, now) if last else None
    open_states = S(case.state) in SUBMIT_STATES
    return {
        "locked": last is not None and ro is None,
        "submitted": _snapshot(last),
        "submissions": len(mine),
        "changed": len(mine) > 1,
        "can_change": last is not None and ro is None and open_states,
        "reopened": ro is not None and open_states,
        "reopen_expires_at": ro.expires_at.isoformat() if ro else None,
    }


def guard_submit(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> AnswerReopen | None:
    """Before a patient submission: refuse a repeat unless this patient has an open reopen request."""
    _own(actor, case)
    if not self_responses(db, case):
        return None
    ro = active_reopen(db, case, actor, now)
    if ro is None:
        raise AnswersLocked(LOCKED_MSG)
    return ro


def finalize_submit(db: Session, case: Case, actor: Actor, qr: QuestionnaireResponse, ro: AnswerReopen | None,
                    now: datetime | None = None) -> None:
    """After the response row was flushed (the writer lock is now held): re-check under the lock and consume the
    reopen request atomically, so two parallel posts can never both be accepted."""
    now = now or utcnow()
    others = db.scalar(select(func.count()).select_from(QuestionnaireResponse).where(
        QuestionnaireResponse.case_id == case.id, QuestionnaireResponse.mode == "self",
        QuestionnaireResponse.id != qr.id))
    if ro is None:
        if others:
            raise AnswersLocked(LOCKED_MSG)
        return
    n = db.execute(update(AnswerReopen).where(AnswerReopen.id == ro.id, AnswerReopen.used_at.is_(None),
                                              AnswerReopen.cancelled_at.is_(None), AnswerReopen.expires_at > now)
                   .values(used_at=now, used_response_id=qr.id).execution_options(synchronize_session=False)).rowcount
    if n != 1:
        raise AnswersLocked(LOCKED_MSG)
    prev = db.get(QuestionnaireResponse, ro.previous_response_id) if ro.previous_response_id else None
    audit.record(db, actor, "U", "teachback.answers_changed", entity_type="QuestionnaireResponse", entity_id=qr.id,
                 case_id=case.id, ts=now,
                 detail={"reopen_id": ro.id, "previous": _snapshot(prev), "new": _snapshot(qr)})


def request_reopen(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> AnswerReopen:
    """The patient explicitly asked to change their saved answers: unlock the form for one new submission."""
    now = now or utcnow()
    _own(actor, case)
    mine = self_responses(db, case)
    if not mine:
        raise DomainError("there are no saved answers to change")
    if S(case.state) not in SUBMIT_STATES:
        raise DomainError("these answers can no longer be changed online - please talk to your care team")
    ro = active_reopen(db, case, actor, now)
    if ro is not None:
        return ro  # asking twice is harmless
    last = mine[-1]
    ro = AnswerReopen(case_id=case.id, patient_id=case.patient_id, user_id=actor.user_id, form=FORM,
                      previous_response_id=last.id, requested_at=now, expires_at=now + REOPEN_WINDOW)
    db.add(ro)
    db.flush()
    audit.record(db, actor, "U", "teachback.reopen_requested", entity_type="AnswerReopen", entity_id=ro.id,
                 case_id=case.id, ts=now,
                 detail={"previous": _snapshot(last), "expires_at": ro.expires_at.isoformat()})
    return ro


def cancel_reopen(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> bool:
    """The patient changed their mind: keep the saved answers and lock the form again."""
    now = now or utcnow()
    _own(actor, case)
    ro = active_reopen(db, case, actor, now)
    if ro is None:
        return False
    ro.cancelled_at = now
    db.flush()
    audit.record(db, actor, "U", "teachback.reopen_cancelled", entity_type="AnswerReopen", entity_id=ro.id,
                 case_id=case.id, ts=now, detail={"previous_response_id": ro.previous_response_id})
    return True


def changes_for_case(db: Session, case_id: int) -> dict[int, int]:
    """Staff view: new response id -> the previous response id it replaced (patient changed their answers)."""
    rows = db.execute(select(AnswerReopen.used_response_id, AnswerReopen.previous_response_id).where(
        AnswerReopen.case_id == case_id, AnswerReopen.used_response_id.is_not(None))).all()
    return {int(n): int(p) for n, p in rows if p is not None}
