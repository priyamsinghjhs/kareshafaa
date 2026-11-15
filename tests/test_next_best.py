"""Next-best LangGraph agent: template fallback, safety, approval audit, role gates."""
from __future__ import annotations

import pytest

from kareshafaa.services import next_best as nb
from kareshafaa.services import assist


@pytest.fixture()
def no_http(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("outbound HTTP must not happen")
    monkeypatch.setattr(assist, "_http_post", boom)


def test_template_when_model_off(no_http):
    out = nb.run({"state_label": "Result received", "tier_label": "Urgent",
                  "primary_label": "Book appointment", "why": "positive FIT"}, use_model=False)
    assert out["origin"] == "template"
    assert out["requires_approval"] is True
    assert "Book appointment" in out["draft"]
    assert "Draft only" in out["disclaimer"]


def test_no_primary_template(no_http):
    out = nb.run({"state_label": "Closed", "tier_label": "Routine"}, use_model=False)
    assert "No primary next step" in out["draft"]
    assert out["requires_approval"] is True


def test_safety_rejects_claims_action(no_http):
    # model path simulated by calling safety via a crafted run is hard; test the node path
    # by injecting a claim through the template-equivalent raw path
    from kareshafaa.services.next_best import _node_safety
    bad = {"raw": "We have closed the case and sent the referral.", "origin": "model",
           "facts": {"state_label": "Open", "tier_label": "High"}}
    out = _node_safety(bad)
    assert out["origin"] == "template"
    assert "closed" not in out["text"].lower() or "Recommended" in out["text"]


def test_safety_rejects_dosing_not_in_facts(no_http):
    from kareshafaa.services.next_best import _node_safety
    bad = {"raw": "Please take 500 mg metformin daily.", "origin": "model",
           "facts": {"state_label": "Open", "tier_label": "High", "primary_label": "Book"}}
    out = _node_safety(bad)
    assert out["origin"] == "template"
    assert "mg" not in out["text"].lower()


def test_next_best_endpoint_staff(client, as_user, new_case, no_http):
    c = new_case()
    r = client.get(f"/api/assist/next-best/{c['id']}", headers=as_user("aisha.rahman"))
    assert r.status_code == 200
    d = r.json()
    assert d["requires_approval"] is True
    assert d["draft"]
    assert d["origin"] in ("template", "model")


def test_next_best_patient_forbidden(client, as_user, new_case, no_http):
    c = new_case()
    r = client.get(f"/api/assist/next-best/{c['id']}", headers=as_user("patient.en"))
    assert r.status_code == 403


def test_approve_records_decision(client, as_user, new_case, db):
    c = new_case()
    r = client.post("/api/assist/next-best/approve",
                    headers=as_user("aisha.rahman"),
                    json={"case_id": c["id"], "decision": "approve", "note": "looks right"})
    assert r.status_code == 200
    assert r.json()["recorded"] is True
    from kareshafaa.models import AuditEvent
    from sqlalchemy import select
    row = db.execute(select(AuditEvent).where(AuditEvent.event == "assist.next_best_decision")
                     .order_by(AuditEvent.id.desc())).scalars().first()
    assert row is not None
    assert row.detail["decision"] == "approve"


def test_approve_bad_decision(client, as_user, new_case):
    c = new_case()
    r = client.post("/api/assist/next-best/approve",
                    headers=as_user("aisha.rahman"),
                    json={"case_id": c["id"], "decision": "maybe"})
    assert r.status_code == 422


def test_approve_patient_forbidden(client, as_user, new_case):
    c = new_case()
    r = client.post("/api/assist/next-best/approve",
                    headers=as_user("patient.en"),
                    json={"case_id": c["id"], "decision": "approve"})
    assert r.status_code == 403
