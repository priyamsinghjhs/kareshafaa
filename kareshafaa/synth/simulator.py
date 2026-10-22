"""Before/after simulator: replays the same synthetic cohort under a 'baseline' (no loop-closure
system) and a 'with KareShafaa' scenario and computes the challenge KPIs.

ALL OUTPUTS ARE SIMULATED from stated ASSUMPTIONS (parameters below). They illustrate the
mechanism by which the system is expected to help; they are not evidence of real-world effect.
"""
from __future__ import annotations

import math
import random
import statistics as st
from collections import Counter, defaultdict

from ..clinical_rules import PatientContext, ResultInput, evaluate
from .cohort import SynthPatient, make_cohort, risk_multiplier
from .history import CONFIRM_POSITIVE
from .results import draw_result

HORIZON_DAYS = 120
TIER_SPEED = {"critical": 0.2, "urgent": 0.4, "high": 0.8, "routine": 1.3}
# Iteration 6: tier, confirmation step and pathway come from the clinical rules engine applied to a synthetic
# actionable result (same distributions as the seed), not from a fixed table.
REFERRAL_PATHWAYS = {"colonoscopy", "colposcopy", "colposcopy_urgent", "biopsy"}
CONFIRM_THEN_REFER = {"assessment_imaging": "biopsy", "reflex_cytology": "colposcopy", "reflex_hpv": "colposcopy",
                      "repeat_fit": "colonoscopy", "repeat_sample": "colposcopy"}
GP_LED = {"prediabetes_gp", "surveillance_1y", "confirm_then_gp", "same_day_review"}
# Colorectal baseline calibration: Abu Dhabi 2015-16 (BMC Cancer 2021) - 30.5 % of positive screens completed
# colonoscopy, median 72 days. Extra baseline losses on the colonoscopy pathway reproduce that order of magnitude.
CRC_BASELINE_EXTRA_DROP = 0.30  # A (calibrated)
CRC_BASELINE_WAIT_FACTOR = 2.6  # A (calibrated)

BASELINE = dict(
    name="baseline (no system)", notify_median=6.0, never_notified=0.08, unsupported_lang_penalty=0.15,
    understood=0.62, understood_lang_penalty=0.20, teach_back=False, confirm_median=18.0, confirm_drop=0.15,
    consult_median=10.0, referral_reject=0.15, reroute_after_reject=0.50, referral_lost=0.06, accept_median=6.0,
    appt_wait_median=24.0, no_show=0.18, rebook=0.40, treat_median=14.0, treat_drop=0.08, closure_documented=0.55,
    decline=0.04, decline_documented=0.30, unreachable_ladder=False, priority_outreach=False, escalation=False,
    confidence_mean=2.9)
WITH_SYSTEM = dict(
    name="with KareShafaa", notify_median=1.0, never_notified=0.01, unsupported_lang_penalty=0.04,
    understood=0.86, understood_lang_penalty=0.06, teach_back=True, confirm_median=10.0, confirm_drop=0.06,
    consult_median=5.0, referral_reject=0.15, reroute_after_reject=0.92, referral_lost=0.005, accept_median=2.5,
    appt_wait_median=14.0, no_show=0.11, rebook=0.85, treat_median=8.0, treat_drop=0.04, closure_documented=0.97,
    decline=0.04, decline_documented=0.97, unreachable_ladder=True, priority_outreach=True, escalation=True,
    confidence_mean=4.1)


def _ln(rng: random.Random, median: float, spread: float = 0.6) -> float:
    return max(0.05, rng.lognormvariate(math.log(median), spread))


def _pick(rng, pairs):
    r, acc = rng.random(), 0.0
    for k, w in pairs:
        acc += w
        if r <= acc:
            return k
    return pairs[-1][0]


def _decision(sp: SynthPatient, rng: random.Random):
    ctx = PatientContext(age=sp.age, sex=sp.gender, pregnant=sp.pregnant, deceased=False,
                         under_specialist_care=False, haemoglobinopathy=sp.haemoglobinopathy)
    r = draw_result(rng, sp.programme, ctx, enriched=True)
    return evaluate(ResultInput(sp.programme, r.value, r.value_text, r.unit, r.test, r.status, r.cytology), ctx)


def journey(sp: SynthPatient, p: dict, rng: random.Random, top_risk: bool, dec=None) -> dict:
    dec = dec or _decision(sp, rng)
    tier = dec.tier or "routine"
    sp_f = TIER_SPEED[tier]
    baseline = not p["escalation"]
    crc = dec.pathway in ("colonoscopy", "repeat_fit")
    rm = risk_multiplier(sp)
    unsupported = sp.language in ("tl", "ml")
    support = 0.7 if (p["priority_outreach"] and top_risk) else 1.0  # navigator outreach for top-risk cases
    r = dict(programme=sp.programme, tier=tier, language=sp.language, region=sp.region, gender=sp.gender,
             age_band=sp.age_band, nationality=sp.nationality, t_notify=None, understood=False, t_ack=None,
             t_confirm=None, t_consult=None, needs_referral=False, referral_done=False, appt_attended=False,
             t_care=None, closed=False, closure="open", reason=None, confidence=None, pathway=dec.pathway,
             review=dec.disposition == "review")
    t = 0.0
    if dec.disposition == "review":  # uncertain result: human review first
        if baseline and rng.random() < 0.20:  # A: no review queue -> ambiguous results often sit unactioned
            return {**r, "reason": "uncertain_result_not_actioned"}
        t += _ln(rng, 1.0 if not baseline else 7.0)

    def done(reason: str) -> dict:
        r["reason"] = reason
        return r

    if rng.random() < p["never_notified"] * rm * support:
        if p["unreachable_ladder"]:
            r.update(closed=True, closure="authorised_unable_to_contact")
        return done("never_notified_or_unreachable")
    t += _ln(rng, p["notify_median"] * sp_f * (1.3 if not sp.has_smartphone else 1.0))
    r["t_notify"] = t
    und = p["understood"] - (p["understood_lang_penalty"] if unsupported else 0) - 0.15 * (1 - sp.responsiveness)
    if p["teach_back"]:
        und = max(und, 0.0)
        if rng.random() >= und:  # failed teach-back -> navigator call in patient's language
            und2 = 0.85 if not unsupported else 0.75
            if rng.random() >= und2:
                return done("did_not_understand")
        r["understood"] = True
    elif rng.random() >= und:
        if rng.random() < 0.5 * rm:
            return done("did_not_understand")  # unclear message -> patient disengages
    else:
        r["understood"] = True
    r["confidence"] = min(5.0, max(1.0, rng.gauss(p["confidence_mean"] - (0 if r["understood"] else 0.8), 0.7)))
    t += _ln(rng, 2.0 * sp_f * rm)
    r["t_ack"] = t
    if rng.random() < p["decline"]:
        if rng.random() < p["decline_documented"]:
            r.update(closed=True, closure="authorised_informed_decline")
        return done("patient_declined")
    pathway = dec.pathway
    if dec.needs_confirmation:
        if rng.random() < p["confirm_drop"] * rm * support:
            return done("confirmatory_test_not_done")
        t += _ln(rng, p["confirm_median"] * sp_f)
        r["t_confirm"] = t
        if rng.random() >= CONFIRM_POSITIVE.get(pathway, CONFIRM_POSITIVE.get(sp.programme, 0.5)):
            if rng.random() < p["closure_documented"]:
                # breast/FIT return to routine screening; cervical/diabetes need a surveillance plan (v1.1)
                kind = "negative_confirmation" if sp.programme in ("breast", "colorectal_fit") else "surveillance_plan"
                r.update(closed=True, closure=f"evidence_{kind}", t_care=t)
            return done("negative_confirmation")
        pathway = CONFIRM_THEN_REFER.get(pathway, pathway)
    t += _ln(rng, p["consult_median"] * sp_f)
    r["t_consult"] = t
    if crc and baseline and rng.random() < CRC_BASELINE_EXTRA_DROP:
        return done("colonoscopy_not_arranged")
    if pathway in REFERRAL_PATHWAYS or (pathway == "human_review" and sp.programme != "diabetes_hba1c"):
        r["needs_referral"] = True
        if rng.random() < p["referral_lost"]:
            return done("referral_lost")
        if rng.random() < p["referral_reject"]:
            if rng.random() >= p["reroute_after_reject"]:
                return done("referral_rejected_not_rerouted")
            t += _ln(rng, 5.0)
        t += _ln(rng, p["accept_median"] * sp_f)
        r["referral_done"] = True
        t += _ln(rng, p["appt_wait_median"] * sp_f * (CRC_BASELINE_WAIT_FACTOR if crc and baseline else 1.0))
        if rng.random() < p["no_show"] * rm * support:
            if rng.random() >= p["rebook"]:
                return done("no_show_not_rebooked")
            t += _ln(rng, 14.0 * sp_f)
        r["appt_attended"] = True
    if rng.random() < p["treat_drop"] * rm:
        return done("treatment_not_started")
    t += _ln(rng, p["treat_median"] * sp_f)
    if t > HORIZON_DAYS:
        return done("still_in_progress_at_horizon")
    r["t_care"] = t
    if rng.random() < p["closure_documented"]:
        r.update(closed=True, closure="evidence_care_started")
    else:
        r["reason"] = "care_started_closure_not_documented"
    return r


def _median(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None and x <= HORIZON_DAYS]
    return round(st.median(xs), 1) if xs else None


def _pct(n: int, d: int) -> float | None:
    return round(100 * n / d, 1) if d else None


def summarise(rows: list[dict]) -> dict:
    n = len(rows)
    ref = [r for r in rows if r["needs_referral"]]
    out = {
        "n": n,
        "median_days_to_notification": _median([r["t_notify"] for r in rows]),
        "median_days_to_acknowledgement": _median([r["t_ack"] for r in rows]),
        "median_days_to_confirmation": _median([r["t_confirm"] for r in rows]),
        "median_days_to_consultation": _median([r["t_consult"] for r in rows]),
        "median_days_to_care": _median([r["t_care"] for r in rows]),
        "pct_notified": _pct(sum(r["t_notify"] is not None for r in rows), n),
        "pct_understood": _pct(sum(r["understood"] for r in rows), n),
        "pct_referral_completed": _pct(sum(r["referral_done"] for r in ref), len(ref)),
        "pct_appointment_attended": _pct(sum(r["appt_attended"] for r in ref), len(ref)),
        "pct_care_reached_or_resolved": _pct(sum(r["t_care"] is not None for r in rows), n),
        "pct_documented_closure": _pct(sum(r["closed"] for r in rows), n),
        "pct_open_unresolved_at_horizon": _pct(sum(not r["closed"] for r in rows), n),
        "mean_confidence_1to5": round(st.mean([r["confidence"] for r in rows if r["confidence"]]), 2),
    }
    crc = [r for r in rows if r["pathway"] in ("colonoscopy",)]
    out["pct_fit_positive_reaching_colonoscopy"] = _pct(sum(r["appt_attended"] for r in crc), len(crc))
    out["median_days_fit_positive_to_colonoscopy"] = _median([r["t_care"] for r in crc if r["appt_attended"]])
    out["pct_review_results_actioned"] = _pct(sum(r["reason"] != "uncertain_result_not_actioned" for r in rows
                                                  if r["review"]), sum(r["review"] for r in rows))
    reasons = Counter(r["reason"] for r in rows if r["reason"] and not r["closed"])
    out["open_reasons"] = dict(reasons.most_common())
    by_lang: dict[str, list] = defaultdict(list)
    for r in rows:
        by_lang[r["language"]].append(r)
    out["by_language"] = {k: {"n": len(v), "pct_care_reached": _pct(sum(x["t_care"] is not None for x in v), len(v)),
                              "pct_documented_closure": _pct(sum(x["closed"] for x in v), len(v))}
                          for k, v in sorted(by_lang.items())}
    return out


def run_simulation(n: int = 2000, seed: int = 20261120, replications: int = 5, min_age: int = 25) -> dict:
    """Common random numbers: both scenarios see the same cohort, the same results and random stream."""
    cohort = make_cohort(n, seed, min_age=min_age)
    drng = random.Random(seed)
    decisions = [_decision(sp, drng) for sp in cohort]  # identical clinical results in both scenarios
    risks = sorted(risk_multiplier(sp) for sp in cohort)
    top_cut = risks[int(len(risks) * 0.7)]
    reps: dict[str, list[dict]] = {"baseline": [], "with_system": []}
    for k in range(replications):
        for key, params in (("baseline", BASELINE), ("with_system", WITH_SYSTEM)):
            rng = random.Random(seed * 31 + k)
            rows = [journey(sp, params, rng, risk_multiplier(sp) >= top_cut, dec)
                    for sp, dec in zip(cohort, decisions)]
            reps[key].append(summarise(rows))
    scalar_keys = [k for k, v in reps["baseline"][0].items() if isinstance(v, (int, float)) and k != "n"]
    kpis = []
    for k in scalar_keys:
        b = [r[k] for r in reps["baseline"] if r[k] is not None]
        w = [r[k] for r in reps["with_system"] if r[k] is not None]
        kpis.append({"kpi": k, "baseline": round(st.mean(b), 1) if b else None,
                     "baseline_range": [min(b), max(b)] if b else None,
                     "with_system": round(st.mean(w), 1) if w else None,
                     "with_system_range": [min(w), max(w)] if w else None})
    return {
        "label": "SIMULATED - synthetic cohort and assumed parameters; not real-world evidence",
        "n_patients": n, "replications": replications, "horizon_days": HORIZON_DAYS, "seed": seed,
        "kpis": kpis,
        "baseline_detail": reps["baseline"][0], "with_system_detail": reps["with_system"][0],
        "assumptions": {"baseline": BASELINE, "with_system": WITH_SYSTEM,
                        "notes": "Priority outreach uses the latent synthetic risk as a stand-in for the model ranking "
                                 "(top 30%). Medians are of cases reaching that step within the horizon."},
    }
