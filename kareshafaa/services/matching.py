"""Fuzzy candidate suggestions for an unmatched lab result.

A person always confirms the match (existing ``ingest.resolve_match``). This module never
links a result to a patient on its own. Candidates are ranked by name (including Arabic
transliteration variants: Mohammed/Muhammad/Mohamed, Al-/El- prefixes — see
``services/name_keys.py``), date of birth, Emirates ID (including a partial / last-4 match)
and MRN closeness. At most three are returned, each with a confidence and a reason.
"""
from __future__ import annotations

import re
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from ..models import IngestMessage, Patient, PatientNameKey
from .name_keys import name_keys
from .patients import _mask_eid, name_similarity

# Below this, a candidate is not shown (too many false friends, especially shared names).
MIN_SCORE = 0.50
MAX_SUGGESTIONS = 3


def _digits(s: str | None) -> str:
    return re.sub(r"\D", "", s or "")


def _parse_date(raw: Any) -> date | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, date) and not isinstance(raw, datetime):
        return raw
    if isinstance(raw, datetime):
        return raw.date()
    s = str(raw).strip()
    for n, fmt in ((8, "%Y%m%d"), (10, "%Y-%m-%d"), (10, "%d/%m/%Y"), (10, "%d-%m-%Y")):
        try:
            return datetime.strptime(s[:n], fmt).date()
        except ValueError:
            continue
    return None


def _ids_of(msg: IngestMessage) -> list[str]:
    vals = [msg.patient_identifier, *(msg.alt_identifiers or [])]
    return [v for v in vals if v and str(v).strip()]


def _demographics(msg: IngestMessage) -> dict:
    return dict(msg.patient_demographics or {})


def _close(a: str | None, b: str | None) -> float:
    """0..1 closeness of two identifiers (MRN / Emirates ID), digit-aware."""
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    da, db = _digits(a), _digits(b)
    if da and da == db:
        return 1.0
    if da and db and (da in db or db in da) and min(len(da), len(db)) >= 6:
        return 0.85
    if da and db and len(da) >= 4 and len(db) >= 4 and da[-4:] == db[-4:]:
        return 0.6
    return SequenceMatcher(None, (a or "").lower(), (b or "").lower()).ratio()


def suggest_for_message(db: Session, msg: IngestMessage, *, limit: int = MAX_SUGGESTIONS) -> list[dict]:
    """Top candidate patients for an unmatched ingest message. Empty when there is nothing useful to go on."""
    if msg is None:
        return []
    demo = _demographics(msg)
    given = (demo.get("given_name") or "").strip()
    family = (demo.get("family_name") or "").strip()
    # some messages put the whole name in one field (patient_name) — split once
    if not given and not family and demo.get("name"):
        parts = str(demo["name"]).split()
        given, family = (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (parts[0] if parts else "", "")
    dob = _parse_date(demo.get("birth_date"))
    year = msg.birth_year or (dob.year if dob else None)
    idents = _ids_of(msg)

    scored: dict[int, dict] = {}

    def add(p: Patient, score: float, reasons: list[str], flags: list[str] | None = None) -> None:
        if p.id in scored and scored[p.id]["score"] >= score:
            # still merge new reasons
            for r in reasons:
                if r not in scored[p.id]["reasons"]:
                    scored[p.id]["reasons"].append(r)
            return
        prev = scored.get(p.id)
        reasons = list(dict.fromkeys((prev["reasons"] if prev else []) + reasons))
        scored[p.id] = {
            "id": p.id,
            "mrn": p.mrn,
            "name": f"{p.given_name} {p.family_name}",
            "birth_date": p.birth_date.isoformat() if p.birth_date else None,
            "birth_year": p.birth_year,
            "emirates_id": _mask_eid(p.national_id),
            "score": round(min(1.0, score), 2),
            "confidence": _confidence(min(1.0, score)),
            "reasons": reasons,
            "flags": list(dict.fromkeys((prev["flags"] if prev else []) + (flags or []))),
        }

    # 1) identifier hits (exact or close MRN / Emirates ID) — may already have been rejected for a birth-year clash
    for ident in idents:
        dig = _digits(ident)
        rows = list(db.scalars(select(Patient).where(or_(
            Patient.mrn == ident,
            Patient.national_id == ident,
            (Patient.national_id.is_not(None) & (func.replace(func.replace(Patient.national_id, "-", ""), " ", "") == dig))
            if dig else False,
        ))))
        for p in rows:
            reasons, flags, score = [], [], 0.95
            if p.mrn == ident or _digits(p.mrn) == dig:
                reasons.append("Same medical record number")
            if p.national_id and (_digits(p.national_id) == dig or p.national_id == ident):
                reasons.append("Same Emirates ID")
            if year is not None and p.birth_year and p.birth_year != year:
                flags.append("birth_year_differs")
                reasons.append(f"Birth year in the message ({year}) differs from the record ({p.birth_year}) — check carefully")
                score = 0.7
            add(p, score, reasons, flags)

        # last-4 of a long identifier, only when we also have a name or a date of birth (otherwise too broad)
        if dig and len(dig) >= 4 and (given or family or dob or year):
            last4 = dig[-4:]
            # dashed Emirates IDs store the last four digits as "NNN-C", so match both forms
            dashed = f"%{last4[:3]}-{last4[3:]}"
            plain = f"%{last4}"
            q = select(Patient).where(or_(
                Patient.national_id.is_not(None) & (Patient.national_id.like(plain) | Patient.national_id.like(dashed)),
                Patient.mrn.like(plain),
            ))
            for p in db.scalars(q.limit(50)):
                if p.id in scored:
                    continue
                close = max(_close(ident, p.mrn), _close(ident, p.national_id))
                if close < 0.6:
                    continue
                # confirm the last-4 on the digit form (LIKE can over-match)
                if _digits(p.national_id)[-4:] != last4 and _digits(p.mrn)[-4:] != last4:
                    continue
                why = "Matching last 4 characters of the identifier"
                add(p, 0.55 + 0.15 * close, [why])

    # 2) name + date of birth (transliteration-aware), via the name-key index — no full table scan
    if given or family:
        keys = sorted(name_keys(given, family))
        name_q = None
        if keys:
            shares = select(PatientNameKey.patient_id).where(PatientNameKey.key.in_(keys))
            name_q = Patient.id.in_(shares)
        candidates = []
        conds = []
        if name_q is not None and dob is not None:
            conds.append(name_q & or_(Patient.birth_date == dob,
                                      (Patient.birth_date.is_(None)) & (Patient.birth_year == dob.year)))
        elif name_q is not None and year is not None:
            conds.append(name_q & (Patient.birth_year == year))
        elif name_q is not None:
            # name only: keep this tight — canonical name keys plus a high similarity cut later
            conds.append(name_q)
        if conds:
            candidates = list(db.scalars(select(Patient).where(or_(*conds)).limit(80)))
        full = f"{given} {family}".strip()
        for p in candidates:
            sim = name_similarity(full, f"{p.given_name} {p.family_name}")
            reasons, score = [], 0.0
            dob_hit = dob is not None and (p.birth_date == dob or
                                           (p.birth_date is None and p.birth_year == dob.year))
            year_hit = (not dob_hit) and year is not None and p.birth_year == year
            if dob_hit and sim >= 0.82:
                score = min(1.0, 0.72 + 0.28 * sim)
                reasons.append("Same date of birth and a similar name")
            elif year_hit and sim >= 0.88:
                score = min(0.9, 0.58 + 0.3 * sim)
                reasons.append("Same birth year and a similar name")
            elif sim >= 0.94 and not dob and year is None:
                score = 0.58 + 0.2 * (sim - 0.94) / 0.06
                reasons.append("Very similar name (date of birth not confirmed)")
            else:
                continue
            if sim >= 0.82:
                # mention the transliteration fold when the raw strings differ
                raw_same = full.lower() == f"{p.given_name} {p.family_name}".lower()
                if not raw_same:
                    reasons.append("Name matched allowing Arabic spelling variants (for example Mohammed/Muhammad, Al-/El-)")
            add(p, score, reasons)

    out = sorted(scored.values(), key=lambda c: (-c["score"], c["id"]))
    out = [c for c in out if c["score"] >= MIN_SCORE][:limit]
    for c in out:
        c["why"] = "; ".join(c["reasons"])
    return out


def _confidence(score: float) -> str:
    if score >= 0.85:
        return "high"
    if score >= 0.65:
        return "medium"
    return "low"


def suggest_for_message_id(db: Session, msg_id: int) -> list[dict]:
    msg = db.get(IngestMessage, msg_id)
    return suggest_for_message(db, msg) if msg is not None else []
