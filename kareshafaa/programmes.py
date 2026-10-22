"""Screening programme definitions. The decision rules live in kareshafaa/clinical_rules.py (sourced in
docs/clinical_rules.md); a programme's clinical lead must sign them off before any pilot. AI is never used
for triage."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Programme:
    key: str
    name: str
    needs_confirmation: bool
    confirmatory_test: str | None
    referral_service: str | None
    specialty: str | None
    screening_code: str
    screening_display: str
    unit: str | None


PROGRAMMES: dict[str, Programme] = {
    "colorectal_fit": Programme(
        "colorectal_fit", "Bowel (colorectal) screening - FIT", False, None,
        "Colonoscopy", "gastroenterology", "29771-3", "Faecal immunochemical test (FIT) haemoglobin", "ug/g"),
    "cervical": Programme(
        "cervical", "Cervical screening - HPV", True, "Cervical cytology (reflex)",
        "Colposcopy", "gynaecology", "77379-6", "HPV high-risk DNA", None),
    "breast": Programme(
        "breast", "Breast screening - mammography", True, "Diagnostic imaging / biopsy",
        "Breast clinic", "breast_surgery", "24606-6", "Screening mammogram (BI-RADS)", None),
    "diabetes_hba1c": Programme(
        "diabetes_hba1c", "Diabetes screening - HbA1c / fasting glucose", True, "Repeat HbA1c / fasting glucose",
        "Diabetes clinic", "endocrinology", "4548-4", "Haemoglobin A1c", "%"),
}

# Urgency tiers aligned with the KareShafaa design: T0 Critical, T1 Urgent, T2 Soon, T3 Routine.
TIERS = ("critical", "urgent", "high", "routine")
TIER_CODE = {"critical": "T0", "urgent": "T1", "high": "T2", "routine": "T3"}
TIER_LABEL = {"critical": "T0 Critical", "urgent": "T1 Urgent", "high": "T2 Soon", "routine": "T3 Routine"}
# T0/T1 must be confirmed by a clinician; the rules engine may auto-triage T2/T3.
CLINICIAN_CONFIRMED_TIERS = frozenset({"critical", "urgent"})
# T2/T3 step clocks count UAE working days; T0/T1 clocks run 24/7 (on-call roster out of hours).
WORKING_DAY_TIERS = frozenset({"high", "routine"})


DEFAULT_UNIT = {"colorectal_fit": "ug/g", "diabetes_hba1c": "%"}


def _decide(programme: str, value: float | None, value_text: str | None, unit: str | None = None):
    """Thin compatibility wrappers over the clinical rules engine (kareshafaa/clinical_rules.py) for callers that
    work in the programme's default unit. New code should call clinical_rules.evaluate directly."""
    from .clinical_rules import ResultInput, evaluate

    return evaluate(ResultInput(programme, value, value_text, unit or (DEFAULT_UNIT.get(programme) if value is not None else None)))


def triage_tier(programme: str, value: float | None, value_text: str | None) -> str | None:
    return _decide(programme, value, value_text).tier


def needs_confirmation(programme: str, value: float | None, value_text: str | None) -> bool:
    return _decide(programme, value, value_text).needs_confirmation


def is_actionable(programme: str, value: float | None, value_text: str | None) -> bool:
    return _decide(programme, value, value_text).creates_case
