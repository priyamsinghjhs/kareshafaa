"""FHIR R4 JSON export of a case (Bundle type=collection). Shape follows R4; not profile-validated."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import (Appointment, AuditEvent, CallTask, CarePlan, Case, Communication, Consent, ConsultRequest,
                     DiagnosticReport, Organization, Practitioner, QuestionnaireResponse, ServiceRequest, StepTask,
                     utcnow)
from .programmes import PROGRAMMES
from .statemachine import LABELS, STATE_MACHINE_VERSION

BASE = "https://wasl.example/fhir"
SYS = "https://wasl.example/CodeSystem"


def _ts(d: datetime | None) -> str | None:
    return None if d is None else d.replace(microsecond=0).isoformat() + "Z"


def _ref(kind: str, id_: Any) -> dict:
    return {"reference": f"{kind}/{id_}"}


TASK_STATUS = {"in-progress": "in-progress", "completed": "completed"}
SR_STATUS = {"requested": "active", "accepted": "active", "rejected": "revoked", "completed": "completed", "revoked": "revoked"}
TASK_BIZ = {"requested": "requested", "accepted": "accepted", "rejected": "rejected", "completed": "completed", "revoked": "cancelled"}
APPT_STATUS = {"booked": "booked", "fulfilled": "fulfilled", "noshow": "noshow", "cancelled": "cancelled"}


def export_case(db: Session, case: Case, include_audit: bool = True) -> dict:
    p = case.patient
    res: list[dict] = []
    res.append({"resourceType": "Patient", "id": str(p.id), "meta": {"tag": [{"system": SYS + "/data", "code": "synthetic"}]},
                "identifier": [{"system": "https://wasl.example/mrn", "value": p.mrn}],
                "name": [{"family": p.family_name, "given": [p.given_name]}], "gender": p.gender,
                "birthDate": str(p.birth_year), "deceasedBoolean": p.deceased,
                "communication": [{"language": {"coding": [{"system": "urn:ietf:bcp:47", "code": p.language}]},
                                   "preferred": True}],
                "address": [{"district": p.region, "country": "AE"}],
                "managingOrganization": _ref("Organization", p.managing_org_id),
                **({"generalPractitioner": [_ref("Practitioner", p.gp_id)]} if p.gp_id else {})})
    org_ids = {p.managing_org_id, case.managing_org_id}
    prac_ids = {case.owner_id} | ({p.gp_id} if p.gp_id else set())
    steps = db.scalars(select(StepTask).where(StepTask.case_id == case.id).order_by(StepTask.id)).all()
    prac_ids |= {s.owner_id for s in steps}
    reports = db.scalars(select(DiagnosticReport).where(
        (DiagnosticReport.case_id == case.id) | (DiagnosticReport.id == case.report_id))).all()
    for r in reports:
        org_ids.add(r.performer_org_id)
        obs_refs = []
        for o in r.observations:
            obs = {"resourceType": "Observation", "id": f"obs-{o.id}", "status": "final",
                   "category": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/observation-category",
                                             "code": "laboratory"}]}],
                   "code": {"coding": [{"system": "http://loinc.org", "code": o.code, "display": o.display}]},
                   "subject": _ref("Patient", p.id), "issued": _ts(r.issued_at),
                   "interpretation": [{"coding": [{"system": "http://terminology.hl7.org/CodeSystem/v3-ObservationInterpretation",
                                                   "code": o.interpretation}]}]}
            if o.value is not None:
                obs["valueQuantity"] = {"value": o.value, "unit": o.unit}
            else:
                obs["valueString"] = o.value_text
            res.append(obs)
            obs_refs.append(_ref("Observation", f"obs-{o.id}"))
        res.append({"resourceType": "DiagnosticReport", "id": str(r.id), "status": r.status,
                    "category": [{"coding": [{"system": SYS + "/report-category", "code": r.category}]}],
                    "code": {"coding": [{"system": "http://loinc.org", "code": r.code, "display": r.display}]},
                    "subject": _ref("Patient", p.id), "issued": _ts(r.issued_at),
                    "performer": [_ref("Organization", r.performer_org_id)], "result": obs_refs,
                    "conclusion": r.conclusion})
    prog = PROGRAMMES[case.programme]
    case_task = {
        "resourceType": "Task", "id": f"case-{case.id}",
        "meta": {"tag": [{"system": SYS + "/state-machine", "code": STATE_MACHINE_VERSION}]},
        "identifier": [{"system": BASE + "/loop-case", "value": str(case.id)}],
        "status": "completed" if case.state == "closed" else "in-progress",
        "businessStatus": {"coding": [{"system": SYS + "/case-state", "code": case.state}], "text": LABELS.get(case.state)},
        "intent": "order", "priority": {"critical": "stat", "urgent": "urgent", "high": "asap", "routine": "routine"}[case.tier],
        "code": {"coding": [{"system": SYS + "/task-code", "code": "loop-case"}], "text": prog.name},
        "focus": _ref("DiagnosticReport", case.report_id), "for": _ref("Patient", p.id),
        "authoredOn": _ts(case.created_at), "lastModified": _ts(case.state_entered_at),
        "owner": _ref("Practitioner", case.owner_id),
        "restriction": {"period": {"end": _ts(case.due_at)}} if case.due_at else None,
    }
    if case.state == "closed":
        case_task["statusReason"] = {"coding": [{"system": SYS + "/closure", "code": case.closure_reason}],
                                     "text": f"{case.closure_type}: {case.closure_note or ''}"}
        if case.closure_evidence_id:
            kind = {"appointment_fulfilled": "Appointment", "care_plan": "CarePlan",
                    "negative_confirmatory_result": "ServiceRequest"}[case.closure_evidence_type or "care_plan"]
            case_task["output"] = [{"type": {"text": "closure-evidence"}, "valueReference": _ref(kind, case.closure_evidence_id)}]
    res.append({k: v for k, v in case_task.items() if v is not None})
    for s in steps:
        res.append({"resourceType": "Task", "id": f"step-{s.id}", "partOf": [_ref("Task", f"case-{case.id}")],
                    "status": TASK_STATUS.get(s.status, "completed"), "intent": "order",
                    "businessStatus": {"coding": [{"system": SYS + "/case-state", "code": s.state}]},
                    "code": {"text": LABELS.get(s.state, s.state)}, "for": _ref("Patient", p.id),
                    "owner": _ref("Practitioner", s.owner_id),
                    "executionPeriod": {k: v for k, v in {"start": _ts(s.started_at), "end": _ts(s.ended_at)}.items() if v},
                    **({"restriction": {"period": {"end": _ts(s.due_at)}}} if s.due_at else {}),
                    "extension": [{"url": BASE + "/StructureDefinition/sla-breached", "valueBoolean": bool(s.breached)}]})
    for sr in db.scalars(select(ServiceRequest).where(ServiceRequest.case_id == case.id)):
        org_ids.add(sr.performer_org_id)
        prac_ids.add(sr.requester_id)
        if sr.performer_id:
            prac_ids.add(sr.performer_id)
        res.append({"resourceType": "ServiceRequest", "id": str(sr.id), "status": SR_STATUS.get(sr.status, "active"),
                    "intent": "order", "category": [{"text": sr.kind}], "code": {"text": sr.code},
                    "priority": {"critical": "stat", "urgent": "urgent", "high": "asap"}.get(sr.priority, "routine"),
                    "subject": _ref("Patient", p.id), "authoredOn": _ts(sr.created_at),
                    "requester": _ref("Practitioner", sr.requester_id),
                    "performer": [_ref("Organization", sr.performer_org_id)] +
                                 ([_ref("Practitioner", sr.performer_id)] if sr.performer_id else []),
                    "basedOn": [_ref("DiagnosticReport", case.report_id)],
                    **({"note": [{"text": sr.clinical_summary}]} if sr.clinical_summary else {})})
        res.append({"resourceType": "Task", "id": f"sr-task-{sr.id}", "status": TASK_BIZ.get(sr.status, "requested"),
                    "intent": "order", "focus": _ref("ServiceRequest", sr.id), "for": _ref("Patient", p.id),
                    "partOf": [_ref("Task", f"case-{case.id}")], "owner": _ref("Organization", sr.performer_org_id),
                    **({"statusReason": {"coding": [{"system": SYS + "/referral-reject", "code": sr.reason_code}],
                                         "text": sr.reason_note}} if sr.reason_code else {})})
    for a in db.scalars(select(Appointment).where(Appointment.case_id == case.id)):
        org_ids.add(a.org_id)
        res.append({"resourceType": "Appointment", "id": str(a.id), "status": APPT_STATUS[a.status],
                    "start": _ts(a.start), "basedOn": [_ref("ServiceRequest", a.service_request_id)] if a.service_request_id else [],
                    "participant": [{"actor": _ref("Patient", p.id), "status": "accepted"},
                                    {"actor": _ref("Organization", a.org_id), "status": "accepted"}],
                    **({"cancelationReason": {"text": f"no-show barrier: {a.barrier_code}"}} if a.barrier_code else {})})
    for cp in db.scalars(select(CarePlan).where(CarePlan.case_id == case.id)):
        res.append({"resourceType": "CarePlan", "id": str(cp.id), "status": cp.status, "intent": "plan",
                    "category": [{"text": cp.kind}], "description": cp.description, "subject": _ref("Patient", p.id),
                    "created": _ts(cp.created_at), "author": _ref("Practitioner", cp.recorded_by_id)})
    for c in db.scalars(select(Communication).where(Communication.case_id == case.id)):
        status = {"completed": "completed", "failed": "stopped", "not-done": "not-done", "pending": "in-progress"}[c.status]
        res.append({"resourceType": "Communication", "id": str(c.id), "status": status,
                    "category": [{"text": c.category}], "medium": [{"text": c.channel}],
                    "subject": _ref("Patient", p.id), "sent": _ts(c.sent_at),
                    "recipient": [_ref("Practitioner", c.recipient_practitioner_id)] if c.recipient_practitioner_id
                    else [_ref("Patient", p.id)],
                    "payload": [{"contentString": c.content}], "language": c.language,
                    "meta": {"tag": [{"system": SYS + "/data", "code": "simulated-channel"}]}})
    for t in db.scalars(select(CallTask).where(CallTask.case_id == case.id)):
        res.append({"resourceType": "Task", "id": f"call-{t.id}", "status": t.status, "intent": "order",
                    "code": {"text": "phone-outreach"}, "for": _ref("Patient", p.id), "owner": _ref("Practitioner", t.owner_id),
                    "partOf": [_ref("Task", f"case-{case.id}")], **({"output": [{"type": {"text": "outcome"},
                                                                               "valueString": t.outcome}]} if t.outcome else {})})
    for cr in db.scalars(select(ConsultRequest).where(ConsultRequest.case_id == case.id)):
        prac_ids.add(cr.target_practitioner_id)
        res.append({"resourceType": "CommunicationRequest", "id": str(cr.id),
                    "status": "completed" if cr.status == "answered" else "active",
                    "category": [{"text": f"e-consult ({cr.requester_type})"}], "priority": cr.urgency if cr.urgency != "high" else "asap",
                    "subject": _ref("Patient", p.id), "recipient": [_ref("Practitioner", cr.target_practitioner_id)],
                    "payload": [{"contentString": cr.question}] + ([{"contentString": "Response: " + cr.response}] if cr.response else []),
                    "authoredOn": _ts(cr.created_at)})
    for q in db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id == case.id)):
        res.append({"resourceType": "QuestionnaireResponse", "id": str(q.id), "status": "completed",
                    "questionnaire": BASE + "/Questionnaire/teach-back", "subject": _ref("Patient", p.id),
                    "authored": _ts(q.created_at),
                    "item": [{"linkId": k, "answer": [{"valueString": v}]} for k, v in q.answers.items()] +
                            [{"linkId": "score", "answer": [{"valueInteger": q.score}]},
                             {"linkId": "passed", "answer": [{"valueBoolean": q.passed}]}] +
                            ([{"linkId": "confidence", "answer": [{"valueInteger": q.confidence}]}] if q.confidence else [])})
    for c in db.scalars(select(Consent).where(Consent.patient_id == p.id)):
        res.append({"resourceType": "Consent", "id": str(c.id), "status": "active" if c.status == "active" else "inactive",
                    "scope": {"coding": [{"system": "http://terminology.hl7.org/CodeSystem/consentscope", "code": "patient-privacy"
                                          if c.scope.startswith(("share", "analytics", "comm", "alt")) else "treatment"}]},
                    "category": [{"coding": [{"system": SYS + "/consent-scope", "code": c.scope}]}],
                    "patient": _ref("Patient", p.id), "dateTime": _ts(c.recorded_at),
                    "provision": {"type": c.decision}})
    for pid in prac_ids:  # PractitionerRole references the practitioner's organisation
        pr = db.get(Practitioner, pid)
        if pr:
            org_ids.add(pr.org_id)
    for oid in sorted(i for i in org_ids if i is not None):
        o = db.get(Organization, oid)
        if o:
            res.append({"resourceType": "Organization", "id": str(o.id), "name": o.name,
                        "type": [{"text": o.kind}], "address": [{"district": o.region, "country": "AE"}]})
    for pid in sorted(prac_ids):
        pr = db.get(Practitioner, pid)
        if pr:
            res.append({"resourceType": "Practitioner", "id": str(pr.id), "name": [{"text": pr.name}], "active": pr.active})
            res.append({"resourceType": "PractitionerRole", "id": f"role-{pr.id}", "practitioner": _ref("Practitioner", pr.id),
                        "organization": _ref("Organization", pr.org_id), "code": [{"text": pr.role}],
                        **({"specialty": [{"text": pr.specialty}]} if pr.specialty else {})})
    if include_audit:
        for ev in db.scalars(select(AuditEvent).where(AuditEvent.case_id == case.id).order_by(AuditEvent.id)):
            res.append({"resourceType": "AuditEvent", "id": f"audit-{ev.id}",
                        "type": {"system": "http://dicom.nema.org/resources/ontology/DCM", "code": "110110",
                                 "display": "Patient Record"},
                        "subtype": [{"system": SYS + "/audit-event", "code": ev.event}], "action": ev.action,
                        "recorded": _ts(ev.ts), "outcome": "0" if ev.outcome == "success" else "4",
                        "agent": [{"who": {"display": ev.actor_display}, "type": {"text": ev.actor_role},
                                   "requestor": ev.actor_role not in ("system", "ml_service")}],
                        "source": {"observer": {"display": "Wasl"}},
                        "entity": [{"what": _ref("Task", f"case-{case.id}")}],
                        "extension": [{"url": BASE + "/StructureDefinition/hash", "valueString": ev.hash}]})
    return {"resourceType": "Bundle", "type": "collection", "timestamp": _ts(utcnow()),
            "meta": {"tag": [{"system": SYS + "/data", "code": "synthetic"}]},
            "entry": [{"fullUrl": f"{BASE}/{r['resourceType']}/{r['id']}", "resource": r} for r in res]}
