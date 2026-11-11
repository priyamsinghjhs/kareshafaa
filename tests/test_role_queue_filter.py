"""GP/specialist work queues hide cases with no primary action for that role.

The filter uses the same rule as the case page (“Nothing for your role now” = no primary
action). Coordinator and clinical-lead overviews are unchanged. A hidden case stays
openable by URL and is returned when searching by case number, not by name or MRN.
"""
from __future__ import annotations

from sqlalchemy import select

from kareshafaa.api.actions import available_actions, has_primary_action
from kareshafaa.api.deps import actor_from_user
from kareshafaa.models import Case, Organization, Practitioner, User
from kareshafaa.services.access import case_visible
from kareshafaa.services.queue import VIEWS, _org_scope, work_queue
from kareshafaa.services import workflow as wf
from queue_oracle import work_queue_python

SUMMARY = "Positive screening result; patient informed; please assess for the next step. " * 2
BIG = 100_000


def _actor(db, username):
    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def _in_scope(db, actor):
    cases = list(db.scalars(select(Case).where(Case.state != "closed")))
    orgs = _org_scope(db, [c.id for c in cases])
    return [c for c in cases if actor.org_id in orgs.get(c.id, ()) and case_visible(db, actor, c)]


def _ids(q):
    return {i["id"] for i in q["items"]}


def test_gp_queue_matches_primary_action_and_search_finds_hidden_cases(db, new_case, client, as_user):
    gp = _actor(db, "omar.haddad")
    visible = _in_scope(db, gp)
    idle = [c for c in visible if not has_primary_action(db, gp, c)]
    busy = [c for c in visible if has_primary_action(db, gp, c)]
    if not idle:
        idle = [_referral_waiting_on_specialist(db, gp, new_case=new_case, client=client, as_user=as_user)]
        db.expire_all()
        visible = _in_scope(db, gp)
        idle = [c for c in visible if not has_primary_action(db, gp, c)]
        busy = [c for c in visible if has_primary_action(db, gp, c)]
    assert idle and busy

    q = work_queue(db, gp, "all", limit=BIG)
    assert _ids(q) == {c.id for c in busy}
    assert q["counts"]["all"] == q["total"] == len(q["items"]) == len(busy)
    for v in VIEWS:
        qv = work_queue(db, gp, v, limit=BIG)
        assert qv["counts"][v] == qv["total"] == len(qv["items"])
        assert _ids(qv).isdisjoint({c.id for c in idle})
        assert work_queue_python(db, gp, v, limit=BIG)["counts"] == qv["counts"]

    hidden = idle[0]
    assert hidden.id not in _ids(q)
    # case page unchanged: still no primary button, secondary actions may remain
    assert not any(a.get("primary") for a in available_actions(db, gp, hidden))
    assert not has_primary_action(db, gp, hidden)

    for term in (str(hidden.id), f"#{hidden.id}"):
        found = work_queue(db, gp, "all", limit=BIG, search=term)
        assert hidden.id in _ids(found)
        assert found["counts"]["all"] == found["total"]
        assert work_queue_python(db, gp, "all", limit=BIG, search=term)["items"] == found["items"]

    # name / MRN search does not reveal a case with nothing for this role
    assert hidden.id not in _ids(work_queue(db, gp, "all", limit=BIG, search=hidden.patient.mrn))
    assert hidden.id not in _ids(work_queue(db, gp, "all", limit=BIG, search=hidden.patient.family_name))

    # overdue / mine also hide it; a case-number search lifts only the role filter, not the view
    assert hidden.id not in _ids(work_queue(db, gp, "overdue", limit=BIG))
    assert hidden.id not in _ids(work_queue(db, gp, "mine", limit=BIG))
    found_over = work_queue(db, gp, "overdue", limit=BIG, search=f"#{hidden.id}")
    found_mine = work_queue(db, gp, "mine", limit=BIG, search=f"#{hidden.id}")
    co_over = hidden.id in _ids(work_queue(db, _actor(db, "aisha.rahman"), "overdue", limit=BIG, search=f"#{hidden.id}"))
    if co_over:
        assert hidden.id in _ids(found_over)
    else:
        assert hidden.id not in _ids(found_over)
    if hidden.owner_id == gp.practitioner_id:
        assert hidden.id in _ids(found_mine)
    else:
        assert hidden.id not in _ids(found_mine)

    lead = _actor(db, "fatima.al.mansoori")
    co = _actor(db, "aisha.rahman")
    assert hidden.id in _ids(work_queue(db, lead, "all", limit=BIG))
    assert hidden.id in _ids(work_queue(db, co, "all", limit=BIG))


def _at_gp_org(gp):
    return (Case.managing_org_id == gp.org_id) | (Case.owner_id == gp.practitioner_id)


def _referral_waiting_on_specialist(db, gp, *, new_case=None, client=None, as_user=None):
    """A referral-sent case still in the GP's organisation scope (next step is the receiving specialist)."""
    from sqlalchemy import or_
    hosp = db.scalar(select(Organization).where(Organization.name.like("Capital General%")))
    assert hosp is not None and hosp.id != gp.org_id
    c = db.scalar(select(Case).where(Case.state == "consultation", _at_gp_org(gp)).order_by(Case.id).limit(1))
    if c is None:
        c = db.scalar(select(Case).where(Case.state == "acknowledged", Case.needs_confirmation.is_(False),
                                         _at_gp_org(gp)).order_by(Case.id).limit(1))
        if c is not None:
            wf.transition(db, c, "consultation", gp, note="GP reviewed")
    if c is None:
        assert new_case is not None, "no in-session case to refer and no new_case fixture"
        from test_journey_api import notify_and_ack
        cid = new_case()["id"]
        db.expire_all()
        notify_and_ack(client, as_user, cid)
        r = client.post(f"/api/cases/{cid}/transition", headers=as_user("omar.haddad"),
                        json={"to_state": "consultation", "note": "GP reviewed"})
        assert r.status_code == 200, r.text
        r = client.post(f"/api/cases/{cid}/referral", headers=as_user("omar.haddad"),
                        json={"performer_org_id": hosp.id, "summary": SUMMARY})
        assert r.status_code == 200, r.text
        db.expire_all()
        c = db.get(Case, cid)
    else:
        wf.record_consent(db, c.patient, _actor(db, "aisha.rahman"), "share_external", "permit",
                          note="Patient agreed to share the referral with the hospital.")
        wf.create_referral(db, c, gp, hosp.id, summary=SUMMARY)
    assert c.state == "referral_sent"
    assert not has_primary_action(db, gp, c)
    return c


def test_referral_sent_is_hidden_from_gp_queue_but_visible_to_receiving_specialist(db, new_case, client, as_user):
    gp = _actor(db, "omar.haddad")
    c = _referral_waiting_on_specialist(db, gp, new_case=new_case, client=client, as_user=as_user)
    db.expire_all()
    c = db.get(Case, c.id)
    assert c.id not in _ids(work_queue(db, gp, "all", limit=BIG))
    assert c.id in _ids(work_queue(db, gp, "all", limit=BIG, search=f"#{c.id}"))

    sp = actor_from_user(db.scalar(
        select(User).join(Practitioner, User.practitioner_id == Practitioner.id)
        .where(User.role == "specialist", User.active.is_(True),
               Practitioner.org_id == db.scalar(
                   select(Organization.id).where(Organization.name.like("Capital General%"))))
        .order_by(User.id).limit(1)))
    assert has_primary_action(db, sp, c)
    assert any(a["key"] == "decide_referral" and a.get("primary") for a in available_actions(db, sp, c))
    assert c.id in _ids(work_queue(db, sp, "all", limit=BIG))

    # a specialist at another organisation has no primary action; if the case is in their
    # scope at all it stays hidden (and is still found by case number)
    other = actor_from_user(db.scalar(
        select(User).join(Practitioner, User.practitioner_id == Practitioner.id)
        .where(User.role == "specialist", User.active.is_(True), Practitioner.org_id != sp.org_id)
        .order_by(User.id).limit(1)))
    if c.id in {x.id for x in _in_scope(db, other)}:
        assert not has_primary_action(db, other, c)
        assert c.id not in _ids(work_queue(db, other, "all", limit=BIG))
        assert c.id in _ids(work_queue(db, other, "all", limit=BIG, search=str(c.id)))


def test_specialist_queue_hides_idle_cases_and_agrees_with_oracle(db):
    sp = _actor(db, "vikram.mehta")
    visible = _in_scope(db, sp)
    idle = [c for c in visible if not has_primary_action(db, sp, c)]
    busy = [c for c in visible if has_primary_action(db, sp, c)]
    assert busy
    q = work_queue(db, sp, "all", limit=BIG)
    assert _ids(q) == {c.id for c in busy}
    assert q["counts"]["all"] == len(busy)
    if idle:
        hidden = idle[0]
        assert hidden.id not in _ids(q)
        assert hidden.id in _ids(work_queue(db, sp, "all", limit=BIG, search=f"#{hidden.id}"))
        assert hidden.id not in _ids(work_queue(db, sp, "all", limit=BIG, search=hidden.patient.mrn))
    old = work_queue_python(db, sp, "all", limit=BIG)
    assert old["counts"] == q["counts"] and _ids(old) == _ids(q)
