"""Exhaustive state-machine tests: every (from, to) pair and every closure combination.

EXPECTED is written out by hand from docs/clinical_rules.md section 6 so that any edit to the transition table
(or a mutation of it) fails here and must be reviewed."""
from __future__ import annotations

import itertools

import pytest

from wasl.statemachine import (AUTHORISED_CLOSURE_REASONS, S, TransitionError, check_closure, check_transition)

EXPECTED = {
    "issued": {"triaged"},
    "triaged": {"patient_notified", "unreachable"},
    "patient_notified": {"acknowledged", "declined_pending_review", "unreachable"},
    "acknowledged": {"confirmatory_test", "consultation", "declined_pending_review"},
    "confirmatory_test": {"consultation", "declined_pending_review", "treatment_or_surveillance_started"},
    "consultation": {"declined_pending_review", "referral_sent", "treatment_or_surveillance_started"},
    "referral_sent": {"referral_accepted", "referral_rejected"},
    "referral_accepted": {"appointment_booked"},
    "referral_rejected": {"consultation", "referral_sent"},
    "appointment_booked": {"appointment_attended", "no_show"},
    "appointment_attended": {"treatment_or_surveillance_started"},
    "treatment_or_surveillance_started": set(),
    "closed": {"triaged"},
    "unreachable": {"patient_notified"},
    "no_show": {"appointment_booked", "declined_pending_review", "unreachable"},
    "declined_pending_review": {"acknowledged", "consultation"},
}
ALL = [s.value for s in S]
PAIRS = list(itertools.product(ALL, ALL))


def test_expected_covers_every_state():
    assert set(EXPECTED) == set(ALL)


@pytest.mark.parametrize("src,dst", PAIRS, ids=[f"{a}->{b}" for a, b in PAIRS])
def test_transition_pair(src, dst):
    if dst in EXPECTED[src]:
        check_transition(src, dst)
    else:
        with pytest.raises(TransitionError):
            check_transition(src, dst)


def test_closed_is_never_a_transition_target():
    """Closing is only possible through the closure guard (evidence / authorised decision)."""
    for src in ALL:
        with pytest.raises(TransitionError):
            check_transition(src, "closed")


EVIDENCE_OK = {"appointment_attended": {"appointment_fulfilled", "care_plan"},
               "treatment_or_surveillance_started": {"appointment_fulfilled", "care_plan"},
               "confirmatory_test": {"negative_confirmatory_result"}}
NEG_CLOSES = {"colorectal_fit", "breast"}
EVID = ["appointment_fulfilled", "care_plan", "negative_confirmatory_result", None, "bogus"]
PROGS = ["colorectal_fit", "cervical", "breast", "diabetes_hba1c", None]
CLOSURE_CASES = list(itertools.product(ALL, EVID, PROGS))


@pytest.mark.parametrize("state,ev,prog", CLOSURE_CASES)
def test_evidence_closure_matrix(state, ev, prog):
    kw = dict(evidence_type=ev, reason=None, note="closure note", actor_is_human=True, actor_can_authorise=True,
              programme=prog)
    ok = state != "closed" and ev in EVIDENCE_OK.get(state, set()) and \
        (ev != "negative_confirmatory_result" or prog in NEG_CLOSES)
    if ok:
        check_closure(state, "evidence", **kw)
    else:
        with pytest.raises(TransitionError):
            check_closure(state, "evidence", **kw)


AUTH_CASES = list(itertools.product(ALL, list(AUTHORISED_CLOSURE_REASONS) + ["bogus", None], [True, False],
                                    ["", "short", "a rationale of enough length"]))


@pytest.mark.parametrize("state,reason,can_auth,note", AUTH_CASES)
def test_authorised_closure_matrix(state, reason, can_auth, note):
    kw = dict(evidence_type=None, reason=reason, note=note, actor_is_human=True, actor_can_authorise=can_auth)
    ok = state != "closed" and can_auth and reason in AUTHORISED_CLOSURE_REASONS and len(note.strip()) >= 10
    if ok:
        check_closure(state, "authorised_decision", **kw)
    else:
        with pytest.raises(TransitionError):
            check_closure(state, "authorised_decision", **kw)


@pytest.mark.parametrize("state", ALL)
@pytest.mark.parametrize("ctype", ["evidence", "authorised_decision"])
def test_system_actor_never_closes(state, ctype):
    with pytest.raises(TransitionError):
        check_closure(state, ctype, evidence_type="care_plan", reason="deceased", note="long enough note",
                      actor_is_human=False, actor_can_authorise=True, programme="breast")


@pytest.mark.parametrize("ctype", ["", "auto", "ml", None])
def test_unknown_closure_type_rejected(ctype):
    with pytest.raises(TransitionError):
        check_closure("triaged", ctype, evidence_type=None, reason="deceased", note="long enough note",
                      actor_is_human=True, actor_can_authorise=True)
