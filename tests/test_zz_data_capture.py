"""Data capture: every staff and patient form field is stored and shown back.

Each write sends a unique Unicode sentinel (Arabic + accented Latin) and checks three things:
  1. the request succeeds (no silent 4xx/5xx);
  2. the exact value is stored in a clinical table, not only in the audit log (no dropped or truncated field);
  3. the value comes back in the read API the page renders from (case detail / clinical record / list).
The browser version of the same walk-through is scripts/ui_capture_check.py (desktop + mobile).
Named test_zz_* so it runs last: it acknowledges a seeded escalation and signs off content in the shared test DB.
"""
from __future__ import annotations

import itertools
from datetime import timedelta

import pytest
from sqlalchemy import JSON, String, Text, select, text

from kareshafaa.db import Base
from kareshafaa.models import Appointment, Case, Escalation, Patient, QuestionnaireResponse, User, utcnow

_n = itertools.count(1)


def tok(name: str) -> str:
    return f"ZC{next(_n)}{name.replace('_', '')}Z ملاحظة عربية é"


def _stored_in(db, value: str) -> list[str]:
    """Clinical (non-audit) columns that hold the exact value."""
    db.expire_all()
    key = value.split(" ")[0]
    hits = []
    for tbl in Base.metadata.sorted_tables:
        if tbl.name.startswith("audit"):
            continue
        for col in tbl.columns:
            if isinstance(col.type, (String, Text, JSON)):
                v = db.execute(text(f'SELECT CAST("{col.name}" AS TEXT) FROM "{tbl.name}" WHERE CAST("{col.name}" AS TEXT) LIKE :p LIMIT 1'),
                               {"p": f"%{key}%"}).scalar()
                if v is not None and (value in v or value in v.encode().decode("unicode_escape", "ignore")):
                    hits.append(f"{tbl.name}.{col.name}")
    return hits


def captured(client, db, r, value, *reads, h):
    assert r.status_code < 400, f"{r.request.url} -> {r.status_code} {r.text[:300]}"
    where = _stored_in(db, value)
    assert where, f"{value!r} not stored in any clinical table (dropped or truncated)"
    bodies = [client.get(u, headers=h).text for u in reads]
    import json as _j
    assert any(value in b or value in _j.dumps(_j.loads(b), ensure_ascii=False) for b in bodies), f"{value!r} stored in {where} but not shown back by {reads}"
    return where


def P(client, h, url, **body):
    return client.post(url, headers=h, json=body)


@pytest.fixture
def staff(as_user):
    return {k: as_user(u) for k, u in (("co", "aisha.rahman"), ("gp", "omar.haddad"), ("sp", "vikram.mehta"), ("lead", "fatima.al.mansoori"))}


def test_case_pathway_forms_capture_every_field(client, db, staff, new_case):
    co, gp, sp, lead = staff["co"], staff["gp"], staff["sp"], staff["lead"]
    cid = new_case("colorectal_fit", 250)["id"]; u = f"/api/cases/{cid}"; rd = (u, f"{u}/record")
    assert P(client, co, f"{u}/notify", category="result_notification").status_code < 400
    t = tok("call"); captured(client, db, P(client, co, f"{u}/call", outcome="reached", note=t), t, *rd, h=co)
    r = P(client, co, f"{u}/teach-back", answers={"q1": "a", "q2": "b", "q3": "a"}, confidence=4, language="ar"); assert r.status_code < 400
    qr = db.scalar(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == cid).order_by(QuestionnaireResponse.id.desc()))
    assert qr.confidence == 4 and qr.language == "ar" and qr.answers == {"q1": "a", "q2": "b", "q3": "a"}
    t = tok("consent"); captured(client, db, P(client, co, f"{u}/consent", scope="share_external", decision="permit", note=t), t, *rd, h=co)
    t = tok("lesson"); captured(client, db, P(client, co, f"{u}/lessons", kind="overdue", reason_code="capacity", detail=t), t, *rd, h=co)
    t = tok("transition"); captured(client, db, P(client, gp, f"{u}/transition", to_state="consultation", note=t), t, *rd, h=gp)
    t = tok("consultq"); r = P(client, gp, f"{u}/consults", target_practitioner_id=31, urgency="high", question=t); captured(client, db, r, t, *rd, h=gp)
    t = tok("consulta"); captured(client, db, P(client, sp, f"/api/consults/{r.json()['consult_id']}/respond", response=t), t, *rd, h=gp)
    t = tok("assign"); captured(client, db, P(client, co, f"{u}/assign", owner_id=19, reason=t), t, *rd, h=co)
    t = tok("refsummary"); captured(client, db, P(client, gp, f"{u}/referral", performer_org_id=9, performer_id=31, summary=t), t, *rd, h=gp)
    t = tok("refreject"); captured(client, db, P(client, sp, f"{u}/referral/decision", accept=False, reason_code="incomplete_info", note=t), t, *rd, h=gp)
    P(client, gp, f"{u}/referral", performer_org_id=9, performer_id=31, summary=tok("refsummary2"))
    t = tok("refaccept"); captured(client, db, P(client, sp, f"{u}/referral/decision", accept=True, note=t), t, *rd, h=gp)
    t = tok("booking"); captured(client, db, P(client, co, f"{u}/appointment", start=(utcnow() - timedelta(hours=1)).isoformat() + "Z", beyond_target_reason=t), t, *rd, h=co)
    r = P(client, sp, f"{u}/attendance", attended=False, barrier_code="transport"); assert r.status_code < 400
    db.expire_all(); assert db.scalar(select(Appointment.barrier_code).where(Appointment.case_id == cid).order_by(Appointment.id.desc())) == "transport"
    when = (utcnow() - timedelta(hours=1)).replace(microsecond=0)
    assert P(client, co, f"{u}/appointment", start=when.isoformat() + "+00:00").status_code < 400
    db.expire_all(); st = db.scalar(select(Appointment.start).where(Appointment.case_id == cid).order_by(Appointment.id.desc()))
    assert st.replace(tzinfo=None) == when.replace(tzinfo=None), "appointment time shifted"
    assert P(client, sp, f"{u}/attendance", attended=True).status_code < 400
    t = tok("careplan"); captured(client, db, P(client, sp, f"{u}/care-plan", kind="surveillance", description=t, review_due_on="2029-04-15"), t, *rd, h=sp)
    assert client.get(u, headers=sp).json()["care_plans"][-1]["review_due_on"] == "2029-04-15"
    t = tok("close"); captured(client, db, P(client, sp, f"{u}/close", closure_type="evidence", evidence_type="care_plan", note=t), t, *rd, h=sp)
    rc = client.get(u, headers=co).json()["recall"]
    assert rc["next_screening_due"] == "2029-04-15" and rc["recall"]["status"] == "due"
    t = tok("reopen"); captured(client, db, P(client, lead, f"{u}/reopen", note=t), t, *rd, h=lead)


def test_transfer_decline_reconsider_and_closure_notes(client, db, staff, new_case):
    co, gp, lead = staff["co"], staff["gp"], staff["lead"]
    cid = new_case("colorectal_fit", 250)["id"]; u = f"/api/cases/{cid}"; rd = (u, f"{u}/record")
    t = tok("transfer"); captured(client, db, P(client, co, f"{u}/transfer", to_owner_id=19, note=t), t, *rd, h=co)
    t = tok("transferok"); captured(client, db, P(client, gp, f"{u}/transfer/decision", accept=True, note=t), t, *rd, h=co)
    P(client, co, f"{u}/notify", category="result_notification"); P(client, co, f"{u}/call", outcome="reached", note="reached ok")
    t = tok("decline"); captured(client, db, P(client, co, f"{u}/decline", note=t + " patient said no thanks"), t, *rd, h=co)
    P(client, co, f"{u}/teach-back", answers={"q1": "a", "q2": "b", "q3": "a"}, confidence=3, language="en")
    t = tok("reconsider"); captured(client, db, P(client, gp, f"{u}/reconsider", note=t), t, *rd, h=gp)
    t = tok("closeauth"); captured(client, db, P(client, lead, f"{u}/close", closure_type="authorised_decision", reason="moved_abroad", note=t + " rationale"), t, *rd, h=lead)


def test_unreachable_review_death_and_escalation_notes(client, db, staff, new_case):
    co, gp, lead = staff["co"], staff["gp"], staff["lead"]
    cid = new_case("colorectal_fit", 250)["id"]; u = f"/api/cases/{cid}"
    P(client, co, f"{u}/notify", category="result_notification")
    P(client, co, f"{u}/call", outcome="no_answer", note="no answer 1"); P(client, co, f"{u}/call", outcome="no_answer", note="no answer 2")
    t = tok("unreachable"); captured(client, db, P(client, co, f"{u}/transition", to_state="unreachable", note=t), t, u, h=co)
    pid = db.scalar(select(Case.patient_id).where(Case.id == cid))
    c3 = client.post("/api/results", headers=co, json={"patient_id": pid, "programme": "colorectal_fit", "value": 250, "unit": "ug/g", "status": "preliminary"}).json()["case"]
    t = tok("review"); captured(client, db, P(client, gp, f"/api/cases/{c3['id']}/review", note=t + " reviewed", tier="urgent"), t, f"/api/cases/{c3['id']}", h=gp)
    c5 = new_case("colorectal_fit", 250)["id"]; u5 = f"/api/cases/{c5}"
    t = tok("death"); captured(client, db, P(client, lead, f"{u5}/patient-deceased", note=t + " registry"), t, u5, f"{u5}/record", h=lead)
    p = db.get(Patient, db.scalar(select(Case.patient_id).where(Case.id == c5))); assert p.deceased_recorded_at and p.deceased_recorded_by
    t = tok("deathfix"); captured(client, db, P(client, lead, f"{u5}/patient-deceased/revoke", note=t + " wrong patient"), t, f"{u5}/record", h=lead)
    esc = db.scalar(select(Escalation).where(Escalation.acknowledged_at.is_(None)).limit(1))
    if esc:
        t = tok("ack"); captured(client, db, P(client, lead, f"/api/escalations/{esc.id}/ack", note=t), t, f"/api/cases/{esc.case_id}", h=lead)


def test_admin_user_forms_capture_site_team_and_status_reason(client, db, as_user):
    adm = as_user("admin"); lst = "/api/admin/users"
    s1, t1 = tok("site"), tok("team")
    r = P(client, adm, lst, username=f"capture.user{next(_n)}", display_name="Ünïcode مستخدم", role="analyst", site=s1, team=t1, temporary_password="Cobalt-Harbor-Lantern-93")
    captured(client, db, r, s1, lst, h=adm); captured(client, db, r, t1, lst, h=adm)
    uid = r.json()["user"]["id"]
    s2 = tok("site2"); captured(client, db, P(client, adm, f"{lst}/{uid}/role", role="analyst", site=s2, team=""), s2, lst, h=adm)
    row = next(x for x in client.get(lst, headers=adm).json()["items"] if x["id"] == uid)
    assert row["display_name"] == "Ünïcode مستخدم" and not row.get("team")
    t = tok("deact"); captured(client, db, P(client, adm, f"{lst}/{uid}/deactivate", reason=t), t, lst, h=adm)
    t = tok("react"); captured(client, db, P(client, adm, f"{lst}/{uid}/activate", reason=t), t, lst, h=adm)
    db.expire_all(); usr = db.get(User, uid); assert usr.status_changed_at and usr.status_changed_by


def test_patient_question_language_teachback_and_decline(client, db, as_user):
    pt = as_user("patient.en")
    t = tok("ptq"); captured(client, db, P(client, pt, "/api/me/consult", question=t), t, "/api/me/case", h=pt)
    try:
        assert P(client, pt, "/api/me/display-language", language="ur").status_code < 400
        d = client.get("/api/me/case", headers=pt).json(); assert d["language"] == "ur"
        db.expire_all(); assert db.scalar(select(User.ui_language).where(User.username == "patient.en")) == "ur"
    finally:
        P(client, pt, "/api/me/display-language", language="en")
    ph = as_user("patient.hi"); d = client.get("/api/me/case", headers=ph).json()
    if d.get("can_teach_back"):
        r = P(client, ph, "/api/me/teach-back", answers={"q1": "a", "q2": "b", "q3": "a"}, confidence=5, language="hi"); assert r.status_code < 400, r.text
        sub = client.get("/api/me/case", headers=ph).json()["answers_lock"]["submitted"]
        assert sub["confidence"] == 5 and sub["answers"] == {"q1": "a", "q2": "b", "q3": "a"}
    pu = as_user("patient.ur")
    if client.get("/api/me/case", headers=pu).json().get("can_decline"):
        t = tok("ptdecline"); r = P(client, pu, "/api/me/decline", note=t); assert r.status_code < 400, r.text
        assert _stored_in(db, t)


def test_content_signoff_note_and_validation_errors(client, db, staff):
    lead = staff["lead"]
    items = client.get("/api/governance/content", headers=lead).json()["items"]
    todo = [i for i in items if i["readability"]["ok"] and not i["clinical_signoff"]]
    if todo:
        t = tok("signoff"); captured(client, db, P(client, lead, "/api/governance/content/signoff", content_id=todo[0]["content_id"], kind="clinical", note=t), t, "/api/governance/content", h=lead)
    # a rejected form names the field so the page can show it next to the kept values
    r = P(client, lead, "/api/governance/content/signoff", content_id=items[0]["content_id"], kind="clinical", note="x")
    assert r.status_code == 422 and r.json().get("errors")


def _name_tok() -> str:
    """Unique letters-only Unicode name (names may not hold digits)."""
    i, s = next(_n) + 7919, ""
    while i:
        i, r = divmod(i, 20)
        s += "bcdfghjklmnpqrstvwz"[r % 19]
    return f"Zé{s} عربي"


def _eid(year: int = 1979) -> str:
    import random
    from kareshafaa.services.patients import emirates_id_check_digit
    b = f"784{year}{random.randint(0, 9_999_999):07d}"
    return f"{b[:3]}-{b[3:7]}-{b[7:14]}-{emirates_id_check_digit(b)}"


def test_patient_registration_and_record_forms_capture_every_field(client, db, staff):
    co = staff["co"]
    given, family, addr, city = _name_tok(), _name_tok(), tok("address"), tok("city")
    e = _eid()
    r = P(client, co, "/api/patients", given_name=given, family_name=family, birth_date="1979-09-21", gender="female",
          national_id=e, nationality="Jordan", language="ur", phone="0521234567", email="capture.reg@example.ae",
          address_line=addr, city=city, emirate="Abu Dhabi", region="Al Dhafra", preferred_channel="whatsapp",
          whatsapp=True, has_smartphone=True, managing_org_id=4, gp_id=None,
          consent_care_coordination="permit", consent_note=tok("regconsent"))
    assert r.status_code == 200, r.text
    pid = r.json()["patient_id"]; rd = f"/api/patients/{pid}"
    for v in (given, family, addr, city):
        captured(client, db, r, v, rd, h=co)
    rec = client.get(rd, headers=co).json()
    d, c, k = rec["demographics"], rec["contact"], rec["care"]
    assert (d["birth_date"], d["gender"], d["national_id"], d["nationality"], d["language"]) == ("1979-09-21", "female", e, "Jordan", "ur")
    assert (c["phone"], c["email"], c["region"], c["preferred_channel"], c["whatsapp"], c["has_smartphone"]) == \
        ("+971521234567", "capture.reg@example.ae", "Al Dhafra", "whatsapp", True, True)
    assert k["managing_org_id"] == 4 and rec["consents"][0]["decision"] == "permit"
    db.expire_all(); p = db.get(Patient, pid)
    assert p.birth_year == 1979 and p.nationality_group == "Other Arab" and p.registered_by and p.registered_at
    # each edit section: changed values stored and shown back
    a2, c2 = tok("address2"), tok("city2")
    captured(client, db, P(client, co, f"{rd}/contact", address_line=a2, city=c2, emirate="Dubai", phone="+971 4 123 4567",
                           email="moved@example.ae", preferred_channel="phone", whatsapp=False, has_smartphone=False), a2, rd, h=co)
    rec = client.get(rd, headers=co).json()
    assert rec["contact"]["city"] == c2 and rec["contact"]["emirate"] == rec["contact"]["region"] == "Dubai" and rec["contact"]["phone"] == "+97141234567"
    g2 = _name_tok()
    captured(client, db, P(client, co, f"{rd}/demographics", given_name=g2, birth_date="1979-09-22", nationality="Philippines", language="tl"), g2, rd, h=co)
    db.expire_all(); p = db.get(Patient, pid); assert p.nationality_group == "Filipino" and p.language == "tl"
    assert P(client, co, f"{rd}/care", managing_org_id=3, gp_id=19).status_code < 400
    # the patient's GP (now at health centre 3) can edit; a GP elsewhere cannot
    assert P(client, staff["gp"], f"{rd}/contact", city=c2 + " GP").status_code < 400
    assert client.get(rd, headers=co).json()["care"]["gp_id"] == 19
    # a possible-duplicate override reason is stored (audited) and the duplicate warning names the first record
    reason = tok("dupreason")
    r2 = P(client, co, "/api/patients", given_name=g2, family_name=family, birth_date="1979-09-22", gender="female", nationality="Jordan",
           language="ar", emirate="Abu Dhabi", region="Abu Dhabi", managing_org_id=3)
    assert r2.status_code == 409 and r2.json()["code"] == "possible_duplicate"
    r3 = P(client, co, "/api/patients", given_name=g2, family_name=family, birth_date="1979-09-22", gender="female", nationality="Jordan",
           language="ar", emirate="Abu Dhabi", region="Abu Dhabi", managing_org_id=3, create_anyway_reason=reason)
    assert r3.status_code == 200, r3.text
    assert reason in client.get(f"/api/patients/{r3.json()['patient_id']}", headers=co).text


def test_patient_account_invite_and_revoke_reason_capture(client, db, staff):
    co = staff["co"]
    r = P(client, co, "/api/patients", given_name=_name_tok(), family_name=_name_tok(), birth_date="1968-01-30", gender="male",
          nationality="Pakistan", language="ur", phone="0559876543", emirate="Abu Dhabi", region="Al Ain", managing_org_id=3)
    pid = r.json()["patient_id"]; rd = f"/api/patients/{pid}"
    inv = P(client, co, f"{rd}/account-invite", channel="sms"); assert inv.status_code == 200, inv.text
    acc = client.get(rd, headers=co).json()["account"]
    assert acc["invite"]["channel"] == "sms" and acc["username"] == inv.json()["username"] and acc["invite_messages"]
    t = tok("revoke")
    captured(client, db, P(client, co, f"{rd}/account-invite/revoke", reason=t), t, rd, h=co)


def test_recall_cancel_reason_capture(client, db, staff, make_patient):
    from kareshafaa.models import Recall
    co = staff["co"]
    pid = make_patient(phone_valid=True)
    assert P(client, co, "/api/results", patient_id=pid, programme="colorectal_fit", value=4, unit="ug/g").status_code == 200
    db.expire_all()
    rid = db.scalar(select(Recall.id).where(Recall.patient_id == pid))
    r = P(client, co, f"/api/recalls/{rid}/cancel", reason="x"); assert r.status_code == 422  # same rule as the form (5+)
    t = tok("recallcancel")
    captured(client, db, P(client, co, f"/api/recalls/{rid}/cancel", reason=t), t, f"/api/patients/{pid}", "/api/recalls?view=upcoming", h=co)


def test_gp_summary_ack_note_capture(client, db, staff, as_user, new_case):
    from kareshafaa.models import GpSummary
    from tests.test_gp_summary import _to_care
    cid = new_case(value=250)["id"]
    _to_care(client, as_user, cid)
    db.expire_all()
    sid = db.scalar(select(GpSummary.id).where(GpSummary.case_id == cid))
    t = tok("gpack")
    captured(client, db, P(client, staff["gp"], f"/api/gp-summaries/{sid}/ack", note=t), t, f"/api/cases/{cid}", "/api/gp-summaries?view=all", h=staff["gp"])
    assert P(client, staff["gp"], f"/api/gp-summaries/{sid}/ack", note="x" * 1001).status_code == 422


def test_appointment_change_reason_and_barrier_capture(client, db, staff, as_user, new_case):
    from datetime import timedelta
    from kareshafaa.models import Appointment, utcnow
    from tests.test_appointment_lifecycle import _booked
    cid, co, *_rest, start = _booked(client, as_user, new_case)
    t = tok("apptbarrier")
    # patient asked to move: barrier required; note optional here
    r = P(client, co, f"/api/cases/{cid}/appointment/reschedule", start=(start + timedelta(days=2)).isoformat(),
          reason_code="patient_request", barrier_code="transport", note=t)
    captured(client, db, r, t, f"/api/cases/{cid}", h=co)
    db.expire_all()
    a = db.scalars(select(Appointment).where(Appointment.case_id == cid, Appointment.status == "cancelled")).one()
    assert a.cancel_reason == "patient_request" and a.barrier_code == "transport"


def test_insurance_next_of_kin_and_proxy_consent_capture(client, db, staff):
    from datetime import date, timedelta
    from kareshafaa.models import Patient
    from tests.test_patient_registration import reg
    co = staff["co"]
    pid = reg(client, co).json()["patient_id"]
    t = tok("insplan")
    end = (date.today() + timedelta(days=200)).isoformat()
    r = P(client, co, f"/api/patients/{pid}/insurance", insurance_payer="adnic", insurance_plan=t,
          insurance_member_id="ADN/2026-0099", insurance_valid_to=end)
    captured(client, db, r, t, f"/api/patients/{pid}", h=co)
    t2 = tok("proxynote")
    r = P(client, co, f"/api/patients/{pid}/contacts", nok_name="Layla Haddad", nok_relationship="sibling",
          nok_phone="+44 7700 900123", nok_is_guardian=False, proxy_consent="permit", proxy_note=t2)
    where = captured(client, db, r, t2, f"/api/patients/{pid}", h=co)
    assert "patients.proxy_note" in where and "consents.note" in where
    db.expire_all()
    p = db.get(Patient, pid)
    assert (p.insurance_payer, p.insurance_member_id, p.insurance_valid_to.isoformat()) == ("adnic", "ADN/2026-0099", end)
    assert (p.nok_name, p.nok_relationship, p.nok_phone, p.nok_is_guardian) == ("Layla Haddad", "sibling", "+447700900123", False)
    rec = client.get(f"/api/patients/{pid}", headers=co).json()
    assert rec["insurance"]["insurance_member_id"] == "ADN/2026-0099" and rec["contacts"]["nok_name"] == "Layla Haddad"
    assert rec["contacts"]["proxy_consent"] == "permit"
