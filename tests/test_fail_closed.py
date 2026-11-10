"""Fail-closed review (docs/fail_closed_review.md): every clinical write path is all-or-nothing.

For each operation, a fault is injected in the MIDDLE of the request (the operation's first domain audit write,
after its first changes are in the session), at the END (inside the final commit, after every change has been
flushed to the database transaction) and, for paths that message the patient, in the message gateway. The request
must fail, leave NO partial change (every row of the case and patient, and every table's row count, unchanged), leave
an audit trail of the failed request (request audit event with outcome failure, or a durable fallback event), and
the same operation must then succeed on retry (nothing half-done blocks it)."""
from __future__ import annotations

import hashlib
import json
from datetime import date, timedelta

import pytest
from sqlalchemy import func, inspect, select, text
from sqlalchemy.orm import Session

from kareshafaa.models import utcnow

CO, GP, SP, LEAD = "aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori"
SUMMARY = "FIT 250 ug/g, no red flags, fit for colonoscopy; anticoagulants: none. Synthetic."
LOG_TABLES = {"audit_events", "audit_fallback_events", "login_attempts", "user_sessions", "sessions"}


class Injected(RuntimeError):
    pass


def _past():
    return (utcnow() - timedelta(hours=1)).isoformat()


def _future(d=3):
    return (utcnow() + timedelta(days=d)).replace(microsecond=0).isoformat()


# the colorectal pathway, one step per clinical write path (user, url, body); each step needs the ones before it
JOURNEY = [
    ("notify", CO, "/api/cases/{cid}/notify", {"category": "result_notification"}),
    ("call outcome", CO, "/api/cases/{cid}/call", {"outcome": "reached", "note": "explained result"}),
    ("teach-back", CO, "/api/cases/{cid}/teach-back", {"answers": {"q1": "a", "q2": "b", "q3": "a"}, "confidence": 4,
                                                       "language": "en"}),
    ("transition", GP, "/api/cases/{cid}/transition", {"to_state": "consultation", "note": "GP reviewed"}),
    ("referral", GP, "/api/cases/{cid}/referral", {"performer_org_id": 9, "performer_id": 31, "summary": SUMMARY}),
    ("referral decision", SP, "/api/cases/{cid}/referral/decision", {"accept": True, "note": "accepted"}),
    ("appointment book", CO, "/api/cases/{cid}/appointment", {"start": "FUTURE"}),
    ("appointment reschedule", CO, "/api/cases/{cid}/appointment/reschedule",
     {"start": "FUTURE5", "reason_code": "patient_request", "barrier_code": "transport", "note": "taxi not available"}),
    ("appointment cancel", CO, "/api/cases/{cid}/appointment/cancel", {"reason_code": "clinic_cancelled",
                                                                       "note": "Endoscopist on leave"}),
    ("appointment rebook", CO, "/api/cases/{cid}/appointment", {"start": "PAST"}),
    ("attendance", SP, "/api/cases/{cid}/attendance", {"attended": True}),
    ("care plan", SP, "/api/cases/{cid}/care-plan", {"kind": "surveillance",
                                                      "description": "Colonoscopy done; 3y surveillance"}),
    ("closure", SP, "/api/cases/{cid}/close", {"closure_type": "evidence", "evidence_type": "care_plan"}),
    ("reopen", LEAD, "/api/cases/{cid}/reopen", {"note": "New symptoms reported by GP"}),
]
# single operations on a fresh case / patient
SINGLE = [
    ("consent withdrawal", CO, "/api/cases/{cid}/consent", {"scope": "care_coordination", "decision": "deny",
                                                            "note": "patient asked by phone today"}),
    ("channel consent", CO, "/api/cases/{cid}/consent", {"scope": "comm_sms", "decision": "deny", "note": "no texts please, said by phone"}),
    ("assign", CO, "/api/cases/{cid}/assign", {"owner_id": 31, "reason": "specialist takes over"}),
    ("decline", CO, "/api/cases/{cid}/decline", {"note": "Patient does not want colonoscopy now"}),  # after notify
    ("authorised closure", LEAD, "/api/cases/{cid}/close", {"closure_type": "authorised_decision",
                                                            "reason": "duplicate",
                                                            "note": "Informed decline documented by GP"}),
    ("medication", GP, "/api/cases/{cid}/medications", {"name": "Metformin 500 mg", "dose": "1 tablet",
                                                        "frequency": "twice daily", "reason": "diabetes"}),
    ("contact edit", CO, "/api/patients/{pid}/contact", {"email": "fail.closed@example.ae"}),
    ("insurance edit", CO, "/api/patients/{pid}/insurance", {
        "insurance_payer": "daman_enhanced", "insurance_plan": "Enhanced - Silver network (synthetic)",
        "insurance_member_id": "SYN-DME-0099999", "insurance_valid_to": (date.today() + timedelta(days=200)).isoformat(),
        "insurance_preauth_required": True}),
    ("result ingestion", CO, "/api/results", {"patient_id": "PID", "programme": "diabetes_hba1c", "value": 9.1,
                                              "unit": "%"}),
    ("recall invite", CO, "/api/recalls/{rid}/invite", None),
]


def _resolve(body, ctx):
    if body is None:
        return None
    out = {}
    for k, v in body.items():
        v = {"FUTURE": _future(3), "FUTURE5": _future(5), "PAST": _past(), "PID": ctx["pid"]}.get(v, v) \
            if isinstance(v, str) else v
        out[k] = v
    return out


def _fingerprint(db, ctx) -> dict:
    """Row counts of every non-log table, plus the full content of every row of this case and patient."""
    db.expire_all()
    conn = db.connection()
    names = [t for t in inspect(conn).get_table_names() if t not in LOG_TABLES]
    fp = {}
    for t in names:
        cols = {c["name"] for c in inspect(conn).get_columns(t)}
        fp[f"{t}#"] = conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
        cond = []
        if "case_id" in cols:
            cond.append(f"case_id = {int(ctx['cid'])}")
        if "patient_id" in cols:
            cond.append(f"patient_id = {int(ctx['pid'])}")
        if t == "cases":
            cond.append(f"id = {int(ctx['cid'])}")
        if t == "patients":
            cond.append(f"id = {int(ctx['pid'])}")
        if cond:
            rows = conn.execute(text(f'SELECT * FROM "{t}" WHERE {" OR ".join(cond)} ORDER BY 1')).all()
            fp[t] = hashlib.sha256(json.dumps([list(r) for r in rows], default=str).encode()).hexdigest()
    db.rollback()
    return fp


def _trail(db, since, url) -> bool:
    from kareshafaa.models import AuditEvent, AuditFallbackEvent
    db.expire_all()
    ev = db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.id > since[0], AuditEvent.path == url, AuditEvent.outcome == "failure"))
    fb = db.scalar(select(func.count()).select_from(AuditFallbackEvent))
    return bool(ev) or fb > since[1]


def _heads(db):
    from kareshafaa.models import AuditEvent, AuditFallbackEvent
    db.expire_all()
    return (db.scalar(select(func.max(AuditEvent.id))) or 0, db.scalar(select(func.count()).select_from(AuditFallbackEvent)))


def _is_clinical(obj) -> bool:
    from kareshafaa.models import AuditEvent, AuditFallbackEvent, LoginAttempt, User, UserSession
    return not isinstance(obj, (AuditEvent, AuditFallbackEvent, LoginAttempt, User, UserSession))


@pytest.fixture()
def inject(monkeypatch):
    """inject(mode) arms ONE fault for the next request: 'mid' | 'commit' | 'gateway'."""
    from kareshafaa import audit
    from kareshafaa import queue_store
    from kareshafaa.comms import service as comms_service

    state = {"mode": None, "fired": False}
    real_record, real_commit = audit.record, Session.commit
    real_deliver = comms_service.GATEWAY.deliver
    real_refresh = queue_store.refresh

    def refresh(*a, **kw):
        if state["mode"] == "queue" and not state["fired"]:
            state["fired"] = True
            raise Injected("injected fault in the work-queue SLA refresh (flush hook)")
        return real_refresh(*a, **kw)

    def record(db, actor, action, event, *a, **kw):
        if state["mode"] == "mid" and not state["fired"] and not event.startswith("http."):
            state["fired"] = True
            raise Injected(f"injected fault at audit event {event}")
        return real_record(db, actor, action, event, *a, **kw)

    def commit(self):
        if state["mode"] == "commit" and not state["fired"] and self.info.get("clinical_flushed"):
            self.flush()  # every change is in the database transaction now
            state["fired"] = True
            raise Injected("injected fault at commit")
        return real_commit(self)

    def deliver(*a, **kw):
        if state["mode"] == "gateway" and not state["fired"]:
            state["fired"] = True
            raise Injected("injected message gateway fault")
        return real_deliver(*a, **kw)

    from sqlalchemy import event

    def mark(session, _ctx, _inst):
        if any(map(_is_clinical, list(session.new) + list(session.dirty) + list(session.deleted))):
            session.info["clinical_flushed"] = True

    def clear(session):
        session.info.pop("clinical_flushed", None)

    event.listen(Session, "before_flush", mark)
    event.listen(Session, "after_commit", clear)
    event.listen(Session, "after_rollback", clear)
    monkeypatch.setattr(queue_store, "refresh", refresh)
    monkeypatch.setattr(audit, "record", record)
    monkeypatch.setattr(Session, "commit", commit)
    monkeypatch.setattr(comms_service.GATEWAY, "deliver", deliver)

    def arm(mode):
        state.update(mode=mode, fired=False)
        return state

    yield arm
    event.remove(Session, "before_flush", mark)
    event.remove(Session, "after_commit", clear)
    event.remove(Session, "after_rollback", clear)


@pytest.fixture()
def raw(app):
    from fastapi.testclient import TestClient
    return TestClient(app, raise_server_exceptions=False)


def _ctx(new_case, db, **kw):
    from kareshafaa.models import Case
    c = new_case("colorectal_fit", 250, smartphone=True, phone_valid=True, **kw)
    return {"cid": c["id"], "pid": db.get(Case, c["id"]).patient_id}


def _check_fault(raw, as_user, db, ctx, inject, mode, user, url_t, body):
    url = url_t.format(**ctx)
    h, payload = as_user(user), _resolve(body, ctx)
    before, since = _fingerprint(db, ctx), _heads(db)
    st = inject(mode)
    r = raw.post(url, headers=h, json=payload)
    fired = st["fired"]
    inject(None)
    if not fired:
        return r, False  # this path has no such point (e.g. no message sent): nothing to prove for this mode
    assert r.status_code >= 500, (mode, url, r.status_code, r.text[:300])
    assert _fingerprint(db, ctx) == before, f"{mode} fault in {url} left a partial change"
    assert _trail(db, since, url), f"{mode} fault in {url} left no audit trail"
    return r, True


@pytest.mark.parametrize("mode", ["mid", "commit", "gateway", "queue"])
def test_journey_steps_are_all_or_nothing(mode, raw, as_user, new_case, db, inject):
    ctx = _ctx(new_case, db)
    proved = []
    for name, user, url_t, body in JOURNEY:
        r, fired = _check_fault(raw, as_user, db, ctx, inject, mode, user, url_t, body)
        proved += [name] if fired else []
        if not fired:  # no such fault point on this step (it ran normally)
            assert r.status_code == 200, (name, r.status_code, r.text[:300])
            continue
        r = raw.post(url_t.format(**ctx), headers=as_user(user), json=_resolve(body, ctx))  # the retry works
        assert r.status_code == 200, (name, r.status_code, r.text[:300])
    assert proved, "no fault fired: the test proved nothing"
    expected = {n for n, *_ in JOURNEY}
    if mode == "queue":  # the call is logged after the app message already notified the patient: no case change
        expected -= {"call outcome"}
    if mode == "gateway":  # the steps that message the patient
        expected = {"notify", "appointment book", "appointment reschedule", "appointment cancel", "appointment rebook"}
    assert expected <= set(proved), expected - set(proved)


@pytest.mark.parametrize("mode", ["mid", "commit"])
@pytest.mark.parametrize("name,user,url_t,body", SINGLE, ids=[s[0] for s in SINGLE])
def test_single_operations_are_all_or_nothing(mode, name, user, url_t, body, raw, as_user, new_case, db, inject):
    ctx = _ctx(new_case, db)
    if name == "decline":  # needs a notified patient
        for _n, u, ut, b in JOURNEY[:2]:
            assert raw.post(ut.format(**ctx), headers=as_user(u), json=_resolve(b, ctx)).status_code == 200
    if "{rid}" in url_t:
        from kareshafaa.models import Recall
        rec = Recall(patient_id=ctx["pid"], programme="cervical", due_on=date.today(), status="due", reason="routine",
                     created_by="test")
        db.add(rec)
        db.commit()
        ctx["rid"] = rec.id
    _r, fired = _check_fault(raw, as_user, db, ctx, inject, mode, user, url_t, body)
    assert fired, f"no {mode} fault point in {name}"
    r = raw.post(url_t.format(**ctx), headers=as_user(user), json=_resolve(body, ctx))
    assert r.status_code == 200, (name, r.status_code, r.text[:300])


@pytest.mark.parametrize("mode", ["mid", "commit", "queue"])
def test_sla_sweep_is_all_or_nothing(mode, raw, as_user, new_case, db, inject):
    """The timed sweep (escalations, reminders, stored queue thresholds): a fault part-way leaves nothing behind."""
    from kareshafaa.models import Case, StepTask
    ctx = _ctx(new_case, db)
    step = db.scalar(select(StepTask).where(StepTask.case_id == ctx["cid"], StepTask.status == "in-progress"))
    step.started_at -= timedelta(days=30)
    step.due_at -= timedelta(days=30)  # long overdue: the sweep escalates it
    db.get(Case, ctx["cid"]).row_version  # noqa: B018
    db.commit()
    _r, fired = _check_fault(raw, as_user, db, ctx, inject, mode, CO, "/api/sla/sweep", {"case_ids": [ctx["cid"]]})
    assert fired
    r = raw.post("/api/sla/sweep", headers=as_user(CO), json={"case_ids": [ctx["cid"]]})
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("user,url_t,body", [
    (GP, "/api/cases/{cid}/close", {"closure_type": "evidence", "evidence_type": "appointment_fulfilled"}),  # no evidence
    (GP, "/api/cases/{cid}/transition", {"to_state": "closed"}),                       # not a valid transition
    (GP, "/api/cases/{cid}/referral", {"performer_org_id": 9, "summary": "too short"}),  # downstream validation
    (CO, "/api/cases/{cid}/appointment", {"start": "PAST"}),                           # wrong state for booking
])
def test_rejected_operations_change_nothing_and_are_audited(user, url_t, body, raw, as_user, new_case, db):
    ctx = _ctx(new_case, db)
    url, h, payload = url_t.format(**ctx), as_user(user), _resolve(body, ctx)
    before, since = _fingerprint(db, ctx), _heads(db)
    r = raw.post(url, headers=h, json=payload)
    assert 400 <= r.status_code < 500, (url, r.status_code, r.text[:200])
    assert _fingerprint(db, ctx) == before
    assert _trail(db, since, url), "the refusal left no audit trail"


@pytest.mark.parametrize("mode", ["mid", "commit", "queue"])
def test_lab_message_ingestion_is_all_or_nothing(mode, raw, as_user, new_case, db, inject):
    """HL7 v2 result for a known patient (creates report, observation, case, inbox row): all or nothing."""
    from kareshafaa.models import Patient
    from tests.test_ingest import hl7
    ctx = _ctx(new_case, db)
    p = db.get(Patient, ctx["pid"])
    url, h = "/api/ingest/hl7v2", {**as_user(CO), "Content-Type": "text/plain"}
    msg = hl7(p.mrn, "8.4", f"FC-{mode}-{p.id}", code="4548-4", dob="19700101")
    before, since = _fingerprint(db, ctx), _heads(db)
    st = inject(mode)
    r = raw.post(url, headers=h, content=msg)
    fired = st["fired"]
    inject(None)
    assert fired and r.status_code >= 500, (r.status_code, r.text[:200])
    assert _fingerprint(db, ctx) == before
    assert _trail(db, since, url)
    assert raw.post(url, headers=h, content=msg).status_code == 200


@pytest.mark.parametrize("mode", ["mid", "commit", "queue"])
def test_critical_read_back_is_all_or_nothing(mode, raw, as_user, new_case, db, inject):
    """Critical-result fast lane: the read-back record and its audit event are saved together or not at all."""
    from kareshafaa.models import Case
    c = new_case("diabetes_hba1c", 16.0)
    ctx = {"cid": c["id"], "pid": db.get(Case, c["id"]).patient_id}
    body = {"method": "phone", "reported_by": "Lab duty scientist", "read_back_confirmed": True}
    _r, fired = _check_fault(raw, as_user, db, ctx, inject, mode, LEAD, "/api/cases/{cid}/critical-read-back", body)
    assert fired
    r = raw.post(f"/api/cases/{ctx['cid']}/critical-read-back", headers=as_user(LEAD), json=body)
    assert r.status_code == 200, r.text


def test_critical_triage_without_read_back_changes_nothing(raw, as_user, new_case, db):
    from kareshafaa.models import Case
    c = new_case("diabetes_hba1c", 16.0)
    ctx = {"cid": c["id"], "pid": db.get(Case, c["id"]).patient_id}
    url = f"/api/cases/{ctx['cid']}/transition"
    before, since = _fingerprint(db, ctx), _heads(db)
    r = raw.post(url, headers=as_user(LEAD), json={"to_state": "triaged"})
    assert 400 <= r.status_code < 500, r.text
    assert _fingerprint(db, ctx) == before
    assert _trail(db, since, url), "the refusal left no audit trail"


@pytest.mark.parametrize("mode", ["mid", "commit", "queue"])
def test_reconciliation_open_case_is_all_or_nothing(mode, raw, as_user, make_patient, db, inject):
    """Putting a result found by reconciliation back into the loop: the case, the report link, the item resolution
    and the audit events are saved together or not at all."""
    from kareshafaa.models import DiagnosticReport, ResultReconciliationItem
    from kareshafaa.services import reconcile
    pid = make_patient()
    rep = DiagnosticReport(patient_id=pid, programme="breast", category="screening", code="24606-6", display="Mammogram",
                           conclusion="actionable", issued_at=utcnow(), performer_org_id=1, status="final")
    db.add(rep)
    db.commit()
    reconcile.run(db)
    db.commit()
    it = db.scalar(select(ResultReconciliationItem).where(ResultReconciliationItem.report_id == rep.id))
    ctx = {"cid": 0, "pid": pid}
    _r, fired = _check_fault(raw, as_user, db, ctx, inject, mode, CO, f"/api/ingest/reconciliation/{it.id}/resolve",
                             {"action": "open_case"})
    assert fired
    r = raw.post(f"/api/ingest/reconciliation/{it.id}/resolve", headers=as_user(CO), json={"action": "open_case"})
    assert r.status_code == 200, r.text
