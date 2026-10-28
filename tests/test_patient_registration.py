"""Patient registration, search, record editing and duplicate checks (staff side)."""
from __future__ import annotations

import itertools
import random

import pytest

from kareshafaa.services import patients as svc

_n = itertools.count(1)


def eid(year: int = 1980) -> str:
    body = f"784{year}{random.randint(0, 9_999_999):07d}"
    return f"{body[:3]}-{body[3:7]}-{body[7:14]}-{svc.emirates_id_check_digit(body)}"


def _letters(i: int) -> str:
    out = ""
    i += random.randint(1000, 10**7)
    while i:
        i, r = divmod(i, 26)
        out += "bcdfghjklmnpqrstvwxyzaeiou"[r]
    return out.capitalize()


def person(**kw) -> dict:
    i = next(_n)
    base = {"given_name": _letters(i * 7), "family_name": _letters(i), "birth_date": "1978-03-1" + str(i % 9),
            "gender": "female", "national_id": eid(1978), "nationality": "United Arab Emirates", "language": "ar",
            "phone": "050 123 4567", "email": f"m{i}@example.ae", "address_line": "Villa 12, Street 4", "city": "Khalifa City",
            "emirate": "Abu Dhabi", "region": "Abu Dhabi", "preferred_channel": "sms", "whatsapp": True,
            "has_smartphone": False, "managing_org_id": 3, "gp_id": None}
    base.update(kw)
    return base


def reg(client, h, **kw):
    return client.post("/api/patients", headers=h, json=person(**kw))


# ---------- validation rules ----------
def test_emirates_id_checksum_and_format():
    good = eid()
    assert svc.normalise_emirates_id(good.replace("-", "")) == good
    assert svc.normalise_emirates_id(" " + good + " ") == good
    bad = good[:-1] + str((int(good[-1]) + 1) % 10)
    for v in (bad, "123-1980-1234567-1", "784-1980-123456-1", "784-3020-1234567-1", "784-1980-12a4567-1"):
        with pytest.raises(svc.Invalid):
            svc.normalise_emirates_id(v)
    assert svc.normalise_emirates_id("") is None


def test_phone_email_and_dob_rules():
    assert svc.normalise_phone("050 123 4567") == "+971501234567"
    assert svc.normalise_phone("+971 50 123 4567") == "+971501234567"
    assert svc.normalise_phone("00971501234567") == "+971501234567"
    assert svc.normalise_phone("+44 20 7946 0958") == "+442079460958"
    for v in ("12345", "05012", "abc", "0401234567x"):
        with pytest.raises(svc.Invalid):
            svc.normalise_phone(v)
    with pytest.raises(svc.Invalid):
        svc.validate_email("not-an-email")
    from datetime import date
    with pytest.raises(svc.Invalid):
        svc.validate_birth_date(date(2999, 1, 1))
    with pytest.raises(svc.Invalid):
        svc.validate_birth_date(date(1890, 1, 1))
    assert svc.age_band(45) == "40-49"


def test_name_similarity_handles_prefixes_and_order():
    assert svc.name_similarity("Mariam Al Qasimi", "Maryam Qasimi") >= 0.82
    assert svc.name_similarity("Fatima Hassan", "Hassan Fatima") >= 0.95
    assert svc.name_similarity("Fatima Hassan", "John Smith") < 0.5


# ---------- API ----------
def test_register_persists_and_derives_compat_fields(client, as_user, db):
    h = as_user("aisha.rahman")
    r = reg(client, h, birth_date="1975-06-30", nationality="India")
    assert r.status_code == 200, r.text
    b = r.json()
    assert b["mrn"].startswith("KS-") and b["patient_id"]
    rec = client.get(f"/api/patients/{b['patient_id']}", headers=h).json()
    d, c = rec["demographics"], rec["contact"]
    assert d["birth_date"] == "1975-06-30" and d["birth_year"] == 1975 and d["nationality"] == "India"
    assert c["phone"] == "+971501234567" and c["phone_valid"] and c["preferred_channel"] == "sms" and c["whatsapp"]
    assert rec["registration"]["by"] and rec["registration"]["source"] == "staff_form" and not rec["sample"]
    from kareshafaa.models import AuditEvent, Patient
    p = db.get(Patient, b["patient_id"])
    assert p.age_band in ("40-49", "50-59") and p.nationality_group == "Indian" and p.synthetic is False
    assert db.query(AuditEvent).filter_by(entity_type="Patient", entity_id=p.id, event="patient.registered").count() == 1


def test_register_validation_errors_are_clear(client, as_user):
    h = as_user("aisha.rahman")
    for kw, frag in [({"national_id": "784-1980-1234567-0" if svc.emirates_id_check_digit("78419801234567") != 0 else "784-1980-1234567-1"}, "check digit"),
                     ({"phone": "12"}, "phone"), ({"email": "x@"}, "email"), ({"emirate": "Narnia"}, "emirate"),
                     ({"region": None}, "region"), ({"birth_date": "2999-01-01"}, "birth"), ({"managing_org_id": 9}, "health centre"),
                     ({"preferred_channel": "sms", "phone": None}, "phone"), ({"mrn": "SYN-000001"}, "SYN-")]:
        r = reg(client, h, **kw)
        assert r.status_code == 422, (kw, r.text)
        assert frag.lower() in r.text.lower(), (kw, r.text)


def test_other_emirate_sets_region(client, as_user):
    h = as_user("aisha.rahman")
    r = reg(client, h, emirate="Sharjah", region=None)
    assert r.status_code == 200, r.text
    rec = client.get(f"/api/patients/{r.json()['patient_id']}", headers=h).json()
    assert rec["contact"]["emirate"] == "Sharjah" and rec["contact"]["region"] == "Sharjah"


def test_duplicate_emirates_id_blocked_and_audited(client, as_user, db):
    h = as_user("aisha.rahman")
    e = eid(1966)
    assert reg(client, h, national_id=e).status_code == 200
    r = reg(client, h, national_id=e.replace("-", ""), given_name="Other", family_name="Person", birth_date="1990-01-01",
            create_anyway_reason="I am sure this is someone else")
    assert r.status_code == 409 and r.json()["code"] == "duplicate_emirates_id" and r.json()["candidates"]
    from kareshafaa.models import AuditEvent
    assert db.query(AuditEvent).filter_by(event="patient.register_blocked").count() >= 1


def test_similar_name_dob_needs_reason_and_is_audited(client, as_user, db):
    h = as_user("omar.haddad")
    first = reg(client, h, given_name="Noura", family_name="Al Shamsi Zq", birth_date="1981-02-03", national_id=None)
    assert first.status_code == 200, first.text
    again = dict(given_name="Nora", family_name="Shamsi Zq", birth_date="1981-02-03", national_id=None)
    r = reg(client, h, **again)
    assert r.status_code == 409 and r.json()["code"] == "possible_duplicate"
    assert any(c["id"] == first.json()["patient_id"] for c in r.json()["candidates"])
    assert reg(client, h, **again, create_anyway_reason="short").status_code in (409, 422)
    ok = reg(client, h, **again, create_anyway_reason="Different mother's name and phone - checked with the patient")
    assert ok.status_code == 200, ok.text
    from kareshafaa.models import AuditEvent
    ev = db.query(AuditEvent).filter_by(event="patient.duplicate_override", entity_id=ok.json()["patient_id"]).one()
    assert "mother" in str(ev.detail)


def test_search_and_edit_sections(client, as_user):
    h = as_user("aisha.rahman")
    pid = reg(client, h, given_name="Searchable", family_name="Uniquename", national_id=None).json()["patient_id"]
    s = client.get("/api/patients?q=uniquenam", headers=h).json()
    assert any(x["id"] == pid for x in s["items"])
    assert client.get("/api/patients?q=a", headers=h).json()["items"] == []  # 2+ characters
    r = client.post(f"/api/patients/{pid}/contact", headers=h,
                    json={"phone": "+971 52 765 4321", "email": "new@example.ae", "address_line": "Flat 3", "city": "Al Ain City",
                          "emirate": "Abu Dhabi", "region": "Al Ain", "preferred_channel": "whatsapp", "whatsapp": True, "has_smartphone": True})
    assert r.status_code == 200, r.text
    rec = client.get(f"/api/patients/{pid}", headers=h).json()
    assert rec["contact"]["phone"] == "+971527654321" and rec["contact"]["region"] == "Al Ain"
    assert any(c["summary"] and "phone" in c["fields"] for c in rec["changes"])
    bad = client.post(f"/api/patients/{pid}/demographics", headers=h, json={"national_id": "784-1980-0000000-9"})
    assert bad.status_code in (409, 422)
    assert client.post(f"/api/patients/{pid}/care", headers=h, json={"managing_org_id": 4, "gp_id": None}).status_code == 200
    assert client.post(f"/api/patients/{pid}/contact", headers=h, json={"unknown": 1}).status_code == 422


def test_rbac_who_can_register_and_read(client, as_user):
    body = person()
    for u in ("admin", "fatima.al.mansoori", "vikram.mehta", "analyst", "translator", "patient.en"):
        assert client.post("/api/patients", headers=as_user(u), json=body).status_code == 403, u
    pid = reg(client, as_user("aisha.rahman")).json()["patient_id"]
    for u in ("admin", "analyst", "translator", "patient.en"):
        assert client.get(f"/api/patients/{pid}", headers=as_user(u)).status_code == 403, u
        assert client.get("/api/patients?q=uniquename", headers=as_user(u)).status_code == 403, u
    # a specialist sees a patient only through a referral / case (need to know)
    assert client.get(f"/api/patients/{pid}", headers=as_user("vikram.mehta")).status_code == 403
    for u in ("fatima.al.mansoori",):  # programme-wide, read-only
        rec = client.get(f"/api/patients/{pid}", headers=as_user(u))
        assert rec.status_code == 200 and rec.json()["can_write"] is False, u
        assert client.post(f"/api/patients/{pid}/contact", headers=as_user(u), json={"email": "x@example.ae"}).status_code == 403


def test_register_from_unmatched_result(client, as_user, db):
    h = as_user("aisha.rahman")
    e = eid(1972)
    msg = "\r".join([
        "MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|T-REG-FROM-1|P|2.5.1",
        f"PID|1||LAB-77812^^^LAB^MR~{e.replace('-', '')}^^^ICA^NI||HAMMADI^SALMA^K||19720405|F|||||^PRN^CP^^^^^^^^^0501112233",
        "OBX|1|NM|29771-3^FIT^LN||260|ug/g|||||F"])
    r = client.post("/api/ingest/hl7v2", headers={**h, "Content-Type": "text/plain"}, content=msg).json()
    assert r["status"] == "needs_match_review"
    mid = r["id"]
    lst = client.get("/api/ingest/messages", headers=h).json()["items"]
    row = next(m for m in lst if m["id"] == mid)
    assert row["patient_name"] == "SALMA K HAMMADI" and row["patient_birth_date"] == "1972-04-05"
    pre = client.get(f"/api/patients/prefill?message_id={mid}", headers=h).json()["values"]
    assert pre["family_name"] == "HAMMADI" and pre["birth_date"] == "1972-04-05" and pre["gender"] == "female"
    assert pre["national_id"] == e and pre["mrn"] == "LAB-77812" and pre["phone"] == "0501112233"
    body = person(**{k: pre[k] for k in ("given_name", "family_name", "birth_date", "gender", "national_id", "mrn", "phone")},
                  from_message_id=mid, consent_care_coordination="permit")
    out = client.post("/api/patients", headers=h, json=body)
    assert out.status_code == 200, out.text
    assert out.json()["case_id"]
    rec = client.get(f"/api/patients/{out.json()['patient_id']}", headers=h).json()
    assert rec["registration"]["source"] == "result_import" and rec["cases"] and rec["mrn"] == "LAB-77812"
    assert any(c["scope"] == "care_coordination" and c["decision"] == "permit" for c in rec["consents"])
    # the message is resolved: a second registration from it is refused
    assert client.post("/api/patients", headers=h, json={**body, "national_id": eid(), "mrn": None}).status_code == 422
    assert client.get(f"/api/patients/prefill?message_id={mid}", headers=h).status_code == 422


def test_fhir_demographics_parsed():
    from kareshafaa.services.ingest import parse_fhir_bundle
    b = {"resourceType": "Bundle", "id": "b1", "entry": [
        {"resource": {"resourceType": "Patient", "identifier": [{"value": "X1"}], "birthDate": "1969-11-02", "gender": "male",
                      "name": [{"family": "Rahim", "given": ["Yousef"]}], "telecom": [{"system": "phone", "value": "+971501234567"}, {"system": "email", "value": "y@x.ae"}]}},
        {"resource": {"resourceType": "Observation", "code": {"coding": [{"code": "29771-3"}]}, "valueQuantity": {"value": 10, "unit": "ug/g"}}}]}
    r = parse_fhir_bundle(b)
    assert r.demographics == {"family_name": "Rahim", "given_name": "Yousef", "birth_date": "1969-11-02", "gender": "male",
                              "phone": "+971501234567", "email": "y@x.ae"}


def test_pages_render_for_allowed_roles(client, as_user):
    pid = reg(client, as_user("aisha.rahman")).json()["patient_id"]
    for path in ("/patients", "/patients/new", f"/patients/{pid}"):
        r = client.get(path)
        assert r.status_code in (200, 302, 303, 307), path
