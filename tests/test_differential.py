"""Differential testing against the independent reference implementation (kareshafaa_reference/rules.py).
The full 1M+ run is `make differential`; this keeps a 60k-case seeded sample in the normal suite."""
import random
import sys
from collections import Counter
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import differential_test as dt  # noqa: E402

from kareshafaa.clinical_rules import PatientContext, ResultInput, evaluate  # noqa: E402
from kareshafaa_reference.rules import reference  # noqa: E402

F = dict(age=50, sex="female")


@pytest.mark.parametrize("seed", range(3))
def test_no_safety_critical_disagreement(seed):
    random.seed(7_000 + seed)
    c = Counter(dt.compare(*dt.gen_case())[0] for _ in range(20_000))
    assert not {k: v for k, v in c.items() if k.startswith("SAFETY")}, c


# regression tests for the bugs the differential test found (Iteration 7)
@pytest.mark.parametrize("value", [-1, -4, -1.0, 4.5, 7, 1e6, float("nan")])
def test_breast_numeric_out_of_range_is_never_a_birads_category(value):
    d = evaluate(ResultInput("breast", value), PatientContext(**F))
    assert d.disposition == "review" and d.tier == "urgent" and "malformed_value" in d.reasons


@pytest.mark.parametrize("text", ["-1", ":2", "- 1", "-4"])
def test_breast_bare_separator_text_is_malformed(text):
    d = evaluate(ResultInput("breast", value_text=text), PatientContext(**F))
    assert d.disposition == "review" and d.tier == "urgent"


@pytest.mark.parametrize("value,unit,tier", [(5.0, "%", "high"), (5.6, "%", "high"), (30, "mmol/mol", "high"),
                                              (3.5, "%", "high"), (6.0, "%", "high"), (12.0, "%", "high"),
                                              (16.0, "%", "critical")])
def test_haemoglobinopathy_reviews_every_hba1c(value, unit, tier):
    ctx = PatientContext(age=50, sex="male", haemoglobinopathy=True)
    d = evaluate(ResultInput("diabetes_hba1c", value, unit=unit), ctx)
    assert d.disposition == "review" and "haemoglobinopathy" in d.reasons and d.tier == tier
    ref = reference("diabetes_hba1c", value, unit=unit, age=50, sex="male", haemoglobinopathy=True)
    assert ref.kind == "review" and ref.tier == tier


def test_haemoglobinopathy_does_not_affect_glucose():
    d = evaluate(ResultInput("diabetes_hba1c", 90, unit="mg/dL", test="FPG"),
                 PatientContext(age=50, sex="male", haemoglobinopathy=True))
    assert d.disposition == "negative"


def test_blank_cytology_field_means_not_reported():
    d = evaluate(ResultInput("cervical", value_text="HPV negative", cytology=" "), PatientContext(age=40, sex="female"))
    assert d.disposition == "negative"


def test_hpv_invalid_low_grade_is_high_review():
    d = evaluate(ResultInput("cervical", value_text="HPV invalid", cytology="LSIL"), PatientContext(age=40, sex="female"))
    assert d.disposition == "review" and d.tier == "high" and d.reasons == ["hpv_invalid_low_grade"]


# edge cases found by mutation testing (Iteration 7)
@pytest.mark.parametrize("value,unit,test,expect", [
    (3.0, "%", None, ("review", "routine", "unexpectedly_low")), (2.99, "%", None, ("review", "urgent", "implausible_value")),
    (20.0, "%", None, ("actionable", "critical", None)), (20.01, "%", None, ("review", "urgent", "implausible_value")),
    (9.0, "mmol/mol", None, ("review", "routine", "unexpectedly_low")), (195.0, "mmol/mol", None, ("actionable", "critical", None)),
    (195.1, "mmol/mol", None, ("review", "urgent", "implausible_value")),
    (10.0, "mg/dL", "FPG", ("review", "urgent", "hypoglycaemia")), (9.9, "mg/dL", "FPG", ("review", "urgent", "implausible_value")),
    (1500.0, "mg/dL", "FPG", ("actionable", "critical", None)), (0.6, "mmol/L", "FPG", ("review", "urgent", "hypoglycaemia")),
    (83.0, "mmol/L", "FPG", ("actionable", "critical", None)), (83.1, "mmol/L", "FPG", ("review", "urgent", "implausible_value")),
])
def test_diabetes_plausibility_range_edges(value, unit, test, expect):
    d = evaluate(ResultInput("diabetes_hba1c", value, unit=unit, test=test), PatientContext(age=50, sex="male"))
    assert (d.disposition, d.tier) == expect[:2] and (expect[2] is None or expect[2] in d.reasons)
    r = reference("diabetes_hba1c", value, unit=unit, test=test, age=50, sex="male")
    assert (r.kind, r.tier) == expect[:2]


@pytest.mark.parametrize("value,expect", [(10000.0, ("actionable", "urgent")), (10000.1, ("review", "urgent")),
                                          (0.0, ("negative", None)), (-0.1, ("review", "urgent"))])
def test_fit_plausibility_edges(value, expect):
    d = evaluate(ResultInput("colorectal_fit", value, unit="ug/g"), PatientContext(age=50, sex="male"))
    assert (d.disposition, d.tier) == expect


@pytest.mark.parametrize("prog,unit", [("colorectal_fit", "ug/g"), ("diabetes_hba1c", "%"), ("breast", None)])
def test_boolean_value_is_malformed_not_one(prog, unit):
    d = evaluate(ResultInput(prog, True, unit=unit), PatientContext(age=50, sex="female"))
    assert d.disposition == "review" and d.tier == "urgent" and "malformed_value" in d.reasons
