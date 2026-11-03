"""Interoperability descriptors: FHIR R4 CapabilityStatement, integration status (HL7 v2 and FHIR inbound),
Malaffi (Abu Dhabi HIE) readiness and the emirate deployment profiles."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from . import __version__
from .models import IngestMessage, utcnow

EXPORTED = ["Patient", "Practitioner", "PractitionerRole", "Organization", "Task", "DiagnosticReport", "Observation",
            "ServiceRequest", "Appointment", "CarePlan", "Communication", "CommunicationRequest", "Consent",
            "QuestionnaireResponse", "Condition", "Encounter", "MedicationRequest", "AllergyIntolerance", "AuditEvent"]
ACCEPTED = ["Patient", "DiagnosticReport", "Observation", "Specimen", "ServiceRequest", "Practitioner", "Encounter"]


def capability_statement(base_url: str) -> dict:
    """What this system does with FHIR R4 today, stated exactly (it is not a general FHIR server)."""
    res = []
    for t in sorted(set(EXPORTED) | set(ACCEPTED)):
        doc = []
        if t in ACCEPTED:
            doc.append("accepted inside an inbound result Bundle (POST /api/ingest/fhir)")
        if t in EXPORTED:
            doc.append("returned inside the per-case export Bundle (GET /api/cases/{id}/fhir)")
        res.append({"type": t, "profile": f"http://hl7.org/fhir/StructureDefinition/{t}", "documentation": "; ".join(doc)})
    return {
        "resourceType": "CapabilityStatement", "id": "kareshafaa", "status": "draft", "experimental": True,
        "date": utcnow().date().isoformat(), "publisher": "KareShafaa", "kind": "instance",
        "name": "KareShafaaCapabilityStatement", "title": "KareShafaa screening-to-care loop - FHIR R4 capabilities",
        "description": "Inbound screening results as FHIR R4 Bundles or HL7 v2 ORU^R01 messages; outbound FHIR R4 "
                       "collection Bundle per case. Search and per-resource REST reads are not offered. Prototype "
                       "software with synthetic data, not a certified medical device.",
        "software": {"name": "KareShafaa", "version": __version__},
        "implementation": {"description": "KareShafaa API", "url": base_url.rstrip("/")},
        "fhirVersion": "4.0.1", "format": ["json"],
        "rest": [{
            "mode": "server",
            "documentation": "Every call needs a signed-in session or bearer token and is checked against the caller's "
                             "role and organisation; every read and write is written to the hash-chained audit trail.",
            "security": {"cors": False, "description": "Session cookie (HttpOnly, SameSite) with CSRF token, or bearer "
                                                       "token; role-based access control. SMART on FHIR is a pilot step."},
            "resource": res,
            "operation": [
                {"name": "ingest-bundle", "definition": "urn:kareshafaa:operation:ingest-bundle",
                 "documentation": "POST /api/ingest/fhir - idempotent on Bundle.identifier; returns an acknowledgement "
                                  "(accepted, needs match review, or rejected with the reason)."},
                {"name": "export-case", "definition": "urn:kareshafaa:operation:export-case",
                 "documentation": "GET /api/cases/{id}/fhir - collection Bundle with the patient, result, Task history, "
                                  "referrals, appointments, care plan, messages, consents and audit events."}],
        }],
    }


def integration_status(db: Session) -> dict:
    now = utcnow()
    out = []
    for src, label in (("hl7v2", "HL7 v2 ORU^R01 results"), ("fhir", "FHIR R4 result Bundles")):
        q = select(IngestMessage.status, func.count(IngestMessage.id)).where(IngestMessage.source == src)
        all_ = dict(db.execute(q.group_by(IngestMessage.status)).all())
        week = dict(db.execute(q.where(IngestMessage.received_at >= now - timedelta(days=7)).group_by(IngestMessage.status)).all())
        last = db.scalar(select(func.max(IngestMessage.received_at)).where(IngestMessage.source == src))
        out.append({"source": src, "label": label, "endpoint": "/api/ingest/hl7v2" if src == "hl7v2" else "/api/ingest/fhir",
                    "total": sum(all_.values()), "last_7_days": sum(week.values()), "by_status": all_,
                    "waiting_for_match_review": all_.get("needs_match_review", 0), "rejected": all_.get("rejected", 0),
                    "last_received_at": last.isoformat() if last else None,
                    "state": "receiving" if week else ("idle" if last else "ready, nothing received yet")})
    return {"inbound": out, "outbound": [
        {"label": "FHIR R4 case export", "endpoint": "/api/cases/{id}/fhir", "state": "available"},
        {"label": "FHIR R4 CapabilityStatement", "endpoint": "/fhir/metadata", "state": "available"},
        {"label": "Patient messages (SMS / WhatsApp / app)", "endpoint": "channel adapter", "state": "simulated - no gateway connected"},
        {"label": "Malaffi (Abu Dhabi HIE)", "endpoint": "-", "state": "not connected (see readiness)"}]}


# Sources checked 3 Nov 2026: https://www.malaffi.ae/providers/facilities-required-to-connect/ ,
# https://www.doh.gov.ae/en/resources/HIE , https://www.malaffi.ae/providers/resources/
MALAFFI_READINESS = [
    {"item": "HL7-capable system", "need": "Malaffi requires an HL7-capable EMR to connect.",
     "status": "ready", "evidence": "HL7 v2 ORU^R01 inbound with ACK AA/AE, idempotent on MSH-10; FHIR R4 Bundles in and out."},
    {"item": "DoH demographic data standards", "need": "Emirates ID and passport, name, date of birth, nationality, phone, email.",
     "status": "ready", "evidence": "Captured at registration with the same rules (Emirates ID check digit, E.164 phone)."},
    {"item": "DoH code sets for laboratory results", "need": "Results coded with the DoH mandated code sets and units.",
     "status": "to do", "evidence": "Tests are recognised by LOINC code; mapping to the DoH code sets is a pilot task."},
    {"item": "Security assessment", "need": "Pass the Malaffi security assessment (an excerpt of the DoH ADHICS survey).",
     "status": "partly", "evidence": "Access control, audit, sessions and lockout are built; a formal ADHICS gap assessment is a pilot task."},
    {"item": "Accession agreement and network", "need": "Accession agreement and facility declaration; SD-WAN connection.",
     "status": "to do", "evidence": "Signed by the pilot provider, not by KareShafaa. No connection or partnership is claimed."},
    {"item": "Integration pattern", "need": "Read results and appointments across providers; detect when a patient was seen elsewhere.",
     "status": "designed", "evidence": "Subscribe to result feeds for enrolled screening patients through the provider's "
                                      "Malaffi connection; match on Emirates ID; never write clinical data back without a clinician."},
]

# CONFIRM: deployment profiles per emirate (regulator and health information exchange). Only Abu Dhabi is configured.
EMIRATE_PROFILES = [
    {"emirates": "Abu Dhabi", "regulator": "Department of Health - Abu Dhabi (DoH)", "hie": "Malaffi", "security_standard": "ADHICS V2",
     "configured": True},
    {"emirates": "Dubai", "regulator": "Dubai Health Authority (DHA)", "hie": "NABIDH", "security_standard": "DHA policies", "configured": False},
    {"emirates": "Sharjah, Ajman, Umm Al Quwain, Ras Al Khaimah, Fujairah", "regulator": "Ministry of Health and Prevention (MOHAP)",
     "hie": "Riayati", "security_standard": "MOHAP policies", "configured": False},
]
