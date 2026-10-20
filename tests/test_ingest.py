"""HL7 v2 ORU^R01 and FHIR Bundle ingestion: parsing, idempotency, human match review."""
from pathlib import Path

import pytest

from wasl.services.errors import Invalid
from wasl.services.ingest import parse_fhir_bundle, parse_hl7v2

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


def hl7(mrn: str, value: str, ctrl: str, code: str = "29771-3", dob: str = "19700101") -> str:
    return "\r".join([
        f"MSH|^~\\&|LIS|LAB|WASL|PROG|20261020093000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1",
        f"PID|1||{mrn}^^^WASL^MR||SYNTHETIC^PATIENT||{dob}|F",
        f"OBX|1|NM|{code}^test^LN||{value}|ug/g|||||F"])


def test_parse_sample_message():
    r = parse_hl7v2((SAMPLES / "oru_r01_fit_positive.hl7").read_text())
    assert r.programme == "colorectal_fit" and r.value == 250 and r.message_id == "LAB-20261020-000123"
    assert r.patient_identifier == "SYN-100001" and r.birth_year == 1974


def test_parse_errors():
    with pytest.raises(Invalid):
        parse_hl7v2("PID|1")
    with pytest.raises(Invalid):
        parse_hl7v2(hl7("X", "250", "c1").replace("ORU^R01", "ADT^A01"))
    # Iteration 6 (fail-safe): malformed values and unknown codes are no longer rejected (a rejected ACK can be
    # lost at the sender); they are kept and routed to human review.
    bad = parse_hl7v2(hl7("X", "abc", "c1"))
    assert bad.programme == "colorectal_fit" and bad.value is None and bad.value_text == "abc"
    unk = parse_hl7v2(hl7("X", "250", "c1", code="9999-9"))
    assert unk.programme is None and "9999-9" in unk.problem
    with pytest.raises(Invalid):
        parse_fhir_bundle({"resourceType": "Patient"})


def test_hl7_ingest_idempotent_and_match(client, as_user, db, make_patient):
    from wasl.models import Patient

    pid = make_patient()
    p = db.get(Patient, pid)
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    msg = hl7(p.mrn, "310", "T-CTRL-1")
    r = client.post("/api/ingest/hl7v2", headers=h, content=msg)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "processed" and body["case"]["state"] == "triaged" and "MSA|AA|T-CTRL-1" in body["ack"]
    again = client.post("/api/ingest/hl7v2", headers=h, content=msg).json()
    assert again["case"]["id"] == body["case"]["id"]  # replay never creates a second case
    # a negative while a case is open is a CONFLICT: linked to the open case and sent to human review
    neg = client.post("/api/ingest/hl7v2", headers=h, content=hl7(p.mrn, "40", "T-CTRL-NEG")).json()
    assert neg["status"] == "processed" and neg["case"]["id"] == body["case"]["id"]
    assert neg["case"]["review_required"] and "conflicting_result" in neg["case"]["review_reasons"]
    # negative result for a patient with no open case: report stored, no case
    p2 = db.get(Patient, make_patient())
    neg2 = client.post("/api/ingest/hl7v2", headers=h, content=hl7(p2.mrn, "40", "T-CTRL-NEG2")).json()
    assert neg2["status"] == "processed" and neg2["case"] is None


def test_unmatched_goes_to_human_review(client, as_user, db, make_patient):
    from wasl.models import Patient

    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    r = client.post("/api/ingest/hl7v2", headers=h, content=hl7("NO-SUCH-MRN", "300", "T-CTRL-2")).json()
    assert r["status"] == "needs_match_review" and r["case"] is None and "MSA|AE" in r["ack"]
    # birth-year mismatch is never auto-matched either
    pid = make_patient()
    mrn = db.get(Patient, pid).mrn
    r2 = client.post("/api/ingest/hl7v2", headers=h, content=hl7(mrn, "300", "T-CTRL-3", dob="19990101")).json()
    assert r2["status"] == "needs_match_review"
    items = client.get("/api/ingest/messages?status=needs_match_review", headers=h).json()["items"]
    mid = next(i["id"] for i in items if i["message_id"] == "T-CTRL-3")
    assert client.post(f"/api/ingest/messages/{mid}/resolve", headers=as_user("analyst"),
                       json={"patient_id": pid}).status_code == 403
    hj = as_user("aisha.rahman")
    res = client.post(f"/api/ingest/messages/{mid}/resolve", headers=hj, json={"patient_id": pid}).json()
    assert res["status"] == "resolved" and res["case"]["state"] == "triaged"
    assert client.post(f"/api/ingest/messages/{mid}/resolve", headers=hj, json={"patient_id": pid}).status_code == 409


def test_fhir_bundle_ingest(client, as_user, db, make_patient):
    import json

    from wasl.models import Patient

    pid = make_patient()
    b = json.loads((SAMPLES / "fhir_bundle_hba1c.json").read_text())
    b["identifier"]["value"] = "T-FHIR-1"
    b["entry"][0]["resource"]["identifier"][0]["value"] = db.get(Patient, pid).mrn
    r = client.post("/api/ingest/fhir", headers=as_user("aisha.rahman"), json=b)
    assert r.status_code == 200, r.text
    assert r.json()["case"]["programme"] == "diabetes_hba1c"


def test_demo_clock_single_case_only(client, as_user, new_case):
    cid = new_case("diabetes_hba1c", 7.0)["id"]
    h = as_user("aisha.rahman")
    assert client.post("/api/sla/sweep", headers=h, json={"hours_ahead": 100}).status_code == 422
    r = client.post("/api/sla/sweep", headers=h, json={"hours_ahead": 400, "case_ids": [cid]})
    assert r.status_code == 200 and r.json()["simulated_clock_hours_ahead"] == 400
    assert client.get(f"/api/cases/{cid}", headers=h).json()["sla"]["escalation_level"] >= 2


@pytest.mark.parametrize("key", ["fatima_rejected_referral", "maria_urgent_escalation", "ramesh_transfer",
                                 "unreachable_ladder", "ramadan_no_show"])
def test_demo_scenarios(client, as_user, key):
    r = client.post(f"/api/demo/scenarios/{key}", headers=as_user("fatima.al.mansoori"))
    assert r.status_code == 200, r.text
    body = r.json()
    expected = {"fatima_rejected_referral": "referral_rejected", "maria_urgent_escalation": "triaged",
                "ramesh_transfer": "consultation", "unreachable_ladder": "unreachable", "ramadan_no_show": "no_show"}[key]
    assert body["state"] == expected
    case = client.get(f"/api/cases/{body['case_id']}", headers=as_user("fatima.al.mansoori")).json()
    if key == "maria_urgent_escalation":
        assert case["sla"]["escalation_level"] == 4
        r = client.post(f"/api/cases/{body['case_id']}/close", headers=as_user("fatima.al.mansoori"),
                        json={"closure_type": "evidence", "evidence_type": "care_plan"})
        assert r.status_code == 409


def test_demo_scenarios_forbidden_for_patients_and_analysts(client, as_user):
    for u in ("patient.en", "analyst"):
        assert client.post("/api/demo/scenarios/ramesh_transfer", headers=as_user(u)).status_code == 403
