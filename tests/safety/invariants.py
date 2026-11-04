"""Global 'must never happen' checks over the WHOLE database (every case, communication and audit event), used by
the safety suite after random operation sequences. Each function returns a list of violations (empty = safe)."""
from __future__ import annotations

from collections import defaultdict

from sqlalchemy import func, select

from kareshafaa.clinical_rules import TIER_RANK
from kareshafaa.statemachine import S

# app replies to something the patient just did in the app (their own decline / appointment request): not outreach
PATIENT_INITIATED = {"decline_ack", "appointment_request"}
SENT = {"completed", "failed"}  # attempted on the channel (failed = the gateway tried)


def case_violations(db) -> list[str]:
    from kareshafaa.models import Appointment, Case, DiagnosticReport, StepTask
    out: list[str] = []
    running = defaultdict(list)
    for cid, st in db.execute(select(StepTask.case_id, StepTask.state).where(StepTask.status == "in-progress")):
        running[cid].append(st)
    booked = set(db.scalars(select(Appointment.case_id).where(Appointment.status == "booked")))
    open_per = defaultdict(int)
    for c in db.scalars(select(Case)):
        tag = f"case {c.id} ({c.state})"
        if c.state not in {s.value for s in S}:
            out.append(f"{tag}: unknown state")
        if c.tier not in TIER_RANK:
            out.append(f"{tag}: unknown tier {c.tier}")
        if c.state == S.CLOSED:
            if c.closed_at is None or c.closure_type not in ("evidence", "authorised_decision"):
                out.append(f"{tag}: closed without closure time/type")
            if c.closure_type == "evidence" and not c.closure_evidence_id:
                out.append(f"{tag}: evidence closure without evidence")
            if c.closure_type == "authorised_decision" and not (c.closure_reason and len(c.closure_note or "") >= 10):
                out.append(f"{tag}: authorised closure without reason and rationale")
            if running.get(c.id):
                out.append(f"{tag}: closed but a step clock is still running")
            if c.review_required:
                out.append(f"{tag}: closed with an unresolved clinical review flag")
            if c.sla_amber_at is not None or c.sla_red_at is not None:
                out.append(f"{tag}: closed but still carries work-queue SLA thresholds")
        else:
            open_per[(c.patient_id, c.programme)] += 1
            if c.closed_at is not None or c.closure_type is not None:
                out.append(f"{tag}: open but has closure data")
            if len(running.get(c.id, [])) != 1:
                out.append(f"{tag}: {len(running.get(c.id, []))} running step clocks (expected 1)")
            elif running[c.id][0] != c.state:
                out.append(f"{tag}: running step is '{running[c.id][0]}'")
            if c.state == S.APPOINTMENT_BOOKED and c.id not in booked:
                out.append(f"{tag}: appointment booked state without a booked appointment")
    out += [f"patient {p} has {n} open {prog} cases" for (p, prog), n in open_per.items() if n > 1]
    lost = db.scalar(select(func.count()).select_from(DiagnosticReport).where(
        DiagnosticReport.conclusion.in_(["actionable", "needs_review"]), DiagnosticReport.case_id.is_(None)))
    if lost:
        out.append(f"{lost} actionable/review result(s) without a case")
    from kareshafaa import queue_store
    conn = db.connection()
    stale = queue_store.refresh(conn, None, write=False)
    if stale:
        out.append(f"{stale} open case(s) with stale work-queue SLA thresholds: {queue_store.last_stale[:3]}")
    return out


def _consent_at(rows, t):
    """Latest decision recorded at or before t (any status: the history at that moment)."""
    best = None
    for rec_at, rid, decision in rows:
        if rec_at <= t and (best is None or (rec_at, rid) > best[:2]):
            best = (rec_at, rid, decision)
    return best[2] if best else None


def communication_violations(db) -> list[str]:
    """No message on a channel the patient refused at that moment, and no AUTOMATED outreach after the patient
    withdrew care-coordination consent (a named clinician may still contact them: duty of care, audited)."""
    from kareshafaa.models import AuditEvent, Case, Communication, Consent
    cons = defaultdict(list)
    for pid, scope, rec_at, rid, decision in db.execute(select(Consent.patient_id, Consent.scope, Consent.recorded_at,
                                                               Consent.id, Consent.decision)):
        cons[(pid, scope)].append((rec_at, rid, decision))
    out = []
    for m in db.scalars(select(Communication).where(Communication.patient_id.is_not(None),
                                                    Communication.status.in_(SENT),
                                                    Communication.channel.in_(["app", "sms", "whatsapp", "phone"]))):
        if m.category in PATIENT_INITIATED:
            continue
        if _consent_at(cons[(m.patient_id, f"comm_{m.channel}")], m.sent_at) == "deny":
            out.append(f"communication {m.id} ({m.category}) on {m.channel} after the patient refused that channel")
    case_patient = dict(db.execute(select(Case.id, Case.patient_id)).all())
    for ev in db.scalars(select(AuditEvent).where(AuditEvent.actor_role.in_(["system", "ml_service"]),
                                                  AuditEvent.event.like("communication.%"))):
        pid = case_patient.get(ev.case_id)
        delivered = (ev.detail or {}).get("delivered")
        if pid and delivered and _consent_at(cons[(pid, "care_coordination")], ev.ts) == "deny":
            out.append(f"automated {ev.event} (audit {ev.id}) delivered after care-coordination consent was withdrawn")
    return out


def audit_violations(db) -> list[str]:
    from kareshafaa import audit
    v = audit.verify_chain(db)
    return [] if v["valid"] else [f"audit hash chain broken at event {v['broken_at_id']}"]
