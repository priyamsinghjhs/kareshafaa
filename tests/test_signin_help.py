"""Sign-in help: "Forgot username?" / "Forgot password?" on the sign-in page.

Patients: 3-factor identity check (date of birth, last 4 of the ID document, registered mobile), generic answers
(no account enumeration), device pause and silent record lock, every attempt audited; username shown on a match;
password -> coordinator reset request -> identity check -> one-time reset code -> /reset-password (code + date of
birth) -> every session ended. Staff: a username on Forgot password emails a one-time reset link (or the same generic message if none is sent);
staff who cannot use email may still leave a request for the administrator. Admin reset with a one-time
temporary password, change forced at next sign-in, sessions ended. RBAC: only coordinators handle patient resets,
only admins staff ones; the clinical lead has no admin tools."""
from __future__ import annotations

import itertools
import random
import re
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from kareshafaa import accounts
from kareshafaa.comms.signin_text import HELP_TEXT, LOGIN_TEXT, RESET_OVERRIDES, SMS_RESET_TEXT, STAFF_RESET_TEXT
from kareshafaa.models import AuditEvent, Communication, Patient, PatientInvite, SigninHelpRequest, User, utcnow
from kareshafaa.services import invites as inv
from kareshafaa.services import signin_help as sh

_ip = itertools.count(1)
PW = "Quartz-Meadow-Lantern-91"
NEW_PW = "Copper-Harbour-Willow-37"
COORD = "aisha.rahman"


def fresh(app) -> TestClient:
    """A browser from its own address (rate limits are per device)."""
    n = next(_ip)
    return TestClient(app, client=(f"10.77.{n // 250}.{n % 250 + 1}", 50000), follow_redirects=False)


def csrf(c: TestClient, path: str) -> str:
    html = c.get(path).text
    return re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)


def help_post(c, mode, lang="en", **data):
    tok = csrf(c, f"/forgot-{mode}?lang={lang}&who={data.get('who', 'patient')}")
    return c.post(f"/forgot-{mode}", data={"csrf_token": tok, "lang": lang, "who": "patient", **data})


def result(html: str) -> str:
    m = re.search(r'id="help-result"[^>]*>(.*?)</div>', html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


@pytest.fixture()
def pat(db, make_patient):
    """A patient with an ACTIVE app account and known date of birth, Emirates ID and mobile."""
    pid = make_patient(language="ar")
    p = db.get(Patient, pid)
    p.birth_date = date(1940, 1, 1) + timedelta(days=random.randint(0, 20000))
    eid_tail = f"{random.randint(0, 9999):04d}"
    p.national_id = f"784-{p.birth_date.year}-{random.randint(0, 9999999):07d}"[:-4] + eid_tail[:3] + "-" + eid_tail[3]
    p.phone = f"+97150{random.randint(0, 9999999):07d}"
    p.phone_valid = True
    u = User(username=f"p.help{pid}", display_name="Help Patient", role="patient", patient_id=pid, active=True)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    digits = re.sub(r"\D", "", p.national_id)
    return {"pid": pid, "user": u.username, "uid": u.id, "dob": p.birth_date.isoformat(), "last4": digits[-4:],
            "phone": "0" + p.phone[4:], "e164": p.phone}


def _f(pat, **kw):
    return {"birth_date": pat["dob"], "id_type": "emirates_id", "id_last4": pat["last4"], "phone": pat["phone"], **kw}


# ------------------------------------------------------------------ texts and pages
def test_texts_have_the_same_keys_in_every_language_and_no_demo_wording():
    for D in (LOGIN_TEXT, HELP_TEXT, RESET_OVERRIDES, SMS_RESET_TEXT, STAFF_RESET_TEXT):
        assert set(D) == {"en", "ar", "hi", "ur"}
        for lang in D:
            if isinstance(D["en"], dict):
                assert set(D[lang]) == set(D["en"]), lang
                assert set(D[lang].get("roles", {})) == set(D["en"].get("roles", {}))
                if "err" in D["en"]:
                    assert set(D[lang]["err"]) == set(D["en"]["err"]), lang
    blob = repr(LOGIN_TEXT) + repr(HELP_TEXT) + repr(RESET_OVERRIDES) + repr(SMS_RESET_TEXT) + repr(STAFF_RESET_TEXT)
    assert "demo" not in blob.lower()
    assert set(HELP_TEXT["en"]["roles"]) == set(sh.STAFF_ROLES)


@pytest.mark.parametrize("lang", ["en", "ar", "hi", "ur"])
def test_signin_page_has_forgot_links_in_each_language(app, lang):
    c = fresh(app)
    html = c.get(f"/login?lang={lang}").text
    t = LOGIN_TEXT[lang]
    assert t["forgot_username"] in html and t["forgot_password"] in html
    assert f'href="/forgot-username?lang={lang}"' in html and f'href="/forgot-password?lang={lang}"' in html
    assert f'dir="{"rtl" if lang in ("ar", "ur") else "ltr"}"' in html
    for mode in ("username", "password"):
        for who in ("patient", "staff"):
            r = c.get(f"/forgot-{mode}?lang={lang}&who={who}")
            assert r.status_code == 200 and HELP_TEXT[lang]["title_" + mode] in r.text
            assert ('id="birth_date"' in r.text) == (who == "patient")
            if who == "staff" and mode == "password":
                assert 'id="username"' in r.text and 'id="full_name"' not in r.text
                assert 'id="help-ask-admin"' in r.text
                assert 'id="full_name"' in c.get(f"/forgot-password?lang={lang}&who=staff&via=admin").text
            else:
                assert ('id="full_name"' in r.text) == (who == "staff")
    assert RESET_OVERRIDES[lang]["title"] in c.get(f"/reset-password?lang={lang}").text
    assert STAFF_RESET_TEXT[lang]["title"] in c.get(f"/staff-reset-password?lang={lang}").text


def test_signin_error_is_translated_and_keeps_language(app):
    c = fresh(app)
    tok = csrf(c, "/login?lang=ar")
    r = c.post("/login", data={"username": "nobody.here", "password": "wrong-password-123", "csrf_token": tok, "lang": "ar", "next": ""})
    assert r.status_code == 401 and LOGIN_TEXT["ar"]["err_generic"] in r.text and 'dir="rtl"' in r.text


def test_help_forms_need_the_page_token(app, pat):
    c = fresh(app)
    c.get("/forgot-username")
    r = c.post("/forgot-username", data={"csrf_token": "x" * 30, "lang": "en", "who": "patient", **_f(pat)})
    assert r.status_code == 403 and pat["user"] not in r.text


# ------------------------------------------------------------------ patients: forgot username
def test_forgot_username_shows_it_only_on_a_full_match(app, db, pat):
    r = help_post(fresh(app), "username", **_f(pat))
    assert r.status_code == 200 and pat["user"] in r.text and 'data-result="found"' in r.text
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "signin_help.patient_username", AuditEvent.outcome == "success",
                                                 AuditEvent.entity_id == str(pat["pid"])))
    # international format and passport-style spacing are accepted too
    assert pat["user"] in help_post(fresh(app), "username", **_f(pat, phone=pat["e164"])).text


def test_forgot_username_never_reveals_whether_an_account_exists(app, db, pat, make_patient):
    no_account = db.get(Patient, make_patient())
    no_account.birth_date, no_account.phone = date(1961, 2, 3), "+971509990001"
    no_account.national_id = "784-1961-1234567-1"
    db.commit()
    answers = {
        "wrong phone": help_post(fresh(app), "username", **_f(pat, phone="050 000 0000")),
        "wrong ID": help_post(fresh(app), "username", **_f(pat, id_last4="0000" if pat["last4"] != "0000" else "1111")),
        "wrong date of birth": help_post(fresh(app), "username", **_f(pat, birth_date="1901-01-01")),
        "nobody": help_post(fresh(app), "username", birth_date="1902-02-02", id_type="emirates_id", id_last4="4321", phone="0501112222"),
        "patient without an account": help_post(fresh(app), "username", birth_date="1961-02-03", id_type="emirates_id", id_last4="5671", phone="0509990001"),
    }
    texts = {k: result(r.text) for k, r in answers.items()}
    assert len(set(texts.values())) == 1, texts
    assert set(texts.values()) == {HELP_TEXT["en"]["generic_username"]}
    assert all(r.status_code == 200 and pat["user"] not in r.text for r in answers.values())
    db.expire_all()
    fails = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_username", AuditEvent.outcome == "failure")
                       .order_by(AuditEvent.id.desc()).limit(5)).all()
    assert len(fails) == 5 and all(e.entity_id is None for e in fails)  # failed attempts never point at a record
    blob = " ".join(repr(e.detail) for e in fails)
    assert pat["phone"] not in blob and pat["dob"] not in blob  # typed values are not stored


def test_passport_last_four(app, db, make_patient):
    pid = make_patient()
    p = db.get(Patient, pid)
    p.birth_date, p.phone, p.id_type, p.id_number = date(1977, 7, 7), "+971501234570", "passport", "N1234AB9"
    u = User(username=f"p.pass{pid}", display_name="Passport Patient", role="patient", patient_id=pid)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    r = help_post(fresh(app), "username", birth_date="1977-07-07", id_type="passport", id_last4="ab9", phone="050 123 4570")
    assert u.username not in r.text  # 3 characters: refused before any lookup
    r = help_post(fresh(app), "username", birth_date="1977-07-07", id_type="passport", id_last4="4ab9", phone="050 123 4570")
    assert u.username in r.text


def test_device_is_paused_after_repeated_failures(app, pat):
    c = fresh(app)
    for _ in range(sh.HELP_IP_MAX_FAILURES):
        assert help_post(c, "username", birth_date="1903-03-03", id_last4="9999", phone="0500000000").status_code == 200
    r = help_post(c, "username", **_f(pat))
    assert r.status_code == 429 and pat["user"] not in r.text and HELP_TEXT["en"]["paused"] in r.text
    assert pat["user"] in help_post(fresh(app), "username", **_f(pat)).text  # another device is not affected


def test_record_stops_answering_after_near_misses(app, db, pat):
    for _ in range(sh.HELP_RECORD_MAX_FAILURES):  # right date of birth and mobile, wrong ID, from different devices
        help_post(fresh(app), "username", **_f(pat, id_last4="0000" if pat["last4"] != "0000" else "1111"))
    r = help_post(fresh(app), "username", **_f(pat))
    assert r.status_code == 200 and pat["user"] not in r.text and result(r.text) == HELP_TEXT["en"]["generic_username"]
    db.expire_all()
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "signin_help.patient_username").order_by(AuditEvent.id.desc())).first()
    assert ev.detail["reason"] == "record_locked"


# ------------------------------------------------------------------ patients: forgot password -> coordinator -> reset
def test_forgot_password_same_answer_and_request_only_on_match(app, db, pat):
    ok = help_post(fresh(app), "password", lang="ar", **_f(pat))
    miss = help_post(fresh(app), "password", lang="ar", **_f(pat, phone="0500000000"))
    assert ok.status_code == miss.status_code == 200
    assert result(ok.text) == result(miss.text) == HELP_TEXT["ar"]["generic_password"]
    db.expire_all()
    rows = db.scalars(select(SigninHelpRequest).where(SigninHelpRequest.patient_id == pat["pid"])).all()
    assert len(rows) == 1 and rows[0].status == "open" and rows[0].language == "ar" and rows[0].due_at > rows[0].created_at
    help_post(fresh(app), "password", **_f(pat))  # asking again does not add a second request
    db.expire_all()
    assert len(db.scalars(select(SigninHelpRequest).where(SigninHelpRequest.patient_id == pat["pid"])).all()) == 1
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "signin_help.reset_requested", AuditEvent.entity_id == str(pat["pid"])))
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "signin_help.reset_requested_again", AuditEvent.entity_id == str(pat["pid"])))


def test_only_coordinators_see_and_handle_patient_resets(client, db, pat, as_user, app):
    help_post(fresh(app), "password", **_f(pat))
    co = as_user(COORD)
    lst = client.get("/api/signin-help?audience=patient", headers=co).json()
    row = next(x for x in lst["items"] if x["patient"] and x["patient"]["id"] == pat["pid"])
    assert row["rag"] in ("green", "amber", "red") and "contact" not in row
    for who in ("omar.haddad", "fatima.al.mansoori", "admin", "vikram.mehta", "analyst"):
        assert client.get("/api/signin-help?audience=patient", headers=as_user(who)).status_code == 403, who
    for who in (COORD, "omar.haddad", "fatima.al.mansoori"):
        assert client.get("/api/signin-help?audience=staff", headers=as_user(who)).status_code == 403, who
    # a GP may still invite new patients, but cannot reset an active account
    assert client.post(f"/api/patients/{pat['pid']}/account-invite", headers=as_user("omar.haddad"),
                       json={"channel": "in_person", "verify_method": "in_person_id"}).status_code == 403
    det = client.get(f"/api/patients/{pat['pid']}", headers=co).json()["account"]
    assert det["reset_request"]["id"] == row["id"] and det["can_reset"] is True
    assert client.get(f"/api/patients/{pat['pid']}", headers=as_user("omar.haddad")).json()["account"]["can_reset"] is False
    # coordinators must say how they checked identity
    u = f"/api/patients/{pat['pid']}/account-invite"
    assert client.post(u, headers=co, json={"channel": "sms"}).status_code == 422
    r = client.post(u, headers=co, json={"channel": "sms", "verify_method": "phone_call_checks"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["purpose"] == "reset" and "/reset-password?t=" in body["link"]
    db.expire_all()
    req = db.get(SigninHelpRequest, row["id"])
    assert (req.status, req.verify_method) == ("done", "phone_call_checks") and req.invite_id
    i = db.get(PatientInvite, req.invite_id)
    hours = (i.expires_at - i.created_at).total_seconds() / 3600
    assert abs(hours - inv.RESET_TTL_HOURS) < 0.01
    sms = db.get(Communication, i.communication_id)
    code = body["code"]
    assert sms.simulated and "/reset-password" in sms.content and code not in sms.content and code[-4:] in sms.content
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "signin_help.request_done", AuditEvent.entity_id == str(req.id)))


def test_coordinator_can_close_a_request_without_a_code(client, db, pat, as_user, app):
    help_post(fresh(app), "password", **_f(pat))
    db.expire_all()
    rid = sh.open_patient_request(db, pat["pid"]).id
    co = as_user(COORD)
    assert client.post(f"/api/signin-help/{rid}/close", headers=co, json={"outcome": "closed", "note": "x"}).status_code == 422
    assert client.post(f"/api/signin-help/{rid}/close", headers=as_user("admin"), json={"outcome": "closed", "note": "not mine to do"}).status_code == 403
    assert client.post(f"/api/signin-help/{rid}/close", headers=co, json={"outcome": "closed", "note": "Caller could not confirm the address"}).status_code == 200
    db.expire_all()
    assert db.get(SigninHelpRequest, rid).status == "closed"


def _issue_reset(client, as_user, pat, channel="in_person") -> str:
    r = client.post(f"/api/patients/{pat['pid']}/account-invite", headers=as_user(COORD),
                    json={"channel": channel, "verify_method": "in_person_id"})
    assert r.status_code == 200, r.text
    return r.json()["code"]


def _reset(c, code, dob, pw=NEW_PW, confirm=None, path="/reset-password", lang="en"):
    tok = csrf(c, f"{path}?lang={lang}")
    return c.post(path, data={"csrf_token": tok, "lang": lang, "code": code, "birth_date": dob, "password": pw,
                              "confirm": confirm or pw})


def _login(c, username, pw):
    tok = csrf(c, "/login")
    return c.post("/login", data={"username": username, "password": pw, "csrf_token": tok, "next": ""})


def test_reset_code_sets_new_password_ends_every_session_and_works_once(client, db, pat, as_user, app):
    old = fresh(app)
    assert _login(old, pat["user"], PW).status_code == 303
    assert old.get("/api/auth/me").status_code == 200
    code = _issue_reset(client, as_user, pat)
    c = fresh(app)
    r = _reset(c, code, "1900-01-01")
    assert r.status_code == 400 and RESET_OVERRIDES["en"]["title"] in r.text
    r = _reset(c, code, pat["dob"], confirm="something-else-entirely")
    assert r.status_code == 422
    r = _reset(c, code, pat["dob"])
    assert r.status_code == 200 and RESET_OVERRIDES["en"]["done_title"] in r.text and pat["user"] in r.text
    assert old.get("/api/auth/me").status_code == 401  # the earlier session ended
    assert _login(fresh(app), pat["user"], PW).status_code == 401
    assert _login(fresh(app), pat["user"], NEW_PW).status_code == 303
    again = _reset(fresh(app), code, pat["dob"], pw="Another-Strong-Phrase-55")
    assert again.status_code == 400 and RESET_OVERRIDES["en"]["err"]["used"] in again.text  # single use
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "patient.password_reset", AuditEvent.entity_id == str(pat["uid"])))


def test_reset_code_locks_after_wrong_dates_and_can_be_revoked(client, db, pat, as_user, app):
    code = _issue_reset(client, as_user, pat)
    for i in range(inv.MAX_FAILED_ATTEMPTS):
        _reset(fresh(app), code, f"1900-01-{i + 1:02d}")
    r = _reset(fresh(app), code, pat["dob"])
    assert r.status_code == 400 and RESET_OVERRIDES["en"]["err"]["locked"] in r.text
    code2 = _issue_reset(client, as_user, pat)
    assert client.post(f"/api/patients/{pat['pid']}/account-invite/revoke", headers=as_user(COORD),
                       json={"reason": "Given to the wrong person"}).status_code == 200
    r = _reset(fresh(app), code2, pat["dob"])
    assert r.status_code == 400 and RESET_OVERRIDES["en"]["err"]["revoked"] in r.text
    assert _login(fresh(app), pat["user"], PW).status_code == 303  # the old password still works until a reset succeeds


def test_reset_code_expires(client, db, pat, as_user, app):
    code = _issue_reset(client, as_user, pat)
    db.expire_all()
    i = db.scalars(select(PatientInvite).where(PatientInvite.patient_id == pat["pid"]).order_by(PatientInvite.id.desc())).first()
    i.expires_at = utcnow() - timedelta(minutes=1)
    db.commit()
    r = _reset(fresh(app), code, pat["dob"])
    assert r.status_code == 400 and RESET_OVERRIDES["en"]["err"]["expired"] in r.text


def test_reset_page_refuses_an_activation_code(client, db, as_user, app, make_patient):
    pid = make_patient()
    p = db.get(Patient, pid)
    p.birth_date = date(1980, 5, 5)
    db.commit()
    r = client.post(f"/api/patients/{pid}/account-invite", headers=as_user(COORD), json={"channel": "in_person"})
    assert r.status_code == 200 and r.json()["purpose"] == "activate"
    bad = _reset(fresh(app), r.json()["code"], "1980-05-05")
    assert bad.status_code == 400 and RESET_OVERRIDES["en"]["err"]["invalid"] in bad.text


# ------------------------------------------------------------------ staff
def _staff(c, mode="password", **kw):
    data = {"who": "staff", "via": "admin", "full_name": "Sara Ali", "role": "coordinator", "org": "Corniche Family Health Centre",
            "contact": "02 555 0101", "need": mode, "note": "", **kw}
    tok = csrf(c, f"/forgot-{mode}?lang=en&who=staff&via=admin")
    return c.post(f"/forgot-{mode}", data={"csrf_token": tok, "lang": "en", **data})


def test_staff_request_validation_storage_and_admin_only(client, db, as_user, app):
    r = _staff(fresh(app), full_name="", contact="1")
    assert r.status_code == 422 and HELP_TEXT["en"]["s_err_name"] in r.text and HELP_TEXT["en"]["s_err_contact"] in r.text
    r = _staff(fresh(app), full_name="Sara Ali Haddad", note="Locked out after leave")
    assert r.status_code == 200 and 'data-result="staff_sent"' in r.text
    real = _staff(fresh(app), full_name="Fatima Al Mansoori", role="clinical_lead")
    assert result(real.text) == result(r.text) == HELP_TEXT["en"]["s_sent"]  # same answer whoever it is
    ad = as_user("admin")
    lst = client.get("/api/signin-help?audience=staff", headers=ad).json()
    row = next(x for x in lst["items"] if x["full_name"] == "Sara Ali Haddad")
    assert (row["role"], row["org"], row["contact"], row["note"], row["kind"]) == ("coordinator", "Corniche Family Health Centre", "02 555 0101", "Locked out after leave", "password")
    for who in (COORD, "fatima.al.mansoori", "omar.haddad", "analyst"):
        assert client.get("/api/signin-help?audience=staff", headers=as_user(who)).status_code == 403, who
        assert client.post(f"/api/signin-help/{row['id']}/close", headers=as_user(who), json={"outcome": "done", "note": "trying it"}).status_code == 403
    db.expire_all()
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "signin_help.staff_request", AuditEvent.entity_id == str(row["id"])))


def test_staff_requests_are_rate_limited_per_device(app):
    c = fresh(app)
    for _ in range(sh.STAFF_REQUESTS_PER_IP_HOUR):
        assert _staff(c).status_code == 200
    r = _staff(c)
    assert r.status_code == 429 and HELP_TEXT["en"]["s_paused"] in r.text


def _staff_user(db, role="analyst") -> User:
    u = User(username=f"help.staff{next(_ip)}", display_name="Help Staff", role=role, active=True)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    return u


def test_admin_reset_forces_change_ends_sessions_clears_lockout_and_closes_request(client, db, as_user, app):
    u = _staff_user(db)
    s1 = fresh(app)
    assert _login(s1, u.username, PW).status_code == 303
    attacker = fresh(app)
    for _ in range(6):
        _login(attacker, u.username, "wrong-password-999")
    assert _login(fresh(app), u.username, PW).status_code == 429  # locked
    _staff(fresh(app), full_name="Help Staff")
    ad = as_user("admin")
    hid = next(x["id"] for x in client.get("/api/signin-help?audience=staff", headers=ad).json()["items"] if x["full_name"] == "Help Staff")
    assert client.post(f"/api/admin/users/{u.id}/reset-password", headers=as_user("fatima.al.mansoori"), json={}).status_code == 403
    r = client.post(f"/api/admin/users/{u.id}/reset-password", headers=ad, json={"help_request_id": hid})
    assert r.status_code == 200
    temp = r.json()["temporary_password"]
    assert temp and r.json()["user"]["must_change_password"] is True
    assert s1.get("/api/auth/me").status_code == 401  # every session ended
    c = fresh(app)
    r = _login(c, u.username, temp)
    assert r.status_code == 303 and r.headers["location"] == "/account/password"  # lockout cleared, change forced
    assert c.get("/api/queue").status_code == 403  # nothing else until the password is changed
    db.expire_all()
    h = db.get(SigninHelpRequest, hid)
    assert h.status == "done" and h.handled_by
    assert db.scalar(select(AuditEvent.id).where(AuditEvent.event == "user.password_reset", AuditEvent.entity_id == str(u.id)))
    assert temp not in repr(db.scalars(select(AuditEvent.detail).where(AuditEvent.entity_id == str(u.id))).all())


def test_admin_cannot_reset_a_patient_account_and_gains_no_clinical_actions(client, db, pat, as_user):
    ad = as_user("admin")
    assert client.post(f"/api/admin/users/{pat['uid']}/reset-password", headers=ad, json={}).status_code == 403
    assert client.post(f"/api/patients/{pat['pid']}/account-invite", headers=ad, json={"channel": "in_person", "verify_method": "in_person_id"}).status_code == 403
    assert client.get("/api/signin-help?audience=patient", headers=ad).status_code == 403


def test_staff_close_needs_a_note(client, as_user, app):
    _staff(fresh(app), full_name="Close Note Person")
    ad = as_user("admin")
    hid = next(x["id"] for x in client.get("/api/signin-help?audience=staff", headers=ad).json()["items"] if x["full_name"] == "Close Note Person")
    assert client.post(f"/api/signin-help/{hid}/close", headers=ad, json={"outcome": "done", "note": "ok"}).status_code == 422
    assert client.post(f"/api/signin-help/{hid}/close", headers=ad, json={"outcome": "done", "note": "Called back, told username"}).status_code == 200
    assert client.post(f"/api/signin-help/{hid}/close", headers=ad, json={"outcome": "done", "note": "Called back, told username"}).status_code == 422
