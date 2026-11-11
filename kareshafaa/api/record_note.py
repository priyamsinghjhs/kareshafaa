"""Record-sourced (not AI) note suggestions for case-action forms.

POST /api/assist/record-note fills a note from structured data already on the case. It never
closes, moves or files anything. POST /api/assist/record-note/used records whether the clinician
edited the suggestion before saving. Role permissions match the action; the AI kill switch does
not apply (this is not a model).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import Field
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..security import Actor
from ..services import record_note as rn
from ..services import workflow as wf
from ..services.access import assert_can_view
from ..services.errors import Forbidden, Invalid, NotFound
from .actions import available_actions
from .deps import require
from .schemas import _In

router = APIRouter(prefix="/api/assist", tags=["assist"])


class RecordNoteIn(_In):
    case_id: int = Field(ge=1)
    action: str = Field(max_length=40)
    evidence_type: str | None = Field(default=None, max_length=40)
    option: str | None = Field(default=None, max_length=40)  # e.g. authorised-closure reason
    lang: str = Field(default="en", max_length=8)


class RecordNoteUsedIn(_In):
    case_id: int = Field(ge=1)
    action: str = Field(max_length=40)
    fingerprint: str = Field(min_length=8, max_length=64)
    edited: bool
    facts: list[str] | None = Field(default=None, max_length=40)


def _out(s: rn.Suggestion) -> dict:
    return {
        "suggestion": s.note,
        "sources": s.sources,
        "facts": s.fact_keys,
        "disclaimer": s.disclaimer,
        "kind": s.kind,
        "fingerprint": s.fingerprint,
        "lang": s.lang,
    }


def _assert_may_suggest(db: Session, actor: Actor, case, action: str, evidence_type: str | None) -> None:
    if actor.role == "patient" or not actor.is_human:
        raise Forbidden("record-sourced notes are for staff only")
    assert_can_view(db, actor, case)
    if action not in rn.ACTIONS:
        raise Invalid("record-sourced notes are not offered for this form")
    if evidence_type and evidence_type not in rn.EVIDENCE_TYPES:
        raise Invalid("unknown evidence type")
    if action == "close_evidence" and not evidence_type:
        raise Invalid("an evidence type is required for this form")
    acts = available_actions(db, actor, case)
    matching = [a for a in acts if a["key"] == action]
    if not matching:
        raise Forbidden("this action is not available for your role on this case")
    if action == "close_evidence" and evidence_type:
        if not any(a.get("evidence_type") == evidence_type for a in matching):
            raise Forbidden("this closure evidence is not available on this case")


@router.post("/record-note")
def record_note(body: RecordNoteIn, actor: Actor = Depends(require("case:read")),
                db: Session = Depends(get_db)) -> dict:
    if body.lang not in rn.LANGS:
        raise Invalid("language must be en or ar")
    case = wf.get_case(db, body.case_id)
    _assert_may_suggest(db, actor, case, body.action, body.evidence_type)
    s = rn.suggest(db, case, action=body.action, evidence_type=body.evidence_type,
                   option=body.option, lang=body.lang)
    audit.record(db, actor, "E", "note.suggested", entity_type="Case", entity_id=case.id, case_id=case.id,
                 detail={"phase": "generated", "action": body.action, "evidence_type": body.evidence_type,
                         "option": body.option, "lang": s.lang, "facts": s.fact_keys,
                         "fingerprint": s.fingerprint, "kind": s.kind, "edited": None})
    db.commit()
    return _out(s)


@router.post("/record-note/used")
def record_note_used(body: RecordNoteUsedIn, actor: Actor = Depends(require("case:read")),
                     db: Session = Depends(get_db)) -> dict:
    if actor.role == "patient" or not actor.is_human:
        raise Forbidden("record-sourced notes are for staff only")
    case = wf.get_case(db, body.case_id)
    assert_can_view(db, actor, case)
    facts = [k for k in (body.facts or []) if isinstance(k, str) and k.isidentifier()][:30]
    audit.record(db, actor, "U", "note.suggested", entity_type="Case", entity_id=case.id, case_id=case.id,
                 detail={"phase": "applied", "action": body.action, "fingerprint": body.fingerprint,
                         "edited": bool(body.edited), "facts": facts, "kind": rn.KIND})
    db.commit()
    return {"ok": True, "edited": bool(body.edited)}
