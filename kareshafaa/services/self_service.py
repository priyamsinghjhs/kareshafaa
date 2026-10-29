"""Patient self-service on the patient app (/patient): own contact details, appointment and result history.

* A patient account can change only its own phone, email, address, emirate / region, preferred channel and
  WhatsApp, with exactly the same validation as the staff form (services/patients.validate_fields). Every change
  is audited as ``patient.self_contact_updated`` and listed on the staff record ("by the patient").
* History is read-only and written for the patient: no clinician notes, no raw lab codes. An actionable result is
  shown as "needs follow-up" only after the care team has told the patient (never before clinician review).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..comms.self_service_text import SELF_TEXT
from ..models import Appointment, Case, DiagnosticReport, Organization, Patient, utcnow
from ..programmes import PROGRAMMES
from ..security import Actor
from ..statemachine import S
from .errors import Forbidden, Invalid
from .patients import ABU_DHABI_REGIONS, EMIRATES, _apply, validate_fields

SELF_KEYS = ("phone", "email", "address_line", "city", "emirate", "region", "preferred_channel", "whatsapp")
_NOT_TOLD = {S.ISSUED.value, S.TRIAGED.value}


def _me(db: Session, actor: Actor) -> Patient:
    if actor.role != "patient" or actor.patient_id is None:
        raise Forbidden("not a patient account")
    p = db.get(Patient, actor.patient_id)
    if p is None:
        raise Forbidden("not a patient account")
    return p


def contact_view(p: Patient) -> dict[str, Any]:
    return {"phone": p.phone, "email": p.email, "address_line": p.address_line, "city": p.city,
            "emirate": p.emirate or ("Abu Dhabi" if p.region in ABU_DHABI_REGIONS else None), "region": p.region,
            "preferred_channel": p.preferred_channel, "whatsapp": bool(p.whatsapp)}


def update_own_contact(db: Session, actor: Actor, data: dict[str, Any], now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    p = _me(db, actor)
    unknown = set(data) - set(SELF_KEYS)
    if unknown:
        raise Invalid(f"fields not editable here: {', '.join(sorted(unknown))}")
    if "emirate" in data and data.get("emirate") and data["emirate"] != "Abu Dhabi":
        data = {**data, "region": None}
    f = validate_fields(db, {**data, "_existing_phone": p.phone if "phone" not in data else None}, partial=True, now=now)
    v = f.values
    if v.get("preferred_channel") == "app" and not p.has_smartphone:
        p.has_smartphone = True  # the person is using the app right now
    changed = _apply(p, v, now)
    if changed:
        p.updated_at, p.updated_by = now, f"{actor.display} (patient)"
        audit.record(db, actor, "U", "patient.self_contact_updated", entity_type="Patient", entity_id=p.id, ts=now,
                     detail={"fields": changed, "by_role": actor.role})
    return changed


def _prog_name(lang: str, programme: str) -> str:
    from ..comms.content import PROGRAMME_TEXT

    t = (PROGRAMME_TEXT.get(lang) or {}).get(programme) or (PROGRAMME_TEXT.get("en") or {}).get(programme) or {}
    return t.get("name") or PROGRAMMES[programme].name


def history(db: Session, actor: Actor, lang: str, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    p = _me(db, actor)
    T = SELF_TEXT[lang]
    cases = {c.id: c for c in db.scalars(select(Case).where(Case.patient_id == p.id))}
    appts = []
    if cases:
        for a in db.scalars(select(Appointment).where(Appointment.case_id.in_(cases)).order_by(Appointment.start.desc()).limit(30)):
            c = cases[a.case_id]
            org = db.get(Organization, a.org_id)
            status = "upcoming" if a.status == "booked" and a.start >= now else ("past" if a.status == "booked" else a.status)
            appts.append({"start": a.start.isoformat(), "place": org.name if org else None,
                          "programme": _prog_name(lang, c.programme), "status": status,
                          "status_text": T["appt_status"].get(status, status)})
    results = []
    for r in db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == p.id)
                        .order_by(DiagnosticReport.issued_at.desc()).limit(30)):
        c = cases.get(r.case_id) if r.case_id else None
        if r.status in ("entered-in-error", "cancelled"):
            continue
        if c is not None and c.state in _NOT_TOLD:
            outcome = "reviewing"
        elif r.category == "confirmatory":
            outcome = "follow_up" if r.conclusion == "positive" else "normal"
        else:
            outcome = {"negative": "normal", "actionable": "follow_up", "needs_review": "reviewing"}.get(r.conclusion, "reviewing")
            if outcome == "reviewing" and c is not None and c.state not in _NOT_TOLD and not c.review_required:
                outcome = "follow_up"
        results.append({"date": r.issued_at.isoformat(),
                        "programme": _prog_name(lang, r.programme),
                        "kind": T["kind"]["confirmatory" if r.category == "confirmatory" else "screening"],
                        "outcome": outcome, "outcome_text": T["outcome"][outcome],
                        "site": r.screening_site})
    return {"appointments": appts, "results": results}
