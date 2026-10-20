"""Deterministic clinical decision rules for screening results (pure module: no DB, no ML).

Every rule has an id (RULE_*) that appears in docs/clinical_rules.md with its source. Design principles:

* FAIL SAFE. Anything the engine cannot interpret with certainty (unknown test, malformed or implausible value,
  missing/unknown unit, sex/age/pregnancy/deceased/specialist-care context, amended or preliminary status) is
  never dropped and never downgraded: it becomes a HUMAN REVIEW decision with machine-readable reasons and a
  tier at least as urgent as the most urgent plausible interpretation.
* NATIVE UNITS. Thresholds are compared in the unit the lab reported (HbA1c % vs mmol/mol, glucose mg/dL vs
  mmol/L) using the guideline-published value pairs; conversion is only used for display and plausibility.
* NO ML. Nothing here reads a model score; tiers come only from these rules or an authorised human.

PROTOTYPE: thresholds marked CONFIRM in the docs must be signed off by the programme clinical lead.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

TIER_RANK = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}  # lower = more urgent


def max_tier(*tiers: str | None) -> str | None:
    """Most urgent of the given tiers (None ignored)."""
    ts = [t for t in tiers if t]
    return min(ts, key=lambda t: TIER_RANK[t]) if ts else None


def is_downgrade(old: str | None, new: str | None) -> bool:
    return bool(old and new and TIER_RANK[new] > TIER_RANK[old])


# ----------------------------------------------------------------- configurable thresholds (clinical lead)
FIT_POSITIVE_UG_G = 80.0        # CONFIRM: UK NSC / Scotland / Wales 80; England moving 120 -> 80 by 2028
FIT_URGENT_UG_G = 400.0         # CONFIRM: local rule (no screening guideline) - very high f-Hb, faster pathway
FIT_MAX_PLAUSIBLE = 10000.0

HBA1C_PCT = {"prediabetes": 5.7, "diabetes": 6.5, "high": 10.0, "critical": 15.0}   # ADA 2026 Tables 2.1/2.2
# mmol/mol cut-offs = exact NGSP->IFCC conversion of the % cut-offs, rounded DOWN to 0.1 (safer). Integer lab
# reports classify exactly as ADA's published pairs (39 / 48 / 86 mmol/mol); decimals never fall on the less
# urgent side. The CRITICAL cut-off (local rule) also tolerates integer rounding by the lab: 15.0 % = 140.43
# mmol/mol may be reported as "140", so 139.9 is used (found by test_hba1c_units_agree_on_category).
HBA1C_MMOL = {"prediabetes": 38.7, "diabetes": 47.5, "high": 85.7, "critical": 139.9}
HBA1C_LOW_PCT, HBA1C_LOW_MMOL = 4.0, 20                                           # CONFIRM: suspiciously low
HBA1C_RANGE_PCT, HBA1C_RANGE_MMOL = (3.0, 20.0), (9.0, 195.0)                     # physiological/reportable

FPG_MGDL = {"hypo": 54, "prediabetes": 100, "diabetes": 126, "high": 300, "critical": 500}
FPG_MMOL = {"hypo": 3.0, "prediabetes": 5.6, "diabetes": 7.0, "high": 16.7, "critical": 27.7}
FPG_RANGE_MGDL, FPG_RANGE_MMOL = (10.0, 1500.0), (0.6, 83.0)

ELIGIBLE_AGE = {  # Department of Health Abu Dhabi
    "colorectal_fit": (40, 75), "cervical": (25, 65), "breast": (40, 200), "diabetes_hba1c": (18, 200)}
ELIGIBLE_SEX = {"cervical": {"female"}, "breast": {"female"}}

UNIT_ALIASES = {
    "ug/g": "ug/g", "µg/g": "ug/g", "ug hb/g": "ug/g", "µg hb/g": "ug/g", "mcg/g": "ug/g", "ug/g faeces": "ug/g",
    "%": "%", "% hb": "%", "%hb": "%", "% (ngsp)": "%", "ngsp %": "%", "dcct %": "%",
    "mmol/mol": "mmol/mol", "mmol/mol hb": "mmol/mol",
    "mg/dl": "mg/dL", "mmol/l": "mmol/L",
    "ng/ml": "ng/mL",
}

# Review-reason codes -> minimum tier for the human review (never lower than the clinical tier).
REVIEW_MIN_TIER = {
    "unknown_programme": "urgent", "unknown_test": "urgent", "malformed_value": "urgent", "missing_value": "urgent",
    "implausible_value": "urgent", "missing_unit": "urgent", "unknown_unit": "urgent", "unit_needs_assay_conversion": "urgent",
    "sex_mismatch": "urgent", "amended_result": "urgent", "corrected_result": "urgent", "result_retracted": "urgent",
    "preliminary_result": "high", "hypoglycaemia": "urgent", "suspected_invasive": "urgent", "known_malignancy": "urgent",
    "pregnant": "high", "deceased": "high", "under_specialist_care": "high", "haemoglobinopathy": "high",
    "screening_category_unexpected": "high", "hpv_negative_abnormal_cytology": "high", "hpv_invalid_low_grade": "high", "unexpectedly_low": "routine",
    "age_below_programme": "routine", "age_above_programme": "routine", "age_unknown": "high",
    "duplicate_result": "routine", "conflicting_result": "high", "result_after_closure": "high", "more_urgent_result": "urgent",
}


@dataclass(frozen=True)
class ResultInput:
    programme: str
    value: float | None = None
    value_text: str | None = None
    unit: str | None = None
    test: str | None = None            # FIT | HPV | CYTOLOGY | MAMMO | HBA1C | FPG (inferred if None)
    status: str = "final"              # final | preliminary | amended | corrected | entered-in-error | cancelled
    cytology: str | None = None        # reflex cytology accompanying an HPV result


@dataclass(frozen=True)
class PatientContext:
    age: int | None = None
    sex: str | None = None
    pregnant: bool = False
    deceased: bool = False
    under_specialist_care: bool = False   # for THIS programme (e.g. known cancer, endocrinology follow-up)
    haemoglobinopathy: bool = False


@dataclass
class Decision:
    disposition: str                     # negative | actionable | review
    tier: str | None
    pathway: str                         # pathway code
    next_step: str
    needs_confirmation: bool = False
    target: str | None = None            # target timeframe text (from the source)
    rule_id: str = ""
    reasons: list[str] = field(default_factory=list)
    clinical_tier: str | None = None     # tier from the clinical rule alone, before review floors
    normalised: dict = field(default_factory=dict)

    @property
    def creates_case(self) -> bool:
        return self.disposition in ("actionable", "review")

    @property
    def interpretation(self) -> str:
        return {"negative": "N", "actionable": "A", "review": "R"}[self.disposition]


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def norm_unit(unit: str | None) -> str | None:
    if unit is None or not str(unit).strip():
        return None
    u = str(unit).strip().lower().replace("\u00a0", " ")
    return UNIT_ALIASES.get(u, UNIT_ALIASES.get(u.replace(" ", ""), "?" + u))


def hba1c_pct_to_mmol(pct: float) -> float:
    """IFCC = (NGSP - 2.152) / 0.09148 (NGSP/IFCC master equation)."""
    return (pct - 2.152) / 0.09148


def hba1c_mmol_to_pct(mmol: float) -> float:
    return 0.09148 * mmol + 2.152


def _neg(rule: str, step: str = "Return to routine screening interval") -> Decision:
    return Decision("negative", None, "routine_recall", step, rule_id=rule)


def _act(tier: str, pathway: str, step: str, rule: str, *, confirm: bool = False, target: str | None = None,
         norm: dict | None = None) -> Decision:
    return Decision("actionable", tier, pathway, step, confirm, target, rule, [], tier, norm or {})


def _rev(reason: str, rule: str, step: str = "Clinician review of the result before any patient pathway",
         tier: str | None = None, norm: dict | None = None) -> Decision:
    t = max_tier(tier, REVIEW_MIN_TIER[reason])
    return Decision("review", t, "human_review", step, False, "Human review within the tier SLA", rule, [reason], tier, norm or {})


# ----------------------------------------------------------------- colorectal (FIT)
def _fit(r: ResultInput) -> Decision:
    text = (r.value_text or "").strip().lower()
    v = _num(r.value)
    if r.value is not None and v is None:
        return _rev("malformed_value", "CRC-ERR")
    if v is None:
        if text in ("positive", "pos", "detected", "abnormal"):
            return _act("high", "colonoscopy", "Specialist assessment then colonoscopy", "CRC-FIT-QUAL-POS",
                        target="Colonoscopy within 31 days of referral (DoH)")
        if text in ("negative", "neg", "not detected", "normal"):
            return _neg("CRC-FIT-QUAL-NEG", "Return to annual FIT screening")
        if text in ("invalid", "spoilt", "unsatisfactory", "insufficient", "rejected sample", "inadequate"):
            return _act("routine", "repeat_fit", "Send a new FIT kit and repeat the test", "CRC-FIT-REPEAT",
                        target="New kit sent within 5 working days (CONFIRM)")
        return _rev("missing_value" if not text else "malformed_value", "CRC-ERR")
    u = norm_unit(r.unit)
    if u is None:
        return _rev("missing_unit", "CRC-ERR", tier=_fit_tier(v) if 0 <= v <= FIT_MAX_PLAUSIBLE else None)
    if u == "ng/mL":
        return _rev("unit_needs_assay_conversion", "CRC-ERR")
    if u != "ug/g":
        return _rev("unknown_unit", "CRC-ERR")
    if not 0 <= v <= FIT_MAX_PLAUSIBLE:
        return _rev("implausible_value", "CRC-ERR")
    t = _fit_tier(v)
    if t is None:
        return _neg("CRC-FIT-NEG", "Return to annual FIT screening")
    rid = "CRC-FIT-URGENT" if t == "urgent" else "CRC-FIT-POS"
    return _act(t, "colonoscopy", "Specialist assessment then colonoscopy", rid,
                target="Colonoscopy within 31 days of referral (DoH)" + ("; expedite (local rule)" if t == "urgent" else ""),
                norm={"fit_ug_g": v})


def _fit_tier(v: float) -> str | None:
    if v >= FIT_URGENT_UG_G:
        return "urgent"
    if v >= FIT_POSITIVE_UG_G:
        return "high"
    return None


# ----------------------------------------------------------------- cervical (primary HPV + reflex cytology)
HPV_CODES = {
    "hpv16": "16", "hpv 16": "16", "hpv18": "18", "hpv 18": "18", "hpv16/18": "16", "hpv 16/18": "16", "hpv16 and 18": "16",
    "hpv other hr": "other", "hpv other": "other", "hrhpv other": "other", "other hr hpv": "other", "hpv other high risk": "other",
    "hpv positive": "other_unknown", "hrhpv positive": "other_unknown",
    "hpv negative": "neg", "hrhpv negative": "neg", "hpv not detected": "neg",
    "hpv invalid": "invalid", "hpv unsatisfactory": "invalid", "hpv inadequate": "invalid",
}
CYTO = {"nilm": "NILM", "asc-us": "ASC-US", "ascus": "ASC-US", "lsil": "LSIL", "asc-h": "ASC-H", "hsil": "HSIL",
        "agc": "AGC", "ais": "AIS", "scc": "SCC", "squamous cell carcinoma": "SCC", "adenocarcinoma": "SCC",
        "unsatisfactory": "UNSAT", "inadequate": "UNSAT"}
HIGH_GRADE = {"ASC-H", "HSIL", "AGC", "AIS"}


def _parse_cervical(r: ResultInput) -> tuple[str | None, str | None, bool]:
    """Returns (hpv, cytology, ok). value_text may be 'HPV16', 'HPV other hr|ASC-US', 'HSIL' etc."""
    parts = [p.strip().lower() for p in re.split(r"[|;,]", r.value_text or "") if p.strip()]
    if r.cytology and r.cytology.strip():  # a blank cytology field means "not reported"
        parts.append(r.cytology.strip().lower())
    hpv = cyto = None
    hpvs: set[str] = set()
    for p in parts:
        if p in HPV_CODES:
            hpvs.add(HPV_CODES[p])
        elif p in CYTO:
            if cyto not in (None, CYTO[p]):
                return None, None, False
            cyto = CYTO[p]
        else:
            return None, None, False
    if hpvs:
        if len(hpvs) > 1 and hpvs & {"neg", "invalid"}:
            return None, None, False  # contradictory: negative/invalid together with another HPV result
        # co-reported positive types (e.g. HPV16 + other hrHPV, or "HPV positive" + "HPV other") are managed by
        # the most significant type: 16/18 > other (ASCCP 2019). CONFIRM.
        hpv = ("16" if hpvs & {"16", "18"} else "other" if "other" in hpvs else next(iter(hpvs)))
    return hpv, cyto, bool(parts)


def _cervical(r: ResultInput) -> Decision:
    if r.value is not None and not r.value_text:
        return _rev("malformed_value", "CX-ERR")
    hpv, cyto, ok = _parse_cervical(r)
    if not ok:
        return _rev("malformed_value" if r.value_text else "missing_value", "CX-ERR")
    colpo2w = "Colposcopy offered within 2 weeks (NHS high-grade standard)"
    colpo6w = "Colposcopy offered within 6 weeks (NHS low-grade standard)"
    if cyto == "SCC":
        d = _act("urgent", "colposcopy_urgent", "Urgent colposcopy / gynae-oncology assessment", "CX-SUSP-INVASIVE",
                 target=colpo2w)
        return d
    if cyto in HIGH_GRADE:
        if hpv == "neg":
            return _rev("hpv_negative_abnormal_cytology", "CX-HPVNEG-HG", tier="urgent")
        return _act("urgent", "colposcopy_urgent", "Colposcopy (expedited treatment may be considered for HPV16+ HSIL)",
                    "CX-HIGH-GRADE", target=colpo2w)
    if hpv in ("16", "18"):
        return _act("high", "colposcopy", "Colposcopy with biopsy (HPV 16/18 positive)", "CX-HPV1618", target=colpo6w)
    if hpv in ("other", "other_unknown"):
        if cyto is None:
            return _act("routine", "reflex_cytology", "Reflex cytology on the same sample, then risk-based plan",
                        "CX-HPVOTHER-REFLEX", confirm=True, target="Cytology result within 2 weeks (CONFIRM)")
        if cyto in ("ASC-US", "LSIL"):
            return _act("high", "colposcopy", "Colposcopy (HPV positive with low-grade cytology)", "CX-HPVOTHER-LG",
                        target=colpo6w)
        if cyto == "NILM":
            return _act("routine", "surveillance_1y", "Repeat HPV test in 12 months (surveillance plan required)",
                        "CX-HPVOTHER-NILM", target="Surveillance plan documented; recall at 12 months")
        if cyto == "UNSAT":
            return _act("routine", "repeat_sample", "Repeat cervical sample", "CX-UNSAT",
                        target="Repeat as soon as convenient, no later than 4 months (ASCCP 2023)")
    if hpv == "invalid" and cyto in ("ASC-US", "LSIL"):
        return _rev("hpv_invalid_low_grade", "CX-HPVINV-LG", tier="high",
                    step="Clinician review: low-grade cytology with an invalid HPV test (repeat HPV vs colposcopy)")
    if hpv == "invalid" or (hpv is None and cyto == "UNSAT"):
        return _act("routine", "repeat_sample", "Repeat cervical sample", "CX-UNSAT",
                    target="Repeat as soon as convenient, no later than 4 months (ASCCP 2023)")
    if hpv == "neg":
        if cyto in (None, "NILM"):
            return _neg("CX-NEG", "Return to 5-yearly HPV screening (DoH)")
        if cyto in ("ASC-US", "LSIL", "UNSAT"):
            return _rev("hpv_negative_abnormal_cytology", "CX-HPVNEG-LG", tier="routine")
    if hpv is None and cyto in ("ASC-US", "LSIL"):
        return _act("routine", "reflex_hpv", "HPV test on the same sample, then risk-based plan", "CX-CYTO-LG-NOHPV",
                    confirm=True, target="HPV result within 2 weeks (CONFIRM)")
    if hpv is None and cyto == "NILM":
        return _rev("unknown_test", "CX-CYTO-ONLY")  # cytology-only screening is not the DoH primary test
    return _rev("malformed_value", "CX-ERR")


# ----------------------------------------------------------------- breast (BI-RADS)
# A separator (":" / "-") is only allowed after a prefix, so "-1" is NOT read as BI-RADS 1 (found by differential test).
BIRADS_RE = re.compile(r"^\s*(?:(?:bi-?rads|birads|category|cat)\s*[:\-]?\s*)?([0-6])\s*([abc])?\s*$", re.I)


def _breast(r: ResultInput) -> Decision:
    if r.value_text is None:
        if r.value is None:
            return _rev("missing_value", "BR-ERR")
        n = _num(r.value)  # numeric category must be an integer 0-6 (e.g. -1 or 4.5 is malformed, never BI-RADS 1/4)
        if n is None or n != int(n) or not 0 <= n <= 6:
            return _rev("malformed_value", "BR-ERR")
        text = str(int(n))
    else:
        text = r.value_text
    if not str(text).strip():
        return _rev("missing_value", "BR-ERR")
    t = str(text).strip()
    if re.fullmatch(r"[0-6]\.0", t):
        t = t[0]
    m = BIRADS_RE.match(t)
    if not m:
        return _rev("malformed_value", "BR-ERR")
    cat, sub = int(m.group(1)), (m.group(2) or "").upper()
    if sub and cat != 4:
        return _rev("malformed_value", "BR-ERR")
    if cat in (1, 2):
        return _neg(f"BR-{cat}", "Routine screening mammography every 2 years (DoH)")
    if cat == 0:
        return _act("high", "assessment_imaging", "Recall for additional imaging at a breast assessment unit", "BR-0",
                    confirm=True, target="Assessment offered within 5 working days (DoH)")
    if cat in (4, 5):
        return _act("urgent", "biopsy", "Triple assessment with image-guided core biopsy", f"BR-{cat}{sub}",
                    target="Assessment within 5 working days; needle biopsy result within 5 working days (DoH)")
    if cat == 3:
        return _rev("screening_category_unexpected", "BR-3", tier="high",
                    step="Radiologist/clinician review: BI-RADS 3 is a diagnostic category (short-interval follow-up)")
    return _rev("known_malignancy", "BR-6", tier="urgent",
                step="Known biopsy-proven malignancy: confirm the patient is under the breast cancer team")


# ----------------------------------------------------------------- diabetes (HbA1c / FPG)
def _infer_diabetes_test(r: ResultInput) -> str | None:
    if r.test:
        return r.test.upper()
    u = norm_unit(r.unit)
    if u in ("%", "mmol/mol"):
        return "HBA1C"
    if u in ("mg/dL", "mmol/L"):
        return "FPG"
    return "HBA1C"  # programme default test; unit checks below still apply


def _hba1c_tier(v: float, unit: str) -> tuple[str | None, str]:
    th = HBA1C_PCT if unit == "%" else HBA1C_MMOL
    if v >= th["critical"]:
        return "critical", "DM-A1C-CRIT"
    if v >= th["high"]:
        return "high", "DM-A1C-HIGH"
    if v >= th["diabetes"]:
        return "routine", "DM-A1C-DIAB"
    if v >= th["prediabetes"]:
        return "routine", "DM-A1C-PRE"
    return None, "DM-A1C-NEG"


def _fpg_tier(v: float, unit: str) -> tuple[str | None, str]:
    th = FPG_MGDL if unit == "mg/dL" else FPG_MMOL
    if v >= th["critical"]:
        return "critical", "DM-FPG-CRIT"
    if v >= th["high"]:
        return "high", "DM-FPG-HIGH"
    if v >= th["diabetes"]:
        return "routine", "DM-FPG-DIAB"
    if v >= th["prediabetes"]:
        return "routine", "DM-FPG-PRE"
    if v < th["hypo"]:
        return "hypo", "DM-FPG-HYPO"
    return None, "DM-FPG-NEG"


DIAB_PATHWAYS = {
    "DM-A1C-CRIT": ("same_day_review", "Same-day clinician contact (very high HbA1c); assess for hyperglycaemic crisis", False,
                    "Clinician contact the same day (local rule - CONFIRM)"),
    "DM-FPG-CRIT": ("same_day_review", "Same-day clinician contact (very high glucose); assess for hyperglycaemic crisis", False,
                    "Clinician contact the same day (local rule - CONFIRM)"),
    "DM-A1C-HIGH": ("confirm_then_gp", "Confirm diagnosis and GP review soon (consider insulin per ADA if symptomatic)", True,
                    "GP review within 2 weeks (CONFIRM)"),
    "DM-FPG-HIGH": ("confirm_then_gp", "Confirm diagnosis and GP review soon (consider insulin per ADA if symptomatic)", True,
                    "GP review within 2 weeks (CONFIRM)"),
    "DM-A1C-DIAB": ("confirm_then_gp", "Repeat HbA1c or fasting glucose to confirm, then GP visit and care plan", True,
                    "Confirmatory test within 4 weeks (CONFIRM)"),
    "DM-FPG-DIAB": ("confirm_then_gp", "Repeat fasting glucose or HbA1c to confirm, then GP visit and care plan", True,
                    "Confirmatory test within 4 weeks (CONFIRM)"),
    "DM-A1C-PRE": ("prediabetes_gp", "GP review: prediabetes advice / prevention programme; retest within 12 months", False,
                   "GP review within 3 months; retest at least yearly (ADA)"),
    "DM-FPG-PRE": ("prediabetes_gp", "GP review: prediabetes advice / prevention programme; retest within 12 months", False,
                   "GP review within 3 months; retest at least yearly (ADA)"),
}


def _diabetes(r: ResultInput, ctx: PatientContext) -> Decision:
    test = _infer_diabetes_test(r)
    if test not in ("HBA1C", "FPG"):
        return _rev("unknown_test", "DM-ERR")
    if r.value is None:
        return _rev("missing_value", "DM-ERR")
    v = _num(r.value)
    if v is None:
        return _rev("malformed_value", "DM-ERR")
    u = norm_unit(r.unit)
    units = ("%", "mmol/mol") if test == "HBA1C" else ("mg/dL", "mmol/L")
    rng = {"%": HBA1C_RANGE_PCT, "mmol/mol": HBA1C_RANGE_MMOL, "mg/dL": FPG_RANGE_MGDL, "mmol/L": FPG_RANGE_MMOL}
    tier_fn = _hba1c_tier if test == "HBA1C" else _fpg_tier
    if u is None or u not in units:
        # evaluate under every plausible unit and keep the MOST urgent interpretation
        tiers = []
        for cand in units:
            lo, hi = rng[cand]
            if lo <= v <= hi:
                t, _ = tier_fn(v, cand)
                tiers.append("urgent" if t == "hypo" else t)
        return _rev("missing_unit" if u is None else "unknown_unit", "DM-ERR", tier=max_tier(*tiers))
    lo, hi = rng[u]
    if not lo <= v <= hi:
        return _rev("implausible_value", "DM-ERR")
    norm = {}
    if test == "HBA1C":
        pct = v if u == "%" else hba1c_mmol_to_pct(v)
        norm = {"hba1c_pct": round(pct, 2), "hba1c_mmol_mol": round(v if u == "mmol/mol" else hba1c_pct_to_mmol(v), 1)}
        low = HBA1C_LOW_PCT if u == "%" else HBA1C_LOW_MMOL
        if v < low:
            d = _rev("unexpectedly_low", "DM-A1C-LOW", norm=norm,
                     step="Clinician review: unexpectedly low HbA1c (haemoglobin variant / haemolysis?)")
            if ctx.haemoglobinopathy:
                d.reasons.append("haemoglobinopathy")
                d.tier = max_tier(d.tier, REVIEW_MIN_TIER["haemoglobinopathy"])
            return d
        if ctx.haemoglobinopathy:
            # HbA1c is unreliable with a haemoglobin variant: EVERY HbA1c result (normal ones too - a falsely
            # normal value is the dangerous case) is reviewed; use glucose criteria (found by differential test).
            t, rid = tier_fn(v, u)
            return _rev("haemoglobinopathy", rid, tier=t, norm=norm,
                        step="Clinician review: HbA1c may be unreliable with a haemoglobin variant - use glucose criteria")
    t, rid = tier_fn(v, u)
    if t == "hypo":
        return _rev("hypoglycaemia", "DM-FPG-HYPO", tier="urgent", step="Clinician review: fasting glucose in hypoglycaemic range")
    if t is None:
        return _neg(rid, "Rescreen at the routine interval (ADA: every 3 years if normal)")
    pw, step, confirm, target = DIAB_PATHWAYS[rid]
    return _act(t, pw, step, rid, confirm=confirm, target=target, norm=norm)


# ----------------------------------------------------------------- entry point
PROGRAMME_RULES = {"colorectal_fit": _fit, "cervical": _cervical, "breast": _breast}


def evaluate(r: ResultInput, ctx: PatientContext | None = None) -> Decision:
    """Classify one screening result. Never raises for bad input: bad input becomes a review decision."""
    ctx = ctx or PatientContext()
    if r.programme == "diabetes_hba1c":
        d = _diabetes(r, ctx)
    elif r.programme in PROGRAMME_RULES:
        if r.test and r.test.upper() not in {"colorectal_fit": {"FIT"}, "cervical": {"HPV", "CYTOLOGY", "HPV+CYTOLOGY"},
                                              "breast": {"MAMMO", "MAMMOGRAM"}}[r.programme]:
            d = _rev("unknown_test", "GEN-ERR")
        else:
            d = PROGRAMME_RULES[r.programme](r)
    else:
        return _rev("unknown_programme", "GEN-ERR")

    reasons: list[str] = []
    status = (r.status or "final").lower()
    if status in ("amended", "corrected"):
        reasons.append(f"{status}_result")
    elif status in ("preliminary", "partial", "registered"):
        reasons.append("preliminary_result")
    elif status in ("entered-in-error", "cancelled", "retracted"):
        reasons.append("result_retracted")
    elif status != "final":
        reasons.append("malformed_value")
    if r.programme in ELIGIBLE_SEX and ctx.sex and ctx.sex not in ELIGIBLE_SEX[r.programme]:
        reasons.append("sex_mismatch")
    lo, hi = ELIGIBLE_AGE.get(r.programme, (0, 200))
    if ctx.age is None:
        reasons.append("age_unknown")
    elif ctx.age < lo:
        reasons.append("age_below_programme")
    elif ctx.age > hi:
        reasons.append("age_above_programme")
    if ctx.pregnant:
        reasons.append("pregnant")
    if ctx.deceased:
        reasons.append("deceased")
    if ctx.under_specialist_care:
        reasons.append("under_specialist_care")
    if not reasons:
        return d
    # context makes the result a human-review item; tier never lower than the clinical tier
    floor = max_tier(*(REVIEW_MIN_TIER[x] for x in reasons))
    return Decision("review", max_tier(d.tier, floor), "human_review" if d.disposition == "negative" else d.pathway,
                    d.next_step if d.disposition != "negative" else "Clinician review of the result and patient context",
                    d.needs_confirmation, d.target, d.rule_id, d.reasons + reasons, d.clinical_tier, d.normalised)


# End-to-end pathway targets in CALENDAR days from the result (docs/clinical_rules.md). Measured from the
# result rather than from the referral, i.e. stricter than the source where the source counts from referral.
PATHWAY_TARGET_DAYS = {
    "colonoscopy": 31,          # DoH CRC spec: colonoscopy within 31 days of referral
    "assessment_imaging": 7,    # DoH breast 2025: assessment within 5 working days
    "biopsy": 7,                # DoH breast 2025: assessment within 5 working days
    "colposcopy_urgent": 14,    # NHS: high-grade within 2 weeks
    "colposcopy": 42,           # NHS: low-grade within 6 weeks
    "reflex_cytology": 14, "reflex_hpv": 14,  # CONFIRM
    "surveillance_1y": 28,      # CONFIRM: plan documented
    "repeat_sample": 120,       # ASCCP 2023: as soon as convenient, no later than 4 months
    "repeat_fit": 28,           # CONFIRM
    "same_day_review": 1,       # CONFIRM (local rule)
    "confirm_then_gp": 28,      # CONFIRM
    "prediabetes_gp": 90,       # CONFIRM
}
RULE_TARGET_DAYS = {"CRC-FIT-URGENT": 14, "DM-A1C-HIGH": 14, "DM-FPG-HIGH": 14}  # CONFIRM (local rules)
TIER_TARGET_DAYS = {"critical": 1, "urgent": 14, "high": 31, "routine": 90}     # CONFIRM (fallback)


def target_days(pathway: str | None, rule_id: str | None, tier: str, clinical_tier: str | None) -> int:
    """Pathway target; the tier default applies when there is no pathway target or the case was escalated
    above its clinical tier (review floors, more urgent results). Always the MORE urgent of the candidates."""
    days = RULE_TARGET_DAYS.get(rule_id or "") or PATHWAY_TARGET_DAYS.get(pathway or "")
    if days is None or clinical_tier is None or TIER_RANK[tier] < TIER_RANK[clinical_tier]:
        days = min(days or 10**6, TIER_TARGET_DAYS[tier])
    return days


def review_min_tier(reasons: list[str]) -> str | None:
    return max_tier(*(REVIEW_MIN_TIER.get(x, "urgent") for x in reasons))
