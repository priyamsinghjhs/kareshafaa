"""Ordering clinician, screening site and specimen collection date on DiagnosticReport (HL7, FHIR, typed-in)."""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

from kareshafaa.models import DiagnosticReport, IngestMessage, Patient, utcnow
from kareshafaa.services.ingest import parse_fhir_bundle, parse_hl7v2

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
CO = "aisha.rahman"


def hl7(mrn, ctrl, *, orc=True, pv1=True, obr16=False, collected="20261020083000"):
    segs = [f"MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1",
            f"PID|1||{mrn}^^^KARESHAFAA^MR||SYNTHETIC^PATIENT||19700101|F"]
    if pv1:
        segs.append("PV1|1|O|SCRN^^^Al Ain Mobile Screening Unit")
    if orc:
        segs.append("ORC|RE|ORD-1|FIL-1|||||||||D123^Haddad^Omar^^^Dr|||||||||Mushrif Healthcare Centre")
    segs.append("OBR|1|ORD-1|FIL-1|29771-3^FIT^LN|||" + collected + "|||||||||" + ("D9^Rahman^Aisha^^^Dr" if obr16 else "") + "||||||||F")
    segs.append("OBX|1|NM|29771-3^test^LN||300|ug/g|||||F")
    return "\r".join(segs)


def test_hl7_parses_orderer_site_and_collection():
    r = parse_hl7v2(hl7("X", "c1"))
    assert r.ordering_clinician == "Dr Omar Haddad" and r.screening_site == "Mushrif Healthcare Centre"
    assert r.collected_at.isoformat() == "2026-10-20T04:30:00"  # no offset = UAE local -> UTC
    r = parse_hl7v2(hl7("X", "c2", orc=False, obr16=True))
    assert r.ordering_clinician == "Dr Aisha Rahman" and r.screening_site == "Al Ain Mobile Screening Unit"
    r = parse_hl7v2(hl7("X", "c3", orc=False, pv1=False, collected=""))
    assert (r.ordering_clinician, r.screening_site, r.collected_at) == (None, None, None)


def test_fhir_parses_requester_location_and_specimen():
    b = json.loads((SAMPLES / "fhir_bundle_hba1c.json").read_text())
    b["entry"] += [{"resource": {"resourceType": "ServiceRequest", "requester": {"display": "Dr Layla Nasser"},
                                 "locationReference": [{"display": "Khalifa City Screening Clinic"}]}},
                   {"resource": {"resourceType": "Specimen", "collection": {"collectedDateTime": "2026-10-19T07:15:00+04:00"}}}]
    r = parse_fhir_bundle(b)
    assert r.ordering_clinician == "Dr Layla Nasser" and r.screening_site == "Khalifa City Screening Clinic"
    assert r.collected_at.isoformat() == "2026-10-19T03:15:00"
    b["entry"] = [e for e in b["entry"] if e["resource"]["resourceType"] != "ServiceRequest"]
    b["entry"] += [{"resource": {"resourceType": "Practitioner", "name": [{"prefix": ["Dr"], "given": ["Sami"], "family": "Odeh"}]}},
                   {"resource": {"resourceType": "Encounter", "serviceProvider": {"display": "Baniyas Health Centre"}}}]
    r = parse_fhir_bundle(b)
    assert r.ordering_clinician == "Dr Sami Odeh" and r.screening_site == "Baniyas Health Centre"


def test_hl7_ingest_stores_on_report_and_shows_on_case(client, as_user, db, make_patient):
    p = db.get(Patient, make_patient())
    h = {**as_user(CO), "Content-Type": "text/plain"}
    now = utcnow() + timedelta(hours=4)
    body = client.post("/api/ingest/hl7v2", headers=h, content=hl7(p.mrn, "T-META-1", collected=now.strftime("%Y%m%d%H%M%S"))).json()
    cid = body["case"]["id"]
    rep = db.scalars(__import__("sqlalchemy").select(DiagnosticReport).where(DiagnosticReport.case_id == cid)).first()
    assert rep.ordering_clinician == "Dr Omar Haddad" and rep.screening_site == "Mushrif Healthcare Centre"
    assert rep.specimen_collected_at is not None
    for u in (CO, "omar.haddad"):
        d = client.get(f"/api/cases/{cid}", headers=as_user(u)).json()
        assert d["result"]["ordering_clinician"] == "Dr Omar Haddad", u
        assert d["result"]["screening_site"] == "Mushrif Healthcare Centre" and d["result"]["specimen_collected_at"]
    rec = client.get(f"/api/patients/{p.id}", headers=as_user(CO)).json()
    assert any(r["ordered_by"] == "Dr Omar Haddad" and r["site"] for r in rec["results"])
    cr = client.get(f"/api/cases/{cid}/record", headers=as_user("omar.haddad"))
    assert cr.status_code == 200, cr.text
    t = next(t for t in cr.json()["tests"] if t["triggering"])
    assert t["ordered_by"] == "Dr Omar Haddad" and t["screening_site"] == "Mushrif Healthcare Centre"


def test_unmatched_message_keeps_metadata_through_match_review(client, as_user, db, make_patient):
    pid = make_patient()
    h = {**as_user(CO), "Content-Type": "text/plain"}
    r = client.post("/api/ingest/hl7v2", headers=h, content=hl7("NO-SUCH-MRN-META", "T-META-2",
                    collected=(utcnow() + timedelta(hours=3)).strftime("%Y%m%d%H%M%S"))).json()
    assert r["status"] == "needs_match_review"
    items = client.get("/api/ingest/messages?status=needs_match_review", headers=as_user(CO)).json()["items"]
    it = next(i for i in items if i["message_id"] == "T-META-2")
    assert it["ordering_clinician"] == "Dr Omar Haddad" and it["screening_site"]
    res = client.post(f"/api/ingest/messages/{it['id']}/resolve", headers=as_user(CO), json={"patient_id": pid}).json()
    db.expire_all()
    m = db.get(IngestMessage, it["id"])
    rep = db.get(DiagnosticReport, m.report_id)
    assert res["status"] == "resolved" and rep.ordering_clinician == "Dr Omar Haddad" and rep.specimen_collected_at


def test_typed_in_result_metadata_validation(client, as_user, db, make_patient):
    h = as_user(CO)
    pid = make_patient()
    base = {"patient_id": pid, "programme": "colorectal_fit", "value": 260, "unit": "ug/g"}
    future = (utcnow() + timedelta(days=3)).isoformat()
    assert client.post("/api/results", headers=h, json={**base, "specimen_collected_at": future}).status_code == 422
    assert client.post("/api/results", headers=h, json={**base, "ordering_clinician": "x"}).status_code == 422
    assert client.post("/api/results", headers=h, json={**base, "screening_site": "a\x07b"}).status_code == 422
    r = client.post("/api/results", headers=h, json={**base, "ordering_clinician": "Dr Omar Haddad",
                                                      "screening_site": "Al Ain Mobile Screening Unit",
                                                      "specimen_collected_at": "2026-10-28T09:00:00"})
    assert r.status_code == 200, r.text
    db.expire_all()
    rep = db.scalars(__import__("sqlalchemy").select(DiagnosticReport).where(DiagnosticReport.patient_id == pid)).one()
    assert rep.screening_site == "Al Ain Mobile Screening Unit" and rep.specimen_collected_at.isoformat() == "2026-10-28T05:00:00"
    # an old specimen typed in is sent to clinician review, as for lab messages
    pid2 = make_patient()
    old = (utcnow() - timedelta(days=400)).date().isoformat()
    r = client.post("/api/results", headers=h, json={**base, "patient_id": pid2, "specimen_collected_at": old})
    assert r.status_code == 200
    c = r.json()["case"]
    assert c["review_required"] and "stale_result" in c["review_reasons"]
