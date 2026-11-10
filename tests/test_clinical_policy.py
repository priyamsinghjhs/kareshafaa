"""Clinical policy version: placeholder, verify; no approved flag without a named approver; the live profile is
refused at startup; the version is shown and recorded on each result decision."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from kareshafaa import policy


def test_policy_is_a_placeholder_without_an_approver():
    assert policy.POLICY_STATUS == "placeholder" and policy.POLICY_STATUS_NOTE == "placeholder, verify"
    assert policy.APPROVED_BY is None and policy.APPROVAL_REF is None
    assert policy.is_approved() is False


def test_live_profile_refused_and_unknown_profile_refused(monkeypatch):
    with pytest.raises(RuntimeError, match="refused"):
        policy.check_profile("live")
    with pytest.raises(RuntimeError):
        policy.check_profile("production")
    policy.check_profile("synthetic")
    # approval status alone is not enough: a named approver and a reference are both required
    monkeypatch.setattr(policy, "POLICY_STATUS", "approved")
    with pytest.raises(RuntimeError):
        policy.check_profile("live")


def test_live_profile_refused_by_settings(monkeypatch):
    from kareshafaa.config import get_settings
    monkeypatch.setenv("KARESHAFAA_CLINICAL_PROFILE", "live")
    with pytest.raises(RuntimeError, match="placeholder, verify"):
        get_settings.__wrapped__()  # the uncached function: the shared settings object is untouched


def test_policy_version_shown_and_recorded(client, as_user, new_case, db):
    from kareshafaa.models import AuditEvent
    assert client.get("/healthz").json()["clinical_policy"]["version"] == policy.POLICY_VERSION
    s = client.get("/api/admin/system", headers=as_user("admin")).json()
    assert s["clinical_policy"]["status"] == "placeholder" and s["clinical_profile"] == "synthetic"
    new_case()
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "result.received").order_by(AuditEvent.id.desc()).limit(1))
    assert ev.detail["policy_version"] == policy.POLICY_VERSION
    d = client.get("/api/analytics/impact", headers=as_user("fatima.al.mansoori")).json()
    assert d["settings"]["clinical_policy_version"] == policy.POLICY_VERSION
