"""Context-aware next actions for a case (drives the UI action panel)."""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from ..programmes import CLINICIAN_CONFIRMED_TIERS

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from ..models import CallTask, Case, Consent, CriticalReadBack, QuestionnaireResponse, ServiceRequest
from ..security import Actor
from ..services.workflow import can_decide_referral
from ..statemachine import S


@dataclass
class _Lookups:
    """Inputs for available_actions, so the work-queue filter can reuse the exact case-page rules."""

    read_back: set[int]
    open_calls: set[int]
    teachbacks: dict[int, list[QuestionnaireResponse]]
    confirmatory: dict[int, ServiceRequest]
    share_denied: set[int]
    decide_ok: set[int]

    @classmethod
    def load(cls, db: Session, actor: Actor, cases: Sequence[Case]) -> _Lookups:
        ids = [c.id for c in cases]
        if not ids:
            return cls(set(), set(), {}, {}, set(), set())
        critical_ids = [c.id for c in cases if c.tier == "critical" and S(c.state) != S.CLOSED]
        have_rb = set()
        if critical_ids:
            have_rb = set(db.scalars(select(CriticalReadBack.case_id).where(
                CriticalReadBack.case_id.in_(critical_ids), CriticalReadBack.read_back_confirmed.is_(True))))
        open_calls = set(db.scalars(select(CallTask.case_id).where(
            CallTask.case_id.in_(ids), CallTask.status == "requested")))
        notified_ids = [c.id for c in cases if S(c.state) == S.PATIENT_NOTIFIED]
        teachbacks: dict[int, list[QuestionnaireResponse]] = defaultdict(list)
        if notified_ids:
            for t in db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id.in_(notified_ids))
                                .order_by(QuestionnaireResponse.created_at.desc(), QuestionnaireResponse.id.desc())):
                teachbacks[t.case_id].append(t)
        conf_ids = [c.id for c in cases if S(c.state) == S.CONFIRMATORY_TEST]
        confirmatory: dict[int, ServiceRequest] = {}
        if conf_ids:
            for sr in db.scalars(select(ServiceRequest).where(
                    ServiceRequest.case_id.in_(conf_ids), ServiceRequest.kind == "confirmatory_test")):
                if sr.case_id not in confirmatory or sr.id > confirmatory[sr.case_id].id:
                    confirmatory[sr.case_id] = sr
        share_ids = [c.patient_id for c in cases if S(c.state) in (S.CONSULTATION, S.REFERRAL_REJECTED)]
        share_denied: set[int] = set()
        if share_ids:
            seen: set[int] = set()
            for row in db.scalars(select(Consent).where(
                    Consent.patient_id.in_(share_ids), Consent.scope == "share_external",
                    Consent.status == "active").order_by(Consent.id.desc())):
                if row.patient_id in seen:
                    continue
                seen.add(row.patient_id)
                if row.decision == "deny":
                    share_denied.add(row.patient_id)
        # same rule as the actions panel and the server (workflow.can_decide_referral)
        decide_ok = {c.id for c in cases if can_decide_referral(db, c, actor)}
        return cls(set(critical_ids) - have_rb, open_calls, dict(teachbacks), confirmatory, share_denied, decide_ok)


def available_actions(db: Session, actor: Actor, c: Case, *, lookups: _Lookups | None = None) -> list[dict]:
    st = S(c.state)
    can = actor.can
    acts: list[dict] = []
    L = lookups or _Lookups.load(db, actor, [c])

    def add(key: str, label: str, perm: str | None = None, primary: bool = False, **extra) -> None:
        if perm is None or can(perm):
            acts.append({"key": key, "label": label, "primary": primary, **extra})

    read_back_needed = c.id in L.read_back
    if read_back_needed:  # critical-result fast lane: before anything else, a clinician has it and read it back
        add("read_back", "Record direct contact and read-back (critical result)", "critical:readback", True)
    open_call = c.id in L.open_calls
    if c.review_required and st != S.CLOSED:
        add("review", "Resolve clinical review", "case:triage_urgent", True)
    if st == S.ISSUED and c.review_required:
        pass  # nothing proceeds until a clinician has reviewed the result
    elif st == S.ISSUED and read_back_needed:
        pass  # triage opens once the read-back is recorded (workflow._guard)
    elif st == S.ISSUED:
        clin = c.tier in CLINICIAN_CONFIRMED_TIERS
        add("triage", "Confirm triage" + (" (clinician)" if clin else ""),
            "case:triage_urgent" if clin else "case:triage", True)
    if st in (S.TRIAGED, S.UNREACHABLE):
        add("notify", "Tell the patient their result", "comm:send", True)
    if st in (S.TRIAGED, S.PATIENT_NOTIFIED, S.UNREACHABLE, S.NO_SHOW) or open_call:
        add("call", "Record phone call outcome", "call:record", st == S.TRIAGED and bool(open_call))
    if st == S.PATIENT_NOTIFIED:
        tbs = L.teachbacks.get(c.id, [])
        if not tbs:
            add("teachback", "Check the patient understood (teach-back)", "call:record", True)
        elif not any(t.passed for t in tbs):  # a passed check is never offered again; a failed one can be retried
            last = tbs[0]
            add("teachback", "Try teach-back again", "call:record", True, retry=True,
                last_teach_back={"passed": last.passed, "score": last.score, "total": last.total,
                                 "at": last.created_at.isoformat() if last.created_at else None, "mode": last.mode,
                                 "attempts": len(tbs)})
        add("reminder", "Send reminder", "comm:send")
    if st in (S.TRIAGED, S.PATIENT_NOTIFIED, S.NO_SHOW):
        add("unreachable", "Mark unreachable (all calls tried)", "call:record")
    if st == S.ACKNOWLEDGED:
        if c.needs_confirmation:
            add("order_confirmatory", "Order confirmatory test", "confirm:order", True)
        else:
            add("consultation", "Record consultation", "case:write", True)
    if st == S.CONFIRMATORY_TEST:
        sr = L.confirmatory.get(c.id)
        if sr and sr.status == "requested":
            add("confirm_result", "Record confirmatory result", "confirm:result", True)
        elif sr and sr.result == "negative":
            from ..statemachine import NEGATIVE_CONFIRMATION_CLOSES

            if c.programme in NEGATIVE_CONFIRMATION_CLOSES:
                add("close_evidence", "Close with evidence (negative result)", "case:write", True,
                    evidence_type="negative_confirmatory_result")
            else:
                add("start_care", "Record surveillance plan (repeat test)", "careplan:write", True)
    if st in (S.CONSULTATION, S.REFERRAL_REJECTED):
        add("refer", "Send referral" if st == S.CONSULTATION else "Re-route referral", "referral:create", True,
            consent_blocked=c.patient_id in L.share_denied)
    if st == S.REFERRAL_REJECTED:
        add("consultation", "Alternative plan: back to consultation", "case:write")
    if st in (S.CONSULTATION, S.APPOINTMENT_ATTENDED):
        add("start_care", "Record treatment / surveillance plan", "careplan:write", st == S.APPOINTMENT_ATTENDED)
    if st == S.REFERRAL_SENT and c.id in L.decide_ok:  # never the clinician who sent it
        add("decide_referral", "Accept / reject referral", "referral:decide", True)
    if st in (S.REFERRAL_ACCEPTED, S.NO_SHOW):
        add("book", "Book appointment" if st == S.REFERRAL_ACCEPTED else "Rebook appointment", "appointment:write", True)
    if st == S.APPOINTMENT_BOOKED:
        add("attendance", "Record attendance / no-show", "appointment:write", True)
        add("reschedule", "Change appointment time", "appointment:write")
        add("cancel_appointment", "Cancel appointment", "appointment:write")
        add("appointment_reminder", "Send appointment reminder", "comm:send")
    if st == S.APPOINTMENT_ATTENDED:
        add("close_evidence", "Close with evidence (attended appointment)", "case:write", evidence_type="appointment_fulfilled")
    if st == S.TREATMENT_STARTED:
        add("close_evidence", "Close with evidence (care plan)", "case:write", True, evidence_type="care_plan")
    if st in (S.PATIENT_NOTIFIED, S.ACKNOWLEDGED, S.CONFIRMATORY_TEST, S.CONSULTATION, S.NO_SHOW):
        add("decline", "Record patient decline", "consent:write")
    if st == S.DECLINED_REVIEW:
        add("reconsider", "Patient reconsidered - resume", "case:write")
    if st != S.CLOSED:
        add("close_authorised", "Authorised closure (reason required)", "closure:authorise")
        add("assign", "Reassign owner", "case:assign")
        if not c.transfer_pending_owner_id:
            add("transfer", "Transfer to another provider", "case:transfer")
        add("consult", "Request clinician consultation", "consult:create")
        add("lesson", "Record delay / incident reason", "lesson:write")
        add("consent", "Record consent", "consent:write")
        if not c.patient.deceased:
            add("patient_deceased", "Record patient death (stops all outreach)", "closure:authorise")
    if c.patient.deceased:
        add("patient_deceased_revoke", "Correct a death recorded in error", "case:reopen")
    if c.transfer_pending_owner_id and (actor.practitioner_id == c.transfer_pending_owner_id or actor.role == "clinical_lead"):
        add("decide_transfer", "Accept / decline incoming transfer", None, True)
    if st == S.CLOSED:
        add("reopen", "Re-open case", "case:reopen")
    if can("fhir:export"):
        add("fhir", "Export FHIR R4 bundle", "fhir:export")
    return acts


def has_primary_action(db: Session, actor: Actor, c: Case, *, lookups: _Lookups | None = None) -> bool:
    """True when the case page would show a primary next-step button, not “Nothing for your role now”."""
    return any(a.get("primary") for a in available_actions(db, actor, c, lookups=lookups))


def primary_action_case_ids(db: Session, actor: Actor, case_ids: Iterable[int]) -> set[int]:
    """Case ids in `case_ids` that have a primary action for this actor (same rule as the case page)."""
    ids = sorted({i for i in case_ids if i is not None})
    if not ids:
        return set()
    cases = list(db.scalars(select(Case).options(joinedload(Case.patient)).where(Case.id.in_(ids))).unique())
    lookups = _Lookups.load(db, actor, cases)
    return {c.id for c in cases if has_primary_action(db, actor, c, lookups=lookups)}
