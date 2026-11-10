"""Who may accept / reject a referral: never the clinician who sent it.

The receiving specialist (own organisation) decides; a clinical lead may decide for oversight unless they sent that
referral. GPs and coordinators send referrals but never decide them. The actions panel and the server use the same
rule (workflow.can_decide_referral), so the button only shows to someone the server will accept it from."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from kareshafaa.api.actions import available_actions
from kareshafaa.models import Case, Organization, Practitioner, User
from kareshafaa.services.errors import Forbidden
from kareshafaa.services import workflow as wf

SUMMARY = "Positive screening result; patient informed; please assess for the next step. " * 2


def _user(db, username):
    from kareshafaa.api.deps import actor_from_user

    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def _specialist(db, *, at_org: int | None = None, not_org: int | None = None):
    from kareshafaa.api.deps import actor_from_user

    q = select(User).join(Practitioner, User.practitioner_id == Practitioner.id).where(
        User.role == "specialist", User.active.is_(True))
    q = q.where(Practitioner.org_id == at_org) if at_org is not None else q.where(Practitioner.org_id != not_org)
    u = db.scalar(q.order_by(User.id).limit(1))
    assert u is not None, "seed has no specialist user for this organisation"
    return actor_from_user(u)


def _keys(db, actor, c):
    return {a["key"] for a in available_actions(db, actor, c)}


def _sent(db, sender_username):
    """A case in consultation, referred to Capital General by `sender_username`."""
    c = db.scalar(select(Case).where(Case.state == "consultation").order_by(Case.id).limit(1))
    assert c is not None, "seed has no case in consultation"
    hosp = db.scalar(select(Organization).where(Organization.name.like("Capital General%")))
    sender = _user(db, sender_username)
    wf.record_consent(db, c.patient, _user(db, "aisha.rahman"), "share_external", "permit",
                      note="Patient agreed to share the referral with the hospital.")
    wf.create_referral(db, c, sender, hosp.id, summary=SUMMARY)
    assert c.state == "referral_sent"
    return c, sender, hosp


def test_clinical_lead_who_sent_referral_cannot_see_or_decide_it(db):
    c, lead, _ = _sent(db, "fatima.al.mansoori")
    assert lead.can("referral:create") and lead.can("referral:decide")
    assert "decide_referral" not in _keys(db, lead, c)
    assert not wf.can_decide_referral(db, c, lead)
    with pytest.raises(Forbidden, match="sent this referral"):
        wf.decide_referral(db, c, lead, True, note="accepting my own referral")
    assert c.state == "referral_sent"


def test_another_clinical_lead_can_decide(db):
    c, _, _ = _sent(db, "fatima.al.mansoori")
    other = _user(db, "layla.al.hashimi")
    assert "decide_referral" in _keys(db, other, c)
    wf.decide_referral(db, c, other, True, note="accepted (oversight)")
    assert c.state == "referral_accepted"


def test_receiving_specialist_can_decide_a_leads_referral(db):
    c, _, hosp = _sent(db, "fatima.al.mansoori")
    sp = _specialist(db, at_org=hosp.id)
    assert "decide_referral" in _keys(db, sp, c)
    wf.decide_referral(db, c, sp, False, reason_code="capacity", note="no slots within six weeks")
    assert c.state == "referral_rejected"


@pytest.mark.parametrize("sender", ["omar.haddad", "aisha.rahman"])
def test_gp_or_coordinator_sender_never_sees_decide_but_receiving_specialist_does(db, sender):
    c, s, hosp = _sent(db, sender)
    assert not s.can("referral:decide")
    assert "decide_referral" not in _keys(db, s, c)
    sp = _specialist(db, at_org=hosp.id)
    assert "decide_referral" in _keys(db, sp, c)
    wf.decide_referral(db, c, sp, True, note="accepted")
    assert c.state == "referral_accepted"


def test_specialist_at_another_organisation_still_cannot_decide(db):
    c, _, hosp = _sent(db, "omar.haddad")
    sp = _specialist(db, not_org=hosp.id)
    assert "decide_referral" not in _keys(db, sp, c)
    with pytest.raises(Forbidden, match="receiving organisation"):
        wf.decide_referral(db, c, sp, True, note="accepted")
    assert c.state == "referral_sent"
