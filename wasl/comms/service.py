"""Patient communication: templating, channel fallback ladder, teach-back.

ALL SENDING IS SIMULATED. There is no network code in this module and no gateway credentials
exist anywhere in the prototype; messages are rendered and stored as FHIR Communication rows
marked simulated=True.
"""
from __future__ import annotations

import hashlib
import random
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import CallTask, Case, Communication, Patient, QuestionnaireResponse, utcnow
from ..security import SYSTEM, Actor
from ..services.access import has_consent
from ..services.configstore import channel_order, template_variant
from ..services.errors import DomainError, Forbidden, Invalid
from ..statemachine import S
from .content import PROGRAMME_TEXT, programme_text, SUPPORTED_LANGUAGES, TEACH_BACK, TEACH_BACK_CORRECT, TEMPLATES, TIMEFRAME

SIM_PREFIX = "[SIMULATED - not sent] "


def comm_language(patient: Patient, override: str | None = None) -> str:
    lang = override or patient.language
    return lang if lang in SUPPORTED_LANGUAGES else "en"


def render(category: str, lang: str, case: Case, *, variant: str = "standard", date: str | None = None) -> str:
    lang = lang if lang in TEMPLATES else "en"
    tmpl = TEMPLATES[lang][category][variant]
    prog = programme_text(lang, case)
    return tmpl.format(name=case.patient.given_name, programme=prog["name"], meaning=prog["meaning"],
                       next_step=prog["next_step"], timeframe=TIMEFRAME[lang][case.tier],
                       link=f"https://wasl.example/p/{_link_token(case)}", date=date or "")


def _link_token(case: Case) -> str:
    # opaque, non-identifying demo token (a real system would issue a signed, expiring link)
    return hashlib.sha256(f"case-{case.id}-{case.patient.mrn}".encode()).hexdigest()[:12]


class SimulatedGateway:
    """Deterministic simulation of delivery outcomes from the synthetic patient's channel profile."""

    def deliver(self, patient: Patient, channel: str, case_id: int, attempt: int) -> bool:
        rng = random.Random(f"{case_id}:{channel}:{attempt}:{patient.id}")
        if channel == "app":
            return patient.has_smartphone and rng.random() < 0.55 + 0.4 * patient.responsiveness
        if channel == "sms":
            return patient.phone_valid and rng.random() < 0.85
        if channel == "whatsapp":
            return patient.phone_valid and patient.whatsapp and rng.random() < 0.9
        return False


GATEWAY = SimulatedGateway()


def _attempt_no(db: Session, case: Case, category: str) -> int:
    return (db.scalar(select(func.count()).select_from(Communication).where(
        Communication.case_id == case.id, Communication.category == category)) or 0) + 1


def send_with_ladder(db: Session, case: Case, category: str, actor: Actor = SYSTEM, *, now: datetime | None = None,
                     date: str | None = None, max_channels: int | None = None) -> dict:
    """Try channels in order (app link -> SMS -> WhatsApp -> phone-call task), honouring consent."""
    now = now or utcnow()
    p = case.patient
    if p.deceased:
        raise DomainError("patient is recorded as deceased: all patient messaging is stopped")
    if actor.is_human and not actor.can("comm:send"):
        raise Forbidden("role lacks comm:send")
    lang = comm_language(p)
    variant = template_variant(db, category, lang)
    body = render(category, lang, case, variant=variant, date=date)
    attempts: list[dict] = []
    delivered = None
    order = channel_order(db, p.language)
    if max_channels:
        order = order[:max_channels]
    for ch in order:
        if ch == "phone":
            task = _ensure_call_task(db, case, actor, now)
            attempts.append({"channel": "phone", "status": "call_task", "call_task_id": task.id})
            break
        attempt = _attempt_no(db, case, category)
        if not has_consent(db, p.id, f"comm_{ch}"):
            com = Communication(case_id=case.id, patient_id=p.id, category=category, channel=ch, language=lang,
                                template_key=category, template_variant=variant, content=SIM_PREFIX + "(not sent: no consent)",
                                status="not-done", simulated=True, attempt_no=attempt, sent_at=now)
            db.add(com)
            attempts.append({"channel": ch, "status": "not-done (no consent)"})
            continue
        ok = GATEWAY.deliver(p, ch, case.id, attempt)
        com = Communication(case_id=case.id, patient_id=p.id, category=category, channel=ch, language=lang,
                            template_key=category, template_variant=variant, content=SIM_PREFIX + body,
                            status="completed" if ok else "failed", simulated=True, attempt_no=attempt, sent_at=now)
        db.add(com)
        db.flush()
        attempts.append({"channel": ch, "status": com.status, "communication_id": com.id})
        if ok:
            delivered = ch
            break
    db.flush()
    audit.record(db, actor, "C", f"communication.{category}", entity_type="Communication", case_id=case.id, ts=now,
                 detail={"attempts": attempts, "delivered": delivered, "language": lang, "variant": variant,
                         "simulated": True})
    if category == "result_notification" and delivered and S(case.state) in (S.TRIAGED, S.UNREACHABLE):
        from ..services.workflow import transition

        transition(db, case, S.PATIENT_NOTIFIED, SYSTEM, now=now, note=f"delivery confirmed via {delivered} (simulated)")
    return {"delivered_channel": delivered, "attempts": attempts, "language": lang, "variant": variant}


def _ensure_call_task(db: Session, case: Case, actor: Actor, now: datetime) -> CallTask:
    open_task = db.scalar(select(CallTask).where(CallTask.case_id == case.id, CallTask.status == "requested"))
    if open_task:
        return open_task
    from ..services.workflow import programme_coordinator

    t = CallTask(case_id=case.id, owner_id=programme_coordinator(db, case.programme).id, created_at=now)
    db.add(t)
    db.flush()
    audit.record(db, actor, "C", "calltask.created", entity_type="Task", entity_id=f"call-{t.id}", case_id=case.id, ts=now)
    return t


def record_call(db: Session, case: Case, actor: Actor, outcome: str, *, note: str | None = None,
                now: datetime | None = None) -> CallTask:
    """Navigator records a phone call outcome. 'reached' counts as delivery of the result notification."""
    now = now or utcnow()
    if not actor.can("call:record"):
        raise Forbidden("role lacks call:record")
    if outcome not in ("reached", "no_answer", "wrong_number"):
        raise Invalid("outcome must be reached, no_answer or wrong_number")
    task = db.scalar(select(CallTask).where(CallTask.case_id == case.id, CallTask.status == "requested"))
    if task is None:
        task = CallTask(case_id=case.id, owner_id=actor.practitioner_id or case.owner_id, created_at=now)
        db.add(task)
    task.status = "completed"
    task.outcome = outcome
    task.completed_at = now
    lang = comm_language(case.patient)
    com = Communication(case_id=case.id, patient_id=case.patient_id, category="result_notification", channel="phone",
                        language=lang, template_key="call_script", content=SIM_PREFIX + f"Phone call: {outcome}. {note or ''}",
                        status="completed" if outcome == "reached" else "failed", simulated=True,
                        attempt_no=_attempt_no(db, case, "result_notification"), sent_at=now)
    db.add(com)
    db.flush()
    audit.record(db, actor, "U", "calltask.completed", entity_type="Task", entity_id=f"call-{task.id}",
                 case_id=case.id, ts=now, detail={"outcome": outcome})
    if outcome == "reached" and S(case.state) in (S.TRIAGED, S.UNREACHABLE):
        from ..services.workflow import transition

        transition(db, case, S.PATIENT_NOTIFIED, actor, now=now, note="patient reached by phone")
    elif outcome != "reached":
        from ..services.workflow import add_lesson

        add_lesson(db, case, "unreachable", "contact_details_wrong" if outcome == "wrong_number" else "patient_not_responding",
                   actor, channel="phone", now=now)
    return task


def teach_back_form(case: Case, lang: str) -> dict:
    lang = lang if lang in TEACH_BACK else "en"
    tb = TEACH_BACK[lang]
    prog = programme_text(lang, case)
    fill = {"next_step": prog["next_step"], "timeframe": TIMEFRAME[lang][case.tier]}
    qs = []
    for q in ("q1", "q2", "q3"):
        qs.append({"id": q, "text": tb[q]["text"],
                   "options": [{"key": k, "text": v.format(**fill)} for k, v in tb[q]["options"].items()]})
    return {"language": lang, "title": tb["title"], "intro": tb["intro"], "questions": qs,
            "confidence": tb["confidence"], "labels": {k: tb[k] for k in ("submit", "understand", "talk", "decline",
                                                                           "passed", "failed")}}


def submit_teach_back(db: Session, case: Case, actor: Actor, answers: dict[str, str], *, confidence: int | None,
                      language: str, mode: str = "self", now: datetime | None = None) -> QuestionnaireResponse:
    now = now or utcnow()
    if actor.role == "patient":
        if actor.patient_id != case.patient_id:
            raise Forbidden("not your case")
        mode = "self"
    elif not actor.can("call:record"):
        raise Forbidden("role cannot record a navigator teach-back")
    else:
        mode = "navigator"
    if S(case.state) not in (S.PATIENT_NOTIFIED, S.DECLINED_REVIEW, S.ACKNOWLEDGED):
        raise DomainError("teach-back is available after the patient has been notified")
    if set(answers) - set(TEACH_BACK_CORRECT) or not answers:
        raise Invalid("answers must contain q1..q3")
    if confidence is not None and not 1 <= confidence <= 5:
        raise Invalid("confidence must be between 1 and 5")
    score = sum(1 for q, a in TEACH_BACK_CORRECT.items() if answers.get(q) == a)
    total = len(TEACH_BACK_CORRECT)
    qr = QuestionnaireResponse(case_id=case.id, language=language, answers=answers, score=score, total=total,
                               passed=score == total, confidence=confidence, mode=mode, created_at=now)
    db.add(qr)
    db.flush()
    audit.record(db, actor, "C", "teachback.submitted", entity_type="QuestionnaireResponse", entity_id=qr.id,
                 case_id=case.id, ts=now, detail={"score": score, "passed": qr.passed, "mode": mode})
    from ..services.workflow import add_lesson, transition

    if qr.passed and S(case.state) == S.PATIENT_NOTIFIED:
        transition(db, case, S.ACKNOWLEDGED, actor, now=now, note=f"teach-back passed ({mode})")
    elif not qr.passed:
        _ensure_call_task(db, case, SYSTEM, now)
        variant = template_variant(db, "result_notification", comm_language(case.patient))
        add_lesson(db, case, "teach_back_failed", f"teach_back_failed:{variant}", actor,
                   detail=f"score {score}/{total} language={language}", now=now)
    return qr
