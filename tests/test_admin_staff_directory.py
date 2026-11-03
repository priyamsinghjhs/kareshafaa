"""Admin > Users > Add a user: every staff role can be created with the right staff directory record (practitioner).

Regression: in a live directory every practitioner already has an account, so the old form offered only
"None (non-clinical role)" and a new GP / specialist / coordinator / clinical lead could not be added at all."""
from __future__ import annotations

import itertools

import pytest
from sqlalchemy import select

from kareshafaa import accounts
from kareshafaa.models import AuditEvent, Organization, Practitioner, User

_n = itertools.count()
LST = "/api/admin/users"


@pytest.fixture()
def made(db):
    """Usernames created by a test; their accounts and new staff records are deactivated afterwards so the shared
    test database keeps the seeded owners (case assignment picks active practitioners)."""
    names: list[str] = []
    yield names
    db.expire_all()
    for u in db.scalars(select(User).where(User.username.in_(names))):
        u.active = False
        if u.practitioner and u.practitioner.name.startswith("Zz "):
            u.practitioner.active = False
    db.commit()


def _org(db, kind: str) -> Organization:
    return db.scalars(select(Organization).where(Organization.kind == kind).order_by(Organization.id)).first()


def _uname(made, role: str) -> str:
    u = f"zz.{role.replace('_', '')}{next(_n)}"
    made.append(u)
    return u


def _post(client, h, **body):
    return client.post(LST, headers=h, json=body)


def test_form_options_are_populated_for_every_role(client, as_user, db):
    d = client.get(LST, headers=as_user("admin")).json()
    assert [r["key"] for r in d["roles"]] == list(accounts.ASSIGNABLE_ROLES)
    assert {r["key"] for r in d["roles"] if r["clinical"]} == set(accounts.CLINICAL_ROLES) == set(d["staff_rules"])
    kinds = {o["kind"] for o in d["organizations"]}
    for role, rule in d["staff_rules"].items():
        assert set(rule["org_kinds"]) & kinds, role  # every clinical role has somewhere to work
        assert rule["org_label"] and rule["org_noun"] and rule["noun"]
    assert {s["key"] for s in d["specialties"]} >= {"gastroenterology", "gynaecology", "breast_surgery", "endocrinology"}
    assert {p["key"] for p in d["programmes"]} == {"colorectal_fit", "cervical", "breast", "diabetes_hba1c"}
    assert all(o["kind_label"] for o in d["organizations"])
    for p in d["unlinked_practitioners"]:
        assert {"id", "name", "role", "org", "specialty", "programme"} <= set(p)


def test_old_form_dead_end_is_gone_when_every_record_has_an_account(client, as_user, db, made):
    """The seeded directory has an account for every practitioner (like the live system): a GP can still be added."""
    h = as_user("admin")
    d = client.get(LST, headers=h).json()
    assert not [p for p in d["unlinked_practitioners"] if p["role"] == "gp"]
    r = _post(client, h, username=_uname(made, "gp"), display_name="Zz Dr. New GP", role="gp")
    assert r.status_code == 422 and r.json()["field"] == "practitioner_id"
    assert "staff directory record" in r.json()["detail"] and "Create a new staff record" in r.json()["detail"]
    org = _org(db, "primary_care")
    r = _post(client, h, username=_uname(made, "gp"), display_name="Zz Dr. New GP", role="gp", staff_record={"org_id": org.id})
    assert r.status_code == 200, r.text
    u = r.json()["user"]
    assert u["role"] == "gp" and u["site"] == org.name and u["practitioner_id"] and r.json()["temporary_password"]


@pytest.mark.parametrize("role,kind,extra", [
    ("coordinator", "screening_programme", {"programme": "cervical"}),
    ("clinical_lead", "screening_programme", {"programme": "breast"}),
    ("clinical_lead", "screening_programme", {}),  # programme-wide medical director
    ("gp", "primary_care", {}),
    ("specialist", "hospital", {"specialty": "gastroenterology"}),
])
def test_admin_adds_each_clinical_role_with_a_new_staff_record(client, as_user, db, made, role, kind, extra):
    org = _org(db, kind)
    uname = _uname(made, role)
    r = _post(client, as_user("admin"), username=uname, display_name=f"Zz {role} person", role=role, team="Team A",
              staff_record={"org_id": org.id, **extra})
    assert r.status_code == 200, r.text
    db.expire_all()
    u = db.scalar(select(User).where(User.username == uname))
    p = u.practitioner
    assert p.role == role and p.org_id == org.id and p.active and not p.is_pool and p.name == u.display_name
    assert p.specialty == extra.get("specialty") and p.programme == extra.get("programme")
    assert u.site == org.name and u.team == "Team A" and u.must_change_password
    if role == "coordinator":  # reports to the coordination supervisor (escalations)
        assert db.get(Practitioner, p.supervisor_id).specialty == "supervisor"
    if role == "clinical_lead" and extra.get("programme"):
        sup = db.get(Practitioner, p.supervisor_id)
        assert sup.role == "clinical_lead" and sup.programme is None
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "practitioner.create", AuditEvent.entity_id == p.id)).all()
    assert len(ev) == 1
    # the new clinician is in the staff directory that coordinators use to refer / assign
    items = client.get(f"/api/directory/practitioners?role={role}", headers=as_user("aisha.rahman")).json()["items"]
    assert p.id in {i["id"] for i in items}


@pytest.mark.parametrize("role", ["analyst", "admin", "translator"])
def test_non_clinical_roles_need_and_get_no_staff_record(client, as_user, db, made, role):
    h = as_user("admin")
    r = _post(client, h, username=_uname(made, role), display_name="Zz Office person", role=role, site="HQ")
    assert r.status_code == 200 and r.json()["user"]["practitioner_id"] is None
    org = _org(db, "screening_programme")
    r = _post(client, h, username=_uname(made, role), display_name="Zz Office person", role=role,
              staff_record={"org_id": org.id})
    assert r.status_code == 422 and "only clinical roles" in r.json()["detail"]
    free = db.scalars(select(Practitioner).where(Practitioner.is_pool.is_(False))).first()
    r = _post(client, h, username=_uname(made, role), display_name="Zz Office person", role=role, practitioner_id=free.id)
    assert r.status_code == 422 and r.json()["field"] == "practitioner_id"


@pytest.mark.parametrize("role,rec,field,words", [
    ("gp", {}, "org_id", "GP practice"),
    ("gp", {"kind": "hospital"}, "org_id", "GP practice"),
    ("specialist", {"kind": "hospital"}, "specialty", "specialty"),
    ("specialist", {"kind": "hospital", "specialty": "cardiology"}, "specialty", "unknown specialty"),
    ("specialist", {"kind": "primary_care", "specialty": "endocrinology"}, "org_id", "hospital"),
    ("coordinator", {"kind": "screening_programme"}, "programme", "programme"),
    ("coordinator", {"kind": "screening_programme", "programme": "lungs"}, "programme", "unknown screening programme"),
    ("coordinator", {"kind": "screening_programme", "programme": "cervical", "specialty": "gynaecology"}, "specialty", "only recorded for specialist"),
    ("gp", {"kind": "primary_care", "programme": "cervical"}, "programme", "leave the programme empty"),
    ("clinical_lead", {"kind": "lab"}, "org_id", "programme office"),
])
def test_validation_messages_name_the_field(client, as_user, db, made, role, rec, field, words):
    body = {k: v for k, v in rec.items() if k != "kind"}
    if "kind" in rec:
        body["org_id"] = _org(db, rec["kind"]).id
    n_before = db.scalar(select(Practitioner.id).order_by(Practitioner.id.desc()).limit(1))
    r = _post(client, as_user("admin"), username=_uname(made, role), display_name="Zz Bad", role=role, staff_record=body)
    assert r.status_code == 422, r.text
    assert r.json()["field"] == field and words.lower() in r.json()["detail"].lower(), r.json()
    db.expire_all()
    assert db.scalar(select(Practitioner.id).order_by(Practitioner.id.desc()).limit(1)) == n_before  # nothing half-saved


def test_link_an_existing_record_checks_role_and_existing_account(client, as_user, db, made):
    h = as_user("admin")
    org = _org(db, "primary_care")
    p = Practitioner(name="Zz Dr. Directory Only (GP)", role="gp", org_id=org.id)
    db.add(p)
    db.commit()
    d = client.get(LST, headers=h).json()
    row = next(x for x in d["unlinked_practitioners"] if x["id"] == p.id)
    assert row["org"] == org.name and row["role"] == "gp"
    r = _post(client, h, username=_uname(made, "spec"), display_name="Zz Wrong", role="specialist", practitioner_id=p.id)
    assert r.status_code == 422 and "is for a Family doctor (GP)" in r.json()["detail"]
    r = _post(client, h, username=_uname(made, "both"), display_name="Zz Both", role="gp", practitioner_id=p.id,
              staff_record={"org_id": org.id})
    assert r.status_code == 422 and "not both" in r.json()["detail"]
    r = _post(client, h, username=_uname(made, "gp"), display_name="Zz Dr. Directory Only", role="gp", practitioner_id=p.id)
    assert r.status_code == 200 and r.json()["user"]["practitioner_id"] == p.id and r.json()["user"]["site"] == org.name
    r = _post(client, h, username=_uname(made, "gp"), display_name="Zz Again", role="gp", practitioner_id=p.id)
    assert r.status_code == 422 and "already has an account" in r.json()["detail"]
    assert p.id not in {x["id"] for x in client.get(LST, headers=h).json()["unlinked_practitioners"]}
    db.expire_all()
    p = db.get(Practitioner, p.id)
    p.active = False
    db.commit()


def test_change_role_to_clinical_creates_the_staff_record(client, as_user, db, made):
    h = as_user("admin")
    uname = _uname(made, "analyst")
    uid = _post(client, h, username=uname, display_name="Zz Moving Analyst", role="analyst").json()["user"]["id"]
    r = client.post(f"{LST}/{uid}/role", headers=h, json={"role": "specialist"})
    assert r.status_code == 422 and r.json()["field"] == "practitioner_id"
    org = _org(db, "hospital")
    r = client.post(f"{LST}/{uid}/role", headers=h, json={"role": "specialist", "staff_record": {"org_id": org.id, "specialty": "gynaecology"}})
    assert r.status_code == 200, r.text
    db.expire_all()
    u = db.get(User, uid)
    assert u.role == "specialist" and u.practitioner.role == "specialist" and u.practitioner.org_id == org.id
    assert u.practitioner.specialty == "gynaecology"
    # same role again keeps the record; no extra one is created
    pid = u.practitioner_id
    assert client.post(f"{LST}/{uid}/role", headers=h, json={"role": "specialist", "team": "Clinic B"}).status_code == 200
    db.expire_all()
    assert db.get(User, uid).practitioner_id == pid


@pytest.mark.parametrize("user", ["aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori", "analyst", "translator"])
def test_only_admins_can_add_staff_records(client, as_user, db, user):
    org = _org(db, "primary_care")
    h = as_user(user)
    assert client.get(LST, headers=h).status_code == 403
    r = _post(client, h, username=f"zz.nope{next(_n)}", display_name="Zz Nope", role="gp", staff_record={"org_id": org.id})
    assert r.status_code == 403
    assert db.scalar(select(Practitioner).where(Practitioner.name == "Zz Nope")) is None


def test_add_user_page_has_the_staff_record_controls(client, as_user):
    html = client.get("/admin/users", headers=as_user("admin")).text
    for needle in ('id="f-staff"', "staffBlock(", "Create a new staff record", "Link an existing record", "checkStaff(",
                   'id="f-site-list"', "fieldErr("):
        assert needle in html, needle
