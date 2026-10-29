"""Insurance (payer) details, eligibility warning, next of kin / guardian and proxy consent for a patient.

* Insurance never blocks screening or follow-up: an expired or missing policy only shows a warning so the
  coordinator can sort out payment before a referral or appointment (UAE: Thiqa / Daman / private payers).
* Next of kin is a named contact (name, relationship, phone). "Guardian" marks a legal guardian / person who
  decides for the patient. Proxy consent = the patient agrees that the care team may speak to that person about
  their screening and follow-up. Stored as a Consent row (scope proxy_contact) so it is versioned and audited.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import audit
from ..clinical_rules import INSURANCE_EXPIRY_WARN_DAYS
from ..models import Patient, utcnow
from ..security import Actor
from .access import consent_status
from .errors import Forbidden, Invalid
from .patients import _clean_name, can_view_patient, can_write_patient, normalise_phone

PAYERS = {
    "thiqa": "Thiqa (UAE nationals)", "daman_enhanced": "Daman Enhanced", "daman_basic": "Daman Basic",
    "adnic": "ADNIC", "axa_gig": "GIG / AXA Gulf", "neuron": "NextCare / other TPA", "other": "Other insurer",
    "self_pay": "Self-pay (no insurance)",
}
RELATIONSHIPS = {"spouse": "Spouse", "parent": "Parent", "child": "Son / daughter", "sibling": "Brother / sister",
                 "relative": "Other relative", "friend": "Friend", "carer": "Carer", "legal_guardian": "Legal guardian"}
INSURANCE_KEYS = ("insurance_payer", "insurance_plan", "insurance_member_id", "insurance_valid_to")
CONTACT_PERSON_KEYS = ("nok_name", "nok_relationship", "nok_phone", "nok_is_guardian", "proxy_consent", "proxy_note")
_MEMBER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 ./-]{2,39}$")


def eligibility(p: Patient, today: date | None = None) -> dict[str, Any]:
    """ok | expiring | expired | missing | self_pay, with a short message for the record and the case screen."""
    today = today or utcnow().date()
    payer = p.insurance_payer
    if not payer:
        return {"status": "missing", "warn": True,
                "message": "No insurance recorded: check payer details before booking a referral or appointment."}
    if payer == "self_pay":
        return {"status": "self_pay", "warn": True, "message": "Self-pay: discuss costs before a referral or appointment."}
    if not p.insurance_valid_to:
        return {"status": "missing", "warn": True, "message": "Insurance end date not recorded: confirm the policy is active."}
    days = (p.insurance_valid_to - today).days
    if days < 0:
        return {"status": "expired", "warn": True, "days": days,
                "message": f"Insurance expired on {p.insurance_valid_to.strftime('%d %b %Y')}: confirm cover before booking."}
    if days <= INSURANCE_EXPIRY_WARN_DAYS:
        return {"status": "expiring", "warn": True, "days": days,
                "message": f"Insurance ends on {p.insurance_valid_to.strftime('%d %b %Y')} (in {days} days)."}
    return {"status": "ok", "warn": False, "days": days, "message": "Insurance active."}


def view(db: Session, p: Patient) -> dict[str, Any]:
    return {
        "insurance": {"insurance_payer": p.insurance_payer, "payer_label": PAYERS.get(p.insurance_payer or "", None),
                      "insurance_plan": p.insurance_plan, "insurance_member_id": p.insurance_member_id,
                      "insurance_valid_to": p.insurance_valid_to.isoformat() if p.insurance_valid_to else None,
                      "eligibility": eligibility(p)},
        "contacts": {"nok_name": p.nok_name, "nok_relationship": p.nok_relationship,
                     "relationship_label": RELATIONSHIPS.get(p.nok_relationship or "", None), "nok_phone": p.nok_phone,
                     "nok_is_guardian": bool(p.nok_is_guardian), "proxy_consent": consent_status(db, p.id, "proxy_contact"),
                     "proxy_note": p.proxy_note},
    }


def options() -> dict[str, Any]:
    return {"payers": PAYERS, "relationships": RELATIONSHIPS}


def _date(v: Any) -> date | None:
    if v in (None, ""):
        return None
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError as e:
        raise Invalid("insurance end date is not a valid date") from e


def validate_insurance(data: dict[str, Any], now: datetime) -> dict[str, Any]:
    v: dict[str, Any] = {}
    if "insurance_payer" in data:
        payer = (data.get("insurance_payer") or "").strip() or None
        if payer is not None and payer not in PAYERS:
            raise Invalid("choose the insurer from the list")
        v["insurance_payer"] = payer
    if "insurance_plan" in data:
        plan = (data.get("insurance_plan") or "").strip() or None
        if plan and len(plan) > 80:
            raise Invalid("plan name is too long (80 characters at most)")
        v["insurance_plan"] = plan
    if "insurance_member_id" in data:
        mid = (data.get("insurance_member_id") or "").strip() or None
        if mid and not _MEMBER.match(mid):
            raise Invalid("member / card number: 3-40 letters, digits, spaces, dots, slashes or dashes")
        v["insurance_member_id"] = mid
    if "insurance_valid_to" in data:
        d = _date(data.get("insurance_valid_to"))
        if d is not None and not (date(2000, 1, 1) <= d <= now.date() + timedelta(days=366 * 10)):
            raise Invalid("insurance end date must be between 2000 and 10 years from now")
        v["insurance_valid_to"] = d
    payer = v.get("insurance_payer", data.get("_current_payer"))
    member = v.get("insurance_member_id", data.get("_current_member"))
    if payer and payer != "self_pay" and not member and ("insurance_payer" in v or "insurance_member_id" in v):
        raise Invalid("member / card number is required for an insurer (leave the insurer empty or choose self-pay)")
    if payer == "self_pay":
        v.update({"insurance_plan": None, "insurance_member_id": None, "insurance_valid_to": None})
    return v


def validate_contacts(data: dict[str, Any]) -> dict[str, Any]:
    v: dict[str, Any] = {}
    if "nok_name" in data:
        n = (data.get("nok_name") or "").strip()
        v["nok_name"] = _clean_name(n, "next of kin name") if n else None
    if "nok_relationship" in data:
        r = (data.get("nok_relationship") or "").strip() or None
        if r is not None and r not in RELATIONSHIPS:
            raise Invalid("choose the relationship from the list")
        v["nok_relationship"] = r
    if "nok_phone" in data:
        v["nok_phone"] = normalise_phone(data.get("nok_phone")) if (data.get("nok_phone") or "").strip() else None
    if "nok_is_guardian" in data:
        v["nok_is_guardian"] = bool(data.get("nok_is_guardian"))
    if "proxy_note" in data:
        note = (data.get("proxy_note") or "").strip() or None
        if note and len(note) > 500:
            raise Invalid("proxy consent note is too long (500 characters at most)")
        v["proxy_note"] = note
    if "proxy_consent" in data:
        pc = (data.get("proxy_consent") or "").strip() or None
        if pc not in (None, "permit", "deny"):
            raise Invalid("proxy consent must be permit or deny")
        v["proxy_consent"] = pc
    return v


def update_insurance(db: Session, actor: Actor, p: Patient, data: dict[str, Any], now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    if not can_write_patient(db, actor, p):
        raise Forbidden("your role cannot edit patient details")
    unknown = set(data) - set(INSURANCE_KEYS)
    if unknown:
        raise Invalid(f"fields not editable here: {', '.join(sorted(unknown))}")
    v = validate_insurance({**data, "_current_payer": p.insurance_payer, "_current_member": p.insurance_member_id}, now)
    changed = [k for k, val in v.items() if getattr(p, k) != val]
    for k in changed:
        setattr(p, k, v[k])
    if changed:
        p.updated_at, p.updated_by = now, actor.display
        audit.record(db, actor, "U", "patient.insurance_updated", entity_type="Patient", entity_id=p.id, ts=now,
                     detail={"fields": changed, "eligibility": eligibility(p, now.date())["status"]})
    return changed


def update_contacts(db: Session, actor: Actor, p: Patient, data: dict[str, Any], now: datetime | None = None) -> list[str]:
    now = now or utcnow()
    if not can_write_patient(db, actor, p):
        raise Forbidden("your role cannot edit patient details")
    unknown = set(data) - set(CONTACT_PERSON_KEYS)
    if unknown:
        raise Invalid(f"fields not editable here: {', '.join(sorted(unknown))}")
    v = validate_contacts(data)
    proxy = v.pop("proxy_consent", "__unset__")
    name = v.get("nok_name", p.nok_name)
    phone = v.get("nok_phone", p.nok_phone)
    if v.get("nok_is_guardian", p.nok_is_guardian) and not name:
        raise Invalid("a guardian needs a name")
    effective = proxy if proxy not in ("__unset__", None) else consent_status(db, p.id, "proxy_contact")
    if effective == "permit" and not (name and phone):
        raise Invalid("proxy consent needs the next of kin's name and phone number (record 'No' first to remove them)")
    changed = [k for k, val in v.items() if getattr(p, k) != val]
    for k in changed:
        setattr(p, k, v[k])
    if proxy != "__unset__" and proxy is not None and proxy != consent_status(db, p.id, "proxy_contact"):
        from .workflow import record_consent

        record_consent(db, p, actor, "proxy_contact", proxy, v.get("proxy_note") or p.proxy_note, now=now)
        changed.append("proxy_consent")
    if changed:
        p.updated_at, p.updated_by = now, actor.display
        audit.record(db, actor, "U", "patient.contacts_updated", entity_type="Patient", entity_id=p.id, ts=now,
                     detail={"fields": changed})
    return changed


def case_banner(db: Session, actor: Actor, p: Patient) -> dict[str, Any] | None:
    """Short coverage / contact summary for the case screen (staff who can see the patient)."""
    if not can_view_patient(db, actor, p):
        return None
    e = eligibility(p)
    return {"eligibility": e, "payer": PAYERS.get(p.insurance_payer or "", None),
            "nok": ({"name": p.nok_name, "relationship": RELATIONSHIPS.get(p.nok_relationship or "", p.nok_relationship),
                     "phone": p.nok_phone, "guardian": bool(p.nok_is_guardian),
                     "proxy_consent": consent_status(db, p.id, "proxy_contact")} if p.nok_name else None)}
