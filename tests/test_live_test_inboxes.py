"""Start-up assignment of allowlisted role test inboxes on an existing (live) database."""
from __future__ import annotations

import inspect

import pytest
from sqlalchemy import select

from kareshafaa import bootstrap
from kareshafaa.comms import mail
from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Patient, User
from kareshafaa.services import email_backfill as eb
from kareshafaa.services.invites import mask_email
from kareshafaa.synth import test_mailboxes as tm
from kareshafaa.synth.test_mailboxes import PATIENT_MAILBOXES, STAFF_MAILBOXES

FAKE_SECRET = "not-a-real-secret-for-tests"

# Confirmed live mapping (usernames -> test inboxes). Keep in sync with synth/test_mailboxes.py.
CONFIRMED = {
    "admin": "testingkareshafaa+calpha_admin@gmail.com",
    "fatima.al.mansoori": "testingkareshafaa+clead1234@gmail.com",
    "aisha.rahman": "testingkareshafaa+ccalpha1234@gmail.com",
    "omar.haddad": "testingkareshafaa+rehmanmalik1234@gmail.com",
    "vikram.mehta": "testingkareshafaa+splist123@gmail.com",
    "analyst": "testingkareshafaa+program_analysts@gmail.com",
    "translator": "testingkareshafaa+medi_trans@gmail.com",
    "patient.en": "testingkareshafaa+asimkhan123@gmail.com",
}


@pytest.fixture()
def mail_on(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "smtp_password", FAKE_SECRET)
    monkeypatch.setattr(s, "mail_from", "kareshafaa_com@outlook.com")
    monkeypatch.setattr(s, "smtp_host", None)
    monkeypatch.setattr(s, "alert_to", None)
    monkeypatch.setattr(s, "mail_allow_synthetic", False)
    monkeypatch.setattr(s, "mail_oauth_client_id", None)
    monkeypatch.setattr(s, "mail_oauth_refresh_token", None)
    monkeypatch.setattr(s, "mail_transport", "auto")
    monkeypatch.setattr(s, "public_base_url", "https://kareshafaa.onrender.com")
    sent: list[tuple] = []

    def fake_send(to, subject, body, **kw):
        sent.append((to, subject, body, kw))

    monkeypatch.setattr(mail, "send_email", fake_send)
    return sent


def _user(db, username: str) -> User:
    u = db.scalar(select(User).where(User.username == username))
    assert u is not None, username
    return u


def _audits(db):
    return db.scalars(select(AuditEvent).where(AuditEvent.event == "directory.test_inboxes_applied")
                      .order_by(AuditEvent.id)).all()


def test_confirmed_mapping_matches_test_mailboxes_module():
    assert STAFF_MAILBOXES == {k: v for k, v in CONFIRMED.items() if k != "patient.en"}
    assert PATIENT_MAILBOXES == {"patient.en": CONFIRMED["patient.en"]}


def test_apply_sets_inbox_over_placeholder_and_is_idempotent(db, mail_on):
    omar = _user(db, "omar.haddad")
    omar.email = f"omar.haddad.{omar.id}@example.com"
    demo = _user(db, "patient.en")
    assert demo.patient_id
    pat = db.get(Patient, demo.patient_id)
    demo.email = f"emma.clarke.{demo.id}@example.com"
    pat.email = f"emma.clarke.{pat.id}@example.com"
    db.flush()

    out = eb.apply_test_mailboxes(db)
    db.flush()
    assert out["users"] >= 2 and out["patients"] == 1 and out["conflicts"] == 0 and out["missing"] == 0
    assert out["overwrites"] >= 3
    assert (omar.email or "").lower() == CONFIRMED["omar.haddad"].lower()
    assert (demo.email or "").lower() == CONFIRMED["patient.en"].lower()
    assert (pat.email or "").lower() == CONFIRMED["patient.en"].lower()
    assert mail.can_send_to_address(omar.email)
    assert mail.can_send_to_patient(pat)

    ev = _audits(db)[-1]
    assert ev.detail["users"] == out["users"] and ev.detail["patients"] == out["patients"]
    blob = repr(ev.detail)
    assert "omar.haddad@" not in blob and CONFIRMED["omar.haddad"] not in blob
    assert any(c["username"] == "omar.haddad" and c["field"] == "user" and c["was_placeholder"]
               and c["old_masked"] for c in ev.detail["changes"])
    assert mask_email(f"omar.haddad.{omar.id}@example.com") in {
        c["old_masked"] for c in ev.detail["changes"] if c["username"] == "omar.haddad"
    }

    again = eb.apply_test_mailboxes(db)
    assert again["users"] == 0 and again["patients"] == 0 and again["overwrites"] == 0
    assert again["already"] >= 8 and again["conflicts"] == 0
    assert len(_audits(db)) == 1  # no second audit when nothing changes
    db.rollback()


def test_apply_overwrites_a_different_real_address_and_masks_the_old_one(db):
    u = _user(db, "aisha.rahman")
    old = "aisha.old@clinic.ae"
    u.email = old
    db.flush()
    out = eb.apply_test_mailboxes(db)
    db.flush()
    assert (u.email or "").lower() == CONFIRMED["aisha.rahman"].lower()
    assert out["users"] >= 1 and out["overwrites"] >= 1
    ev = _audits(db)[-1]
    change = next(c for c in ev.detail["changes"] if c["username"] == "aisha.rahman" and c["field"] == "user")
    assert change["old_masked"] == mask_email(old) and change["was_placeholder"] is False
    assert old not in repr(ev.detail) and CONFIRMED["aisha.rahman"] not in repr(ev.detail)
    db.rollback()


def test_apply_skips_when_another_row_already_holds_the_inbox(db, caplog):
    omar = _user(db, "omar.haddad")
    placeholder = f"omar.haddad.{omar.id}@example.com"
    omar.email = placeholder
    other = User(username="locum.conflict", display_name="Other Staff", role="gp", active=True,
                 email=CONFIRMED["omar.haddad"])
    db.add(other)
    db.flush()
    caplog.set_level("WARNING", logger="kareshafaa.mail")
    out = eb.apply_test_mailboxes(db)
    db.flush()
    assert out["conflicts"] >= 1
    assert (omar.email or "").strip() == placeholder
    assert (other.email or "").lower() == CONFIRMED["omar.haddad"].lower()
    assert any("omar.haddad" in r.message and "already held" in r.message for r in caplog.records)
    db.rollback()


def test_apply_leaves_unmapped_accounts_alone_and_skips_missing_usernames(db, monkeypatch, caplog):
    kept = User(username="locum.kept.inbox", display_name="Kept Staff", role="admin", active=True,
                email="staff.kept@clinic.ae")
    db.add(kept)
    db.flush()
    monkeypatch.setitem(tm.STAFF_MAILBOXES, "no.such.role", "testingkareshafaa+nobody@gmail.com")
    caplog.set_level("WARNING", logger="kareshafaa.mail")
    out = eb.apply_test_mailboxes(db)
    db.flush()
    assert kept.email == "staff.kept@clinic.ae"
    assert out["missing"] >= 1
    assert any("no.such.role" in r.message and "not found" in r.message for r in caplog.records)
    db.rollback()


def test_staff_reset_email_sends_for_omar_haddad_after_apply(db, mail_on):
    from kareshafaa.services import signin_help as sh

    u = _user(db, "omar.haddad")
    u.email = f"omar.haddad.{u.id}@example.com"
    db.flush()
    assert sh.staff_forgot_password(db, "omar.haddad", "127.0.0.1", "en",
                                    "https://kareshafaa.onrender.com") == "sent"
    assert mail_on == []  # placeholder is skipped; the page still says "sent"
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.staff_reset_requested")
                    .order_by(AuditEvent.id.desc())).first()
    assert ev is not None and ev.detail["reason"] == "placeholder"

    out = eb.apply_test_mailboxes(db)
    db.flush()
    assert out["users"] >= 1
    assert (u.email or "").lower() == CONFIRMED["omar.haddad"].lower()
    assert mail.can_send_to_address(u.email)
    assert sh.staff_forgot_password(db, "omar.haddad", "127.0.0.1", "en",
                                    "https://kareshafaa.onrender.com") == "sent"
    assert mail_on and mail_on[0][0].lower() == CONFIRMED["omar.haddad"].lower()
    db.rollback()


def test_bootstrap_assigns_test_inboxes_before_placeholder_backfill():
    src = inspect.getsource(bootstrap._prepare)
    assert src.index("apply_live_test_inboxes") < src.index("backfill_missing_emails")
