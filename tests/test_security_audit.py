"""RBAC, consent, patient isolation and append-only audit."""
import pytest
from sqlalchemy import func, select, text

from kareshafaa.models import AuditEvent


def test_unauthenticated_is_401(client):
    assert client.get("/api/queue").status_code == 401
    assert client.get("/api/cases/1").status_code == 401


def test_tampered_token_rejected(client, as_user):
    h = as_user("aisha.rahman")
    bad = {"Authorization": h["Authorization"][:-3] + "abc"}
    assert client.get("/api/queue", headers=bad).status_code == 401


@pytest.mark.parametrize("user,url,code", [
    ("analyst", "/api/cases/1", 403),
    ("analyst", "/api/queue", 403),
    ("analyst", "/api/audit", 403),
    ("analyst", "/api/analytics/kpis", 200),
    ("admin", "/api/cases/1", 403),
    ("admin", "/api/audit", 200),
    ("aisha.rahman", "/api/governance/models", 403),
    ("aisha.rahman", "/api/analytics/kpis", 403),
    ("patient.en", "/api/queue", 403),
    ("patient.en", "/api/cases/1", 403),
    ("patient.en", "/api/me/case", 200),
    ("fatima.al.mansoori", "/api/governance/models", 200),
])
def test_rbac_matrix(client, as_user, user, url, code):
    assert client.get(url, headers=as_user(user)).status_code == code


def test_analyst_cannot_write_or_approve(client, as_user):
    h = as_user("analyst")
    assert client.post("/api/governance/models/1/approve", headers=h, json={"note": "looks good"}).status_code == 403
    assert client.post("/api/governance/models/rollback", headers=h, json={"note": "rollback"}).status_code == 403


def test_org_scoped_access(client, as_user, new_case):
    cid = new_case()["id"]  # managed by Corniche PHC (org 3)
    assert client.get(f"/api/cases/{cid}", headers=as_user("omar.haddad")).status_code == 200
    assert client.get(f"/api/cases/{cid}", headers=as_user("imran.siddiqui")).status_code == 403  # other PHC
    assert client.get(f"/api/cases/{cid}", headers=as_user("vikram.mehta")).status_code == 403  # hospital not involved


def test_share_external_deny_blocks_referral_and_transfer(client, as_user, new_case):
    from test_journey_api import notify_and_ack

    cid = new_case(share_external="deny")["id"]
    notify_and_ack(client, as_user, cid)
    gp = as_user("omar.haddad")
    client.post(f"/api/cases/{cid}/transition", headers=gp, json={"to_state": "consultation"})
    r = client.post(f"/api/cases/{cid}/referral", headers=gp,
                    json={"performer_org_id": 9, "summary": "x" * 60})
    assert r.status_code == 403 and "consent" in r.json()["detail"]
    r = client.post(f"/api/cases/{cid}/transfer", headers=gp, json={"to_owner_id": 21, "note": "moving PHC"})
    assert r.status_code == 403
    # patient permits sharing -> referral now possible
    r = client.post(f"/api/cases/{cid}/consent", headers=gp, json={"scope": "share_external", "decision": "permit",
                                                                 "note": "verbal consent recorded"})
    assert r.status_code == 200
    r = client.post(f"/api/cases/{cid}/referral", headers=gp, json={"performer_org_id": 9, "summary": "x" * 60})
    assert r.status_code == 200, r.text


def test_patient_sees_only_own_case(client, as_user):
    r = client.get("/api/me/case?lang=hi", headers=as_user("patient.hi")).json()
    r2 = client.get("/api/me/case", headers=as_user("patient.en")).json()
    assert r["case_id"] != r2["case_id"]
    assert r["language"] == "hi" and r["label"].startswith("KareShafaa prototype")
    ur = client.get("/api/me/case?lang=ur", headers=as_user("patient.ur")).json()
    assert ur["rtl"] is True
    # no internal fields leak to the patient view
    assert "risk_score" not in r and "mrn" not in str(r["patient"])


def test_patient_teach_back_fail_then_pass(client, as_user):
    h = as_user("patient.ar")
    case = client.get("/api/me/case?lang=ar", headers=h).json()
    assert case["can_teach_back"]
    r = client.post("/api/me/teach-back", headers=h, json={"answers": {"q1": "b", "q2": "b", "q3": "a"},
                                                           "confidence": 2, "language": "ar"}).json()
    assert r["passed"] is False and r["state"] == "patient_notified"
    again = {"answers": {"q1": "a", "q2": "b", "q3": "a"}, "confidence": 4, "language": "ar"}
    blocked = client.post("/api/me/teach-back", headers=h, json=again)  # answers are locked once sent
    assert blocked.status_code == 409 and blocked.json()["code"] == "answers_locked"
    assert client.post("/api/me/teach-back/reopen", headers=h, json={"confirm": True}).status_code == 200
    r = client.post("/api/me/teach-back", headers=h, json=again).json()
    assert r["passed"] is True and r["state"] == "acknowledged" and r["changed"] is True


def test_every_request_is_audited(client, as_user, db):
    db.expire_all()
    before = db.scalar(select(func.count()).select_from(AuditEvent))
    client.get("/api/queue", headers=as_user("aisha.rahman"))
    client.get("/api/cases/1", headers=as_user("analyst"))  # denied
    db.expire_all()
    rows = db.scalars(select(AuditEvent).where(AuditEvent.id > 0).order_by(AuditEvent.id.desc()).limit(10)).all()
    assert db.scalar(select(func.count()).select_from(AuditEvent)) >= before + 2
    outcomes = {(r.event, r.outcome) for r in rows}
    assert any(o == "denied" for _, o in outcomes)


def test_audit_is_append_only(db, seeded):
    row = db.scalar(select(AuditEvent).limit(1))
    row.detail = {"tampered": True}
    with pytest.raises(Exception):
        db.flush()
    db.rollback()
    with pytest.raises(Exception):
        db.execute(text("DELETE FROM audit_events WHERE id = 1"))
    db.rollback()
    with pytest.raises(Exception):
        db.execute(text("UPDATE audit_events SET action='X' WHERE id = 1"))
    db.rollback()


def test_audit_hash_chain_verifies(client, as_user):
    r = client.get("/api/audit/verify", headers=as_user("fatima.al.mansoori")).json()
    assert r["valid"] is True and r["checked"] > 100


def test_audit_trail_for_case_reads(client, as_user):
    h = as_user("fatima.al.mansoori")
    client.get("/api/cases/5", headers=h)
    r = client.get("/api/audit?case_id=5", headers=h).json()
    assert r["items"]


def test_audit_chain_survives_concurrent_writers(client, as_user):
    """Many simultaneous requests (each audited) must never fork the hash chain."""
    from concurrent.futures import ThreadPoolExecutor

    h = as_user("aisha.rahman")
    with ThreadPoolExecutor(max_workers=12) as ex:
        codes = list(ex.map(lambda i: client.get(f"/api/queue?limit=5&offset={i % 3}", headers=h).status_code, range(60)))
    assert set(codes) == {200}
    r = client.get("/api/audit/verify", headers=as_user("fatima.al.mansoori")).json()
    assert r["valid"] is True, r


def test_queue_pagination_and_search(client, as_user):
    h = as_user("aisha.rahman")
    p1 = client.get("/api/queue?view=all&limit=10&offset=0", headers=h).json()
    p2 = client.get("/api/queue?view=all&limit=10&offset=10", headers=h).json()
    assert len(p1["items"]) == 10 and p1["total"] == p2["total"]
    assert not {i["id"] for i in p1["items"]} & {i["id"] for i in p2["items"]}
    target = p1["items"][0]
    s = client.get(f"/api/queue?q={target['mrn']}", headers=h).json()
    assert [i["id"] for i in s["items"]] == [target["id"]]
    s2 = client.get(f"/api/queue?q=%23{target['id']}", headers=h).json()
    assert target["id"] in [i["id"] for i in s2["items"]]


def test_gp_queue_is_org_scoped(client, as_user, db):
    from kareshafaa.models import Case
    from kareshafaa.services.access import involved_org_ids

    h = as_user("omar.haddad")
    items = client.get("/api/queue?limit=1000", headers=h).json()["items"]
    assert items
    for i in items[:50]:
        assert 3 in involved_org_ids(db, db.get(Case, i["id"]))  # Omar works at org 3
