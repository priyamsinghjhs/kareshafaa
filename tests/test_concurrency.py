"""Iteration 7 failure-mode round: concurrent actions on the same case / patient.

Before this round two people acting on one case at once could both succeed on a stale view (a transition applied
twice, or a referral sent on a case another user had just closed), and two results for one patient arriving at
once could open two cases. Now: optimistic row-version on cases (second writer gets HTTP 409, nothing saved) and a
partial unique index (one open case per patient and programme)."""
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.exc import StaleDataError


def _sessions():
    from kareshafaa.db import session_factory

    sf = session_factory()
    return sf(), sf()


def _actor(db, username="aisha.rahman"):
    from kareshafaa.api.deps import actor_from_user
    from kareshafaa.models import User

    return actor_from_user(db.scalar(select(User).where(User.username == username)))


def test_stale_transition_is_rejected(new_case):
    from kareshafaa.models import Case, StepTask
    from kareshafaa.services import workflow as wf

    cid = new_case()["id"]  # FIT 250 -> auto-triaged
    a, b = _sessions()
    try:
        ca, cb = a.get(Case, cid), b.get(Case, cid)
        assert ca.state == cb.state == "triaged"
        pool = wf.programme_pool(a, ca.programme).id
        lead = wf.programme_clinical_lead(b, cb.programme).id
        wf.assign(a, ca, pool, _actor(a), "A: hand to the programme pool for cover")
        a.commit()
        with pytest.raises(StaleDataError):
            wf.assign(b, cb, lead, _actor(b), "B: escalate to lead (decided on a stale view)")
            b.commit()
        b.rollback()
        a.expire_all()
        assert a.get(Case, cid).owner_id == pool
        n_open = a.scalar(select(func.count()).select_from(StepTask).where(StepTask.case_id == cid,
                                                                           StepTask.ended_at.is_(None)))
        assert n_open == 1
    finally:
        a.close()
        b.close()


def test_action_on_case_closed_meanwhile_is_rejected(new_case):
    from kareshafaa.models import Case
    from kareshafaa.services import workflow as wf

    cid = new_case()["id"]
    a, b = _sessions()
    try:
        ca, cb = a.get(Case, cid), b.get(Case, cid)
        from kareshafaa.security import Actor

        lead = Actor(user_id=1, role="clinical_lead", display="Named Person")
        wf.close_case(a, ca, lead, "authorised_decision", reason="moved_abroad",
                      note="Patient confirmed relocation abroad; summary provided.")
        a.commit()
        # B still sees the case open and reassigns it
        pool = wf.programme_pool(b, cb.programme).id
        with pytest.raises((StaleDataError, Exception)) as ei:
            wf.assign(b, cb, pool, _actor(b), "B: reassign on a stale view of the case")
            b.commit()
        assert isinstance(ei.value, StaleDataError), ei.value
        b.rollback()
        a.expire_all()
        assert a.get(Case, cid).state == "closed"
    finally:
        a.close()
        b.close()


def test_stale_action_over_http_is_409(client, as_user, new_case, monkeypatch):
    """The API maps the stale-write error to 409 with a plain message (nothing saved)."""
    from kareshafaa.api import cases as cases_api

    cid = new_case()["id"]
    real = cases_api.wf.assign

    def racing(db, case, *args, **kw):  # another user changes the case between our read and our write
        from kareshafaa.db import session_factory
        from kareshafaa.models import Case

        with session_factory()() as other:
            oc = other.get(Case, case.id)
            oc.escalation_level = (oc.escalation_level or 0) + 1
            other.commit()
        return real(db, case, *args, **kw)

    monkeypatch.setattr(cases_api.wf, "assign", racing)
    from kareshafaa.db import session_factory
    from kareshafaa.models import Case
    from kareshafaa.services import workflow as wf

    with session_factory()() as s:
        pool = wf.programme_pool(s, s.get(Case, cid).programme).id
    r = client.post(f"/api/cases/{cid}/assign", headers=as_user("aisha.rahman"),
                    json={"owner_id": pool, "reason": "hand over to the programme pool"})
    assert r.status_code == 409 and "changed by someone else" in r.json()["detail"]


def test_one_open_case_per_patient_programme_enforced_by_db(db, new_case):
    from kareshafaa.models import Case

    c = db.get(Case, new_case()["id"])
    dup = Case(patient_id=c.patient_id, report_id=c.report_id, programme=c.programme, tier=c.tier, state="issued",
               owner_id=c.owner_id, managing_org_id=c.managing_org_id)
    db.add(dup)
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_concurrent_results_for_one_patient_never_open_two_cases(client, as_user, db, make_patient):
    from kareshafaa.models import Case

    pid = make_patient()
    h = as_user("aisha.rahman")

    def post(v):
        return client.post("/api/results", headers=h, json={"patient_id": pid, "programme": "colorectal_fit",
                                                             "value": v, "unit": "ug/g"})

    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(post, [150 + i for i in range(8)]))
    assert all(r.status_code in (200, 409) for r in rs), [r.text for r in rs]
    assert sum(r.status_code == 200 for r in rs) >= 1
    db.expire_all()
    n = db.scalar(select(func.count()).select_from(Case).where(Case.patient_id == pid, Case.state != "closed"))
    assert n == 1


def test_concurrent_duplicate_hl7_deliveries_are_idempotent(client, as_user, db, make_patient):
    from kareshafaa.models import IngestMessage, Patient

    mrn = db.get(Patient, make_patient()).mrn
    m = "\r".join(["MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|CONC-1|P|2.5.1",
                   f"PID|1||{mrn}^^^KARESHAFAA^MR||SYN^PAT||19700101|F", "OBX|1|NM|29771-3^FIT^LN||300|ug/g|||||F"])
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    with ThreadPoolExecutor(8) as ex:
        rs = list(ex.map(lambda _: client.post("/api/ingest/hl7v2", headers=h, content=m), range(8)))
    assert all(r.status_code == 200 for r in rs), [r.text for r in rs]
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(IngestMessage).where(IngestMessage.message_id == "CONC-1")) == 1
    assert len({r.json()["case"]["id"] for r in rs}) == 1
