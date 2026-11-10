"""Clinical lead vs admin separation of duties.

Clinical lead: clinical approvals and sign-offs, the outreach-model approval gate, escalations, overdue oversight,
case review, specialist answers and clinical audit/quality reports (audit log read-only).
Admin: user accounts, system status, deadline-check runner, model retraining, proposal engine, simulation runs,
raw result import, audit-chain verification, the Advanced tools page. Admin makes no clinical decisions.
Every rule is checked on the server (API) and in the UI (navigation / page access)."""
from __future__ import annotations

import pytest

LEAD, ADMIN = "fatima.al.mansoori", "admin"

ADMIN_ONLY_POSTS = [  # (method, path, body)
    ("post", "/api/sla/sweep", {}),
    ("post", "/api/governance/models/train", {"algorithm": "logreg"}),
    ("post", "/api/governance/proposals/generate", None),
    ("post", "/api/analytics/simulation/run?n=100", None),
    ("post", "/api/results", {"patient_id": 1, "programme": "colorectal_fit", "value": 250, "unit": "ug/g"}),
    ("post", "/api/admin/users", {"username": "lead.made.this", "display_name": "X", "role": "analyst"}),
]


@pytest.mark.parametrize("lead", [LEAD, "layla.al.hashimi"])
@pytest.mark.parametrize("method,path,body", ADMIN_ONLY_POSTS)
def test_clinical_lead_is_refused_admin_actions(client, as_user, lead, method, path, body):
    r = getattr(client, method)(path, headers=as_user(lead), **({"json": body} if body is not None else {}))
    assert r.status_code == 403, (path, r.status_code, r.text[:200])


@pytest.mark.parametrize("path", ["/api/audit/verify", "/api/admin/users", "/api/admin/system"])
def test_clinical_lead_cannot_read_admin_tooling(client, as_user, path):
    assert client.get(path, headers=as_user(LEAD)).status_code == 403
    assert client.get(path, headers=as_user(ADMIN)).status_code == 200


def test_admin_keeps_every_admin_function(client, as_user):
    h = as_user(ADMIN)
    r = client.post("/api/sla/sweep", headers=h, json={}); assert r.status_code == 200, r.text
    assert client.get("/api/audit/verify", headers=h).json()["valid"] is True
    assert client.get("/api/audit?limit=1", headers=h).status_code == 200
    assert client.get("/api/admin/users", headers=h).status_code == 200
    for page in ("/admin/users", "/admin/system", "/advanced", "/audit", "/walkthrough"):
        assert client.get(page, headers=h).status_code == 200, page
    assert "Check the log is intact" in client.get("/audit", headers=h).text


def test_clinical_lead_keeps_clinical_duties_and_a_read_only_audit_view(client, as_user):
    h = as_user(LEAD)
    for page in ("/overview", "/queue", "/governance", "/dashboard", "/audit", "/consults"):
        assert client.get(page, headers=h).status_code == 200, page
    for api in ("/api/governance/models", "/api/governance/proposals", "/api/governance/content",
                "/api/governance/sla-rules", "/api/audit?limit=1", "/api/analytics/kpis", "/api/queue?view=escalated&limit=1"):
        assert client.get(api, headers=h).status_code == 200, api
    page = client.get("/audit", headers=h).text
    assert "Check the log is intact" not in page and "read-only" in page and 'href="/advanced"' not in page
    for p in ("/advanced", "/walkthrough", "/ingest", "/admin/users", "/admin/system"):
        assert client.get(p, headers=h).status_code == 403, p


def test_navigation_matches_the_split(client, as_user):
    from kareshafaa.ui import nav_for
    lead = [n["href"] for n in nav_for("clinical_lead")]
    assert "/advanced" not in lead and "/admin/users" not in lead and "/audit" in lead and "/governance" in lead
    admin = [n["href"] for n in nav_for("admin")]
    assert {"/admin/users", "/admin/system", "/advanced"} <= set(admin)
    assert not {"/queue", "/governance", "/overview", "/consults"} & set(admin)


def test_admin_makes_no_clinical_decisions(client, as_user, db):
    from sqlalchemy import select
    from kareshafaa.models import Escalation
    h = as_user(ADMIN)
    assert client.get("/api/queue?limit=1", headers=h).status_code == 403
    assert client.get("/api/cases/1", headers=h).status_code == 403
    assert client.post("/api/governance/models/1/approve", headers=h, json={"note": "admin approving"}).status_code == 403
    assert client.post("/api/governance/models/rollback", headers=h, json={"note": "admin rolling back"}).status_code == 403
    assert client.post("/api/governance/proposals/1/decide", headers=h, json={"approve": True, "note": "admin decision"}).status_code == 403
    assert client.post("/api/governance/content/signoff", headers=h, json={"content_id": "x", "kind": "clinical", "note": "admin sign"}).status_code == 403
    esc = db.scalar(select(Escalation).limit(1))
    if esc:
        assert client.post(f"/api/escalations/{esc.id}/ack", headers=h, json={"note": "admin ack attempt"}).status_code == 403
    gov = client.get("/governance?tab=models", headers=h)
    assert gov.status_code == 200  # read-only model registry + retraining live under Advanced for admin


def test_admin_deadline_check_sends_due_reminders_as_the_rules_engine(client, as_user, db, new_case):
    """Regression: the admin-run check failed with 'role lacks comm:send' as soon as a reminder was due."""
    from sqlalchemy import select
    from kareshafaa.models import AuditEvent
    new_case("colorectal_fit", 250)
    r = client.post("/api/sla/sweep", headers=as_user(ADMIN), json={})
    assert r.status_code == 200, r.text
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "sla.sweep_requested").order_by(AuditEvent.id.desc()))
    assert ev is not None and ev.actor_role == "admin"


def test_more_menu_keeps_the_split_and_daily_nav_short(client, as_user):
    """Declutter: each role keeps <= 5 daily items; "More" items obey the same role split and page access."""
    from kareshafaa.ui import MORE, NAV, can_open, nav_for
    for role in NAV:
        assert len(NAV[role]) <= 5, role
        for n in nav_for(role):
            assert can_open(role, n["href"].split("?")[0]), (role, n["href"])
    lead = {n["href"].split("?")[0] for n in nav_for("clinical_lead")}
    assert not lead & {"/admin/users", "/admin/system", "/advanced", "/walkthrough", "/ingest"}
    assert "/audit" in lead  # audit trail stays one tap away (More)
    admin = {n["href"].split("?")[0] for n in nav_for("admin")}
    assert not admin & {"/queue", "/governance", "/overview", "/consults", "/patients", "/cases", "/recalls"}
    assert {h for h, _, _ in MORE["admin"]} == {"/dashboard", "/advanced"}
    html = client.get("/overview", headers=as_user("fatima.al.mansoori")).text
    assert 'class="nav-more"' in html and 'href="/audit"' in html and "Needs you now" in html
    q = client.get("/queue", headers=as_user("aisha.rahman")).text
    assert "Needs you now" in q and 'class="nav-more"' not in q
    for page in (html, q):
        assert "demo" not in page.lower().replace("demo_", "")
