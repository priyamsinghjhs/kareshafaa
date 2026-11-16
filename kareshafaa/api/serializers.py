"""Response serialisation helpers (data minimisation applied here)."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Appointment, AuditEvent, CallTask, CarePlan, Case, Communication, Consent, ConsultRequest,
                      DiagnosticReport, Escalation, Organization, OwnershipEvent, Practitioner, QuestionnaireResponse,
                      ServiceRequest, StepTask, utcnow, Lesson, User)
from ..programmes import PROGRAMMES
from ..security import Actor
from ..sla import SlaLookup, rag_reminder_fraction, rag_status
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
    prog = PROGRAMMES.get(c.programme)
    return {"id": c.id, "programme": c.programme, "programme_name": prog.name if prog else c.programme, "tier": c.tier,
            "state": c.state, "state_label": LABELS.get(c.state, c.state), "owner_id": c.owner_id,
            "owner": c.owner.name if c.owner else "Unassigned",
            "owner_is_pool": bool(c.owner and c.owner.is_pool), "created_at": iso(c.created_at), "due_at": iso(c.due_at),
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


def _patient_requests(db: Session, c: Case) -> list[dict]:
    from ..services.patient_loop import staff_view

    return staff_view(db, c)


def _gp_summaries(db: Session, c: Case, actor: Actor, now: datetime) -> list[dict]:
    from ..services.gp_summary import for_case

    return for_case(db, c, actor, now)


def _recall_info(db: Session, c: Case, now: datetime) -> dict:
    from ..clinical_rules import RECALL_INTERVAL_MONTHS, SURVEILLANCE_MONTHS
    from ..models import Recall
    from ..services.recall import add_months

    r = db.scalar(select(Recall).where(Recall.source_case_id == c.id).order_by(Recall.id.desc()).limit(1))
    return {"next_screening_due": iso(c.next_screening_due),
            "recall": {"id": r.id, "due_on": iso(r.due_on), "status": r.status, "reason": r.reason} if r else None,
            "routine_interval_months": RECALL_INTERVAL_MONTHS.get(c.programme),
            "surveillance_default_due": iso(add_months(now.date(), SURVEILLANCE_MONTHS.get(c.programme, 12)))}


def _critical_status(db: Session, c: Case) -> dict | None:
    from ..services.critical import status
    return status(db, c)



def _risk(c: Case) -> dict:
    from ..services.priority import CALIBRATION_NOTE, risk_band
    score = None if c.risk_score is None else round(c.risk_score, 3)
    return {"score": score, "model_version": c.risk_model_version,
            "rationale": c.risk_rationale or [],
            "band": risk_band(score),
            "calibration_note": CALIBRATION_NOTE,
            "ordering": "Orders cases WITHIN the same clinical urgency tier only; it never changes the tier",
            "use": "Outreach prioritisation only - never used for clinical decisions, state changes or closure"}


def case_detail(db: Session, c: Case, actor: Actor, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    # Minimal first response so the case page opens. Related records are empty
    # and the advisory panels are deferred.
    p = c.patient
    steps = []
    for s in c.steps:
        steps.append({"id": s.id, "state": s.state, "label": LABELS.get(s.state, s.state), "owner_id": s.owner_id,
                      "owner": pname(db, s.owner_id), "status": s.status, "started_at": iso(s.started_at),
                      "ended_at": iso(s.ended_at), "due_at": iso(s.due_at), "sla_hours": s.sla_hours,
                      "breached": bool(s.breached), "sla_status": "red" if s.breached else "green",
                      "sla_fraction": 0.0, "note": s.note})
    cur = next((s for s in steps if s["status"] == "in-progress"), None)
    return {
        **case_summary(db, c),
        "label": "KareShafaa case record",
        "needs_confirmation": c.needs_confirmation,
        "sla": {"status": cur["sla_status"] if cur else "none", "fraction": cur["sla_fraction"] if cur else None,
                "due_at": cur["due_at"] if cur else None, "escalation_level": max(c.escalation_level or 0, c.target_alert_level or 0)},
        "why": {"tier": c.tier, "tier_label": c.tier, "steps": [], "deferred": True},
        "risk": _risk(c),
        "contact_suggestion": None,
        "patient": {"id": p.id, "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "gender": p.gender,
                    "age_band": p.age_band, "language": p.language, "region": p.region,
                    "managing_org": None, "gp": None,
                    "channels": {"smartphone": p.has_smartphone, "whatsapp": p.whatsapp, "phone_valid": p.phone_valid, "email": bool(p.email)},
                    "deceased": p.deceased},
        "result": None,
        "journey": journey(db, c),
        "fast_lane": None,
        "coverage": None,
        "steps": steps,
        "transfer_pending": None,
        "closure": {"type": c.closure_type, "reason": c.closure_reason, "note": c.closure_note,
                    "evidence_type": c.closure_evidence_type, "evidence_id": c.closure_evidence_id,
                    "closed_at": iso(c.closed_at)} if c.closed_at else None,
        "ownership_events": [],
        "service_requests": [],
        "appointments": [],
        "patient_requests": [],
        "reviews": list(c.review_log or []),
        "lessons": [],
        "care_plans": [],
        "recall": {"next_screening_due": None, "recall": None},
        "gp_summaries": [],
        "communications": [],
        "call_tasks": [],
        "teach_back": [],
        "consults": [],
        "escalations": [],
        "consents": [],
        "audit": [],
    }


def _coverage_banner(db: Session, actor, p) -> dict | None:
    from ..services.coverage import case_banner

    return case_banner(db, actor, p)


def _why(db: Session, c: Case) -> dict:
    from ..services.explain import why_priority

    return why_priority(db, c)
