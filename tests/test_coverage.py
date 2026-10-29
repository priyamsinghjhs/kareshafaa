"""Insurance (payer) details, eligibility warning, next of kin / guardian and proxy consent (services/coverage.py)."""
from __future__ import annotations

from datetime import date, timedelta

from kareshafaa.clinical_rules import INSURANCE_EXPIRY_WARN_DAYS
from kareshafaa.models import AuditEvent, Consent, Patient
from kareshafaa.services import coverage

from test_patient_registration import person, reg

COORD = "aisha.rahman"


def _today(n=0):
    return (date.today() + timedelta(days=n)).isoformat()


def test_eligibility_states():
    p = Patient(insurance_payer=None)
    assert coverage.eligibility(p)["status"] == "missing"
    p.insurance_payer = "self_pay"
    assert coverage.eligibility(p)["status"] == "self_pay" and coverage.eligibility(p)["warn"]
    p.insurance_payer, p.insurance_valid_to = "thiqa", None
    assert coverage.eligibility(p)["status"] == "missing"
    p.insurance_valid_to = date.today() - timedelta(days=1)
    assert coverage.eligibility(p)["status"] == "expired"
    p.insurance_valid_to = date.today() + timedelta(days=INSURANCE_EXPIRY_WARN_DAYS)
    assert coverage.eligibility(p)["status"] == "expiring"
    p.insurance_valid_to = date.today() + timedelta(days=INSURANCE_EXPIRY_WARN_DAYS + 1)
    e = coverage.eligibility(p)
    assert e["status"] == "ok" and not e["warn"]


def test_insurance_edit_validates_persists_and_audits(client, as_user, db):
    h = as_user(COORD)
    pid = reg(client, h).json()["patient_id"]
    rec = client.get(f"/api/patients/{pid}", headers=h).json()
    assert rec["insurance"]["eligibility"]["status"] == "missing" and rec["insurance"]["eligibility"]["warn"]
    url = f"/api/patients/{pid}/insurance"
    assert client.post(url, headers=h, json={"insurance_payer": "acme"}).status_code == 422
    r = client.post(url, headers=h, json={"insurance_payer": "daman_enhanced", "insurance_member_id": ""})
    assert r.status_code == 422 and "member" in r.json()["detail"].lower()
    assert client.post(url, headers=h, json={"insurance_payer": "thiqa", "insurance_member_id": "x"}).status_code == 422
    assert client.post(url, headers=h, json={"insurance_payer": "thiqa", "insurance_member_id": "THQ-1234567",
                                             "insurance_valid_to": "1999-12-31"}).status_code == 422
    assert client.post(url, headers=h, json={"insurance_valid_to": "31/12/2030"}).status_code == 422
    r = client.post(url, headers=h, json={"insurance_payer": "thiqa", "insurance_plan": "Thiqa Gold",
                                          "insurance_member_id": "THQ-1234567", "insurance_valid_to": _today(-3)})
    assert r.status_code == 200, r.text
    assert r.json()["eligibility"]["status"] == "expired"
    rec = client.get(f"/api/patients/{pid}", headers=h).json()["insurance"]
    assert rec["insurance_payer"] == "thiqa" and rec["insurance_member_id"] == "THQ-1234567"
    assert rec["insurance_valid_to"] == _today(-3) and rec["eligibility"]["warn"]
    r = client.post(url, headers=h, json={"insurance_valid_to": _today(400)})
    assert r.json()["eligibility"]["status"] == "ok" and r.json()["changed"] == ["insurance_valid_to"]
    a = db.query(AuditEvent).filter(AuditEvent.event == "patient.insurance_updated", AuditEvent.entity_id == str(pid)).all()
    assert len(a) == 2 and "insurance_member_id" in a[0].detail["fields"]
    # self-pay clears policy details
    client.post(url, headers=h, json={"insurance_payer": "self_pay"})
    db.expire_all()
    p = db.get(Patient, pid)
    assert p.insurance_member_id is None and p.insurance_valid_to is None
    assert client.post(url, headers=h, json={"unknown": 1}).status_code == 422


def test_next_of_kin_guardian_and_proxy_consent(client, as_user, db):
    h = as_user(COORD)
    pid = reg(client, h).json()["patient_id"]
    url = f"/api/patients/{pid}/contacts"
    assert client.post(url, headers=h, json={"nok_relationship": "uncle"}).status_code == 422
    assert client.post(url, headers=h, json={"nok_name": "Sara", "nok_phone": "12ab"}).status_code == 422
    assert client.post(url, headers=h, json={"nok_is_guardian": True}).status_code == 422  # a guardian needs a name
    r = client.post(url, headers=h, json={"nok_name": "Sara Al Hammadi", "proxy_consent": "permit",
                                          "proxy_note": "In person at the clinic"})
    assert r.status_code == 422 and "phone" in r.json()["detail"]
    r = client.post(url, headers=h, json={"nok_name": "Sara Al Hammadi", "nok_relationship": "child",
                                          "nok_phone": "050 765 4321", "nok_is_guardian": True,
                                          "proxy_consent": "permit", "proxy_note": "short"})
    assert r.status_code == 422  # consent on the patient's behalf needs how it was given (10+ characters)
    db.rollback()
    r = client.post(url, headers=h, json={"nok_name": "Sara Al Hammadi", "nok_relationship": "child",
                                          "nok_phone": "050 765 4321", "nok_is_guardian": True,
                                          "proxy_consent": "permit", "proxy_note": "In person at the clinic, with interpreter"})
    assert r.status_code == 200, r.text
    c = client.get(f"/api/patients/{pid}", headers=h).json()["contacts"]
    assert c["nok_phone"] == "+971507654321" and c["nok_is_guardian"] is True and c["proxy_consent"] == "permit"
    assert c["relationship_label"] == "Son / daughter"
    r = client.post(url, headers=h, json={"proxy_consent": "deny", "proxy_note": "Patient changed their mind by phone"})
    assert r.status_code == 200 and "proxy_consent" in r.json()["changed"]
    rows = db.query(Consent).filter(Consent.patient_id == pid, Consent.scope == "proxy_contact").order_by(Consent.id).all()
    assert [x.status for x in rows] == ["inactive", "active"] and rows[-1].decision == "deny"
    assert db.query(AuditEvent).filter(AuditEvent.event == "consent.recorded").filter(
        AuditEvent.detail["scope"].as_string() == "proxy_contact").count() >= 2


def test_registration_takes_insurance_and_next_of_kin(client, as_user, db):
    h = as_user(COORD)
    bad = client.post("/api/patients", headers=h, json=person(insurance_payer="thiqa"))
    assert bad.status_code == 422  # member number needed; nothing written
    body = person(insurance_payer="daman_basic", insurance_member_id="DMN 55-77", insurance_valid_to=_today(10),
                  nok_name="Omar Saeed", nok_relationship="spouse", nok_phone="+971 55 111 2233")
    r = client.post("/api/patients", headers=h, json=body)
    assert r.status_code == 200, r.text
    rec = client.get(f"/api/patients/{r.json()['patient_id']}", headers=h).json()
    assert rec["insurance"]["eligibility"]["status"] == "expiring"
    assert rec["contacts"]["nok_name"] == "Omar Saeed" and rec["contacts"]["proxy_consent"] is None


def test_rbac_only_patient_writers(client, as_user):
    pid = reg(client, as_user(COORD)).json()["patient_id"]
    for u in ("fatima.al.mansoori", "vikram.mehta", "analyst", "translator", "admin", "patient.en"):
        assert client.post(f"/api/patients/{pid}/insurance", headers=as_user(u),
                           json={"insurance_payer": "self_pay"}).status_code == 403, u
        assert client.post(f"/api/patients/{pid}/contacts", headers=as_user(u),
                           json={"nok_name": "Test Person"}).status_code == 403, u


def test_case_screen_shows_eligibility_and_next_of_kin(client, as_user, new_case, db):
    c = new_case(value=250)
    h = as_user(COORD)
    d = client.get(f"/api/cases/{c['id']}", headers=h).json()
    assert d["coverage"]["eligibility"]["status"] == "missing" and d["coverage"]["nok"] is None
    pid = d["patient"]["id"]
    client.post(f"/api/patients/{pid}/contacts", headers=h, json={"nok_name": "Mariam Test", "nok_phone": "0501112222"})
    d = client.get(f"/api/cases/{c['id']}", headers=h).json()
    assert d["coverage"]["nok"]["name"] == "Mariam Test"
