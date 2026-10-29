"""Care summary to the registered GP at care start and closure: content, timeline, inbox, acknowledgement, RBAC."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select

from tests.test_journey_api import SUMMARY, notify_and_ack


def _p(client, h, url, **body):
    return client.post(url, headers=h, json=body)


def _to_care(client, as_user, cid):
    co, gp, sp = (as_user(u) for u in ("aisha.rahman", "omar.haddad", "vikram.mehta"))
    from kareshafaa.models import utcnow
    notify_and_ack(client, as_user, cid)
    assert _p(client, gp, f"/api/cases/{cid}/transition", to_state="consultation", note="GP reviewed").status_code == 200
    assert _p(client, gp, f"/api/cases/{cid}/referral", performer_org_id=9, performer_id=31, summary=SUMMARY).status_code == 200
    assert _p(client, sp, f"/api/cases/{cid}/referral/decision", accept=True, note="ok").status_code == 200
    assert _p(client, co, f"/api/cases/{cid}/appointment", start=(utcnow() - timedelta(hours=1)).isoformat()).status_code == 200
    assert _p(client, sp, f"/api/cases/{cid}/attendance", attended=True).status_code == 200
    r = _p(client, sp, f"/api/cases/{cid}/care-plan", kind="surveillance", description="Two small polyps removed; surveillance", review_due_on="2029-05-02")
    assert r.status_code == 200, r.text


def test_summary_written_at_care_start_and_closure_and_acknowledged(client, db, as_user, new_case):
    from kareshafaa.models import AuditEvent, GpSummary
    cid = new_case(value=250)["id"]
    _to_care(client, as_user, cid)
    gp, sp, lead = as_user("omar.haddad"), as_user("vikram.mehta"), as_user("fatima.al.mansoori")
    db.expire_all()
    s1 = db.scalars(select(GpSummary).where(GpSummary.case_id == cid)).one()
    assert s1.kind == "care_started" and s1.to_practitioner_id == 19 and s1.acknowledged_at is None
    assert "surveillance" in s1.text and "02 May 2029" in s1.text and not s1.text.startswith("[SIMULATED")
    assert _p(client, sp, f"/api/cases/{cid}/close", closure_type="evidence", evidence_type="care_plan").status_code == 200
    db.expire_all()
    s2 = db.scalars(select(GpSummary).where(GpSummary.case_id == cid, GpSummary.kind == "case_closed")).one()
    assert s2.content["outcome"]["reason"] and any("surveillance review due 02 May 2029" in x for x in s2.content["next_steps"])
    # timeline (any role that can see the case) and inbox (GP)
    tl = client.get(f"/api/cases/{cid}", headers=lead).json()["gp_summaries"]
    assert [x["kind"] for x in tl] == ["care_started", "case_closed"] and not any(x["can_ack"] for x in tl)
    ib = client.get("/api/gp-summaries", headers=gp).json()
    assert {s1.id, s2.id} <= {x["id"] for x in ib["items"]} and ib["unread"] >= 2
    # acknowledge (note optional, stored, audited); second time refused
    r = _p(client, gp, f"/api/gp-summaries/{s2.id}/ack", note="Seen – will review at next visit")
    assert r.status_code == 200 and r.json()["summary"]["ack_note"] == "Seen – will review at next visit"
    assert _p(client, gp, f"/api/gp-summaries/{s2.id}/ack").status_code == 422
    assert _p(client, gp, f"/api/gp-summaries/{s1.id}/ack").status_code == 200
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "gp_summary.acknowledged", AuditEvent.entity_id == str(s2.id)))
    assert s2.id not in {x["id"] for x in client.get("/api/gp-summaries", headers=gp).json()["items"]}
    assert s2.id in {x["id"] for x in client.get("/api/gp-summaries?view=all", headers=gp).json()["items"]}


def test_rbac_only_the_gp_practice_acknowledges(client, db, as_user, new_case):
    from kareshafaa.models import GpSummary
    cid = new_case(value=250)["id"]
    _to_care(client, as_user, cid)
    db.expire_all()
    sid = db.scalar(select(GpSummary.id).where(GpSummary.case_id == cid))
    for u in ("aisha.rahman", "vikram.mehta", "fatima.al.mansoori", "admin", "analyst"):
        assert _p(client, as_user(u), f"/api/gp-summaries/{sid}/ack").status_code == 403, u
        assert client.get("/api/gp-summaries", headers=as_user(u)).status_code == 403, u
    assert client.get(f"/api/gp-summaries/{sid}", headers=as_user("admin")).status_code in (403, 404)


def test_gp_closing_their_own_case_files_summary_as_read(client, db, as_user, new_case):
    from kareshafaa.models import GpSummary
    cid = new_case(value=250)["id"]
    r = _p(client, as_user("omar.haddad"), f"/api/cases/{cid}/close", closure_type="authorised_decision",
           reason="clinically_not_indicated", note="Known to colorectal clinic; colonoscopy 2 months ago")
    assert r.status_code == 200, r.text
    db.expire_all()
    s = db.scalars(select(GpSummary).where(GpSummary.case_id == cid)).one()
    assert s.kind == "case_closed" and s.acknowledged_at is not None and "Known to colorectal clinic" in s.text
    assert "No automatic recall" in s.text


def test_no_registered_gp_records_why(db, new_case):
    from kareshafaa.models import AuditEvent, Case, Practitioner
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import gp_summary as g
    c = db.get(Case, new_case(value=250)["id"])
    c.patient.gp_id = None
    c.owner_id = db.scalar(select(Practitioner.id).where(Practitioner.role == "coordinator"))
    assert g.write(db, c, "case_closed", SYSTEM) is None
    assert db.scalar(select(AuditEvent).where(AuditEvent.event == "gp_summary.no_recipient", AuditEvent.case_id == c.id))
    db.rollback()


def test_summaries_page_gp_only(client, as_user):
    from kareshafaa.ui import nav_for
    assert any(x["href"] == "/summaries" for x in nav_for("gp"))
    assert client.get("/summaries", headers=as_user("omar.haddad")).status_code == 200
