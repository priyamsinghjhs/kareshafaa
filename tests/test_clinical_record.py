"""Doctor's clinical record on the case screen: RBAC, clinician notes never reach the patient, audited writes,
FHIR history import, and the one-time SYN- sample steps (history backfill + date re-anchoring): idempotent and
SYN-only."""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from kareshafaa import accounts, audit
from kareshafaa.models import (AllergyIntolerance, AuditEvent, Case, Communication, Condition, DataMigration,
                               Encounter, Investigation, MedicationRequest, Patient, StepTask, User, utcnow)

PW = "copper-meadow-orchid-41"
_n = itertools.count()


def _notified_case(client, as_user, new_case):
    c = new_case("colorectal_fit", 250)
    co = as_user("aisha.rahman")
    client.post(f"/api/cases/{c['id']}/notify", headers=co, json={"category": "result_notification"})
    r = client.post(f"/api/cases/{c['id']}/call", headers=co, json={"outcome": "reached", "note": "explained"})
    assert r.json()["case"]["state"] == "patient_notified"
    return c["id"]


def _events(db, cid, name):
    db.expire_all()
    return db.scalars(select(AuditEvent).where(AuditEvent.case_id == cid, AuditEvent.event == name)).all()


def test_record_rbac(client, as_user, new_case):
    cid = _notified_case(client, as_user, new_case)
    r = client.get(f"/api/cases/{cid}/record", headers=as_user("omar.haddad"))  # the patient's GP
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["can_write"] is True and d["summary"]["mrn"].startswith("TEST-")
    assert any(t["triggering"] for t in d["tests"]) and d["none"] == "None recorded"
    co = client.get(f"/api/cases/{cid}/record", headers=as_user("aisha.rahman"))  # coordinator: view only
    assert co.status_code == 200 and co.json()["can_write"] is False
    assert client.get(f"/api/cases/{cid}/record", headers=as_user("fatima.al.mansoori")).status_code == 200
    assert client.get(f"/api/cases/{cid}/record", headers=as_user("vikram.mehta")).status_code == 403  # not involved
    assert client.get(f"/api/cases/{cid}/record", headers=as_user("analyst")).status_code == 403
    assert client.get(f"/api/cases/{cid}/record", headers=as_user("admin")).status_code == 403
    assert client.get(f"/api/cases/{cid}/record", headers=as_user("patient.en")).status_code == 403
    body = {"kind": "gp_visit", "start": utcnow().isoformat() + "Z", "notes": "Seen in clinic, result discussed."}
    assert client.post(f"/api/cases/{cid}/encounters", headers=as_user("aisha.rahman"), json=body).status_code == 403
    assert client.post(f"/api/cases/{cid}/medications", headers=as_user("aisha.rahman"),
                       json={"name": "Metformin"}).status_code == 403
    assert client.post(f"/api/cases/{cid}/encounters", headers=as_user("vikram.mehta"), json=body).status_code == 403


def test_add_consultation_and_medicine_are_audited(client, as_user, new_case, db):
    cid = _notified_case(client, as_user, new_case)
    gp = as_user("omar.haddad")
    r = client.post(f"/api/cases/{cid}/encounters", headers=gp, json={
        "kind": "gp_visit", "start": (utcnow() - timedelta(hours=2)).isoformat() + "Z", "reason": "FIT positive",
        "notes": "Discussed colonoscopy; no red flags; anticoagulants none.", "outcome": "Referral agreed"})
    assert r.status_code == 200, r.text
    future = {"kind": "gp_visit", "start": (utcnow() + timedelta(days=3)).isoformat() + "Z", "notes": "not yet seen"}
    assert client.post(f"/api/cases/{cid}/encounters", headers=gp, json=future).status_code == 422
    assert client.post(f"/api/cases/{cid}/encounters", headers=gp, json={**future, "kind": "surgery"}).status_code == 422
    r = client.post(f"/api/cases/{cid}/medications", headers=gp, json={
        "name": "Metformin", "dose": "500 mg", "frequency": "Twice daily with meals", "status": "active",
        "reason": "Type 2 diabetes"})
    assert r.status_code == 200, r.text
    mid = r.json()["medication_id"]
    rec = client.get(f"/api/cases/{cid}/record", headers=gp).json()
    enc = [e for e in rec["encounters"] if e["kind"] == "gp_visit"]
    assert enc and enc[0]["clinician"] == "Dr. Omar Haddad" and "colonoscopy" in enc[0]["notes"]
    med = next(m for m in rec["medications"] if m["id"] == mid)
    assert med["status"] == "active" and med["prescriber"] == "Dr. Omar Haddad"
    assert client.post(f"/api/cases/{cid}/medications/{mid}/stop", headers=gp, json={"reason": "x"}).status_code == 422
    assert client.post(f"/api/cases/{cid}/medications/{mid}/stop", headers=gp,
                       json={"reason": "Stomach upset"}).status_code == 200
    assert client.post(f"/api/cases/{cid}/medications/{mid}/stop", headers=gp,
                       json={"reason": "again please"}).status_code == 422  # already stopped
    assert len(_events(db, cid, "encounter.recorded")) == 1
    assert len(_events(db, cid, "medication.recorded")) == 1
    assert len(_events(db, cid, "medication.stopped")) == 1
    assert audit.verify_chain(db)["valid"]
    fhir = client.get(f"/api/cases/{cid}/fhir", headers=gp).json()
    kinds = {e["resource"]["resourceType"] for e in fhir["entry"]}
    assert {"Encounter", "MedicationRequest"} <= kinds


def test_patient_never_sees_clinician_notes(client, as_user, new_case, db):
    cid = _notified_case(client, as_user, new_case)
    secret = "Clinician-only note 7f3a: discussed family history privately."
    client.post(f"/api/cases/{cid}/encounters", headers=as_user("omar.haddad"), json={
        "kind": "gp_visit", "start": utcnow().isoformat() + "Z", "notes": secret})
    db.expire_all()
    pid = db.get(Case, cid).patient_id
    u = User(username=f"rec.patient{next(_n)}.{pid}", display_name="Patient", role="patient", patient_id=pid)
    db.add(u)
    db.flush()
    h = {"Authorization": "Bearer " + accounts.create_session(db, u)}
    db.commit()
    me = client.get("/api/me/case", headers=h)
    assert me.status_code == 200 and secret not in me.text and "7f3a" not in me.text
    assert client.get(f"/api/cases/{cid}/record", headers=h).status_code == 403
    assert client.get(f"/api/cases/{cid}", headers=h).status_code == 403


def test_cookie_session_writes_need_csrf(app, db, new_case, client, as_user):
    from kareshafaa.models import Practitioner
    cid = _notified_case(client, as_user, new_case)
    pr = Practitioner(name=f"Test GP rec {next(_n)}", role="gp", org_id=db.get(Practitioner, 19).org_id)
    db.add(pr)
    db.flush()
    u = User(username=f"t.gp.rec{next(_n)}", display_name="GP", role="gp", practitioner_id=pr.id)
    accounts.set_password(u, PW)
    db.add(u)
    db.commit()
    c = TestClient(app)
    c.get("/login")
    c.post("/login", data={"username": u.username, "password": PW, "csrf_token": c.cookies.get("kareshafaa_csrf"), "next": ""})
    body = {"name": "Atorvastatin", "dose": "20 mg"}
    assert c.post(f"/api/cases/{cid}/medications", json=body).status_code == 403
    tok = c.get("/api/auth/me").json()["csrf"]
    assert c.post(f"/api/cases/{cid}/medications", json=body, headers={"X-CSRF-Token": tok}).status_code == 200


def test_fhir_history_import_is_idempotent_and_skips_programme_results(client, as_user, new_case, db):
    cid = _notified_case(client, as_user, new_case)
    db.expire_all()
    p = db.get(Case, cid).patient
    bundle = {"resourceType": "Bundle", "type": "collection", "id": "hist-1", "entry": [
        {"resource": {"resourceType": "Patient", "identifier": [{"value": p.mrn}], "birthDate": f"{p.birth_year}-01-01"}},
        {"resource": {"resourceType": "Encounter", "id": "e1", "status": "finished", "type": [{"text": "GP consultation"}],
                      "period": {"start": "2026-03-01T09:00:00Z"}, "participant": [{"individual": {"display": "Dr External"}}],
                      "reasonCode": [{"text": "Hypertension review"}]}},
        {"resource": {"resourceType": "MedicationRequest", "id": "m1", "status": "active", "intent": "order",
                      "medicationCodeableConcept": {"text": "Amlodipine"}, "authoredOn": "2025-01-10",
                      "dosageInstruction": [{"text": "Once daily", "doseAndRate": [{"doseQuantity": {"value": 5, "unit": "mg"}}]}]}},
        {"resource": {"resourceType": "AllergyIntolerance", "id": "a1", "code": {"text": "Penicillin"},
                      "reaction": [{"manifestation": [{"text": "Rash"}], "severity": "mild"}]}},
        {"resource": {"resourceType": "Condition", "id": "c1", "code": {"text": "Essential hypertension",
                      "coding": [{"code": "I10"}]}, "clinicalStatus": {"coding": [{"code": "active"}]}}},
        {"resource": {"resourceType": "Observation", "id": "o1", "code": {"text": "LDL cholesterol", "coding": [{"code": "13457-7"}]},
                      "valueQuantity": {"value": 3.9, "unit": "mmol/L"}, "interpretation": [{"coding": [{"code": "H"}]}],
                      "effectiveDateTime": "2026-02-01T08:00:00Z"}},
        {"resource": {"resourceType": "Observation", "id": "o2", "code": {"coding": [{"code": "29771-3"}]},
                      "valueQuantity": {"value": 300, "unit": "ug/g"}}},
    ]}
    gp = as_user("omar.haddad")
    r = client.post("/api/ingest/fhir-history", headers=gp, json=bundle)
    assert r.status_code == 200, r.text
    assert r.json()["imported"] == {"Encounter": 1, "MedicationRequest": 1, "AllergyIntolerance": 1, "Condition": 1,
                                    "Observation": 1} and r.json()["skipped"] == 1
    again = client.post("/api/ingest/fhir-history", headers=gp, json=bundle).json()
    assert again["imported"] == {} and again["skipped"] == 6
    assert client.post("/api/ingest/fhir-history", headers=as_user("aisha.rahman"), json=bundle).status_code == 403
    bad = {**bundle, "entry": [{"resource": {"resourceType": "Patient", "identifier": [{"value": "NOPE-1"}]}}]}
    assert client.post("/api/ingest/fhir-history", headers=gp, json=bad).status_code == 404
    rec = client.get(f"/api/cases/{cid}/record", headers=gp).json()
    assert any(a["substance"] == "Penicillin" for a in rec["summary"]["allergies"])
    assert any(t["name"] == "LDL cholesterol" and t["abnormal"] for t in rec["tests"])
    assert any(m["name"] == "Amlodipine" and m["dose"] == "5 mg" for m in rec["medications"])


def _make_sample(db, cid):
    """Turn the patient of a test case into a SYN- sample patient."""
    p = db.get(Case, cid).patient
    p.mrn = f"SYN-T{next(_n)}-{p.id}"
    db.commit()
    return p.id


def test_backfill_is_syn_only_and_idempotent(client, as_user, new_case, db):
    from kareshafaa.services.sample_data import backfill_sample_clinical_history
    syn_case, real_case = (_notified_case(client, as_user, new_case) for _ in range(2))
    syn_pid = _make_sample(db, syn_case)
    real_pid = db.get(Case, real_case).patient_id
    res = backfill_sample_clinical_history(db, only_patient_ids={syn_pid, real_pid})
    db.commit()
    assert res["patients"] == 1
    tables = (AllergyIntolerance, Condition, MedicationRequest, Encounter, Investigation)

    def n(pid):
        return sum(db.scalar(select(func.count()).select_from(t).where(t.patient_id == pid, t.source == "sample_backfill"))
                   for t in tables)
    first = n(syn_pid)
    assert first >= 4 and n(real_pid) == 0  # never touches non-SYN records
    assert db.scalar(select(func.count()).select_from(AllergyIntolerance).where(AllergyIntolerance.patient_id == syn_pid)) == 1
    again = backfill_sample_clinical_history(db, only_patient_ids={syn_pid, real_pid})
    db.commit()
    assert again["patients"] == 0 and n(syn_pid) == first
    now = utcnow()
    for t, col in ((Encounter, Encounter.start), (Investigation, Investigation.effective_at)):
        assert db.scalar(select(func.max(col)).where(t.patient_id == syn_pid)) <= now  # nothing in the future
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "sample.history_backfilled")).all()
    assert ev and ev[-1].detail["patients"] == 1


def test_reanchor_moves_only_stale_syn_cases_and_keeps_order(client, as_user, new_case, db):
    from kareshafaa.services.sample_data import reanchor_sample_case_dates
    syn_case, real_case = (_notified_case(client, as_user, new_case) for _ in range(2))
    _make_sample(db, syn_case)
    shift = timedelta(days=240)
    for cid in (syn_case, real_case):  # make both look like a journey that stalled 8 months ago
        for st in db.scalars(select(StepTask).where(StepTask.case_id == cid)):
            st.started_at = st.started_at - shift
            st.due_at = st.due_at - shift if st.due_at else None
            if st.ended_at:
                st.ended_at = st.ended_at - shift
        for m in db.scalars(select(Communication).where(Communication.case_id == cid)):
            m.sent_at = m.sent_at - shift
        c = db.get(Case, cid)
        c.created_at, c.state_entered_at = c.created_at - shift, c.state_entered_at - shift
        c.due_at = c.due_at - shift if c.due_at else None
    db.commit()
    recent = Communication(case_id=syn_case, patient_id=db.get(Case, syn_case).patient_id, category="reminder",
                           channel="sms", content="recent", status="completed", sent_at=utcnow() - timedelta(hours=3))
    db.add(recent)
    db.commit()
    real_before = db.scalar(select(StepTask.due_at).where(StepTask.case_id == real_case, StepTask.status == "in-progress"))
    now = utcnow()
    res = reanchor_sample_case_dates(db, now=now, only_case_ids={syn_case, real_case})
    db.commit()
    assert res["moved"] == 1 and res["case_ids"] == [syn_case]
    db.expire_all()
    step = db.scalar(select(StepTask).where(StepTask.case_id == syn_case, StepTask.status == "in-progress"))
    overdue = (now - step.due_at).days
    assert 3 <= overdue <= 16 and step.started_at < step.due_at
    c = db.get(Case, syn_case)
    assert c.created_at <= c.state_entered_at <= now and abs((c.due_at - step.due_at).total_seconds()) < 1
    assert db.get(Communication, recent.id).sent_at == recent.sent_at  # last 2 days are never moved
    sent = [m.sent_at for m in db.scalars(select(Communication).where(Communication.case_id == syn_case))]
    assert all(t <= now for t in sent)
    assert db.scalar(select(StepTask.due_at).where(StepTask.case_id == real_case, StepTask.status == "in-progress")) == real_before
    assert len(_events(db, syn_case, "sample.dates_reanchored")) == 1
    assert reanchor_sample_case_dates(db, now=now, only_case_ids={syn_case, real_case})["moved"] == 0
    assert audit.verify_chain(db)["valid"]


def test_data_steps_run_once(db, monkeypatch):
    from kareshafaa.db import session_factory
    from kareshafaa.services import sample_data
    calls = []
    name = f"test_step_{next(_n)}"
    monkeypatch.setattr(sample_data, "MIGRATIONS", [(name, lambda d, now=None: calls.append(1) or {"ok": 1})])
    assert sample_data.run_pending(session_factory())[name] == {"ok": 1}
    assert sample_data.run_pending(session_factory())[name] == "already applied"
    assert calls == [1]
    db.expire_all()
    assert db.scalar(select(DataMigration).where(DataMigration.name == name)) is not None


def test_case_page_has_record_above_current_step():
    from pathlib import Path
    import kareshafaa
    js = (Path(kareshafaa.__file__).parent / "static" / "js" / "case.js").read_text()
    assert js.index("${record()}") < js.index('<dl class="facts" aria-labelledby="step-h">')
    assert "None recorded" in js and "overdue by" in js and "demo" not in js.lower()
