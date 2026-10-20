"""Case-level access control (RBAC + organisational relationship + consent)."""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Appointment, Case, Consent, ConsultRequest, Practitioner, ServiceRequest
from ..security import PROGRAMME_WIDE_ROLES, Actor
from .errors import Forbidden


def consent_status(db: Session, patient_id: int, scope: str, case_id: int | None = None) -> str | None:
    """Latest active decision ('permit' | 'deny') for a scope, or None if never recorded."""
    q = select(Consent).where(Consent.patient_id == patient_id, Consent.scope == scope, Consent.status == "active")
    if case_id is not None:
        q = q.where((Consent.case_id == case_id) | (Consent.case_id.is_(None)))
    row = db.scalar(q.order_by(Consent.id.desc()).limit(1))
    return row.decision if row else None


def has_consent(db: Session, patient_id: int, scope: str, default: bool = True) -> bool:
    d = consent_status(db, patient_id, scope)
    return default if d is None else d == "permit"


def involved_org_ids(db: Session, case: Case) -> set[int]:
    orgs = {case.managing_org_id, case.owner.org_id}
    orgs |= set(db.scalars(select(ServiceRequest.performer_org_id).where(ServiceRequest.case_id == case.id)))
    orgs |= set(db.scalars(select(Appointment.org_id).where(Appointment.case_id == case.id)))
    tgt = db.scalars(select(Practitioner.org_id).join(ConsultRequest, ConsultRequest.target_practitioner_id == Practitioner.id)
                     .where(ConsultRequest.case_id == case.id))
    orgs |= set(tgt)
    if case.transfer_pending_owner_id:
        p = db.get(Practitioner, case.transfer_pending_owner_id)
        if p:
            orgs.add(p.org_id)
    return orgs


def can_view_case(db: Session, actor: Actor, case: Case) -> bool:
    if actor.role == "patient":
        return actor.patient_id == case.patient_id
    if not actor.can("case:read"):
        return False
    if actor.role in PROGRAMME_WIDE_ROLES:
        return True
    return actor.org_id in involved_org_ids(db, case)


def assert_can_view(db: Session, actor: Actor, case: Case) -> None:
    if not can_view_case(db, actor, case):
        raise Forbidden("you do not have access to this case")
    # Consent: organisations other than the patient's managing organisation and the screening
    # programme need the patient's permission to share with other providers.
    if actor.role not in ("patient",) and actor.role not in PROGRAMME_WIDE_ROLES \
            and actor.org_id != case.patient.managing_org_id \
            and consent_status(db, case.patient_id, "share_external") == "deny":
        raise Forbidden("patient has not consented to sharing with other organisations")
