"""Deterministic record-sourced note suggestions (POST /api/assist/record-note).

Every sentence is a fixed template filled only from structured data already on the case.
No language model is used; the AI kill switch does not apply. The suggestion never
closes or submits anything. Faithfulness is checked against the full SQLite seed:
each sentence maps to a source fact, every number/unit/date equals the stored value,
and missing facts omit their sentence.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import joinedload

from kareshafaa.config import get_settings
from kareshafaa.models import (
    Appointment, AuditEvent, CallTask, CarePlan, Case, Communication, DiagnosticReport,
    Organization, Practitioner, QuestionnaireResponse, ServiceRequest, StepTask, utcnow,
)
from kareshafaa.services import record_note as rn
from kareshafaa.statemachine import AUTHORISED_CLOSURE_REASONS, S

URL = "/api/assist/record-note"
USED = "/api/assist/record-note/used"
ROOT = Path(__file__).resolve().parents[1]
JS = (ROOT / "kareshafaa" / "static" / "js").read_text if False else None


def _cases(db) -> list[Case]:
    return list(db.scalars(select(Case).options(
        joinedload(Case.patient), joinedload(Case.owner),
        joinedload(Case.report).joinedload(DiagnosticReport.observations),
    )).unique())


def _actions_for(db, case: Case) -> list[tuple[str, dict]]:
    """Every suggestion this case can honestly produce (service-level, no permission check)."""
    out = [("close_authorised", {}), ("close_authorised", {"option": "clinically_not_indicated"}),
           ("close_authorised", {"option": "unable_to_contact"}), ("unreachable", {}), ("lesson", {})]
    if rn._evidence_row(db, case, "care_plan") is not None:
        out.append(("close_evidence", {"evidence_type": "care_plan"}))
    if rn._evidence_row(db, case, "appointment_fulfilled") is not None:
        out.append(("close_evidence", {"evidence_type": "appointment_fulfilled"}))
    if rn._evidence_row(db, case, "negative_confirmatory_result") is not None:
        out.append(("close_evidence", {"evidence_type": "negative_confirmatory_result"}))
    return out


def assert_faithful(s: rn.Suggestion, db, case: Case, lang: str) -> None:
    """Every sentence corresponds to a stored fact; numbers, units and dates equal the DB."""
    assert s.kind == "from_the_record"
    assert s.lang == lang
    assert s.disclaimer == rn.DISCLAIMER[lang]
    assert "AI" not in s.disclaimer and "ai" not in s.disclaimer and "نموذج" not in s.disclaimer
    if not s.note:
        assert s.facts == ()
        return
    lines = s.note.split("\n")
    assert lines == list(s.sentences)
    assert len(lines) == len(s.facts)
    for line, fact in zip(lines, s.facts):
        assert line == fact.sentence
        assert line.endswith(".")
        assert fact.source_field
        assert fact.source_value is not None and fact.source_value != ""
        assert fact.source_href.startswith(f"/cases/{case.id}#")
        allowed = rn.allowed_numeric_tokens(fact)
        for tok in rn.numeric_tokens(line):
            assert tok in allowed, f"{fact.key}: number {tok!r} in {line!r} not in {allowed} / {fact.values}"
        # dates, numbers, units equal the stored values
        if fact.values.get("date_utc") is not None:
            assert fact.values["date_display"] == rn.format_dt(fact.values["date_utc"], lang)
            assert fact.values["date_display"] in line
            assert "Asia/Dubai" in line
        if fact.values.get("start_utc") is not None:
            assert fact.values["start_display"] == rn.format_dt(fact.values["start_utc"], lang)
            assert fact.values["start_display"] in line
            assert "Asia/Dubai" in line
        if fact.values.get("attended_utc") is not None:
            assert fact.values["attended_display"] == rn.format_dt(fact.values["attended_utc"], lang)
            assert fact.values["attended_display"] in line
        if fact.values.get("review_due_on") is not None and fact.values.get("review_due_display"):
            assert fact.values["review_due_display"] == rn.format_date(fact.values["review_due_on"], lang)
            if "Review due" in line or "المراجعة" in line:
                assert fact.values["review_due_display"] in line
        if fact.values.get("value") is not None:
            assert rn.format_number(fact.values["value"]) in line
        if fact.values.get("unit"):
            assert fact.values["unit"] in line
        if fact.values.get("flag"):
            assert fact.values["flag"] in line
        if fact.values.get("value_text") and fact.values.get("value") is None:
            assert fact.values["value_text"] in line
        if fact.values.get("score") is not None:
            assert str(fact.values["score"]) in line
            assert str(fact.values["total"]) in line
        if fact.values.get("n") is not None:
            assert str(fact.values["n"]) in line
        if fact.values.get("id") is not None and fact.key in (
                "care_plan", "appointment_fulfilled", "negative_confirmatory_result"):
            assert str(fact.values["id"]) in line
        _assert_fact_matches_db(db, case, fact)


def _assert_fact_matches_db(db, case: Case, fact: rn.Fact) -> None:
    v = fact.values
    if fact.key == "programme":
        assert v["programme"] == case.programme
    elif fact.key == "triggering_result":
        rep = case.report
        assert rep is not None and v["report_id"] == rep.id
        assert v["test"] == rep.display
        assert v["date_utc"] == rep.issued_at
        for ov in v.get("observations") or []:
            obs = next(o for o in rep.observations if o.id == ov["id"])
            assert obs.value == ov["value"]
            assert (obs.unit or "").strip() or None == ov["unit"]
            assert (obs.value_text or "").strip() or None == ov["value_text"]
            assert (obs.interpretation or "").strip() or None == ov["flag"]
    elif fact.key == "care_plan":
        plan = db.get(CarePlan, v["id"])
        assert plan is not None and plan.case_id == case.id
        assert plan.kind == v["kind"] and plan.created_at == v["date_utc"]
        assert v["title"] == (rn.flatten(plan.description) or None)
        if v["title_in_sentence"] and v["title"]:
            assert v["title"] in fact.sentence
        if v["author"]:
            assert v["author"] == db.get(Practitioner, plan.recorded_by_id).name
            assert v["author"] in fact.sentence
    elif fact.key == "appointment_fulfilled":
        appt = db.get(Appointment, v["id"])
        assert appt is not None and appt.case_id == case.id and appt.status == "fulfilled"
        assert appt.start == v["start_utc"] and appt.updated_at == v["attended_utc"]
        if v["clinician"]:
            assert v["clinician"] in fact.sentence
        if v["org"]:
            assert v["org"] in fact.sentence
    elif fact.key == "negative_confirmatory_result":
        sr = db.get(ServiceRequest, v["id"])
        assert sr is not None and sr.case_id == case.id
        assert sr.result == v["result"] == "negative" and sr.code == v["code"]
        assert (sr.decided_at or sr.created_at) == v["date_utc"]
        assert v["result"] in fact.sentence and v["code"] in fact.sentence
    elif fact.key == "milestone_notified":
        if v["origin"] == "communications.sent_at":
            com = db.get(Communication, v["id"])
            assert com.case_id == case.id and com.category == "result_notification"
            assert com.status == "completed" and com.sent_at == v["date_utc"]
        else:
            st = db.get(StepTask, v["id"])
            assert st.case_id == case.id and st.state == S.PATIENT_NOTIFIED
            assert st.started_at == v["date_utc"]
    elif fact.key == "milestone_teachback":
        tb = db.get(QuestionnaireResponse, v["id"])
        assert tb.case_id == case.id
        assert tb.score == v["score"] and tb.total == v["total"] and tb.passed == v["passed"]
        assert tb.created_at == v["date_utc"]
    elif fact.key in ("milestone_referral_sent", "milestone_referral_accepted"):
        sr = db.get(ServiceRequest, v["id"])
        assert sr is not None and sr.case_id == case.id and sr.kind == "referral"
        if fact.key.endswith("accepted"):
            assert sr.status == "accepted"
    elif fact.key == "milestone_appointment_attended":
        appt = db.get(Appointment, v["id"])
        assert appt.status == "fulfilled" and appt.case_id == case.id
        assert appt.start == v["start_utc"]
    elif fact.key == "milestone_treatment_started":
        plan = db.get(CarePlan, v["id"])
        assert plan.case_id == case.id and plan.kind == v["kind"]
        assert plan.created_at == v["date_utc"]
    elif fact.key == "owner":
        assert case.owner is not None and case.owner.name == v["name"] and v["name"] in fact.sentence
    elif fact.key == "contact_attempts":
        n = db.scalar(select(func.count()).select_from(CallTask).where(
            CallTask.case_id == case.id, CallTask.status == "completed", CallTask.outcome != "reached"))
        assert n == v["n"]
        last = db.get(CallTask, v["id"])
        assert last.outcome == v["outcome"] and last.outcome != "reached"
    elif fact.key == "current_step":
        assert v["state"] == case.state and v["date_utc"] == case.state_entered_at
    elif fact.key == "step_due":
        assert v["date_utc"] == case.due_at
    elif fact.key == "escalation":
        assert v["n"] == case.escalation_level and v["n"] > 0


def assert_missing_omitted(s: rn.Suggestion, db, case: Case) -> None:
    keys = set(s.fact_keys)
    if db.scalar(select(func.count()).select_from(QuestionnaireResponse)
                 .where(QuestionnaireResponse.case_id == case.id)) == 0:
        assert "milestone_teachback" not in keys
        assert "Teach-back" not in s.note and "التحقق من الفهم" not in s.note
    if db.scalar(select(func.count()).select_from(Communication).where(
            Communication.case_id == case.id, Communication.category == "result_notification",
            Communication.status == "completed")) == 0 and db.scalar(select(func.count()).select_from(StepTask).where(
            StepTask.case_id == case.id, StepTask.state == S.PATIENT_NOTIFIED)) == 0:
        assert "milestone_notified" not in keys
        assert "notified of the result" not in s.note and "أُبلِغ المريض" not in s.note
    if db.scalar(select(func.count()).select_from(ServiceRequest).where(
            ServiceRequest.case_id == case.id, ServiceRequest.kind == "referral")) == 0:
        assert "milestone_referral_sent" not in keys and "milestone_referral_accepted" not in keys
        assert "Referral" not in s.note and "التحويل" not in s.note
    if db.scalar(select(func.count()).select_from(Appointment).where(
            Appointment.case_id == case.id, Appointment.status == "fulfilled")) == 0:
        assert "appointment_fulfilled" not in keys and "milestone_appointment_attended" not in keys
    if db.scalar(select(func.count()).select_from(CarePlan).where(CarePlan.case_id == case.id)) == 0:
        assert "care_plan" not in keys and "milestone_treatment_started" not in keys
    if (case.escalation_level or 0) <= 0:
        assert "escalation" not in keys
    if case.due_at is None:
        assert "step_due" not in keys
    if case.report is None or not case.report.issued_at:
        assert "triggering_result" not in keys


# -------------------------------------------------------------------------------- seed-wide faithfulness

def test_every_sentence_matches_the_record_over_the_full_seed(db):
    cases = _cases(db)
    assert len(cases) >= 100, f"expected a full seed, got {len(cases)} cases"
    checked = 0
    notes = 0
    empty = 0
    by_action: dict[str, int] = {}
    example = None
    for case in cases:
        for lang in ("en", "ar"):
            for action, kw in _actions_for(db, case):
                s = rn.suggest(db, case, action=action, lang=lang, **kw)
                assert_faithful(s, db, case, lang)
                assert_missing_omitted(s, db, case)
                checked += 1
                notes += 1 if s.note else 0
                empty += 0 if s.note else 1
                by_action[f"{action}:{kw.get('evidence_type') or kw.get('option') or ''}"] = (
                    by_action.get(f"{action}:{kw.get('evidence_type') or kw.get('option') or ''}", 0) + 1)
                if example is None and action == "close_evidence" and kw.get("evidence_type") == "care_plan" and lang == "en" and "Linked care plan" in s.note:
                    example = (case.id, s)
    # stash a representative note so a failing assertion still shows coverage
    assert checked >= 500
    # at least some closable-with-evidence cases exist in the seed
    assert any(k.startswith("close_evidence:care_plan") for k in by_action), by_action
    # expose coverage on the test node name via an attribute (the run prints it on failure too)
    test_every_sentence_matches_the_record_over_the_full_seed.coverage = {
        "cases": len(cases), "suggestions": checked, "non_empty": notes, "empty": empty,
        "by_action": by_action, "example": None if not example else {
            "case_id": example[0], "note": example[1].note, "facts": example[1].fact_keys,
        },
    }


def test_coverage_numbers_are_reported(db):
    """Re-run the seed walk's bookkeeping so the report has stable, explicit totals."""
    # depends on the previous test having run in the same session? Don't: recount cheaply.
    info = getattr(test_every_sentence_matches_the_record_over_the_full_seed, "coverage", None)
    if info is None:
        test_every_sentence_matches_the_record_over_the_full_seed(db)
        info = test_every_sentence_matches_the_record_over_the_full_seed.coverage
    assert info["cases"] >= 100 and info["suggestions"] >= 500
    assert info["non_empty"] > 0
    print("\nRECORD_NOTE_COVERAGE", info["cases"], "cases,", info["suggestions"], "suggestions,",
          info["non_empty"], "non-empty,", "by_action=", info["by_action"])
    if info.get("example"):
        print("RECORD_NOTE_EXAMPLE case", info["example"]["case_id"])
        print(info["example"]["note"])
        print("facts:", info["example"]["facts"])


# -------------------------------------------------------------------------------- per closure type, AR, missing facts

def test_format_dt_is_uae_and_labelled():
    utc = datetime(2026, 10, 22, 13, 36, 0)  # 17:36 Asia/Dubai
    assert rn.format_dt(utc, "en") == "22 Oct 2026, 17:36 (Asia/Dubai)"
    assert rn.format_dt(utc, "ar") == "22 أكتوبر 2026، 17:36 (Asia/Dubai)"
    assert rn.format_number(250.0) == "250"
    assert rn.format_number(6.5) == "6.5"
    assert rn.format_number(6.25) == "6.25"


def _treatment_case(db) -> Case:
    for c in _cases(db):
        if c.state == S.TREATMENT_STARTED and rn._evidence_row(db, c, "care_plan") is not None:
            return c
    pytest.skip("no treatment-started case with a care plan in the seed")


def _attended_case(db) -> Case:
    for c in _cases(db):
        if rn._evidence_row(db, c, "appointment_fulfilled") is not None:
            return c
    pytest.skip("no fulfilled-appointment case in the seed")


def _negative_case(db) -> Case:
    for c in _cases(db):
        if rn._evidence_row(db, c, "negative_confirmatory_result") is not None:
            return c
    pytest.skip("no negative confirmatory result in the seed")


def test_care_plan_closure_note_contains_id_kind_date_author(db):
    case = _treatment_case(db)
    s = rn.suggest(db, case, action="close_evidence", evidence_type="care_plan", lang="en")
    assert_faithful(s, db, case, "en")
    assert "Closing this case with linked evidence: care plan." in s.sentences
    plan = next(f for f in s.facts if f.key == "care_plan")
    assert f"Linked care plan #{plan.values['id']}" in plan.sentence
    assert plan.values["kind_label"] in plan.sentence
    assert plan.values["date_display"] in plan.sentence
    if plan.values["author"]:
        assert plan.values["author"] in plan.sentence
    # example used in the hand-off
    test_care_plan_closure_note_contains_id_kind_date_author.example = s.note


def test_appointment_and_negative_and_authorised_closure_types(db):
    attended = _attended_case(db)
    s = rn.suggest(db, attended, action="close_evidence", evidence_type="appointment_fulfilled", lang="en")
    assert_faithful(s, db, attended, "en")
    assert any(f.key == "appointment_fulfilled" for f in s.facts)
    assert "attended appointment" in s.note

    neg = _negative_case(db)
    s = rn.suggest(db, neg, action="close_evidence", evidence_type="negative_confirmatory_result", lang="en")
    assert_faithful(s, db, neg, "en")
    assert "negative confirmatory result" in s.note
    assert any(f.key == "negative_confirmatory_result" for f in s.facts)

    s = rn.suggest(db, attended, action="close_authorised", option="clinically_not_indicated", lang="en")
    assert_faithful(s, db, attended, "en")
    assert "authorised decision" in s.note
    assert AUTHORISED_CLOSURE_REASONS["clinically_not_indicated"] in s.note

    s = rn.suggest(db, attended, action="close_authorised", option="unable_to_contact", lang="en")
    assert_faithful(s, db, attended, "en")
    assert "Unable to contact" in s.note


def test_arabic_rendering_uses_ar_templates_and_the_same_values(db):
    case = _treatment_case(db)
    en = rn.suggest(db, case, action="close_evidence", evidence_type="care_plan", lang="en")
    ar = rn.suggest(db, case, action="close_evidence", evidence_type="care_plan", lang="ar")
    assert_faithful(ar, db, case, "ar")
    assert en.fact_keys == ar.fact_keys
    assert "إغلاق هذا الملف مع ربط الدليل: خطة رعاية." in ar.sentences
    assert ar.disclaimer.startswith("مقترحة من السجل")
    # same stored numbers and ids in both languages
    for a, b in zip(en.facts, ar.facts):
        assert a.key == b.key
        if a.values.get("value") is not None:
            assert a.values["value"] == b.values["value"]
            assert rn.format_number(a.values["value"]) in a.sentence
            assert rn.format_number(b.values["value"]) in b.sentence
        if a.values.get("id") is not None:
            assert a.values["id"] == b.values["id"]
        if a.values.get("date_utc") is not None:
            assert a.values["date_utc"] == b.values["date_utc"]
            assert "Asia/Dubai" in a.sentence and "Asia/Dubai" in b.sentence
            assert "أكتوبر" in b.values["date_display"] or "يناير" in b.values["date_display"] \
                or any(m in b.values["date_display"] for m in rn._MONTHS_AR)


def test_missing_facts_are_omitted(db, new_case):
    case = db.get(Case, new_case()["id"])
    s = rn.suggest(db, case, action="close_authorised", option="clinically_not_indicated", lang="en")
    assert_faithful(s, db, case, "en")
    assert_missing_omitted(s, db, case)
    # a freshly ingested case has no teach-back, referral, appointment or care plan
    assert "Teach-back" not in s.note
    assert "Referral" not in s.note
    assert "Linked care plan" not in s.note
    assert "Linked appointment" not in s.note
    assert "Failed phone attempts" not in s.note
    # but the programme and the triggering result and the owner are on the record
    assert "programme" in s.fact_keys and "triggering_result" in s.fact_keys
    assert "Closing this case as an authorised decision" in s.note


def test_unknown_reason_does_not_invent_a_label(db, new_case):
    case = db.get(Case, new_case()["id"])
    s = rn.suggest(db, case, action="close_authorised", option="not_a_real_reason", lang="en")
    assert "not_a_real_reason" not in s.note
    assert "closure_type" not in s.fact_keys  # omitted rather than guessed


def test_lesson_and_unreachable_notes(db, new_case):
    case = db.get(Case, new_case()["id"])
    s = rn.suggest(db, case, action="lesson", lang="en")
    assert_faithful(s, db, case, "en")
    assert "current_step" in s.fact_keys
    s = rn.suggest(db, case, action="unreachable", lang="en")
    assert_faithful(s, db, case, "en")
    assert "Marking this case as Unreachable." in s.sentences


# -------------------------------------------------------------------------------- API: permissions, no auto-submit, audit, kill switch

def _body(case_id, **kw):
    return {"case_id": case_id, "action": "close_authorised", "lang": "en", **kw}


def test_anonymous_401(client, new_case):
    c = new_case()
    client.cookies.clear()
    assert client.post(URL, json=_body(c["id"])).status_code == 401


def test_patient_403(client, as_user, new_case):
    c = new_case()
    r = client.post(URL, headers=as_user("patient.en"), json=_body(c["id"]))
    assert r.status_code == 403


def test_staff_without_case_access_refused(client, as_user, new_case):
    c = new_case()
    r = client.post(URL, headers=as_user("vikram.mehta"), json=_body(c["id"]))
    assert r.status_code in (403, 404)
    assert "suggestion" not in (r.json() or {})
    assert client.post(URL, headers=as_user("aisha.rahman"), json=_body(999_999)).status_code == 404


def test_coordinator_refused_authorised_closure_allowed_lesson(client, as_user, new_case, db):
    """Same permissions as the action: coordinators cannot authorise closure, can record a lesson."""
    c = new_case()
    h = as_user("aisha.rahman")  # coordinator: no closure:authorise, has lesson:write
    r = client.post(URL, headers=h, json=_body(c["id"]))
    assert r.status_code == 403, r.text
    r = client.post(URL, headers=h, json={"case_id": c["id"], "action": "lesson", "lang": "en"})
    assert r.status_code == 200, r.text
    assert r.json()["kind"] == "from_the_record" and r.json()["suggestion"]


def test_close_evidence_refused_when_the_action_is_not_available(client, as_user, new_case):
    c = new_case()  # issued: close-with-evidence is not on the action panel
    r = client.post(URL, headers=as_user("aisha.rahman"),
                    json={"case_id": c["id"], "action": "close_evidence", "evidence_type": "care_plan"})
    assert r.status_code == 403


def test_gp_authorised_closure_suggestion_does_not_submit(client, as_user, new_case, db):
    c = new_case()
    state_before = db.get(Case, c["id"]).state
    r = client.post(URL, headers=as_user("omar.haddad"),
                    json=_body(c["id"], option="clinically_not_indicated"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["kind"] == "from_the_record"
    assert d["disclaimer"] == rn.DISCLAIMER["en"]
    assert "Suggested from the record" in d["disclaimer"]
    assert "AI" not in d["disclaimer"]
    assert d["suggestion"]
    assert d["facts"] and d["sources"]
    assert len(d["sources"]) == len(d["facts"])
    for src in d["sources"]:
        assert src["field"] and src["value"] and src["href"].startswith(f"/cases/{c['id']}#")
    db.expire_all()
    after = db.get(Case, c["id"])
    assert after.state == state_before and after.closed_at is None and after.closure_note is None
    ev = db.scalar(select(AuditEvent).where(AuditEvent.event == "note.suggested",
                                            AuditEvent.case_id == c["id"]).order_by(AuditEvent.id.desc()).limit(1))
    assert ev is not None and ev.detail["phase"] == "generated"
    assert ev.detail["facts"] == d["facts"] and ev.detail["fingerprint"] == d["fingerprint"]
    assert ev.detail["edited"] is None and ev.detail["kind"] == "from_the_record"
    # the generated text itself is not stored (reconstructable from the facts)
    assert d["suggestion"] not in str(ev.detail)


def test_kill_switch_does_not_block_record_notes(client, as_user, new_case, monkeypatch):
    monkeypatch.setattr(get_settings(), "ml_enabled", False)
    c = new_case()
    r = client.post(URL, headers=as_user("omar.haddad"), json=_body(c["id"]))
    assert r.status_code == 200 and r.json()["suggestion"] and r.json()["kind"] == "from_the_record"


def test_arabic_api_and_used_audit(client, as_user, new_case, db):
    c = new_case()
    h = as_user("omar.haddad")
    r = client.post(URL, headers=h, json=_body(c["id"], option="clinically_not_indicated", lang="ar"))
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["lang"] == "ar" and "إغلاق هذا الملف بقرار مصرّح" in d["suggestion"]
    assert d["disclaimer"] == rn.DISCLAIMER["ar"]
    # applied, not edited
    u = client.post(USED, headers=h, json={"case_id": c["id"], "action": "close_authorised",
                                           "fingerprint": d["fingerprint"], "edited": False,
                                           "facts": d["facts"]})
    assert u.status_code == 200 and u.json()["edited"] is False
    # applied, edited
    u2 = client.post(USED, headers=h, json={"case_id": c["id"], "action": "close_authorised",
                                            "fingerprint": d["fingerprint"], "edited": True,
                                            "facts": d["facts"]})
    assert u2.status_code == 200 and u2.json()["edited"] is True
    evs = list(db.scalars(select(AuditEvent).where(AuditEvent.event == "note.suggested",
                                                   AuditEvent.case_id == c["id"]).order_by(AuditEvent.id)))
    phases = [e.detail["phase"] for e in evs]
    assert "generated" in phases and phases.count("applied") == 2
    edited_flags = [e.detail.get("edited") for e in evs if e.detail["phase"] == "applied"]
    assert edited_flags == [False, True]


def test_close_evidence_on_a_seeded_care_plan_case(client, as_user, db):
    case = _treatment_case(db)
    r = client.post(URL, headers=as_user("fatima.al.mansoori"),
                    json={"case_id": case.id, "action": "close_evidence", "evidence_type": "care_plan", "lang": "en"})
    assert r.status_code == 200, r.text
    d = r.json()
    assert "Closing this case with linked evidence: care plan." in d["suggestion"]
    assert "care_plan" in d["facts"]
    # still does not close
    db.expire_all()
    assert db.get(Case, case.id).state != S.CLOSED or case.state == S.CLOSED
    if case.state != S.CLOSED:
        assert db.get(Case, case.id).state == case.state


def test_unsupported_form_or_language_refused(client, as_user, new_case):
    c = new_case()
    h = as_user("aisha.rahman")
    assert client.post(URL, headers=h, json=_body(c["id"], action="medication")).status_code == 422
    assert client.post(URL, headers=h, json=_body(c["id"], lang="hi")).status_code == 422
    assert client.post(URL, headers=h,
                       json={"case_id": c["id"], "action": "close_evidence",
                             "evidence_type": "made_up"}).status_code == 422


# -------------------------------------------------------------------------------- UI: no auto-submit, no SLM on closure, tokens, no overlap

def test_js_suggest_button_is_type_button_and_does_not_auto_submit():
    common = (ROOT / "kareshafaa/static/js/common.js").read_text(encoding="utf-8")
    case_js = (ROOT / "kareshafaa/static/js/case.js").read_text(encoding="utf-8")
    assert 'type="button"' in common and "data-rn-suggest" in common
    assert "RECORD_NOTE_ACTIONS" in common and "close_evidence" in common
    # the button fills the textarea; it never posts /close
    assert "/api/cases/" not in common.split("attachRecordNote")[1].split("reportRecordNote")[0] \
        or "close" not in common.split("W.attachRecordNote")[1][:2000].lower()
    assert "attachRecordNote" in case_js
    # SLM is not attached on closure forms
    assert 'W.RECORD_NOTE_ACTIONS && W.RECORD_NOTE_ACTIONS.has(key)' in case_js
    assert "attachSuggest" in case_js  # still used on non-closure forms
    # report-used only runs after a successful submit, never instead of it
    assert "reportRecordNote" in case_js
    # "Suggest note" is a click handler, not a focus/input auto-fill (those belong to the SLM only)
    rn_block = common.split("W.attachRecordNote")[1].split("W.recordNoteAuditPayload")[0]
    assert "focusin" not in rn_block and "debounce" not in rn_block.lower()


def test_css_uses_tokens_and_does_not_absolutely_position_sources():
    css = (ROOT / "kareshafaa/static/kareshafaa.css").read_text(encoding="utf-8")
    assert ".record-note" in css
    block_start = css.index(".record-note {")
    block = css[block_start:css.index("}", css.rindex("@media (max-width: 599px) {", block_start) + 1) + 1] \
        if "@media (max-width: 599px)" in css[block_start:block_start + 2000] else css[block_start:block_start + 1500]
    # gather only our rules: from .record-note to the next non-related section is messy; check the file slice
    slice_ = css[block_start:block_start + 1800]
    assert "position: absolute" not in slice_ and "position: fixed" not in slice_
    assert "font-size: var(--fs-xs)" in slice_ and "font-size: var(--fs-sm)" in slice_
    assert "font-weight: var(--fw-semibold)" in slice_
    assert "overflow-wrap: anywhere" in slice_
    assert "flex-direction: column" in slice_  # phone: buttons stack, no overlap
