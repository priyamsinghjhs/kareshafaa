"""Must never happen: patient contact after the patient withdrew consent; a broken or forgeable audit chain."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from tests.safety.invariants import audit_violations, communication_violations

pytestmark = pytest.mark.safety


def _patient(p):
    from kareshafaa.security import Actor
    return Actor(user_id=None, role="patient", display="patient", patient_id=p.id)


def _coordinator(db):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User
    return actor_from_user(db.scalar(select(User).where(User.username == "aisha.rahman")))


def test_no_automated_contact_after_care_coordination_withdrawal(db, new_case):
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import Case, Recall
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import DomainError
    from kareshafaa.services.recall import send_invite

    c = db.get(Case, new_case(value=250, smartphone=True, phone_valid=True, whatsapp=True)["id"])
    wf.record_consent(db, c.patient, _patient(c.patient), "care_coordination", "deny")
    db.commit()
    for cat in ("reminder", "result_notification"):
        with pytest.raises(DomainError):
            send_with_ladder(db, c, cat, SYSTEM)
        db.rollback()
    from datetime import date
    r = Recall(patient_id=c.patient_id, programme=c.programme, due_on=date.today(), status="due", reason="routine",
               created_by="test")
    db.add(r)
    db.flush()
    with pytest.raises(DomainError):
        send_invite(db, _coordinator(db), r, c.patient)
    db.rollback()


@pytest.mark.parametrize("channel", ["sms", "whatsapp", "app"])
def test_no_message_on_a_refused_channel(db, new_case, channel):
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import Case, Communication
    from kareshafaa.services import workflow as wf

    c = db.get(Case, new_case(value=250, smartphone=True, phone_valid=True, whatsapp=True)["id"])
    wf.record_consent(db, c.patient, _patient(c.patient), f"comm_{channel}", "deny")
    db.commit()
    send_with_ladder(db, c, "result_notification", _coordinator(db))
    sent = db.scalars(select(Communication).where(Communication.case_id == c.id, Communication.channel == channel,
                                                  Communication.status.in_(["completed", "failed"]))).all()
    assert sent == []
    db.rollback()


def test_account_code_is_not_texted_to_a_patient_who_refused_sms(db, make_patient):
    from datetime import date
    from kareshafaa.models import Communication, Patient
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import DomainError, Invalid
    from kareshafaa.services.invites import create_invite

    p = db.get(Patient, make_patient(phone_valid=True))
    p.phone, p.birth_date = "0501234567", date(1970, 1, 1)
    wf.record_consent(db, p, _patient(p), "comm_sms", "deny")
    db.commit()
    with pytest.raises((Invalid, DomainError), match="SMS"):
        create_invite(db, _coordinator(db), p, channel="sms", base_url="http://t")
    db.rollback()
    assert db.scalars(select(Communication).where(Communication.patient_id == p.id,
                                                  Communication.channel == "sms")).all() == []


def test_no_communication_anywhere_breaks_consent(db):
    """Global scan of every message ever recorded against the consent history at the time it was sent."""
    assert communication_violations(db) == []


def test_audit_hash_chain_verifies_and_detects_forgery(db):
    from kareshafaa.models import AuditEvent
    assert audit_violations(db) == []
    from sqlalchemy import insert, text
    from sqlalchemy.exc import DatabaseError
    t = AuditEvent.__table__
    last = db.execute(select(t).order_by(t.c.id.desc()).limit(1)).mappings().one()
    with pytest.raises(DatabaseError):  # the database itself refuses edits behind the application's back
        db.execute(text("UPDATE audit_events SET event = event || '.x' WHERE id = :i"), {"i": last["id"]})
    db.rollback()
    try:  # a row slipped in with a made-up hash is detected
        forged = {**last, "id": last["id"] + 1, "prev_hash": last["hash"], "hash": "0" * 64, "event": "forged"}
        db.execute(insert(t).values(**forged))
        v = audit_violations(db)
        assert v and str(last["id"] + 1) in v[0], v
    finally:
        db.rollback()
    assert audit_violations(db) == []
