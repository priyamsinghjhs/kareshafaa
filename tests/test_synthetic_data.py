"""Synthetic-data calibration and internal-consistency tests (docs/synthetic_data.md)."""
from __future__ import annotations

import random
from collections import Counter

import pytest

from kareshafaa.clinical_rules import PatientContext, ResultInput, evaluate
from kareshafaa.synth import cohort as C
from kareshafaa.synth import results as R


def _tables():
    for mod in (C, R):
        for k, v in vars(mod).items():
            if k == "DEFECTS":
                continue  # independent per-result rates, not a distribution
            if isinstance(v, list) and v and isinstance(v[0], tuple) and isinstance(v[-1][-1], float):
                yield f"{mod.__name__}.{k}", v
            if isinstance(v, dict) and v and all(isinstance(x, list) for x in v.values()):
                for kk, vv in v.items():
                    if vv and isinstance(vv[0], tuple):
                        yield f"{mod.__name__}.{k}[{kk}]", vv


@pytest.mark.parametrize("name,table", list(_tables()), ids=[n for n, _ in _tables()])
def test_distribution_tables_sum_to_one(name, table):
    """Regression for a real bug: A1C_BANDS summed to 0.88 and the weighted picker put the missing 12 % on
    the LAST band (HbA1c >= 15 %), producing ~1,800 'critical' cases in the 20k cohort."""
    assert abs(sum(w for *_, w in table) - 1.0) < 1e-9, name


def _rates(programme, n=40000, seed=7):
    rng = random.Random(seed)
    ctx = PatientContext(age=50, sex="female")
    out = Counter()
    for _ in range(n):
        r = R.draw_result(rng, programme, ctx, enriched=False)
        d = evaluate(ResultInput(programme, r.value, r.value_text, r.unit, r.test, r.status, r.cytology), ctx)
        out[d.rule_id] += 1
        out["_case"] += d.creates_case
    return {k: v / n for k, v in out.items()}


def test_fit_positivity_calibrated():
    r = _rates("colorectal_fit")
    pos = r.get("CRC-FIT-POS", 0) + r.get("CRC-FIT-URGENT", 0)
    assert 0.035 <= pos <= 0.055, pos  # target 4.5 % (assumption between UK 1.7-2.1 % @120 and AD 13.5 %)


def test_hrhpv_prevalence_calibrated():
    r = _rates("cervical")
    hpv_pos = sum(v for k, v in r.items() if k.startswith(("CX-HPV1618", "CX-HPVOTHER", "CX-HIGH", "CX-SUSP")))
    assert 0.12 <= hpv_pos <= 0.17, hpv_pos  # UAE 14.7 % hrHPV


def test_mammography_recall_calibrated():
    r = _rates("breast")
    recall = r.get("BR-0", 0) + r.get("BR-4", 0) + r.get("BR-5", 0) + r.get("BR-3", 0)
    assert 0.08 <= recall <= 0.13, recall  # DoH target < 10-15 %; BCSC mean 11.6 %


def test_diabetes_mix_calibrated():
    r = _rates("diabetes_hba1c")
    diab = sum(r.get(k, 0) for k in ("DM-A1C-DIAB", "DM-A1C-HIGH", "DM-A1C-CRIT", "DM-FPG-DIAB", "DM-FPG-HIGH",
                                       "DM-FPG-CRIT"))
    pre = r.get("DM-A1C-PRE", 0) + r.get("DM-FPG-PRE", 0)
    crit = r.get("DM-A1C-CRIT", 0) + r.get("DM-FPG-CRIT", 0)
    assert 0.10 <= diab <= 0.145, diab   # IDF 2021 UAE 12.3 %
    assert 0.15 <= pre <= 0.21, pre      # IGT 18.3 % proxy
    assert crit <= 0.005, crit           # very high HbA1c is rare (assumption 0.2 %)


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_cohort_internal_consistency(seed):
    cohort = C.make_cohort(5000, seed, min_age=18)
    for p in cohort:
        assert 18 <= p.age <= 84
        if p.programme in ("cervical", "breast"):
            assert p.gender == "female", "cervical/breast screening for a male"
        if p.pregnant:
            assert p.gender == "female" and 18 <= p.age <= 45
        lo, hi = C.ELIGIBLE[p.programme]
        assert (lo <= p.age <= hi) or p.out_of_age, "ineligible age without the out-of-age flag"
    female = sum(p.gender == "female" for p in cohort) / len(cohort)
    assert 0.30 <= female <= 0.36, female  # Abu Dhabi Census 2023: 33 % female


def test_enriched_mode_only_actionable_and_same_support():
    rng = random.Random(3)
    ctx = PatientContext(age=50, sex="male")
    for _ in range(2000):
        r = R.draw_result(rng, "colorectal_fit", ctx, enriched=True)
        d = evaluate(ResultInput("colorectal_fit", r.value, r.value_text, r.unit, r.test, r.status, r.cytology), ctx)
        assert d.creates_case
        if r.value is not None and r.unit == "ug/g":
            assert 0 <= r.value <= 10000 or r.defect == "implausible"


@pytest.mark.parametrize("programme", ["colorectal_fit", "cervical", "breast", "diabetes_hba1c"])
def test_values_physiologically_plausible_unless_defect(programme):
    rng = random.Random(11)
    ctx = PatientContext(age=50, sex="female")
    for _ in range(5000):
        r = R.draw_result(rng, programme, ctx, enriched=False)
        if r.defect:
            continue
        d = evaluate(ResultInput(programme, r.value, r.value_text, r.unit, r.test, r.status, r.cytology), ctx)
        assert d.disposition != "review" or d.rule_id in ("BR-3", "BR-6", "CX-HPVNEG-LG", "CX-HPVNEG-HG",
                                                          "DM-A1C-LOW", "DM-FPG-HYPO"), (r, d.rule_id, d.reasons)
        if r.unit == "%":
            assert 3.0 <= r.value <= 20.0
        if r.unit == "mmol/mol":
            assert 9 <= r.value <= 195
