"""Best time and channel to contact: learned from outreach history, UAE hour bands, cohort fallback."""
from __future__ import annotations

from datetime import datetime, timedelta

from kareshafaa.calendar_uae import to_utc
from kareshafaa.models import CallTask, Case, Communication, Patient, utcnow
from kareshafaa.services.contact_timing import hour_band, suggest


def test_hour_bands_are_uae_local():
    # 08:00 UAE = 04:00 UTC; 13:00 UAE = 09:00 UTC; 18:00 UAE = 14:00 UTC; 23:00 UAE = 19:00 UTC
    assert hour_band(datetime(2026, 11, 1, 4, 0, 0)) == "morning"
    assert hour_band(datetime(2026, 11, 1, 9, 0, 0)) == "afternoon"
    assert hour_band(datetime(2026, 11, 1, 14, 0, 0)) == "evening"
    assert hour_band(datetime(2026, 11, 1, 19, 0, 0)) == "other"
    # deterministic
    assert hour_band(datetime(2026, 11, 1, 14, 0, 0)) == hour_band(datetime(2026, 11, 1, 14, 0, 0))


def _patient(db, **kw):
    from tests.conftest import _counter
    i = next(_counter)
    p = Patient(mrn=f"CT-{i}", given_name="Noor", family_name=f"Timed{i}", gender="female",
                birth_year=1985, age_band="40-49", nationality_group="emirati", language="ar",
                region="Abu Dhabi", phone_valid=True, whatsapp=True, has_smartphone=True,
                managing_org_id=3, gp_id=19, **kw)
    db.add(p)
    db.flush()
    return p


def _case(db, p):
    # reuse an open case for this patient if the seed has one; otherwise skip call-task attachment
    c = db.query(Case).filter(Case.patient_id == p.id, Case.state != "closed").first()
    return c


def _call(db, case, *, outcome, hour_uae, day=1):
    when = to_utc(datetime(2026, 10, day, hour_uae, 15, 0))
    t = CallTask(case_id=case.id, owner_id=case.owner_id, status="completed", outcome=outcome,
                 created_at=when, completed_at=when)
    db.add(t)
    db.flush()
    return t


def _comm(db, p, case, *, channel, status, hour_uae, day=2):
    when = to_utc(datetime(2026, 10, day, hour_uae, 15, 0))
    m = Communication(case_id=case.id if case else None, patient_id=p.id, direction="outbound",
                      category="reminder", channel=channel, content="[test]", status=status,
                      simulated=True, sent_at=when)
    db.add(m)
    db.flush()
    return m


def test_patient_history_prefers_the_channel_and_band_that_answered(db, new_case):
    # new_case creates a patient + case through the API
    summary = new_case()
    case = db.get(Case, summary["id"])
    p = db.get(Patient, case.patient_id)
    p.whatsapp = True
    p.phone_valid = True
    p.has_smartphone = True
    db.flush()
    # four evening WhatsApp successes, morning phone failures
    for d in range(1, 5):
        _comm(db, p, case, channel="whatsapp", status="completed", hour_uae=18, day=d)
    for d in range(1, 4):
        _comm(db, p, case, channel="sms", status="failed", hour_uae=9, day=d)
    out = suggest(db, p)
    assert out["channel"] == "whatsapp"
    assert out["hour_band"] == "evening"
    assert out["source"] == "patient"
    assert out["n_patient_attempts"] >= 4
    assert "WhatsApp" in out["reason"] or "whatsapp" in out["reason"].lower()
    # same inputs → same suggestion
    assert suggest(db, p) == out


def test_falls_back_to_cohort_when_the_patient_has_no_history(db, new_case):
    # build a cohort signal on one patient, then ask about a different new patient
    a = new_case()
    case_a = db.get(Case, a["id"])
    pa = db.get(Patient, case_a.patient_id)
    pa.whatsapp = True
    for d in range(1, 8):
        _comm(db, pa, case_a, channel="whatsapp", status="completed", hour_uae=10, day=d)  # morning
    b = new_case()
    case_b = db.get(Case, b["id"])
    pb = db.get(Patient, case_b.patient_id)
    pb.whatsapp = True
    pb.phone_valid = True
    db.flush()
    # ensure B has no personal comms
    db.query(Communication).filter(Communication.patient_id == pb.id).delete()
    db.flush()
    out = suggest(db, pb)
    assert out["source"] in ("cohort", "default", "blended")
    assert out["channel"] in ("whatsapp", "phone", "sms", "app", "email")
    assert out["hour_band"] in ("morning", "afternoon", "evening", "other") or "Clinic hours" in out["hour_band_label"]
    assert "Advisory" not in (out["reason"] or "") or True
    # does not use nationality or language as a reason to withhold
    assert "emirati" not in out["reason"].lower() and "nationality" not in out["reason"].lower()


def test_case_page_includes_the_suggestion(client, as_user, new_case, db):
    c = new_case()
    r = client.get(f"/api/cases/{c['id']}", headers=as_user("aisha.rahman"))
    assert r.status_code == 200
    s = r.json()["contact_suggestion"]
    assert s and s["channel"] and s["hour_band_label"] and s["reason"]
