"""Coordinator copilot: LangGraph draft, template fallback, safety filter, kill switch, no auto-actions."""
from __future__ import annotations

import pytest

from kareshafaa.config import get_settings
from kareshafaa.models import AuditEvent, Case, Communication, utcnow
from kareshafaa.services import assist, copilot
from kareshafaa.services.copilot import LANGS, TASKS, safety_check, template


@pytest.fixture()
def slm(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "slm_api_key", "test-key-not-real")
    calls: list = []
    reply = {"text": "Your screening result is ready. Please contact the clinic to arrange the next step."}

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
    def boom(*a, **k):
        raise AssertionError("outbound HTTP must not happen")
    monkeypatch.setattr(assist, "_http_post", boom)


def _ctx(task="outreach", language="en", **extra):
    ctx = {"task": task, "language": language, "programme": "Diabetes screening - HbA1c / fasting glucose",
           "urgency": "T2 Soon", "case_state": "Patient notified",
           "screening_result": {"test": "Haemoglobin A1c", "conclusion": "actionable", "values": ["7.8 %"]},
           "pathway": "Repeat HbA1c", "deadline": "30/11/2026", "sources": ["programme", "screening_result", "pathway"]}
    ctx.update(extra)
    return ctx


def test_tasks_and_languages():
    assert TASKS == ("outreach", "summary", "simplify_letter")
    assert LANGS == ("en", "ar", "hi", "ur")


def test_templates_exist_in_all_four_languages_and_pass_safety():
    for task in TASKS:
        for lang in LANGS:
            if task == "summary" and lang in ("hi", "ur"):
                # summaries fall back to English for Hindi/Urdu (staff page) — still must pass safety
                pass
            text = template(_ctx(task, lang))
            assert text and len(text) > 20
            assert safety_check(text, _ctx(task, lang)) == text
            assert "demo" not in text.lower() and "prototype" not in text.lower()
            # no dosing, no promises, no invented diagnosis
            assert "mg" not in text.lower() and "guarantee" not in text.lower()
            assert "cure" not in text.lower()


def test_safety_rejects_dosing_promises_and_diagnoses_not_in_the_record():
    ctx = _ctx()
    assert safety_check("Please take 500 mg metformin twice daily.", ctx) is None
    assert safety_check("We guarantee you will be cured.", ctx) is None
    assert safety_check("You have breast cancer and should start chemotherapy.", ctx) is None
    # a diagnosis that IS in the facts (diabetes is in the programme name) may pass if nothing else fails
    ok = safety_check("This is about your diabetes screening result. Please contact the clinic.", ctx)
    assert ok is not None
    # a claimed already-sent message is refused
    assert safety_check("We have sent the referral and closed the case.", ctx) is None


def test_template_fallback_when_model_is_off(no_http):
    out = copilot.run(_ctx(), task="outreach", language="en", use_model=False)
    assert out["origin"] == "template" and out["text"] and out["error"] is None
    assert "follow-up team" in out["text"] or "screening" in out["text"].lower()
    assert out["sources"] and out["disclaimer"]
    assert "not a diagnosis" in out["text"].lower() or "not a new diagnosis" in out["text"].lower()


def test_template_fallback_when_model_fails_safety(slm):
    slm["reply"]["text"] = "Start 10 mg of drug X tonight, you will be cured."
    out = copilot.run(_ctx(), task="outreach", language="en", use_model=True)
    assert slm["calls"], "the model was asked"
    assert out["origin"] == "template" and out["text"]
    assert "mg" not in out["text"] and "cured" not in out["text"]


def test_model_success_is_labelled_and_uses_langgraph(slm):
    out = copilot.run(_ctx(), task="outreach", language="en", use_model=True)
    assert slm["calls"]
    payload = slm["calls"][0][2]
    assert payload["messages"] and payload["model"]
    assert out["origin"] == "model" and out["text"]
    assert "clinic" in out["text"].lower()
    # the compiled graph is a LangGraph
    g = copilot.graph()
    assert g is not None and callable(getattr(g, "invoke", None))


def test_kill_switch_off_still_returns_a_template(client, as_user, new_case, no_http):
    h = as_user("admin")
    c = new_case()
    try:
        assert client.post("/api/admin/system/ml-switch", headers=h,
                           json={"enabled": False, "reason": "copilot template fallback check"}).status_code == 200
        r = client.post("/api/assist/copilot", headers=as_user("aisha.rahman"),
                        json={"case_id": c["id"], "task": "outreach", "language": "en"})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["text"] and d["origin"] == "template"
        assert d["disclaimer"]
    finally:
        assert client.post("/api/admin/system/ml-switch", headers=h,
                           json={"enabled": True, "reason": "restore after the copilot check"}).status_code == 200


def test_api_is_staff_only_and_never_sends_or_moves_the_case(client, as_user, new_case, db, no_http):
    c = new_case()
    cid = c["id"]
    before = db.get(Case, cid)
    state, due = before.state, before.due_at
    comms = db.query(Communication).filter(Communication.case_id == cid).count()
    assert client.post("/api/assist/copilot", json={"case_id": cid, "task": "outreach"}).status_code == 401
    assert client.post("/api/assist/copilot", headers=as_user("patient.en"),
                       json={"case_id": cid, "task": "outreach"}).status_code == 403
    r = client.post("/api/assist/copilot", headers=as_user("aisha.rahman"),
                    json={"case_id": cid, "task": "summary", "language": "en"})
    assert r.status_code == 200
    d = r.json()
    assert d["text"] and d["task"] == "summary" and d["origin"] in ("template", "model")
    assert "factual" in d["text"].lower() or "urgency" in d["text"].lower() or "step" in d["text"].lower()
    db.expire_all()
    after = db.get(Case, cid)
    assert after.state == state and after.due_at == due
    assert db.query(Communication).filter(Communication.case_id == cid).count() == comms
    # audited without storing the draft text
    ev = db.query(AuditEvent).filter(AuditEvent.event == "copilot.requested",
                                     AuditEvent.case_id == cid).order_by(AuditEvent.id.desc()).first()
    assert ev is not None
    assert ev.detail.get("task") == "summary" and ev.detail.get("returned") is True
    blob = str(ev.detail) + (ev.detail.get("text") or "")
    assert d["text"] not in blob


def test_letter_in_arabic_hindi_urdu(no_http):
    for lang, needle in (("ar", "ليست"), ("hi", "नहीं"), ("ur", "نہیں")):
        out = copilot.run(_ctx(), task="simplify_letter", language=lang, use_model=False)
        assert out["text"] and needle in out["text"]
        assert safety_check(out["text"], _ctx("simplify_letter", lang)) == out["text"]


def test_gather_strips_identifiers(db, new_case):
    c = db.get(Case, new_case()["id"])
    p = c.patient
    p.given_name, p.family_name, p.phone = "SecretGiven", "SecretFamily", "+971501234567"
    db.flush()
    ctx = copilot.gather(db, c, task="outreach", language="en", staff_names=["Dr Secret"])
    blob = str(ctx)
    assert "SecretGiven" not in blob and "SecretFamily" not in blob
    assert "+971501234567" not in blob and "Secret" not in blob
    assert "programme" in ctx["sources"]
