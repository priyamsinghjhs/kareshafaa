"""Every patient and user gets an e-mail: empty rows receive a unique @example.com placeholder (never delivered)."""
from __future__ import annotations

from sqlalchemy import func, or_, select

from kareshafaa.comms import mail
from kareshafaa.models import AuditEvent, Patient, User
from kareshafaa.services import email_backfill as eb
from kareshafaa.synth.test_mailboxes import PATIENT_MAILBOXES, STAFF_MAILBOXES


def _empty_email(model):
    return or_(model.email.is_(None), func.trim(model.email) == "")


def test_seed_leaves_an_email_on_every_patient_and_user(seeded, db):
    """Asserted from seed() stats (the shared test DB is later mutated by other tests)."""
    assert seeded["patients_without_email"] == 0 and seeded["users_without_email"] == 0
    assert seeded["email_placeholders"]["overwrites"] == 0
    inbox = PATIENT_MAILBOXES["patient.en"].lower()
    demo = db.scalar(select(User).where(User.username == "patient.en"))
    assert demo and demo.patient_id
    assert (db.get(Patient, demo.patient_id).email or "").lower() == inbox
    for uname, addr in STAFF_MAILBOXES.items():
        u = db.scalar(select(User).where(User.username == uname))
        assert u is not None and (u.email or "").lower() == addr.lower(), uname


def test_backfill_fills_empties_uniquely_is_idempotent_and_preserves_existing(db):
    inbox = PATIENT_MAILBOXES["patient.en"].lower()
    demo = db.scalar(select(User).where(User.username == "patient.en"))
    kept_patient = Patient(mrn="KS-KEEP-MAIL", given_name="Kept", family_name="Address", gender="female",
                           birth_year=1975, age_band="50-59", nationality_group="Emirati", language="en",
                           region="Abu Dhabi", managing_org_id=1, synthetic=False,
                           email="keep.me@clinic.ae")
    empty_patient = Patient(mrn="KS-NEED-MAIL", given_name="Noura", family_name="Al Shamsi", gender="female",
                            birth_year=1978, age_band="40-49", nationality_group="Emirati", language="ar",
                            region="Abu Dhabi", managing_org_id=1, synthetic=False, email=None)
    blank_patient = Patient(mrn="KS-BLANK-MAIL", given_name="Omar", family_name="Haddad", gender="male",
                            birth_year=1970, age_band="50-59", nationality_group="Emirati", language="ar",
                            region="Abu Dhabi", managing_org_id=1, synthetic=False, email="  ")
    db.add_all([kept_patient, empty_patient, blank_patient])
    empty_user = User(username="locum.gp", display_name="Dr. Laila Mansour (GP)", role="gp", active=True, email=None)
    kept_user = User(username="locum.kept", display_name="Kept Staff", role="admin", active=True,
                     email="staff.kept@clinic.ae")
    db.add_all([empty_user, kept_user])
    db.flush()

    out = eb.backfill_missing_emails(db)
    db.flush()
    assert out["patients"] >= 2 and out["users"] >= 1 and out["overwrites"] == 0
    assert out["domain"] == "example.com"
    assert empty_patient.email == f"noura.alshamsi.{empty_patient.id}@example.com"
    assert blank_patient.email == f"omar.haddad.{blank_patient.id}@example.com"
    assert empty_user.email.endswith(f".{empty_user.id}@example.com") and empty_user.email.endswith("@example.com")
    assert "laila" in empty_user.email and "mansour" in empty_user.email
    assert kept_patient.email == "keep.me@clinic.ae" and kept_user.email == "staff.kept@clinic.ae"
    assert (db.get(Patient, demo.patient_id).email or "").lower() == inbox
    ev = db.query(AuditEvent).filter_by(event="directory.email_placeholders_backfilled").order_by(AuditEvent.id.desc()).first()
    assert ev is not None and ev.detail["patients"] == out["patients"] and ev.detail["users"] == out["users"]
    assert ev.detail["overwrites"] == 0
    # unique across the new placeholders and the rest of the directory
    addrs = [e.strip().lower() for e in (empty_patient.email, blank_patient.email, empty_user.email)]
    assert len(addrs) == len(set(addrs))
    again = eb.backfill_missing_emails(db)
    assert again == {"patients": 0, "users": 0, "overwrites": 0, "domain": "example.com"}
    assert empty_patient.email == f"noura.alshamsi.{empty_patient.id}@example.com"
    db.rollback()


def test_mailers_skip_example_com_placeholders(db, caplog, monkeypatch):
    from kareshafaa.config import get_settings
    from kareshafaa.security import Actor
    from kareshafaa.services import invites

    p = Patient(mrn="KS-PLACEHOLDER", given_name="Sara", family_name="Nasser", gender="female",
                birth_year=1980, age_band="40-49", nationality_group="Emirati", language="en",
                region="Abu Dhabi", managing_org_id=1, synthetic=False,
                email="sara.nasser.1@example.com")
    db.add(p)
    db.flush()
    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", "not-a-real-secret-for-tests")
    monkeypatch.setattr(s, "mail_from", "kareshafaa_com@outlook.com")
    monkeypatch.setattr(s, "mail_oauth_client_id", None)
    monkeypatch.setattr(s, "mail_oauth_refresh_token", None)
    monkeypatch.setattr(s, "mail_allow_synthetic", False)
    sent = []
    monkeypatch.setattr(mail, "send_email", lambda *a, **k: sent.append((a, k)))
    caplog.set_level("INFO", logger="kareshafaa.mail")
    assert mail.is_synthetic_address(p.email) is True
    assert mail.can_send_to_patient(p) is False
    assert sent == []
    assert any("reserved example domain" in r.message for r in caplog.records)
    staff = db.scalar(select(User).where(User.username == "aisha.rahman"))
    actor = Actor(user_id=staff.id, role="coordinator", display=staff.display_name, is_human=True,
                  practitioner_id=staff.practitioner_id)
    p.birth_date = __import__("datetime").date(1980, 1, 2)
    out = invites.invite_after_registration(db, actor, p, base_url="https://kareshafaa.onrender.com")
    assert out["signup_email"] == "not_sent" and sent == []
    db.rollback()
