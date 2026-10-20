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
IN, MO, CAL = "wasl/services/ingest.py", "wasl/models.py", "wasl/calendar_uae.py"

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
    # Iteration 7 failure-mode round (adversarial HL7, concurrency, time, duplicate identities)
    ("F34", IN, "    main = min(mains, key=lambda k: _severity(prog, k[0][1], k[1], k[2], cyto)) if len(mains) > 1 else mains[0]",
     "    main = mains[0]", "HL7: first OBX used, not the most severe (bilateral BI-RADS 1 + 4)"),
    ("F35", IN, "    if len(pids) > 1:\n        return ParsedResult(", "    if False:\n        return ParsedResult(",
     "HL7: two patients in one message merged"),
    ("F36", IN, 'val = num if cmp_ in ("", "=") and len(parts) <= 2 else', "val = num if True else",
     "HL7: structured-numeric comparator dropped ('>9.9' read as 9.9)"),
    ("F37", IN, "or prior.payload_sha256 == r.payload_sha256):", "or True):",
     "HL7: reused control ID with new content treated as a replay"),
    ("F38", MO, '    __mapper_args__ = {"version_id_col": row_version}\n', "",
     "Concurrency: optimistic row-version removed"),
    ("F39", CAL, "    return to_utc(add_working_hours(to_local(start_utc), hours))", "    return add_working_hours(start_utc, hours)",
     "Time: SLA working calendar applied to the UTC date"),
    ("F40", WF, "    group = identity_group(db, patient)", "    group = [patient.id]",
     "Identity: other records with the same Emirates ID ignored"),
    ("F41", WF, "    if not attended and now < appt.start:", "    if False:",
     "Clock: no-show accepted before the appointment time"),
    ("F42", IN, "    if not isinstance(v, str) or not _NUMERIC.match(v):", "    if not isinstance(v, str):",
     "HL7: float() quirks accepted ('1_000' read as 1000)"),
    ("F43", CM, '    if not actor.is_human and consent_status(db, p.id, "care_coordination") == "deny":', "    if False:",
     "Consent: automated messages sent after care-coordination consent was withdrawn"),
    ("F44", WF, '    if scope == "care_coordination" and decision == "deny":', "    if False:",
     "Consent: withdrawal does not send open cases to clinician review"),
    ("F45", IN, "        elif now - r.collected_at > timedelta(days=STALE_RESULT_DAYS):", "        elif False:",
     "Specimen: a months-old specimen is handled as a fresh result"),
    ("F46", IN, "        if r.collected_at > now + FUTURE_TOLERANCE:", "        if False:",
     "Specimen: a future-dated specimen is not reviewed"),
    ("F47", WF, '        if reason == "deceased" and not case.patient.deceased:', "        if False:",
     "Death: a living patient's case can be closed as 'deceased'"),
    ("F48", WF, '        flag_review(db, c, ["deceased"], actor, now, detail={"note": note})', "        pass",
     "Death: open cases are not sent to clinician review"),
    ("F49", WF, "    if beyond and (not beyond_target_reason or len(beyond_target_reason.strip()) < 10):", "    if False:",
     "Booking: appointment after the pathway target accepted without a reason"),
    ("F50", IN, '        mag = ("birads", -(int(m.group(1)) * 10 + " abc".index((m.group(2) or " ").lower())))', "        pass",
     "HL7: bilateral BI-RADS 4 + 5 headlined as BI-RADS 4"),
]

TESTS = ["tests/test_clinical_golden.py", "tests/test_failsafe.py", "tests/test_properties.py",
         "tests/test_statemachine_exhaustive.py", "tests/test_statemachine.py", "tests/test_workflow.py",
         "tests/test_ingest_adversarial.py", "tests/test_concurrency.py", "tests/test_time_handling.py",
         "tests/test_duplicate_identity.py", "tests/test_consent_withdrawal.py", "tests/test_deceased.py",
         "tests/test_journey_api.py"]


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
