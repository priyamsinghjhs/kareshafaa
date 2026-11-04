"""Patient summary: contact details (phone, email, address, language, contact consent) and an Insurance tile (FHIR
Coverage) on the case screen and in the patient app; FHIR export; SYN- sample backfill; role-based masking."""
from __future__ import annotations

from datetime import date, timedelta

import pytest

from kareshafaa.security import Actor


def _case_and_patient(client, h):
    items = client.get("/api/queue?view=all&limit=50", headers=h).json()["items"]
    assert items
    c = client.get(f"/api/cases/{items[0]['id']}", headers=h).json()
    return items[0]["id"], c["patient"]["id"]


@pytest.fixture()
def insured(client, as_user):
    """A case the GP sees, whose patient gets known insurance and contact details; restored afterwards."""
    h = as_user("aisha.rahman")
    cid, pid = _case_and_patient(client, as_user("omar.haddad"))
    before = client.get(f"/api/patients/{pid}", headers=h).json()
    ins0 = {k: before["insurance"][k] for k in ("insurance_payer", "insurance_plan", "insurance_member_id",
                                                "insurance_valid_to", "insurance_preauth_required")}
    r = client.post(f"/api/patients/{pid}/insurance", headers=h, json={
        "insurance_payer": "daman_enhanced", "insurance_plan": "Enhanced - Silver network (synthetic)",
        "insurance_member_id": "SYN-DME-0012345", "insurance_valid_to": (date.today() + timedelta(days=200)).isoformat(),
        "insurance_preauth_required": True})
    assert r.status_code == 200, r.text
    yield cid, pid
    client.post(f"/api/patients/{pid}/insurance", headers=h, json=ins0)


def test_case_summary_shows_contact_and_insurance_for_contacting_roles(client, as_user, insured):
    cid, _pid = insured
    for user, masked in (("aisha.rahman", False), ("omar.haddad", False), ("fatima.al.mansoori", True)):
        S = client.get(f"/api/cases/{cid}/record", headers=as_user(user)).json()["summary"]
        K, I = S["contact"], S["insurance"]
        assert not K.get("hidden") and {"phone", "email", "address", "consent", "channels_refused"} <= set(K), user
        assert S["language"] and S["contact_preference"]
        assert I["on_file"] and I["payer"] == "Daman Enhanced" and I["status"] == "active" and I["preauth_required"] is True
        assert I["member_masked"] is masked, user
        assert (I["member"] == "•••• 2345") if masked else (I["member"] == "SYN-DME-0012345"), user


def test_analyst_and_admin_get_no_patient_contact_or_insurance(client, as_user, insured):
    cid, pid = insured
    for u in ("analyst", "admin"):
        assert client.get(f"/api/cases/{cid}/record", headers=as_user(u)).status_code == 403, u
        assert client.get(f"/api/patients/{pid}", headers=as_user(u)).status_code == 403, u


def test_summary_tile_masks_member_except_booking_roles_and_patient():
    from kareshafaa.models import Patient
    from kareshafaa.services.coverage import summary_tile

    p = Patient(mrn="SYN-X", given_name="A", family_name="B", gender="female", birth_year=1980, age_band="40-49",
                nationality_group="Emirati", language="ar", region="Abu Dhabi", managing_org_id=1, synthetic=True,
                insurance_payer="thiqa", insurance_plan="Thiqa (synthetic)", insurance_member_id="SYN-THQ-0000777",
                insurance_valid_to=date.today() - timedelta(days=3), insurance_preauth_required=False)
    for role, masked in (("coordinator", False), ("gp", False), ("patient", False), ("specialist", True),
                         ("clinical_lead", True)):
        t = summary_tile(p, Actor(user_id=1, role=role, display="x"))
        assert t["member_masked"] is masked and t["status"] == "expired" and t["preauth_required"] is False, role
        assert t["synthetic"] is True
    p.insurance_payer = None
    t = summary_tile(p, Actor(user_id=1, role="coordinator", display="x"))
    assert t["on_file"] is False and t["status"] == "none"


def test_patient_app_profile_has_own_insurance_tile_in_four_languages(client, as_user):
    from kareshafaa.comms.self_service_text import SELF_TEXT

    for lang in ("en", "ar", "hi", "ur"):
        d = client.get(f"/api/me/profile?lang={lang}", headers=as_user("patient.en")).json()
        assert "insurance" in d and "on_file" in d["insurance"]
        X = d["text"]["ins"]
        assert X == SELF_TEXT[lang]["ins"] and X["none"] and X["st"]["expired"]
    assert SELF_TEXT["ar"]["ins"]["title"] != SELF_TEXT["en"]["ins"]["title"]


def test_fhir_export_has_telecom_address_and_valid_coverage(client, as_user, insured):
    pytest.importorskip("fhir.resources")
    from fhir.resources.R4B import get_fhir_model_class

    cid, pid = insured
    b = client.get(f"/api/cases/{cid}/fhir", headers=as_user("fatima.al.mansoori")).json()
    res = {(e["resource"]["resourceType"], e["resource"]["id"]): e["resource"] for e in b["entry"]}
    cov = res[("Coverage", f"cov-{pid}")]
    assert cov["beneficiary"] == {"reference": f"Patient/{pid}"} and cov["subscriberId"] == "SYN-DME-0012345"
    assert cov["payor"][0]["reference"] == "Organization/payer-daman-enhanced"
    assert ("Organization", "payer-daman-enhanced") in res
    assert any(x["url"].endswith("preauthorisation-required") and x["valueBoolean"] is True for x in cov["extension"])
    patient = res[("Patient", str(pid))]
    assert patient["address"][0]["country"] == "AE"
    for r in (cov, res[("Organization", "payer-daman-enhanced")], patient):
        cls = get_fhir_model_class(r["resourceType"])
        cls.model_validate(r) if hasattr(cls, "model_validate") else cls.parse_obj(r)


def test_self_pay_coverage_is_valid_fhir():
    pytest.importorskip("fhir.resources")
    from fhir.resources.R4B import get_fhir_model_class

    from kareshafaa.fhir import coverage_resources
    from kareshafaa.models import Patient

    p = Patient(id=9, mrn="SYN-9", given_name="A", family_name="B", gender="male", birth_year=1970, age_band="50-59",
                nationality_group="Indian", language="hi", region="Al Ain", managing_org_id=1, synthetic=True,
                insurance_payer="self_pay")
    (cov,) = coverage_resources(p)
    get_fhir_model_class("Coverage").model_validate(cov)
    p.insurance_payer = None
    assert coverage_resources(p) == []


def test_backfill_fills_only_empty_fields_of_syn_sample_patients(db):
    from sqlalchemy import select

    from kareshafaa.models import Patient
    from kareshafaa.services import sample_data as sd

    assert "backfill_sample_contact_insurance_v1" in [n for n, _ in sd.MIGRATIONS]
    real = Patient(mrn="REAL-0001", given_name="Real", family_name="Person", gender="female", birth_year=1975,
                   age_band="50-59", nationality_group="Emirati", language="ar", region="Abu Dhabi", managing_org_id=1,
                   synthetic=False)
    fake_syn = Patient(mrn="SYN-REALFLAG", given_name="Not", family_name="Sample", gender="female", birth_year=1975,
                       age_band="50-59", nationality_group="Emirati", language="ar", region="Abu Dhabi",
                       managing_org_id=1, synthetic=False)  # SYN- prefix but not flagged synthetic: untouched
    db.add_all([real, fake_syn])
    kept = db.scalars(select(Patient).where(Patient.mrn.like("SYN-%"), Patient.synthetic.is_(True))).first()
    kept.email, kept.insurance_payer, kept.insurance_member_id = "kept@example.com", "adnic", "KEEP-1"
    db.flush()
    out = sd.backfill_sample_contact_insurance(db)
    assert out["patients"] > 0 and out["fields"].get("insurance_payer", 0) > 0
    for p in (real, fake_syn):
        assert (p.phone, p.email, p.address_line, p.insurance_payer) == (None, None, None, None), p.mrn
    assert (kept.email, kept.insurance_payer, kept.insurance_member_id) == ("kept@example.com", "adnic", "KEEP-1")
    samples = db.scalars(select(Patient).where(Patient.mrn.like("SYN-%"), Patient.synthetic.is_(True))).all()
    assert all(p.address_line and p.city for p in samples)
    phones = [p.phone for p in samples if p.phone]
    assert phones and len(phones) == len(set(phones)) and all(x.startswith("+9715") for x in phones)
    assert all(p.email.endswith("@example.com") for p in samples if p.email and p is not kept)
    insured = [p for p in samples if p.insurance_payer and p is not kept]
    assert insured and all("synthetic" in (p.insurance_plan or "") and p.insurance_member_id.startswith("SYN-")
                           for p in insured)
    assert {p.insurance_payer for p in insured if p.nationality_group == "Emirati"} <= {"thiqa"}
    assert sd.backfill_sample_contact_insurance(db)["fields"].get("insurance_payer", 0) == 0  # idempotent
    db.rollback()
