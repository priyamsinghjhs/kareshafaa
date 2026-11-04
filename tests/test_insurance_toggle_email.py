"""'Has insurance?' Yes / No: No hides and strips every policy field (form, server, summary, patient app, FHIR); Yes
requires an insurer and a member number. Staff and patients can edit the email (validated, audited, in FHIR)."""
from __future__ import annotations

import json
import shutil
import subprocess
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest

from kareshafaa.services.errors import Invalid

POLICY = ("insurance_payer", "insurance_plan", "insurance_member_id", "insurance_valid_to", "insurance_preauth_required")
NOW = datetime(2026, 11, 4, 9, 0)


def _ids(client, h):
    items = client.get("/api/queue?view=all&limit=50", headers=h).json()["items"]
    c = client.get(f"/api/cases/{items[1]['id']}", headers=h).json()
    return items[1]["id"], c["patient"]["id"]


@pytest.fixture()
def record(client, as_user):
    """A case the GP sees; its patient's insurance and email are put back afterwards."""
    h = as_user("aisha.rahman")
    cid, pid = _ids(client, as_user("omar.haddad"))
    before = client.get(f"/api/patients/{pid}", headers=h).json()
    ins0 = {k: before["insurance"][k] for k in POLICY}
    yield cid, pid, h
    client.post(f"/api/patients/{pid}/insurance", headers=h, json=ins0)
    client.post(f"/api/patients/{pid}/contact", headers=h, json={"email": before["contact"]["email"]})


# ------------------------------------------------------------------------------------------ server rules
def test_no_strips_every_policy_field_whatever_was_sent():
    from kareshafaa.services.coverage import validate_insurance

    v = validate_insurance({"has_insurance": False, "insurance_payer": "adnic", "insurance_plan": "Gold",
                            "insurance_member_id": "ADN-123456", "insurance_valid_to": "2027-01-01",
                            "insurance_preauth_required": True}, NOW)
    assert v == {"has_insurance": False, "insurance_payer": "self_pay", "insurance_plan": None,
                 "insurance_member_id": None, "insurance_valid_to": None, "insurance_preauth_required": None}
    assert validate_insurance({"has_insurance": "no"}, NOW)["insurance_payer"] == "self_pay"
    nr = validate_insurance({"has_insurance": None, "insurance_member_id": "X-1234"}, NOW)
    assert nr["has_insurance"] is None and all(nr[k] is None for k in POLICY)


def test_yes_requires_an_insurer_and_a_member_number():
    from kareshafaa.services.coverage import validate_insurance

    for bad in ({"has_insurance": True}, {"has_insurance": True, "insurance_member_id": "THQ-1234567"},
                {"has_insurance": True, "insurance_payer": "thiqa"},
                {"has_insurance": True, "insurance_payer": "self_pay", "insurance_member_id": "THQ-1234567"},
                {"has_insurance": "maybe", "insurance_payer": "thiqa", "insurance_member_id": "THQ-1234567"}):
        with pytest.raises(Invalid):
            validate_insurance(bad, NOW)
    ok = validate_insurance({"has_insurance": "yes", "insurance_payer": "thiqa", "insurance_member_id": "THQ-1234567"}, NOW)
    assert ok["has_insurance"] is True and ok["insurance_payer"] == "thiqa"
    # editing one field of an insured record keeps the current insurer / member number
    assert validate_insurance({"has_insurance": True, "insurance_plan": "Thiqa", "_current_payer": "thiqa",
                               "_current_member": "THQ-1"}, NOW)["insurance_plan"] == "Thiqa"


def test_api_no_then_yes_on_record_summary_and_fhir(client, as_user, record):
    pytest.importorskip("fhir.resources")
    from fhir.resources.R4B import get_fhir_model_class

    cid, pid, h = record
    url = f"/api/patients/{pid}/insurance"
    r = client.post(url, headers=h, json={"has_insurance": False, "insurance_payer": "adnic",
                                          "insurance_member_id": "ADN-999999", "insurance_plan": "Gold"})
    assert r.status_code == 200, r.text
    I = client.get(f"/api/patients/{pid}", headers=h).json()["insurance"]
    assert I["has_insurance"] is False and I["insurance_payer"] == "self_pay"
    assert (I["insurance_plan"], I["insurance_member_id"], I["insurance_valid_to"]) == (None, None, None)
    tile = client.get(f"/api/cases/{cid}/record", headers=h).json()["summary"]["insurance"]
    assert tile["state"] == "no" and tile["status"] == "self_pay" and tile["on_file"] is False
    assert not {"payer", "plan", "member", "valid_to", "preauth_required"} & set(tile)  # no empty policy fields
    b = client.get(f"/api/cases/{cid}/fhir", headers=as_user("fatima.al.mansoori")).json()
    covs = [e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Coverage"]
    assert len(covs) == 1 and covs[0]["payor"] == [{"reference": f"Patient/{pid}"}] and "subscriberId" not in covs[0]
    get_fhir_model_class("Coverage").model_validate(covs[0])
    assert not [e for e in b["entry"] if e["resource"]["id"].startswith("payer-")]

    # Yes needs an insurer and a member number (422, nothing saved)
    for body in ({"has_insurance": True}, {"has_insurance": True, "insurance_payer": "thiqa"}):
        assert client.post(url, headers=h, json=body).status_code == 422, body
    assert client.get(f"/api/patients/{pid}", headers=h).json()["insurance"]["has_insurance"] is False
    r = client.post(url, headers=h, json={"has_insurance": True, "insurance_payer": "thiqa",
                                          "insurance_member_id": "THQ-7654321",
                                          "insurance_valid_to": (date.today() + timedelta(days=300)).isoformat()})
    assert r.status_code == 200, r.text
    tile = client.get(f"/api/cases/{cid}/record", headers=h).json()["summary"]["insurance"]
    assert tile["state"] == "yes" and tile["payer"].startswith("Thiqa") and tile["member"] == "THQ-7654321"
    b = client.get(f"/api/cases/{cid}/fhir", headers=as_user("fatima.al.mansoori")).json()
    cov = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Coverage")
    assert cov["payor"][0]["reference"] == "Organization/payer-thiqa" and cov["subscriberId"] == "THQ-7654321"

    # never answered -> 'Insurance not recorded', no Coverage at all
    assert client.post(url, headers=h, json={"has_insurance": None}).status_code == 200
    tile = client.get(f"/api/cases/{cid}/record", headers=h).json()["summary"]["insurance"]
    assert tile["state"] == "not_recorded" and tile["has_insurance"] is None
    b = client.get(f"/api/cases/{cid}/fhir", headers=as_user("fatima.al.mansoori")).json()
    assert not [e for e in b["entry"] if e["resource"]["resourceType"] == "Coverage"]


def test_older_payer_only_saves_keep_the_answer_in_step():
    from kareshafaa.models import Patient
    from kareshafaa.services.coverage import has_insurance, summary_tile, validate_insurance

    assert validate_insurance({"insurance_payer": "self_pay"}, NOW)["has_insurance"] is False
    assert validate_insurance({"insurance_payer": "adnic", "insurance_member_id": "ADN-1234"}, NOW)["has_insurance"] is True
    p = Patient(mrn="SYN-T", given_name="A", family_name="B", gender="female", birth_year=1980, age_band="40-49",
                nationality_group="Indian", language="en", region="Abu Dhabi", managing_org_id=1, synthetic=True)
    assert has_insurance(p) is None and summary_tile(p)["state"] == "not_recorded"
    p.insurance_payer = "self_pay"
    assert has_insurance(p) is False and summary_tile(p)["state"] == "no"
    p.insurance_payer, p.has_insurance = "daman_basic", None
    assert has_insurance(p) is True and summary_tile(p)["state"] == "yes"


def test_patient_app_card_follows_the_same_rule_in_four_languages(client, as_user):
    from kareshafaa.comms.self_service_text import SELF_TEXT

    for lang in ("en", "ar", "hi", "ur"):
        X = SELF_TEXT[lang]["ins"]
        assert X["no_ins"] and X["no_ins_note"] and X["not_recorded"], lang
        d = client.get(f"/api/me/profile?lang={lang}", headers=as_user("patient.en")).json()
        assert d["insurance"]["state"] in ("yes", "no", "not_recorded")
    assert SELF_TEXT["ar"]["ins"]["no_ins"] != SELF_TEXT["en"]["ins"]["no_ins"]
    js = Path("kareshafaa/static/js/patient_profile.js").read_text()
    assert 'I.state === "no"' in js and "X.not_recorded" in js


# ------------------------------------------------------------------------------------------ staff form (JS)
def _render_form(v: dict) -> str:
    node = shutil.which("node")
    if not node:
        pytest.skip("node not installed")
    src = Path("kareshafaa/static/js/patients.js").read_text()
    prog = (f"const W = {{ esc: s => String(s ?? '').replace(/[&<>\"]/g, c => '&#' + c.charCodeAt(0) + ';') }};\n{src}\n"
            "PF.opts = { payers: { thiqa: 'Thiqa', adnic: 'ADNIC', self_pay: 'Self-pay (no insurance)' } };\n"
            f"process.stdout.write(PF.insurance({json.dumps(v)}));")
    return subprocess.run([node, "-e", prog], capture_output=True, text=True, check=True, timeout=30).stdout


def test_form_toggle_shows_fields_for_yes_and_hides_them_for_no():
    yes = _render_form({"has_insurance": True, "insurance_payer": "thiqa", "insurance_member_id": "THQ-1"})
    no = _render_form({"insurance_payer": "self_pay"})
    unset = _render_form({})
    for html in (yes, no, unset):
        assert 'name="has_insurance" value="yes"' in html and 'name="has_insurance" value="no"' in html
        assert html.count('aria-controls="ins-fields"') == 2 and "<legend>Has insurance?</legend>" in html
        assert 'value="self_pay"' not in html  # self-pay is the No answer, not an insurer
    assert '<div id="ins-fields" data-ins-fields >' in yes and 'value="yes" aria-controls="ins-fields" checked' in yes
    assert '<div id="ins-fields" data-ins-fields hidden>' in no and 'value="no" aria-controls="ins-fields" checked' in no
    assert '<div id="ins-fields" data-ins-fields hidden>' in unset and "checked" not in unset
    js = Path("kareshafaa/static/js/patients.js").read_text()
    assert "box.hidden = x !== \"yes\"" in js  # switched without a page reload
    assert 'out.has_insurance !== true) keys.forEach' in js  # No / unanswered: hidden fields are never sent


# ------------------------------------------------------------------------------------------ email
def test_staff_edit_email_validated_audited_in_summary_and_fhir(client, as_user, record, db):
    from sqlalchemy import select

    from kareshafaa.models import AuditEvent

    cid, pid, h = record
    url = f"/api/patients/{pid}/contact"
    for bad in ("not-an-email", "a@b", "two@@example.com", "x" * 195 + "@example.com"):
        assert client.post(url, headers=h, json={"email": bad}).status_code == 422, bad
    r = client.post(url, headers=h, json={"email": "New.Address@Example.com"})
    assert r.status_code == 200 and "email" in r.json()["changed"], r.text
    assert client.get(f"/api/patients/{pid}", headers=h).json()["contact"]["email"] == "new.address@example.com"
    ev = db.scalars(select(AuditEvent).where(AuditEvent.event == "patient.contact_updated",
                                             AuditEvent.entity_id == str(pid)).order_by(AuditEvent.id.desc())).first()
    assert ev is not None and "email" in ev.detail["fields"] and "new.address" not in json.dumps(ev.detail)
    K = client.get(f"/api/cases/{cid}/record", headers=h).json()["summary"]["contact"]
    assert K["email"] == "new.address@example.com" and K["can_edit"] is True and K["patient_id"] == pid
    lead = client.get(f"/api/cases/{cid}/record", headers=as_user("fatima.al.mansoori")).json()["summary"]["contact"]
    assert lead["can_edit"] is False  # the lead sees contact details but does not edit them
    b = client.get(f"/api/cases/{cid}/fhir", headers=as_user("fatima.al.mansoori")).json()
    pat = next(e["resource"] for e in b["entry"] if e["resource"]["resourceType"] == "Patient")
    assert {"system": "email", "value": "new.address@example.com", "use": "home"} in pat["telecom"]


def test_patient_can_update_own_email(client, as_user):
    ph = as_user("patient.en")
    before = client.get("/api/me/profile", headers=ph).json()["contact"]["email"]
    assert client.post("/api/me/contact", headers=ph, json={"email": "bad@"}).status_code == 422
    r = client.post("/api/me/contact", headers=ph, json={"email": "me.myself@example.com"})
    assert r.status_code == 200, r.text
    assert client.get("/api/me/profile", headers=ph).json()["contact"]["email"] == "me.myself@example.com"
    client.post("/api/me/contact", headers=ph, json={"email": before})


# ------------------------------------------------------------------------------------------ SYN- backfill
def test_backfill_fills_every_empty_syn_email_and_the_insurance_answer(db):
    from sqlalchemy import select

    from kareshafaa.models import Patient
    from kareshafaa.services import sample_data as sd

    assert "backfill_sample_email_insurance_answer_v1" in [n for n, _ in sd.MIGRATIONS]
    real = Patient(mrn="REAL-0002", given_name="Real", family_name="Person", gender="female", birth_year=1975,
                   age_band="50-59", nationality_group="Emirati", language="ar", region="Abu Dhabi", managing_org_id=1,
                   synthetic=False, insurance_payer="thiqa", insurance_member_id="THQ-1")
    db.add(real)
    samples = db.scalars(select(Patient).where(Patient.mrn.like("SYN-%"), Patient.synthetic.is_(True))).all()
    no_phone = [p for p in samples if not p.has_smartphone]
    for p in no_phone[:5]:
        p.email = None  # the case that showed 'None recorded' (step 4 only gave smartphone users an email)
    kept = samples[0]
    kept.email, kept.has_insurance, kept.insurance_payer = "kept@example.com", None, "self_pay"
    db.flush()
    out = sd.backfill_sample_email_insurance_answer(db)
    assert out["emails"] >= min(5, len(no_phone))
    assert all(p.email and p.email.endswith("@example.com") for p in samples)
    assert kept.email == "kept@example.com" and kept.has_insurance is False
    assert all(p.has_insurance is (p.insurance_payer != "self_pay") for p in samples if p.insurance_payer)
    assert all(p.has_insurance is None for p in samples if not p.insurance_payer)
    assert real.email is None and real.has_insurance is None  # never touches non-sample records
    assert sd.backfill_sample_email_insurance_answer(db)["emails"] == 0  # idempotent
    db.rollback()
