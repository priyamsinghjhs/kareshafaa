"""Check every record in the KareShafaa database against the clinical and data-integrity invariants.

Usage:  python scripts/validate_data.py [--json] [--max-examples 5]
        (uses KARESHAFAA_DATABASE_URL like the app; `make validate` / `make validate-large`)
Exit code 0 = zero violations. Each invariant is documented in docs/synthetic_data.md (section "Invariants")
and referenced from the hazard log in docs/clinical_safety_case.md.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timedelta
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from kareshafaa import audit  # noqa: E402
from kareshafaa.clinical_rules import (ELIGIBLE_AGE, HBA1C_RANGE_MMOL, HBA1C_RANGE_PCT, TIER_RANK, ResultInput,  # noqa: E402
                                 evaluate)
from kareshafaa.services.workflow import patient_context  # noqa: E402  # noqa: E402
from kareshafaa.db import session_scope  # noqa: E402
from kareshafaa.services import patients as pt  # noqa: E402
from kareshafaa.models import (Appointment, AuditEvent, CarePlan, Case, Communication, DiagnosticReport,  # noqa: E402
                         Observation, Patient, Practitioner, ServiceRequest, StepTask, User)
from kareshafaa.statemachine import AUTHORISED_CLOSURE_REASONS, CLOSABLE_WITH_EVIDENCE, S  # noqa: E402

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
        human_users = {uid for uid, role in db.execute(select(User.id, User.role)) if role != "system"}  # columns only: works on pre-login-schema DBs too
        ev_by_case = defaultdict(list)
        for e in db.scalars(select(AuditEvent).where(AuditEvent.case_id.is_not(None),
                                                     AuditEvent.event.in_(("case.created", "case.review_resolved",
                                                                           "case.tier_escalated",
                                                                           "case.review_flagged")))):
            ev_by_case[e.case_id].append(e)
        cases_by_id = {c.id: c for c in cases}
        # death recorded during the pathway (workflow.record_death): contact BEFORE that moment was legitimate
        died_at = {int(e.entity_id): e.ts for e in db.scalars(select(AuditEvent).where(
            AuditEvent.event == "patient.deceased_recorded"))}
        death_revoked = {int(e.entity_id) for e in db.scalars(select(AuditEvent).where(
            AuditEvent.event == "patient.deceased_revoked"))}  # listed for re-opening by the lead at revocation

        # --- patient-level -------------------------------------------------------------------------
        for p in patients.values():
            checked["patient"] += 1
            if p.gender not in ("female", "male", "other", "unknown"):
                bad("patient.gender_valid", p.id)
            if p.birth_year is None or not (1900 <= p.birth_year <= 2026):
                bad("patient.birth_year_plausible", p.id)
            if p.pregnant and p.gender != "female":
                bad("patient.pregnancy_only_female", p.id)
            # registered (non-synthetic) records: real-world identity rules
            if p.national_id and not (p.mrn or "").startswith("SYN-"):
                try:
                    if pt.normalise_emirates_id(p.national_id) != p.national_id:
                        bad("patient.emirates_id_valid_checksum", p.id)
                except Exception:  # noqa: BLE001
                    bad("patient.emirates_id_valid_checksum", p.id)
            if p.birth_date is not None and p.birth_year != p.birth_date.year:
                bad("patient.birth_year_matches_birth_date", p.id)
            if p.registered_at is not None and not p.registered_by:
                bad("patient.registration_has_registrar", p.id)
        eids = defaultdict(list)
        for p in patients.values():
            if p.national_id and p.registered_at is not None:
                eids[p.national_id].append(p.id)
        for e, ids in eids.items():
            if len(ids) > 1:
                bad("patient.registered_emirates_id_unique", ids)
        # patient app accounts: linked to a record; activation codes used once, before expiry
        from kareshafaa.models import PatientInvite as _PI
        for uid, pid_ in db.execute(select(User.id, User.patient_id).where(User.role == "patient")):
            checked["patient_accounts"] += 1
            if pid_ is None or pid_ not in patients:
                bad("account.patient_role_linked_to_patient", uid)
        for iv in db.scalars(select(_PI)):
            checked["patient_invites"] += 1
            if iv.used_at is not None and (iv.used_at > iv.expires_at or (iv.revoked_at is not None and iv.revoked_at < iv.used_at)):
                bad("account.invite_used_only_while_valid", iv.id)
        # recalls: one open recall per person and programme; completed ones say when; cancelled ones say why
        from kareshafaa.models import Recall as _RC
        open_rc = defaultdict(list)
        for rc in db.scalars(select(_RC)):
            checked["recalls"] += 1
            if rc.patient_id not in patients:
                bad("recall.patient_exists", rc.id)
            if rc.status in ("due", "invited"):
                open_rc[(rc.patient_id, rc.programme)].append(rc.id)
            if rc.status == "completed" and (rc.completed_at is None or rc.completed_report_id is None):
                bad("recall.completed_has_result", rc.id)
            if rc.status == "cancelled" and not (rc.closed_note or "").strip():
                bad("recall.cancelled_has_reason", rc.id)
            if rc.status == "invited" and rc.invited_at is None:
                bad("recall.invited_has_time", rc.id)
        for k, ids in open_rc.items():
            if len(ids) > 1:
                bad("recall.one_open_per_programme", ids)

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
            # independent re-application of the rules: no stored result may be "negative" if the rules say it
            # needs a case (lost actionable result), and every case-creating result must be linked to a case
            d = evaluate(ResultInput(r.programme, o.value, o.value_text, o.unit, None, r.status), patient_context(
                p, r.programme, r.issued_at))
            checked["rules_reapplied"] += 1
            if d.creates_case and (r.case_id is None or r.conclusion == "negative"):
                bad("report.rules_reapplied_no_lost_actionable", (r.id, d.rule_id, r.conclusion))
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
            for s_ in st:  # alert precision: once care has started, the pathway target must not cap the clock
                if s_.state == S.TREATMENT_STARTED and s_.sla_hours and s_.due_at is not None and s_.due_at <= s_.started_at:
                    bad("sla.post_care_step_not_instantly_overdue", (c.id, s_.id))
            if c.state != S.CLOSED:
                open_by_pp[(c.patient_id, c.programme)].append(c.id)
                if c.owner_id is None or c.owner_id not in pracs:
                    bad("case.open_has_owner", c.id)
                running = [s for s in st if s.status == "in-progress"]
                if len(running) != 1:
                    bad("case.open_has_exactly_one_running_step", (c.id, len(running)))
                if c.target_due_at is None:
                    bad("case.has_pathway_target", c.id)
                elif c.care_reached_at is None:
                    for s_ in running:
                        if s_.due_at is not None and s_.due_at > max(c.target_due_at, s_.started_at):
                            bad("sla.step_clock_within_pathway_target", (c.id, s_.id))
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
            if p.deceased and any(p.id not in died_at or (m.sent_at and m.sent_at >= died_at[p.id])
                                  for m in comms.get(c.id, [])):
                bad("safety.deceased_never_contacted", c.id)
            if c.closure_reason == "deceased" and not p.deceased and p.id not in death_revoked:
                bad("closure.deceased_reason_requires_deceased_patient", c.id)
            if p.pregnant and c.programme in ("cervical", "diabetes_hba1c", "breast") and \
                    "pregnant" not in (c.review_reasons or []):
                bad("safety.pregnancy_flagged_for_review", c.id)
            if c.review_required and c.state not in (S.CLOSED,) and pracs.get(c.owner_id) is None:
                bad("safety.review_case_has_owner", c.id)
        for k, ids in open_by_pp.items():
            if len(ids) > 1:
                bad("case.at_most_one_open_case_per_patient_programme", (k, ids))
        # one person under two records (same Emirates ID) must not have two open cases for one programme
        by_eid = defaultdict(list)
        for (pid_, prog_), ids in open_by_pp.items():
            eid = getattr(patients[pid_], "national_id", None)
            if eid:
                by_eid[(eid, prog_)].extend(ids)
        for k, ids in by_eid.items():
            checked["identity_groups"] += 1
            if len(ids) > 1:
                bad("identity.one_open_case_per_emirates_id_programme", (k, ids))
        # automated SLA reminders reach patients only inside the UAE-local message window
        from kareshafaa.calendar_uae import to_local as _tl
        from kareshafaa.config import get_settings as _gs
        from kareshafaa.models import Communication as _Cm

        _w0, _w1 = _gs().patient_message_window
        for cm in db.query(_Cm).filter(_Cm.category == "reminder", _Cm.channel.in_(("app", "sms", "whatsapp"))):
            checked["reminders"] += 1
            if not _w0 <= _tl(cm.sent_at).hour < _w1:
                bad("comms.reminders_inside_patient_window", (cm.id, cm.sent_at.isoformat()))
        # clock sanity: attendance / no-show never recorded before the appointment (early arrival allowance)
        from kareshafaa.models import Appointment as _A
        from kareshafaa.services.workflow import ATTENDANCE_EARLY_HOURS as _EARLY

        for a in db.query(_A).filter(_A.status.in_(("fulfilled", "noshow"))):
            checked["attendance"] += 1
            if a.updated_at is not None and (
                    (a.status == "noshow" and a.updated_at < a.start)
                    or (a.status == "fulfilled" and a.updated_at < a.start - timedelta(hours=_EARLY))):
                bad("dates.attendance_not_before_appointment", (a.id, a.status))
        from collections import Counter as _C

        from kareshafaa.models import Escalation as _E
        pw = _C((e.case_id, e.level) for e in db.query(_E).filter(_E.kind == "pathway"))
        for k, n in pw.items():
            if n > 1:
                bad("alert.one_pathway_alert_per_case_per_level", (k, n))
        # patient content: only signed-off wording reaches patients; sign-offs respect separation of duties
        from kareshafaa.comms import governance as _G
        from kareshafaa.comms.content import PATHWAY_TEXT as _PT
        from kareshafaa.models import Communication as _Cm, ContentApproval as _CA
        live = _G.live_ids(db)
        drafts = {(lang, t["meaning"]) for lang, d in _PT.items() if lang != "en" for pw, t in d.items()
                  if f"pathway:{pw}:{lang}" not in live}
        for cm in db.query(_Cm).filter(_Cm.language.in_(["ar", "hi", "ur"])):
            if any(lang == cm.language and frag in (cm.content or "") for lang, frag in drafts):
                bad("content.no_unapproved_translation_sent", cm.id)
        # Round 16: a patient message is in ONE language (no English wording spliced into an ar/hi/ur template;
        # untranslated result wording is sent wholly in English plus an interpreter call)
        import re as _re
        _latin = _re.compile(r"[A-Za-z]+[,.]?\s+[A-Za-z]+[,.]?\s+[A-Za-z]+")
        _native = _re.compile(r"[\u0600-\u06FF\u0900-\u097F]")
        for cm in db.query(_Cm).filter(_Cm.status != "not-done", _Cm.template_key != "call_script"):
            checked["patient_messages"] = checked.get("patient_messages", 0) + 1
            txt = _re.sub(r"https://\S+", "", (cm.content or "").replace("[SIMULATED - not sent] ", ""))
            if (cm.language in ("ar", "hi", "ur") and _latin.search(txt)) or (cm.language == "en" and _native.search(txt)):
                bad("comms.sent_message_single_language", cm.id)
        by = _C()
        for a in db.query(_CA):
            by[(a.content_id, a.sha256, a.user_id)] += 1
        for k, n in by.items():
            if n > 1:
                bad("content.signoffs_by_different_people", k)
        # --- round 20 (edge cohort) invariants -----------------------------------------------------------
        from kareshafaa.models import Consent as _Cs, IngestMessage as _IM

        reopen_at: dict[int, datetime] = {}
        for e in db.scalars(select(AuditEvent).where(AuditEvent.event == "case.transition")):
            if (e.detail or {}).get("from") == "closed" and e.case_id:
                reopen_at[e.case_id] = max(reopen_at.get(e.case_id, e.ts), e.ts)
        from kareshafaa.config import get_settings as _gs

        restart = _gs().reopen_restarts_target  # CLINICAL LEAD TO CONFIRM; default False keeps the original target
        restarted = {e.case_id for e in db.scalars(select(AuditEvent).where(AuditEvent.event == "case.target_restarted"))}
        flagged = {e.case_id for e in db.scalars(select(AuditEvent).where(AuditEvent.event == "case.reopened_past_target"))}
        for cid, ts in reopen_at.items():
            c = cases_by_id.get(cid)
            checked["reopened"] = checked.get("reopened", 0) + 1
            if c is None:
                continue
            if restart:
                if c.state != S.CLOSED and c.target_due_at is not None and c.target_due_at <= ts:
                    bad("reopen.target_follows_policy", cid)
            elif cid in restarted or (c.target_due_at is not None and c.target_due_at <= ts and cid not in flagged):
                bad("reopen.target_follows_policy", cid)  # target relaxed, or breached on re-opening but not flagged
        withdrawn: dict[int, datetime] = {}
        for cs in db.scalars(select(_Cs).where(_Cs.scope == "care_coordination", _Cs.decision == "deny")):
            withdrawn[cs.patient_id] = min(withdrawn.get(cs.patient_id, cs.recorded_at), cs.recorded_at)
        auto_comm = defaultdict(list)
        for e in db.scalars(select(AuditEvent).where(AuditEvent.event.like("communication.%"),
                                                     AuditEvent.actor_user_id.is_(None),
                                                     AuditEvent.actor_role != "patient")):
            if e.case_id:
                auto_comm[e.case_id].append(e.ts)
        for c in cases:
            w = withdrawn.get(c.patient_id)
            if w is None:
                continue
            checked["consent_withdrawn_cases"] = checked.get("consent_withdrawn_cases", 0) + 1
            if (c.closed_at is None or c.closed_at > w) and c.created_at <= w \
                    and "consent_withdrawn" not in (c.review_reasons or []):
                bad("consent.withdrawal_sends_open_cases_to_review", c.id)
            if any(t > w for t in auto_comm.get(c.id, [])):
                bad("consent.no_automated_contact_after_withdrawal", c.id)
        for c in cases:
            d_at = died_at.get(c.patient_id)
            if d_at is not None and c.created_at <= d_at and (c.closed_at is None or c.closed_at > d_at) \
                    and "deceased" not in (c.review_reasons or []):
                bad("death.open_cases_reviewed_when_recorded", c.id)
        for m in db.scalars(select(_IM)):
            checked["ingest_messages"] = checked.get("ingest_messages", 0) + 1
            if m.status not in ("processed", "needs_review", "needs_match_review", "resolved", "rejected"):
                bad("ingest.status_valid", (m.id, m.status))
            if m.status in ("processed", "resolved") and m.report_id is None:
                bad("ingest.processed_message_has_report", m.id)
            timing = {"stale_result", "collection_date_in_future"} & set(m.flags or [])
            if timing and m.case_id is not None:
                c = cases_by_id.get(m.case_id)
                if c is not None and not timing <= set(c.review_reasons or []):
                    bad("ingest.specimen_timing_flag_reaches_case", m.id)
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
    "report.rules_reapplied_no_lost_actionable", "report.hba1c_plausible_or_review", "report.numeric_has_unit_or_review", "observation.interpretation_valid",
    "observation.interpretation_matches_conclusion", "case.tier_valid", "case.open_has_owner",
    "case.open_has_exactly_one_running_step", "case.has_pathway_target", "sla.step_clock_within_pathway_target", "sla.post_care_step_not_instantly_overdue", "case.closed_at_after_created", "case.closed_with_unresolved_review",
    "case.closed_by_human", "case.evidence_closure_has_evidence_id", "case.evidence_exists_and_belongs_to_case",
    "case.no_negative_confirmatory_closure_without_surveillance", "case.authorised_closure_reason_and_note",
    "case.closure_type_valid", "dates.step_after_case_created", "dates.step_ends_after_start",
    "sla.due_not_before_start (clock never runs backwards)", "dates.steps_in_causal_order",
    "dates.service_request_after_case", "dates.appointment_has_referral", "dates.no_appointment_before_referral",
    "dates.careplan_after_case", "dates.care_reached_after_case", "audit.case_created_event",
    "tier.never_lowered_without_audited_clinician_decision", "safety.no_patient_contact_before_clinician_review",
    "safety.deceased_never_contacted", "safety.pregnancy_flagged_for_review", "safety.review_case_has_owner",
    "case.at_most_one_open_case_per_patient_programme", "alert.one_pathway_alert_per_case_per_level",
    "content.no_unapproved_translation_sent", "content.signoffs_by_different_people", "audit.hash_chain_valid",
    "identity.one_open_case_per_emirates_id_programme", "dates.attendance_not_before_appointment",
    "comms.reminders_inside_patient_window", "closure.deceased_reason_requires_deceased_patient",
    "reopen.target_follows_policy", "consent.withdrawal_sends_open_cases_to_review",
    "consent.no_automated_contact_after_withdrawal", "death.open_cases_reviewed_when_recorded", "ingest.status_valid",
    "ingest.processed_message_has_report", "ingest.specimen_timing_flag_reaches_case",
    "comms.sent_message_single_language", "patient.emirates_id_valid_checksum", "patient.birth_year_matches_birth_date",
    "patient.registration_has_registrar", "patient.registered_emirates_id_unique",
    "account.patient_role_linked_to_patient", "account.invite_used_only_while_valid",
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-examples", type=int, default=5)
    a = ap.parse_args()
    from kareshafaa.db import init_db  # noqa: PLC0415
    init_db()  # additive only (new tables / nullable columns), the same safe migration the app runs at start-up
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
