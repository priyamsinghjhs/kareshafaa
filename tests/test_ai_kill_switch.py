"""AI kill switch: the advisory outreach-priority model can be switched off (deployment setting or admin on
Admin > System). Off = no scores, the queue uses deadline order. Admin only, reason required, audited."""
from __future__ import annotations

from sqlalchemy import select

from kareshafaa.ml import registry, switch
from kareshafaa.models import AuditEvent, Case


def _scored(db) -> int:
    return db.query(Case).filter(Case.state != "closed", Case.risk_score.is_not(None)).count()


def test_admin_switch_off_clears_scores_and_on_restores(client, as_user, db):
    h = as_user("admin")
    assert client.post("/api/admin/system/ml-switch", headers=h, json={"enabled": False, "reason": "short"}).status_code == 422
    try:
        r = client.post("/api/admin/system/ml-switch", headers=h, json={"enabled": False, "reason": "model drift under review"})
        assert r.status_code == 200 and r.json()["enabled"] is False
        db.expire_all()
        assert _scored(db) == 0
        assert registry.score_open_cases(db) == 0 and _scored(db) == 0
        db.rollback()
        ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "ml.switched_off").order_by(AuditEvent.id.desc()).limit(1))
        assert ev is not None and ev.detail["reason"] == "model drift under review"
        assert client.get("/api/admin/system", headers=h).json()["ai"]["enabled"] is False
        q = client.get("/api/queue", headers=as_user("aisha.rahman"))
        assert q.status_code == 200
    finally:
        r = client.post("/api/admin/system/ml-switch", headers=h, json={"enabled": True, "reason": "review finished, back on"})
        assert r.status_code == 200 and r.json()["enabled"] is True
    db.expire_all()
    assert client.get("/api/admin/system", headers=h).json()["ai"]["enabled"] is True


def test_only_admin_may_switch(client, as_user):
    for u in ("fatima.al.mansoori", "aisha.rahman", "omar.haddad", "analyst"):
        r = client.post("/api/admin/system/ml-switch", headers=as_user(u), json={"enabled": False, "reason": "not allowed to do this"})
        assert r.status_code == 403, u


def test_deployment_setting_off_means_no_scoring(db, monkeypatch):
    from kareshafaa.config import get_settings
    monkeypatch.setattr(get_settings(), "ml_enabled", False)
    try:
        assert switch.enabled(db) is False
        assert registry.score_open_cases(db) == 0
        assert _scored(db) == 0
    finally:
        db.rollback()


def test_model_actor_may_not_act_on_cases(db):
    import pytest

    from kareshafaa.security import ML_SERVICE
    from kareshafaa.services import workflow as wf
    from kareshafaa.services.errors import Forbidden
    c = db.scalar(select(Case).where(Case.state != "closed").limit(1))
    with pytest.raises(Forbidden):
        wf.transition(db, c, "triaged", ML_SERVICE, note="x")
    with pytest.raises(Forbidden):
        wf.close_case(db, c, ML_SERVICE, "authorised_decision", reason="moved_abroad", note="valid rationale text")
    from kareshafaa.comms.service import send_with_ladder
    with pytest.raises(Forbidden):
        send_with_ladder(db, c, "reminder", ML_SERVICE)
    db.rollback()
