"""Property-based tests (Hypothesis): random results and random event sequences must preserve the safety
invariants. See docs/clinical_safety_case.md for the hazards each property covers."""
from __future__ import annotations

import math
import random
from datetime import timedelta

import pytest
from hypothesis import HealthCheck, assume, event, given, settings, strategies as st

from wasl.clinical_rules import (TIER_RANK, UNIT_ALIASES, PatientContext, ResultInput, evaluate)

PROGS = ["colorectal_fit", "cervical", "breast", "diabetes_hba1c"]
TEXTS = ["HPV16", "HPV18", "HPV other hr", "HPV negative", "HPV invalid", "NILM", "ASC-US", "LSIL", "HSIL", "ASC-H",
         "AGC", "SCC", "unsatisfactory", "BI-RADS 0", "BI-RADS 1", "BI-RADS 2", "BI-RADS 3", "BI-RADS 4", "BI-RADS 4B",
         "BI-RADS 5", "BI-RADS 6", "positive", "negative", "spoilt", "", " ", "n/a", "HPV16|HSIL", "HPV other hr|NILM"]
UNITS = list(UNIT_ALIASES) + [None, "", "mg", "g/L", "IU", "ug/L"]
STATUSES = ["final", "final", "final", "amended", "corrected", "preliminary", "entered-in-error", "cancelled", "x"]

values = st.one_of(st.none(), st.floats(allow_nan=True, allow_infinity=True, width=32),
                   st.floats(min_value=0, max_value=2000), st.integers(-10, 20000).map(float))
results = st.builds(ResultInput, programme=st.sampled_from(PROGS + ["unknown_prog"]), value=values,
                    value_text=st.one_of(st.none(), st.sampled_from(TEXTS), st.text(max_size=12)),
                    unit=st.sampled_from(UNITS), test=st.sampled_from([None, None, "FIT", "HPV", "MAMMO", "HBA1C", "FPG", "X"]),
                    status=st.sampled_from(STATUSES), cytology=st.one_of(st.none(), st.sampled_from(TEXTS[5:13])))
contexts = st.builds(PatientContext, age=st.one_of(st.none(), st.integers(0, 110)),
                     sex=st.sampled_from(["female", "male", None, "other"]), pregnant=st.booleans(),
                     deceased=st.booleans(), under_specialist_care=st.booleans(), haemoglobinopathy=st.booleans())


@settings(max_examples=3000, deadline=None)
@given(results, contexts)
def test_rules_total_and_fail_safe(r, ctx):
    d = evaluate(r, ctx)  # never raises
    assert d.disposition in ("negative", "actionable", "review")
    if d.disposition == "review":
        assert d.reasons and d.tier in TIER_RANK
    if d.disposition == "actionable":
        assert d.tier in TIER_RANK and d.pathway and d.rule_id
    if d.clinical_tier and d.tier:
        assert TIER_RANK[d.tier] <= TIER_RANK[d.clinical_tier], "a review floor lowered the clinical tier"
    exceptional = ctx.pregnant or ctx.deceased or ctx.under_specialist_care or ctx.age is None or \
        (r.status or "final") != "final"
    if exceptional and r.programme in PROGS:
        assert d.disposition == "review", "an exceptional context was decided automatically"
    if r.programme == "diabetes_hba1c" and r.value is not None and isinstance(r.value, float) and \
            not math.isfinite(r.value):
        assert d.disposition == "review"


def _rank(d):
    return 9 if d.tier is None else TIER_RANK[d.tier]


@settings(max_examples=1500, deadline=None)
@given(st.floats(0, 10000), st.floats(0, 10000))
def test_fit_monotone(a, b):
    lo, hi = sorted((a, b))
    ctx = PatientContext(age=50, sex="male")
    assert _rank(evaluate(ResultInput("colorectal_fit", hi, unit="ug/g"), ctx)) <= \
        _rank(evaluate(ResultInput("colorectal_fit", lo, unit="ug/g"), ctx))


@settings(max_examples=1500, deadline=None)
@given(st.sampled_from([("%", 4.0, 20.0), ("mmol/mol", 20.0, 195.0), ("mg/dL", 54.0, 1500.0), ("mmol/L", 3.0, 83.0)]),
       st.floats(0, 1), st.floats(0, 1))
def test_diabetes_monotone_above_low_range(spec, x, y):
    unit, lo_, hi_ = spec
    lo, hi = sorted((lo_ + x * (hi_ - lo_), lo_ + y * (hi_ - lo_)))
    ctx = PatientContext(age=50, sex="male")
    dl = evaluate(ResultInput("diabetes_hba1c", lo, unit=unit), ctx)
    dh = evaluate(ResultInput("diabetes_hba1c", hi, unit=unit), ctx)
    assert _rank(dh) <= _rank(dl), (unit, lo, hi, dl.rule_id, dh.rule_id)


@settings(max_examples=1000, deadline=None)
@given(st.floats(3.0, 20.0))
def test_hba1c_same_result_in_either_unit_never_less_urgent(pct):
    from wasl.clinical_rules import hba1c_pct_to_mmol

    ctx = PatientContext(age=50, sex="male")
    a = evaluate(ResultInput("diabetes_hba1c", round(pct, 1), unit="%"), ctx)
    b = evaluate(ResultInput("diabetes_hba1c", round(hba1c_pct_to_mmol(round(pct, 1))), unit="mmol/mol"), ctx)
    assume(a.disposition != "review" and b.disposition != "review")
    assert _rank(b) <= _rank(a) and b.creates_case >= a.creates_case


# ---------------------------------------------------------------- random event sequences through the DB
ACTIONS = ["ingest", "ingest_same", "transition", "transition", "resolve", "close_evidence", "close_authorised", "resolve", "sweep", "score",
           "advance", "flag_tier"]


@pytest.mark.usefixtures("seeded")
@settings(max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow, HealthCheck.data_too_large])
@given(st.lists(st.tuples(st.sampled_from(ACTIONS), st.integers(0, 10**6)), min_size=5, max_size=40),
       st.lists(results, min_size=1, max_size=6))
def test_random_event_sequences_preserve_invariants(ops, rs):
    from sqlalchemy import select

    from wasl import audit
    from wasl.db import session_factory
    from wasl.ml import registry
    from wasl.models import AuditEvent, Case, DiagnosticReport, Patient, StepTask, User, utcnow
    from wasl.security import SYSTEM
    from wasl.services import workflow as wf
    from wasl.services.errors import DomainError
    from wasl.services.escalation import run_sweep
    from wasl.statemachine import S, TransitionError
    from wasl.synth.history import actor_from_user

    db = session_factory()()
    try:
        now = utcnow()
        users = [actor_from_user(u) for u in db.scalars(select(User).where(User.practitioner_id.is_not(None)))]
        pts = []
        for i in range(3):
            p = Patient(mrn=f"PROP-{random.random()}", given_name="P", family_name=str(i), gender=["female", "male"][i % 2],
                        birth_year=now.year - (30 + 15 * i), age_band="x", nationality_group="x", language="en",
                        region="Abu Dhabi", phone_valid=True, has_smartphone=True, whatsapp=True, managing_org_id=3,
                        gp_id=19, responsiveness=0.5, pregnant=False, deceased=(i == 2 and len(rs) > 4))
            db.add(p)
            db.flush()
            pts.append(p)
        mine: list[Case] = []
        max_tier_seen: dict[int, int] = {}
        last = rs[0]
        for op, k in ops:
            rnd = random.Random(k)
            try:
                with db.begin_nested():
                    if op in ("ingest", "ingest_same"):
                        good = [ResultInput("colorectal_fit", 250.0, unit="ug/g"), ResultInput("breast", value_text="BI-RADS 0"),
                                ResultInput("diabetes_hba1c", 7.2, unit="%"), ResultInput("cervical", value_text="HPV16")]
                        r = last if op == "ingest_same" else (rs[k % len(rs)] if rnd.random() < 0.5 else rnd.choice(good))
                        last = r
                        prog = r.programme if r.programme in PROGS else "colorectal_fit"
                        _, c = wf.ingest_result(db, pts[k % 3], prog, value=r.value if r.value is None or
                                                math.isfinite(r.value) else None, value_text=r.value_text, unit=r.unit,
                                                test=r.test, status=r.status, cytology=r.cytology, performer_org_id=1,
                                                now=now)
                        if c is not None and c not in mine:
                            mine.append(c)
                    elif not mine:
                        continue
                    else:
                        c = mine[k % len(mine)]
                        clin = [u for u in users if u.role in ("clinical_lead", "gp")]
                        actor = (rnd.choice(clin) if rnd.random() < 0.6 else users[k % len(users)]) \
                            if rnd.random() < 0.85 else SYSTEM
                        if op == "transition":
                            from wasl.statemachine import allowed_targets

                            tgt = sorted(allowed_targets(c.state)) if rnd.random() < 0.75 else list(S)
                            if tgt:
                                wf.transition(db, c, rnd.choice(tgt), actor, now=now, note="random op")
                        elif op == "close_evidence":
                            wf.close_case(db, c, actor, "evidence", evidence_type=rnd.choice(
                                ["care_plan", "appointment_fulfilled", "negative_confirmatory_result"]), note="x", now=now)
                        elif op == "close_authorised":
                            wf.close_case(db, c, actor, "authorised_decision", reason=rnd.choice(
                                ["deceased", "moved_abroad", "moved_abroad", "unable_to_contact", "informed_decline", "bogus"]),
                                note=rnd.choice(["", "valid rationale text", "valid rationale text"]), now=now)
                        elif op == "resolve":
                            if rnd.random() < 0.8:
                                actor = rnd.choice([u for u in users if u.role == "clinical_lead"])
                            wf.resolve_review(db, c, actor, note=rnd.choice(["", "reviewed carefully, proceed",
                                                                            "reviewed carefully, proceed"]),
                                              tier=rnd.choice([None, None, "routine", "critical"]), now=now)
                        elif op == "sweep":
                            run_sweep(db, now=now, case_ids=[x.id for x in mine])
                        elif op == "score":
                            registry.score_open_cases(db, now=now)
                        elif op == "advance":
                            now = now + timedelta(hours=rnd.choice([1, 12, 72, 400]))
                        elif op == "flag_tier":
                            c.tier = rnd.choice(list(TIER_RANK))  # unauthorised direct write (only escalation may pass)
                event(f"ok:{op}")
            except (DomainError, TransitionError, ValueError, KeyError, PermissionError) as e:
                event(f"refused:{op}:{type(e).__name__}")  # refused operations are fine; the invariants below must hold regardless
            db.flush()
            # ---- invariants after every step
            for c in mine:
                db.refresh(c)
                prev = max_tier_seen.get(c.id)
                downgraded = db.scalar(select(AuditEvent.id).where(AuditEvent.case_id == c.id,
                                                                   AuditEvent.event == "case.review_resolved"))
                if prev is not None and TIER_RANK[c.tier] > prev:
                    assert downgraded, f"case {c.id} tier lowered without an audited clinician review"
                max_tier_seen[c.id] = TIER_RANK[c.tier] if downgraded else min(prev if prev is not None else 9,
                                                                                 TIER_RANK[c.tier])
                if c.state != S.CLOSED:
                    assert c.owner_id is not None
                    running = db.scalars(select(StepTask).where(StepTask.case_id == c.id,
                                                                StepTask.status == "in-progress")).all()
                    assert len(running) == 1
                    for s_ in running:
                        assert s_.due_at is None or s_.due_at >= s_.started_at
                else:
                    assert not c.review_required
                    assert (c.closure_type == "evidence" and c.closure_evidence_id) or \
                        (c.closure_type == "authorised_decision" and c.closure_reason and len(c.closure_note or "") >= 10)
            for p in pts:
                for prog in PROGS:
                    n_open = db.query(Case).filter(Case.patient_id == p.id, Case.programme == prog,
                                                   Case.state != S.CLOSED).count()
                    assert n_open <= 1
                lost = db.query(DiagnosticReport).filter(DiagnosticReport.patient_id == p.id,
                                                          DiagnosticReport.conclusion.in_(["actionable", "needs_review"]),
                                                          DiagnosticReport.case_id.is_(None)).count()
                assert lost == 0, "an actionable/review result has no case"
        db.flush()
    finally:
        db.rollback()
        db.close()


def test_audit_chain_valid_after_property_runs(db):
    from wasl import audit

    assert audit.verify_chain(db)["valid"]
