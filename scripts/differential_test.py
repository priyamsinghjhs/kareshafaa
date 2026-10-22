"""Differential testing: the production rules engine (kareshafaa/clinical_rules.py) vs the INDEPENDENT reference
implementation (kareshafaa_reference/rules.py, written from docs/clinical_rules.md) on N randomised results.

Usage: python scripts/differential_test.py [N=1000000] [--seed S] [--workers W] [--out docs/differential_results.md]

Each mismatch is classified:
  SAFETY-CRITICAL  reference opens a case but engine says negative (missed result);
                   engine tier less urgent than the reference (under-triage);
                   reference requires human review but engine auto-actions (review gate bypassed);
                   both actionable but a different clinical pathway.
  NON-CRITICAL     engine more urgent than the reference (over-triage), extra human review, or engine opens a
                   case the reference calls negative (over-call).
The observed safety-critical error rate is reported with an exact one-sided 95 % upper bound
(Clopper-Pearson; equals the rule of three, 3/N, when zero errors are observed).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import random
import sys
import time
from collections import Counter
from multiprocessing import Pool

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kareshafaa.clinical_rules import PatientContext, ResultInput, evaluate  # noqa: E402
from kareshafaa_reference.rules import ORDER, reference  # noqa: E402

PROGRAMMES = ["colorectal_fit", "cervical", "breast", "diabetes_hba1c"]
JUNK = ["", " ", "abc", "n/a", "??", "pending", "see report", "-", "null", "0x1F", "1e3", "<5", ">10000", "+", "NaN"]
STATUSES = ["final", "preliminary", "partial", "registered", "amended", "corrected", "entered-in-error", "cancelled",
            "retracted", "FINAL", "Final", "unknown", "F", "C", "", None]

FIT_UNITS = ["ug/g", "µg/g", "ug Hb/g", "µg Hb/g", "mcg/g", "UG/G", " ug/g ", "ug/g faeces", "ng/mL", "ng/ml", "mg/L",
             "%", None, "", " ", "ug/L"]
A1C_UNITS = ["%", "% Hb", "%Hb", "% (NGSP)", "NGSP %", "DCCT %", "mmol/mol", "mmol/mol Hb", "MMOL/MOL", None, "",
             "mg/dL", "mmol/L", "g/dL", "percent"]
FPG_UNITS = ["mg/dL", "mg/dl", "MG/DL", "mmol/L", "mmol/l", None, "", "%", "mmol/mol", "g/L"]
HPV_T = ["HPV16", "HPV 16", "HPV18", "hpv 18", "HPV16/18", "HPV 16/18", "HPV16 and 18", "HPV other hr", "HPV other",
         "hrHPV other", "other hr HPV", "HPV other high risk", "HPV positive", "hrHPV positive", "HPV negative",
         "hrHPV negative", "HPV not detected", "HPV invalid", "HPV unsatisfactory", "HPV inadequate"]
CYTO_T = ["NILM", "ASC-US", "ASCUS", "LSIL", "ASC-H", "HSIL", "AGC", "AIS", "SCC", "squamous cell carcinoma",
          "adenocarcinoma", "unsatisfactory", "inadequate"]
BIRADS_FMT = ["{c}{s}", "BI-RADS {c}{s}", "BIRADS {c}{s}", "BI-RADS: {c}{s}", "birads-{c}{s}", "Category {c}{s}",
              "cat {c}{s}", "Cat: {c} {s}", " {c}{s} ", "BI-RADS {c} {s}"]


def boundary(cuts, eps=(0.0, 0.1, 0.01, 1.0)):
    c = random.choice(cuts)
    e = random.choice(eps)
    return round(c + random.choice((-e, e)), 4)


def numeric(cuts, lo, hi):
    r = random.random()
    if r < 0.45:
        return boundary(cuts)
    if r < 0.9:
        return round(random.uniform(lo, hi), random.choice((0, 1, 2)))
    if r < 0.95:
        return random.choice([lo - 0.1, lo, hi, hi + 0.1, -1, 0, 1e6])
    return str(round(random.uniform(lo, hi), 1))  # numeric string


def bad_value():
    return random.choice([float("nan"), float("inf"), "abc", "", True, [], "1,5", "12 %"])


def gen_fit():
    d = {}
    r = random.random()
    if r < 0.80:
        d["value"] = numeric([80, 400, 0, 10000], 0, 700)
        d["unit"] = random.choice(FIT_UNITS) if random.random() < 0.25 else "ug/g"
        if random.random() < 0.03:
            d["value_text"] = random.choice(["positive", "negative", "abc"])
    elif r < 0.95:
        d["value_text"] = random.choice(["positive", "pos", "detected", "abnormal", "negative", "neg", "not detected",
                                         "normal", "invalid", "spoilt", "unsatisfactory", "insufficient",
                                         "rejected sample", "inadequate", "Positive ", "NEGATIVE"] + JUNK)
    elif r < 0.98:
        d["value"] = bad_value()
    return d


def gen_cervical():
    d = {}
    k = random.choice((1, 1, 2, 2, 3))
    toks = [random.choice(HPV_T if random.random() < 0.6 else CYTO_T) for _ in range(k)]
    if random.random() < 0.03:
        toks.append(random.choice(JUNK + ["HPV 31", "LSIL?", "CIN2"]))
    sep = random.choice(["|", ";", ",", " | ", "; "])
    d["value_text"] = sep.join(toks)
    if random.random() < 0.3:
        d["cytology"] = random.choice(CYTO_T + ["", " "])
        if random.random() < 0.5:
            d["value_text"] = random.choice(HPV_T)
    if random.random() < 0.02:
        d["value_text"] = None
    if random.random() < 0.01:
        d["value"] = random.choice([1, 0, 16.0])
    return d


def gen_breast():
    r = random.random()
    if r < 0.85:
        c = random.choice([0, 1, 2, 3, 4, 4, 4, 5, 6, 7, 9])
        s = random.choice(["", "", "", "A", "B", "C", "a", "D"])
        return {"value_text": random.choice(BIRADS_FMT).format(c=c, s=s)}
    if r < 0.95:
        return {"value": random.choice([0, 1, 2, 3, 4, 5, 6, 7, 4.0, 2.0, 4.5, -1, 0.0])}
    return {"value_text": random.choice(JUNK + ["BI-RADS", "negative", "benign", "4/5"])}


def gen_diabetes():
    d = {}
    test = random.choice(["HBA1C", "HBA1C", "FPG", None, None, None, "hba1c", "fpg", "OGTT", "glucose"])
    if test:
        d["test"] = test
    is_fpg = (test or "").upper() == "FPG" or (test is None and random.random() < 0.3)
    if is_fpg:
        unit = random.choice(["mg/dL", "mmol/L"]) if random.random() < 0.8 else random.choice(FPG_UNITS)
        if unit in ("mmol/L", "mmol/l"):
            v = numeric([0.6, 3.0, 5.6, 7.0, 16.7, 27.7, 83], 1, 40)
        else:
            v = numeric([10, 54, 100, 126, 300, 500, 1500], 20, 700)
    else:
        unit = random.choice(["%", "mmol/mol"]) if random.random() < 0.8 else random.choice(A1C_UNITS)
        if unit in ("mmol/mol", "mmol/mol Hb", "MMOL/MOL"):
            v = numeric([9, 20, 38.7, 39, 47.5, 48, 85.7, 86, 139.9, 140, 140.4, 195], 15, 200)
        else:
            v = numeric([3, 4, 5.7, 6.5, 10, 15, 20], 3, 20)
    d["unit"] = unit
    d["value"] = v
    if random.random() < 0.02:
        d["value"] = random.choice([None, bad_value()])
    return d


GEN = {"colorectal_fit": gen_fit, "cervical": gen_cervical, "breast": gen_breast, "diabetes_hba1c": gen_diabetes}
AGE_EDGES = [17, 18, 24, 25, 39, 40, 65, 66, 75, 76]


def gen_case():
    p = random.choice(PROGRAMMES) if random.random() > 0.003 else random.choice(["colon", "", "prostate", None])
    res = GEN.get(p, gen_fit)()
    if p in PROGRAMMES and random.random() < 0.03:
        res["test"] = random.choice(["FIT", "HPV", "CYTOLOGY", "HPV+CYTOLOGY", "MAMMO", "MAMMOGRAM", "HBA1C", "PSA", "fit"])
    res["status"] = "final" if random.random() < 0.85 else random.choice(STATUSES)
    ctx = {}
    r = random.random()
    ctx["age"] = None if r < 0.02 else (random.choice(AGE_EDGES) if r < 0.2 else random.randint(10, 100))
    r = random.random()
    ctx["sex"] = "female" if r < 0.7 else ("male" if r < 0.95 else random.choice([None, "unknown", "F", "other"]))
    for f in ("pregnant", "deceased", "under_specialist_care", "haemoglobinopathy"):
        ctx[f] = random.random() < 0.03
    return p, res, ctx


def rank(t):
    return ORDER.index(t) if t else 99


def compare(p, res, ctx):
    try:
        d = evaluate(ResultInput(p, **res), PatientContext(**ctx))
    except Exception as e:  # the engine must never raise
        return "SAFETY:engine_exception", repr(e)
    ref = reference(p, **res, **ctx)
    if ref.kind != "negative" and d.disposition == "negative":
        return "SAFETY:missed_result", None
    if ref.kind != "negative" and rank(d.tier) > rank(ref.tier):
        return "SAFETY:under_triage", None
    if ref.kind == "review" and d.disposition == "actionable":
        return "SAFETY:review_bypassed", None
    if ref.kind == d.disposition == "actionable" and ref.pathway != d.pathway:
        return "SAFETY:wrong_pathway", None
    if ref.kind == "negative" and d.disposition != "negative":
        return "minor:over_call", None
    if ref.kind != "negative" and rank(d.tier) < rank(ref.tier):
        return "minor:over_triage", None
    if ref.kind == "actionable" and d.disposition == "review":
        return "minor:extra_review", None
    return "agree", None


def worker(args):
    seed, n = args
    random.seed(seed)
    counts, examples, strata = Counter(), {}, Counter()
    for _ in range(n):
        p, res, ctx = gen_case()
        cls, info = compare(p, res, ctx)
        counts[cls] += 1
        ref = reference(p, **res, **ctx)
        strata[(str(p), ref.kind, ref.tier)] += 1
        if cls != "agree" and len(examples.setdefault(cls, [])) < 5:
            examples[cls].append({"programme": p, "result": {k: repr(v) for k, v in res.items()}, "context": ctx,
                                  "info": info, "reference": ref.__dict__})
    return counts, examples, strata


def upper95(k, n):
    """Exact one-sided 95 % Clopper-Pearson upper bound (rule of three when k = 0)."""
    if k == 0:
        return 1 - 0.05 ** (1 / n)  # ~ 3/n
    from scipy.stats import beta
    return float(beta.ppf(0.95, k + 1, n - k))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("n", nargs="?", type=int, default=1_000_000)
    ap.add_argument("--seed", type=int, default=20261021)
    ap.add_argument("--workers", type=int, default=os.cpu_count() or 4)
    ap.add_argument("--out", default="docs/differential_results.md")
    a = ap.parse_args()
    t0 = time.time()
    chunk = 20_000
    jobs = [(a.seed * 1000 + i, min(chunk, a.n - i * chunk)) for i in range(math.ceil(a.n / chunk))]
    counts, examples, strata = Counter(), {}, Counter()
    with Pool(a.workers) as pool:
        for c, ex, st in pool.imap_unordered(worker, jobs):
            counts.update(c)
            strata.update(st)
            for k, v in ex.items():
                examples.setdefault(k, []).extend(v[: 5 - len(examples.get(k, []))])
    n = sum(counts.values())
    safety = sum(v for k, v in counts.items() if k.startswith("SAFETY"))
    minor = sum(v for k, v in counts.items() if k.startswith("minor"))
    secs = time.time() - t0
    summary = {"n": n, "seed": a.seed, "seconds": round(secs, 1), "agree": counts["agree"],
               "safety_critical": safety, "non_critical": minor, "by_class": dict(counts),
               "safety_rate": safety / n, "safety_upper95": upper95(safety, n),
               "examples": examples}
    print(json.dumps({k: v for k, v in summary.items() if k != "examples"}, indent=1))
    for k, v in examples.items():
        print(k, json.dumps(v[:3], default=str)[:1500])
    if a.out:
        with open(a.out.replace(".md", ".json"), "w") as f:
            json.dump(summary | {"strata": {"|".join(map(str, k)): v for k, v in sorted(strata.items(), key=str)}},
                      f, indent=1, default=str)
        with open(a.out, "w") as f:
            f.write("# Differential test: rules engine vs independent reference\n\n")
            f.write(f"Generated by `scripts/differential_test.py {n} --seed {a.seed}` in {secs:.0f} s. "
                    "Reference: `kareshafaa_reference/rules.py`, written from `docs/clinical_rules.md`.\n\n")
            f.write(f"* Randomised results (boundary-weighted, all four programmes, malformed input, units, statuses, "
                    f"contexts): **{n:,}**\n")
            f.write(f"* Safety-critical disagreements: **{safety}** (observed rate {safety / n:.2e}; one-sided 95 % "
                    f"upper bound **{upper95(safety, n):.2e}**{' = rule of three, 3/N' if safety == 0 else ''})\n")
            f.write(f"* Non-critical disagreements (engine more cautious): {minor}\n\n| Class | Count |\n|---|---|\n")
            for k, v in sorted(counts.items()):
                f.write(f"| {k} | {v:,} |\n")
            f.write("\nStrata (programme, reference decision, tier) are in the JSON file next to this report.\n")
    return 1 if safety else 0


if __name__ == "__main__":
    sys.exit(main())
