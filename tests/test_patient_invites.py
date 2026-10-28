"""Patient app account: one-time activation code from the patient record, /activate, expiry, single use, revoke."""
from __future__ import annotations

import re
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from tests.test_patient_registration import person


def _browser(app, ip: str) -> TestClient:
    return TestClient(app, client=(ip, 50000), follow_redirects=False)


def _csrf(b: TestClient, url: str = "/activate") -> tuple[str, str]:
    r = b.get(url)
    assert r.status_code == 200, r.text[:300]
    return re.search(r'name="csrf_token" value="([^"]+)"', r.text).group(1), r.text


@pytest.fixture
def invited(client, as_user):
    h = as_user("aisha.rahman")
    pid = client.post("/api/patients", headers=h, json=person(birth_date="1971-05-17", phone="0501239876")).json()["patient_id"]
    r = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "sms"})
    assert r.status_code == 200, r.text
    return pid, r.json(), h


def test_invite_creates_passwordless_account_and_masked_sms(client, db, invited):
    from kareshafaa.models import Communication, PatientInvite, User
    pid, inv, h = invited
    assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}", inv["code"]) and "/activate?t=" in inv["link"]
    u = db.query(User).filter_by(username=inv["username"]).one()
    assert u.role == "patient" and u.patient_id == pid and u.password_hash is None
    raw = inv["code"].replace("-", "")
    for m in db.query(Communication).filter_by(patient_id=pid).all():
        assert raw not in m.content and inv["code"] not in m.content
    sms = db.query(Communication).filter_by(patient_id=pid, category="account_invite").one()
    assert sms.content.startswith("[SIMULATED - not sent] ") and "****-" + raw[-4:] in sms.content and sms.channel == "sms"
    row = db.query(PatientInvite).filter_by(patient_id=pid).one()
    assert raw not in (row.code_hash + row.link_hash) and row.expires_at - row.created_at == timedelta(hours=72)
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["account"]
    assert rec["state"] == "invited" and rec["username"] == inv["username"] and rec["invite"]["code_hint"].endswith(raw[-4:])
    assert "code" not in str(rec["invite"]).lower().replace("code_hint", "")  # never the full code again


def test_activation_by_link_wrong_dob_then_success_then_single_use(app, client, db, invited):
    from kareshafaa.models import AuditEvent, PatientInvite
    pid, inv, h = invited
    token = inv["link"].split("t=")[1]
    b = _browser(app, "10.20.0.1")
    tok, page = _csrf(b, "/activate?t=" + token)
    assert 'name="t"' in page and 'name="code"' not in page
    r = b.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1971-05-18", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77", "lang": "en"})
    assert r.status_code == 400 and "does not match" in r.text and 'value="1971-05-18"' in r.text  # value kept
    db.expire_all(); assert db.query(PatientInvite).filter_by(patient_id=pid).one().failed_attempts == 1
    tok, _ = _csrf(b, "/activate?t=" + token)
    r = b.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1971-05-17", "password": "short", "confirm": "short", "lang": "en"})
    assert r.status_code == 422 and "stronger password" in r.text
    tok, _ = _csrf(b, "/activate?t=" + token)
    r = b.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1971-05-17", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-78", "lang": "en"})
    assert r.status_code == 422 and "do not match" in r.text
    tok, _ = _csrf(b, "/activate?t=" + token)
    r = b.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1971-05-17", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77", "lang": "en"})
    assert r.status_code == 200 and "Your account is ready" in r.text and inv["username"] in r.text
    me = b.get("/api/me/case")
    assert me.status_code == 200 and me.json()["case_id"] is None
    assert db.query(AuditEvent).filter_by(event="patient.account_activated", entity_id=str(pid)).count() == 1
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["account"]
    assert rec["state"] == "active" and rec["activated_at"]
    # single use
    b2 = _browser(app, "10.20.0.2")
    tok, _ = _csrf(b2, "/activate?t=" + token)
    r = b2.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1971-05-17", "password": "Another-Lantern-Path-55", "confirm": "Another-Lantern-Path-55", "lang": "en"})
    assert r.status_code == 400 and "already been used" in r.text
    # the password works on the normal sign-in page
    tok, _ = _csrf(b2, "/login")
    r = b2.post("/login", data={"csrf_token": tok, "username": inv["username"], "password": "Lantern-Orbit-Cobalt-77"})
    assert r.status_code == 303 and r.headers["location"].startswith("/patient")


def test_typed_code_and_arabic_page(app, client, invited):
    pid, inv, h = invited
    b = _browser(app, "10.20.0.3")
    tok, page = _csrf(b, "/activate?lang=ar")
    assert 'dir="rtl"' in page and "تفعيل حسابك" in page and 'name="code"' in page
    r = b.post("/activate", data={"csrf_token": tok, "code": "ZZZZ-ZZZZ", "birth_date": "1971-05-17", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77", "lang": "ar"})
    assert r.status_code == 400 and "رمز التفعيل غير صحيح" in r.text and 'value="ZZZZ-ZZZZ"' in r.text
    tok, _ = _csrf(b, "/activate?lang=ar")
    r = b.post("/activate", data={"csrf_token": tok, "code": inv["code"].lower().replace("-", " "), "birth_date": "1971-05-17", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77", "lang": "ar"})
    assert r.status_code == 200 and "حسابك جاهز" in r.text


def test_csrf_expiry_lockout_revoke_and_resend(app, client, db, invited):
    from kareshafaa.models import PatientInvite
    pid, inv, h = invited
    token = inv["link"].split("t=")[1]
    b = _browser(app, "10.20.0.4")
    _csrf(b, "/activate")
    assert b.post("/activate", data={"csrf_token": "x" * 30, "t": token, "birth_date": "1971-05-17", "password": "a", "confirm": "a"}).status_code == 403
    for i in range(5):
        tok, _ = _csrf(b, "/activate?t=" + token)
        r = b.post("/activate", data={"csrf_token": tok, "t": token, "birth_date": "1980-01-01", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77"})
    assert "Too many wrong attempts" in r.text
    db.expire_all(); row = db.query(PatientInvite).filter_by(patient_id=pid).one(); assert row.revoked_at and row.revoke_reason == "too many wrong attempts"
    # staff send a new code: works; then cancel it with a reason
    r = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "in_person"})
    assert r.status_code == 200 and r.json()["sms"] is None and r.json()["purpose"] == "activate"
    assert client.post(f"/api/patients/{pid}/account-invite/revoke", headers=h, json={"reason": "x"}).status_code == 422
    r = client.post(f"/api/patients/{pid}/account-invite/revoke", headers=h, json={"reason": "patient asked to wait"})
    assert r.status_code == 200 and r.json()["account"]["state"] == "invite_expired"
    # expiry
    r = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "in_person"}).json()
    db.expire_all(); row = db.query(PatientInvite).filter_by(patient_id=pid).order_by(PatientInvite.id.desc()).first()
    row.expires_at = row.created_at - timedelta(minutes=1); db.commit()
    tok, _ = _csrf(b, "/activate")
    r2 = b.post("/activate", data={"csrf_token": tok, "code": r["code"], "birth_date": "1971-05-17", "password": "Lantern-Orbit-Cobalt-77", "confirm": "Lantern-Orbit-Cobalt-77"})
    assert r2.status_code == 400 and "expired" in r2.text


def test_invite_rules_and_rbac(client, as_user):
    h = as_user("aisha.rahman")
    pid = client.post("/api/patients", headers=h, json=person(phone=None, preferred_channel=None)).json()["patient_id"]
    r = client.post(f"/api/patients/{pid}/account-invite", headers=h, json={"channel": "sms"})
    assert r.status_code == 422 and "mobile" in r.text
    for u in ("admin", "fatima.al.mansoori", "vikram.mehta", "analyst", "translator", "patient.en"):
        assert client.post(f"/api/patients/{pid}/account-invite", headers=as_user(u), json={"channel": "in_person"}).status_code == 403, u
    assert client.post(f"/api/patients/{pid}/account-invite", headers=as_user("omar.haddad"), json={"channel": "in_person"}).status_code in (200, 403)
    # admin cannot create patient-role accounts on the users page; the message points to the patient record
    r = client.post("/api/admin/users", headers=as_user("admin"), json={"username": "x.patient", "display_name": "Xavier Patient", "role": "patient", "temporary_password": "Cobalt-Harbor-Lantern-93"})
    assert r.status_code == 422 and "patient record" in r.text


def test_activation_pages_public_and_no_referrer(app):
    b = _browser(app, "10.20.0.5")
    r = b.get("/activate?t=" + "a" * 32 + "&lang=ur")
    assert r.status_code == 200 and 'dir="rtl"' in r.text and r.headers.get("referrer-policy") == "no-referrer"
    r = b.get("/activate?lang=hi"); assert "सक्रियण कोड" in r.text
