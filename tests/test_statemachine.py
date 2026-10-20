"""Unit tests: deterministic state machine and the closure guard."""
import pytest

from wasl.statemachine import (AUTHORISED_CLOSURE_REASONS, MAIN_PATH, S, TRANSITIONS, TransitionError,
                               check_closure, check_transition)


def test_main_path_is_connected():
    for a, b in zip(MAIN_PATH, MAIN_PATH[1:]):
        if b == S.CLOSED:
            continue
        # every consecutive main-path step is reachable (directly or via an optional step)
        assert b in TRANSITIONS[a] or any(b in TRANSITIONS[m] for m in TRANSITIONS[a]), (a, b)


def test_every_state_has_transition_entry():
    assert set(TRANSITIONS) == set(S)


@pytest.mark.parametrize("src,dst", [("issued", "appointment_booked"), ("triaged", "referral_accepted"),
                                     ("referral_sent", "treatment_or_surveillance_started"),
                                     ("appointment_booked", "consultation")])
def test_illegal_transitions_rejected(src, dst):
    with pytest.raises(TransitionError):
        check_transition(src, dst)


def test_closed_only_via_guard():
    with pytest.raises(TransitionError, match="closure guard"):
        check_transition("treatment_or_surveillance_started", "closed")


def test_reopen_allowed_from_closed():
    check_transition("closed", "triaged")


KW = dict(evidence_type=None, reason=None, note=None, actor_is_human=True, actor_can_authorise=True)


def test_closure_evidence_must_match_state():
    check_closure("appointment_attended", "evidence", **{**KW, "evidence_type": "appointment_fulfilled"})
    check_closure("confirmatory_test", "evidence", **{**KW, "evidence_type": "negative_confirmatory_result"},
                  programme="breast")
    for prog in ("cervical", "diabetes_hba1c", None):  # surveillance plan required; unknown programme fails closed
        with pytest.raises(TransitionError, match="surveillance"):
            check_closure("confirmatory_test", "evidence", **{**KW, "evidence_type": "negative_confirmatory_result"},
                          programme=prog)
    with pytest.raises(TransitionError):
        check_closure("referral_sent", "evidence", **{**KW, "evidence_type": "care_plan"})
    with pytest.raises(TransitionError):
        check_closure("confirmatory_test", "evidence", **{**KW, "evidence_type": "care_plan"})


def test_closure_system_actor_never_closes():
    with pytest.raises(TransitionError, match="human"):
        check_closure("appointment_attended", "evidence",
                      **{**KW, "evidence_type": "appointment_fulfilled", "actor_is_human": False})


def test_authorised_decision_requires_authority_reason_and_note():
    ok = {**KW, "reason": "moved_abroad", "note": "Patient relocated, summary sent to new GP."}
    check_closure("triaged", "authorised_decision", **ok)
    with pytest.raises(TransitionError, match="authorised"):
        check_closure("triaged", "authorised_decision", **{**ok, "actor_can_authorise": False})
    with pytest.raises(TransitionError, match="reason"):
        check_closure("triaged", "authorised_decision", **{**ok, "reason": "bored"})
    with pytest.raises(TransitionError, match="note"):
        check_closure("triaged", "authorised_decision", **{**ok, "note": "short"})
    with pytest.raises(TransitionError):
        check_closure("closed", "authorised_decision", **ok)
    with pytest.raises(TransitionError):
        check_closure("triaged", "something_else", **ok)


def test_reason_codes_are_documented():
    assert {"informed_decline", "unable_to_contact", "deceased"} <= set(AUTHORISED_CLOSURE_REASONS)
