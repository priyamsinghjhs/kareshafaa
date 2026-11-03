"""Patient registration, search and the patient record (staff). Server-side RBAC: kareshafaa/services/patients.py."""
from __future__ import annotations

import re

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session

from .. import audit
from ..db import get_db
from ..models import IngestMessage
from ..security import Actor
from ..services import patients as svc
from ..services.errors import Invalid, NotFound
from . import schemas as sch
from .deps import require

router = APIRouter(prefix="/api/patients", tags=["patients"])


@router.get("")
def search(q: str = Query("", max_length=80), actor: Actor = Depends(require("patient:read")),
           db: Session = Depends(get_db)) -> dict:
    items = svc.search_patients(db, actor, q)
    return {"items": items, "count": len(items)}


@router.get("/options")
def options(actor: Actor = Depends(require("patient:read")), db: Session = Depends(get_db)) -> dict:
    return svc.options(db)


@router.get("/prefill")
def prefill(message_id: int, actor: Actor = Depends(require("patient:register")), db: Session = Depends(get_db)) -> dict:
    """Registration form values taken from an unmatched result message (the person still checks every field)."""
    msg = db.get(IngestMessage, message_id)
    if msg is None:
        raise NotFound("message not found")
    if msg.status != "needs_match_review":
        raise Invalid("this result is no longer waiting for a patient match")
    vals = dict(msg.patient_demographics or {})
    ids = [msg.patient_identifier, *(msg.alt_identifiers or [])]
    for i in ids:
        if not i:
            continue
        try:
            eid = svc.normalise_emirates_id(i)
        except Invalid:
            eid = None
        if eid:
            vals.setdefault("national_id", eid)
            vals.setdefault("id_type", "emirates_id")
            vals.setdefault("id_number", eid)
        elif re.fullmatch(r"[A-Za-z0-9-]{4,40}", i) and not i.upper().startswith("SYN-"):
            vals.setdefault("mrn", i.upper())
    if msg.birth_year and not vals.get("birth_date"):
        vals["birth_year"] = msg.birth_year
    return {"message_id": msg.id, "programme": msg.programme, "values": vals,
            "identifier": msg.patient_identifier}


@router.post("")
def register(body: sch.PatientCreateIn, actor: Actor = Depends(require("patient:register")),
             db: Session = Depends(get_db)) -> dict:
    from ..models import utcnow
    from ..services import coverage

    cov_keys = set(coverage.INSURANCE_KEYS) | set(coverage.CONTACT_PERSON_KEYS)
    data = body.model_dump(exclude={"create_anyway_reason", "from_message_id", "consent_care_coordination",
                                    "consent_note", *cov_keys})
    given = body.model_dump(include=cov_keys)
    ins = {k: v for k, v in given.items() if k in coverage.INSURANCE_KEYS and v not in (None, "")}
    kin = {k: v for k, v in given.items() if k in coverage.CONTACT_PERSON_KEYS and v not in (None, "", False)}
    coverage.validate_insurance(ins, utcnow())  # fail before anything is written, with the same messages as the record
    coverage.validate_contacts(kin)
    msg = None
    if body.from_message_id is not None:
        msg = db.get(IngestMessage, body.from_message_id)
        if msg is None or msg.status != "needs_match_review":
            raise Invalid("the result message is not waiting for a patient match any more")
    p, warnings = svc.register_patient(db, actor, data, create_anyway_reason=body.create_anyway_reason,
                                       source="result_import" if msg else "staff_form")
    if body.consent_care_coordination:
        from ..services.workflow import record_consent
        note = (body.consent_note or "").strip() or "Recorded at registration (patient asked in person)"
        if len(note) < 10:
            note = f"Recorded at registration: {note}"
        record_consent(db, p, actor, "care_coordination", body.consent_care_coordination, note)
    if ins:
        coverage.update_insurance(db, actor, p, ins)
    if kin:
        coverage.update_contacts(db, actor, p, kin)
    out: dict = {"ok": True, "patient_id": p.id, "mrn": p.mrn, "warnings": warnings}
    if msg is not None:
        from ..services.ingest import resolve_match
        m = resolve_match(db, msg.id, p.id, actor)
        out["case_id"] = m.case_id
        out["report_id"] = m.report_id
    db.commit()
    return out


@router.get("/{patient_id}")
def record(patient_id: int, actor: Actor = Depends(require("patient:read")), db: Session = Depends(get_db)) -> dict:
    p = svc.get_patient(db, actor, patient_id)
    return svc.build_patient_record(db, actor, p)


def _update(db: Session, actor: Actor, patient_id: int, section: str, body) -> dict:
    p = svc.get_patient(db, actor, patient_id)
    changed, warnings = svc.update_patient(db, actor, p, body.model_dump(exclude_unset=True), section=section)
    db.commit()
    return {"ok": True, "changed": changed, "warnings": warnings}


@router.post("/{patient_id}/demographics")
def edit_demographics(patient_id: int, body: sch.PatientDemographicsIn, actor: Actor = Depends(require("patient:write")),
                      db: Session = Depends(get_db)) -> dict:
    return _update(db, actor, patient_id, "demographics", body)


@router.post("/{patient_id}/contact")
def edit_contact(patient_id: int, body: sch.PatientContactIn, actor: Actor = Depends(require("patient:write")),
                 db: Session = Depends(get_db)) -> dict:
    return _update(db, actor, patient_id, "contact", body)


@router.post("/{patient_id}/care")
def edit_care(patient_id: int, body: sch.PatientCareIn, actor: Actor = Depends(require("patient:write")),
              db: Session = Depends(get_db)) -> dict:
    return _update(db, actor, patient_id, "care", body)


@router.post("/{patient_id}/insurance")
def edit_insurance(patient_id: int, body: sch.PatientInsuranceIn, actor: Actor = Depends(require("patient:write")),
                   db: Session = Depends(get_db)) -> dict:
    from ..services import coverage

    p = svc.get_patient(db, actor, patient_id)
    changed = coverage.update_insurance(db, actor, p, body.model_dump(exclude_unset=True))
    db.commit()
    return {"ok": True, "changed": changed, "warnings": [], "eligibility": coverage.eligibility(p)}


@router.post("/{patient_id}/contacts")
def edit_contacts(patient_id: int, body: sch.PatientContactsIn, actor: Actor = Depends(require("patient:write")),
                  db: Session = Depends(get_db)) -> dict:
    from ..services import coverage

    p = svc.get_patient(db, actor, patient_id)
    changed = coverage.update_contacts(db, actor, p, body.model_dump(exclude_unset=True))
    db.commit()
    return {"ok": True, "changed": changed, "warnings": []}


# ------------------------------------------------------------------------------------- patient app account
def base_url(request: Request) -> str:
    from ..config import get_settings

    s = get_settings()
    if s.public_base_url:
        return s.public_base_url.rstrip("/")
    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme).split(",")[0].strip()
    return f"{proto}://{request.url.netloc}"


@router.post("/{patient_id}/account-invite")
def account_invite(patient_id: int, body: sch.AccountInviteIn, request: Request,
                   actor: Actor = Depends(require("patient:invite")), db: Session = Depends(get_db)) -> dict:
    """Create the patient's app account (if needed) and a one-time activation code. The code is returned ONCE."""
    from ..services import invites

    p = svc.get_patient(db, actor, patient_id)
    new = invites.create_invite(db, actor, p, channel=body.channel, base_url=base_url(request),
                                verify_method=body.verify_method)
    db.commit()
    return {"ok": True, "username": new.user.username, "code": invites.fmt_code(new.code), "link": new.link,
            "expires_at": new.invite.expires_at.isoformat(), "purpose": new.invite.purpose, "channel": new.invite.channel,
            "sms": new.sms.content if new.sms else None, "account": invites.account_status(db, p)}


@router.post("/{patient_id}/account-invite/revoke")
def account_invite_revoke(patient_id: int, body: sch.ReasonIn, actor: Actor = Depends(require("patient:invite")),
                          db: Session = Depends(get_db)) -> dict:
    from ..services import invites

    p = svc.get_patient(db, actor, patient_id)
    invites.revoke_invite(db, actor, p, body.reason)
    db.commit()
    return {"ok": True, "account": invites.account_status(db, p)}
