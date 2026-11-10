"""Critical-result fast lane (screening-to-care principle 5): direct clinician contact with read-back before
triage, phone escalation at the first breach, never closed as unreachable, visible on its own worklist."""
from datetime import timedelta
import random

import pytest
from sqlalchemy import select

from kareshafaa.models import Case, Communication, CriticalReadBack, Escalation, StepTask
from kareshafaa.services.escalation import run_sweep
from kareshafaa.statemachine import S, TransitionError, check_closure

LEAD = "fatima.al.mansoori"
GP = "omar.haddad"
COORD = "aisha.rahman"
RB = {"method": "phone", "reported_by": "Lab duty scientist", "read_back_confirmed": True}


def _critical(new_case):
    c = new_case("diabetes_hba1c", 16.0)
    assert c["tier"] == "critical" and c["state"] == "issued"
    return c


def test_triage_blocked_until_read_back(client, as_user, new_case, db):
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}"
    r = client.post(f"{u}/transition", headers=as_user(LEAD), json={"to_state": "triaged"})
    assert r.status_code in (400, 409, 422) and "read-back" in r.text
    acts = {a["key"] for a in client.get(u, headers=as_user(LEAD)).json()["actions"]}
    assert "read_back" in acts and "triage" not in acts
    r = client.post(f"{u}/critical-read-back", headers=as_user(LEAD), json=RB)
    assert r.status_code == 200, r.text
    d = client.get(u, headers=as_user(LEAD)).json()
    assert d["fast_lane"]["read_back"]["method"] == "phone" and not d["fast_lane"]["read_back_needed"]
    assert "triage" in {a["key"] for a in d["actions"]}
    assert client.post(f"{u}/transition", headers=as_user(LEAD), json={"to_state": "triaged"}).status_code == 200


def test_read_back_must_be_confirmed_and_by_a_clinician(client, as_user, new_case):
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}/critical-read-back"
    assert client.post(u, headers=as_user(LEAD), json={**RB, "read_back_confirmed": False}).status_code in (400, 422)
    assert client.post(u, headers=as_user(COORD), json=RB).status_code == 403  # coordinator: not a clinician
    assert client.post(u, headers=as_user("admin"), json=RB).status_code == 403  # admin: no clinical actions
    assert client.post(u, headers=as_user(GP), json={**RB, "reported_by": "x"}).status_code in (400, 422)


def test_read_back_only_for_critical(client, as_user, new_case):
    c = new_case()  # FIT 250: not critical
    r = client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    assert r.status_code in (400, 422)


def test_audit_detail_is_pseudonymous(client, as_user, new_case, db):
    from kareshafaa.models import AuditEvent
    c = _critical(new_case)
    client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    db.expire_all()
    ev = db.scalar(select(AuditEvent).where(AuditEvent.case_id == c["id"],
                                            AuditEvent.event == "critical.read_back_recorded"))
    assert ev is not None and set(ev.detail) == {"method", "received_by_practitioner_id", "contacted_at"}


def test_worklist_shows_critical_without_read_back(client, as_user, new_case):
    c = _critical(new_case)
    q = client.get("/api/queue?view=critical&limit=1000", headers=as_user(LEAD)).json()
    row = next(r for r in q["items"] if r["id"] == c["id"])
    assert row["read_back_needed"] and q["counts"]["critical"] >= 1
    client.post(f"/api/cases/{c['id']}/critical-read-back", headers=as_user(LEAD), json=RB)
    q = client.get("/api/queue?view=critical&limit=1000", headers=as_user(LEAD)).json()
    assert c["id"] not in {r["id"] for r in q["items"]}


def test_first_breach_escalates_by_phone(db, new_case):
    c = _critical(new_case)
    db.expire_all()
    step = db.scalar(select(StepTask).where(StepTask.case_id == c["id"], StepTask.status == "in-progress"))
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 1.05), case_ids=[c["id"]])
    escs = db.scalars(select(Escalation).where(Escalation.case_id == c["id"])).all()
    assert {e.level for e in escs if e.channel == "phone"} >= {3}
    assert all(e.channel == "internal" for e in escs if e.level == 1)
    comm = db.scalars(select(Communication).where(Communication.case_id == c["id"],
                                                  Communication.category == "escalation")).all()
    assert any(m.channel == "phone" for m in comm)
    db.rollback()


def test_non_critical_breach_stays_internal(db, new_case):
    c = new_case()
    db.expire_all()
    step = db.scalar(select(StepTask).where(StepTask.case_id == c["id"], StepTask.status == "in-progress"))
    run_sweep(db, now=step.started_at + timedelta(hours=step.sla_hours * 1.6), case_ids=[c["id"]])
    escs = db.scalars(select(Escalation).where(Escalation.case_id == c["id"])).all()
    assert escs and all(e.channel == "internal" for e in escs)
    db.rollback()


def test_critical_never_closed_as_unreachable():
    with pytest.raises(TransitionError, match="critical"):
        check_closure("unreachable", "authorised_decision", evidence_type=None, reason="unable_to_contact",
                      note="tried every channel for days", actor_is_human=True, actor_can_authorise=True,
                      tier="critical")
    check_closure("unreachable", "authorised_decision", evidence_type=None, reason="unable_to_contact",
                  note="tried every channel for days", actor_is_human=True, actor_can_authorise=True, tier="urgent")


def test_closure_reason_list_hides_unreachable_for_critical(client, as_user, new_case):
    c = _critical(new_case)
    reasons = client.get(f"/api/cases/{c['id']}/actions", headers=as_user(LEAD)).json()["closure_reasons"]
    assert "unable_to_contact" not in reasons and "informed_decline" in reasons
    other = new_case()
    assert "unable_to_contact" in client.get(f"/api/cases/{other['id']}/actions", headers=as_user(LEAD)).json()["closure_reasons"]


def test_read_back_row_is_never_written_without_confirmation(db, new_case):
    from kareshafaa.security import Actor
    from kareshafaa.services.critical import record_read_back
    from kareshafaa.services.errors import Invalid
    c = _critical(new_case)
    case = db.get(Case, c["id"])
    lead = Actor(user_id=None, role="clinical_lead", display="Lead", practitioner_id=case.owner_id)
    with pytest.raises(Invalid):
        record_read_back(db, case, lead, method="phone", reported_by="Lab", read_back_confirmed=False)
    assert db.scalar(select(CriticalReadBack).where(CriticalReadBack.case_id == case.id)) is None
    db.rollback()


def test_contacted_at_utc_now_ok_and_unconverted_local_time_is_future(client, as_user, new_case):
    """The case page sends the contact time as UTC (local wall clock converted in the browser). A naive local
    IST time sent as if it were UTC looks ~5.5 h ahead and must be refused, not silently stored."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).replace(microsecond=0)
    c = _critical(new_case)
    u = f"/api/cases/{c['id']}/critical-read-back"
    ahead = (now + timedelta(hours=5, minutes=30)).replace(tzinfo=None).isoformat()
    r = client.post(u, headers=as_user(LEAD), json={**RB, "contacted_at": ahead})
    assert r.status_code == 422 and "future" in r.text
    r = client.post(u, headers=as_user(LEAD), json={**RB, "contacted_at": now.isoformat().replace("+00:00", "Z")})
    assert r.status_code == 200, r.text


def _replay_directory(db):
    """Rebuild the seed directory and user map from the already-seeded test database."""
    from kareshafaa.models import Organization, Practitioner, User
    from kareshafaa.programmes import PROGRAMMES

    orgs = list(db.scalars(select(Organization)))
    pracs = list(db.scalars(select(Practitioner)))
    leads, coordinators, gps, specialists = {}, {}, {}, {}
    for p in pracs:
        if p.role == "clinical_lead" and p.programme:
            leads[p.programme] = p
        elif p.role == "coordinator" and p.programme and not p.is_pool:
            coordinators.setdefault(p.programme, []).append(p)
        elif p.role == "gp":
            gps.setdefault(p.org_id, []).append(p)
        elif p.role == "specialist":
            specialists[(p.org_id, p.specialty)] = p
    for key in PROGRAMMES:
        assert key in leads and key in coordinators
    d = {"lab": next(o for o in orgs if o.kind == "lab"),
         "phc": [o for o in orgs if o.kind == "primary_care"],
         "hosp": [o for o in orgs if o.kind == "hospital"],
         "leads": leads, "coordinators": coordinators, "gps": gps, "specialists": specialists}
    users_by_pid = {u.practitioner_id: u for u in db.scalars(select(User).where(User.practitioner_id.is_not(None)))}
    return d, users_by_pid


def test_history_replay_records_critical_read_back_before_review_triage(db, monkeypatch):
    """A critical, review-required result used to crash Docker SEED_AT_BUILD at resolve_review
    (ISSUED → TRIAGED) because the history replayer had not recorded the fast-lane read-back."""
    import random
    from types import SimpleNamespace

    from kareshafaa.models import utcnow
    from kareshafaa.services import critical as crit
    from kareshafaa.services import workflow as wf
    from kareshafaa.synth.cohort import SynthPatient
    from kareshafaa.synth.history import Replayer, add_patient

    d, users_by_pid = _replay_directory(db)
    now = utcnow()
    t0 = now - timedelta(days=14)
    rng = random.Random(42)
    sp = SynthPatient(idx=810001, given="Critical", family="Review", gender="male", age=50,
                      nationality="emirati", language="en", region="Abu Dhabi", phone_valid=True,
                      has_smartphone=True, whatsapp=True, alt_contact=True, prior_no_shows=0,
                      prior_screens=1, responsiveness=1.0, programme="diabetes_hba1c",
                      share_consent=True, analytics_consent=True, haemoglobinopathy=True)
    patient = add_patient(db, sp, d, rng, now, age_at=t0)
    db.flush()

    monkeypatch.setattr("kareshafaa.synth.history.draw_result",
                        lambda *_a, **_k: SimpleNamespace(value=16.0, value_text=None, unit="%",
                                                          test="HBA1C", status="final",
                                                          cytology=None, defect=None))
    seen = {}
    orig_resolve = wf.resolve_review

    def _resolve(db_s, case, actor, **kw):
        rb = crit.latest(db_s, case.id)
        seen["before"] = rb
        assert rb is not None and rb.read_back_confirmed, "read-back must exist before review resolves"
        return orig_resolve(db_s, case, actor, **kw)

    monkeypatch.setattr(wf, "resolve_review", _resolve)

    rep = Replayer(db, rng, d, users_by_pid, now, enriched=True)
    case = rep.run(patient, sp, t0)
    assert case is not None and case.tier == "critical"
    assert "before" in seen, "review should have been resolved for this haemoglobinopathy + critical HbA1c"
    assert seen["before"].read_back_confirmed and seen["before"].method == "phone"
    assert seen["before"].reported_by == "lab duty scientist"
    db.expire_all()
    case = db.get(Case, case.id)
    assert case is not None and S(case.state) != S.ISSUED
    assert crit.latest(db, case.id) is not None
    db.rollback()


class _MidRandom(random.Random):
    """Deterministic stream that never takes the 15% 'leave overdue' draw in _resolve (0.5 >= 0.15)."""

    def random(self):
        return 0.5


def _critical_review_patient(db, *, idx, alt_contact=True, now=None, days_ago=14):
    from kareshafaa.models import utcnow
    from kareshafaa.synth.cohort import SynthPatient
    from kareshafaa.synth.history import add_patient

    d, users_by_pid = _replay_directory(db)
    now = now or utcnow()
    t0 = now - timedelta(days=days_ago)
    sp = SynthPatient(idx=idx, given="Critical", family="Review", gender="male", age=50,
                      nationality="emirati", language="en", region="Abu Dhabi", phone_valid=True,
                      has_smartphone=True, whatsapp=True, alt_contact=alt_contact, prior_no_shows=0,
                      prior_screens=1, responsiveness=1.0, programme="diabetes_hba1c",
                      share_consent=True, analytics_consent=True, haemoglobinopathy=True)
    patient = add_patient(db, sp, d, random.Random(1), now, age_at=t0)
    db.flush()
    return d, users_by_pid, now, t0, sp, patient


def test_history_resolve_leaves_critical_case_open(db):
    """A stalled critical case in _resolve must not be closed as unable_to_contact (the live guard)."""
    from kareshafaa.comms.service import send_with_ladder
    from kareshafaa.models import CallTask
    from kareshafaa.security import SYSTEM
    from kareshafaa.services import workflow as wf
    from kareshafaa.synth.history import Replayer

    d, users_by_pid, now, t0, sp, patient = _critical_review_patient(db, idx=810002, days_ago=200)
    _, case = wf.ingest_result(db, patient, "diabetes_hba1c", value=16.0, value_text=None, unit="%", test="HBA1C",
                               performer_org_id=d["lab"].id, now=t0)
    assert case is not None and case.tier == "critical" and case.review_required

    rep = Replayer(db, _MidRandom(), d, users_by_pid, now, enriched=True)
    rep.t = t0
    rep._ensure_critical_read_back(case)
    assert rep._review(case, patient)
    send_with_ladder(db, case, "result_notification", SYSTEM, now=rep.t)
    assert S(case.state) == S.PATIENT_NOTIFIED

    rep._resolve(case)
    db.expire_all()
    case = db.get(Case, case.id)
    assert case is not None and S(case.state) != S.CLOSED
    assert case.closure_reason != "unable_to_contact"
    calls = list(db.scalars(select(CallTask).where(CallTask.case_id == case.id, CallTask.outcome == "no_answer")))
    assert len(calls) >= 2
    db.rollback()


def test_history_replay_critical_run_does_not_close_as_unreachable(db, monkeypatch):
    """Full Replayer.run of a critical, review-required result must exercise _resolve without
    TransitionError and must never close the case as unable_to_contact."""
    from types import SimpleNamespace

    from kareshafaa.synth.history import Replayer

    d, users_by_pid, now, t0, sp, patient = _critical_review_patient(
        db, idx=810003, alt_contact=False, days_ago=400)

    monkeypatch.setattr("kareshafaa.synth.history.draw_result",
                        lambda *_a, **_k: SimpleNamespace(value=16.0, value_text=None, unit="%",
                                                          test="HBA1C", status="final",
                                                          cytology=None, defect=None))
    resolved = []
    orig = Replayer._resolve

    def _resolve(self, case):
        resolved.append(S(case.state))
        return orig(self, case)

    monkeypatch.setattr(Replayer, "_resolve", _resolve)

    class OpenReplayer(Replayer):
        """Force a silent, unresolved journey so run() reaches _resolve with the case still open."""

        def force(self, key, default, **kw):
            if key == "silent":
                real = self.rng.random

                def _stay_open():
                    self.rng.random = real
                    return 0.0  # the next draw is `if rng.random() < 0.5: return`

                self.rng.random = _stay_open
                return True
            return default

    rep = OpenReplayer(db, random.Random(42), d, users_by_pid, now, enriched=True)
    case = rep.run(patient, sp, t0)
    assert case is not None and case.tier == "critical"
    assert resolved, "_resolve must run end-to-end after the journey leaves the case open"
    db.expire_all()
    case = db.get(Case, case.id)
    assert case is not None
    assert S(case.state) != S.CLOSED
    assert case.closure_reason != "unable_to_contact"
    db.rollback()
