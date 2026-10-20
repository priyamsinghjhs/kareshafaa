"""Iteration 7 failure-mode round: adversarial HL7 v2 / FHIR inputs.

Every finding here was a real defect in the parser before this round (see ITERATIONS.md): e.g. a bilateral
mammogram reported as BI-RADS 1 (left) + BI-RADS 4 (right) was filed NEGATIVE because only the first OBX was used."""
import json
import string
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from wasl.services.errors import Invalid
from wasl.services.ingest import parse_fhir_bundle, parse_hl7v2

SAMPLES = Path(__file__).resolve().parent.parent / "samples"
MSH = "MSH|^~\\&|LIS|LAB|WASL|PROG|20261020093000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1"


def msg(mrn: str, *obx: str, ctrl: str = "ADV-1", dob: str = "19700101", extra_pid: str | None = None) -> str:
    segs = [MSH.format(ctrl=ctrl), f"PID|1||{mrn}^^^WASL^MR||SYN^PAT||{dob}|F"]
    segs += [f"OBX|{i}|{o}" for i, o in enumerate(obx, 1)]
    if extra_pid:
        segs.append(extra_pid)
    return "\r".join(segs)


FIT = "NM|29771-3^FIT^LN||{v}|ug/g|||||F"
MAMMO = "ST|24606-6^Mammogram^LN|{side}|{v}||||||F"


# ------------------------------------------------------------------ parser level
@pytest.mark.parametrize("order", [("BI-RADS 1", "BI-RADS 4"), ("BI-RADS 4", "BI-RADS 1")])
def test_bilateral_mammogram_uses_most_severe_side(order):
    r = parse_hl7v2(msg("M", MAMMO.format(side="L", v=order[0]), MAMMO.format(side="R", v=order[1])))
    assert r.value_text == "BI-RADS 4" and r.flags == ["multiple_results"]
    assert [o[2] for o in r.other_observations] == ["BI-RADS 1"]


@pytest.mark.parametrize("vals", [("5", "300"), ("300", "5")])
def test_two_fit_values_use_the_positive_and_flag(vals):
    r = parse_hl7v2(msg("M", FIT.format(v=vals[0]), FIT.format(v=vals[1])))
    assert r.value == 300 and "multiple_results" in r.flags


def test_identical_repeated_value_is_not_flagged():
    r = parse_hl7v2(msg("M", FIT.format(v="300"), FIT.format(v="300")))
    assert r.value == 300 and r.flags == []


def test_hba1c_and_fpg_together_prefer_diagnostic_range():
    r = parse_hl7v2(msg("M", "NM|4548-4^HbA1c^LN||6.0|%|||||F", "NM|1558-6^FPG^LN||7.5|mmol/L|||||F"))
    assert r.test == "FPG" and r.value == 7.5 and "multiple_results" in r.flags


def test_two_cytology_results_use_most_severe():
    r = parse_hl7v2(msg("M", "ST|77379-6^HPV^LN||HPV other||||||F", "ST|10524-7^Cytology^LN||NILM||||||F",
                        "ST|10524-7^Cytology^LN||HSIL||||||F"))
    assert r.cytology == "HSIL" and "multiple_results" in r.flags


def test_more_than_one_pid_goes_to_review_not_merged():
    r = parse_hl7v2(msg("A", FIT.format(v="5"), extra_pid="PID|2||B^^^WASL^MR||X^Y||19600101|F"))
    assert r.programme is None and "more than one patient" in r.problem


def test_more_than_one_patient_in_fhir_bundle():
    b = json.loads((SAMPLES / "fhir_bundle_hba1c.json").read_text())
    b["entry"].append(json.loads(json.dumps(b["entry"][0])))
    r = parse_fhir_bundle(b)
    assert r.programme is None and "more than one Patient" in r.problem


@pytest.mark.parametrize("sn,expect_text", [(">^9.9", ">9.9"), ("<^10", "<10"), (">=^10", ">=10"),
                                            ("^10^-^20", "10-20")])
def test_structured_numeric_comparator_never_dropped(sn, expect_text):
    r = parse_hl7v2(msg("M", f"SN|29771-3^FIT^LN||{sn}|ug/g|||||F"))
    assert r.value is None and r.value_text == expect_text


def test_structured_numeric_plain_value():
    assert parse_hl7v2(msg("M", "SN|29771-3^FIT^LN||^120|ug/g|||||F")).value == 120
    assert parse_hl7v2(msg("M", "SN|29771-3^FIT^LN||=^120|ug/g|||||F")).value == 120


def test_comparator_value_goes_to_urgent_review():
    from wasl.clinical_rules import PatientContext, ResultInput, evaluate

    d = evaluate(ResultInput(programme="colorectal_fit", value=None, value_text=">9.9", unit="ug/g", test="FIT"),
                 PatientContext(age=55))
    assert d.disposition == "review" and d.tier in ("urgent", "critical")


def test_custom_delimiters_from_msh():
    m = msg("M", FIT.format(v="300")).replace("|", "#").replace("^", "$")
    r = parse_hl7v2(m)
    assert r.programme == "colorectal_fit" and r.value == 300 and r.patient_identifier == "M"


def test_escape_sequences_decoded():
    r = parse_hl7v2(msg("M\\T\\X", MAMMO.format(side="", v="BI\\T\\RADS 4")))
    assert r.patient_identifier == "M&X" and r.value_text == "BI&RADS 4"


def test_pid3_repetitions_split():
    m = msg("M", FIT.format(v="300")).replace("PID|1||M^^^WASL^MR", "PID|1||MRN1^^^WASL^MR~EID9^^^ICA^NI")
    r = parse_hl7v2(m)
    assert r.patient_identifier == "MRN1" and r.alt_identifiers == ["EID9"]


@pytest.mark.parametrize("bad", ["MSH", "MSH||||", "MSHa^~\\&|x", "MSH|^^\\&|"])
def test_bad_delimiters_rejected(bad):
    with pytest.raises(Invalid):
        parse_hl7v2(bad)


def test_missing_control_id_gets_content_id():
    r = parse_hl7v2(msg("M", FIT.format(v="300"), ctrl=""))
    assert r.message_id.startswith("NOID-") and r.payload_sha256


@settings(max_examples=400, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(st.lists(st.text(alphabet=string.printable + "\u0627\u0928\\~^&|#", max_size=40), max_size=6),
       st.sampled_from(["29771-3", "24606-6", "4548-4", "1558-6", "77379-6", "10524-7", "x"]),
       st.sampled_from(["NM", "SN", "ST", "", "CE"]))
def test_fuzz_parser_only_raises_invalid(fields, code, vtype):
    m = msg("M", *[f"{vtype}|{code}^t^LN||{f}|ug/g|||||F" for f in fields])
    try:
        r = parse_hl7v2(m)
    except Invalid:
        return
    assert r.programme is None or r.problem is None


@settings(max_examples=300, deadline=None)
@given(st.text(max_size=300))
def test_fuzz_raw_text_only_raises_invalid(raw):
    try:
        parse_hl7v2("MSH|^~\\&|" + raw)
    except Invalid:
        pass


# ------------------------------------------------------------------ end to end
def _post(client, as_user, m):
    r = client.post("/api/ingest/hl7v2", headers={**as_user("aisha.rahman"), "Content-Type": "text/plain"}, content=m)
    assert r.status_code == 200, r.text
    return r.json()


def test_bilateral_birads_creates_review_case_with_both_values(client, as_user, db, make_patient):
    from wasl.models import Case, DiagnosticReport, Patient

    p = db.get(Patient, make_patient())
    body = _post(client, as_user, msg(p.mrn, MAMMO.format(side="L", v="BI-RADS 1"),
                                      MAMMO.format(side="R", v="BI-RADS 4"), ctrl="ADV-BIL"))
    case = body["case"]
    assert case is not None and case["review_required"] and "multiple_results" in case["review_reasons"]
    c = db.get(Case, case["id"])
    assert c.tier in ("urgent", "critical", "high") and c.programme == "breast"
    rep = db.get(DiagnosticReport, c.report_id)
    assert sorted(o.value_text for o in rep.observations) == ["BI-RADS 1", "BI-RADS 4"]


def test_two_negative_values_differ_still_reviewed(client, as_user, db, make_patient):
    from wasl.models import Patient

    p = db.get(Patient, make_patient())
    body = _post(client, as_user, msg(p.mrn, FIT.format(v="3"), FIT.format(v="7"), ctrl="ADV-NN"))
    assert body["case"] is not None and "multiple_results" in body["case"]["review_reasons"]


def test_reused_control_id_with_different_content_is_not_silently_dropped(client, as_user, db, make_patient):
    from wasl.models import Patient

    p = db.get(Patient, make_patient())
    first = _post(client, as_user, msg(p.mrn, FIT.format(v="5"), ctrl="ADV-DUP"))
    assert first["status"] == "processed" and first["case"] is None
    same = _post(client, as_user, msg(p.mrn, FIT.format(v="5"), ctrl="ADV-DUP"))
    assert same["message_id"] == "ADV-DUP" and same["status"] == "processed"  # a true replay is idempotent
    corrected = _post(client, as_user, msg(p.mrn, FIT.format(v="300"), ctrl="ADV-DUP"))
    assert corrected["message_id"].startswith("ADV-DUP#conflict-") and corrected["status"] == "processed"
    assert corrected["case"]["review_required"] and "control_id_reused" in corrected["case"]["review_reasons"]
    assert corrected["case"]["tier"] in ("high", "urgent", "critical")  # FIT 300 (positive >= 80): high
    again = _post(client, as_user, msg(p.mrn, FIT.format(v="300"), ctrl="ADV-DUP"))
    assert again["message_id"] == corrected["message_id"]
    from wasl.models import IngestMessage
    assert db.query(IngestMessage).filter(IngestMessage.message_id.like("ADV-DUP%")).count() == 2


def test_identifiers_pointing_to_two_patients_go_to_review(client, as_user, db, make_patient):
    from wasl.models import Patient

    a, b = db.get(Patient, make_patient()), db.get(Patient, make_patient())
    m = msg("X", FIT.format(v="300"), ctrl="ADV-2ID").replace("PID|1||X^^^WASL^MR", f"PID|1||{a.mrn}~{b.mrn}")
    body = _post(client, as_user, m)
    assert body["status"] == "needs_match_review" and "different patients" in body["reason"]


def test_alternative_identifier_matches(client, as_user, db, make_patient):
    from wasl.models import Patient

    a = db.get(Patient, make_patient())
    m = msg("X", FIT.format(v="300"), ctrl="ADV-ALT").replace("PID|1||X^^^WASL^MR", f"PID|1||UNKNOWN-1~{a.mrn}")
    body = _post(client, as_user, m)
    assert body["status"] == "processed" and body["case"]["patient"]["id"] == a.id


# ------------------------------------------------------------------ FHIR structure / numeric syntax (round 9b)
def _bundle():
    return json.loads((SAMPLES / "fhir_bundle_hba1c.json").read_text())


def _obs_index(b):
    return next(i for i, e in enumerate(b["entry"]) if e["resource"]["resourceType"] == "Observation")


@pytest.mark.parametrize("mutate", [
    lambda b: b.__setitem__("entry", "x"),
    lambda b: b.__setitem__("entry", ["x", 1, None]),
    lambda b: b.__setitem__("entry", [{"resource": [1]}]),
    lambda b: b.__setitem__("identifier", [1]),
    lambda b: b.__setitem__("identifier", "abc"),
    lambda b: b["entry"][_obs_index(b)]["resource"].__setitem__("valueQuantity", "7"),
    lambda b: b["entry"][_obs_index(b)]["resource"].__setitem__("code", {"coding": "abc"}),
    lambda b: b["entry"][0]["resource"].__setitem__("identifier", "abc"),
    lambda b: b["entry"][0]["resource"].__setitem__("birthDate", 1970),
])
def test_structurally_broken_fhir_is_invalid_not_a_crash(mutate):
    b = _bundle()
    mutate(b)
    with pytest.raises(Invalid):
        parse_fhir_bundle(b)


def test_deeply_nested_json_is_rejected_cleanly(client, as_user):
    r = client.post("/api/ingest/fhir", headers={**as_user("aisha.rahman"), "Content-Type": "application/json"},
                    content="[" * 30000 + "]" * 30000)
    assert r.status_code in (400, 422)


@pytest.mark.parametrize("raw", ["1_000", "NaN", "inf", "1e400", "0x10", "8O", "--5"])
def test_non_decimal_numbers_stay_text(raw):
    r = parse_hl7v2(msg("M", FIT.format(v=raw)))
    assert r.value is None and r.value_text == raw


@pytest.mark.parametrize("raw,val", [("120", 120), (" 80 ", 80), ("1.5e2", 150), ("\u0661\u0662\u0660", 120)])
def test_decimal_numbers_parse(raw, val):
    assert parse_hl7v2(msg("M", FIT.format(v=raw))).value == val


@settings(max_examples=200, deadline=None)
@given(st.recursive(st.none() | st.booleans() | st.integers() | st.floats(allow_nan=True) | st.text(max_size=8),
                    lambda ch: st.lists(ch, max_size=3) | st.dictionaries(st.text(max_size=8), ch, max_size=3),
                    max_leaves=12))
def test_fuzz_fhir_only_raises_invalid(junk):
    b = _bundle()
    b["entry"][_obs_index(b)]["resource"]["valueQuantity"] = junk
    try:
        parse_fhir_bundle(b)
    except Invalid:
        pass


def test_bom_and_mllp_framing_tolerated():
    base = msg("M", FIT.format(v="300"))
    for raw in ("\ufeff" + base, "\x0b" + base + "\x1c\r", "\n\n" + base + "\n"):
        r = parse_hl7v2(raw)
        assert r.value == 300 and r.patient_identifier == "M"


def test_sweep_skips_when_clock_moves_backwards(monkeypatch):
    from datetime import datetime

    import wasl.main as m

    calls = []
    monkeypatch.setattr(m, "run_sweep", lambda db: calls.append(1) or {"ok": 1})
    monkeypatch.setattr(m, "_LAST_SWEEP_AT", datetime(2030, 1, 1))
    assert m._sweep_once() == {"skipped": "clock_moved_backwards"} and not calls


# ------------------------------------------------------------------ specimen collection time (round 12)
def _ts(delta_days: float) -> str:
    from datetime import timedelta

    from wasl.models import utcnow

    return (utcnow() + timedelta(days=delta_days)).strftime("%Y%m%d%H%M") + "+0000"


@pytest.mark.parametrize("v,expect", [("20261020", (2026, 10, 19, 20, 0)), ("202610201530+0400", (2026, 10, 20, 11, 30)),
                                      ("20261020153000-0100", (2026, 10, 20, 16, 30)), ("2026-13-40", None),
                                      ("garbage", None), ("", None)])
def test_hl7_collection_time_parsing(v, expect):
    from wasl.services.ingest import parse_collection_time

    t = parse_collection_time(v, True)
    assert (t.timetuple()[:5] if t else None) == expect  # no offset = UAE local (UTC+4)


def test_fhir_collection_time_parsing():
    from wasl.services.ingest import parse_collection_time

    assert parse_collection_time("2026-10-20T08:00:00Z", False).hour == 8
    assert parse_collection_time("2026-10-20T08:00:00", False).hour == 4  # naive = UAE local
    assert parse_collection_time("2026-10-20", False).day == 19
    assert parse_collection_time("not a date", False) is None and parse_collection_time(42, False) is None


@pytest.mark.parametrize("days,reason", [(-400, "stale_result"), (5, "collection_date_in_future")])
def test_specimen_timing_sends_positive_to_review_tier_kept(client, as_user, db, make_patient, days, reason):
    from wasl.models import Patient

    p = db.get(Patient, make_patient())
    body = _post(client, as_user, msg(p.mrn, FIT.format(v="300") + f"|||{_ts(days)}", ctrl=f"ADV-T{days}"))
    c = body["case"]
    assert c is not None and c["review_required"] and reason in c["review_reasons"]
    q = db.get(Patient, make_patient())
    plain = _post(client, as_user, msg(q.mrn, FIT.format(v="300"), ctrl=f"ADV-TP{days}"))["case"]
    assert c["tier"] == plain["tier"]  # never lowered (nor raised) by a timing flag


@pytest.mark.parametrize("days,reason", [(-400, "stale_result"), (5, "collection_date_in_future")])
def test_specimen_timing_negative_is_not_silently_filed(client, as_user, db, make_patient, days, reason):
    from wasl.models import Patient

    p = db.get(Patient, make_patient())
    body = _post(client, as_user, msg(p.mrn, FIT.format(v="3") + f"|||{_ts(days)}", ctrl=f"ADV-TN{days}"))
    assert body["case"] is not None and reason in body["case"]["review_reasons"]


@pytest.mark.parametrize("days", [-30, 0.5, -0.01])
def test_recent_or_slightly_ahead_specimen_is_normal(client, as_user, db, make_patient, days):
    from wasl.models import Patient

    p = db.get(Patient, make_patient())
    body = _post(client, as_user, msg(p.mrn, FIT.format(v="300") + f"|||{_ts(days)}", ctrl=f"ADV-OK{days}"))
    assert not (body["case"].get("review_reasons") or [])


@pytest.mark.parametrize("vals,primary", [(("BI-RADS 4", "BI-RADS 5"), "BI-RADS 5"), (("BI-RADS 5", "BI-RADS 4"), "BI-RADS 5"),
                                          (("BI-RADS 4C", "BI-RADS 4A"), "BI-RADS 4C"), (("BI-RADS 4A", "BI-RADS 4C"), "BI-RADS 4C")])
def test_same_tier_bilateral_shows_most_suspicious_category(vals, primary):
    r = parse_hl7v2(msg("M", MAMMO.format(side="L", v=vals[0]), MAMMO.format(side="R", v=vals[1])))
    assert r.value_text == primary and "multiple_results" in r.flags


@pytest.mark.parametrize("vals", [("150", "400"), ("400", "150")])
def test_two_positive_fit_values_show_the_higher(vals):
    r = parse_hl7v2(msg("M", FIT.format(v=vals[0]), FIT.format(v=vals[1])))
    assert r.value == 400 and "multiple_results" in r.flags
