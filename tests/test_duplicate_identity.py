"""Iteration 7 failure-mode round: one person, two patient records (two MRNs, same Emirates ID).

Before: each record was handled independently - a negative on record B could not be compared with the open
positive case on record A, and a second positive opened a second case (two coordinators calling one person)."""
from sqlalchemy import func, select


def _twin(db, pid):
    from wasl.models import Patient

    p = db.get(Patient, pid)
    p.national_id = f"784-1970-{9500000 + pid:07d}-1"
    twin = Patient(mrn=p.mrn + "-DUP", national_id=p.national_id, given_name=p.given_name, family_name=p.family_name,
                   gender=p.gender, birth_year=p.birth_year, age_band=p.age_band, nationality_group=p.nationality_group,
                   language=p.language, region=p.region, managing_org_id=p.managing_org_id, gp_id=p.gp_id)
    db.add(twin)
    db.commit()
    return p, twin


def _result(client, as_user, pid, value):
    r = client.post("/api/results", headers=as_user("aisha.rahman"),
                    json={"patient_id": pid, "programme": "colorectal_fit", "value": value, "unit": "ug/g"})
    assert r.status_code == 200, r.text
    return r.json()


def test_second_record_result_links_to_open_case(client, as_user, db, make_patient):
    from wasl.models import Case

    a, b = _twin(db, make_patient())
    first = _result(client, as_user, a.id, 250)["case"]
    assert first and "possible_duplicate_identity" in first["review_reasons"]  # the twin exists: flagged at once
    neg = _result(client, as_user, b.id, 20)  # negative on the OTHER record
    assert neg["case"]["id"] == first["id"]
    assert {"conflicting_result", "possible_duplicate_identity"} <= set(neg["case"]["review_reasons"])
    pos = _result(client, as_user, b.id, 300)  # second positive on the other record: no second case
    assert pos["case"]["id"] == first["id"]
    db.expire_all()
    n = db.scalar(select(func.count()).select_from(Case).where(Case.patient_id.in_([a.id, b.id]),
                                                                Case.state != "closed"))
    assert n == 1


def test_no_twin_no_flag(client, as_user, make_patient):
    c = _result(client, as_user, make_patient(), 250)["case"]
    assert "possible_duplicate_identity" not in c["review_reasons"]


def test_hl7_emirates_id_match_and_ambiguity(client, as_user, db, make_patient):
    from wasl.models import Patient

    solo = db.get(Patient, make_patient())
    solo.national_id = f"784-1970-{9600000 + solo.id:07d}-2"
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}

    def msg(ids, ctrl):
        return "\r".join([f"MSH|^~\\&|LIS|LAB|WASL|PROG|20261020093000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1",
                          f"PID|1||{ids}||SYN^PAT||19700101|F", "OBX|1|NM|29771-3^FIT^LN||300|ug/g|||||F"])

    r = client.post("/api/ingest/hl7v2", headers=h, content=msg(f"UNKNOWN-MRN~{solo.national_id}^^^ICA^NI", "EID-1")).json()
    assert r["status"] == "processed" and r["case"]["patient"]["id"] == solo.id
    a, _b = _twin(db, make_patient())
    r = client.post("/api/ingest/hl7v2", headers=h, content=msg(f"UNKNOWN-MRN~{a.national_id}^^^ICA^NI", "EID-2")).json()
    assert r["status"] == "needs_match_review" and "more than one patient record" in r["reason"]


def test_synthetic_emirates_id_format():
    from wasl.synth.history import synthetic_emirates_id

    eid = synthetic_emirates_id(1974, 42)
    parts = eid.split("-")
    assert parts[0] == "784" and parts[1] == "1974" and len(parts[2]) == 7 and parts[2].startswith("9")
    digits = "".join(parts)
    total = 0
    for i, ch in enumerate(reversed(digits)):  # Luhn over the full number including the check digit
        d = int(ch) * (2 if i % 2 == 1 else 1)
        total += d - 9 if d > 9 else d
    assert total % 10 == 0
