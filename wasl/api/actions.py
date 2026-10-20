"""Context-aware next actions for a case (drives the UI action panel)."""
from __future__ import annotations

from ..programmes import CLINICIAN_CONFIRMED_TIERS

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import CallTask, Case, ServiceRequest
from ..security import Actor
from ..services.access import consent_status
from ..statemachine import S


def available_actions(db: Session, actor: Actor, c: Case) -> list[dict]:
    st = S(c.state)
    can = actor.can
    acts: list[dict] = []

    def add(key: str, label: str, perm: str | None = None, primary: bool = False, **extra) -> None:
        if perm is None or can(perm):
            acts.append({"key": key, "label": label, "primary": primary, **extra})

    open_call = db.scalar(select(CallTask).where(CallTask.case_id == c.id, CallTask.status == "requested"))
    if c.review_required and st != S.CLOSED:
        add("review", "Resolve clinical review (reason shown above)", "case:triage_urgent", True)
    if st == S.ISSUED and c.review_required:
        pass  # nothing proceeds until a clinician has reviewed the result
    elif st == S.ISSUED:
        clin = c.tier in CLINICIAN_CONFIRMED_TIERS
        add("triage", "Confirm triage" + (" (clinician)" if clin else ""),
            "case:triage_urgent" if clin else "case:triage", True)
    if st in (S.TRIAGED, S.UNREACHABLE):
        add("notify", "Send result notification (channel ladder)", "comm:send", True)
    if st in (S.TRIAGED, S.PATIENT_NOTIFIED, S.UNREACHABLE, S.NO_SHOW) or open_call:
        add("call", "Record phone call outcome", "call:record", st == S.TRIAGED and bool(open_call))
    if st == S.PATIENT_NOTIFIED:
        add("teachback", "Record navigator teach-back", "call:record", True)
        add("reminder", "Send reminder", "comm:send")
    if st in (S.TRIAGED, S.PATIENT_NOTIFIED, S.NO_SHOW):
        add("unreachable", "Mark unreachable (after call ladder)", "call:record")
    if st == S.ACKNOWLEDGED:
        if c.needs_confirmation:
            add("order_confirmatory", "Order confirmatory test", "confirm:order", True)
        else:
            add("consultation", "Record consultation", "case:write", True)
    if st == S.CONFIRMATORY_TEST:
        sr = db.scalar(select(ServiceRequest).where(ServiceRequest.case_id == c.id, ServiceRequest.kind == "confirmatory_test")
                       .order_by(ServiceRequest.id.desc()))
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
            consent_blocked=consent_status(db, c.patient_id, "share_external") == "deny")
    if st == S.REFERRAL_REJECTED:
        add("consultation", "Alternative plan: back to consultation", "case:write")
    if st in (S.CONSULTATION, S.APPOINTMENT_ATTENDED):
        add("start_care", "Record treatment / surveillance plan", "careplan:write", st == S.APPOINTMENT_ATTENDED)
    if st == S.REFERRAL_SENT:
        add("decide_referral", "Accept / reject referral", "referral:decide", True)
    if st in (S.REFERRAL_ACCEPTED, S.NO_SHOW):
        add("book", "Book appointment" if st == S.REFERRAL_ACCEPTED else "Rebook appointment", "appointment:write", True)
    if st == S.APPOINTMENT_BOOKED:
        add("attendance", "Record attendance / no-show", "appointment:write", True)
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
            add("transfer", "Transfer to another provider (two-key)", "case:transfer")
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
