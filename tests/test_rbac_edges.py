"""Permission-leak edge cases: IDOR on every id-taking route, GP without an organisation, patients reaching other
patients' data, automated actors, organisation changes mid-session, deactivated users with a live session, and
admin / clinical lead crossing into each other's duties."""
from __future__ import annotations

import re
import typing
from datetime import date, datetime, timedelta

import pytest
from pydantic import BaseModel
from sqlalchemy import select

from kareshafaa.security import ML_SERVICE, PROGRAMME_WIDE_ROLES, SYSTEM, has_perm, make_token
from tests.rbac_routes import POLICY, flat_routes

GP, SPEC, CO, LEAD, ADMIN = "omar.haddad", "vikram.mehta", "aisha.rahman", "fatima.al.mansoori", "admin"
SCOPED = {GP: "gp", SPEC: "specialist"}  # organisation-scoped roles; programme-wide roles see every case by design
# ids that are not owned by a patient's care team: global by design, checked by role only (tests/rbac_routes.py)
GLOBAL_IDS = {
    "uid": "staff accounts - administrators only",
    "version": "model versions - governance, not patient data",
    "proposal_id": "improvement proposals - governance, not patient data",
    "key": "walkthrough scenario name - administrators only, test servers only",
    "msg_id": "results inbox - programme-level lab feed, care coordination only (results:inbox)",
    "item_id": "result reconciliation list - programme-level dead-letter list, care coordination only (results:inbox)",
}
PATIENT_SELF = "/api/me/"  # patient routes take the patient from the session; tested in test_patient_*_isolation
PROGRAMME_ONLY = {"POST /api/patient-requests/{request_id}/confirm", "POST /api/patient-requests/{request_id}/decline",
                  "POST /api/signin-help/{request_id}/close"}  # coordinator / admin, programme-wide by design


def _routes():
    from kareshafaa.main import create_app
    return [(m, p, r) for m, p, r in flat_routes(create_app()) if re.search(r"\{\w+\}", p) and p.startswith("/api/")]


ID_ROUTES = _routes()


# ------------------------------------------------------------------------------------------- body synthesis
def _fake(ann, info=None):
    origin = typing.get_origin(ann)
    if origin is typing.Union or str(origin) == "<class 'types.UnionType'>":
        args = [a for a in typing.get_args(ann) if a is not type(None)]
        return _fake(args[0], info)
    if origin is typing.Literal:
        return typing.get_args(ann)[0]
    if origin in (list, tuple, set):
        mn = next((getattr(m, "min_length", 0) for m in (info.metadata if info else []) if hasattr(m, "min_length")), 0)
        return [_fake(typing.get_args(ann)[0])] * (mn or 0)
    if origin is dict:
        return {}
    if isinstance(ann, type) and issubclass(ann, BaseModel):
        return {n: _fake(f.annotation, f) for n, f in ann.model_fields.items() if f.is_required()}
    meta = info.metadata if info else []
    if ann is bool:
        return False
    if ann in (int, float):
        ge = next((getattr(m, "ge", None) for m in meta if getattr(m, "ge", None) is not None), None)
        gt = next((getattr(m, "gt", None) for m in meta if getattr(m, "gt", None) is not None), None)
        return ann(ge if ge is not None else (gt + 1 if gt is not None else 1))
    if ann is datetime:
        return (datetime.utcnow() + timedelta(days=3)).replace(microsecond=0).isoformat() + "Z"
    if ann is date:
        return date.today().isoformat()
    pat = next((getattr(m, "pattern", None) for m in meta if getattr(m, "pattern", None)), None)
    if pat:
        alt = re.match(r"\^\(?([A-Za-z0-9_ -]+)", pat)
        return alt.group(1) if alt else "x"
    mx = next((getattr(m, "max_length", None) for m in meta if getattr(m, "max_length", None)), None)
    return "access check note"[: mx or 100]


def _body(route) -> dict | None:
    if not route.dependant.body_params:
        return None
    f = route.dependant.body_params[0]
    return _fake(f.field_info.annotation, f.field_info)


# ------------------------------------------------------------------------------------------- fixtures
def _actor(db, username):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User
    return actor_from_user(db.scalar(select(User).where(User.username == username)))


@pytest.fixture(scope="module")
def outside(seeded, client):
    """One patient + case + dependent rows that neither the test GP nor the test specialist may see."""
    from kareshafaa.db import session_factory
    from kareshafaa.models import (Appointment, AppointmentRequest, Case, Consent, ConsultRequest, Escalation,
                                   GpSummary, MedicationRequest, Organization, Patient, Practitioner, Recall, StepTask,
                                   utcnow)
    from kareshafaa.services.access import can_view_case
    from kareshafaa.services.patients import can_view_patient

    db = session_factory()()
    try:
        gp, spec = _actor(db, GP), _actor(db, SPEC)
        other_gp = db.scalars(select(Practitioner).where(Practitioner.role == "gp", Practitioner.is_pool.is_(False),
                                                         Practitioner.org_id.not_in([gp.org_id, spec.org_id]))).first()
        p = Patient(mrn=f"TEST-IDOR-{utcnow().timestamp()}", given_name="Idor", family_name="Outside", gender="female",
                    birth_year=1968, age_band="50-59", nationality_group="emirati", language="en", region="Abu Dhabi",
                    managing_org_id=other_gp.org_id, gp_id=other_gp.id, responsiveness=0.8)
        db.add(p)
        db.flush()
        db.add(Consent(patient_id=p.id, scope="care_coordination", decision="permit", recorded_at=utcnow()))
        db.commit()
        pid = p.id
    finally:
        db.close()
    t = client.post("/api/auth/demo-login", json={"username": CO}).json()["token"]
    client.cookies.clear()
    r = client.post("/api/results", headers={"Authorization": f"Bearer {t}"}, json={"patient_id": pid, "programme": "colorectal_fit",
                                                                "value": 260, "unit": "ug/g"})
    assert r.status_code == 200, r.text
    cid = r.json()["case"]["id"]
    db = session_factory()()
    try:
        c = db.get(Case, cid)
        spec_other = db.scalars(select(Practitioner).where(Practitioner.role == "specialist",
                                                           Practitioner.org_id != spec.org_id)).first()
        st = db.scalars(select(StepTask).where(StepTask.case_id == cid)).first()
        now = utcnow()
        appt = Appointment(case_id=cid, org_id=other_gp.org_id, start=now + timedelta(days=5), status="booked")
        med = MedicationRequest(patient_id=pid, case_id=cid, name="IDOR-MED", status="active", source="kareshafaa")
        rec = Recall(patient_id=pid, programme="colorectal_fit", due_on=date.today(), reason="routine_after_negative", created_by="test")
        con = ConsultRequest(case_id=cid, requester_type="practitioner", target_practitioner_id=spec_other.id,
                             question="access check", status="requested")
        summ = GpSummary(case_id=cid, patient_id=pid, kind="care_started", to_practitioner_id=other_gp.id,
                         title="t", text="t", created_by="test")
        db.add_all([appt, med, rec, con, summ])
        db.flush()
        esc = Escalation(case_id=cid, step_task_id=st.id, level=1, target_practitioner_id=c.owner_id)
        areq = AppointmentRequest(appointment_id=appt.id, case_id=cid, patient_id=pid, kind="change", status="open")
        db.add_all([esc, areq])
        db.commit()
        for a in (gp, spec):  # the fixture really is outside both readers' scope
            assert not can_view_case(db, a, c) and not can_view_patient(db, a, db.get(Patient, pid))
        return {"case_id": cid, "patient_id": pid, "med_id": med.id, "recall_id": rec.id, "consult_id": con.id,
                "sid": summ.id, "esc_id": esc.id, "request_id": areq.id, "appt_id": appt.id}
    finally:
        db.close()


def _state(db, ids):
    from kareshafaa.models import Case, MedicationRequest, Patient
    c, p, m = db.get(Case, ids["case_id"]), db.get(Patient, ids["patient_id"]), db.get(MedicationRequest, ids["med_id"])
    db.refresh(c), db.refresh(p), db.refresh(m)
    return (c.state, c.owner_id, c.tier, c.managing_org_id, p.phone, p.email, p.given_name, m.status)


# ------------------------------------------------------------------------------------------- IDOR
def test_every_id_route_is_covered():
    for m, p, _r in ID_ROUTES:
        names = re.findall(r"\{(\w+)\}", p)
        if p.startswith(PATIENT_SELF) or f"{m} {p}" in PROGRAMME_ONLY:
            continue
        assert all(n in GLOBAL_IDS or n in {"case_id", "patient_id", "med_id", "recall_id", "consult_id", "sid",
                                            "esc_id", "request_id"} for n in names), \
            f"{m} {p}: new id parameter - add an out-of-scope fixture or justify it in GLOBAL_IDS"


@pytest.mark.parametrize("key", sorted(f"{m} {p}" for m, p, _ in ID_ROUTES))
def test_idor_out_of_scope_ids_are_refused(key, client, as_user, outside, db):
    method, path = key.split(" ", 1)
    route = next(r for m, p, r in ID_ROUTES if f"{m} {p}" == key)
    names = re.findall(r"\{(\w+)\}", path)
    if path.startswith(PATIENT_SELF) or key in PROGRAMME_ONLY or any(n in GLOBAL_IDS for n in names):
        pytest.skip("not an organisation-scoped id (see GLOBAL_IDS / PROGRAMME_ONLY)")
    pol = POLICY[key]
    perms = pol.split(":", 1)[1].split("|") if ":" in pol else []
    users = [u for u, role in SCOPED.items() if any(has_perm(role, x) for x in perms)]
    if not users:
        assert all(r in PROGRAMME_WIDE_ROLES for r in ("coordinator", "clinical_lead") if any(has_perm(r, x) for x in perms))
        pytest.skip("only programme-wide roles hold this permission")
    url = re.sub(r"\{(\w+)\}", lambda mm: str(outside[mm.group(1)]), path)
    body = _body(route)
    before = _state(db, outside)
    for u in users:
        r = client.request(method, url, headers=as_user(u), json=body)
        assert r.status_code in (403, 404), (key, u, r.status_code, r.text[:300], body)
        db.expire_all()
        assert _state(db, outside) == before, (key, u, "state changed")


def test_gp_cannot_create_or_read_results_for_another_clinics_patient(client, as_user, outside):
    """Leak fixed: POST /api/results accepted any patient id from an organisation-scoped GP and answered with the
    new case's summary."""
    r = client.post("/api/results", headers=as_user(GP), json={"patient_id": outside["patient_id"],
                                                                "programme": "diabetes_hba1c", "value": 9.9, "unit": "%"})
    assert r.status_code == 404, r.text


def test_scoped_roles_cannot_import_history_into_another_clinics_record(client, as_user, outside, db):
    """Leak fixed: the FHIR history import matched ANY patient by MRN for an organisation-scoped GP/specialist."""
    from kareshafaa.models import Patient
    p = db.get(Patient, outside["patient_id"])
    bundle = {"resourceType": "Bundle", "entry": [
        {"resource": {"resourceType": "Patient", "identifier": [{"value": p.mrn}]}},
        {"resource": {"resourceType": "Condition", "id": "idor-c1", "code": {"text": "IDOR"}}}]}
    for u in (GP, SPEC):
        assert client.post("/api/ingest/fhir-history", headers=as_user(u), json=bundle).status_code == 404


def test_results_inbox_is_care_coordination_only(client, as_user):
    """Leak fixed: GPs (case:create) could read the programme-wide lab inbox (names, identifiers, case ids), get a
    random patient's MRN from the sample endpoint and attach unmatched results to any patient."""
    for u in (GP, SPEC, LEAD, ADMIN):
        h = as_user(u)
        assert client.get("/api/ingest/messages", headers=h).status_code == 403
        assert client.get("/api/ingest/sample", headers=h).status_code == 403
        assert client.post("/api/ingest/messages/1/resolve", headers=h, json={"patient_id": 1}).status_code == 403
        assert client.post("/api/ingest/hl7v2", headers={**h, "Content-Type": "text/plain"}, content=b"MSH|").status_code == 403
    assert client.get("/api/ingest/messages", headers=as_user(CO)).status_code == 200


# ------------------------------------------------------------------------------------------- GP with no organisation
def test_gp_without_an_organisation_sees_nothing(client, db, outside):
    from kareshafaa.models import Case, Patient, User
    u = User(username=f"gp.noorg.{datetime.utcnow().timestamp()}", display_name="GP without org", role="gp",
             active=True, practitioner_id=None)
    db.add(u)
    db.commit()
    h = {"Authorization": f"Bearer {make_token(u.id)}"}
    q = client.get("/api/queue?limit=50&view=all", headers=h)
    assert q.status_code == 200 and q.json().get("total", len(q.json().get("items", []))) in (0, None) or not q.json()["items"]
    assert client.get("/api/cases?limit=50", headers=h).json()["items"] == []
    assert client.get("/api/patients?q=a", headers=h).json().get("items", []) == []
    any_case = db.scalars(select(Case).order_by(Case.id)).first()
    assert client.get(f"/api/cases/{any_case.id}", headers=h).status_code == 403
    assert client.get(f"/api/patients/{any_case.patient_id}", headers=h).status_code in (403, 404)
    assert client.post("/api/results", headers=h, json={"patient_id": any_case.patient_id, "programme": "diabetes_hba1c",
                                                        "value": 9.9, "unit": "%"}).status_code == 404
    pid = db.scalars(select(Patient.id)).first()
    assert client.get(f"/api/patients/{pid}", headers=h).status_code in (403, 404)


# ------------------------------------------------------------------------------------------- patients
def test_patient_cannot_reach_another_patients_record_case_or_fhir(client, as_user, outside, db):
    from kareshafaa.models import User
    h = as_user("patient.en")
    me = db.scalar(select(User).where(User.username == "patient.en"))
    assert me.patient_id != outside["patient_id"]
    for url in (f"/api/patients/{outside['patient_id']}", f"/api/cases/{outside['case_id']}",
                f"/api/cases/{outside['case_id']}/fhir", f"/api/cases/{outside['case_id']}/record", "/api/queue",
                f"/api/gp-summaries/{outside['sid']}"):
        assert client.get(url, headers=h).status_code in (403, 404), url
    # the patient routes take the patient from the session: another patient's ids are "not found"
    assert client.post(f"/api/me/appointments/{outside['appt_id']}/confirm", headers=h).status_code == 404
    assert client.post(f"/api/me/appointments/{outside['appt_id']}/change-request", headers=h,
                       json={"barrier_code": "work", "note": "x"}).status_code in (404, 422)
    assert client.post(f"/api/me/requests/{outside['request_id']}/withdraw", headers=h).status_code == 404
    from kareshafaa.models import Patient
    other = db.get(Patient, outside["patient_id"])
    for url in ("/api/me/case", "/api/me/profile"):
        assert other.mrn not in client.get(url, headers=h).text and "Idor" not in client.get(url, headers=h).text


# ------------------------------------------------------------------------------------------- automated actors
def test_system_and_ml_actors_cannot_open_any_case_or_take_high_impact_actions(db, outside):
    from kareshafaa.models import Case
    from kareshafaa.services.access import assert_can_view, can_view_case
    from kareshafaa.services.errors import Forbidden
    from kareshafaa.signoff import HIGH_IMPACT, require_signoff
    for cid in (outside["case_id"], db.scalars(select(Case.id)).first()):
        c = db.get(Case, cid)
        for a in (SYSTEM, ML_SERVICE):
            assert not can_view_case(db, a, c)
            with pytest.raises(Forbidden):
                assert_can_view(db, a, c)
    for action in HIGH_IMPACT:
        for a in (SYSTEM, ML_SERVICE):
            with pytest.raises(Forbidden):
                require_signoff(a, action, "automated actors never sign off")


# ------------------------------------------------------------------------------------------- mid-session changes
def test_case_moved_to_another_organisation_mid_session(client, as_user, new_case, db):
    from kareshafaa.models import Case, Practitioner
    h = as_user(GP)
    gp = _actor(db, GP)
    c0 = new_case(org_id=gp.org_id, gp_id=gp.practitioner_id)
    assert client.get(f"/api/cases/{c0['id']}", headers=h).status_code == 200
    other = db.scalars(select(Practitioner).where(Practitioner.role == "gp", Practitioner.org_id != gp.org_id,
                                                  Practitioner.is_pool.is_(False))).first()
    c = db.get(Case, c0["id"])
    c.managing_org_id, c.owner_id = other.org_id, other.id
    c.patient.managing_org_id, c.patient.gp_id = other.org_id, other.id
    db.commit()
    assert client.get(f"/api/cases/{c0['id']}", headers=h).status_code == 403  # same session, next request
    assert client.get(f"/api/patients/{c.patient_id}", headers=h).status_code in (403, 404)
    ids = [x["id"] for x in client.get("/api/queue?limit=500&view=all", headers=h).json()["items"]]
    assert c0["id"] not in ids


def test_role_change_and_deactivation_apply_to_a_live_session(client, as_user, fresh_user):
    from kareshafaa.db import session_factory
    from kareshafaa.models import User
    uid, tok = fresh_user("analyst")
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/analytics/kpis", headers=h).status_code == 200
    ad = as_user(ADMIN)
    r = client.post(f"/api/admin/users/{uid}/role", headers=ad, json={"role": "translator"})
    assert r.status_code == 200, r.text
    # the very next request: the old role's access is gone (a role change also ends the user's sessions)
    assert client.get("/api/analytics/kpis", headers=h).status_code in (401, 403)
    uid, tok = fresh_user("analyst")  # deactivation with a live session
    h = {"Authorization": f"Bearer {tok}"}
    assert client.get("/api/auth/me", headers=h).status_code == 200
    assert client.post(f"/api/admin/users/{uid}/deactivate", headers=ad, json={"reason": "access check"}).status_code == 200
    assert client.get("/api/auth/me", headers=h).status_code == 401
    assert client.get("/api/governance/content", headers=h).status_code == 401
    db = session_factory()()
    try:
        assert db.get(User, uid).active is False
    finally:
        db.close()


@pytest.fixture()
def fresh_user():
    """A real (server-side session) sign-in for a new account of the given role."""
    from kareshafaa import accounts
    from kareshafaa.db import session_factory
    from kareshafaa.models import User

    def _mk(role: str):
        db = session_factory()()
        try:
            u = User(username=f"rbac.{role}.{datetime.utcnow().timestamp()}", display_name=f"RBAC {role}", role=role,
                     active=True)
            db.add(u)
            db.flush()
            tok = accounts.create_session(db, u)
            db.commit()
            return u.id, tok
        finally:
            db.close()
    return _mk


# ------------------------------------------------------------------------------------------- crossing duties
ADMIN_CLINICAL = [("/api/cases/{c}/transition", {"to_state": "triaged"}), ("/api/cases/{c}/notify", {}),
                  ("/api/cases/{c}/referral", {"performer_org_id": 1}), ("/api/cases/{c}/assign", {"owner_id": 1, "reason": "xyz"}),
                  ("/api/cases/{c}/close", {"closure_type": "authorised_decision", "reason": "x"}),
                  ("/api/cases/{c}/consent", {"scope": "share_external", "decision": "deny"}),
                  ("/api/results", {"patient_id": 1, "programme": "colorectal_fit", "value": 250, "unit": "ug/g"}),
                  ("/api/patients", {}), ("/api/recalls/invite", {"ids": [1]}),
                  ("/api/governance/proposals/1/decide", {"accept": True, "note": "xxxxxx"}),
                  ("/api/governance/models/1/approve", {"note": "xxxxxxxxxx"})]
LEAD_ADMIN = [("/api/admin/users", {"username": "lead.x", "display_name": "x", "role": "analyst"}),
              ("/api/admin/users/1/reset-password", {}), ("/api/admin/users/2/deactivate", {"reason": "x"}),
              ("/api/admin/users/2/role", {"role": "admin", "reason": "x"}), ("/api/admin/system/audit-fallback/reconcile", None),
              ("/api/sla/sweep", {}), ("/api/governance/models/train", {"algorithm": "logreg"}),
              ("/api/governance/proposals/generate", None), ("/api/demo/scenarios/ramesh_transfer", None)]


@pytest.mark.parametrize("path,body", ADMIN_CLINICAL)
def test_admin_is_refused_clinical_actions(client, as_user, outside, path, body):
    r = client.post(path.format(c=outside["case_id"]), headers=as_user(ADMIN), json=body)
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    assert client.get(f"/api/cases/{outside['case_id']}", headers=as_user(ADMIN)).status_code == 403
    assert client.get(f"/api/patients/{outside['patient_id']}", headers=as_user(ADMIN)).status_code == 403


@pytest.mark.parametrize("path,body", LEAD_ADMIN)
def test_clinical_lead_is_refused_admin_actions(client, as_user, path, body):
    r = client.post(path, headers=as_user(LEAD), **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (path, r.status_code, r.text[:200])
    for url in ("/api/admin/users", "/api/admin/system", "/api/audit/verify", "/api/admin/about", "/api/demo/scenarios"):
        assert client.get(url, headers=as_user(LEAD)).status_code == 403, url


# ------------------------------------------------------------------------------------------- sharing consent in lists
def test_withdrawn_sharing_consent_hides_the_case_from_other_organisations_lists(client, as_user, new_case, db):
    """Leak fixed: after the patient withdrew consent to share with other organisations, the receiving hospital
    could no longer open the case, but still saw it (name, MRN, step) in its work queue and case list, and could open
    the patient's record through the referral."""
    from kareshafaa.models import Case, Consent, utcnow
    from test_journey_api import notify_and_ack
    spec = _actor(db, SPEC)
    c0 = new_case()
    cid = c0["id"]
    notify_and_ack(client, as_user, cid)
    gp = as_user(GP)
    client.post(f"/api/cases/{cid}/transition", headers=gp, json={"to_state": "consultation"})
    r = client.post(f"/api/cases/{cid}/referral", headers=gp, json={"performer_org_id": spec.org_id, "summary": "x" * 60})
    assert r.status_code == 200, r.text
    sp = as_user(SPEC)
    pid = db.get(Case, cid).patient_id
    assert client.get(f"/api/cases/{cid}", headers=sp).status_code == 200
    assert client.get(f"/api/patients/{pid}", headers=sp).status_code == 200
    db.add(Consent(patient_id=pid, scope="share_external", decision="deny", recorded_at=utcnow()))
    db.commit()
    assert client.get(f"/api/cases/{cid}", headers=sp).status_code == 403
    assert client.get(f"/api/patients/{pid}", headers=sp).status_code in (403, 404)
    q = client.get("/api/queue?limit=500&view=all", headers=sp).json()["items"]
    assert cid not in [x["id"] for x in q]
    assert cid not in [x["id"] for x in client.get("/api/cases?limit=500", headers=sp).json()["items"]]
    found = client.get(f"/api/patients?q={db.get(Case, cid).patient.mrn}", headers=sp).json().get("items", [])
    assert pid not in [x.get("id") for x in found]
    # the patient's own organisation still sees everything
    assert client.get(f"/api/cases/{cid}", headers=gp).status_code == 200
