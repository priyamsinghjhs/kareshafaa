"""INDEPENDENT reference implementation of the clinical rules, written from docs/clinical_rules.md
(sections 3-7), NOT from kareshafaa/clinical_rules.py. It deliberately imports nothing from `kareshafaa` and uses a
different structure (interval tables and token sets) so that a shared coding error is unlikely.
Only the input vocabulary (accepted unit spellings, HPV/cytology words, BI-RADS formats, statuses) is a
shared interface specification. Used by scripts/differential_test.py and tests/test_differential.py.

Output: Ref(kind, tier, pathway, reasons) with kind in {"negative", "actionable", "review"}.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

ORDER = ("critical", "urgent", "high", "routine")  # most urgent first


def worst(*tiers):
    ts = [t for t in tiers if t]
    return min(ts, key=ORDER.index) if ts else None


@dataclass
class Ref:
    kind: str
    tier: str | None = None
    pathway: str | None = None
    reasons: list = field(default_factory=list)


def NEG():
    return Ref("negative")


def ACT(tier, pathway):
    return Ref("actionable", tier, pathway)


def REV(reason, floor, clinical=None, pathway=None):
    return Ref("review", worst(floor, clinical), pathway, [reason])


# doc section 7 (reason -> minimum tier)
FLOOR = dict(amended_result="urgent", corrected_result="urgent", preliminary_result="high", result_retracted="urgent",
             malformed_status="urgent", sex_mismatch="urgent", age_below_programme="routine",
             age_above_programme="routine", age_unknown="high", pregnant="high", deceased="high",
             under_specialist_care="high")
STATUS = {"final": None, "amended": "amended_result", "corrected": "corrected_result",
          "preliminary": "preliminary_result", "partial": "preliminary_result", "registered": "preliminary_result",
          "entered-in-error": "result_retracted", "cancelled": "result_retracted", "retracted": "result_retracted"}
AGES = {"colorectal_fit": (40, 75), "cervical": (25, 65), "breast": (40, None), "diabetes_hba1c": (18, None)}
FEMALE_ONLY = ("cervical", "breast")
TESTS = {"colorectal_fit": ("FIT",), "cervical": ("HPV", "CYTOLOGY", "HPV+CYTOLOGY"), "breast": ("MAMMO", "MAMMOGRAM"),
         "diabetes_hba1c": ("HBA1C", "FPG")}


def as_number(x):
    """A finite real number, or None. Booleans are not numbers. Numeric strings are numbers."""
    if x is None or x is True or x is False:
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def unit_of(u):
    """Canonical unit, None if missing, 'OTHER:<u>' if not recognised."""
    if u is None:
        return None
    s = str(u).replace("\u00a0", " ").strip().lower()
    if s == "":
        return None
    squashed = s.replace(" ", "")
    table = {"ug/g": "ug/g", "µg/g": "ug/g", "mcg/g": "ug/g", "ughb/g": "ug/g", "µghb/g": "ug/g", "ug/gfaeces": "ug/g",
             "%": "%", "%hb": "%", "%(ngsp)": "%", "ngsp%": "%", "dcct%": "%",
             "mmol/mol": "mmol/mol", "mmol/molhb": "mmol/mol", "mg/dl": "mg/dL", "mmol/l": "mmol/L", "ng/ml": "ng/mL"}
    return table.get(squashed, "OTHER:" + s)


# ------------------------------------------------------------------ section 3: FIT
FIT_BANDS = [(80.0, None), (400.0, "high"), (math.inf, "urgent")]  # upper bound (exclusive) -> tier


def fit_tier(v):
    for upper, tier in FIT_BANDS:
        if v < upper:
            return tier
    return "urgent"


def fit(value, text, unit):
    if value is not None and as_number(value) is None:
        return REV("malformed_value", "urgent")
    v = as_number(value)
    if v is None:
        w = (text or "").strip().lower()
        if w in {"positive", "pos", "detected", "abnormal"}:
            return ACT("high", "colonoscopy")
        if w in {"negative", "neg", "not detected", "normal"}:
            return NEG()
        if w in {"invalid", "spoilt", "unsatisfactory", "insufficient", "rejected sample", "inadequate"}:
            return ACT("routine", "repeat_fit")
        return REV("missing_value" if w == "" else "malformed_value", "urgent")
    u = unit_of(unit)
    if u != "ug/g":
        return REV("unit_problem", "urgent")
    if v < 0 or v > 10000:
        return REV("implausible_value", "urgent")
    t = fit_tier(v)
    return NEG() if t is None else ACT(t, "colonoscopy")


# ------------------------------------------------------------------ section 4: cervical
HPV_WORDS = {"16": {"hpv16", "hpv 16", "hpv16/18", "hpv 16/18", "hpv16 and 18"}, "18": {"hpv18", "hpv 18"},
             "other": {"hpv other hr", "hpv other", "hrhpv other", "other hr hpv", "hpv other high risk",
                       "hpv positive", "hrhpv positive"},
             "neg": {"hpv negative", "hrhpv negative", "hpv not detected"},
             "invalid": {"hpv invalid", "hpv unsatisfactory", "hpv inadequate"}}
CYTO_WORDS = {"NILM": {"nilm"}, "LG": {"asc-us", "ascus", "lsil"}, "HG": {"asc-h", "hsil", "agc", "ais"},
              "INV": {"scc", "squamous cell carcinoma", "adenocarcinoma"}, "UNSAT": {"unsatisfactory", "inadequate"}}
CYTO_ID = {w: (k, w) for k, ws in CYTO_WORDS.items() for w in ws}
CYTO_CANON = {"ascus": "asc-us", "squamous cell carcinoma": "scc", "adenocarcinoma": "scc", "inadequate": "unsatisfactory"}
HPV_ID = {w: k for k, ws in HPV_WORDS.items() for w in ws}


def cervical(value, text, cytology_field):
    if value is not None and not text:
        return REV("malformed_value", "urgent")
    tokens = [t.strip().lower() for t in re.split(r"[|;,]", text or "")]
    tokens = [t for t in tokens if t]
    if cytology_field and cytology_field.strip():
        tokens.append(cytology_field.strip().lower())
    if not tokens:
        return REV("missing_value", "urgent")
    hpvs, cytos = set(), set()
    for t in tokens:
        if t in HPV_ID:
            hpvs.add(HPV_ID[t])
        elif t in CYTO_ID:
            cytos.add(CYTO_CANON.get(t, t))
        else:
            return REV("malformed_value", "urgent")
    # doc 4: negative/invalid together with any other HPV result, or two different cytology results, are
    # contradictory; co-reported positive types are managed by the most significant (16/18 > other).
    if (len(hpvs) > 1 and hpvs & {"neg", "invalid"}) or len(cytos) > 1:
        return REV("contradictory", "urgent")
    hpv = None if not hpvs else ("16/18" if hpvs & {"16", "18"} else next(iter(hpvs)))
    cyto = None if not cytos else next(k for k, ws in CYTO_WORDS.items() if next(iter(cytos)) in ws or
                                       CYTO_CANON.get(next(iter(cytos))) in ws)
    if cyto == "INV":
        return ACT("urgent", "colposcopy_urgent")
    if cyto == "HG":
        return REV("hpv_negative_abnormal_cytology", "urgent") if hpv == "neg" else ACT("urgent", "colposcopy_urgent")
    if hpv == "16/18":
        return ACT("high", "colposcopy")
    if hpv == "other":
        return {None: ACT("routine", "reflex_cytology"), "LG": ACT("high", "colposcopy"),
                "NILM": ACT("routine", "surveillance_1y"), "UNSAT": ACT("routine", "repeat_sample")}[cyto]
    if hpv == "invalid":
        return REV("hpv_invalid_low_grade", "high") if cyto == "LG" else ACT("routine", "repeat_sample")
    if hpv == "neg":
        return NEG() if cyto in (None, "NILM") else REV("hpv_negative_abnormal_cytology", "high")
    # no HPV result
    return {"LG": ACT("routine", "reflex_hpv"), "NILM": REV("cytology_only", "urgent"),
            "UNSAT": ACT("routine", "repeat_sample")}[cyto]


# ------------------------------------------------------------------ section 5: breast
BIRADS_FORMS = [re.compile(p, re.I) for p in (
    r"([0-6])([abc]?)", r"bi-?rads\s*[:\-]?\s*([0-6])\s*([abc]?)", r"birads\s*[:\-]?\s*([0-6])\s*([abc]?)",
    r"category\s*[:\-]?\s*([0-6])\s*([abc]?)", r"cat\s*[:\-]?\s*([0-6])\s*([abc]?)", r"([0-6])\s+([abc])")]


def breast(value, text):
    if text is None:
        if value is None:
            return REV("missing_value", "urgent")
        n = as_number(value)
        if n is None or n != int(n) or not 0 <= n <= 6:
            return REV("malformed_value", "urgent")
        s = str(int(n))
    else:
        s = str(text).strip()
        if s == "":
            return REV("missing_value", "urgent")
        if re.fullmatch(r"[0-6]\.0", s):
            s = s[0]
    cat = sub = None
    for f in BIRADS_FORMS:
        m = f.fullmatch(s)
        if m:
            cat, sub = int(m.group(1)), m.group(2).upper()
            break
    if cat is None:
        return REV("malformed_value", "urgent")
    if sub and cat != 4:
        return REV("malformed_value", "urgent")
    if cat in (1, 2):
        return NEG()
    if cat == 0:
        return ACT("high", "assessment_imaging")
    if cat in (4, 5):
        return ACT("urgent", "biopsy")
    if cat == 3:
        return REV("screening_category_unexpected", "high")
    return REV("known_malignancy", "urgent")


# ------------------------------------------------------------------ section 6: diabetes
# (lower bound inclusive, category) in each unit, ascending; doc table 6.1
A1C = {"%": [(15.0, "critical"), (10.0, "high"), (6.5, "diab"), (5.7, "pre")],
       "mmol/mol": [(139.9, "critical"), (85.7, "high"), (47.5, "diab"), (38.7, "pre")]}
A1C_LOW = {"%": 4.0, "mmol/mol": 20.0}
A1C_RANGE = {"%": (3.0, 20.0), "mmol/mol": (9.0, 195.0)}
GLU = {"mg/dL": [(500, "critical"), (300, "high"), (126, "diab"), (100, "pre")],
       "mmol/L": [(27.7, "critical"), (16.7, "high"), (7.0, "diab"), (5.6, "pre")]}
GLU_HYPO = {"mg/dL": 54, "mmol/L": 3.0}
GLU_RANGE = {"mg/dL": (10.0, 1500.0), "mmol/L": (0.6, 83.0)}
CAT = {"critical": ("critical", "same_day_review"), "high": ("high", "confirm_then_gp"),
       "diab": ("routine", "confirm_then_gp"), "pre": ("routine", "prediabetes_gp")}


def diab_category(test, v, u):
    """Clinical category for an in-range value: one of CAT keys, 'neg', 'low' (HbA1c) or 'hypo' (FPG)."""
    if test == "HBA1C":
        if v < A1C_LOW[u]:
            return "low"
        bands = A1C[u]
    else:
        bands = GLU[u]
    for lo, cat in bands:
        if v >= lo:
            return cat
    if test == "FPG" and v < GLU_HYPO[u]:
        return "hypo"
    return "neg"


def diabetes(value, unit, test, haemoglobinopathy):
    if test:
        t = test.upper()
        if t not in ("HBA1C", "FPG"):
            return REV("unknown_test", "urgent")
    else:
        u0 = unit_of(unit)
        t = "FPG" if u0 in ("mg/dL", "mmol/L") else "HBA1C"
    if value is None:
        return REV("missing_value", "urgent")
    v = as_number(value)
    if v is None:
        return REV("malformed_value", "urgent")
    units = ("%", "mmol/mol") if t == "HBA1C" else ("mg/dL", "mmol/L")
    ranges = A1C_RANGE if t == "HBA1C" else GLU_RANGE
    u = unit_of(unit)
    if u not in units:
        interp = []
        for cand in units:
            lo, hi = ranges[cand]
            if lo <= v <= hi:
                c = diab_category(t, v, cand)
                interp.append({"hypo": "urgent", "low": "routine", "neg": None}.get(c, CAT.get(c, (None,))[0]))
        return REV("unit_problem", "urgent", worst(*interp))
    lo, hi = ranges[u]
    if not lo <= v <= hi:
        return REV("implausible_value", "urgent")
    c = diab_category(t, v, u)
    if c == "low":
        r = REV("unexpectedly_low", "routine")
    elif c == "hypo":
        r = REV("hypoglycaemia", "urgent")
    elif c == "neg":
        r = NEG()
    else:
        r = ACT(*CAT[c])
    # doc 6 / 7: HbA1c is unreliable with a haemoglobin variant -> review (high), whatever the value
    if t == "HBA1C" and haemoglobinopathy:
        return REV("haemoglobinopathy", "high", r.tier, r.pathway)
    return r


# ------------------------------------------------------------------ entry point + section 7 overlay
def reference(programme, value=None, value_text=None, unit=None, test=None, status="final", cytology=None,
              age=None, sex=None, pregnant=False, deceased=False, under_specialist_care=False,
              haemoglobinopathy=False) -> Ref:
    if programme not in TESTS:
        return REV("unknown_programme", "urgent")
    if programme != "diabetes_hba1c" and test and test.upper() not in TESTS[programme]:
        r = REV("unknown_test", "urgent")
    elif programme == "colorectal_fit":
        r = fit(value, value_text, unit)
    elif programme == "cervical":
        r = cervical(value, value_text, cytology)
    elif programme == "breast":
        r = breast(value, value_text)
    else:
        r = diabetes(value, unit, test, haemoglobinopathy)
    extra = []
    st = (status or "final").lower()
    extra.append(STATUS[st] if st in STATUS else "malformed_status")
    if programme in FEMALE_ONLY and sex and sex != "female":
        extra.append("sex_mismatch")
    lo, hi = AGES[programme]
    if age is None:
        extra.append("age_unknown")
    elif age < lo:
        extra.append("age_below_programme")
    elif hi is not None and age > hi:
        extra.append("age_above_programme")
    for flag, name in ((pregnant, "pregnant"), (deceased, "deceased"), (under_specialist_care, "under_specialist_care")):
        if flag:
            extra.append(name)
    extra = [e for e in extra if e]
    if not extra:
        return r
    return Ref("review", worst(r.tier, *(FLOOR[e] for e in extra)), r.pathway, r.reasons + extra)
