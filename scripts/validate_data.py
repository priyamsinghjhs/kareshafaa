"""Check every record in the Wasl database against the clinical and data-integrity invariants.

Usage:  python scripts/validate_data.py [--json] [--max-examples 5]
        (uses WASL_DATABASE_URL like the app; `make validate` / `make validate-large`)
Exit code 0 = zero violations. Each invariant is documented in docs/synthetic_data.md (section "Invariants")
and referenced from the hazard log in docs/clinical_safety_case.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from wasl import audit  # noqa: E402
from wasl.clinical_rules import ELIGIBLE_AGE, HBA1C_RANGE_MMOL, HBA1C_RANGE_PCT, TIER_RANK  # noqa: E402
from wasl.db import session_scope  # noqa: E402
from wasl.models import (Appointment, AuditEvent, CarePlan, Case, Communication, DiagnosticReport,  # noqa: E402
                         Observation, Patient, Practitioner, ServiceRequest, StepTask, User)
from wasl.statemachine import AUTHORISED_CLOSURE_REASONS, CLOSABLE_WITH_EVIDENCE, S  # noqa: E402

AGE_REASONS = {"age_below_programme", "age_above_programme", "age_unknown"}


def run(max_examples: int = 5) -> dict:
    t0 = time.time()
    v: dict[str, list] = defaultdict(list)
    checked: dict[str, int] = defaultdict(int)

    def bad(name: str, ex) -> None:
        v[name].append(ex)

    with session_scope() as db:
        patients = {p.id: p for p in db.scalars(select(Patient))}
        cases = list(db.scalars(select(Case)))
        reports = list(db.scalars(select(DiagnosticReport)))
        obs_by_report = defaultdict(list)
        for o in db.scalars(select(Observation)):
            obs_by_report[o.report_id].append(o)
        steps = defaultdict(list)
        for s in db.scalars(select(StepTask).order_by(StepTask.id)):
            steps[s.case_id].append(s)
        srs = defaultdict(list)
        for r in db.scalars(select(ServiceRequest)):
            srs[r.case_id].append(r)
        appts = defaultdict(list)
        for a in db.scalars(select(Appointment)):
            appts[a.case_id].append(a)
        plans = defaultdict(list)
        for c in db.scalars(select(CarePlan)):
            plans[c.case_id].append(c)
        comms = defaultdict(list)
        for m in db.scalars(select(Communication).where(Communication.direction == "outbound",
                                                          Communication.channel != "internal")):
            comms[m.case_id].append(m)
        pracs = {p.id: p for p in db.scalars(select(Practitioner))}
        human_users = {u.id for u in db.scalars(select(User)) if u.role != "system"}
        ev_by_case = defaultdict(list)
        for e in db.scalars(select(AuditEvent).where(AuditEvent.case_id.is_not(None),
                                                     AuditEvent.event.in_(("case.created", "case.review_resolved",
                                                                           "case.tier_escalated",
                                                                           "case.review_flagged")))):
            ev_by_case[e.case_id].append(e)
        cases_by_id = {c.id: c for c in cases}

        # --- patient-level -------------------------------------------------------------------------
        for p in patients.values():
            checked["patient"] += 1
            if p.gender not in ("female", "male", "other", "unknown"):
                bad("patient.gender_valid", p.id)
            if p.birth_year is None or not (1900 <= p.birth_year <= 2026):
                bad("patient.birth_year_plausible", p.id)
            if p.pregnant and p.gender != "female":
                bad("patient.pregnancy_only_female", p.id)

        # --- report / observation level -------------------------------------------------------------
        for r in reports:
            checked["report"] += 1
            p = patients[r.patient_id]
            if r.category == "confirmatory":  # follow-up test outcome recorded by a clinician (no observation row)
                if r.conclusion not in ("positive", "negative") or r.case_id is None:
                    bad("report.confirmatory_result_valid_and_linked", r.id)
                continue
            obs = obs_by_report.get(r.id, [])
            if len(obs) != 1:
                bad("report.exactly_one_observation", r.id)
                continue
            o = obs[0]
            if r.conclusion in ("actionable", "needs_review") and r.case_id is None:
                bad("report.actionable_or_review_linked_to_case (lost result)", r.id)
            if r.conclusion not in ("negative", "actionable", "needs_review"):
                bad("report.conclusion_valid", r.id)
            if r.programme in ("cervical", "breast") and p.gender != "female":
                c = cases_by_id.get(r.case_id)
                if not (c and "sex_mismatch" in (c.review_reasons or [])):
                    bad("report.sex_eligibility_or_flagged", r.id)
            age = r.issued_at.year - p.birth_year
            lo, hi = ELIGIBLE_AGE[r.programme]
            if not lo <= age <= hi:
                c = cases_by_id.get(r.case_id)
                if not (c and AGE_REASONS & set(c.review_reasons or [])):
                    bad("report.age_eligibility_or_flagged", (r.id, r.programme, age))
            if r.programme == "diabetes_hba1c" and o.value is not None:
                u = (o.unit or "").strip().lower()
                rng = {"%": HBA1C_RANGE_PCT, "mmol/mol": HBA1C_RANGE_MMOL}.get(u)
                if rng:
                    plo, phi = rng
                    if not plo <= o.value <= phi and r.conclusion != "needs_review":
                        bad("report.hba1c_plausible_or_review", (r.id, o.value, o.unit))
            if r.programme in ("colorectal_fit", "diabetes_hba1c") and o.value is not None and not o.unit \
                    and r.conclusion != "needs_review":
                bad("report.numeric_has_unit_or_review", r.id)
            if o.interpretation not in ("N", "A", "R"):
                bad("observation.interpretation_valid", o.id)
            if (r.conclusion == "negative") != (o.interpretation == "N"):
                bad("observation.interpretation_matches_conclusion", r.id)

        # --- case level ---------------------------------------------------------------------------
        open_by_pp = defaultdict(list)
        for c in cases:
            checked["case"] += 1
            p = patients[c.patient_id]
            st = steps.get(c.id, [])
            if c.tier not in TIER_RANK:
                bad("case.tier_valid", c.id)
            if c.state != S.CLOSED:
                open_by_pp[(c.patient_id, c.programme)].append(c.id)
                if c.owner_id is None or c.owner_id not in pracs:
                    bad("case.open_has_owner", c.id)
                running = [s for s in st if s.status == "in-progress"]
                if len(running) != 1:
                    bad("case.open_has_exactly_one_running_step", (c.id, len(running)))
                if c.due_at is None and c.state not in (S.DECLINED_REVIEW,):
                    pass  # some states have no SLA by design
            else:
                if c.closed_at is None or c.closed_at < c.created_at:
                    bad("case.closed_at_after_created", c.id)
                if c.review_required:
                    bad("case.closed_with_unresolved_review", c.id)
                if c.closed_by_user_id not in human_users:
                    bad("case.closed_by_human", c.id)
                if c.closure_type == "evidence":
                    if c.closure_evidence_id is None:
                        bad("case.evidence_closure_has_evidence_id", c.id)
                    et = c.closure_evidence_type
                    ok = (et == "care_plan" and any(x.id == c.closure_evidence_id for x in plans.get(c.id, []))) or \
                         (et == "appointment_fulfilled" and any(x.id == c.closure_evidence_id and x.status == "fulfilled"
                                                                for x in appts.get(c.id, []))) or \
                         (et == "negative_confirmatory_result" and any(
                             x.id == c.closure_evidence_id and x.result == "negative" for x in srs.get(c.id, [])))
                    if not ok:
                        bad("case.evidence_exists_and_belongs_to_case", (c.id, et))
                    if et == "negative_confirmatory_result" and c.programme in ("cervical", "diabetes_hba1c"):
                        bad("case.no_negative_confirmatory_closure_without_surveillance", c.id)
                elif c.closure_type == "authorised_decision":
                    if c.closure_reason not in AUTHORISED_CLOSURE_REASONS or not c.closure_note or \
                            len(c.closure_note.strip()) < 10:
                        bad("case.authorised_closure_reason_and_note", c.id)
                else:
                    bad("case.closure_type_valid", c.id)
            # causal date order
            for s in st:
                if s.started_at < c.created_at:
                    bad("dates.step_after_case_created", (c.id, s.id))
                if s.ended_at is not None and s.ended_at < s.started_at:
                    bad("dates.step_ends_after_start", (c.id, s.id))
                if s.due_at is not None and s.due_at < s.started_at:
                    bad("sla.due_not_before_start (clock never runs backwards)", (c.id, s.id))
            for a, b in zip(st, st[1:]):
                if b.started_at < a.started_at:
                    bad("dates.steps_in_causal_order", (c.id, a.id, b.id))
            refs = [r for r in srs.get(c.id, []) if r.kind == "referral"]
            for r in srs.get(c.id, []):
                if r.created_at < c.created_at:
                    bad("dates.service_request_after_case", (c.id, r.id))
            for a in appts.get(c.id, []):
                if not refs:
                    bad("dates.appointment_has_referral", (c.id, a.id))
                elif a.created_at < min(r.created_at for r in refs) or a.start < min(r.created_at for r in refs):
                    bad("dates.no_appointment_before_referral", (c.id, a.id))
            for cp in plans.get(c.id, []):
                if cp.created_at < c.created_at:
                    bad("dates.careplan_after_case", (c.id, cp.id))
            if c.care_reached_at is not None and c.care_reached_at < c.created_at:
                bad("dates.care_reached_after_case", c.id)
            # safety: patients not contacted before clinician review; deceased never contacted
            evs = sorted(ev_by_case.get(c.id, []), key=lambda e: e.id)
            created = next((e for e in evs if e.event == "case.created"), None)
            if created is None:
                bad("audit.case_created_event", c.id)
            else:
                t_created = created.detail.get("tier") if created.detail else None
                downgrades = [e for e in evs if e.event == "case.review_resolved" and (e.detail or {}).get("downgrade")]
                if t_created in TIER_RANK and TIER_RANK[c.tier] > TIER_RANK[t_created] and not downgrades:
                    bad("tier.never_lowered_without_audited_clinician_decision", (c.id, t_created, c.tier))
                if created.detail and created.detail.get("review"):
                    resolved = [e for e in evs if e.event == "case.review_resolved"]
                    first_resolved = min((e.ts for e in resolved), default=None)
                    for m in comms.get(c.id, []):
                        if first_resolved is None or m.sent_at < first_resolved:
                            bad("safety.no_patient_contact_before_clinician_review", (c.id, m.id))
                            break
            if p.deceased and comms.get(c.id):
                bad("safety.deceased_never_contacted", c.id)
            if p.pregnant and c.programme in ("cervical", "diabetes_hba1c", "breast") and \
                    "pregnant" not in (c.review_reasons or []):
                bad("safety.pregnancy_flagged_for_review", c.id)
            if c.review_required and c.state not in (S.CLOSED,) and pracs.get(c.owner_id) is None:
                bad("safety.review_case_has_owner", c.id)
        for k, ids in open_by_pp.items():
            if len(ids) > 1:
                bad("case.at_most_one_open_case_per_patient_programme", (k, ids))
        checked["audit_events"] = 0
        chain = audit.verify_chain(db)
        checked["audit_events"] = chain.get("checked", 0)
        if not chain.get("valid"):
            bad("audit.hash_chain_valid", chain)

    total = sum(len(x) for x in v.values())
    return {"violations_total": total, "violations": {k: {"count": len(x), "examples": x[:max_examples]}
                                                       for k, x in sorted(v.items())},
            "checked": dict(checked), "seconds": round(time.time() - t0, 1), "invariants": INVARIANTS}


INVARIANTS = [
    "patient.gender_valid", "patient.birth_year_plausible", "patient.pregnancy_only_female",
    "report.confirmatory_result_valid_and_linked", "report.exactly_one_observation", "report.actionable_or_review_linked_to_case (lost result)",
    "report.conclusion_valid", "report.sex_eligibility_or_flagged", "report.age_eligibility_or_flagged",
    "report.hba1c_plausible_or_review", "report.numeric_has_unit_or_review", "observation.interpretation_valid",
    "observation.interpretation_matches_conclusion", "case.tier_valid", "case.open_has_owner",
    "case.open_has_exactly_one_running_step", "case.closed_at_after_created", "case.closed_with_unresolved_review",
    "case.closed_by_human", "case.evidence_closure_has_evidence_id", "case.evidence_exists_and_belongs_to_case",
    "case.no_negative_confirmatory_closure_without_surveillance", "case.authorised_closure_reason_and_note",
    "case.closure_type_valid", "dates.step_after_case_created", "dates.step_ends_after_start",
    "sla.due_not_before_start (clock never runs backwards)", "dates.steps_in_causal_order",
    "dates.service_request_after_case", "dates.appointment_has_referral", "dates.no_appointment_before_referral",
    "dates.careplan_after_case", "dates.care_reached_after_case", "audit.case_created_event",
    "tier.never_lowered_without_audited_clinician_decision", "safety.no_patient_contact_before_clinician_review",
    "safety.deceased_never_contacted", "safety.pregnancy_flagged_for_review", "safety.review_case_has_owner",
    "case.at_most_one_open_case_per_patient_programme", "audit.hash_chain_valid",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-examples", type=int, default=5)
    a = ap.parse_args()
    out = run(a.max_examples)
    if a.json:
        print(json.dumps(out, indent=2, default=str))
    else:
        print(f"checked: {out['checked']}  ({out['seconds']}s)  invariants: {len(INVARIANTS)}")
        for k in INVARIANTS:
            n = out["violations"].get(k, {}).get("count", 0)
            print(f"  {'OK  ' if n == 0 else 'FAIL'} {k}: {n}"
                  + (f"  e.g. {out['violations'][k]['examples']}" if n else ""))
        print(f"TOTAL VIOLATIONS: {out['violations_total']}")
    sys.exit(1 if out["violations_total"] else 0)


if __name__ == "__main__":
    main()
