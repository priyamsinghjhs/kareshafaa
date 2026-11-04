"""Round 2 conformance suite: every FHIR resource we emit or accept is validated against the FHIR R4B models
of the `fhir.resources` library (R4B shares R4's resource structure for the resources used here), and the HL7 v2
ORU^R01 path is checked segment by segment (MSH/PID/OBX in, ACK out)."""
import json
from pathlib import Path

import pytest

pytest.importorskip("fhir.resources")
from fhir.resources.R4B import get_fhir_model_class  # noqa: E402

from kareshafaa.services.ingest import parse_hl7v2  # noqa: E402

SAMPLES = Path(__file__).resolve().parent.parent / "samples"


# fhir.resources enforces structure, cardinality, types and unknown elements but not required value sets,
# so the required-binding status codes are checked here (values from the R4 specification).
REQUIRED_CODES = {
    "Observation": ("status", {"registered", "preliminary", "final", "amended", "corrected", "cancelled",
                               "entered-in-error", "unknown"}),
    "DiagnosticReport": ("status", {"registered", "partial", "preliminary", "final", "amended", "corrected",
                                    "appended", "cancelled", "entered-in-error", "unknown"}),
    "Task": ("status", {"draft", "requested", "received", "accepted", "rejected", "ready", "cancelled",
                        "in-progress", "on-hold", "failed", "completed", "entered-in-error"}),
    "Consent": ("status", {"draft", "proposed", "active", "rejected", "inactive", "entered-in-error"}),
    "Communication": ("status", {"preparation", "in-progress", "not-done", "on-hold", "stopped", "completed",
                                 "entered-in-error", "unknown"}),
    "Bundle": ("type", {"document", "message", "transaction", "transaction-response", "batch", "batch-response",
                        "history", "searchset", "collection"}),
}


def _validate(res: dict) -> None:
    cls = get_fhir_model_class(res["resourceType"])
    cls.model_validate(res) if hasattr(cls, "model_validate") else cls.parse_obj(res)
    if res["resourceType"] in REQUIRED_CODES:
        field, allowed = REQUIRED_CODES[res["resourceType"]]
        assert res.get(field) in allowed, (res["resourceType"], field, res.get(field))
    if res["resourceType"] == "Task":
        assert res.get("intent") in {"unknown", "proposal", "plan", "order", "original-order", "reflex-order",
                                     "filler-order", "instance-order", "option"}, res.get("intent")


def test_sample_fhir_input_bundle_is_valid_r4():
    b = json.loads((SAMPLES / "fhir_bundle_hba1c.json").read_text())
    _validate(b)
    for e in b["entry"]:
        _validate(e["resource"])


def test_capability_statement_is_valid(client):
    cs = client.get("/fhir/metadata").json()
    assert cs["resourceType"] == "CapabilityStatement" and cs["fhirVersion"].startswith("4.")
    _validate(cs)


@pytest.mark.parametrize("state", ["closed", ""])
def test_exported_case_bundles_validate(client, as_user, state):
    h = as_user("fatima.al.mansoori")
    items = client.get(f"/api/cases?state={state}&limit=4" if state else "/api/cases?limit=4", headers=h).json()["items"]
    assert items, state
    seen: set[str] = set()
    for c in items:
        b = client.get(f"/api/cases/{c['id']}/fhir", headers=h).json()
        _validate(b)  # the Bundle with all entries
        for e in b["entry"]:
            _validate(e["resource"])
            seen.add(e["resource"]["resourceType"])
    assert {"Patient", "Observation", "Task"} <= seen


def test_hl7_oru_segments_and_ack(client, as_user, db, make_patient):
    from kareshafaa.models import Patient

    raw = (SAMPLES / "oru_r01_fit_positive.hl7").read_text()
    segs = [s.split("|")[0] for s in raw.replace("\n", "\r").split("\r") if s.strip()]
    assert segs[0] == "MSH" and "PID" in segs and "OBX" in segs
    r = parse_hl7v2(raw)
    assert r.message_id and r.programme == "colorectal_fit"
    mrn = db.get(Patient, make_patient()).mrn
    msg = "\r".join([
        "MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|CONF-ACK-1|P|2.5.1",
        f"PID|1||{mrn}^^^KARESHAFAA^MR||SYNTHETIC^PATIENT||19700101|F",
        "OBX|1|NM|29771-3^test^LN||250|ug/g|||||F"])
    out = client.post("/api/ingest/hl7v2", headers=as_user("aisha.rahman"), content=msg.encode())
    assert out.status_code == 200, out.text
    ack = json.dumps(out.json())
    assert "MSA" in ack and "CONF-ACK-1" in ack  # ACK echoes the control id


def test_hl7_requires_ingest_permission(client, as_user):
    r = client.post("/api/ingest/hl7v2", content=b"MSH|")
    assert r.status_code in (401, 403)
