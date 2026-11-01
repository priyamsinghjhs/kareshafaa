"""Identity document type on registration and edit: Emirates ID (check digit), passport, GCC national ID, other."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from kareshafaa.fhir import export_case
from kareshafaa.models import AuditEvent, Case, Patient
from kareshafaa.services import patients as svc
from kareshafaa.services.errors import Invalid

from tests.test_patient_registration import eid, person

CO = "aisha.rahman"


def canadian(**kw):
    base = dict(nationality="Canada", language="en", national_id=None, id_type="passport", id_number="ab 123456",
                phone="+1 416 555 0199")
    base.update(kw)
    return person(**base)


def test_document_number_rules():
    assert svc.normalise_document_number("ab-12 3456") == "AB123456"
    for bad in ("AB12", "A" * 21, "AB12/345", "AB12_345", "ÄB12345"):
        with pytest.raises(Invalid):
            svc.normalise_document_number(bad)
    assert svc.normalise_document_number("  ") is None


def test_register_canadian_with_passport_country_defaults_to_nationality(client, as_user, db):
    h = as_user(CO)
    r = client.post("/api/patients", headers=h, json=canadian())
    assert r.status_code == 200, r.text
    pid = r.json()["patient_id"]
    p = db.get(Patient, pid)
    assert (p.id_type, p.id_number, p.id_country, p.national_id) == ("passport", "AB123456", "Canada", None)
    assert p.phone == "+14165550199" and p.mrn.startswith("KS-")
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["demographics"]
    assert rec["id_type"] == "passport" and rec["id_number"] == "AB123456" and rec["other_document"]["label"] == "Passport (Canada)"
    found = client.get("/api/patients?q=AB123456", headers=h).json()["items"]
    assert any(x["id"] == pid and x["document"]["label"] == "Passport (Canada)" and x["document"]["number"].endswith("3456")
               and "AB12" not in x["document"]["number"] for x in found)


def test_document_validation_matches_ui_rules(client, as_user):
    h = as_user(CO)
    cases = [
        (canadian(id_number="X1"), "5 to 20"),
        (person(id_type=None, id_number="AB123456", national_id=None), "document type"),
        (person(nationality="Other", national_id=None, id_type="passport", id_number="ZZ998877"), "issuing country"),
        (person(national_id=None, id_type="gcc_id", id_number="1234567890", id_country="Canada"), "GCC"),
        (person(national_id=None, id_type="emirates_id", id_number="784-1980-0000000-9"), "Emirates ID"),
        (person(national_id=None, id_type="passport", id_number="AB123456", id_country="Atlantis"), "country"),
    ]
    for body, msg in cases:
        r = client.post("/api/patients", headers=h, json=body)
        assert r.status_code == 422 and msg.lower() in r.text.lower(), (body.get("id_number"), r.text)


def test_emirates_id_via_type_keeps_national_id_and_check_digit(client, as_user, db):
    h = as_user(CO)
    e = eid(1978)
    r = client.post("/api/patients", headers=h, json=person(national_id=None, id_type="emirates_id", id_number=e.replace("-", "")))
    assert r.status_code == 200, r.text
    p = db.get(Patient, r.json()["patient_id"])
    assert p.national_id == e and p.id_type == "emirates_id" and p.id_number is None
    again = client.post("/api/patients", headers=h, json=person(national_id=None, id_type="emirates_id", id_number=e))
    assert again.status_code == 409 and again.json()["code"] == "duplicate_emirates_id"
    r2 = client.post("/api/patients", headers=h, json=person(national_id=eid(1981)))  # older API field still works
    assert r2.status_code == 200 and db.get(Patient, r2.json()["patient_id"]).id_type == "emirates_id"


def test_same_passport_needs_audited_override(client, as_user, db):
    h = as_user(CO)
    assert client.post("/api/patients", headers=h, json=canadian(id_number="CA7766554")).status_code == 200
    other = canadian(id_number="ca-7766554")  # a different name / birth date, same passport
    r = client.post("/api/patients", headers=h, json=other)
    assert r.status_code == 409 and r.json()["code"] == "possible_duplicate"
    assert any("passport" in c["why"].lower() for c in r.json()["candidates"])
    # the same number from another country is a different document
    assert client.post("/api/patients", headers=h, json=canadian(id_number="CA7766554", id_country="Indonesia")).status_code == 200
    r = client.post("/api/patients", headers=h, json={**other, "create_anyway_reason": "Twin siblings, passports checked in person"})
    assert r.status_code == 200, r.text
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "patient.duplicate_override",
                                              AuditEvent.entity_id == str(r.json()["patient_id"])))


def test_edit_switch_document_keeps_emirates_id_and_warns_on_shared_number(client, as_user, db):
    h = as_user(CO)
    e = eid(1979)
    pid = client.post("/api/patients", headers=h, json=person(national_id=e)).json()["patient_id"]
    r = client.post(f"/api/patients/{pid}/demographics", headers=h,
                    json={"id_type": "passport", "id_number": "P4455667", "id_country": "Jordan"})
    assert r.status_code == 200, r.text
    db.expire_all()
    p = db.get(Patient, pid)
    assert (p.id_type, p.id_number, p.id_country, p.national_id) == ("passport", "P4455667", "Jordan", e)
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["demographics"]
    assert rec["national_id"] == e and rec["other_document"]["number"] == "P4455667"
    client.post(f"/api/patients/{pid}/demographics", headers=h, json={"id_type": "passport", "id_number": None})
    db.expire_all()
    assert (db.get(Patient, pid).id_type, db.get(Patient, pid).id_number) == ("emirates_id", None)
    assert client.post("/api/patients", headers=h, json=canadian(id_number="DUP12345")).status_code == 200
    r = client.post(f"/api/patients/{pid}/demographics", headers=h,
                    json={"id_type": "passport", "id_number": "DUP12345", "id_country": "Canada"})
    assert r.status_code == 200 and any("same passport" in w.lower() for w in r.json()["warnings"])
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "patient.duplicate_document",
                                              AuditEvent.entity_id == str(pid)))


def test_international_phone_numbers_accepted():
    assert svc.normalise_phone("+62 812 3456 7890") == "+6281234567890"
    assert svc.normalise_phone("+1 (416) 555-0199") == "+14165550199"
    assert svc.normalise_phone("0062 812 3456 789") == "+628123456789"
    with pytest.raises(Invalid):
        svc.normalise_phone("812 3456 7890")  # a foreign number without + is ambiguous


def test_fhir_patient_identifiers(db, new_case):
    c = db.get(Case, new_case(value=250)["id"])
    p = c.patient
    p.national_id, p.id_type, p.id_number, p.id_country = eid(1970), "passport", "CA1234567", "Canada"
    db.commit()
    pat = next(r for r in export_case(db, c, include_audit=False)["entry"] if r["resource"]["resourceType"] == "Patient")["resource"]
    codes = {i["type"]["coding"][0]["code"]: i for i in pat["identifier"] if i["type"].get("coding")}
    assert codes["MR"]["value"] == p.mrn and codes["NI"]["value"] == p.national_id
    assert codes["PPN"]["value"] == "CA1234567" and codes["PPN"]["assigner"]["display"] == "Canada"


def test_page_offers_document_types(client, as_user):
    o = client.get("/api/patients/options", headers=as_user(CO)).json()
    assert list(o["id_types"]) == ["emirates_id", "passport", "gcc_id", "other"] and "Oman" in o["gcc_countries"]
    js = client.get("/static/js/patients.js").text
    assert 'inputmode="numeric" pattern="[0-9-]*"' in js and 'autocapitalize="characters"' in js
