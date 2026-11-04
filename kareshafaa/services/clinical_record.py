"""Complete clinical record of a patient for the doctor's case screen (staff only).

Reuses what the app already stores (DiagnosticReport/Observation, ServiceRequest, Appointment, CarePlan,
Communication, CallTask, QuestionnaireResponse, ConsultRequest, IngestMessage) and adds the patient-level FHIR
resources the screening loop did not need before: Encounter (consultations with clinician notes),
MedicationRequest, AllergyIntolerance, Condition and Investigation (Observation outside the programme).

Access: reading needs case:read on THIS case (org scoping + share consent as everywhere else); details of the
patient's OTHER cases are only included when the reader may view those cases too. Writing needs record:write
(GP, specialist, clinical lead). Clinician notes are never part of the patient API (/api/me).
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import (AllergyIntolerance, Appointment, CallTask, CarePlan, Case, Communication, Condition,
                      ConsultRequest, DiagnosticReport, Encounter, IngestMessage, Investigation, MedicationRequest,
                      Organization, Patient, Practitioner, QuestionnaireResponse, ServiceRequest, utcnow)
from ..security import Actor
from .access import assert_can_view
from .errors import Forbidden, Invalid, NotFound

NONE = "None recorded"
ENCOUNTER_KINDS = {
    "gp_visit": "GP visit", "specialist_visit": "Specialist clinic", "colonoscopy": "Colonoscopy",
    "colposcopy": "Colposcopy", "breast_clinic": "Breast clinic assessment", "teleconsult": "Teleconsultation",
    "phone_call": "Phone call", "nurse_visit": "Nurse visit", "e_consult": "e-consultation",
    "other": "Other encounter",
}
MED_STATUSES = ("active", "stopped", "completed")
LANG_NAMES = {"en": "English", "ar": "Arabic", "hi": "Hindi", "ur": "Urdu", "tl": "Tagalog", "ml": "Malayalam"}
ROLE_NAMES = {"gp": "Family doctor (GP)", "specialist": "Specialist", "clinical_lead": "Clinical lead",
              "coordinator": "Care coordinator"}
APPT_STATUS = {"booked": "Booked", "fulfilled": "Attended", "noshow": "Missed (no-show)", "cancelled": "Cancelled"}
SOURCE_NAMES = {"manual": "Entered in KareShafaa", "fhir": "Imported (FHIR)", "sample_backfill": "Sample history",
                "app": "KareShafaa"}


def iso(d: datetime | None) -> str | None:
    return d.isoformat() if d else None


def _prac(db: Session, pid: int | None) -> Practitioner | None:
    return db.get(Practitioner, pid) if pid else None


def _pname(db: Session, pid: int | None) -> str | None:
    p = _prac(db, pid)
    return p.name.split(" (")[0] if p else None


def _prole(db: Session, pid: int | None) -> str | None:
    p = _prac(db, pid)
    if not p:
        return None
    role = ROLE_NAMES.get(p.role, p.role)
    if p.role == "specialist" and "(" in p.name:
        role = p.name.split("(", 1)[1].rstrip(")")
    return role


def _oname(db: Session, oid: int | None) -> str | None:
    o = db.get(Organization, oid) if oid else None
    return o.name if o else None


def _viewable(db: Session, actor: Actor, c: Case) -> bool:
    try:
        assert_can_view(db, actor, c)
        return True
    except Forbidden:
        return False


def _contact(p: Patient) -> str:
    ch = [x for x, ok in (("App", p.has_smartphone), ("WhatsApp", p.whatsapp), ("Phone call / SMS", p.phone_valid))
          if ok]
    return " → ".join(ch) if ch else "No valid phone or app – letter or home visit"


_CHANNEL_NAMES = {"app": "the patient app", "sms": "SMS", "whatsapp": "WhatsApp", "phone": "phone calls"}


def _contact_details(db: Session, p: Patient, actor: Actor) -> dict[str, Any]:
    """Phone, email, address, preferred channel and contact consent (FHIR Patient.telecom / address / Consent) for the
    roles that contact the patient. Roles without patient-record access get no contact details at all."""
    if not actor.can("patient:read"):
        return {"hidden": True}
    from .access import consent_status
    from .patients import ABU_DHABI_REGIONS, can_write_patient

    emirate = p.emirate or ("Abu Dhabi" if p.region in ABU_DHABI_REGIONS else None)
    addr = ", ".join(x for x in (p.address_line, p.city, emirate if emirate != p.city else None) if x) or None
    refused = [_CHANNEL_NAMES[k[5:]] for k in ("comm_app", "comm_sms", "comm_whatsapp", "comm_phone")
               if consent_status(db, p.id, k) == "deny"]
    return {"patient_id": p.id, "phone": p.phone, "phone_valid": bool(p.phone_valid), "email": p.email, "address": addr,
            "whatsapp": bool(p.whatsapp and p.phone),
            "preferred_channel": _CHANNEL_NAMES.get(p.preferred_channel or "", None),
            "consent": consent_status(db, p.id, "care_coordination"), "channels_refused": refused,
            "can_edit": can_write_patient(db, actor, p)}  # same roles that edit phone / address on the record


def _insurance(p: Patient, actor: Actor, now: datetime) -> dict[str, Any] | None:
    if not actor.can("patient:read"):
        return None
    from .coverage import summary_tile

    return summary_tile(p, actor, now.date())


def _tb_text(case: Case, answers: dict) -> list[dict]:
    from ..comms.service import teach_back_form
    form = teach_back_form(case, "en")
    out = []
    for q in form["questions"]:
        opt = {o["key"]: o["text"] for o in q["options"]}
        a = (answers or {}).get(q["id"])
        out.append({"question": q["text"], "answer": opt.get(a, a or NONE)})
    return out


def build_record(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> dict[str, Any]:
    now = now or utcnow()
    p: Patient = case.patient
    all_cases = db.scalars(select(Case).where(Case.patient_id == p.id).order_by(Case.id)).all()
    seen = [c for c in all_cases if c.id == case.id or _viewable(db, actor, c)]
    seen_ids = {c.id for c in seen}
    hidden_cases = len(all_cases) - len(seen)
    gp = _prac(db, p.gp_id)

    allergies = db.scalars(select(AllergyIntolerance).where(AllergyIntolerance.patient_id == p.id)
                           .order_by(AllergyIntolerance.id)).all()
    conds = db.scalars(select(Condition).where(Condition.patient_id == p.id).order_by(Condition.id)).all()
    flags = []
    if p.pregnant:
        flags.append({"display": "Pregnant (recorded on the patient record)", "status": "active"})
    if p.haemoglobinopathy:
        flags.append({"display": "Haemoglobin variant / haemoglobinopathy (affects HbA1c interpretation)",
                      "status": "active"})
    if p.specialist_care:
        flags.append({"display": f"Already under specialist care: {p.specialist_care.replace('_', ' ')}",
                      "status": "active"})
    summary = {
        "name": f"{p.given_name} {p.family_name}", "mrn": p.mrn, "age": max(0, now.year - p.birth_year),
        "birth_year": p.birth_year, "sex": p.gender, "language": LANG_NAMES.get(p.language, p.language),
        "region": p.region, "contact_preference": _contact(p), "alt_contact": p.alt_contact,
        "contact": _contact_details(db, p, actor), "insurance": _insurance(p, actor, now),
        "gp": gp.name if gp else None, "managing_org": _oname(db, p.managing_org_id), "deceased": p.deceased,
        "deceased_record": {"note": p.deceased_note, "at": p.deceased_recorded_at.isoformat() if p.deceased_recorded_at else None,
                            "by": p.deceased_recorded_by} if p.deceased else None,
        "death_corrected": {"note": p.deceased_corrected_note, "by": p.deceased_corrected_by,
                            "at": p.deceased_corrected_at.isoformat() if p.deceased_corrected_at else None}
        if (not p.deceased and p.deceased_corrected_at) else None,
        "allergies": [{"substance": a.substance, "reaction": a.reaction, "severity": a.severity, "status": a.status,
                       "nkda": a.substance.lower().startswith("no known"), "recorded_at": iso(a.recorded_at),
                       "source": SOURCE_NAMES.get(a.source, a.source)} for a in allergies],
        "conditions": [{"display": c.display, "code": c.code, "status": c.clinical_status, "onset": iso(c.onset),
                        "source": SOURCE_NAMES.get(c.source, c.source)} for c in conds] + flags,
    }

    # ---- tests / investigations (screening + confirmatory reports, then other investigations)
    tests = []
    reps = db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == p.id)
                      .order_by(DiagnosticReport.issued_at.desc())).all()
    for r in reps:
        if r.case_id is not None and r.case_id not in seen_ids and r.id != case.report_id:
            continue
        if r.category == "confirmatory":
            res, abnormal = r.conclusion.capitalize(), r.conclusion == "positive"
        else:
            res = "; ".join(f"{o.value if o.value is not None else (o.value_text or '')}"
                            f"{(' ' + o.unit) if o.unit else ''}".strip() for o in r.observations) or r.conclusion
            abnormal = r.conclusion in ("actionable", "needs_review") or any(
                o.interpretation in ("H", "A", "POS", "L") for o in r.observations)
        sr = None
        if r.category == "confirmatory" and r.case_id:
            sr = db.scalars(select(ServiceRequest).where(ServiceRequest.case_id == r.case_id,
                                                         ServiceRequest.kind == "confirmatory_test")
                            .order_by(ServiceRequest.id.desc())).first()
        tests.append({"date": iso(r.issued_at), "name": r.display, "category": r.category, "result": res,
                      "abnormal": bool(abnormal), "interpretation": r.conclusion.replace("_", " "),
                      "source": _oname(db, r.performer_org_id),
                      "ordered_by": r.ordering_clinician or (_pname(db, sr.requester_id) if sr else "Screening programme"),
                      "screening_site": r.screening_site, "collected_at": iso(r.specimen_collected_at),
                      "triggering": r.id == case.report_id, "case_id": r.case_id, "programme": r.programme})
    for i in db.scalars(select(Investigation).where(Investigation.patient_id == p.id)):
        tests.append({"date": iso(i.effective_at), "name": i.name, "category": "investigation",
                      "result": f"{i.value or ''}{(' ' + i.unit) if i.unit else ''}".strip() or NONE,
                      "reference_range": i.reference_range, "abnormal": i.flag != "N",
                      "interpretation": {"N": "normal", "H": "high", "L": "low", "A": "abnormal"}.get(i.flag, i.flag),
                      "source": i.performer, "ordered_by": None, "record_source": SOURCE_NAMES.get(i.source, i.source),
                      "triggering": False,
                      "case_id": i.case_id, "programme": None})
    tests.sort(key=lambda t: t["date"] or "", reverse=True)

    # ---- consultations / encounters
    encs = []
    enc_rows = db.scalars(select(Encounter).where(Encounter.patient_id == p.id)).all()
    linked_appts = {e.appointment_id for e in enc_rows if e.appointment_id}
    for e in enc_rows:
        if e.case_id is not None and e.case_id not in seen_ids:
            continue
        encs.append({"id": e.id, "date": iso(e.start), "kind": e.kind, "type": ENCOUNTER_KINDS.get(e.kind, e.kind),
                     "clinician": _pname(db, e.practitioner_id) or e.clinician_display or NONE,
                     "role": _prole(db, e.practitioner_id), "org": _oname(db, e.org_id), "reason": e.reason,
                     "notes": e.notes, "outcome": e.outcome, "source": SOURCE_NAMES.get(e.source, e.source),
                     "case_id": e.case_id})
    for t in db.scalars(select(CallTask).where(CallTask.case_id.in_(seen_ids), CallTask.status == "completed")):
        encs.append({"date": iso(t.completed_at or t.created_at), "kind": "phone_call", "type": "Phone call",
                     "clinician": _pname(db, t.owner_id), "role": _prole(db, t.owner_id), "org": None,
                     "reason": "Result / follow-up outreach call", "notes": None,
                     "outcome": (t.outcome or "").replace("_", " ") or NONE, "source": "KareShafaa", "case_id": t.case_id})
    for q in db.scalars(select(ConsultRequest).where(ConsultRequest.case_id.in_(seen_ids))):
        encs.append({"date": iso(q.created_at), "kind": "e_consult",
                     "type": "Patient question" if q.requester_type == "patient" else "e-consultation",
                     "clinician": _pname(db, q.target_practitioner_id), "role": _prole(db, q.target_practitioner_id),
                     "org": None, "reason": q.question, "notes": q.response,
                     "outcome": "Answered" if q.status == "answered" else "Awaiting answer", "source": "KareShafaa",
                     "case_id": q.case_id})
    for a in db.scalars(select(Appointment).where(Appointment.case_id.in_(seen_ids), Appointment.status == "fulfilled")):
        if a.id in linked_appts:
            continue
        encs.append({"date": iso(a.start), "kind": "specialist_visit", "type": "Specialist appointment (attended)",
                     "clinician": _pname(db, a.practitioner_id) or NONE, "role": _prole(db, a.practitioner_id),
                     "org": _oname(db, a.org_id), "reason": None, "notes": None, "outcome": "Attended",
                     "source": "KareShafaa", "case_id": a.case_id})
    encs.sort(key=lambda e: e["date"] or "", reverse=True)

    # ---- medicines
    meds = db.scalars(select(MedicationRequest).where(MedicationRequest.patient_id == p.id)).all()
    meds_out = [{"id": m.id, "name": m.name, "dose": m.dose, "frequency": m.frequency, "route": m.route,
                 "start": iso(m.start_date), "end": iso(m.end_date), "status": m.status,
                 "prescriber": _pname(db, m.prescriber_id) or m.prescriber_display or NONE,
                 "reason": m.reason, "note": m.note, "stop_reason": m.stop_reason,
                 "source": SOURCE_NAMES.get(m.source, m.source)} for m in meds]
    meds_out.sort(key=lambda m: m["start"] or "", reverse=True)
    meds_out.sort(key=lambda m: m["status"] != "active")  # active first, newest first within each group

    # ---- other case details (this patient's cases the reader may view)
    referrals = [{"date": iso(s.created_at), "kind": s.kind, "service": s.code, "to": _oname(db, s.performer_org_id),
                  "specialist": _pname(db, s.performer_id), "requester": _pname(db, s.requester_id),
                  "priority": s.priority, "status": s.status, "result": s.result, "summary": s.clinical_summary,
                  "decided_at": iso(s.decided_at), "reason": (s.reason_code or "").replace("_", " ") or None,
                  "case_id": s.case_id}
                 for s in db.scalars(select(ServiceRequest).where(ServiceRequest.case_id.in_(seen_ids))
                                     .order_by(ServiceRequest.created_at.desc()))]
    appts = [{"date": iso(a.start), "org": _oname(db, a.org_id), "clinician": _pname(db, a.practitioner_id),
              "status": a.status, "status_label": APPT_STATUS.get(a.status, a.status),
              "upcoming": a.status == "booked" and a.start > now, "barrier": (a.barrier_code or "").replace("_", " ") or None, "note": a.booking_note,
              "case_id": a.case_id}
             for a in db.scalars(select(Appointment).where(Appointment.case_id.in_(seen_ids))
                                 .order_by(Appointment.start.desc()))]
    outreach = [{"date": iso(m.sent_at), "what": m.category.replace("_", " "), "channel": m.channel,
                 "status": m.status, "attempt": m.attempt_no, "case_id": m.case_id}
                for m in db.scalars(select(Communication).where(Communication.case_id.in_(seen_ids),
                                                                Communication.patient_id == p.id)
                                    .order_by(Communication.sent_at.desc()))]
    outreach += [{"date": iso(t.completed_at or t.created_at), "what": "phone call", "channel": "phone",
                  "status": (t.outcome or t.status).replace("_", " "), "attempt": None, "case_id": t.case_id}
                 for t in db.scalars(select(CallTask).where(CallTask.case_id.in_(seen_ids)))]
    outreach.sort(key=lambda o: o["date"] or "", reverse=True)
    responses = []
    by_id = {c.id: c for c in seen}
    for q in db.scalars(select(QuestionnaireResponse).where(QuestionnaireResponse.case_id.in_(seen_ids))
                        .order_by(QuestionnaireResponse.created_at.desc())):
        responses.append({"date": iso(q.created_at), "kind": "teach_back",
                          "title": f"Teach-back ({'by the patient' if q.mode == 'self' else 'with a navigator'})",
                          "result": f"{q.score}/{q.total} – {'understood' if q.passed else 'not yet clear'}",
                          "passed": q.passed, "confidence": q.confidence,
                          "answers": _tb_text(by_id[q.case_id], q.answers), "case_id": q.case_id})
    for q in db.scalars(select(ConsultRequest).where(ConsultRequest.case_id.in_(seen_ids),
                                                     ConsultRequest.requester_type == "patient")):
        responses.append({"date": iso(q.created_at), "kind": "question", "title": "Question from the patient",
                          "result": q.question, "answers": [], "case_id": q.case_id})
    responses.sort(key=lambda r: r["date"] or "", reverse=True)
    plans = [{"date": iso(cp.created_at), "kind": cp.kind, "description": cp.description,
              "by": _pname(db, cp.recorded_by_id), "case_id": cp.case_id}
             for cp in db.scalars(select(CarePlan).where(CarePlan.case_id.in_(seen_ids))
                                  .order_by(CarePlan.created_at.desc()))]
    docs = []
    if actor.can("fhir:export"):
        docs.append({"title": "Full case record (FHIR R4 bundle)", "date": iso(now), "href": f"/api/cases/{case.id}/fhir",
                     "kind": "FHIR"})
    for s in referrals:
        if s["kind"] == "referral" and s["summary"]:
            docs.append({"title": f"Referral letter – {s['service']} ({s['to']})", "date": s["date"], "href": None,
                         "kind": "Referral", "text": s["summary"]})
    for cp in plans:
        docs.append({"title": f"Care plan – {cp['kind']}", "date": cp["date"], "href": None, "kind": "Care plan",
                     "text": cp["description"]})
    for m in db.scalars(select(IngestMessage).where(IngestMessage.case_id.in_(seen_ids))):
        docs.append({"title": f"Laboratory message ({m.source})", "date": iso(m.received_at), "href": None,
                     "kind": "Result message", "text": f"Message {m.message_id} – {m.status.replace('_', ' ')}"})
    docs.sort(key=lambda d: d["date"] or "", reverse=True)

    other_cases = [{"id": c.id, "programme": c.programme, "state": c.state, "created_at": iso(c.created_at),
                    "closed_at": iso(c.closed_at), "current": c.id == case.id} for c in seen]
    return {"case_id": case.id, "generated_at": iso(now), "summary": summary, "tests": tests, "encounters": encs,
            "medications": meds_out, "referrals": referrals, "appointments": appts, "outreach": outreach[:60],
            "outreach_total": len(outreach), "responses": responses, "care_plans": plans, "documents": docs,
            "cases": other_cases, "hidden_cases": hidden_cases,
            "can_write": actor.can("record:write"), "encounter_kinds": ENCOUNTER_KINDS, "none": NONE}


# ------------------------------------------------------------------------------------------ writes (audited)
def _clean(v: str | None, n: int) -> str | None:
    v = (v or "").strip()
    return v[:n] or None


def add_encounter(db: Session, case: Case, actor: Actor, *, kind: str, start: datetime, notes: str,
                  reason: str | None = None, outcome: str | None = None, now: datetime | None = None) -> Encounter:
    now = now or utcnow()
    if not actor.can("record:write"):
        raise Forbidden("your role cannot add consultation notes")
    if kind not in ENCOUNTER_KINDS:
        raise Invalid("unknown consultation type")
    if start > now + timedelta(hours=1):
        raise Invalid("a consultation note is for a consultation that has happened (date is in the future)")
    if start < now - timedelta(days=365 * 30):
        raise Invalid("consultation date is implausibly old")
    if len((notes or "").strip()) < 5:
        raise Invalid("write a short clinical note (at least 5 characters)")
    pr = _prac(db, actor.practitioner_id)
    e = Encounter(patient_id=case.patient_id, case_id=case.id, kind=kind, start=start,
                  practitioner_id=actor.practitioner_id, org_id=pr.org_id if pr else None,
                  reason=_clean(reason, 300), notes=notes.strip()[:5000], outcome=_clean(outcome, 300),
                  source="manual", recorded_by_user_id=actor.user_id, created_at=now)
    db.add(e)
    db.flush()
    audit.record(db, actor, "C", "encounter.recorded", entity_type="Encounter", entity_id=e.id, case_id=case.id,
                 ts=now, detail={"kind": kind, "start": start.isoformat(), "note_chars": len(e.notes or "")})
    return e


def add_medication(db: Session, case: Case, actor: Actor, *, name: str, dose: str | None, frequency: str | None,
                   route: str | None = None, start_date: datetime | None = None, end_date: datetime | None = None,
                   status: str = "active", reason: str | None = None, note: str | None = None,
                   now: datetime | None = None) -> MedicationRequest:
    now = now or utcnow()
    if not actor.can("record:write"):
        raise Forbidden("your role cannot record medicines")
    if status not in MED_STATUSES:
        raise Invalid("status must be active, stopped or completed")
    if len((name or "").strip()) < 2:
        raise Invalid("medicine name is required")
    if start_date and end_date and end_date < start_date:
        raise Invalid("stop date is before the start date")
    if status == "active" and end_date and end_date < now - timedelta(days=1):
        raise Invalid("an active medicine cannot have a stop date in the past - mark it stopped or completed")
    m = MedicationRequest(patient_id=case.patient_id, case_id=case.id, name=name.strip()[:200],
                          dose=_clean(dose, 80), frequency=_clean(frequency, 80), route=_clean(route, 40),
                          start_date=start_date or now, end_date=end_date, status=status,
                          prescriber_id=actor.practitioner_id, reason=_clean(reason, 300), note=_clean(note, 2000),
                          source="manual", recorded_by_user_id=actor.user_id, created_at=now)
    db.add(m)
    db.flush()
    audit.record(db, actor, "C", "medication.recorded", entity_type="MedicationRequest", entity_id=m.id,
                 case_id=case.id, ts=now, detail={"name": m.name, "dose": m.dose, "frequency": m.frequency,
                                                  "status": m.status})
    return m


def stop_medication(db: Session, case: Case, actor: Actor, med_id: int, reason: str,
                    now: datetime | None = None) -> MedicationRequest:
    now = now or utcnow()
    if not actor.can("record:write"):
        raise Forbidden("your role cannot change medicines")
    m = db.get(MedicationRequest, med_id)
    if m is None or m.patient_id != case.patient_id:
        raise NotFound("medicine not found for this patient")
    if m.status != "active":
        raise Invalid("this medicine is not active")
    if len((reason or "").strip()) < 3:
        raise Invalid("give a reason for stopping")
    m.status, m.end_date, m.stop_reason = "stopped", now, reason.strip()[:300]
    db.flush()
    audit.record(db, actor, "U", "medication.stopped", entity_type="MedicationRequest", entity_id=m.id,
                 case_id=case.id, ts=now, detail={"name": m.name, "reason": m.stop_reason})
    return m


# ------------------------------------------------------------------------------------------ FHIR R4 import
def _txt(cc: Any) -> str | None:
    if not isinstance(cc, dict):
        return None
    return cc.get("text") or next((c.get("display") or c.get("code") for c in cc.get("coding", []) if isinstance(c, dict)), None)


def _dt(v: Any) -> datetime | None:
    if not v or not isinstance(v, str):
        return None
    try:
        from ..calendar_uae import as_utc_naive
        d = datetime.fromisoformat(v.replace("Z", "+00:00")) if "T" in v else datetime.fromisoformat(v[:10])
        return as_utc_naive(d) if d.tzinfo else d
    except ValueError:
        return None


def _enc_kind(e: dict) -> str:
    t = " ".join(filter(None, [_txt(x) for x in e.get("type", []) if isinstance(x, dict)] +
                        [(e.get("class") or {}).get("code") if isinstance(e.get("class"), dict) else None])).lower()
    for key, kind in (("colonoscop", "colonoscopy"), ("colposcop", "colposcopy"), ("breast", "breast_clinic"),
                      ("tele", "teleconsult"), ("vr", "teleconsult"), ("phone", "phone_call"), ("nurse", "nurse_visit"),
                      ("gp", "gp_visit"), ("general practi", "gp_visit"), ("family", "gp_visit"),
                      ("specialist", "specialist_visit"), ("clinic", "specialist_visit"), ("amb", "gp_visit")):
        if key in t:
            return kind
    return "other"


def import_fhir_history(db: Session, bundle: dict, actor: Actor, now: datetime | None = None) -> dict:
    """Import Encounter / MedicationRequest / MedicationStatement / AllergyIntolerance / Condition / Observation
    resources for ONE existing patient (matched by identifier, birth year checked when given). Idempotent per
    resource id. Screening-programme results must use the results inbox (they drive the clinical rules)."""
    from .ingest import LOINC
    now = now or utcnow()
    if not actor.can("record:write"):
        raise Forbidden("your role cannot import clinical history")
    if not isinstance(bundle, dict) or bundle.get("resourceType") != "Bundle" or not isinstance(bundle.get("entry"), list):
        raise Invalid("expected a FHIR Bundle with entries")
    res = [e.get("resource") for e in bundle["entry"] if isinstance(e, dict) and isinstance(e.get("resource"), dict)]
    pats = [r for r in res if r.get("resourceType") == "Patient"]
    if len(pats) != 1:
        raise Invalid("the Bundle must contain exactly one Patient")
    ids = [i.get("value") for i in pats[0].get("identifier", []) if isinstance(i, dict) and i.get("value")]
    p = db.scalars(select(Patient).where(or_(Patient.mrn.in_(ids), Patient.national_id.in_(ids)))).all() if ids else []
    if len(p) != 1:
        raise NotFound("patient not found (or identifier is ambiguous) - nothing imported")
    p = p[0]
    bd = str(pats[0].get("birthDate") or "")
    if bd[:4].isdigit() and int(bd[:4]) != p.birth_year:
        raise Invalid("identifier found but birth year differs - nothing imported")
    counts: dict[str, int] = {}
    skipped = 0

    def _exists(model, ext):
        return ext and db.scalar(select(model.id).where(model.patient_id == p.id, model.source == "fhir",
                                                        model.external_id == ext)) is not None

    for r in res:
        rt, ext = r.get("resourceType"), str(r.get("id") or "")[:120] or None
        if rt == "Encounter":
            if _exists(Encounter, ext):
                skipped += 1
                continue
            per = r.get("period") or {}
            start = _dt(per.get("start")) or now
            part = next((x.get("individual", {}).get("display") for x in r.get("participant", []) if isinstance(x, dict)), None)
            note = next((x.get("valueString") for x in r.get("extension", []) if isinstance(x, dict) and x.get("valueString")), None)
            db.add(Encounter(patient_id=p.id, kind=_enc_kind(r), start=min(start, now), clinician_display=part,
                             reason=_clean(next((_txt(x) for x in r.get("reasonCode", [])), None), 300),
                             notes=_clean(note, 5000), outcome=_clean(_txt((r.get("hospitalization") or {}).get("dischargeDisposition")), 300),
                             status=str(r.get("status") or "finished")[:20], source="fhir", external_id=ext,
                             recorded_by_user_id=actor.user_id, created_at=now))
        elif rt in ("MedicationRequest", "MedicationStatement"):
            if _exists(MedicationRequest, ext):
                skipped += 1
                continue
            name = _txt(r.get("medicationCodeableConcept")) or (r.get("medicationReference") or {}).get("display")
            if not name:
                skipped += 1
                continue
            dosage = (r.get("dosageInstruction") or r.get("dosage") or [{}])[0] or {}
            dq = ((dosage.get("doseAndRate") or [{}])[0] or {}).get("doseQuantity") or {}
            dose = f"{dq.get('value')} {dq.get('unit') or ''}".strip() if dq.get("value") is not None else None
            per = r.get("effectivePeriod") or ((r.get("dispenseRequest") or {}).get("validityPeriod")) or {}
            st = {"active": "active", "on-hold": "active", "stopped": "stopped", "cancelled": "stopped",
                  "completed": "completed", "entered-in-error": None}.get(r.get("status"), "active")
            if st is None:
                skipped += 1
                continue
            who = (r.get("requester") or r.get("informationSource") or {}).get("display")
            db.add(MedicationRequest(patient_id=p.id, name=name[:200], dose=dose or _clean(dosage.get("text"), 80),
                                     frequency=_clean(_txt((dosage.get("timing") or {}).get("code")) or dosage.get("text"), 80),
                                     route=_clean(_txt(dosage.get("route")), 40),
                                     start_date=_dt(r.get("authoredOn")) or _dt(per.get("start")) or _dt(r.get("effectiveDateTime")),
                                     end_date=_dt(per.get("end")), status=st, prescriber_display=_clean(who, 200),
                                     reason=_clean(next((_txt(x) for x in r.get("reasonCode", [])), None), 300),
                                     note=_clean(next((x.get("text") for x in r.get("note", []) if isinstance(x, dict)), None), 2000),
                                     source="fhir", external_id=ext, recorded_by_user_id=actor.user_id, created_at=now))
        elif rt == "AllergyIntolerance":
            if _exists(AllergyIntolerance, ext):
                skipped += 1
                continue
            rx = (r.get("reaction") or [{}])[0] or {}
            db.add(AllergyIntolerance(patient_id=p.id, substance=(_txt(r.get("code")) or "Unknown substance")[:200],
                                      reaction=_clean(next((_txt(m) for m in rx.get("manifestation", [])), None), 200),
                                      severity=_clean(rx.get("severity"), 20),
                                      status=(_txt(r.get("clinicalStatus")) or "active")[:20],
                                      recorded_at=_dt(r.get("recordedDate")) or now, source="fhir", external_id=ext))
        elif rt == "Condition":
            if _exists(Condition, ext):
                skipped += 1
                continue
            code = next((c.get("code") for c in (r.get("code") or {}).get("coding", []) if isinstance(c, dict)), None)
            db.add(Condition(patient_id=p.id, display=(_txt(r.get("code")) or "Unspecified condition")[:200],
                             code=_clean(code, 40), clinical_status=(_txt(r.get("clinicalStatus")) or "active")[:20],
                             onset=_dt(r.get("onsetDateTime")), recorded_at=_dt(r.get("recordedDate")) or now,
                             source="fhir", external_id=ext))
        elif rt == "Observation":
            codes = [c.get("code") for c in (r.get("code") or {}).get("coding", []) if isinstance(c, dict)]
            if any(c in LOINC for c in codes):
                skipped += 1  # programme results go through the results inbox and the clinical rules
                continue
            if _exists(Investigation, ext):
                skipped += 1
                continue
            q = r.get("valueQuantity") or {}
            val = str(q.get("value")) if q.get("value") is not None else (r.get("valueString") or _txt(r.get("valueCodeableConcept")))
            rr = (r.get("referenceRange") or [{}])[0] or {}
            rtxt = rr.get("text") or (f"{(rr.get('low') or {}).get('value', '')}–{(rr.get('high') or {}).get('value', '')}"
                                      if rr.get("low") or rr.get("high") else None)
            interp = next((c.get("code") for i in r.get("interpretation", []) if isinstance(i, dict)
                           for c in i.get("coding", []) if isinstance(c, dict)), "N")
            flag = {"H": "H", "HH": "H", "L": "L", "LL": "L", "A": "A", "AA": "A", "POS": "A", "N": "N"}.get(interp, "A")
            db.add(Investigation(patient_id=p.id, name=(_txt(r.get("code")) or "Observation")[:200],
                                 code=_clean(codes[0] if codes else None, 40), value=_clean(val, 200),
                                 unit=_clean(q.get("unit"), 30), reference_range=_clean(rtxt, 60), flag=flag,
                                 effective_at=_dt(r.get("effectiveDateTime")) or now,
                                 performer=_clean(next((x.get("display") for x in r.get("performer", []) if isinstance(x, dict)), None), 200),
                                 source="fhir", external_id=ext, created_at=now))
        else:
            continue
        counts[rt] = counts.get(rt, 0) + 1
    db.flush()
    audit.record(db, actor, "C", "fhir.history_imported", entity_type="Patient", entity_id=p.id, ts=now,
                 detail={"counts": counts, "skipped": skipped, "bundle": str(bundle.get("id") or "")[:80]})
    return {"patient_id": p.id, "imported": counts, "skipped": skipped}
