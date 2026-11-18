"""Evaluation (judge) accounts: created only from KARESHAFAA_JUDGE_PASSWORD, unusable without it, no staff e-mail,
same role and queue scope as the matching existing account, strict role separation, patients see only their own data.
The password is generated at random per test run (never a literal in the repository)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from kareshafaa import accounts, judge_accounts as ja

STAFF_LIKE = {"judge.coordinator": "aisha.rahman", "judge.gp": "omar.haddad", "judge.specialist": "vikram.mehta",
              "judge.clinicallead": "fatima.al.mansoori", "judge.analyst": "analyst", "judge.admin": "admin"}
ROLES = {"judge.coordinator": "coordinator", "judge.gp": "gp", "judge.specialist": "specialist",
         "judge.clinicallead": "clinical_lead", "judge.analyst": "analyst", "judge.admin": "admin",
         "judge.patient.en": "patient", "judge.patient.ar": "patient"}


@pytest.fixture(scope="module")
def pw() -> str:
    return accounts.generate_password()


@pytest.fixture(scope="module", autouse=True)
def _cleanup(seeded):
    """Leave the shared test database as it was (other tests count users and directory records)."""
    yield
    from kareshafaa.db import session_factory
    from kareshafaa.models import LoginAttempt, Practitioner, User, UserSession

    db = session_factory()()
    try:
        users = list(db.scalars(select(User).where(User.username.in_(ja.JUDGE_USERNAMES))))
        pids = [u.practitioner_id for u in users if u.practitioner_id]
        ids = [u.id for u in users]
        if ids:
            db.execute(delete(UserSession).where(UserSession.user_id.in_(ids)))
            db.execute(delete(LoginAttempt).where(LoginAttempt.username.in_(ja.JUDGE_USERNAMES)))
            db.execute(delete(User).where(User.id.in_(ids)))
        if pids:
            db.execute(delete(Practitioner).where(Practitioner.id.in_(pids)))
        db.commit()
    finally:
        db.close()


def _provision(db, monkeypatch, value):
    if value is None:
        monkeypatch.delenv(ja.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(ja.ENV_VAR, value)
    return ja.provision_judge_accounts(db)


def _users(db):
    from kareshafaa.models import User
    db.expire_all()
    return {u.username: u for u in db.scalars(select(User).where(User.username.in_(ja.JUDGE_USERNAMES)))}


def _login(app, username, password):
    c = TestClient(app)
    c.get("/login")
    r = c.post("/login", data={"username": username, "password": password,
                               "csrf_token": c.cookies.get("kareshafaa_csrf"), "next": ""}, follow_redirects=False)
    return c, r


def _ok(r) -> bool:
    return r.status_code in (302, 303) and "/login" not in r.headers.get("location", "")


def test_unset_creates_nothing_and_nobody_can_sign_in(db, app, monkeypatch, pw):
    out = _provision(db, monkeypatch, None)
    assert out["mode"] == "unset"
    assert not any(u.password_hash for u in _users(db).values())
    _, r = _login(app, "judge.admin", pw)
    assert not _ok(r)


def test_set_creates_every_account_with_the_right_role(db, monkeypatch, pw):
    out = _provision(db, monkeypatch, pw)
    assert out["mode"] == "env" and out["missing"] == [] and pw not in repr(out)
    users = _users(db)
    assert {k: u.role for k, u in users.items()} == ROLES
    for u in users.values():
        assert u.active and u.password_hash and u.password_hash.startswith("$argon2id$") and pw not in u.password_hash
        assert not u.must_change_password  # one judge cannot lock the others out by being forced to change it
        assert accounts.verify_password(u.password_hash, pw)
    again = _provision(db, monkeypatch, pw)  # idempotent: nothing re-hashed or duplicated
    assert again["created"] == 0 and again["password_set"] == 0


def test_staff_have_no_email_and_their_own_directory_record_with_the_same_scope(db, monkeypatch, pw):
    from kareshafaa.models import Practitioner, User
    from kareshafaa.services.email_backfill import backfill_missing_emails

    _provision(db, monkeypatch, pw)
    backfill_missing_emails(db)  # the start-up placeholder step must leave judge accounts without an address
    db.commit()
    users = _users(db)
    for name, like in STAFF_LIKE.items():
        u = users[name]
        assert not (u.email or "").strip(), name
        src = db.scalar(select(User).where(User.username == like))
        assert u.role == src.role
        if src.practitioner_id is None:
            assert u.practitioner_id is None
            continue
        mine, theirs = db.get(Practitioner, u.practitioner_id), db.get(Practitioner, src.practitioner_id)
        assert mine.id != theirs.id and not mine.is_pool and mine.active
        assert (mine.role, mine.org_id, mine.programme, mine.specialty) == \
               (theirs.role, theirs.org_id, theirs.programme, theirs.specialty)
    # every directory record still has at most one account
    linked = [u.practitioner_id for u in db.scalars(select(User).where(User.practitioner_id.is_not(None)))]
    assert len(linked) == len(set(linked))


def test_judge_staff_see_the_same_work_queue_as_the_matching_account(db, monkeypatch, pw):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User
    from kareshafaa.services.queue import work_queue

    _provision(db, monkeypatch, pw)
    users = _users(db)
    for name in ("judge.coordinator", "judge.gp", "judge.specialist", "judge.clinicallead"):
        src = db.scalar(select(User).where(User.username == STAFF_LIKE[name]))
        mine = work_queue(db, actor_from_user(users[name]), limit=5)["counts"]["all"]
        theirs = work_queue(db, actor_from_user(src), limit=5)["counts"]["all"]
        assert mine == theirs, name
        if name in ("judge.coordinator", "judge.gp", "judge.specialist"):
            assert mine > 0, f"{name} has an empty queue"


def test_judge_patients_link_to_a_safe_synthetic_record_with_an_open_case(db, monkeypatch, pw):
    from kareshafaa.comms import mail
    from kareshafaa.models import Case, Patient, User

    _provision(db, monkeypatch, pw)
    users = _users(db)
    seen = set()
    for name, lang in (("judge.patient.en", "en"), ("judge.patient.ar", "ar")):
        u = users[name]
        p = db.get(Patient, u.patient_id)
        assert p.mrn.startswith("SYN-") and p.language == lang and not p.deceased
        assert not (p.email or "").strip() or mail.is_synthetic_address(p.email)
        assert not mail.can_send_to_patient(p)
        assert db.scalar(select(Case).where(Case.patient_id == p.id, Case.state != "closed")) is not None
        others = db.scalars(select(User).where(User.patient_id == p.id, User.id != u.id)).all()
        assert not others, "a judge patient must not share a record with another account"
        seen.add(p.id)
    assert len(seen) == 2
    first = {n: users[n].patient_id for n in ("judge.patient.en", "judge.patient.ar")}
    _provision(db, monkeypatch, pw)
    assert {n: u.patient_id for n, u in _users(db).items() if n in first} == first  # the link is kept


def test_sign_in_role_separation_and_own_data_only(db, app, monkeypatch, pw):
    from kareshafaa.models import Case

    _provision(db, monkeypatch, pw)
    users = _users(db)
    clients = {}
    for name in ROLES:
        c, r = _login(app, name, pw)
        assert _ok(r), (name, r.status_code)
        clients[name] = c
    adm, lead = clients["judge.admin"], clients["judge.clinicallead"]
    assert adm.get("/api/admin/users").status_code == 200
    assert adm.get("/api/queue?limit=1").status_code in (403, 404)  # admin: no clinical work
    assert lead.get("/api/queue?limit=1").status_code == 200
    assert lead.get("/api/admin/users").status_code in (403, 404)  # clinical lead: no admin tools
    for name in ("judge.coordinator", "judge.gp", "judge.specialist"):
        assert clients[name].get("/api/queue?limit=1").status_code == 200
        assert clients[name].get("/api/admin/users").status_code in (403, 404)
    assert clients["judge.analyst"].get("/api/queue?limit=1").status_code in (403, 404)
    for name in ("judge.patient.en", "judge.patient.ar"):
        c = clients[name]
        mine = c.get("/api/me/case")
        assert mine.status_code == 200
        own = db.scalars(select(Case.id).where(Case.patient_id == users[name].patient_id)).all()
        assert mine.json()["case_id"] in own
        other = db.scalar(select(Case.id).where(Case.patient_id != users[name].patient_id).limit(1))
        assert c.get(f"/api/cases/{other}").status_code in (403, 404)
        assert c.get("/api/queue?limit=1").status_code in (403, 404)
        assert c.get("/patient").status_code == 200


def test_rotate_then_disable(db, app, monkeypatch, pw):
    _provision(db, monkeypatch, pw)
    c_old, r = _login(app, "judge.analyst", pw)
    assert _ok(r)
    new = accounts.generate_password()
    out = _provision(db, monkeypatch, new)
    assert out["password_set"] == len(ja.JUDGE_ACCOUNTS)
    assert c_old.get("/api/auth/me").status_code == 401  # rotation signs out sessions on the old password
    _, r = _login(app, "judge.analyst", new)
    assert _ok(r)
    out = _provision(db, monkeypatch, "")  # cleared in the dashboard
    assert out["mode"] == "unset" and out["disabled"] == len(ja.JUDGE_ACCOUNTS)
    assert not any(u.password_hash for u in _users(db).values())
    _, r = _login(app, "judge.analyst", new)
    assert not _ok(r)


def test_weak_value_leaves_accounts_unusable(db, monkeypatch, pw):
    _provision(db, monkeypatch, pw)
    out = _provision(db, monkeypatch, "password123")
    assert out["mode"] == "weak"
    assert not any(u.password_hash for u in _users(db).values())
