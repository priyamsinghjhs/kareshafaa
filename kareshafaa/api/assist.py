"""Draft-text suggestions for staff clinical note fields (kareshafaa/services/assist.py).

POST /api/assist/suggest returns draft TEXT only. It never closes, moves, assigns, files or sends anything; the staff
member inserts, edits or dismisses the draft and then submits the normal form, where every existing check applies.
Patients: 403 (no case:read). No access to the case: the same refusal as every other case route.
Disabled (no API key, or the AI kill switch off) or provider trouble: HTTP 200 with suggestion null.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..security import Actor
from ..services import assist
from ..services import workflow as wf
from ..services.access import assert_can_view
from ..services.errors import Forbidden, Invalid
from .deps import require
from .schemas import _In

router = APIRouter(prefix="/api/assist", tags=["assist"])


class SuggestIn(_In):
    case_id: int = Field(ge=1)
    field: str = Field(default="note", max_length=40)
    action: str = Field(max_length=40)
    evidence_type: str | None = Field(default=None, max_length=40)
    option: str | None = Field(default=None, max_length=40)  # chosen option on the same form (e.g. closure reason)
    consult_id: int | None = Field(default=None, ge=1)
    draft: str = Field(default="", max_length=assist.MAX_DRAFT)


def _out(enabled: bool, reason: str | None = None, suggestion: str | None = None, model: str | None = None,
         unavailable: bool = False) -> dict:
    return {"enabled": enabled, "reason": reason, "suggestion": suggestion, "model": model,
            "unavailable": unavailable, "disclaimer": assist.DISCLAIMER}


@router.post("/suggest")
def suggest(body: SuggestIn, actor: Actor = Depends(require("case:read")), db: Session = Depends(get_db)) -> dict:
    if actor.role == "patient" or not actor.is_human:
        raise Forbidden("draft suggestions are for staff only")
    c = wf.get_case(db, body.case_id)
    assert_can_view(db, actor, c)
    if body.action not in assist.ACTIONS:
        raise Invalid("draft suggestions are not offered for this form")
    if body.field not in assist.FIELDS:
        raise Invalid("draft suggestions are not offered for this field")
    if body.evidence_type and body.evidence_type not in assist.EVIDENCE_TYPES:
        raise Invalid("unknown evidence type")
    reason = assist.availability(db)
    if reason is not None:
        return _out(False, reason)
    s = get_settings()
    if not assist.LIMITER.allow(actor.user_id, int(s.slm_rate_per_minute)):
        return _out(True, "busy", unavailable=True)
    staff = [actor.display or ""]
    if c.owner is not None and not c.owner.is_pool:
        staff.append(c.owner.name)
    ctx = assist.build_context(db, c, action=body.action, field=body.field, evidence_type=body.evidence_type,
                               option=body.option, consult_id=body.consult_id, draft=body.draft, staff_names=staff)
    ids = assist._identifiers(c, staff)
    case_id = c.id
    db.rollback()  # nothing written yet; hand the connection back to the pool during the (slow) outbound call
    res = assist.draft(ctx)
    text = assist.post_check(res.get("suggestion"), body.field, ids) if res.get("suggestion") else None
    # audit: THAT a draft was asked for - never the prompt or the generated text (no copy of clinical text / PHI)
    audit.record(db, actor, "E", "assist.suggestion_requested", entity_type="Case", entity_id=case_id, case_id=case_id,
                 detail={"action": body.action, "field": body.field, "evidence_type": body.evidence_type,
                         "model": s.slm_model, "returned": bool(text), "error": None if text else res.get("error")})
    db.commit()
    return _out(True, None, text, s.slm_model, unavailable=not text)


class CopilotIn(_In):
    case_id: int = Field(ge=1)
    task: str = Field(max_length=40)
    language: str = Field(default="en", max_length=8)
    draft: str = Field(default="", max_length=assist.MAX_DRAFT)


@router.post("/copilot")
def copilot(body: CopilotIn, actor: Actor = Depends(require("case:read")), db: Session = Depends(get_db)) -> dict:
    """Advisory draft only. Never sends, files, moves or closes a case."""
    from ..services import copilot as cop

    if actor.role == "patient" or not actor.is_human:
        raise Forbidden("the copilot is for staff only")
    if body.task not in cop.TASKS:
        raise Invalid("unknown copilot task")
    lang = body.language if body.language in cop.LANGS else "en"
    c = wf.get_case(db, body.case_id)
    assert_can_view(db, actor, c)
    s = get_settings()
    use_model = cop.available_for_model(db)
    if use_model and not cop.assist.LIMITER.allow(actor.user_id, int(s.slm_rate_per_minute)):
        use_model = False
    staff = [actor.display or ""]
    if c.owner is not None and not c.owner.is_pool:
        staff.append(c.owner.name)
    ctx = cop.gather(db, c, task=body.task, language=lang, staff_names=staff)
    case_id = c.id
    sources = list(ctx.get("sources") or [])
    db.rollback()
    res = cop.run(ctx, task=body.task, language=lang, use_model=use_model)
    audit.record(db, actor, "E", "copilot.requested", entity_type="Case", entity_id=case_id, case_id=case_id,
                 detail={"task": body.task, "language": lang, "origin": res.get("origin"),
                         "returned": bool(res.get("text")), "model_used": use_model and res.get("origin") == "model",
                         "error": res.get("error")})
    db.commit()
    return {"text": res.get("text"), "origin": res.get("origin"), "sources": res.get("sources") or sources,
            "disclaimer": cop.DISCLAIMER, "model": (s.slm_model if res.get("origin") == "model" else None),
            "kill_switch_off": not use_model and not cop.available_for_model(db),
            "task": body.task, "language": lang}
