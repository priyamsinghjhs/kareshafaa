"""Result-to-case reconciliation (screening-to-care principle 6: "nothing fails silently").

Not the audit-trail fallback reconciliation (kareshafaa/audit_fallback.py): this job compares the SOURCE results
with the cases. "Every actionable result has a case" is measured, not assumed:

* every screening DiagnosticReport whose conclusion is actionable or needs review must be linked to a case that
  exists (``result_without_case``);
* every lab message marked processed must have produced a result (``message_without_result``).

Anything found goes to a dead-letter list (``result_reconciliation_items``) owned by care coordination for the
programme and shown in the Results inbox (permission ``results:inbox``). Nothing is fixed silently: a person puts
the result into the loop (a clinician-review case, workflow.open_case_for_orphan_report) or, for a message without a
result, documents what happened. An item whose result is later found linked to a case is closed as ``linked_later``.
Runs with the timed jobs after every deadline check, and on demand (Admin -> System, Results inbox).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import Case, DiagnosticReport, IngestMessage, ResultReconciliationItem as Item, utcnow
from ..security import SYSTEM, Actor
from .errors import Forbidden, Invalid, NotFound

ACTIONABLE = ("actionable", "needs_review")
OWNER_ROLE = "coordinator"
KIND_LABEL = {"result_without_case": "Result needs action but has no case",
              "message_without_result": "Lab message processed but no result was filed"}


def _orphans(db: Session) -> list[tuple[int, str]]:
    q = (select(DiagnosticReport.id, DiagnosticReport.programme)
         .outerjoin(Case, Case.id == DiagnosticReport.case_id)
         .where(DiagnosticReport.category == "screening", DiagnosticReport.conclusion.in_(ACTIONABLE),
                or_(DiagnosticReport.case_id.is_(None), Case.id.is_(None))))
    return [(i, p) for i, p in db.execute(q)]


def _silent_messages(db: Session) -> list[tuple[int, str | None]]:
    q = select(IngestMessage.id, IngestMessage.programme).where(IngestMessage.status == "processed",
                                                                IngestMessage.report_id.is_(None))
    return [(i, p) for i, p in db.execute(q)]


def run(db: Session, actor: Actor = SYSTEM, now: datetime | None = None) -> dict:
    """Detect results missing from the loop. Idempotent: one open item per source row; re-runs update last_seen."""
    now = now or utcnow()
    open_items = {(it.kind, it.report_id or it.ingest_message_id): it
                  for it in db.scalars(select(Item).where(Item.status == "open"))}
    found: set[tuple[str, int]] = set()
    new = 0
    for kind, rows, field in (("result_without_case", _orphans(db), "report_id"),
                              ("message_without_result", _silent_messages(db), "ingest_message_id")):
        for src_id, prog in rows:
            key = (kind, src_id)
            found.add(key)
            it = open_items.get(key)
            if it is None:
                db.add(Item(kind=kind, programme=prog, owner_role=OWNER_ROLE, status="open", detected_at=now,
                            last_seen_at=now, **{field: src_id}))
                new += 1
            else:
                it.last_seen_at = now
    linked = 0
    for key, it in open_items.items():
        if key not in found:  # the source row now has its case / result: closed automatically, recorded
            it.status, it.resolution, it.resolved_at = "resolved", "linked_later", now
            if it.report_id:
                it.resolution_case_id = db.scalar(select(DiagnosticReport.case_id).where(DiagnosticReport.id == it.report_id))
            linked += 1
    db.flush()
    still_open = db.scalar(select(func.count()).select_from(Item).where(Item.status == "open")) or 0
    out = {"checked_results": db.scalar(select(func.count()).select_from(DiagnosticReport).where(
        DiagnosticReport.category == "screening", DiagnosticReport.conclusion.in_(ACTIONABLE))) or 0,
        "new": new, "linked_later": linked, "open": still_open}
    if new or linked:  # counts only (pseudonymous)
        audit.record(db, actor, "E", "reconcile.results", ts=now, detail=out)
    return out


def _row(db: Session, it: Item) -> dict:
    rep = db.get(DiagnosticReport, it.report_id) if it.report_id else None
    msg = db.get(IngestMessage, it.ingest_message_id) if it.ingest_message_id else None
    return {"id": it.id, "kind": it.kind, "label": KIND_LABEL.get(it.kind, it.kind), "programme": it.programme,
            "owner_role": it.owner_role, "status": it.status, "detected_at": it.detected_at.isoformat(),
            "last_seen_at": it.last_seen_at.isoformat(), "report_id": it.report_id,
            "patient_id": rep.patient_id if rep else None, "result_issued_at": rep.issued_at.isoformat() if rep else None,
            "result_display": rep.display if rep else None, "message_id": msg.message_id if msg else None,
            "resolution": it.resolution, "resolution_case_id": it.resolution_case_id,
            "action": "open_case" if it.kind == "result_without_case" else "note"}


def items(db: Session, actor: Actor, status: str = "open") -> list[dict]:
    if not actor.can("results:inbox"):
        raise Forbidden("the reconciliation list belongs to care coordination (Results inbox)")
    q = select(Item).where(Item.status == status).order_by(Item.detected_at, Item.id)
    return [_row(db, it) for it in db.scalars(q.limit(500))]


def resolve(db: Session, item_id: int, actor: Actor, *, action: str, note: str | None = None,
            now: datetime | None = None) -> dict:
    """A person closes a dead-letter item: 'open_case' (result -> clinician-review case) or 'note' (message only)."""
    from . import workflow as wf

    now = now or utcnow()
    if not actor.is_human or not actor.can("results:inbox"):
        raise Forbidden("only care coordination may work the reconciliation list")
    it = db.get(Item, item_id)
    if it is None:
        raise NotFound("item not found")
    if it.status != "open":
        raise Invalid("this item is already resolved")
    if it.kind == "result_without_case":
        if action != "open_case":
            raise Invalid("a result that needs action can only be put into the loop (open a case for review)")
        rep = db.get(DiagnosticReport, it.report_id)
        case = wf.open_case_for_orphan_report(db, rep, actor, now)
        it.resolution, it.resolution_case_id = "case_opened", case.id
    else:
        if action != "note" or not note or len(note.strip()) < 10:
            raise Invalid("say what happened to this message (at least 10 characters)")
        it.resolution, it.resolution_note = "noted", note.strip()
    it.status, it.resolved_at, it.resolved_by_user_id = "resolved", now, actor.user_id
    db.flush()
    audit.record(db, actor, "U", "reconcile.item_resolved", entity_type="ResultReconciliationItem", entity_id=it.id,
                 case_id=it.resolution_case_id, ts=now, detail={"kind": it.kind, "resolution": it.resolution})
    return _row(db, it)


def status(db: Session) -> dict:
    """Counts for monitoring (Admin -> System)."""
    last = db.scalar(select(func.max(Item.last_seen_at)))
    return {"open": db.scalar(select(func.count()).select_from(Item).where(Item.status == "open")) or 0,
            "oldest_open_at": (lambda v: v.isoformat() if v else None)(
                db.scalar(select(func.min(Item.detected_at)).where(Item.status == "open"))),
            "last_item_seen_at": last.isoformat() if last else None}
