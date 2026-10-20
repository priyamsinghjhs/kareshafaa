"""Manual fault injection ("hand-made mutation testing") for the safety-critical modules.

Each fault deliberately breaks one clinical threshold, guard or invariant. The test suite must FAIL for every
fault (fault "killed"). A fault that survives means a gap in the tests. Files are restored after each fault.

Usage: python scripts/fault_injection.py [--quick]     (writes docs/fault_injection_results.md)
"""
from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
R, SM, WF, CM = "wasl/clinical_rules.py", "wasl/statemachine.py", "wasl/services/workflow.py", "wasl/comms/service.py"

FAULTS = [
    # id, file, original, replacement, description
    ("F01", R, "FIT_POSITIVE_UG_G = 80.0", "FIT_POSITIVE_UG_G = 120.0", "FIT positive threshold 80 -> 120 ug/g"),
    ("F02", R, "FIT_POSITIVE_UG_G = 80.0", "FIT_POSITIVE_UG_G = 79.0", "FIT positive threshold 80 -> 79 ug/g"),
    ("F03", R, "    if v >= FIT_POSITIVE_UG_G:", "    if v > FIT_POSITIVE_UG_G:", "FIT >= becomes > (boundary)"),
    ("F04", R, "FIT_URGENT_UG_G = 400.0", "FIT_URGENT_UG_G = 401.0", "FIT urgent threshold 400 -> 401"),
    ("F05", R, '"diabetes": 6.5,', '"diabetes": 6.6,', "HbA1c diabetes cut-off 6.5 -> 6.6 %"),
    ("F06", R, '{"prediabetes": 5.7,', '{"prediabetes": 5.8,', "HbA1c prediabetes cut-off 5.7 -> 5.8 %"),
    ("F07", R, '"high": 10.0, "critical": 15.0}', '"high": 10.0, "critical": 15.1}', "HbA1c critical 15.0 -> 15.1 %"),
    ("F08", R, '"diabetes": 47.5,', '"diabetes": 48.5,', "HbA1c diabetes cut-off 47.5 -> 48.5 mmol/mol"),
    ("F09", R, "return (pct - 2.152) / 0.09148", "return (pct - 2.15) / 0.0915", "NGSP->IFCC conversion constants"),
    ("F10", R, '"diabetes": 126,', '"diabetes": 127,', "FPG diabetes 126 -> 127 mg/dL"),
    ("F11", R, '"diabetes": 7.0,', '"diabetes": 7.1,', "FPG diabetes 7.0 -> 7.1 mmol/L"),
    ("F12", R, '{"hypo": 54,', '{"hypo": 50,', "FPG hypoglycaemia 54 -> 50 mg/dL"),
    ("F13", R, 'return _act("high", "assessment_imaging"', 'return _act("routine", "assessment_imaging"', "BI-RADS 0 tier high -> routine"),
    ("F14", R, 'return _act("urgent", "biopsy"', 'return _act("high", "biopsy"', "BI-RADS 4/5 tier urgent -> high"),
    ("F15", R, "    if cat in (1, 2):", "    if cat in (1, 2, 3):", "BI-RADS 3 treated as negative"),
    ("F16", R, 'return _act("high", "colposcopy", "Colposcopy with biopsy (HPV 16/18', 'return _act("routine", "colposcopy", "Colposcopy with biopsy (HPV 16/18', "HPV16/18 tier high -> routine"),
    ("F17", R, 'return _act("urgent", "colposcopy_urgent", "Colposcopy (expedited', 'return _act("high", "colposcopy_urgent", "Colposcopy (expedited', "High-grade cytology urgent -> high"),
    ("F18", R, '"colorectal_fit": (40, 75)', '"colorectal_fit": (45, 75)', "FIT eligibility 40 -> 45"),
    ("F19", R, "    if ctx.pregnant:\n", "    if False:\n", "Pregnancy flag ignored"),
    ("F20", R, "    if ctx.deceased:\n", "    if False:\n", "Deceased flag ignored"),
    ("F21", R, '"missing_unit": "urgent"', '"missing_unit": "routine"', "Missing-unit review floor urgent -> routine"),
    ("F22", R, "return min(ts, key=lambda t: TIER_RANK[t])", "return max(ts, key=lambda t: TIER_RANK[t])", "max_tier picks the LEAST urgent"),
    ("F23", R, '    if status in ("amended", "corrected"):', '    if status in ("corrected",):', "Amended status not reviewed"),
    ("F24", R, "        return _neg(f\"BR-{cat}\"", "        return _act(\"routine\", \"x\", \"x\", f\"BR-{cat}\")\n        return _neg(f\"BR-{cat}\"", "BI-RADS 1/2 create cases (over-call)"),
    ("F25", SM, "    S.ISSUED: frozenset({S.TRIAGED}),", "    S.ISSUED: frozenset({S.TRIAGED, S.PATIENT_NOTIFIED}),", "Triage can be skipped"),
    ("F26", SM, "    if not actor_is_human:\n        raise TransitionError(\"only a human user may close a case\")", "    pass", "System actor may close cases"),
    ("F27", SM, 'NEGATIVE_CONFIRMATION_CLOSES = frozenset({"colorectal_fit", "breast"})', 'NEGATIVE_CONFIRMATION_CLOSES = frozenset({"colorectal_fit", "breast", "cervical"})', "Cervical negative repeat closes without surveillance"),
    ("F28", SM, "if not note or len(note.strip()) < 10:\n            raise TransitionError(\"a closure rationale", "if False:\n            raise TransitionError(\"a closure rationale", "Authorised closure without rationale"),
    ("F29", WF, "    target = max_tier(case.tier, tier, review_min_tier(reasons))", "    target = tier or case.tier", "flag_review can lower the tier"),
    ("F30", WF, "    if open_case is not None:\n        # never create a second open case", "    if False:\n        # never create a second open case", "Duplicate/conflicting result opens a second case"),
    ("F31", WF, "    if case.review_required and not (closure_type == \"authorised_decision\"", "    if False and not (closure_type == \"authorised_decision\"", "Review-flagged case closable with evidence"),
    ("F32", CM, "    if case.review_required and case.state == \"issued\":", "    if False:", "Patient messaged before clinician review"),
    ("F33", WF, "    if case.patient.deceased:\n        raise Invalid(", "    if False:\n        raise Invalid(", "Deceased patient pathway resumed by review"),
]

TESTS = ["tests/test_clinical_golden.py", "tests/test_failsafe.py", "tests/test_properties.py",
         "tests/test_statemachine_exhaustive.py", "tests/test_statemachine.py", "tests/test_workflow.py"]


def run_tests() -> tuple[bool, str]:
    t = [x for x in TESTS if (ROOT / x).exists()]
    for c in (ROOT / "wasl").rglob("__pycache__"):  # never let a stale .pyc hide (or fake) a fault
        shutil.rmtree(c, ignore_errors=True)
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    p = subprocess.run([sys.executable, "-m", "pytest", "-x", "-q", "-p", "no:warnings", "-p", "no:cacheprovider", *t],
                       cwd=ROOT, capture_output=True, text=True, timeout=900, env=env)
    failed = p.returncode != 0
    first = next((ln for ln in p.stdout.splitlines() if ln.startswith("FAILED") or ln.startswith("ERROR")), "")
    return failed, first[:160]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="first 5 faults only")
    a = ap.parse_args()
    faults = FAULTS[:5] if a.quick else FAULTS
    ok, _ = run_tests()
    if ok:
        sys.exit("baseline test run fails - fix the suite before fault injection")
    rows, killed = [], 0
    for fid, f, old, new, desc in faults:
        path = ROOT / f
        src = path.read_text()
        if src.count(old) != 1:
            rows.append((fid, desc, "NOT APPLIED (pattern not unique)", ""))
            continue
        t0 = time.time()
        signal.signal(signal.SIGTERM, lambda *_: (path.write_text(src), sys.exit(1)))  # restore even if killed
        try:
            path.write_text(src.replace(old, new))
            failed, first = run_tests()
        finally:
            path.write_text(src)
        killed += failed
        rows.append((fid, desc, "KILLED" if failed else "SURVIVED", first or "", round(time.time() - t0)))
        print(f"{fid} {'KILLED  ' if failed else 'SURVIVED'} {desc}  | {first}", flush=True)
    applied = [r for r in rows if not r[2].startswith("NOT")]
    rate = killed / max(1, len(applied))
    out = ["# Fault-injection results", "", f"Generated by `scripts/fault_injection.py` on {time.strftime('%Y-%m-%d %H:%M %Z')}.",
           f"Faults applied: {len(applied)}; killed: {killed}; kill rate: {rate:.0%}.", "",
           "| ID | Injected fault | Result | First failing test |", "|---|---|---|---|"]
    out += [f"| {r[0]} | {r[1]} | {r[2]} | `{r[3]}` |" for r in rows]
    (ROOT / "docs").mkdir(exist_ok=True)
    (ROOT / "docs" / "fault_injection_results.md").write_text("\n".join(out) + "\n")
    print(f"kill rate {killed}/{len(applied)} = {rate:.0%}")


if __name__ == "__main__":
    main()
