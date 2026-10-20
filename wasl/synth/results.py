"""Synthetic screening RESULTS, calibrated where a published source exists (see docs/synthetic_data.md).

Two modes:
* realistic  - every screened person gets a result drawn from the full distribution (most are negative),
               plus small rates of real-world data problems (missing units, amended results, duplicates ...)
               so the fail-safe paths are exercised at scale (`make seed-large`, 20,000 people).
* enriched   - the demo default: results are drawn from the SAME distributions conditioned on being
               actionable (rejection sampling), so a 2,000-person demo has a busy work queue.
All values are SIMULATED.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace

from ..clinical_rules import PatientContext, ResultInput, evaluate


@dataclass(frozen=True)
class SynthResult:
    value: float | None
    value_text: str | None
    unit: str | None
    test: str | None = None
    status: str = "final"
    cytology: str | None = None
    defect: str | None = None  # which data-quality defect was injected (None = clean)


# ---- calibration targets (see docs/synthetic_data.md for sources; A = assumption) -------------------------
FIT_POS_80 = 0.045         # A: between England 1.7 % @120 (2024-25) and Abu Dhabi 13.5 % FIT/gFOBT (2015-16)
FIT_URGENT_GIVEN_POS = 0.22  # A
HRHPV = 0.147              # UAE study: 14.7 % high-risk HPV (QMJ 2024 scoping review)
HPV1618_GIVEN_POS = 0.28   # A
CYTO_OTHER = [("NILM", .66), ("ASC-US", .17), ("LSIL", .10), ("ASC-H", .03), ("HSIL", .035), ("AGC", .005)]  # A
CYTO_1618 = [("NILM", .50), ("ASC-US", .15), ("LSIL", .12), ("ASC-H", .07), ("HSIL", .14), ("AGC", .01), ("SCC", .01)]  # A
REFLEX_ON_REPORT = 0.80    # A: share of HPV-other reports that already carry reflex cytology
BIRADS = [("1", .600), ("2", .292), ("0", .100), ("3", .002), ("4", .005), ("5", .001)]  # recall 10 %: DoH <10-15 %, BCSC 11.6 %
A1C_BANDS = [  # (lower %, upper %, probability)  - IDF 2021 UAE: diabetes 12.3 % (20-79); IGT 18.3 % used as prediabetes proxy
    (4.2, 5.69, 0.577), (5.7, 6.49, 0.180), (6.5, 9.99, 0.094), (10.0, 14.99, 0.027), (15.0, 17.5, 0.002)]
A1C_MMOL_SHARE = 0.20      # A: labs reporting IFCC mmol/mol
FPG_SHARE = 0.15           # A: diabetes screens done with fasting glucose instead of HbA1c
FPG_MMOL_SHARE = 0.30      # A
DEFECTS = [  # A: per-result data-quality defect rates in realistic mode (x5 in enriched demo mode)
    ("missing_unit", .003), ("malformed", .001), ("implausible", .0005), ("amended", .003), ("preliminary", .002)]


def _pick(rng: random.Random, pairs):
    r, acc = rng.random(), 0.0
    for k, w in pairs:
        acc += w
        if r <= acc:
            return k
    return pairs[-1][0]


def _fit(rng: random.Random) -> SynthResult:
    u = rng.random()
    if u < 0.01:
        return SynthResult(None, "invalid", None, "FIT")
    if u < 0.01 + FIT_POS_80:
        if rng.random() < FIT_URGENT_GIVEN_POS:
            v = 400 * math.exp(rng.expovariate(1.6))  # heavy tail above 400
        else:
            v = rng.uniform(80, 399)
        return SynthResult(round(min(v, 3000), 0), None, rng.choice(["ug/g", "µg/g", "ug Hb/g"]), "FIT")
    v = rng.choice([0.0, 0.0, 0.0, rng.uniform(0, 10), rng.uniform(0, 30), rng.uniform(10, 79.9)])
    return SynthResult(round(v, 1), None, "ug/g", "FIT")


def _cervical(rng: random.Random) -> SynthResult:
    u = rng.random()
    if u < 0.01:
        return SynthResult(None, "HPV invalid", None, "HPV")
    if u < 0.01 + HRHPV:
        if rng.random() < HPV1618_GIVEN_POS:
            g = rng.choice(["HPV16", "HPV16", "HPV18"])
            cyto = _pick(rng, CYTO_1618)
            return SynthResult(None, g, None, "HPV", cytology=cyto)
        cyto = _pick(rng, CYTO_OTHER) if rng.random() < REFLEX_ON_REPORT else None
        return SynthResult(None, "HPV other hr", None, "HPV", cytology=cyto)
    return SynthResult(None, "HPV negative", None, "HPV")


def _breast(rng: random.Random) -> SynthResult:
    cat = _pick(rng, BIRADS)
    if cat == "4":
        cat += rng.choice(["A", "A", "B", "C"])
    return SynthResult(None, f"BI-RADS {cat}", None, "MAMMO")


def _diabetes(rng: random.Random) -> SynthResult:
    lo, hi = _pick(rng, [((a, b), p) for a, b, p in A1C_BANDS])
    pct = rng.uniform(lo, hi)
    if rng.random() < FPG_SHARE:
        # map the HbA1c band onto the equivalent fasting-glucose band (ADA pairs) - same severity mix
        mg = _a1c_band_to_fpg(rng, pct)
        if rng.random() < FPG_MMOL_SHARE:
            return SynthResult(round(mg / 18.016, 1), None, "mmol/L", "FPG")
        return SynthResult(round(mg), None, "mg/dL", "FPG")
    if rng.random() < A1C_MMOL_SHARE:
        return SynthResult(round((pct - 2.152) / 0.09148), None, "mmol/mol", "HBA1C")
    return SynthResult(round(pct, 1), None, "%", "HBA1C")


def _a1c_band_to_fpg(rng: random.Random, pct: float) -> float:
    if pct >= 15:
        return rng.uniform(500, 650)
    if pct >= 10:
        return rng.uniform(300, 499)
    if pct >= 6.5:
        return rng.uniform(126, 299)
    if pct >= 5.7:
        return rng.uniform(100, 125)
    return rng.uniform(70, 99)


DRAW = {"colorectal_fit": _fit, "cervical": _cervical, "breast": _breast, "diabetes_hba1c": _diabetes}


def _inject_defect(rng: random.Random, r: SynthResult, scale: float) -> SynthResult:
    for name, p in DEFECTS:
        if rng.random() < p * scale:
            if name == "missing_unit" and r.value is not None:
                return replace(r, unit=None, defect=name)
            if name == "malformed":
                return replace(r, value=None, value_text="see comment", unit=None, cytology=None, defect=name)
            if name == "implausible" and r.value is not None:
                return replace(r, value=r.value * 100, defect=name)
            if name == "amended":
                return replace(r, status="amended", defect=name)
            if name == "preliminary":
                return replace(r, status="preliminary", defect=name)
    return r


def draw_result(rng: random.Random, programme: str, ctx: PatientContext, *, enriched: bool) -> SynthResult:
    """Draw one screening result. In enriched mode, condition on the result creating a case."""
    for _ in range(500):
        r = _inject_defect(rng, DRAW[programme](rng), 5.0 if enriched else 1.0)
        if not enriched:
            return r
        d = evaluate(ResultInput(programme, r.value, r.value_text, r.unit, r.test, r.status, r.cytology), ctx)
        if d.creates_case:
            return r
    return r
