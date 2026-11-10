"""Deterministic, versioned case lifecycle state machine.

Pure module: no DB, no ML. The ML risk score is deliberately NOT an input to any function
here - AI never moves a case between states or closes a case.
"""
from __future__ import annotations

from enum import StrEnum

STATE_MACHINE_VERSION = "1.2.0"  # 1.1: surveillance plan after a negative confirmatory test; 1.2: critical fast lane


class S(StrEnum):
    ISSUED = "issued"
    TRIAGED = "triaged"
    PATIENT_NOTIFIED = "patient_notified"
    ACKNOWLEDGED = "acknowledged"
    CONFIRMATORY_TEST = "confirmatory_test"
    CONSULTATION = "consultation"
    REFERRAL_SENT = "referral_sent"
    REFERRAL_ACCEPTED = "referral_accepted"
    REFERRAL_REJECTED = "referral_rejected"
    APPOINTMENT_BOOKED = "appointment_booked"
    APPOINTMENT_ATTENDED = "appointment_attended"
    TREATMENT_STARTED = "treatment_or_surveillance_started"
    CLOSED = "closed"
    # side states
    UNREACHABLE = "unreachable"
    NO_SHOW = "no_show"
    DECLINED_REVIEW = "declined_pending_review"


MAIN_PATH: list[S] = [
    S.ISSUED, S.TRIAGED, S.PATIENT_NOTIFIED, S.ACKNOWLEDGED, S.CONFIRMATORY_TEST, S.CONSULTATION,
    S.REFERRAL_SENT, S.REFERRAL_ACCEPTED, S.APPOINTMENT_BOOKED, S.APPOINTMENT_ATTENDED,
    S.TREATMENT_STARTED, S.CLOSED,
]

LABELS: dict[str, str] = {
    S.ISSUED: "Result issued", S.TRIAGED: "Triaged", S.PATIENT_NOTIFIED: "Patient notified",
    S.ACKNOWLEDGED: "Acknowledged (teach-back passed)", S.CONFIRMATORY_TEST: "Confirmatory test",
    S.CONSULTATION: "Consultation", S.REFERRAL_SENT: "Referral sent", S.REFERRAL_ACCEPTED: "Referral accepted",
    S.REFERRAL_REJECTED: "Referral rejected", S.APPOINTMENT_BOOKED: "Appointment booked",
    S.APPOINTMENT_ATTENDED: "Appointment attended", S.TREATMENT_STARTED: "Treatment / surveillance started",
    S.CLOSED: "Closed", S.UNREACHABLE: "Unreachable", S.NO_SHOW: "No-show",
    S.DECLINED_REVIEW: "Declined - clinician review",
}

# Non-closure transitions. Closure is handled by CLOSABLE_* below and the closure guard.
TRANSITIONS: dict[S, frozenset[S]] = {
    S.ISSUED: frozenset({S.TRIAGED}),
    S.TRIAGED: frozenset({S.PATIENT_NOTIFIED, S.UNREACHABLE}),
    S.PATIENT_NOTIFIED: frozenset({S.ACKNOWLEDGED, S.UNREACHABLE, S.DECLINED_REVIEW}),
    S.UNREACHABLE: frozenset({S.PATIENT_NOTIFIED}),
    S.ACKNOWLEDGED: frozenset({S.CONFIRMATORY_TEST, S.CONSULTATION, S.DECLINED_REVIEW}),
    S.CONFIRMATORY_TEST: frozenset({S.CONSULTATION, S.TREATMENT_STARTED, S.DECLINED_REVIEW}),
    S.CONSULTATION: frozenset({S.REFERRAL_SENT, S.TREATMENT_STARTED, S.DECLINED_REVIEW}),
    S.REFERRAL_SENT: frozenset({S.REFERRAL_ACCEPTED, S.REFERRAL_REJECTED}),
    S.REFERRAL_REJECTED: frozenset({S.REFERRAL_SENT, S.CONSULTATION}),
    S.REFERRAL_ACCEPTED: frozenset({S.APPOINTMENT_BOOKED}),
    S.APPOINTMENT_BOOKED: frozenset({S.APPOINTMENT_ATTENDED, S.NO_SHOW, S.REFERRAL_ACCEPTED}),  # cancelled -> rebook
    S.NO_SHOW: frozenset({S.APPOINTMENT_BOOKED, S.UNREACHABLE, S.DECLINED_REVIEW}),
    S.APPOINTMENT_ATTENDED: frozenset({S.TREATMENT_STARTED}),
    S.TREATMENT_STARTED: frozenset(),
    S.DECLINED_REVIEW: frozenset({S.CONSULTATION, S.ACKNOWLEDGED}),
    S.CLOSED: frozenset({S.TRIAGED}),  # re-open (authorised) e.g. unreachable patient reappears
}

# Which closure evidence is acceptable from which state.
EVIDENCE_TYPES = ("appointment_fulfilled", "care_plan", "negative_confirmatory_result")
CLOSABLE_WITH_EVIDENCE: dict[S, frozenset[str]] = {
    S.APPOINTMENT_ATTENDED: frozenset({"appointment_fulfilled", "care_plan"}),
    S.TREATMENT_STARTED: frozenset({"care_plan", "appointment_fulfilled"}),
    S.CONFIRMATORY_TEST: frozenset({"negative_confirmatory_result"}),
}

# A negative confirmatory test may close the case only where the guideline returns the person to routine
# screening (FIT repeat negative; breast assessment normal). Cervical (HPV+ with negative triage -> 1-year
# repeat, ASCCP) and diabetes (repeat below threshold -> retest/lifestyle, ADA) need a surveillance care plan.
NEGATIVE_CONFIRMATION_CLOSES = frozenset({"colorectal_fit", "breast"})

AUTHORISED_CLOSURE_REASONS: dict[str, str] = {
    "informed_decline": "Patient made an informed decision to decline care (documented)",
    "unable_to_contact": "Unable to contact after the full contact ladder",
    "deceased": "Patient deceased",
    "moved_abroad": "Patient moved abroad; summary provided",
    "care_elsewhere_verified": "Care received elsewhere and verified",
    "clinically_not_indicated": "Further care not clinically indicated (clinician rationale)",
    "duplicate": "Duplicate case merged into another case",
}

# Critical-result fast lane: a critical case is never closed as unreachable (escalation-and-closure, principle 5).
NEVER_FOR_CRITICAL = frozenset({"unable_to_contact"})

TERMINAL = frozenset({S.CLOSED})
ACTIVE_STATES = frozenset(s for s in S if s not in TERMINAL)


class TransitionError(ValueError):
    """Raised when a requested transition is not allowed by the state machine or a guard."""


def allowed_targets(state: str) -> frozenset[S]:
    return TRANSITIONS.get(S(state), frozenset())


def check_transition(src: str, dst: str) -> None:
    s, d = S(src), S(dst)
    if d == S.CLOSED:
        raise TransitionError("use the closure guard (close_case) to close a case")
    if d not in TRANSITIONS.get(s, frozenset()):
        raise TransitionError(f"transition {s.value} -> {d.value} is not allowed")


def check_closure(state: str, closure_type: str, *, evidence_type: str | None, reason: str | None,
                  note: str | None, actor_is_human: bool, actor_can_authorise: bool,
                  programme: str | None = None, tier: str | None = None) -> None:
    """Closure guard. A case may close ONLY with recorded evidence or an authorised human decision."""
    s = S(state)
    if s == S.CLOSED:
        raise TransitionError("case is already closed")
    if not actor_is_human:
        raise TransitionError("only a human user may close a case")
    if closure_type == "evidence":
        allowed = CLOSABLE_WITH_EVIDENCE.get(s)
        if not allowed:
            raise TransitionError(f"no closure evidence is acceptable in state {s.value}")
        if evidence_type not in allowed:
            raise TransitionError(f"evidence '{evidence_type}' is not acceptable in state {s.value}")
        if evidence_type == "negative_confirmatory_result" and programme not in NEGATIVE_CONFIRMATION_CLOSES:
            raise TransitionError("this programme needs a surveillance care plan after a negative confirmatory test")
        return
    if closure_type == "authorised_decision":
        if not actor_can_authorise:
            raise TransitionError("actor is not authorised to sign off closure")
        if reason not in AUTHORISED_CLOSURE_REASONS:
            raise TransitionError("a valid closure reason code is required")
        if reason in NEVER_FOR_CRITICAL and tier == "critical":
            raise TransitionError("a critical result is never closed as 'unable to contact': it keeps escalating "
                                  "(by phone) until a clinician decides")
        if not note or len(note.strip()) < 10:
            raise TransitionError("a closure rationale note (>= 10 characters) is required")
        return
    raise TransitionError("closure_type must be 'evidence' or 'authorised_decision'")
