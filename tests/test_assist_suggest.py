"""Draft-text suggestions on staff note fields (POST /api/assist/suggest, kareshafaa/services/assist.py).

Advisory text only, off without an API key or with the AI kill switch off, minimised context with identifiers
stripped, fail closed (no 500), per-user rate limit, audited without storing the prompt or the text. The real
provider is never called: every test replaces the one outbound call (assist._http_post)."""
from __future__ import annotations

import json

import httpx
import pytest
from sqlalchemy import select

from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Case, CarePlan, Patient, utcnow
from kareshafaa.services import assist

URL = "/api/assist/suggest"


@pytest.fixture()
def slm(monkeypatch):
    """Configured provider with a recording fake transport. calls: list of (url, headers, payload)."""
    s = get_settings()
    monkeypatch.setattr(s, "slm_api_key", "test-key-not-real")
    calls: list = []
    reply = {"text": "Care plan recorded as evidence; closing per pathway. [follow-up date]"}

    def fake(url, *, headers, payload, timeout):
        calls.append((url, headers, payload))
        if isinstance(reply["text"], Exception):
            raise reply["text"]
        return {"choices": [{"message": {"content": reply["text"]}}]}

    monkeypatch.setattr(assist, "_http_post", fake)
    assist.LIMITER.reset()
    yield {"calls": calls, "reply": reply}
    assist.LIMITER.reset()


@pytest.fixture()
def no_http(monkeypatch):
    def boom(*a, **k):  # pragma: no cover - reaching it is the failure
        raise AssertionError("outbound HTTP must not happen")
    monkeypatch.setattr(assist, "_http_post", boom)


def _body(case_id, **kw):
    return {"case_id": case_id, "action": "call", "field": "note", "draft": "", **kw}


def test_anonymous_401(client, new_case):
    c = new_case()
    client.cookies.clear()
    assert client.post(URL, json=_body(c["id"])).status_code == 401


def test_patient_403(client, as_user, new_case, no_http):
    c = new_case()
    assert client.post(URL, headers=as_user("patient.en"), json=_body(c["id"])).status_code == 403


def test_staff_without_case_access_refused(client, as_user, new_case, slm):
    c = new_case()  # not referred to the specialist's hospital
    r = client.post(URL, headers=as_user("vikram.mehta"), json=_body(c["id"]))
    assert r.status_code in (403, 404) and "suggestion" not in r.json()
    assert client.post(URL, headers=as_user("aisha.rahman"), json=_body(999_999)).status_code == 404
    assert slm["calls"] == []


def test_not_configured_is_off_without_http(client, as_user, new_case, monkeypatch, no_http):
    monkeypatch.setattr(get_settings(), "slm_api_key", None)
    c = new_case()
    r = client.post(URL, headers=as_user("aisha.rahman"), json=_body(c["id"]))
    assert r.status_code == 200
    d = r.json()
    assert d["enabled"] is False and d["reason"] == "not_configured" and d["suggestion"] is None


def test_kill_switch_deployment_off(client, as_user, new_case, monkeypatch, no_http):
    monkeypatch.setattr(get_settings(), "slm_api_key", "test-key-not-real")
    monkeypatch.setattr(get_settings(), "ml_enabled", False)
    c = new_case()
    d = client.post(URL, headers=as_user("aisha.rahman"), json=_body(c["id"])).json()
    assert d["enabled"] is False and d["reason"] == "off" and d["suggestion"] is None


def test_kill_switch_runtime_off(client, as_user, new_case, monkeypatch, no_http):
    monkeypatch.setattr(get_settings(), "slm_api_key", "test-key-not-real")
    h = as_user("admin")
    c = new_case()
    try:
        assert client.post("/api/admin/system/ml-switch", headers=h,
                           json={"enabled": False, "reason": "drafts under review today"}).status_code == 200
        d = client.post(URL, headers=as_user("aisha.rahman"), json=_body(c["id"])).json()
        assert d["enabled"] is False and d["reason"] == "off" and d["suggestion"] is None
        sysd = client.get("/api/admin/system", headers=h).json()["ai"]["drafts"]
        assert sysd["enabled"] is False and sysd["reason"] == "off"
    finally:
        assert client.post("/api/admin/system/ml-switch", headers=h,
                           json={"enabled": True, "reason": "review finished, back on"}).status_code == 200


def test_suggestion_returned_audited_without_text(client, as_user, new_case, slm, db):
    c = new_case()
    db.add(CarePlan(case_id=c["id"], kind="treatment", description="Colonoscopy booked at the hospital",
                    recorded_by_id=db.get(Case, c["id"]).owner_id, created_at=utcnow()))
    db.commit()
    state_before = db.get(Case, c["id"]).state
    r = client.post(URL, headers=as_user("aisha.rahman"), json=_body(c["id"], draft="Closing with"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["enabled"] is True and d["suggestion"].startswith("Care plan recorded")
    assert d["model"] == get_settings().slm_model and d["disclaimer"].startswith("Draft only")
    url, headers, payload = slm["calls"][0]
    assert url == "https://openrouter.ai/api/v1/chat/completions"
    assert headers["X-Title"] == "KareShafaa" and headers["Authorization"] == "Bearer test-key-not-real"
    assert payload["model"] == "inclusionai/ling-3.0-flash-sante:free"
    sent = json.dumps(payload)
    assert "Closing with" in sent
    # advisory only: the case did not move
    db.expire_all()
    assert db.get(Case, c["id"]).state == state_before
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "assist.suggestion_requested",
                                            AuditEvent.case_id == c["id"]).order_by(AuditEvent.id.desc()).limit(1))
    assert ev is not None and ev.detail["returned"] is True and ev.detail["action"] == "call"
    assert "Care plan recorded" not in json.dumps(ev.detail) and "Colonoscopy" not in json.dumps(ev.detail)


def test_identifiers_never_sent(client, as_user, new_case, slm, db):
    c = new_case()
    case = db.get(Case, c["id"])
    p = db.get(Patient, case.patient_id)
    p.given_name, p.family_name, p.national_id = "Zaynab", "Qadri", "784-1971-7654321-2"
    p.phone, p.email, p.address_line = "+971501112233", "zaynab.q@example.org", "Villa 12 Palm Street"
    db.commit()
    mrn = p.mrn
    draft = f"Spoke to Zaynab Qadri ({mrn}) on +971501112233, zaynab.q@example.org, EID 784-1971-7654321-2, DOB 1971"
    r = client.post(URL, headers=as_user("aisha.rahman"), json=_body(c["id"], action="call", evidence_type=None,
                                                                    draft=draft))
    assert r.status_code == 200
    sent = json.dumps(slm["calls"][-1][2])
    for secret in ("Zaynab", "Qadri", mrn, "+971501112233", "zaynab.q@example.org", "7654321", "Palm Street",
                   "Aisha", "Rahman"):
        assert secret not in sent, secret
    assert "[patient]" in sent


def test_build_context_redacts(db, new_case):
    c = new_case()
    case = db.get(Case, c["id"])
    p = case.patient
    p.given_name, p.family_name, p.national_id, p.phone = "Huda", "Karimi", "784-1980-1111111-1", "+971509998877"
    p.email, p.address_line = "huda@example.org", "Flat 4 Corniche Road"
    db.add(CarePlan(case_id=case.id, kind="surveillance", description="Huda Karimi to repeat HbA1c; call +971509998877",
                    recorded_by_id=case.owner_id, created_at=utcnow()))
    db.flush()
    ctx = assist.build_context(db, case, action="start_care", field="description", evidence_type="care_plan",
                               draft="Huda agreed", staff_names=["Dr. Omar Haddad"])
    txt = json.dumps(ctx)
    for secret in ("Huda", "Karimi", p.mrn, "1111111", "+971509998877", "huda@example.org", "Corniche", "Haddad",
                   str(p.birth_year)):
        assert secret not in txt, secret
    assert set(ctx) >= {"programme", "urgency", "case_state", "screening_result", "evidence", "current_draft"}
    assert ctx["evidence"]["care_plan_kind"] == "surveillance" and "repeat HbA1c" in ctx["evidence"]["care_plan_description"]
    for k in ("name", "mrn", "phone", "email", "address", "national_id", "birth_year", "patient_id"):
        assert k not in ctx
    db.rollback()


@pytest.mark.parametrize("bad", ["I have closed this case.", "The case has been closed with evidence.",
                                 "We have sent the referral.", "", "   ", "x" * 900])
def test_post_check_rejects(bad):
    assert assist.post_check(bad, "note") is None


def test_post_check_keeps_good_text():
    assert assist.post_check('Note: "Closing with linked care plan evidence; [review date]."', "note") \
        == "Closing with linked care plan evidence; [review date]."


def test_provider_error_or_junk_fails_closed(client, as_user, new_case, slm):
    c = new_case()
    h = as_user("aisha.rahman")
    for failure in (httpx.ReadTimeout("slow"), httpx.ConnectError("down"), RuntimeError("junk"),
                    "I have closed the case for you."):
        slm["reply"]["text"] = failure
        r = client.post(URL, headers=h, json=_body(c["id"]))
        assert r.status_code == 200, r.text
        assert r.json()["enabled"] is True and r.json()["suggestion"] is None and r.json()["unavailable"] is True


def test_rate_limited_per_user(client, as_user, new_case, slm, monkeypatch):
    monkeypatch.setattr(get_settings(), "slm_rate_per_minute", 2)
    c = new_case()
    h = as_user("aisha.rahman")
    got = [client.post(URL, headers=h, json=_body(c["id"])).json() for _ in range(3)]
    assert got[0]["suggestion"] and got[1]["suggestion"]
    assert got[2]["suggestion"] is None and got[2]["reason"] == "busy" and len(slm["calls"]) == 2


def test_unsupported_form_or_field_refused(client, as_user, new_case, slm):
    c = new_case()
    h = as_user("aisha.rahman")
    assert client.post(URL, headers=h, json=_body(c["id"], action="medication")).status_code == 422
    assert client.post(URL, headers=h, json=_body(c["id"], field="dose")).status_code == 422
    assert client.post(URL, headers=h, json=_body(c["id"], field="phone")).status_code == 422
    assert slm["calls"] == []


def test_close_endpoint_unchanged_by_suggestions(client, as_user, new_case, slm):
    """The Use button only inserts text into the field; closing still goes through /close and its evidence check."""
    c = new_case()
    r = client.post(f"/api/cases/{c['id']}/close", headers=as_user("aisha.rahman"),
                    json={"closure_type": "evidence", "evidence_type": "care_plan", "note": "draft text"})
    assert r.status_code in (400, 409, 422)  # no care plan on this case: refused exactly as before


def test_slm_not_offered_on_clinical_closure_notes(client, as_user, new_case, slm):
    """Closure notes use the record-sourced suggestion, not the language model."""
    c = new_case()
    h = as_user("aisha.rahman")
    for action, extra in (("close_evidence", {"evidence_type": "care_plan"}),
                          ("close_authorised", {})):
        r = client.post(URL, headers=h, json=_body(c["id"], action=action, **extra))
        assert r.status_code == 422, r.text
        assert "not offered" in r.json()["detail"]
    assert slm["calls"] == []

