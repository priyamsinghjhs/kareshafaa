"""Response serialisation helpers (data minimisation applied here)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Appointment, AuditEvent, CallTask, CarePlan, Case, Communication, Consent, ConsultRequest,
                      DiagnosticReport, Escalation, Organization, OwnershipEvent, Practitioner, QuestionnaireResponse,
                      ServiceRequest, StepTask, utcnow)
from ..programmes import PROGRAMMES
from ..security import Actor
from ..sla import SlaLookup, rag_status
from ..statemachine import LABELS, MAIN_PATH, S


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def pname(db: Session, pid: int | None) -> str | None:
    if pid is None:
        return None
    p = db.get(Practitioner, pid)
    return p.name if p else None


def oname(db: Session, oid: int | None) -> str | None:
    o = db.get(Organization, oid) if oid else None
    return o.name if o else None


def case_summary(db: Session, c: Case) -> dict:
    p = c.patient
    return {"id": c.id, "programme": c.programme, "programme_name": PROGRAMMES[c.programme].name, "tier": c.tier,
            "state": c.state, "state_label": LABELS.get(c.state, c.state), "owner_id": c.owner_id, "owner": c.owner.name,
            "owner_is_pool": c.owner.is_pool, "created_at": iso(c.created_at), "due_at": iso(c.due_at),
            "patient": {"id": p.id, "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "language": p.language},
            "closed_at": iso(c.closed_at), "closure_type": c.closure_type, "closure_reason": c.closure_reason,
            "review_required": bool(c.review_required), "review_reasons": c.review_reasons or [],
            "rule_id": c.rule_id, "pathway": c.pathway, "target": c.target_text,
            "target_due_at": iso(c.target_due_at)}


def journey(db: Session, c: Case) -> list[dict]:
    visited = {s.state for s in c.steps}
    out = []
    for st in MAIN_PATH:
        if st == S.CONFIRMATORY_TEST and not c.needs_confirmation:
            continue
        status = "current" if c.state == st else "done" if st in visited else "pending"
        if st == S.CLOSED and c.state == S.CLOSED:
            status = "done"
        out.append({"state": st.value, "label": LABELS[st], "status": status})
    side = [s for s in (S.UNREACHABLE, S.NO_SHOW, S.DECLINED_REVIEW, S.REFERRAL_REJECTED) if s in visited]
    for s in side:
        out.append({"state": s.value, "label": LABELS[s], "status": "current" if c.state == s else "side"})
    return out


def case_detail(db: Session, c: Case, actor: Actor, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    sla = SlaLookup.load(db)
    p = c.patient
    rep = db.get(DiagnosticReport, c.report_id)
    steps = []
    for s in c.steps:
        rag, frac = rag_status(s.started_at, s.due_at, s.ended_at or now, sla.reminder_fraction(s.state, c.tier))
        steps.append({"id": s.id, "state": s.state, "label": LABELS.get(s.state, s.state), "owner_id": s.owner_id,
                      "owner": pname(db, s.owner_id), "status": s.status, "started_at": iso(s.started_at),
                      "ended_at": iso(s.ended_at), "due_at": iso(s.due_at), "sla_hours": s.sla_hours,
                      "breached": s.breached or (s.status == "in-progress" and rag == "red"),
                      "sla_status": rag if s.status == "in-progress" else ("red" if s.breached else "green"),
                      "sla_fraction": round(frac, 2), "note": s.note})
    cur = next((s for s in steps if s["status"] == "in-progress"), None)
    q = lambda m: db.scalars(select(m).where(m.case_id == c.id).order_by(m.id)).all()  # noqa: E731
    gp = db.get(Practitioner, p.gp_id) if p.gp_id else None
    return {
        **case_summary(db, c),
        "label": "SYNTHETIC DATA",
        "needs_confirmation": c.needs_confirmation,
        "sla": {"status": cur["sla_status"] if cur else "none", "fraction": cur["sla_fraction"] if cur else None,
                "due_at": cur["due_at"] if cur else None, "escalation_level": c.escalation_level},
        "risk": {"score": None if c.risk_score is None else round(c.risk_score, 3), "model_version": c.risk_model_version,
                 "use": "Outreach prioritisation only - never used for clinical decisions, state changes or closure"},
        "patient": {"id": p.id, "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "gender": p.gender,
                    "age_band": p.age_band, "language": p.language, "region": p.region,
                    "managing_org": oname(db, p.managing_org_id), "gp": gp.name if gp else None,
                    "channels": {"smartphone": p.has_smartphone, "whatsapp": p.whatsapp, "phone_valid": p.phone_valid},
                    "deceased": p.deceased},
        "result": {"id": rep.id, "display": rep.display, "issued_at": iso(rep.issued_at),
                   "observations": [{"display": o.display, "value": o.value, "value_text": o.value_text, "unit": o.unit,
                                     "interpretation": o.interpretation} for o in rep.observations]} if rep else None,
        "journey": journey(db, c),
        "steps": steps,
        "transfer_pending": {"to_owner_id": c.transfer_pending_owner_id, "to_owner": pname(db, c.transfer_pending_owner_id),
                             "note": c.transfer_note} if c.transfer_pending_owner_id else None,
        "closure": {"type": c.closure_type, "reason": c.closure_reason, "note": c.closure_note,
                    "evidence_type": c.closure_evidence_type, "evidence_id": c.closure_evidence_id,
                    "closed_at": iso(c.closed_at)} if c.closed_at else None,
        "ownership_events": [{"kind": e.kind, "from": pname(db, e.from_owner_id), "to": pname(db, e.to_owner_id),
                              "reason": e.reason, "at": iso(e.at)} for e in q(OwnershipEvent)],
        "service_requests": [{"id": s.id, "kind": s.kind, "code": s.code, "status": s.status, "requester": pname(db, s.requester_id),
                              "performer_org": oname(db, s.performer_org_id), "performer_org_id": s.performer_org_id,
                              "performer": pname(db, s.performer_id), "reason_code": s.reason_code,
                              "reason_note": s.reason_note, "result": s.result, "summary": s.clinical_summary,
                              "created_at": iso(s.created_at), "decided_at": iso(s.decided_at)} for s in q(ServiceRequest)],
        "appointments": [{"id": a.id, "start": iso(a.start), "status": a.status, "org": oname(db, a.org_id),
                          "barrier_code": a.barrier_code} for a in q(Appointment)],
        "care_plans": [{"id": cp.id, "kind": cp.kind, "description": cp.description, "created_at": iso(cp.created_at),
                        "by": pname(db, cp.recorded_by_id)} for cp in q(CarePlan)],
        "communications": [{"id": m.id, "category": m.category, "channel": m.channel, "status": m.status,
                            "language": m.language, "variant": m.template_variant, "content": m.content,
                            "to": pname(db, m.recipient_practitioner_id) or "patient", "sent_at": iso(m.sent_at),
                            "simulated": m.simulated} for m in q(Communication)],
        "call_tasks": [{"id": t.id, "status": t.status, "outcome": t.outcome, "owner": pname(db, t.owner_id),
                        "created_at": iso(t.created_at), "completed_at": iso(t.completed_at)} for t in q(CallTask)],
        "teach_back": [{"id": r.id, "score": r.score, "total": r.total, "passed": r.passed, "mode": r.mode,
                        "language": r.language, "confidence": r.confidence, "at": iso(r.created_at)}
                       for r in q(QuestionnaireResponse)],
        "consults": [{"id": r.id, "type": r.requester_type, "target_id": r.target_practitioner_id,
                      "target": pname(db, r.target_practitioner_id), "question": r.question, "urgency": r.urgency,
                      "status": r.status, "response": r.response, "created_at": iso(r.created_at), "due_at": iso(r.due_at),
                      "overdue": r.status == "requested" and r.due_at is not None and r.due_at < now}
                     for r in q(ConsultRequest)],
        "escalations": [{"id": e.id, "level": e.level, "target": pname(db, e.target_practitioner_id), "note": e.note,
                         "created_at": iso(e.created_at), "acknowledged_at": iso(e.acknowledged_at)} for e in q(Escalation)],
        "consents": [{"scope": k.scope, "decision": k.decision, "status": k.status, "at": iso(k.recorded_at), "note": k.note}
                     for k in db.scalars(select(Consent).where(Consent.patient_id == p.id).order_by(Consent.id))],
        "audit": [{"id": a.id, "ts": iso(a.ts), "actor": a.actor_display, "role": a.actor_role, "action": a.action,
                   "event": a.event, "outcome": a.outcome, "detail": a.detail}
                  for a in db.scalars(select(AuditEvent).where(AuditEvent.case_id == c.id).order_by(AuditEvent.id.desc())
                                      .limit(300))],
    }
