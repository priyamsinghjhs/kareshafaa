"""Case privacy on patient-level views: a reader who may open the patient record (through one case) but not every
case of that patient sees ONLY the cases they may open. The others are omitted completely - no id, dates,
programme, step, tier or clinician - and a neutral flag says other care episodes exist."""
from __future__ import annotations

import json

from sqlalchemy import select

from kareshafaa.models import (Case, Encounter, Investigation, MedicationRequest, Practitioner, ServiceRequest, User,
                               utcnow)


def _two_case_patient(client, as_user, make_patient, db):
    """One patient, two open cases. Case A is referred to the specialist's hospital; case B is not."""
    pid = make_patient()
    co = as_user("aisha.rahman")
    ids = []
    for prog, val, unit in (("colorectal_fit", 250, "ug/g"), ("diabetes_hba1c", 9.5, "%")):
        r = client.post("/api/results", headers=co, json={"patient_id": pid, "programme": prog, "value": val,
                                                          "unit": unit})
        assert r.status_code == 200 and r.json()["actionable"], r.text
        ids.append(r.json()["case"]["id"])
    spec = db.scalar(select(User).where(User.username == "vikram.mehta"))
    org = db.get(Practitioner, spec.practitioner_id).org_id
    a, b = db.get(Case, ids[0]), db.get(Case, ids[1])
    db.add(ServiceRequest(case_id=a.id, kind="referral", code="colonoscopy", requester_id=a.owner_id,
                          performer_org_id=org, status="requested"))
    # clinical notes / medicines / investigations recorded under the hidden case B
    db.add(Encounter(patient_id=pid, case_id=b.id, kind="gp_visit", start=utcnow(), notes="HIDDEN-NOTE-B",
                     source="kareshafaa"))
    db.add(MedicationRequest(patient_id=pid, case_id=b.id, name="HIDDEN-MED-B", status="active", source="kareshafaa"))
    db.add(Investigation(patient_id=pid, case_id=b.id, name="HIDDEN-INV-B", value="1", flag="N",
                         effective_at=utcnow(), source="kareshafaa"))
    db.commit()
    return pid, a.id, b.id


def _no_trace(doc, case_id: int) -> None:
    txt = json.dumps(doc)
    assert f'"case_id": {case_id}' not in txt and f'/cases/{case_id}"' not in txt


def test_patient_record_omits_cases_the_reader_cannot_open(client, as_user, make_patient, db):
    pid, a, b = _two_case_patient(client, as_user, make_patient, db)
    r = client.get(f"/api/patients/{pid}", headers=as_user("vikram.mehta"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert [c["id"] for c in d["cases"]] == [a]
    assert all(c["can_open"] for c in d["cases"])
    assert d["other_care_episodes"] is True and "care team" in d["other_care_episodes_note"]
    # nothing about case B: no id, dates, type, step, tier or results linked to it
    assert all(x.get("case_id") != b for x in d["results"])
    assert all(x.get("source_case_id") != b and x.get("completed_case_id") != b for x in d["recalls"])
    _no_trace(d, b)
    assert "diabetes" not in json.dumps(d["cases"]) and "hidden_cases" not in d
    # a reader who may open every case sees both and no flag
    full = client.get(f"/api/patients/{pid}", headers=as_user("aisha.rahman")).json()
    assert {c["id"] for c in full["cases"]} == {a, b} and full["other_care_episodes"] is False
    assert full["other_care_episodes_note"] is None


def test_case_record_and_fhir_drop_rows_from_hidden_cases(client, as_user, make_patient, db):
    pid, a, b = _two_case_patient(client, as_user, make_patient, db)
    sp = as_user("vikram.mehta")
    rec = client.get(f"/api/cases/{a}/record", headers=sp)
    assert rec.status_code == 200, rec.text
    txt = rec.text
    assert "HIDDEN-NOTE-B" not in txt and "HIDDEN-MED-B" not in txt and "HIDDEN-INV-B" not in txt
    assert [c["id"] for c in rec.json()["cases"]] == [a] and rec.json()["hidden_cases"] == 1
    fx = client.get(f"/api/cases/{a}/fhir", headers=sp)
    assert fx.status_code == 200, fx.text
    assert "HIDDEN-NOTE-B" not in fx.text and "HIDDEN-MED-B" not in fx.text and "HIDDEN-INV-B" not in fx.text
    # the coordinator (programme-wide) still gets the patient's full record in the export
    fc = client.get(f"/api/cases/{a}/fhir", headers=as_user("aisha.rahman"))
    assert "HIDDEN-NOTE-B" in fc.text and "HIDDEN-MED-B" in fc.text
    # the hidden case itself stays closed to the specialist
    assert client.get(f"/api/cases/{b}", headers=sp).status_code == 403


def test_patient_search_lists_only_visible_patients_without_case_details(client, as_user, make_patient, db):
    pid, a, b = _two_case_patient(client, as_user, make_patient, db)
    from kareshafaa.models import Patient
    p = db.get(Patient, pid)
    r = client.get("/api/patients", params={"q": p.mrn}, headers=as_user("vikram.mehta"))
    assert r.status_code == 200
    row = next(x for x in r.json()["items"] if x["id"] == pid)
    assert set(row) >= {"open_cases"} and "cases" not in row  # a neutral count only: no case metadata

