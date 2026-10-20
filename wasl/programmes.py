"""Screening programme definitions and deterministic triage rules.

The thresholds are ILLUSTRATIVE prototype defaults, not clinical guidance. A programme's
clinical lead must agree real values before any pilot. AI is never used for triage.
"""
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
        "diabetes_hba1c", "Diabetes screening - HbA1c", True, "Repeat HbA1c / fasting glucose",
        "Diabetes clinic", "endocrinology", "4548-4", "Haemoglobin A1c", "%"),
}

# Urgency tiers aligned with the Wasl design: T0 Critical, T1 Urgent, T2 Soon, T3 Routine.
TIERS = ("critical", "urgent", "high", "routine")
TIER_CODE = {"critical": "T0", "urgent": "T1", "high": "T2", "routine": "T3"}
TIER_LABEL = {"critical": "T0 Critical", "urgent": "T1 Urgent", "high": "T2 Soon", "routine": "T3 Routine"}
# T0/T1 must be confirmed by a clinician; the rules engine may auto-triage T2/T3.
CLINICIAN_CONFIRMED_TIERS = frozenset({"critical", "urgent"})
# T2/T3 step clocks count UAE working days; T0/T1 clocks run 24/7 (on-call roster out of hours).
WORKING_DAY_TIERS = frozenset({"high", "routine"})


def triage_tier(programme: str, value: float | None, value_text: str | None) -> str:
    """Deterministic, clinician-configurable triage rule (illustrative)."""
    if programme == "colorectal_fit":
        return "urgent" if (value or 0) >= 400 else "high"
    if programme == "cervical":
        return "high" if value_text in {"HPV16", "HPV18"} else "routine"
    if programme == "breast":
        return "urgent" if value_text in {"BI-RADS 4", "BI-RADS 5"} else "high"
    if programme == "diabetes_hba1c":
        v = value or 0
        # >= 15% risks hyperglycaemic crisis: same-day clinical contact (T0, runs alongside the lab
        # critical-value phone process, never instead of it)
        return "critical" if v >= 15.0 else "high" if v >= 10.0 else "routine"
    raise ValueError(f"unknown programme {programme}")


def needs_confirmation(programme: str, value: float | None, value_text: str | None) -> bool:
    """Programme-specific pathway (illustrative, after common international practice):
    * FIT positive -> colonoscopy referral directly (no confirmatory step).
    * HPV16/18 -> direct colposcopy; other high-risk HPV -> reflex cytology first.
    * BI-RADS 0 -> recall for diagnostic imaging first; BI-RADS 4/5 -> direct biopsy referral.
    * HbA1c in the diabetes range -> repeat HbA1c / fasting glucose, then GP visit (T0: no delay for repeat).
    """
    if programme == "colorectal_fit":
        return False
    if programme == "cervical":
        return value_text not in {"HPV16", "HPV18"}
    if programme == "breast":
        return value_text == "BI-RADS 0"
    if programme == "diabetes_hba1c":
        return (value or 0) < 15.0
    return PROGRAMMES[programme].needs_confirmation


def is_actionable(programme: str, value: float | None, value_text: str | None) -> bool:
    if programme == "colorectal_fit":
        return (value or 0) >= 120
    if programme == "cervical":
        return bool(value_text and value_text.startswith("HPV"))
    if programme == "breast":
        return value_text in {"BI-RADS 0", "BI-RADS 4", "BI-RADS 5"}
    if programme == "diabetes_hba1c":
        return (value or 0) >= 6.5
    return False
