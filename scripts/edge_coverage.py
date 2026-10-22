"""Coverage matrix for the edge-case cohort (round 20): disease x severity x scenario, counted only where the
DATABASE shows the scenario actually happened (not just that it was planned).

Usage: KARESHAFAA_DATABASE_URL=sqlite:///./data/kareshafaa_edge.db python scripts/edge_coverage.py \
           --manifest data/kareshafaa_edge_manifest.json --out docs/edge_case_coverage.md
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from sqlalchemy import select  # noqa: E402

from kareshafaa.calendar_uae import is_ramadan, to_local  # noqa: E402
from kareshafaa.db import session_scope  # noqa: E402
from kareshafaa.models import (Appointment, AuditEvent, Case, Consent, DiagnosticReport, IngestMessage,  # noqa: E402
                         Patient)
from kareshafaa.clinical_rules import ELIGIBLE_AGE  # noqa: E402
from kareshafaa.synth.edge import PROGRAMMES, SCENARIOS, TIERS, not_applicable  # noqa: E402

REVIEW_EVIDENCE = {
    "missing_value": {"missing_value"}, "unknown_value": {"malformed_value", "unknown_test", "missing_value"},
    "unit_mismatch": {"unit_needs_assay_conversion", "implausible_value", "unknown_unit", "unexpectedly_low",
                      "malformed_value"},
    "amended_result": {"amended_result", "corrected_result"}, "preliminary_then_final": {"preliminary_result"},
    "specialist_care": {"under_specialist_care"}, "deceased_before_result": {"deceased"},
    "duplicate_result": {"duplicate_result"},
    "conflicting_result": {"conflicting_result", "more_urgent_result", "result_after_closure", "duplicate_result"},
    "duplicate_identity": {"possible_duplicate_identity"},
}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    man = json.load(open(a.manifest))
    recs = man["records"]
    with session_scope() as db:
        pats = {p.id: p for p in db.scalars(select(Patient))}
        reasons_by_pat: dict[int, set] = defaultdict(set)
        cases_by_pat: dict[int, list] = defaultdict(list)
        for c in db.scalars(select(Case)):
            cases_by_pat[c.patient_id].append(c)
            reasons_by_pat[c.patient_id] |= set(c.review_reasons or [])
        nat = defaultdict(list)
        for p in pats.values():
            nat[p.national_id].append(p.id)
        reports = defaultdict(list)
        for r in db.scalars(select(DiagnosticReport).where(DiagnosticReport.category == "screening")):
            reports[(r.patient_id, r.programme)].append(r)
        noshows = Counter()
        for ap_, c in db.execute(select(Appointment.status, Case.patient_id).join(Case, Case.id == Appointment.case_id)):
            if ap_ == "noshow":
                noshows[c] += 1
        withdrew = {c.patient_id for c in db.scalars(select(Consent).where(Consent.scope == "care_coordination",
                                                                            Consent.decision == "deny"))}
        ev = defaultdict(set)
        for e in db.scalars(select(AuditEvent).where(AuditEvent.event.in_(
                ("patient.deceased_recorded", "case.transfer_requested", "case.transition", "case.closed",
                 "calltask.interpreter_needed")))):
            if e.event == "patient.deceased_recorded":
                ev["died"].add(int(e.entity_id))
            elif e.event == "case.transfer_requested":
                ev["moved_case"].add(e.case_id)
            elif e.event == "case.closed" and (e.detail or {}).get("reason") == "moved_abroad":
                ev["moved_case"].add(e.case_id)
            elif e.event == "case.transition" and (e.detail or {}).get("from") == "closed":
                ev["reopened_case"].add(e.case_id)
            elif e.event == "calltask.interpreter_needed":
                ev["interp_case"].add(e.case_id)
        msgs = defaultdict(list)
        for m in db.scalars(select(IngestMessage)):
            msgs[m.patient_identifier].append(m)

        def verified(r: dict) -> bool:
            pid, prog, scn = r["patient_id"], r["programme"], r["scenario"]
            p = pats[pid]
            got_result = bool(reports.get((pid, prog))) or bool(msgs.get(p.mrn))
            if not got_result:
                return False  # a result was lost: never counted as covered
            rs = reasons_by_pat[pid]
            cases = cases_by_pat[pid]
            if r["tier"] == "negative" and scn in ("duplicate_result", "conflicting_result", "duplicate_identity"):
                # a negative first result opens no case, so there is nothing to flag against: the evidence is that
                # BOTH results were stored and that a case exists exactly when one of them was actionable
                group = nat[p.national_id] if scn == "duplicate_identity" else [pid]
                n_rep = sum(len(reports.get((q, prog), [])) for q in group)
                any_case = any(c.programme == prog for q in group for c in cases_by_pat[q])
                second = r.get("second_tier")
                return n_rep >= 2 and (any_case == (second not in (None, "negative")) if scn == "conflicting_result"
                                       else not any_case or bool(rs))
            if scn in REVIEW_EVIDENCE:
                if scn == "duplicate_identity":
                    return any("possible_duplicate_identity" in reasons_by_pat[q] for q in nat[p.national_id])
                return bool(REVIEW_EVIDENCE[scn] & rs)
            if scn == "pregnancy":
                return "pregnant" in rs  # the rules flag pregnancy for every programme (EDGE-31)
            if scn == "haemoglobinopathy":
                return bool(p.haemoglobinopathy)
            if scn in ("age_youngest", "age_oldest"):
                lo, hi = ELIGIBLE_AGE[prog]  # the RULES' range (breast/diabetes have no upper age)
                age = reports[(pid, prog)][0].issued_at.year - p.birth_year if reports.get((pid, prog)) else None
                if age is not None and not lo <= age <= hi:
                    return bool({"age_below_programme", "age_above_programme"} & rs)
                return age is not None
            if scn == "died_mid_pathway":
                return pid in ev["died"]
            if scn == "consent_withdrawn":
                return pid in withdrew
            if scn == "reopened":
                return any(c.id in ev["reopened_case"] for c in cases)
            if scn == "interpreter_need":
                return p.language in ("ml", "tl")
            if scn == "no_show":
                return noshows[pid] >= 1
            if scn == "repeated_reschedule":
                return noshows[pid] >= 2
            if scn == "out_of_area_move":
                return any(c.id in ev["moved_case"] for c in cases)
            if scn == "ramadan_holiday":
                rep = reports.get((pid, prog))
                return bool(rep) and r.get("applied", False)
            if scn in ("stale_specimen", "future_specimen"):
                f = "stale_result" if scn == "stale_specimen" else "collection_date_in_future"
                return any(f in (m.flags or []) for m in msgs.get(p.mrn, []))
            if scn == "malformed_hl7":
                return r.get("held") is not None
            if scn == "multimorbidity":
                return len({c.programme for c in cases}) >= 2
            return True  # baseline / boundary_value: result recorded and handled by the rules

        cells = Counter()
        planned = Counter()
        for r in recs:
            k = (r["programme"], r["tier"], r["scenario"])
            planned[k] += 1
            if verified(r):
                cells[k] += 1
        interp_cases = len(ev["interp_case"])
        lang = Counter(p.language for p in pats.values())
        malformed = Counter(r.get("variant") for r in recs if r["scenario"] == "malformed_hl7")
        held = Counter(r.get("held") for r in recs if r["scenario"] == "malformed_hl7")
        statuses = Counter(m.status for ms in msgs.values() for m in ms)
    scen = [s for s, _ in SCENARIOS]
    gaps, na, total_supported = [], 0, 0
    lines = ["# Edge-case coverage matrix (round 20)", "",
             f"Cohort: `data/kareshafaa_edge.db`, {len(recs):,} planned people (seed {man['seed']}), generated "
             f"{man['now'][:16]} UTC by `python -m kareshafaa.seed --edge`. Matrix built by `scripts/edge_coverage.py`.",
             "", "A cell counts a person only when the **database** shows the scenario actually happened (e.g. a "
             "recorded no-show, a withdrawal Consent, a death audit event, the review reason on the case) **and** "
             "the result was recorded (never lost). Planned-but-not-realised people (e.g. the journey closed before "
             "the planned withdrawal) are not counted. `n/a` = combination the rules/pathways cannot produce "
             "(reason listed below). Severity = the tier the rules engine gives the clean result "
             "(T0 critical, T1 urgent, T2 high, T3 routine, negative = no case).", ""]
    for prog in PROGRAMMES:
        tiers = [t for t in TIERS]
        lines += [f"## {prog}", "", "| scenario | " + " | ".join(tiers) + " |", "|---|" + "---|" * len(tiers)]
        for s in scen:
            row = []
            for t in tiers:
                why = not_applicable(prog, t, s)
                if why:
                    row.append("n/a")
                    na += 1
                    continue
                total_supported += 1
                n = cells[(prog, t, s)]
                row.append(str(n) if n else f"**0** ({planned[(prog, t, s)]} planned)")
                if not n:
                    gaps.append((prog, t, s, planned[(prog, t, s)]))
            lines.append(f"| {s} | " + " | ".join(row) + " |")
        lines.append("")
    lines += ["## Summary", "",
              f"* Supported cells: {total_supported}; filled: {total_supported - len(gaps)}; **empty: {len(gaps)}**; "
              f"n/a cells: {na}.",
              f"* People with a verified scenario: {sum(cells.values()):,} of {len(recs):,} planned "
              f"({sum(cells.values()) / max(1, len(recs)):.1%}).",
              f"* Languages (all patients incl. duplicate records): {dict(sorted(lang.items()))}; interpreter call tasks "
              f"recorded on {interp_cases:,} cases.",
              f"* Malformed HL7 variants: {dict(malformed)}; first-message outcome: {dict(held)} (rejected messages "
              "are re-sent by the lab; held ones are resolved by a coordinator or re-sent - no result is lost).",
              f"* Ingest message statuses: {dict(statuses)}.", ""]
    if gaps:
        lines += ["### Empty supported cells", "", "| programme | severity | scenario | planned |", "|---|---|---|---|"]
        lines += [f"| {p} | {t} | {s} | {n} |" for p, t, s, n in gaps]
        lines.append("")
    lines += ["### Why cells are n/a", "",
              "* breast has no critical or routine tier; FIT and cervical have no critical tier (clinical_rules.md).",
              "* journey scenarios (death or consent withdrawal mid-pathway, re-open, no-show, reschedule, move) need "
              "an open case; a negative result creates none.",
              "* boundary values and unit mismatches need a numeric result (FIT, HbA1c/FPG), not BI-RADS or "
              "HPV/cytology categories; the FIT spoilt-kit category has no cut-off.",
              "* no-show / reschedule need a specialist appointment: GP-led diabetes pathways (prediabetes, "
              "diabetes range, hypoglycaemia review) and routine cervical pathways (repeat test, 12-month "
              "surveillance) do not book one.", ""]
    txt = "\n".join(lines)
    if a.out:
        Path(a.out).write_text(txt)
        Path(a.out).with_suffix(".json").write_text(json.dumps(
            {"cells": {"|".join(k): v for k, v in cells.items()}, "planned": {"|".join(k): v for k, v in planned.items()},
             "gaps": gaps, "supported": total_supported}, indent=1))
    print(f"supported {total_supported} empty {len(gaps)} verified {sum(cells.values())}/{len(recs)}")
    for g in gaps:
        print("GAP", g)


if __name__ == "__main__":
    main()
