"""Governance of patient-facing content: reading-level checks + explicit human sign-off per exact wording.

Every patient-facing string has a content id (group:key:lang) and a sha256 of its exact current wording.
A sign-off (ContentApproval) is bound to that hash, so ANY edit to the wording silently invalidates it.

* Clinical sign-off (permission content:approve, clinical lead): the wording is clinically correct, kind and
  plain. Refused if the reading-level / tone checks fail.
* Translation sign-off (permission content:translation_review, translator): the Arabic/Hindi/Urdu wording says the
  same as the clinically approved English. Required for every non-English item.
* Separation of duties: one person cannot give both sign-offs for the same wording.

Gated today: the result-specific pathway texts (PATHWAY_TEXT). Older prototype content is listed with its
readability and sign-off status but is not yet gated; it must be signed off before any pilot (docs/patient_messages.md).
"""
from __future__ import annotations

import hashlib
import json

from sqlalchemy import select
from sqlalchemy.orm import Session, object_session

from .. import audit
from ..models import ContentApproval
from ..security import Actor
from ..services.errors import DomainError, Forbidden, Invalid
from . import content as C
from .readability import measure

GATED_GROUPS = {"pathway"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def catalogue() -> dict[str, dict]:
    """content_id -> {group, key, lang, text, measured, sha256, gated}"""
    items: dict[str, dict] = {}

    def add(group, key, lang, text, measured, **kw):
        cid = f"{group}:{key}:{lang}"
        items[cid] = {"content_id": cid, "group": group, "key": key, "lang": lang, "text": text, "measured": measured,
                      "sha256": _sha(text), "gated": group in GATED_GROUPS, "opts": kw}

    for lang, d in C.PATHWAY_TEXT.items():
        for pw, t in d.items():
            add("pathway", pw, lang, json.dumps(t, ensure_ascii=False, sort_keys=True), t["meaning"], result_message=True)
    for lang, d in C.PROGRAMME_TEXT.items():
        for prog, t in d.items():
            add("programme", prog, lang, json.dumps(t, ensure_ascii=False, sort_keys=True), t["meaning"], result_message=True)
    for lang, d in C.DIRECT_PATHWAY_TEXT.items():
        for prog, t in d.items():
            add("direct", prog, lang, json.dumps(t, ensure_ascii=False, sort_keys=True), t["meaning"], result_message=True)
    for lang, d in C.TEMPLATES.items():
        for cat, vs in d.items():
            for v, t in vs.items():
                add("template", f"{cat}.{v}", lang, t, t, simplified=v == "simplified")
    for lang, tb in C.TEACH_BACK.items():
        for k in ("intro", "passed", "failed"):
            add("teach_back", k, lang, tb[k], tb[k])
    add("pathway_fallback", "neutral", "en", json.dumps(C.NEUTRAL_PATHWAY_TEXT, sort_keys=True),
        C.NEUTRAL_PATHWAY_TEXT["meaning"])
    return items


def _approvals(db: Session) -> dict[tuple[str, str, str], ContentApproval]:
    out = {}
    for a in db.scalars(select(ContentApproval).order_by(ContentApproval.id)):
        out[(a.content_id, a.sha256, a.kind)] = a
    return out


def _is_live(item: dict, appr: dict) -> bool:
    cid, sha = item["content_id"], item["sha256"]
    if (cid, sha, "clinical") not in appr:
        return False
    if item["lang"] != "en":
        tr = appr.get((cid, sha, "translation"))
        if tr is None or tr.user_id == appr[(cid, sha, "clinical")].user_id:
            return False
        # the English source it translates must itself be signed off in its current wording
        src = catalogue_cached().get(f"{item['group']}:{item['key']}:en")
        if src is not None and (src["content_id"], src["sha256"], "clinical") not in appr:
            return False
    return True


_CAT: dict | None = None


def catalogue_cached() -> dict[str, dict]:
    global _CAT
    if _CAT is None:
        _CAT = catalogue()
    return _CAT


def live_ids(db: Session) -> set[str]:
    appr = _approvals(db)
    return {cid for cid, it in catalogue_cached().items() if _is_live(it, appr)}


def live_content(case) -> set[str]:
    """Live (fully signed-off) content ids, cached per DB session; empty set if the case is not session-bound."""
    db = object_session(case)
    if db is None:
        return set()
    cached = db.info.get("content_live")
    if cached is None:
        cached = db.info["content_live"] = live_ids(db)
    return cached


def status(db: Session) -> list[dict]:
    appr = _approvals(db)
    rows = []
    for cid, it in catalogue_cached().items():
        r = measure(it["measured"], it["lang"], **it["opts"])
        cl = appr.get((cid, it["sha256"], "clinical"))
        tr = appr.get((cid, it["sha256"], "translation"))
        rows.append({"content_id": cid, "group": it["group"], "key": it["key"], "lang": it["lang"],
                     "gated": it["gated"], "sha256": it["sha256"][:12], "text": it["text"],
                     "readability": r.as_dict(),
                     "clinical_signoff": {"by": cl.display, "at": cl.approved_at.isoformat(), "note": cl.note} if cl else None,
                     "translation_signoff": {"by": tr.display, "at": tr.approved_at.isoformat(), "note": tr.note} if tr else None,
                     "needs_translation_review": it["lang"] != "en",
                     "translation_status": C.TRANSLATION_STATUS.get(it["lang"]) if it["group"] == "pathway" and not tr else None,
                     "live": _is_live(it, appr)})
    return rows


def approve(db: Session, actor: Actor, content_id: str, kind: str, note: str) -> ContentApproval:
    if not actor.is_human or actor.user_id is None:
        raise Forbidden("content sign-off must be given by a named person")
    if kind not in ("clinical", "translation"):
        raise Invalid("kind must be 'clinical' or 'translation'")
    perm = "content:approve" if kind == "clinical" else "content:translation_review"
    if not actor.can(perm):
        raise Forbidden(f"role lacks {perm}")
    if not note or len(note.strip()) < 5:
        raise Invalid("a sign-off note (what was checked) is required")
    from ..signoff import require_signoff  # noqa: PLC0415

    require_signoff(actor, "content.signoff", note)
    it = catalogue_cached().get(content_id)
    if it is None:
        raise Invalid(f"unknown content id {content_id}")
    if kind == "translation" and it["lang"] == "en":
        raise Invalid("English source text has no translation review")
    r = measure(it["measured"], it["lang"], **it["opts"])
    if kind == "clinical" and not r.ok:
        raise DomainError("reading-level / tone checks failed - simplify the wording first: " + "; ".join(r.problems))
    appr = _approvals(db)
    if (content_id, it["sha256"], kind) in appr:
        raise DomainError("this exact wording already has that sign-off")
    other = appr.get((content_id, it["sha256"], "translation" if kind == "clinical" else "clinical"))
    if other is not None and other.user_id == actor.user_id:
        raise DomainError("separation of duties: the same person cannot give both the clinical and translation sign-off")
    a = ContentApproval(content_id=content_id, sha256=it["sha256"], kind=kind, user_id=actor.user_id,
                        display=actor.display, role=actor.role, note=note.strip(), readability=r.as_dict())
    db.add(a)
    db.flush()
    db.info.pop("content_live", None)
    audit.record(db, actor, "U", f"content.signoff.{kind}", entity_type="Content", entity_id=content_id,
                 detail={"sha256": it["sha256"], "note": note.strip(), "readability_ok": r.ok})
    return a
