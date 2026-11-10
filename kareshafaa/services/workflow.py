"""Case workflow: creation, guarded transitions, ownership, referrals, appointments, closure.

Every write goes through these functions so that guards, ownership and audit are always applied.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..policy import POLICY_VERSION
from ..models import (
    Appointment,
    AuditEvent,
    CallTask,
    CarePlan,
    Case,
    Communication,
    Consent,
    ConsultRequest,
    DiagnosticReport,
    Lesson,
    Observation,
    OwnershipEvent,
    Patient,
    Practitioner,
    QuestionnaireResponse,
    ServiceRequest,
    TIER_CHANGE_AUTHORISED,
    StepTask,
    utcnow,
)
from ..clinical_rules import TIER_TARGET_DAYS, target_days
from ..clinical_rules import TIER_RANK, Decision, PatientContext, ResultInput, evaluate, is_downgrade, max_tier, review_min_tier
from ..programmes import CLINICIAN_CONFIRMED_TIERS, PROGRAMMES
from ..security import SYSTEM, Actor
from ..signoff import require_signoff
from ..sla import SlaLookup, due_at
from ..statemachine import S, TransitionError, check_closure, check_transition
from .access import consent_status
from .errors import DomainError, Forbidden, Invalid, NotFound

REJECT_REASONS = {
    "wrong_service": "Wrong service / specialty",
    "capacity": "No capacity in the requested timeframe",
    "incomplete_info": "Incomplete referral information",
    "out_of_network": "Out of network / not covered",
    "not_clinically_indicated": "Not clinically indicated (rationale required)",
}
NO_SHOW_BARRIERS = ("transport", "work", "fear_anxiety", "cost", "language", "forgot", "childcare", "ramadan_hours", "unknown")
DELAY_REASONS = (
    "patient_not_responding", "contact_details_wrong", "language_barrier", "capacity", "referral_incomplete",
    "awaiting_result", "patient_travel", "patient_anxiety", "staff_absence", "system_issue", "other",
)
CONSULT_SLA_HOURS = {"critical": 1, "urgent": 4, "high": 24, "routine": 72}

# Which permission a human needs to move a case INTO a state.
TARGET_PERMISSION: dict[S, str] = {
    S.TRIAGED: "case:triage", S.PATIENT_NOTIFIED: "call:record", S.ACKNOWLEDGED: "case:write",
    S.CONFIRMATORY_TEST: "confirm:order", S.CONSULTATION: "case:write", S.REFERRAL_SENT: "referral:create",
    S.REFERRAL_ACCEPTED: "referral:decide", S.REFERRAL_REJECTED: "referral:decide",
    S.APPOINTMENT_BOOKED: "appointment:write", S.APPOINTMENT_ATTENDED: "appointment:write",
    S.NO_SHOW: "appointment:write", S.TREATMENT_STARTED: "careplan:write", S.UNREACHABLE: "call:record",
    S.DECLINED_REVIEW: "case:write",
}
# States the rules engine (system actor) may enter automatically, based on recorded events.
SYSTEM_ALLOWED = {S.TRIAGED, S.PATIENT_NOTIFIED, S.ACKNOWLEDGED}
# States a patient may move their own case into, via their own actions.
PATIENT_ALLOWED = {S.ACKNOWLEDGED, S.DECLINED_REVIEW}


ATTENDANCE_EARLY_HOURS = 4  # attendance may be recorded up to 4 h before the booked start (early arrival)


def _claim(case: Case) -> None:
    """Mark the case row as changed so the UPDATE carries the row-version check (models.Case.row_version).

    Two people acting on the same case at once: the second commit fails (StaleDataError -> HTTP 409) instead of
    silently applying an action decided on a stale view (e.g. a referral sent on a case that was just closed)."""
    if isinstance(case, Case) and case.id is not None:
        flag_modified(case, "state")


def _now(now: datetime | None) -> datetime:
    return now or utcnow()


def get_case(db: Session, case_id: int) -> Case:
    c = db.get(Case, case_id)
    if c is None:
        raise NotFound(f"case {case_id} not found")
    return c


def current_step(db: Session, case: Case) -> StepTask | None:
    return db.scalar(select(StepTask).where(StepTask.case_id == case.id, StepTask.status == "in-progress"))


# ------------------------------------------------------------------ owners
def programme_coordinator(db: Session, programme: str) -> Practitioner:
    p = db.scalar(select(Practitioner).where(Practitioner.role == "coordinator", Practitioner.programme == programme,
                                             Practitioner.active.is_(True), Practitioner.is_pool.is_(False))
                  .order_by(Practitioner.id).limit(1))
    return p or programme_pool(db, programme)


def programme_pool(db: Session, programme: str) -> Practitioner:
    p = db.scalar(select(Practitioner).where(Practitioner.is_pool.is_(True), Practitioner.programme == programme))
    if p is None:
        raise DomainError(f"no team pool configured for programme {programme}")
    return p


def programme_clinical_lead(db: Session, programme: str) -> Practitioner:
    p = db.scalar(select(Practitioner).where(Practitioner.role == "clinical_lead", Practitioner.active.is_(True),
                                             Practitioner.programme.in_([programme, None]))
                  .order_by(Practitioner.programme.desc(), Practitioner.id))  # oldest lead first (admins can add more)
    if p is None:  # never fail the ingest (that could lose the result): the programme team pool owns it
        return programme_pool(db, programme)
    return p


def programme_director(db: Session) -> Practitioner:
    """Cross-programme medical director (L4 / duty clinician)."""
    p = db.scalar(select(Practitioner).where(Practitioner.role == "clinical_lead", Practitioner.active.is_(True),
                                             Practitioner.programme.is_(None)).order_by(Practitioner.id).limit(1))
    return p or programme_clinical_lead(db, "colorectal_fit")


def _latest(db: Session, model, case_id: int, **filters):
    q = select(model).where(model.case_id == case_id)
    for k, v in filters.items():
        q = q.where(getattr(model, k) == v)
    return db.scalar(q.order_by(model.id.desc()).limit(1))


# When each kind of evidence was recorded (round 19): evidence recorded before the case's last re-open belongs to the
# CLOSED episode and must not start care, mark attendance or close the re-opened case.
_EVIDENCE_TIME = {CarePlan: "created_at", Appointment: "updated_at", ServiceRequest: "decided_at"}


def episode_start(db: Session, case: Case) -> datetime | None:
    """Time of the case's last re-open (closed -> triaged), or None if it was never re-opened."""
    for ts, detail in db.execute(select(AuditEvent.ts, AuditEvent.detail).where(
            AuditEvent.case_id == case.id, AuditEvent.event == "case.transition").order_by(AuditEvent.id.desc())):
        if isinstance(detail, dict) and detail.get("from") == S.CLOSED.value:
            return ts
    return None


def _latest_this_episode(db: Session, model, case: Case, **filters):
    """Like _latest, but ignores evidence recorded before the last re-open (a stale care plan, a fulfilled
    appointment or a negative confirmatory result of the closed episode)."""
    q = select(model).where(model.case_id == case.id)
    for k, v in filters.items():
        q = q.where(getattr(model, k) == v)
    since = episode_start(db, case)
    if since is not None:
        q = q.where(getattr(model, _EVIDENCE_TIME[model]) >= since)
    return db.scalar(q.order_by(model.id.desc()).limit(1))


def default_owner(db: Session, case: Case, state: S) -> Practitioner:
    patient = case.patient
    gp = db.get(Practitioner, patient.gp_id) if patient.gp_id else None
    if state in (S.ISSUED, S.TRIAGED, S.PATIENT_NOTIFIED, S.UNREACHABLE, S.NO_SHOW):
        owner = programme_coordinator(db, case.programme)
    elif state in (S.REFERRAL_ACCEPTED, S.APPOINTMENT_BOOKED, S.APPOINTMENT_ATTENDED, S.TREATMENT_STARTED) and \
            (ref := _latest(db, ServiceRequest, case.id, kind="referral", status="accepted")):
        owner = db.get(Practitioner, ref.performer_id) if ref.performer_id else None
        owner = owner or db.scalar(select(Practitioner).where(Practitioner.org_id == ref.performer_org_id,
                                                              Practitioner.role == "specialist", Practitioner.active.is_(True)))
        owner = owner or gp or programme_pool(db, case.programme)
    elif state == S.REFERRAL_REJECTED and (ref := _latest(db, ServiceRequest, case.id, kind="referral")):
        owner = db.get(Practitioner, ref.requester_id)
    else:
        owner = gp
    if owner is None or not owner.active:
        owner = programme_pool(db, case.programme)
    return owner


# ------------------------------------------------------------------ creation
RECENT_CLOSURE_DAYS = 180  # a result for a programme closed within this window goes to human review


def identity_group(db: Session, patient: Patient) -> list[int]:
    """Patient ids that are probably the same person: the patient plus any record with the same Emirates ID."""
    if not patient.national_id:
        return [patient.id]
    ids = list(db.scalars(select(Patient.id).where(Patient.national_id == patient.national_id)))
    return sorted(set(ids) | {patient.id})


def patient_context(patient: Patient, programme: str, at: datetime) -> PatientContext:
    sc = {x.strip() for x in (patient.specialist_care or "").split(",") if x.strip()}
    return PatientContext(age=(at.year - patient.birth_year) if patient.birth_year else None, sex=patient.gender,
                          pregnant=bool(patient.pregnant), deceased=bool(patient.deceased),
                          under_specialist_care=programme in sc, haemoglobinopathy=bool(patient.haemoglobinopathy))


def flag_review(db: Session, case: Case, reasons: list[str], actor: Actor, now: datetime, *,
                tier: str | None = None, detail: dict | None = None) -> None:
    """Put a case into the human-review queue (idempotent per reason). The tier can only go UP here."""
    new = [r for r in reasons if r not in (case.review_reasons or [])]
    case.review_reasons = list(case.review_reasons or []) + new
    case.review_required = True
    target = max_tier(case.tier, tier, review_min_tier(reasons))
    if target != case.tier:
        old = case.tier
        case.tier = target
        audit.record(db, actor, "U", "case.tier_escalated", entity_type="Task", entity_id=case.id, case_id=case.id,
                     ts=now, detail={"from": old, "to": target, "reasons": reasons})
        tgt = case.created_at + timedelta(days=TIER_TARGET_DAYS[target])
        if case.target_due_at is None or tgt < case.target_due_at:
            case.target_due_at = tgt  # end-to-end target tightened for the more urgent tier, never relaxed
        step = current_step(db, case)
        if step is not None:  # re-time the running clock for the more urgent tier (never later than before)
            hours = SlaLookup.load(db).hours(S(case.state), target)
            if hours is not None:
                due = _capped_due(case, due_at(step.started_at, hours, target), step.started_at)
                if step.due_at is None or due < step.due_at:
                    step.due_at = case.due_at = due
                    step.sla_hours = hours
    audit.record(db, actor, "U", "case.review_flagged", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"reasons": reasons, **(detail or {})})


def ingest_result(db: Session, patient: Patient, programme: str, *, value: float | None, value_text: str | None,
                  performer_org_id: int, unit: str | None = None, test: str | None = None, status: str = "final",
                  cytology: str | None = None, actor: Actor = SYSTEM, now: datetime | None = None,
                  extra_reasons: list[str] | None = None, other_observations: list[list] | None = None,
                  ordering_clinician: str | None = None, screening_site: str | None = None,
                  collected_at: datetime | None = None) -> tuple[DiagnosticReport, Case | None]:
    """Receive a screening result and apply the clinical rules (kareshafaa/clinical_rules.py).

    Fail-safe: nothing is dropped. Negative -> report only. Actionable -> one case. Anything uncertain (bad data,
    patient context, amended/preliminary status, conflict with an open case, result after closure) -> a case in
    the human-review queue at a tier no lower than the most urgent plausible interpretation."""
    refuse_ai(actor, "file a result")
    now = _now(now)
    prog = PROGRAMMES.get(programme)
    ctx = patient_context(patient, programme, now)
    dec = evaluate(ResultInput(programme=programme, value=value, value_text=value_text, unit=unit, test=test,
                               status=status, cytology=cytology), ctx)
    if extra_reasons:  # message-level concerns (e.g. several values for one test): human review, never lower tier
        dec = Decision("review", max_tier(dec.tier, review_min_tier(extra_reasons)),
                       "human_review" if dec.disposition == "negative" else dec.pathway,
                       dec.next_step if dec.disposition != "negative" else "Clinician review of the result",
                       dec.needs_confirmation, dec.target, dec.rule_id, dec.reasons + list(extra_reasons),
                       dec.clinical_tier, dec.normalised)
    if prog is None:  # the API schema prevents this; message ingest routes unknown programmes to its review queue
        raise Invalid(f"unknown programme {programme}")
    rep = DiagnosticReport(patient_id=patient.id, programme=programme, category="screening", code=prog.screening_code,
                           display=prog.screening_display, conclusion={"negative": "negative", "actionable": "actionable",
                                                                       "review": "needs_review"}[dec.disposition],
                           issued_at=now, performer_org_id=performer_org_id, status=(status or "final")[:20],
                           ordering_clinician=(ordering_clinician or None) and ordering_clinician.strip()[:200] or None,
                           screening_site=(screening_site or None) and screening_site.strip()[:200] or None,
                           specimen_collected_at=collected_at)
    stored_text = ((value_text if not cytology else f"{value_text or ''}|{cytology}")[:100]
                   if (value_text or cytology) else None)
    stored_unit = (unit or None) and str(unit)[:30]
    rep.observations.append(Observation(code=prog.screening_code, display=prog.screening_display, value=value,
                                        value_text=stored_text, unit=stored_unit, interpretation=dec.interpretation))
    for code, otest, raw, ounit in other_observations or []:  # every value in the message is kept, never dropped
        try:
            ov = float(raw)
        except (TypeError, ValueError):
            ov = None
        rep.observations.append(Observation(code=str(code)[:40], display=f"{otest} (additional value in same message)"[:200],
                                            value=ov if ov is not None and ov == ov and abs(ov) != float("inf") else None,
                                            value_text=None if ov is not None else (str(raw or "")[:100] or None),
                                            unit=(ounit or None) and str(ounit)[:30], interpretation="A"))
    db.add(rep)
    db.flush()
    audit.record(db, actor, "C", "result.received", entity_type="DiagnosticReport", entity_id=rep.id, ts=now,
                 detail={"programme": programme, "disposition": dec.disposition, "tier": dec.tier,
                         "rule": dec.rule_id, "reasons": dec.reasons, "policy_version": POLICY_VERSION})
    from . import recall as _recall  # the person's open recall for this programme is now completed (new episode)
    _recall.on_new_result(db, patient, rep, now)
    group = identity_group(db, patient)
    open_case = db.scalar(select(Case).where(Case.patient_id.in_(group), Case.programme == programme,
                                             Case.state != S.CLOSED).order_by(Case.id.desc()).limit(1))
    if open_case is not None:
        # never create a second open case; never silently absorb a new result either
        rep.case_id = open_case.id
        reasons = list(dec.reasons)
        if open_case.patient_id != patient.id:  # same Emirates ID, other record: one person, two MRNs
            reasons.append("possible_duplicate_identity")
        same = db.scalar(select(func.count()).select_from(Observation).join(DiagnosticReport).where(
            DiagnosticReport.case_id == open_case.id, DiagnosticReport.id != rep.id,
            Observation.value.is_(None) if value is None else Observation.value == value,
            # compare with what was STORED (a cervical co-test is kept as "HPV|cytology") and with the unit:
            # 48 % and 48 mmol/mol are different results, and an identical co-test re-send is a duplicate, not a
            # "conflict" that sends the reviewer looking for a discrepancy that does not exist (round 20, bug C)
            Observation.value_text.is_(None) if not stored_text else Observation.value_text == stored_text,
            Observation.unit.is_(None) if not stored_unit else Observation.unit == stored_unit))
        reasons.append("duplicate_result" if (same and dec.disposition != "negative") else "conflicting_result")
        if dec.tier and max_tier(dec.tier, open_case.tier) == dec.tier and dec.tier != open_case.tier:
            reasons.append("more_urgent_result")
        flag_review(db, open_case, reasons, actor, now, tier=dec.tier, detail={"report_id": rep.id, "rule": dec.rule_id})
        audit.record(db, actor, "U", "case.linked_result", entity_type="Task", entity_id=open_case.id,
                     case_id=open_case.id, ts=now, detail={"report_id": rep.id, "disposition": dec.disposition})
        _recall.link_case(db, rep, open_case)
        return rep, open_case
    if not dec.creates_case:
        if rep.conclusion == "negative":
            _recall.after_negative(db, patient, rep, actor, now)
        return rep, None
    reasons = list(dec.reasons)
    if len(group) > 1:
        reasons.append("possible_duplicate_identity")
    recent = db.scalar(select(Case.id).where(Case.patient_id.in_(group), Case.programme == programme,
                                             Case.state == S.CLOSED, Case.closed_at >= now - timedelta(days=RECENT_CLOSURE_DAYS))
                       .order_by(Case.id.desc()).limit(1))
    if recent:
        reasons.append("result_after_closure")
    tier = max_tier(dec.tier, review_min_tier(reasons) if reasons else None) or "urgent"
    review = bool(reasons) or dec.disposition == "review"
    owner = programme_clinical_lead(db, programme) if review else programme_coordinator(db, programme)
    case = Case(patient_id=patient.id, report_id=rep.id, programme=programme, tier=tier, state=S.ISSUED,
                owner_id=owner.id, managing_org_id=patient.managing_org_id, needs_confirmation=dec.needs_confirmation,
                created_at=now, state_entered_at=now, rule_id=dec.rule_id, pathway=dec.pathway,
                target_text=(dec.target or "")[:200] or None, review_required=review, review_reasons=reasons or None,
                target_due_at=now + timedelta(days=target_days(dec.pathway, dec.rule_id, tier, dec.clinical_tier)))
    case.patient = patient
    db.add(case)
    db.flush()
    rep.case_id = case.id
    _open_step(db, case, S.ISSUED, case.owner_id, actor, now, note=("human review: " + ", ".join(reasons)) if review else None)
    audit.record(db, actor, "C", "case.created", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"programme": programme, "tier": tier, "owner_id": case.owner_id, "rule": dec.rule_id,
                         "review": review, "reasons": reasons, "previous_closed_case": recent})
    _recall.link_case(db, rep, case)
    return rep, case


def open_case_for_orphan_report(db: Session, rep: DiagnosticReport, actor: Actor, now: datetime | None = None) -> Case:
    """Reconciliation found an actionable / review result with no case (services/reconcile.py): put it into the loop
    as a clinician-review case owned by the programme clinical lead (reason `missing_from_loop`), or link it to the
    patient's open case for that programme and flag that case for review. Never closes or downgrades anything."""
    now = _now(now)
    if not actor.is_human or actor.role == "ml_service":
        raise Forbidden("a person must put a reconciled result into the loop")
    if rep.case_id is not None and db.get(Case, rep.case_id) is not None:
        raise Invalid("this result already has a case")
    patient = db.get(Patient, rep.patient_id)
    group = identity_group(db, patient)
    rec = db.scalar(select(AuditEvent.detail).where(AuditEvent.event == "result.received",
                                                    AuditEvent.entity_type == "DiagnosticReport",
                                                    AuditEvent.entity_id == rep.id).limit(1))
    seen_tier = (rec or {}).get("tier") if isinstance(rec, dict) else None
    reasons = ["missing_from_loop"]
    tier = max_tier(seen_tier if seen_tier in TIER_TARGET_DAYS else None, review_min_tier(reasons)) or "urgent"
    open_case = db.scalar(select(Case).where(Case.patient_id.in_(group), Case.programme == rep.programme,
                                             Case.state != S.CLOSED).order_by(Case.id.desc()).limit(1))
    if open_case is not None:
        _claim(open_case)
        rep.case_id = open_case.id
        flag_review(db, open_case, reasons, actor, now, tier=tier, detail={"report_id": rep.id})
        audit.record(db, actor, "U", "case.linked_result", entity_type="Task", entity_id=open_case.id,
                     case_id=open_case.id, ts=now, detail={"report_id": rep.id, "via": "reconciliation"})
        return open_case
    owner = programme_clinical_lead(db, rep.programme)
    case = Case(patient_id=patient.id, report_id=rep.id, programme=rep.programme, tier=tier, state=S.ISSUED,
                owner_id=owner.id, managing_org_id=patient.managing_org_id, needs_confirmation=True, created_at=now,
                state_entered_at=now, rule_id=None, pathway="human_review", review_required=True,
                review_reasons=reasons, target_due_at=now + timedelta(days=TIER_TARGET_DAYS[tier]))
    case.patient = patient
    db.add(case)
    db.flush()
    rep.case_id = case.id
    _open_step(db, case, S.ISSUED, case.owner_id, actor, now, note="human review: missing_from_loop (reconciliation)")
    audit.record(db, actor, "C", "case.created", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"programme": rep.programme, "tier": tier, "owner_id": owner.id, "review": True,
                         "reasons": reasons, "via": "reconciliation", "report_id": rep.id})
    return case


def resolve_review(db: Session, case: Case, actor: Actor, *, note: str, tier: str | None = None,
                   now: datetime | None = None) -> Case:
    """An authorised clinician clears the human-review flag. Lowering the tier needs a rationale and is audited."""
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if not actor.is_human or not actor.can("case:triage_urgent"):
        raise Forbidden("only a clinician (GP, specialist or clinical lead) may resolve a clinical review")
    require_signoff(actor, "case.review.resolve", note)
    if not case.review_required:
        raise Invalid("this case is not awaiting review")
    if not note or len(note.strip()) < 10:
        raise Invalid("a review note (>= 10 characters) is required")
    if case.patient.deceased:
        raise Invalid("patient is recorded as deceased: close the case with an authorised decision instead")
    old = case.tier
    if tier and tier != case.tier:
        if tier not in TIER_RANK:
            raise Invalid("unknown tier")
        token = TIER_CHANGE_AUTHORISED.set(True)
        try:
            case.tier = tier
        finally:
            TIER_CHANGE_AUTHORISED.reset(token)
    case.review_required = False
    case.review_log = list(case.review_log or []) + [{"at": now.isoformat(), "by": actor.display, "note": note.strip(),
                                                     "reasons": case.review_reasons or [], "tier_from": old, "tier_to": case.tier}]
    audit.record(db, actor, "U", "case.review_resolved", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"reasons": case.review_reasons, "tier_from": old, "tier_to": case.tier, "note": note,
                         "downgrade": is_downgrade(old, case.tier)})
    if S(case.state) == S.ISSUED:
        transition(db, case, S.TRIAGED, actor, note=f"clinician review: {note}", now=now)
    return case


def _capped_due(case: Case, due: datetime | None, start: datetime, state: S | None = None) -> datetime | None:
    """No step clock may run past the end-to-end pathway target (never earlier than the step start).
    `state` = the state of the step being opened: when a CLOSED case is re-opened, case.state is still CLOSED at
    this point, which used to skip the cap (round 20: the re-opened step's clock ran past the kept target)."""
    if case.target_due_at is None or (state or case.state) == S.CLOSED \
            or getattr(case, "care_reached_at", None) is not None:
        return due
    cap = max(case.target_due_at, start)
    return cap if due is None or due > cap else due


def _open_step(db: Session, case: Case, state: S, owner_id: int, actor: Actor, now: datetime, note: str | None = None) -> StepTask:
    sla = SlaLookup.load(db)
    hours = sla.hours(state, case.tier) if state != S.CLOSED else None
    from ..alert_policy import opened_already_breached

    inherited = state != S.CLOSED and opened_already_breached(case.target_due_at, case.care_reached_at, now)
    step = StepTask(case_id=case.id, state=state, owner_id=owner_id, status="in-progress", started_at=now,
                    due_at=_capped_due(case, due_at(now, hours, case.tier), now, state), sla_hours=hours,
                    actor_user_id=actor.user_id, note=note, inherited_breach=inherited, breached=inherited)
    db.add(step)
    case.state = state
    case.state_entered_at = now
    case.due_at = step.due_at
    case.owner_id = owner_id
    case.escalation_level = 0
    db.flush()
    return step


def _close_step(db: Session, case: Case, now: datetime) -> None:
    step = current_step(db, case)
    if step:
        step.status = "completed"
        step.ended_at = now
        if step.due_at and now > step.due_at and not step.breached and not step.inherited_breach:
            step.breached = True
            from .escalation import BREACH_REASON

            add_lesson(db, case, "overdue", BREACH_REASON.get(step.state, "sla_breach"), SYSTEM,
                       detail=f"step '{step.state}' completed after its SLA ({step.sla_hours}h)", now=now)
        db.flush()


# ------------------------------------------------------------------ guards
def _guard(db: Session, case: Case, dst: S) -> None:
    cfg = get_settings()
    src = S(case.state)

    def need(cond: bool, msg: str) -> None:
        if not cond:
            raise TransitionError(msg)

    if dst == S.TRIAGED and src == S.ISSUED and case.tier == "critical":
        from .critical import latest as _read_back
        need(_read_back(db, case.id) is not None,
             "critical result: record the direct clinician contact and read-back first (fast lane), then triage")
    elif dst == S.PATIENT_NOTIFIED:
        need(db.scalar(select(func.count()).select_from(Communication).where(
            Communication.case_id == case.id, Communication.category == "result_notification",
            Communication.status == "completed")) > 0,
            "patient_notified requires a delivered result notification (delivery receipt or completed call)")
    elif dst == S.ACKNOWLEDGED:
        need(db.scalar(select(func.count()).select_from(QuestionnaireResponse).where(
            QuestionnaireResponse.case_id == case.id, QuestionnaireResponse.passed.is_(True))) > 0,
            "acknowledged requires a passed teach-back check")
    elif dst == S.CONFIRMATORY_TEST:
        need(bool(_latest(db, ServiceRequest, case.id, kind="confirmatory_test", status="requested")),
             "confirmatory_test requires an ordered confirmatory test (ServiceRequest)")
    elif dst == S.CONSULTATION:
        if src == S.ACKNOWLEDGED:
            need(not case.needs_confirmation, "this pathway requires a confirmatory test before consultation")
        if src == S.CONFIRMATORY_TEST:
            sr = _latest(db, ServiceRequest, case.id, kind="confirmatory_test")
            need(bool(sr and sr.result == "positive"), "consultation requires a positive confirmatory result")
    elif dst == S.REFERRAL_SENT:
        need(bool(_latest(db, ServiceRequest, case.id, kind="referral", status="requested")),
             "referral_sent requires an active referral (ServiceRequest)")
    elif dst in (S.REFERRAL_ACCEPTED, S.REFERRAL_REJECTED):
        want = "accepted" if dst == S.REFERRAL_ACCEPTED else "rejected"
        ref = _latest(db, ServiceRequest, case.id, kind="referral")
        need(bool(ref and ref.status == want), f"{dst.value} requires the referral to be {want}")
        if src == S.APPOINTMENT_BOOKED:
            need(_latest(db, Appointment, case.id, status="booked") is None,
                 "finish the current booking change first (with a reason)")
    elif dst == S.APPOINTMENT_BOOKED:
        need(bool(_latest(db, Appointment, case.id, status="booked")), "appointment_booked requires a booked appointment")
    elif dst == S.APPOINTMENT_ATTENDED:
        need(bool(_latest_this_episode(db, Appointment, case, status="fulfilled")),
             "appointment_attended requires a fulfilled appointment (recorded since the last re-open)")
    elif dst == S.NO_SHOW:
        need(bool(_latest_this_episode(db, Appointment, case, status="noshow")),
             "no_show requires an appointment marked no-show (since the last re-open)")
    elif dst == S.TREATMENT_STARTED:
        need(bool(_latest_this_episode(db, CarePlan, case, status="active")),
             "treatment/surveillance requires a care plan recorded since the last re-open (careplan.start sign-off)")
    elif dst == S.UNREACHABLE:
        failed_calls = db.scalar(select(func.count()).select_from(CallTask).where(
            CallTask.case_id == case.id, CallTask.status == "completed", CallTask.outcome != "reached"))
        need(failed_calls >= cfg.phone_attempts_before_unreachable,
             f"unreachable requires the contact ladder: >= {cfg.phone_attempts_before_unreachable} failed phone attempts")
    elif dst == S.DECLINED_REVIEW:
        need(consent_status(db, case.patient_id, "care_followup", case.id) == "deny",
             "declined_pending_review requires a recorded decline (Consent)")


def refuse_ai(actor: Actor, what: str) -> None:
    """The outreach-priority model is advisory only: it may never act on a case or a patient."""
    if actor.role == "ml_service":
        raise Forbidden(f"the outreach-priority model is advisory only and may not {what}")


def transition(db: Session, case: Case, dst: S | str, actor: Actor, *, owner_id: int | None = None,
               note: str | None = None, now: datetime | None = None) -> Case:
    refuse_ai(actor, "move a case")
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    dst = S(dst)
    src = S(case.state)
    check_transition(src, dst)
    # authorisation
    if src == S.CLOSED:
        require_signoff(actor, "case.reopen", note)
        # Round 20 (edge cohort): re-opening while another episode for the same person and programme is open
        # (e.g. a new result arrived after closure) hit the one-open-case database constraint as an unexplained
        # 409/IntegrityError. One accountable open episode per person and programme: say where to continue.
        other = db.scalar(select(Case.id).where(Case.patient_id.in_(identity_group(db, case.patient)),
                                                Case.programme == case.programme, Case.state != S.CLOSED,
                                                Case.id != case.id).limit(1))
        if other is not None:
            raise Invalid(f"case #{other} is already open for this patient and programme: continue the follow-up "
                          "there (the closed episode stays closed)")
    if case.review_required and src == S.ISSUED:
        raise Forbidden("this result is awaiting clinician review (resolve_review) before any pathway step")
    if src == S.CLOSED:
        pass
    elif not actor.is_human:
        if case.review_required:
            raise Forbidden("automated actors may not progress a case awaiting clinician review")
        if dst not in SYSTEM_ALLOWED:
            raise Forbidden(f"automated actors may not move a case to {dst.value}")
        if dst == S.TRIAGED and case.tier in CLINICIAN_CONFIRMED_TIERS:
            raise Forbidden("urgent cases must be triaged by a clinician")
    elif actor.role == "patient":
        if actor.patient_id != case.patient_id or dst not in PATIENT_ALLOWED:
            raise Forbidden("patients may only acknowledge or decline their own case")
    else:
        perm = "appointment:write" if (src, dst) == (S.APPOINTMENT_BOOKED, S.REFERRAL_ACCEPTED) else TARGET_PERMISSION[dst]
        if not actor.can(perm):
            raise Forbidden(f"role {actor.role} lacks permission {perm}")
        if dst == S.TRIAGED and case.tier in CLINICIAN_CONFIRMED_TIERS and not actor.can("case:triage_urgent"):
            raise Forbidden("urgent cases must be triaged (confirmed) by a clinician")
    if src != S.CLOSED:
        _guard(db, case, dst)
    if owner_id is not None:
        owner = db.get(Practitioner, owner_id)
        if owner is None or not owner.active:
            raise Invalid("owner must be an active practitioner")
    else:
        owner = default_owner(db, case, dst)
    _close_step(db, case, now)
    if src == S.CLOSED:
        for f in ("closed_at", "closure_type", "closure_reason", "closure_note", "closure_evidence_type",
                  "closure_evidence_id", "closed_by_user_id"):
            setattr(case, f, None)
        # Re-opened episode and its pathway target - CLINICAL LEAD TO CONFIRM (docs/clinical_rules.md).
        # Default (reopen_restarts_target=False, the safer choice): keep the ORIGINAL target. A case re-opened after
        # its target is born breached (inherited_breach) and escalates straight away: more alerts, not fewer, and a
        # booking beyond the target needs a written reason. Option True: a fresh target for the tier from re-opening.
        old_target = case.target_due_at
        if get_settings().reopen_restarts_target:
            case.target_due_at = now + timedelta(days=target_days(case.pathway, case.rule_id, case.tier, case.tier))
            audit.record(db, actor, "U", "case.target_restarted", entity_type="Task", entity_id=case.id,
                         case_id=case.id, ts=now, detail={"from": old_target.isoformat() if old_target else None,
                                                          "to": case.target_due_at.isoformat(), "reason": "re-opened",
                                                          "policy": "reopen_restarts_target"})
        elif old_target is not None and old_target <= now:
            audit.record(db, actor, "U", "case.reopened_past_target", entity_type="Task", entity_id=case.id,
                         case_id=case.id, ts=now, detail={"target": old_target.isoformat(),
                                                          "note": "original pathway target kept: breached on re-opening"})
    if dst == S.TREATMENT_STARTED and case.care_reached_at is None:
        case.care_reached_at = now  # BEFORE the step opens: the pathway target is met, so no target cap applies
    _open_step(db, case, dst, owner.id, actor, now, note)
    audit.record(db, actor, "U", "case.transition", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"from": src.value, "to": dst.value, "owner_id": owner.id, "note": note})
    return case


# ------------------------------------------------------------------ closure guard
def close_case(db: Session, case: Case, actor: Actor, closure_type: str, *, evidence_type: str | None = None,
               evidence_id: int | None = None, reason: str | None = None, note: str | None = None,
               now: datetime | None = None) -> Case:
    """Close ONLY with linked evidence or an authorised human decision with reason (enforced here)."""
    refuse_ai(actor, "close a case")
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if actor.is_human and actor.role not in ("patient",) and not actor.can("case:write"):
        raise Forbidden("role may not close cases")
    if actor.role == "patient":
        raise Forbidden("patients cannot close cases")
    if actor.role != "patient":
        require_signoff(actor, "case.close.authorised" if closure_type == "authorised_decision" else "case.close.evidence",
                        note)
    if case.review_required and not (closure_type == "authorised_decision" and actor.is_human
                                     and actor.can("case:triage_urgent")):
        raise Forbidden("this case has an unresolved clinical review flag: a clinician must resolve it "
                        "(or close it with an authorised decision) first")
    try:
        check_closure(case.state, closure_type, evidence_type=evidence_type, reason=reason, note=note,
                      actor_is_human=actor.is_human, actor_can_authorise=actor.can("closure:authorise"),
                      programme=case.programme, tier=case.tier)
    except TransitionError:
        raise  # the request-level audit middleware records the denied attempt
    if closure_type == "evidence":
        evidence_id = _verify_evidence(db, case, evidence_type, evidence_id)  # type: ignore[arg-type]
    else:
        if reason == "informed_decline" and consent_status(db, case.patient_id, "care_followup", case.id) != "deny":
            raise TransitionError("informed_decline requires a documented decline (Consent) for this case")
        if reason == "deceased" and not case.patient.deceased:
            raise TransitionError("closure reason 'deceased' requires the death to be recorded for the patient first "
                                  "(record_death: stops all outreach and reviews every open case)")
        if reason == "unable_to_contact":
            failed = db.scalar(select(func.count()).select_from(CallTask).where(
                CallTask.case_id == case.id, CallTask.status == "completed", CallTask.outcome != "reached"))
            if failed < get_settings().phone_attempts_before_unreachable:
                raise TransitionError("unable_to_contact requires the full contact ladder to be exhausted")
    src = case.state
    if case.review_required:  # closed by an authorised clinician decision: the review is resolved by that decision
        case.review_required = False
        audit.record(db, actor, "U", "case.review_resolved", entity_type="Task", entity_id=case.id, case_id=case.id,
                     ts=now, detail={"reasons": case.review_reasons, "by_closure": reason, "note": note})
    _close_step(db, case, now)
    case.state = S.CLOSED
    case.state_entered_at = now
    case.due_at = None
    case.closed_at = now
    case.closure_type = closure_type
    case.closure_reason = reason if closure_type == "authorised_decision" else evidence_type
    case.closure_note = note
    case.closure_evidence_type = evidence_type if closure_type == "evidence" else None
    case.closure_evidence_id = evidence_id if closure_type == "evidence" else None
    case.closed_by_user_id = actor.user_id
    if closure_type == "evidence" and case.care_reached_at is None:
        case.care_reached_at = now
    # closed record keeps a completed terminal step for the timeline
    db.add(StepTask(case_id=case.id, state=S.CLOSED, owner_id=case.owner_id, status="completed", started_at=now,
                    ended_at=now, actor_user_id=actor.user_id, note=note))
    db.flush()
    audit.record(db, actor, "U", "case.closed", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"from": src, "closure_type": closure_type, "reason": case.closure_reason,
                         "evidence_id": evidence_id})
    from . import recall as _recall  # next screening due date from the programme interval (clinical_rules, CONFIRM)
    _recall.after_closure(db, case, actor, now)
    from . import gp_summary as _gps  # closure summary to the registered GP (acknowledged from their inbox)
    _gps.write(db, case, "case_closed", actor, now)
    return case


def _verify_evidence(db: Session, case: Case, evidence_type: str, evidence_id: int | None) -> int:
    if evidence_type == "appointment_fulfilled":
        q = select(Appointment).where(Appointment.case_id == case.id, Appointment.status == "fulfilled")
        model = Appointment
    elif evidence_type == "care_plan":
        q = select(CarePlan).where(CarePlan.case_id == case.id, CarePlan.status == "active")
        model = CarePlan
    elif evidence_type == "negative_confirmatory_result":
        q = select(ServiceRequest).where(ServiceRequest.case_id == case.id, ServiceRequest.kind == "confirmatory_test",
                                         ServiceRequest.result == "negative")
        model = ServiceRequest
    else:
        raise TransitionError("unknown evidence type")
    if evidence_id is not None:
        q = q.where(model.id == evidence_id)
    since = episode_start(db, case)
    if since is not None:  # evidence of the closed episode cannot close the re-opened case (round 19)
        q = q.where(getattr(model, _EVIDENCE_TIME[model]) >= since)
    row = db.scalar(q.order_by(model.id.desc()).limit(1))
    if row is None:
        raise TransitionError(f"no valid '{evidence_type}' evidence is linked to this case"
                              + (" since it was re-opened" if since is not None else ""))
    return row.id


# ------------------------------------------------------------------ ownership
def assign(db: Session, case: Case, new_owner_id: int, actor: Actor, reason: str, now: datetime | None = None) -> Case:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "case.assign", reason)
    if case.state == S.CLOSED:
        raise DomainError("cannot reassign a closed case")
    new = db.get(Practitioner, new_owner_id)
    if new is None or not new.active:
        raise Invalid("new owner must be an active practitioner")
    old = case.owner_id
    case.owner_id = new.id
    step = current_step(db, case)
    if step:
        step.owner_id = new.id
    db.add(OwnershipEvent(case_id=case.id, from_owner_id=old, to_owner_id=new.id, kind="reassign", reason=reason,
                          at=now, by_user_id=actor.user_id))
    audit.record(db, actor, "U", "case.reassigned", entity_type="Task", entity_id=case.id, case_id=case.id, ts=now,
                 detail={"from_owner": old, "to_owner": new.id, "reason": reason})
    return case


def request_transfer(db: Session, case: Case, to_owner_id: int, actor: Actor, note: str, now: datetime | None = None) -> Case:
    """Two-key transfer: the current owner stays accountable until the receiver accepts."""
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "case.transfer.request", note)
    if case.state == S.CLOSED:
        raise DomainError("cannot transfer a closed case")
    if case.transfer_pending_owner_id:
        raise DomainError("a transfer is already pending")
    to = db.get(Practitioner, to_owner_id)
    if to is None or not to.active:
        raise Invalid("receiver must be an active practitioner")
    if to.org_id != case.patient.managing_org_id and consent_status(db, case.patient_id, "share_external") == "deny":
        raise Forbidden("patient has not consented to sharing with other organisations")
    case.transfer_pending_owner_id = to.id
    case.transfer_note = note
    db.add(OwnershipEvent(case_id=case.id, from_owner_id=case.owner_id, to_owner_id=to.id, kind="transfer_requested",
                          reason=note, at=now, by_user_id=actor.user_id))
    audit.record(db, actor, "U", "case.transfer_requested", entity_type="Task", entity_id=case.id, case_id=case.id,
                 ts=now, detail={"to_owner": to.id, "to_org": to.org_id})
    return case


def decide_transfer(db: Session, case: Case, actor: Actor, accept: bool, note: str | None = None,
                    now: datetime | None = None) -> Case:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "case.transfer.decide", note)
    if not accept and (not note or len(note.strip()) < 5):
        raise Invalid("declining a transfer needs a reason (>= 5 characters)")
    pend = case.transfer_pending_owner_id
    if not pend:
        raise DomainError("no transfer pending")
    to = db.get(Practitioner, pend)
    assert to is not None
    if not (actor.practitioner_id == pend or (actor.org_id == to.org_id and actor.can("case:assign"))
            or actor.role == "clinical_lead"):
        raise Forbidden("only the receiving organisation can accept or decline a transfer")
    old = case.owner_id
    if accept:
        case.owner_id = to.id
        case.managing_org_id = to.org_id if to.org.kind == "primary_care" else case.managing_org_id
        if to.org.kind == "primary_care" and to.role == "gp":
            case.patient.managing_org_id = to.org_id
            case.patient.gp_id = to.id
        step = current_step(db, case)
        if step:
            step.owner_id = to.id
    case.transfer_pending_owner_id = None
    db.add(OwnershipEvent(case_id=case.id, from_owner_id=old, to_owner_id=to.id,
                          kind="transfer_accepted" if accept else "transfer_declined", reason=note, at=now,
                          by_user_id=actor.user_id))
    audit.record(db, actor, "U", "case.transfer_" + ("accepted" if accept else "declined"), entity_type="Task",
                 entity_id=case.id, case_id=case.id, ts=now, detail={"from_owner": old, "to_owner": to.id})
    return case


# ------------------------------------------------------------------ clinical steps
def order_confirmatory(db: Session, case: Case, actor: Actor, performer_org_id: int, now: datetime | None = None) -> ServiceRequest:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if not actor.can("confirm:order"):
        raise Forbidden("role lacks confirm:order")
    prog = PROGRAMMES[case.programme]
    if not case.needs_confirmation:
        raise DomainError("this result's pathway has no confirmatory test step (direct referral/consultation)")
    if S(case.state) != S.ACKNOWLEDGED:
        raise TransitionError("confirmatory test can be ordered once the patient has acknowledged")
    sr = ServiceRequest(case_id=case.id, kind="confirmatory_test", code=prog.confirmatory_test or "confirmatory",
                        requester_id=actor.practitioner_id or case.owner_id, performer_org_id=performer_org_id,
                        priority=case.tier, status="requested", created_at=now)
    db.add(sr)
    db.flush()
    audit.record(db, actor, "C", "servicerequest.created", entity_type="ServiceRequest", entity_id=sr.id,
                 case_id=case.id, ts=now, detail={"kind": "confirmatory_test"})
    transition(db, case, S.CONFIRMATORY_TEST, actor, now=now)
    return sr


def record_confirmatory_result(db: Session, case: Case, actor: Actor, result: str, now: datetime | None = None) -> ServiceRequest:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "confirmatory.result")
    if result not in ("positive", "negative"):
        raise Invalid("result must be positive or negative")
    sr = _latest(db, ServiceRequest, case.id, kind="confirmatory_test", status="requested")
    if sr is None or S(case.state) != S.CONFIRMATORY_TEST:
        raise DomainError("no confirmatory test awaiting a result")
    sr.status = "completed"
    sr.result = result
    sr.decided_at = now
    rep = DiagnosticReport(patient_id=case.patient_id, case_id=case.id, programme=case.programme, category="confirmatory",
                           code="confirmatory", display=sr.code, conclusion=result, issued_at=now,
                           performer_org_id=sr.performer_org_id)
    db.add(rep)
    db.flush()
    audit.record(db, actor, "C", "confirmatory.resulted", entity_type="DiagnosticReport", entity_id=rep.id,
                 case_id=case.id, ts=now, detail={"result": result})
    if result == "positive":
        transition(db, case, S.CONSULTATION, actor, now=now)
    return sr


def create_referral(db: Session, case: Case, actor: Actor, performer_org_id: int, *, performer_id: int | None = None,
                    summary: str = "", now: datetime | None = None) -> ServiceRequest:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if not actor.can("referral:create"):
        raise Forbidden("role lacks referral:create")
    if S(case.state) not in (S.CONSULTATION, S.REFERRAL_REJECTED):
        raise TransitionError("a referral can be sent from consultation or after a rejected referral")
    if performer_org_id != case.patient.managing_org_id and \
            consent_status(db, case.patient_id, "share_external") == "deny":
        raise Forbidden("patient has not consented to sharing with other organisations; record consent first")
    from .configstore import get_config

    checklist = get_config(db, f"referral_checklist:{performer_org_id}")
    if checklist and len((summary or "").strip()) < int(checklist.get("min_summary_chars", 40)):
        raise Invalid(f"this provider requires a structured clinical summary (>= {checklist.get('min_summary_chars', 40)} "
                      "characters) - approved referral-checklist rule")
    prog = PROGRAMMES[case.programme]
    sr = ServiceRequest(case_id=case.id, kind="referral", code=prog.referral_service or "specialist",
                        requester_id=actor.practitioner_id or case.owner_id, performer_org_id=performer_org_id,
                        performer_id=performer_id, priority=case.tier, status="requested",
                        clinical_summary=summary, created_at=now)
    db.add(sr)
    db.flush()
    audit.record(db, actor, "C", "referral.sent", entity_type="ServiceRequest", entity_id=sr.id, case_id=case.id,
                 ts=now, detail={"performer_org_id": performer_org_id})
    transition(db, case, S.REFERRAL_SENT, actor, now=now)
    return sr


def decide_referral(db: Session, case: Case, actor: Actor, accept: bool, *, reason_code: str | None = None,
                    note: str | None = None, performer_id: int | None = None, now: datetime | None = None) -> ServiceRequest:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "referral.decide", note)
    sr = _latest(db, ServiceRequest, case.id, kind="referral", status="requested")
    if sr is None or S(case.state) != S.REFERRAL_SENT:
        raise DomainError("no referral awaiting a decision")
    if actor.role == "specialist" and actor.org_id != sr.performer_org_id:
        raise Forbidden("only the receiving organisation can decide this referral")
    if not accept:
        if reason_code not in REJECT_REASONS:
            raise Invalid(f"reject reason_code must be one of {sorted(REJECT_REASONS)}")
        if not note or len(note.strip()) < 5:
            raise Invalid("a note explaining the rejection is required")
    sr.status = "accepted" if accept else "rejected"
    sr.reason_code = reason_code
    sr.reason_note = note
    sr.decided_at = now
    if accept:
        sr.performer_id = performer_id or sr.performer_id or actor.practitioner_id
    audit.record(db, actor, "U", "referral." + sr.status, entity_type="ServiceRequest", entity_id=sr.id,
                 case_id=case.id, ts=now, detail={"reason_code": reason_code})
    if not accept:
        add_lesson(db, case, "rejected_referral", reason_code or "unknown", actor, detail=note,
                   target_org_id=sr.performer_org_id, now=now)
    transition(db, case, S.REFERRAL_ACCEPTED if accept else S.REFERRAL_REJECTED, actor, now=now)
    return sr


MAX_BOOKING_AHEAD_DAYS = 365  # a booking further ahead is almost always a date typo


# why an appointment was cancelled or moved (barrier code required when the patient asked: what got in the way)
APPOINTMENT_CHANGE_REASONS = {
    "patient_request": "Patient asked to change it",
    "clinic_cancelled": "Clinic cancelled / slot no longer available",
    "earlier_slot": "Earlier slot found",
    "clinical_reason": "Clinical reason",
    "booking_error": "Booked in error",
}


def _check_start(case: Case, start: datetime, now: datetime, beyond_target_reason: str | None) -> bool:
    if start < now - timedelta(days=1):
        raise Invalid("appointment start must not be in the past")
    if start > now + timedelta(days=MAX_BOOKING_AHEAD_DAYS):
        raise Invalid(f"appointment is more than {MAX_BOOKING_AHEAD_DAYS} days ahead: check the date (year typo?)")
    beyond = case.target_due_at is not None and start > case.target_due_at
    if beyond and (not beyond_target_reason or len(beyond_target_reason.strip()) < 10):
        from ..calendar_uae import local_display

        target = local_display(case.target_due_at)[:10]  # UAE calendar date, as the booking form shows it
        if case.target_due_at < now:  # the target has gone: booking earlier is impossible, only the reason is missing
            raise Invalid(f"the pathway target ({target}) has already passed, so this appointment is after it: give the "
                          "reason (>= 10 characters, e.g. earliest slot, patient's choice)")
        raise Invalid(f"appointment is after the pathway target ({target}): book "
                      "earlier, or give the reason (>= 10 characters, e.g. earliest slot, patient's choice)")
    return beyond


def _check_change(reason_code: str, barrier_code: str | None, note: str | None) -> None:
    if reason_code not in APPOINTMENT_CHANGE_REASONS:
        raise Invalid(f"reason must be one of {list(APPOINTMENT_CHANGE_REASONS)}")
    if reason_code == "patient_request" and not barrier_code:
        raise Invalid("when the patient asked for the change, record what got in the way (barrier)")
    if barrier_code is not None and barrier_code not in NO_SHOW_BARRIERS:
        raise Invalid(f"barrier_code must be one of {NO_SHOW_BARRIERS}")
    if reason_code in ("clinical_reason", "booking_error") and len((note or "").strip()) < 5:
        raise Invalid("add a short note (at least 5 characters) for this reason")


def _cancel_appt(db: Session, case: Case, appt: Appointment, actor: Actor, reason_code: str, barrier_code: str | None,
                 note: str | None, now: datetime, event: str) -> None:
    appt.status, appt.cancelled_at, appt.cancelled_by = "cancelled", now, actor.display
    appt.cancel_reason, appt.cancel_note, appt.updated_at = reason_code, (note or "").strip() or None, now
    from .patient_loop import close_open_requests  # the patient's "please change it" request is now dealt with
    close_open_requests(db, appt, actor, "appointment moved" if event == "appointment.rescheduled" else "appointment cancelled", now)
    if reason_code == "patient_request":
        appt.barrier_code = barrier_code
        add_lesson(db, case, "appointment_change", barrier_code or "unknown", actor, now=now,
                   detail=f"patient asked to {'move' if event == 'appointment.rescheduled' else 'cancel'} the appointment"
                          + (f": {appt.cancel_note}" if appt.cancel_note else ""))


def reschedule_appointment(db: Session, case: Case, actor: Actor, start: datetime, reason_code: str, *,
                           barrier_code: str | None = None, note: str | None = None,
                           beyond_target_reason: str | None = None, now: datetime | None = None) -> Appointment:
    """Move the booked appointment: the old one is kept as cancelled (with the reason), a new one is booked.
    The case stays at 'appointment booked' (same step owner and clock)."""
    _claim(case)
    now = _now(now)
    if not actor.can("appointment:write"):
        raise Forbidden("role lacks appointment:write")
    old = _latest(db, Appointment, case.id, status="booked")
    if old is None or S(case.state) != S.APPOINTMENT_BOOKED:
        raise DomainError("no booked appointment to reschedule")
    _check_change(reason_code, barrier_code, note)
    if abs((start - old.start).total_seconds()) < 60:
        raise Invalid("the new time is the same as the booked one")
    beyond = _check_start(case, start, now, beyond_target_reason)
    _cancel_appt(db, case, old, actor, reason_code, barrier_code, note, now, "appointment.rescheduled")
    new = Appointment(case_id=case.id, service_request_id=old.service_request_id, org_id=old.org_id,
                      practitioner_id=old.practitioner_id, start=start, status="booked", created_at=now,
                      booking_note=(beyond_target_reason or "").strip() or None, rescheduled_from_id=old.id)
    db.add(new)
    db.flush()
    audit.record(db, actor, "U", "appointment.rescheduled", entity_type="Appointment", entity_id=new.id, case_id=case.id,
                 ts=now, detail={"from_appointment": old.id, "from": old.start.isoformat(), "to": start.isoformat(),
                                 "reason": reason_code, "barrier": barrier_code, "note": old.cancel_note,
                                 "beyond_target": beyond})
    return new


def cancel_appointment(db: Session, case: Case, actor: Actor, reason_code: str, *, barrier_code: str | None = None,
                       note: str | None = None, now: datetime | None = None) -> Appointment:
    """Cancel without a new date: the case goes back to 'clinic accepted' so the next booking is the next step
    (the step clock restarts and escalates if nobody rebooks)."""
    _claim(case)
    now = _now(now)
    if not actor.can("appointment:write"):
        raise Forbidden("role lacks appointment:write")
    appt = _latest(db, Appointment, case.id, status="booked")
    if appt is None or S(case.state) != S.APPOINTMENT_BOOKED:
        raise DomainError("no booked appointment to cancel")
    _check_change(reason_code, barrier_code, note)
    _cancel_appt(db, case, appt, actor, reason_code, barrier_code, note, now, "appointment.cancelled")
    db.flush()
    audit.record(db, actor, "U", "appointment.cancelled", entity_type="Appointment", entity_id=appt.id, case_id=case.id,
                 ts=now, detail={"start": appt.start.isoformat(), "reason": reason_code, "barrier": barrier_code,
                                 "note": appt.cancel_note})
    transition(db, case, S.REFERRAL_ACCEPTED, actor, note="appointment cancelled: rebook", now=now)
    return appt



def book_appointment(db: Session, case: Case, actor: Actor, start: datetime, *, org_id: int | None = None,
                     practitioner_id: int | None = None, beyond_target_reason: str | None = None,
                     now: datetime | None = None) -> Appointment:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if not actor.can("appointment:write"):
        raise Forbidden("role lacks appointment:write")
    if S(case.state) not in (S.REFERRAL_ACCEPTED, S.NO_SHOW):
        raise TransitionError("an appointment can be booked after referral acceptance or a no-show")
    ref = _latest(db, ServiceRequest, case.id, kind="referral", status="accepted")
    if ref is None:
        raise DomainError("no accepted referral")
    beyond = _check_start(case, start, now, beyond_target_reason)
    appt = Appointment(case_id=case.id, service_request_id=ref.id, org_id=org_id or ref.performer_org_id,
                       practitioner_id=practitioner_id or ref.performer_id, start=start, status="booked", created_at=now,
                       booking_note=(beyond_target_reason or "").strip() or None)
    db.add(appt)
    db.flush()
    audit.record(db, actor, "C", "appointment.booked", entity_type="Appointment", entity_id=appt.id, case_id=case.id,
                 ts=now, detail={"start": start.isoformat(), "beyond_target": beyond,
                                 **({"beyond_target_reason": beyond_target_reason} if beyond_target_reason else {})})
    transition(db, case, S.APPOINTMENT_BOOKED, actor, now=now)
    return appt


def record_attendance(db: Session, case: Case, actor: Actor, attended: bool, *, barrier_code: str | None = None,
                      now: datetime | None = None) -> Appointment:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if not actor.can("appointment:write"):
        raise Forbidden("role lacks appointment:write")
    appt = _latest(db, Appointment, case.id, status="booked")
    if appt is None or S(case.state) != S.APPOINTMENT_BOOKED:
        raise DomainError("no booked appointment")
    if barrier_code is not None and barrier_code not in NO_SHOW_BARRIERS:
        raise Invalid(f"barrier_code must be one of {NO_SHOW_BARRIERS}")
    # clock sanity: a no-show cannot be recorded before the appointment time, nor attendance more than
    # ATTENDANCE_EARLY_HOURS before it (wrong case selected, or a skewed client/server clock)
    if not attended and now < appt.start:
        raise Invalid("a no-show cannot be recorded before the appointment time "
                      f"({appt.start.isoformat(timespec='minutes')} UTC)")
    if attended and now < appt.start - timedelta(hours=ATTENDANCE_EARLY_HOURS):
        raise Invalid("attendance cannot be recorded this long before the appointment time "
                      f"({appt.start.isoformat(timespec='minutes')} UTC)")
    appt.status = "fulfilled" if attended else "noshow"
    appt.barrier_code = None if attended else (barrier_code or "unknown")
    appt.updated_at = now
    from .patient_loop import close_open_requests

    close_open_requests(db, appt, actor, "attended" if attended else "no-show recorded", now)
    audit.record(db, actor, "U", "appointment." + appt.status, entity_type="Appointment", entity_id=appt.id,
                 case_id=case.id, ts=now, detail={"barrier": appt.barrier_code})
    if attended:
        transition(db, case, S.APPOINTMENT_ATTENDED, actor, now=now)
    else:
        add_lesson(db, case, "no_show", appt.barrier_code or "unknown", actor, now=now)
        transition(db, case, S.NO_SHOW, actor, now=now)
    return appt


def start_care(db: Session, case: Case, actor: Actor, kind: str, description: str, now: datetime | None = None,
               review_due_on: date | None = None) -> CarePlan:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    require_signoff(actor, "careplan.start", description)
    if kind not in ("treatment", "surveillance"):
        raise Invalid("kind must be treatment or surveillance")
    st = S(case.state)
    if st == S.CONFIRMATORY_TEST:
        if kind != "surveillance" or not _latest_this_episode(db, ServiceRequest, case, kind="confirmatory_test",
                                                               result="negative"):
            raise TransitionError("from a confirmatory test only a surveillance plan after a negative result may start")
    elif st not in (S.APPOINTMENT_ATTENDED, S.CONSULTATION):
        raise TransitionError("care can start after an attended appointment or a primary-care consultation")
    if not description or len(description.strip()) < 5:
        raise Invalid("a care plan description is required")
    if kind == "surveillance":
        from ..clinical_rules import SURVEILLANCE_MONTHS
        from .recall import add_months
        if review_due_on is None:
            review_due_on = add_months(now.date(), SURVEILLANCE_MONTHS.get(case.programme, 12))
        elif review_due_on <= now.date() or review_due_on > add_months(now.date(), 120):
            raise Invalid("the surveillance review date must be after today and within 10 years")
    elif review_due_on is not None:
        raise Invalid("a review date applies to surveillance plans only (treatment follow-up is set by the treating team)")
    cp = CarePlan(case_id=case.id, kind=kind, description=description,
                  recorded_by_id=actor.practitioner_id or case.owner_id, created_at=now, review_due_on=review_due_on)
    db.add(cp)
    db.flush()
    audit.record(db, actor, "C", "careplan.created", entity_type="CarePlan", entity_id=cp.id, case_id=case.id, ts=now,
                 detail={"kind": kind, "review_due_on": review_due_on.isoformat() if review_due_on else None})
    transition(db, case, S.TREATMENT_STARTED, actor, now=now)
    from . import gp_summary as _gps
    _gps.write(db, case, "care_started", actor, now)
    return cp


def record_decline(db: Session, case: Case, actor: Actor, note: str, now: datetime | None = None) -> Consent:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if actor.role == "patient":
        if actor.patient_id != case.patient_id:
            raise Forbidden("not your case")
    else:  # recorded ON BEHALF of the patient: a named person, with what the patient said (round 19)
        require_signoff(actor, "case.decline.record", note)
    if S(case.state) not in (S.PATIENT_NOTIFIED, S.ACKNOWLEDGED, S.CONFIRMATORY_TEST, S.CONSULTATION, S.NO_SHOW):
        raise TransitionError(f"decline cannot be recorded in state {case.state}")
    c = Consent(patient_id=case.patient_id, case_id=case.id, scope="care_followup", decision="deny",
                recorded_by_user_id=actor.user_id, recorded_at=now, note=note)
    db.add(c)
    db.flush()
    audit.record(db, actor, "C", "consent.decline_recorded", entity_type="Consent", entity_id=c.id, case_id=case.id,
                 ts=now)
    add_lesson(db, case, "declined", "patient_declined", actor, detail=note, now=now)
    transition(db, case, S.DECLINED_REVIEW, actor, now=now)
    return c


def reconsider(db: Session, case: Case, actor: Actor, note: str, now: datetime | None = None) -> Case:
    """Patient changed their mind after a decline: withdraw the decline and resume at consultation."""
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if S(case.state) != S.DECLINED_REVIEW:
        raise TransitionError("case is not in declined review")
    for c in db.scalars(select(Consent).where(Consent.case_id == case.id, Consent.scope == "care_followup",
                                              Consent.status == "active")):
        c.status = "inactive"
    passed = db.scalar(select(func.count()).select_from(QuestionnaireResponse).where(
        QuestionnaireResponse.case_id == case.id, QuestionnaireResponse.passed.is_(True)))
    dst = S.CONSULTATION if passed else S.ACKNOWLEDGED
    if dst == S.ACKNOWLEDGED:
        raise TransitionError("patient must pass the teach-back check before resuming")
    return transition(db, case, dst, actor, note=note, now=now)


def record_death(db: Session, patient: Patient, actor: Actor, note: str, now: datetime | None = None) -> list[int]:
    """A named clinician records that the patient has died (e.g. from the death registry or the family).

    All automated and manual patient messaging stops (comms.service refuses), queued reminders and open call tasks
    are cancelled, and every open case goes to clinician review (reason `deceased`) to be closed with an authorised
    decision - never closed automatically. Returns the ids of the open cases flagged."""
    require_signoff(actor, "patient.deceased.record", note)
    now = _now(now)
    if patient.deceased:
        raise Invalid("patient is already recorded as deceased")
    patient.deceased = True
    patient.deceased_note, patient.deceased_recorded_at, patient.deceased_recorded_by = note, now, actor.display
    cases = list(db.scalars(select(Case).where(Case.patient_id == patient.id, Case.state != S.CLOSED)))
    ids = [c.id for c in cases]
    calls = list(db.scalars(select(CallTask).where(CallTask.case_id.in_(ids), CallTask.status == "requested"))) if ids else []
    for t in calls:
        t.status, t.completed_at = "cancelled", now
    held = list(db.scalars(select(Communication).where(Communication.patient_id == patient.id,
                                                       Communication.status == "on-hold")))
    for h in held:
        h.status = "stopped"
    audit.record(db, actor, "U", "patient.deceased_recorded", entity_type="Patient", entity_id=patient.id, ts=now,
                 detail={"note": note, "open_cases": ids, "call_tasks_cancelled": len(calls),
                         "queued_messages_stopped": len(held)})
    for c in cases:
        _claim(c)
        flag_review(db, c, ["deceased"], actor, now, detail={"note": note})
    return ids


def revoke_death(db: Session, patient: Patient, actor: Actor, note: str, now: datetime | None = None) -> dict:
    """Correct a death recorded in error (clinical lead, rationale >= 20 chars, audited). Nothing restarts silently:
    open cases are re-flagged for clinician review and cases closed as 'deceased' are listed for re-opening."""
    require_signoff(actor, "patient.deceased.revoke", note)
    now = _now(now)
    if not patient.deceased:
        raise Invalid("patient is not recorded as deceased")
    patient.deceased = False
    patient.deceased_corrected_note, patient.deceased_corrected_at, patient.deceased_corrected_by = note, now, actor.display
    open_cases = list(db.scalars(select(Case).where(Case.patient_id == patient.id, Case.state != S.CLOSED)))
    closed = list(db.scalars(select(Case.id).where(Case.patient_id == patient.id, Case.state == S.CLOSED,
                                                   Case.closure_reason == "deceased")))
    audit.record(db, actor, "U", "patient.deceased_revoked", entity_type="Patient", entity_id=patient.id, ts=now,
                 detail={"note": note, "open_cases": [c.id for c in open_cases], "closed_as_deceased": closed})
    for c in open_cases:
        _claim(c)
        flag_review(db, c, ["death_record_corrected"], actor, now, detail={"note": note})
    return {"open_cases": [c.id for c in open_cases], "closed_as_deceased": closed}


def record_consent(db: Session, patient: Patient, actor: Actor, scope: str, decision: str, note: str | None = None,
                   now: datetime | None = None) -> Consent:
    now = _now(now)
    if actor.role == "patient":
        if actor.patient_id != patient.id:
            raise Forbidden("not your record")
    else:  # recorded ON BEHALF of the patient: a named person, with how the patient expressed it (round 19)
        require_signoff(actor, "consent.record", note)
    if decision not in ("permit", "deny"):
        raise Invalid("decision must be permit or deny")
    allowed = {"care_coordination", "share_external", "comm_app", "comm_sms", "comm_whatsapp", "comm_phone",
               "analytics", "alt_contact", "proxy_contact"}
    if scope not in allowed:
        raise Invalid(f"scope must be one of {sorted(allowed)}")
    for c in db.scalars(select(Consent).where(Consent.patient_id == patient.id, Consent.scope == scope,
                                              Consent.status == "active", Consent.case_id.is_(None))):
        c.status = "inactive"
    c = Consent(patient_id=patient.id, scope=scope, decision=decision, recorded_by_user_id=actor.user_id,
                recorded_at=now, note=note)
    db.add(c)
    db.flush()
    audit.record(db, actor, "C", "consent.recorded", entity_type="Consent", entity_id=c.id, ts=now,
                 detail={"scope": scope, "decision": decision, "patient_id": patient.id})
    if scope == "care_coordination" and decision == "deny":
        # automated outreach stops (comms.service); every open case goes to a clinician, who documents an informed
        # decline or agrees another route - a withdrawal must never silently strand a positive result
        for oc in db.scalars(select(Case).where(Case.patient_id == patient.id, Case.state != S.CLOSED)):
            _claim(oc)
            flag_review(db, oc, ["consent_withdrawn"], actor, now, detail={"consent_id": c.id})
    return c


# ------------------------------------------------------------------ consultation requests
def create_consult(db: Session, case: Case, actor: Actor, question: str, *, target_practitioner_id: int | None = None,
                   urgency: str | None = None, now: datetime | None = None) -> ConsultRequest:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    if actor.role == "patient":
        if actor.patient_id != case.patient_id:
            raise Forbidden("not your case")
        # the patient's own GP if active; otherwise the case owner if a clinician; otherwise the clinical lead.
        # A patient's question must never be rejected because their GP has left.
        target_practitioner_id = None
        for pid_ in (case.patient.gp_id, case.owner_id):
            cand = db.get(Practitioner, pid_) if pid_ else None
            if cand and cand.active and cand.role in ("gp", "specialist", "clinical_lead"):
                target_practitioner_id = cand.id
                break
        if target_practitioner_id is None:
            target_practitioner_id = programme_clinical_lead(db, case.programme).id
        rtype = "patient"
    else:
        if not actor.can("consult:create"):
            raise Forbidden("role lacks consult:create")
        if target_practitioner_id is None:
            raise Invalid("target_practitioner_id is required")
        rtype = "practitioner"
    tgt = db.get(Practitioner, target_practitioner_id)
    if tgt is None or not tgt.active or tgt.role not in ("gp", "specialist", "clinical_lead"):
        raise Invalid("target must be an active clinician")
    if not question or len(question.strip()) < 5:
        raise Invalid("question is required")
    urgency = urgency or case.tier
    if urgency not in CONSULT_SLA_HOURS:
        raise Invalid("urgency must be urgent, high or routine")
    cr = ConsultRequest(case_id=case.id, requester_type=rtype, requester_user_id=actor.user_id,
                        target_practitioner_id=tgt.id, question=question.strip()[:4000], urgency=urgency,
                        created_at=now, due_at=now + timedelta(hours=CONSULT_SLA_HOURS[urgency]))
    db.add(cr)
    db.flush()
    audit.record(db, actor, "C", "consult.requested", entity_type="CommunicationRequest", entity_id=cr.id,
                 case_id=case.id, ts=now, detail={"type": rtype, "target": tgt.id, "urgency": urgency})
    return cr


def respond_consult(db: Session, cr: ConsultRequest, actor: Actor, response: str, now: datetime | None = None) -> ConsultRequest:
    now = _now(now)
    if not actor.can("consult:respond"):
        raise Forbidden("role lacks consult:respond")
    if actor.practitioner_id != cr.target_practitioner_id and actor.role != "clinical_lead":
        raise Forbidden("only the addressed clinician (or clinical lead) can respond")
    if cr.status != "requested":
        raise DomainError("consult already answered")
    if not response or len(response.strip()) < 2:
        raise Invalid("response is required")
    cr.status = "answered"
    cr.response = response.strip()[:4000]
    cr.responded_by_user_id = actor.user_id
    cr.responded_at = now
    audit.record(db, actor, "U", "consult.answered", entity_type="CommunicationRequest", entity_id=cr.id,
                 case_id=cr.case_id, ts=now)
    return cr


# ------------------------------------------------------------------ lessons
def add_lesson(db: Session, case: Case | None, kind: str, reason_code: str, actor: Actor, *, detail: str | None = None,
               channel: str | None = None, target_org_id: int | None = None, now: datetime | None = None) -> Lesson:
    _claim(case)  # optimistic lock: a concurrent change to this case fails with 409
    now = _now(now)
    les = Lesson(case_id=case.id if case else None, kind=kind, reason_code=reason_code,
                 state=case.state if case else None, programme=case.programme if case else None,
                 tier=case.tier if case else None, language=case.patient.language if case else None,
                 region=case.patient.region if case else None, channel=channel, target_org_id=target_org_id,
                 detail=detail, created_at=now, created_by_user_id=actor.user_id)
    db.add(les)
    db.flush()
    audit.record(db, actor, "C", "lesson.recorded", entity_type="Lesson", entity_id=les.id,
                 case_id=case.id if case else None, ts=now, detail={"kind": kind, "reason_code": reason_code})
    return les
