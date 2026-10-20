"""Fail-safe behaviour (hazard log H-01..H-20, docs/clinical_safety_case.md).

Every abnormal input must reach a human at a safe tier with a reason - never be dropped, never be
auto-downgraded - and the ML score must never change a clinical urgency tier."""
from __future__ import annotations

import random
from datetime import timedelta

import pytest

from wasl.models import Case, Patient, Practitioner, TierDowngradeError, utcnow
from wasl.security import SYSTEM
from wasl.services import workflow as wf
from wasl.services.errors import DomainError, Forbidden, Invalid
from wasl.statemachine import S, TransitionError
from wasl.clinical_rules import TIER_RANK


def _patient(db, *, sex="female", age=50, **kw) -> Patient:
    now = utcnow()
    p = Patient(mrn=f"FS-{random.randint(0, 10**12)}", given_name="Fail", family_name="Safe", gender=sex,
                birth_year=now.year - age, age_band="x", nationality_group="emirati", language="en", region="Abu Dhabi",
                phone_valid=True, has_smartphone=True, whatsapp=True, managing_org_id=3, gp_id=19, responsiveness=0.8,
                **kw)
    db.add(p)
    db.flush()
    return p


def _ingest(db, p, prog="colorectal_fit", value=250.0, text=None, unit="ug/g", **kw):
    return wf.ingest_result(db, p, prog, value=value, value_text=text, unit=unit, performer_org_id=1, **kw)


def _lead(db, prog):
    from wasl.synth.history import actor_from_user
    from wasl.models import User

    lead = wf.programme_clinical_lead(db, prog)
    return actor_from_user(db.query(User).filter(User.practitioner_id == lead.id).one())


def _assert_review(case, reason, min_tier):
    assert case is not None, "result was dropped"
    assert case.review_required, "not routed to human review"
    assert reason in case.review_reasons, case.review_reasons
    assert TIER_RANK[case.tier] <= TIER_RANK[min_tier], (case.tier, min_tier)
    assert case.owner_id is not None


@pytest.fixture()
def sdb(db):
    yield db
    db.rollback()


# H-01 unknown / malformed / out-of-range / missing unit ----------------------------------------------------
@pytest.mark.parametrize("prog,value,text,unit,reason,tier", [
    ("colorectal_fit", None, "??", None, "malformed_value", "urgent"),
    ("colorectal_fit", 250, None, None, "missing_unit", "urgent"),
    ("colorectal_fit", 250, None, "furlongs", "unknown_unit", "urgent"),
    ("colorectal_fit", 99999, None, "ug/g", "implausible_value", "urgent"),
    ("diabetes_hba1c", 8.1, None, None, "missing_unit", "urgent"),
    ("diabetes_hba1c", 31, None, "%", "implausible_value", "urgent"),
    ("diabetes_hba1c", None, None, "%", "missing_value", "urgent"),
    ("breast", None, "BI-RADS 9", None, "malformed_value", "urgent"),
    ("cervical", None, "HPV maybe", None, "malformed_value", "urgent"),
])
def test_bad_data_goes_to_review(sdb, prog, value, text, unit, reason, tier):
    _, case = _ingest(sdb, _patient(sdb), prog, value, text, unit)
    _assert_review(case, reason, tier)
    assert sdb.get(Practitioner, case.owner_id).role == "clinical_lead"


def test_missing_unit_takes_most_urgent_interpretation(sdb):
    # 16 could be 16 % (critical) or 16 mmol/mol (normal-low): the review is CRITICAL, not routine
    _, case = _ingest(sdb, _patient(sdb, sex="male"), "diabetes_hba1c", 16, None, None)
    _assert_review(case, "missing_unit", "critical")
    assert case.tier == "critical"


# H-02 context exceptions ----------------------------------------------------------------------------------
@pytest.mark.parametrize("kw,prog,text,value,unit,reason", [
    (dict(sex="male"), "cervical", "HPV16", None, None, "sex_mismatch"),
    (dict(sex="male"), "breast", "BI-RADS 4", None, None, "sex_mismatch"),
    (dict(age=30), "colorectal_fit", None, 250, "ug/g", "age_below_programme"),
    (dict(age=80), "colorectal_fit", None, 250, "ug/g", "age_above_programme"),
    (dict(age=22), "cervical", "HPV16", None, None, "age_below_programme"),
    (dict(age=70), "cervical", "HPV16", None, None, "age_above_programme"),
    (dict(age=35), "breast", "BI-RADS 0", None, None, "age_below_programme"),
    (dict(pregnant=True, age=30), "cervical", "HPV16", None, None, "pregnant"),
    (dict(pregnant=True, age=30), "diabetes_hba1c", None, 8.1, "%", "pregnant"),
    (dict(deceased=True), "colorectal_fit", None, 250, "ug/g", "deceased"),
    (dict(specialist_care="colorectal_fit"), "colorectal_fit", None, 250, "ug/g", "under_specialist_care"),
])
def test_context_goes_to_review(sdb, kw, prog, text, value, unit, reason):
    _, case = _ingest(sdb, _patient(sdb, **kw), prog, value, text, unit)
    _assert_review(case, reason, "routine")


def test_out_of_age_negative_is_not_silently_filed(sdb):
    """A NEGATIVE result for someone outside the programme age still needs a human look (opportunistic test)."""
    rep, case = _ingest(sdb, _patient(sdb, age=30), "colorectal_fit", 10, None, "ug/g")
    _assert_review(case, "age_below_programme", "routine")


# H-03 statuses ----------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status,reason,tier", [("amended", "amended_result", "urgent"),
                                                ("corrected", "corrected_result", "urgent"),
                                                ("preliminary", "preliminary_result", "high"),
                                                ("entered-in-error", "result_retracted", "urgent"),
                                                ("cancelled", "result_retracted", "urgent"),
                                                ("weird", "malformed_value", "urgent")])
def test_non_final_status_goes_to_review(sdb, status, reason, tier):
    _, case = _ingest(sdb, _patient(sdb), status=status)
    _assert_review(case, reason, tier)


# H-04 duplicates / conflicts / amended lower value / result after closure ---------------------------------
def test_duplicate_never_creates_second_case(sdb):
    p = _patient(sdb)
    _, c1 = _ingest(sdb, p)
    _, c2 = _ingest(sdb, p)
    assert c1.id == c2.id
    _assert_review(c2, "duplicate_result", "routine")
    assert sdb.query(Case).filter(Case.patient_id == p.id).count() == 1


def test_conflicting_negative_flags_and_never_lowers(sdb):
    p = _patient(sdb)
    _, c1 = _ingest(sdb, p, value=500)  # urgent
    _, c2 = _ingest(sdb, p, value=10)   # negative
    assert c2.id == c1.id and c2.tier == "urgent"
    _assert_review(c2, "conflicting_result", "urgent")


def test_amended_lower_value_does_not_downgrade(sdb):
    p = _patient(sdb)
    _, c1 = _ingest(sdb, p, value=500)
    _, c2 = _ingest(sdb, p, value=90, status="amended")
    assert c2.tier == "urgent"
    _assert_review(c2, "amended_result", "urgent")


def test_more_urgent_result_escalates_and_retimes_clock(sdb):
    p = _patient(sdb)
    _, c = _ingest(sdb, p, value=100)  # high
    due_before = c.due_at
    _, c = _ingest(sdb, p, value=800)  # urgent
    assert c.tier == "urgent" and "more_urgent_result" in c.review_reasons
    assert c.due_at is None or due_before is None or c.due_at <= due_before  # clock never extended


def test_result_after_closure_opens_review_case(sdb):
    p = _patient(sdb)
    _, c1 = _ingest(sdb, p, value=250)
    lead = _lead(sdb, "colorectal_fit")
    wf.close_case(sdb, c1, lead, "authorised_decision", reason="moved_abroad", note="Moved abroad, summary given.")
    _, c2 = _ingest(sdb, p, value=300, now=utcnow() + timedelta(days=3))
    assert c2.id != c1.id
    _assert_review(c2, "result_after_closure", "high")


# H-05 review gate: nothing progresses until a clinician has reviewed --------------------------------------
def test_review_case_cannot_progress_or_message_or_close_with_evidence(sdb):
    from wasl.comms.service import send_with_ladder

    p = _patient(sdb)
    _, c = _ingest(sdb, p, unit=None)
    with pytest.raises((Forbidden, TransitionError)):
        wf.transition(sdb, c, S.TRIAGED, SYSTEM)
    with pytest.raises(DomainError):
        send_with_ladder(sdb, c, "result_notification", SYSTEM)
    coord_user = sdb.query(__import__("wasl.models", fromlist=["User"]).User).filter_by(username="aisha.rahman").one()
    from wasl.synth.history import actor_from_user
    with pytest.raises(Forbidden):
        wf.close_case(sdb, c, actor_from_user(coord_user), "authorised_decision", reason="moved_abroad",
                      note="coordinator cannot bypass the clinical review")
    with pytest.raises(Forbidden):
        wf.resolve_review(sdb, c, actor_from_user(coord_user), note="coordinator is not a clinician")
    lead = _lead(sdb, "colorectal_fit")
    with pytest.raises(Invalid):
        wf.resolve_review(sdb, c, lead, note="short")
    wf.resolve_review(sdb, c, lead, note="Lab confirmed unit ug/g; proceed on colonoscopy pathway.")
    assert not c.review_required and c.state == S.TRIAGED


def test_deceased_patient_never_messaged_and_needs_authorised_closure(sdb):
    from wasl.comms.service import send_with_ladder

    p = _patient(sdb, deceased=True)
    _, c = _ingest(sdb, p)
    lead = _lead(sdb, "colorectal_fit")
    with pytest.raises(Invalid):
        wf.resolve_review(sdb, c, lead, note="Trying to resume the pathway for a deceased patient")
    with pytest.raises(DomainError):
        send_with_ladder(sdb, c, "result_notification", SYSTEM)
    wf.close_case(sdb, c, lead, "authorised_decision", reason="deceased", note="Deceased; GP informed by letter.")
    assert c.state == S.CLOSED and not c.review_required


# H-06 the ML score never changes a clinical tier ----------------------------------------------------------
def test_tier_downgrade_interlock(sdb):
    _, c = _ingest(sdb, _patient(sdb), value=500)
    assert c.tier == "urgent"
    with pytest.raises(TierDowngradeError):
        c.tier = "routine"
    c.tier = "critical"  # escalation is always allowed


def test_clinician_downgrade_is_explicit_and_audited(sdb):
    from wasl.models import AuditEvent

    _, c = _ingest(sdb, _patient(sdb, age=30), value=500)
    lead = _lead(sdb, "colorectal_fit")
    wf.resolve_review(sdb, c, lead, note="Symptomatic patient already booked; routine follow-up agreed.", tier="routine")
    ev = sdb.query(AuditEvent).filter(AuditEvent.case_id == c.id, AuditEvent.event == "case.review_resolved").one()
    assert ev.detail["downgrade"] is True and ev.detail["tier_from"] == "urgent"


def test_ml_scoring_never_changes_tiers(sdb):
    from wasl.ml import registry

    before = dict(sdb.query(Case.id, Case.tier).all())
    registry.score_open_cases(sdb)
    sdb.flush()
    sdb.expire_all()
    after = dict(sdb.query(Case.id, Case.tier).all())
    assert before == after


def test_ml_code_never_writes_tier():
    """Static guard: no module under wasl/ml assigns or bulk-updates Case.tier."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parent.parent / "wasl" / "ml"
    for f in root.rglob("*.py"):
        src = f.read_text()
        assert not re.search(r"\.tier\s*=[^=]|Case\.tier\s*:", src), f


def test_queue_ml_orders_only_within_tier():
    from wasl.services.queue import sort_key

    rows = [{"sla_status": "green", "tier": t, "risk_score": r, "due_at": "2026", "review_required": False}
            for t in TIER_RANK for r in (0.0, 0.5, 0.99)]
    random.Random(1).shuffle(rows)
    rows.sort(key=sort_key)
    ranks = [TIER_RANK[r["tier"]] for r in rows]
    assert ranks == sorted(ranks), "a high ML score moved a case ahead of a more urgent tier"


# H-07 ingest paths: unknown test codes, unmatched patients, units from HL7 --------------------------------
def _hl7(mrn, value, ctrl, code="29771-3", unit="ug/g", status="F", dob="19760101", extra=""):
    return "\r".join([f"MSH|^~\\&|LIS|LAB|WASL|PROG|20261020093000||ORU^R01^ORU_R01|{ctrl}|P|2.5.1",
                      f"PID|1||{mrn}^^^WASL^MR||SYN^PAT||{dob}|F",
                      f"OBX|1|NM|{code}^test^LN||{value}|{unit}|||||{status}"] + ([extra] if extra else []))


def test_hl7_unknown_code_is_kept_for_review(client, as_user):
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    r = client.post("/api/ingest/hl7v2", headers=h, content=_hl7("X1", "5", f"FS-UNK-{random.random()}", code="1234-5"))
    body = r.json()
    assert body["status"] == "needs_review" and "MSA|AE" in body["ack"] and "1234-5" in body["reason"]
    items = client.get("/api/ingest/messages?status=needs_review", headers=h).json()["items"]
    assert any(i["message_id"] == body["message_id"] for i in items)


def test_hl7_unit_mmol_mol_is_respected(client, as_user, db):
    p = _patient(db, sex="male")
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    body = client.post("/api/ingest/hl7v2", headers=h,
                       content=_hl7(p.mrn, "40", f"FS-MMOL-{random.random()}", code="59261-8", unit="mmol/mol",
                                    dob=f"{p.birth_year}0101")).json()
    assert body["case"]["tier"] == "routine" and body["case"]["rule_id"] == "DM-A1C-PRE"  # not "critical 40 %"


def test_hl7_corrected_status_goes_to_review(client, as_user, db):
    p = _patient(db)
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    body = client.post("/api/ingest/hl7v2", headers=h, content=_hl7(p.mrn, "250", f"FS-C-{random.random()}", status="C",
                                                                    dob=f"{p.birth_year}0101")).json()
    assert body["case"]["review_required"] and "corrected_result" in body["case"]["review_reasons"]


def test_hl7_missing_control_id_is_not_dropped(client, as_user, db):
    p = _patient(db)
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    msg = _hl7(p.mrn, "250", "", dob=f"{p.birth_year}0101")
    b1 = client.post("/api/ingest/hl7v2", headers=h, content=msg).json()
    b2 = client.post("/api/ingest/hl7v2", headers=h, content=msg).json()
    assert b1["message_id"].startswith("NOID-") and b1["case"] and b2["case"]["id"] == b1["case"]["id"]


def test_failed_patient_match_goes_to_match_review(client, as_user):
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    body = client.post("/api/ingest/hl7v2", headers=h, content=_hl7("NOBODY", "250", f"FS-NM-{random.random()}")).json()
    assert body["status"] == "needs_match_review" and body["case"] is None


def test_hpv_with_reflex_cytology_two_obx(client, as_user, db):
    p = _patient(db, age=40)
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    msg = _hl7(p.mrn, "HPV other hr", f"FS-CX-{random.random()}", code="77379-6", unit="", dob=f"{p.birth_year}0101",
               extra="OBX|2|ST|10524-7^cytology^LN||HSIL||||||F")
    body = client.post("/api/ingest/hl7v2", headers=h, content=msg).json()
    assert body["case"]["tier"] == "urgent" and body["case"]["rule_id"] == "CX-HIGH-GRADE"


# H-08 closure safety --------------------------------------------------------------------------------------
@pytest.mark.parametrize("prog,value,text,unit", [("cervical", None, "HPV other hr", None),
                                                   ("diabetes_hba1c", 7.0, None, "%")])
def test_negative_confirmatory_needs_surveillance_plan(sdb, prog, value, text, unit):
    from wasl.synth.history import actor_from_user
    from wasl.models import User

    p = _patient(sdb, sex="female", age=45)
    _, c = _ingest(sdb, p, prog, value, text, unit)
    assert c.needs_confirmation and not c.review_required
    gp = actor_from_user(sdb.query(User).filter(User.practitioner_id == p.gp_id).one())
    wf.transition(sdb, c, S.TRIAGED, gp)
    c.state = S.ACKNOWLEDGED  # fast-forward the contact steps (tested elsewhere)
    wf.order_confirmatory(sdb, c, gp, 1)
    wf.record_confirmatory_result(sdb, c, gp, "negative")
    with pytest.raises(TransitionError, match="surveillance"):
        wf.close_case(sdb, c, gp, "evidence", evidence_type="negative_confirmatory_result", note="negative repeat")
    wf.start_care(sdb, c, gp, "surveillance", "Repeat test in 12 months; recall booked.")
    wf.close_case(sdb, c, gp, "evidence", evidence_type="care_plan", note="Surveillance plan active.")
    assert c.state == S.CLOSED


def test_patient_question_reaches_a_clinician_when_gp_has_left(sdb):
    from wasl.synth.history import patient_actor

    p = _patient(sdb)
    _, c = _ingest(sdb, p)
    gp = sdb.get(Practitioner, p.gp_id)
    gp.active = False
    try:
        cr = wf.create_consult(sdb, c, patient_actor(p), "Can I speak to a doctor first please?")
        tgt = sdb.get(Practitioner, cr.target_practitioner_id)
        assert tgt.active and tgt.role in ("gp", "specialist", "clinical_lead")
    finally:
        gp.active = True


def test_every_open_case_has_an_owner_even_without_a_clinical_lead(sdb):
    leads = sdb.query(Practitioner).filter(Practitioner.role == "clinical_lead").all()
    for x in leads:
        x.active = False
    try:
        _, c = _ingest(sdb, _patient(sdb), unit=None)
        assert c is not None and c.owner_id is not None and c.review_required
    finally:
        for x in leads:
            x.active = True
