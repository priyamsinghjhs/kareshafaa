"""GP, specialist and coordinator work queues hide cases with no primary action for that role.

The filter uses the same rule as the case page (“Nothing for your role now” = no primary
action). The clinical-lead overview is unchanged. A hidden case stays openable by URL
and is returned when searching by case number, not by name or MRN.
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
    # Coordinator queues use the same action filter: a referral waiting on a specialist
    # is hidden unless the coordinator has a primary step (they do not: they cannot decide it).
    if has_primary_action(db, co, hidden):
        assert hidden.id in _ids(work_queue(db, co, "all", limit=BIG))
    else:
        assert hidden.id not in _ids(work_queue(db, co, "all", limit=BIG))
        assert hidden.id in _ids(work_queue(db, co, "all", limit=BIG, search=f"#{hidden.id}"))


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


def _open(db):
    return list(db.scalars(select(Case).where(Case.state != "closed")))


def test_coordinator_queue_hides_idle_cases_and_counts_match(db):
    """A coordinator's work queue is an action list: nothing for her role leaves every view and tile.

    Clinical lead still sees those cases. Searching by case number reveals one; a name or MRN search
    does not. The No owner tile only counts unassigned cases she can act on.
    """
    co = _actor(db, "aisha.rahman")
    lead = _actor(db, "fatima.al.mansoori")
    open_cases = _open(db)
    idle = [c for c in open_cases if not has_primary_action(db, co, c)]
    busy = [c for c in open_cases if has_primary_action(db, co, c)]
    assert idle and busy

    q = work_queue(db, co, "all", limit=BIG)
    assert _ids(q) == {c.id for c in busy}
    assert q["counts"]["all"] == q["total"] == len(q["items"]) == len(busy)
    for v in VIEWS:
        qv = work_queue(db, co, v, limit=BIG)
        assert qv["counts"][v] == qv["total"] == len(qv["items"])
        assert _ids(qv).isdisjoint({c.id for c in idle})
        assert work_queue_python(db, co, v, limit=BIG)["counts"] == qv["counts"]

    hidden = idle[0]
    assert hidden.id not in _ids(q)
    assert not has_primary_action(db, co, hidden)
    assert hidden.id in _ids(work_queue(db, co, "all", limit=BIG, search=str(hidden.id)))
    assert hidden.id in _ids(work_queue(db, co, "all", limit=BIG, search=f"#{hidden.id}"))
    assert hidden.id not in _ids(work_queue(db, co, "all", limit=BIG, search=hidden.patient.mrn))
    assert hidden.id not in _ids(work_queue(db, co, "all", limit=BIG, search=hidden.patient.family_name))

    # clinical-lead overview is unchanged: every open case, including idle ones
    lead_q = work_queue(db, lead, "all", limit=BIG)
    assert hidden.id in _ids(lead_q)
    assert lead_q["counts"]["all"] == len(open_cases)


def test_coordinator_no_owner_tile_excludes_idle_pool_cases(db):
    """A confirmatory-test case on the team pool (Shamma's situation): coordinator cannot record
    the result, so it leaves her queue and her No owner count, but stays on the clinical-lead overview."""
    from kareshafaa.services.workflow import programme_pool

    co = _actor(db, "aisha.rahman")
    lead = _actor(db, "fatima.al.mansoori")
    c = db.scalar(select(Case).where(Case.state == "confirmatory_test").order_by(Case.id).limit(1))
    assert c is not None
    pool = programme_pool(db, c.programme)
    c.owner_id = pool.id
    db.flush()
    db.expire(c, ["owner"])
    assert c.owner is not None and c.owner.is_pool
    assert not has_primary_action(db, co, c)

    co_un = work_queue(db, co, "unassigned", limit=BIG)
    co_all = work_queue(db, co, "all", limit=BIG)
    assert c.id not in _ids(co_un) and c.id not in _ids(co_all)
    assert co_un["counts"]["unassigned"] == co_un["total"] == len(co_un["items"])
    for item in co_un["items"]:
        assert item["unassigned"]
        assert has_primary_action(db, co, db.get(Case, item["id"]))

    lead_un = work_queue(db, lead, "unassigned", limit=BIG)
    assert c.id in _ids(lead_un)
    assert lead_un["counts"]["unassigned"] == lead_un["total"] == len(lead_un["items"])
    # case-number search still finds it on the coordinator queue
    found = work_queue(db, co, "unassigned", limit=BIG, search=f"#{c.id}")
    assert c.id in _ids(found)
    # the unassigned view still applies: a named-owner idle case would not appear
    assert found["counts"]["unassigned"] == found["total"]


def test_acting_on_unowned_case_claims_the_actor_as_owner(db):
    """A call that does not move the case still takes it off the team pool when the caller acts."""
    from kareshafaa.comms.service import record_call
    from kareshafaa.models import OwnershipEvent
    from kareshafaa.services.workflow import programme_pool

    co = _actor(db, "aisha.rahman")
    c = db.scalar(select(Case).where(Case.state.in_(("triaged", "patient_notified", "unreachable")))
                  .order_by(Case.id).limit(1))
    assert c is not None
    pool = programme_pool(db, c.programme)
    c.owner_id = pool.id
    db.flush()
    db.expire(c, ["owner"])
    before = work_queue(db, co, "unassigned", limit=BIG)
    was_listed = c.id in _ids(before)

    record_call(db, c, co, "no_answer", note="no reply, will try again")
    db.flush()
    db.refresh(c)
    assert c.owner_id == co.practitioner_id
    ev = db.scalar(select(OwnershipEvent).where(OwnershipEvent.case_id == c.id,
                                                OwnershipEvent.kind == "claim")
                   .order_by(OwnershipEvent.id.desc()).limit(1))
    assert ev is not None and ev.to_owner_id == co.practitioner_id and ev.from_owner_id == pool.id

    after = work_queue(db, co, "unassigned", limit=BIG)
    assert c.id not in _ids(after)
    if was_listed:
        assert after["counts"]["unassigned"] == before["counts"]["unassigned"] - 1
    assert after["counts"]["unassigned"] == after["total"] == len(after["items"])


def test_transition_claims_actor_when_default_owner_would_be_the_pool(db):
    """Recording a consultation on a pool-owned case whose GP is inactive makes the coordinator the owner."""
    from kareshafaa.models import OwnershipEvent, Practitioner
    from kareshafaa.services.workflow import programme_pool

    co = _actor(db, "aisha.rahman")
    c = db.scalar(select(Case).where(Case.state == "acknowledged", Case.needs_confirmation.is_(False))
                  .order_by(Case.id).limit(1))
    if c is None:
        c = db.scalar(select(Case).where(Case.state == "acknowledged").order_by(Case.id).limit(1))
        if c is not None:
            c.needs_confirmation = False
            db.flush()
    assert c is not None, "seed has no acknowledged case to record a consultation on"
    assert has_primary_action(db, co, c)
    gp = db.get(Practitioner, c.patient.gp_id)
    if gp is not None:
        gp.active = False
        db.flush()
    pool = programme_pool(db, c.programme)
    c.owner_id = pool.id
    db.flush()
    db.expire(c, ["owner", "patient"])

    before = work_queue(db, co, "unassigned", limit=BIG)["counts"]["unassigned"]
    wf.transition(db, c, "consultation", co, note="Consultation recorded; next step is referral.")
    db.flush()
    db.refresh(c)
    assert c.state == "consultation"
    assert c.owner_id == co.practitioner_id
    ev = db.scalar(select(OwnershipEvent).where(OwnershipEvent.case_id == c.id,
                                                OwnershipEvent.kind == "claim")
                   .order_by(OwnershipEvent.id.desc()).limit(1))
    assert ev is not None and ev.to_owner_id == co.practitioner_id

    after = work_queue(db, co, "unassigned", limit=BIG)
    assert c.id not in _ids(after)
    assert after["counts"]["unassigned"] == after["total"] == len(after["items"])
    assert after["counts"]["unassigned"] == before - 1


def test_unassigned_count_updates_immediately_after_api_action(client, as_user, new_case, db):
    """Queue counts follow the commit: no stale cache after the coordinator takes a pooled case."""
    from kareshafaa.services.workflow import programme_pool

    co = as_user("aisha.rahman")
    body = new_case(smartphone=True, phone_valid=True)  # colorectal; ladder can deliver
    cid = body["id"]
    db.expire_all()
    c = db.get(Case, cid)
    pool = programme_pool(db, c.programme)
    r = client.post(f"/api/cases/{cid}/assign", headers=co,
                    json={"owner_id": pool.id,
                          "reason": "Return to the team pool so the unassigned-count cache can be checked."})
    assert r.status_code == 200, r.text

    db.expire_all()
    state = db.get(Case, cid).state
    q1 = client.get("/api/queue?view=unassigned&limit=1000", headers=co).json()
    assert cid in {i["id"] for i in q1["items"]}
    count1 = q1["counts"]["unassigned"]
    assert count1 == q1["total"]

    # Ingest often auto-triages a routine result; act with the coordinator's primary step.
    if state == "issued":
        r = client.post(f"/api/cases/{cid}/transition", headers=co,
                        json={"to_state": "triaged", "note": "Triage confirmed."})
    elif state == "triaged":
        r = client.post(f"/api/cases/{cid}/notify", headers=co, json={"category": "result_notification"})
    else:
        raise AssertionError(f"unexpected state after ingest+pool assign: {state}")
    assert r.status_code == 200, r.text

    owner = client.get(f"/api/cases/{cid}", headers=co).json()
    assert not owner.get("owner_is_pool"), owner
    q2 = client.get("/api/queue?view=unassigned&limit=1000", headers=co).json()
    assert cid not in {i["id"] for i in q2["items"]}
    assert q2["counts"]["unassigned"] == q2["total"] == count1 - 1
    # still in the coordinator's actionable list (tell the patient, or teach-back)
    q_all = client.get("/api/queue?view=all&limit=1000", headers=co).json()
    assert cid in {i["id"] for i in q_all["items"]}
