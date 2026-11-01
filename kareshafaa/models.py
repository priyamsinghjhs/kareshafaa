"""ORM models. Tables are FHIR-shaped (names/fields follow FHIR R4 resources) but kept relational."""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import (
    text,
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    event,
)
from sqlalchemy.orm import Mapped, Session, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# --------------------------------------------------------------------------- directory
class Organization(Base):  # FHIR Organization
    __tablename__ = "organizations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(40))  # lab | screening_programme | primary_care | hospital
    region: Mapped[str] = mapped_column(String(60), default="Abu Dhabi")
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Practitioner(Base):  # FHIR Practitioner + PractitionerRole
    __tablename__ = "practitioners"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))  # coordinator | gp | specialist | clinical_lead | ...
    specialty: Mapped[str | None] = mapped_column(String(80), nullable=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    programme: Mapped[str | None] = mapped_column(String(40), nullable=True)
    supervisor_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    is_pool: Mapped[bool] = mapped_column(Boolean, default=False)  # team queue (= "unassigned")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    org: Mapped[Organization] = relationship()


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), unique=True)
    display_name: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))
    practitioner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    practitioner: Mapped[Practitioner | None] = relationship()
    # --- real per-user login (kareshafaa/accounts.py). Never serialised by any API (tests/test_auth_accounts.py).
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)  # argon2id; NULL = cannot log in
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    site: Mapped[str | None] = mapped_column(String(120), nullable=True)
    team: Mapped[str | None] = mapped_column(String(120), nullable=True)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True, default=utcnow)
    is_demo_account: Mapped[bool] = mapped_column(Boolean, default=False)
    status_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)  # last deactivate/reactivate reason
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status_changed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    ui_language: Mapped[str | None] = mapped_column(String(8), nullable=True)  # patient page language they chose


class UserSession(Base):
    """Server-side login session. The cookie holds a random token; only its SHA-256 is stored."""
    __tablename__ = "user_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String(200), nullable=True)


class LoginAttempt(Base):
    """One row per password login attempt (for rate limiting and temporary lockout). No passwords stored."""
    __tablename__ = "login_attempts"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(80), index=True)
    ip: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    success: Mapped[bool] = mapped_column(Boolean, default=False)


# --------------------------------------------------------------------------- patient
class Patient(Base):  # FHIR Patient (synthetic only)
    __tablename__ = "patients"
    id: Mapped[int] = mapped_column(primary_key=True)
    mrn: Mapped[str] = mapped_column(String(40), unique=True)
    # Emirates ID (synthetic). Deliberately NOT unique: real registries hold the same person under two MRNs;
    # results for either record are cross-checked (workflow.identity_group) instead of being trusted blindly.
    national_id: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)
    given_name: Mapped[str] = mapped_column(String(100))
    family_name: Mapped[str] = mapped_column(String(100))
    gender: Mapped[str] = mapped_column(String(20))
    birth_year: Mapped[int] = mapped_column(Integer)
    age_band: Mapped[str] = mapped_column(String(20))
    nationality_group: Mapped[str] = mapped_column(String(40))
    language: Mapped[str] = mapped_column(String(8))  # preferred: en | ar | hi | ur | (other -> en)
    region: Mapped[str] = mapped_column(String(60))
    phone_valid: Mapped[bool] = mapped_column(Boolean, default=True)
    has_smartphone: Mapped[bool] = mapped_column(Boolean, default=True)
    whatsapp: Mapped[bool] = mapped_column(Boolean, default=True)
    alt_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    prior_no_shows: Mapped[int] = mapped_column(Integer, default=0)
    prior_screens: Mapped[int] = mapped_column(Integer, default=0)
    responsiveness: Mapped[float] = mapped_column(Float, default=0.8)  # synthetic latent trait
    managing_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    gp_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    deceased: Mapped[bool] = mapped_column(Boolean, default=False)
    # who recorded the death, when and from what source (was kept only in the audit trail)
    deceased_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    deceased_recorded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deceased_recorded_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    deceased_corrected_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # last "recorded in error" fix
    deceased_corrected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    deceased_corrected_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    pregnant: Mapped[bool] = mapped_column(Boolean, default=False)
    haemoglobinopathy: Mapped[bool] = mapped_column(Boolean, default=False)  # e.g. thalassaemia trait / Hb variant
    specialist_care: Mapped[str | None] = mapped_column(String(200), nullable=True)  # csv of programmes under specialist care
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    # ---- registration (patients registered by staff; all nullable so older records and SYN- samples keep working)
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)  # birth_year / age_band are derived from it
    nationality: Mapped[str | None] = mapped_column(String(80), nullable=True)  # country; nationality_group derived
    phone: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # E.164, e.g. +971501234567
    email: Mapped[str | None] = mapped_column(String(200), nullable=True)
    address_line: Mapped[str | None] = mapped_column(String(300), nullable=True)
    city: Mapped[str | None] = mapped_column(String(100), nullable=True)
    emirate: Mapped[str | None] = mapped_column(String(40), nullable=True)
    preferred_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)  # app | sms | whatsapp | phone
    registration_source: Mapped[str | None] = mapped_column(String(30), nullable=True)  # staff_form | result_import
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    registered_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # why the record was created although a similar record (same date of birth, similar name) exists - audited too
    duplicate_override_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ---- identity document (added 1 Nov 2026): Emirates ID stays in national_id (compatibility, cross-record checks);
    # passport / GCC national ID / other documents for visitors and new arrivals use id_number + id_country
    id_type: Mapped[str | None] = mapped_column(String(20), nullable=True)  # emirates_id | passport | gcc_id | other
    id_number: Mapped[str | None] = mapped_column(String(20), nullable=True, index=True)  # non-Emirates ID documents
    id_country: Mapped[str | None] = mapped_column(String(80), nullable=True)  # issuing country (NATIONALITIES name)
    # ---- insurance (payer) and next of kin / guardian (services/coverage.py); all nullable, additive
    insurance_payer: Mapped[str | None] = mapped_column(String(30), nullable=True)  # coverage.PAYERS key
    insurance_plan: Mapped[str | None] = mapped_column(String(80), nullable=True)
    insurance_member_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    insurance_valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    nok_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nok_relationship: Mapped[str | None] = mapped_column(String(30), nullable=True)  # coverage.RELATIONSHIPS key
    nok_phone: Mapped[str | None] = mapped_column(String(20), nullable=True)
    nok_is_guardian: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    proxy_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # decision itself is a Consent(scope=proxy_contact)
    managing_org: Mapped[Organization] = relationship(foreign_keys=[managing_org_id])


class Consent(Base):  # FHIR Consent
    __tablename__ = "consents"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    scope: Mapped[str] = mapped_column(String(40))
    decision: Mapped[str] = mapped_column(String(10))  # permit | deny
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | inactive
    recorded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------- results
class DiagnosticReport(Base):  # FHIR DiagnosticReport
    __tablename__ = "diagnostic_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id", use_alter=True), nullable=True, index=True)
    programme: Mapped[str] = mapped_column(String(40))
    category: Mapped[str] = mapped_column(String(20), default="screening")  # screening | confirmatory
    code: Mapped[str] = mapped_column(String(40))
    display: Mapped[str] = mapped_column(String(200))
    conclusion: Mapped[str] = mapped_column(String(40))  # actionable | positive | negative
    issued_at: Mapped[datetime] = mapped_column(DateTime)
    performer_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    status: Mapped[str] = mapped_column(String(20), default="final")
    # who ordered the test, where the person was screened and when the specimen was taken (HL7 ORC/OBR, FHIR
    # ServiceRequest/Specimen, or typed in); nullable, additive - older results show "Not recorded"
    ordering_clinician: Mapped[str | None] = mapped_column(String(200), nullable=True)
    screening_site: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specimen_collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC
    observations: Mapped[list[Observation]] = relationship(back_populates="report")


class Observation(Base):  # FHIR Observation
    __tablename__ = "observations"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_reports.id"), index=True)
    code: Mapped[str] = mapped_column(String(40))
    display: Mapped[str] = mapped_column(String(200))
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(100), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    interpretation: Mapped[str] = mapped_column(String(20))  # H | A | N | POS
    report: Mapped[DiagnosticReport] = relationship(back_populates="observations")


# --------------------------------------------------------------------------- case (FHIR Task)
class Case(Base):  # FHIR Task (code=loop-case); focus = DiagnosticReport
    __tablename__ = "cases"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("diagnostic_reports.id"))
    programme: Mapped[str] = mapped_column(String(40), index=True)
    tier: Mapped[str] = mapped_column(String(20))  # urgent | high | routine
    state: Mapped[str] = mapped_column(String(40), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"), nullable=False, index=True)
    managing_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    needs_confirmation: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    state_entered_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    escalation_level: Mapped[int] = mapped_column(Integer, default=0)       # step-clock alerts (reset per step)
    target_alert_level: Mapped[int] = mapped_column(Integer, default=0)     # pathway-target alerts (once per case)
    care_reached_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    closure_type: Mapped[str | None] = mapped_column(String(30), nullable=True)  # evidence | authorised_decision
    closure_reason: Mapped[str | None] = mapped_column(String(60), nullable=True)
    closure_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    closure_evidence_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    closure_evidence_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_screening_due: Mapped[date | None] = mapped_column(Date, nullable=True)  # set at closure (recall)
    closed_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    transfer_pending_owner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    transfer_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ML outreach-priority score: used ONLY for queue ordering, never for state changes/closure.
    # clinical rules engine output (kareshafaa/clinical_rules.py) and the fail-safe human-review flag
    rule_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    pathway: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_text: Mapped[str | None] = mapped_column(String(200), nullable=True)
    target_due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # end-to-end pathway target
    review_required: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    review_reasons: Mapped[list | None] = mapped_column(JSON, nullable=True)
    # every clinician review: [{at, by, note, reasons, tier_from, tier_to}] (the note used to live only in the audit log)
    review_log: Mapped[list | None] = mapped_column(JSON, nullable=True)
    risk_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    risk_model_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    risk_rationale: Mapped[list | None] = mapped_column(JSON, nullable=True)  # top factors behind the outreach priority
    synthetic: Mapped[bool] = mapped_column(Boolean, default=True)
    row_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)  # optimistic concurrency
    __mapper_args__ = {"version_id_col": row_version}
    __table_args__ = (
        # DB-level guarantee: never two open cases for one patient and programme, even for concurrent results
        Index("uq_one_open_case", "patient_id", "programme", unique=True,
              sqlite_where=text("state != 'closed'"), postgresql_where=text("state != 'closed'")),
    )
    patient: Mapped[Patient] = relationship()
    owner: Mapped[Practitioner] = relationship(foreign_keys=[owner_id])
    report: Mapped[DiagnosticReport] = relationship(foreign_keys=[report_id])
    steps: Mapped[list[StepTask]] = relationship(back_populates="case", order_by="StepTask.id")


class StepTask(Base):  # FHIR Task (partOf = case), one per lifecycle step, carries step ownership
    __tablename__ = "step_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    state: Mapped[str] = mapped_column(String(40))
    owner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="in-progress")  # in-progress | completed
    started_at: Mapped[datetime] = mapped_column(DateTime)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sla_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    breached: Mapped[bool] = mapped_column(Boolean, default=False)
    inherited_breach: Mapped[bool] = mapped_column(Boolean, default=False)  # opened after the pathway target was missed
    actor_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    case: Mapped[Case] = relationship(back_populates="steps")
    owner: Mapped[Practitioner] = relationship()
    __table_args__ = (
        # DB-level guarantee: at most one in-progress step (= one accountable owner) per case.
        Index("uq_one_active_step", "case_id", unique=True,
              sqlite_where=(status == "in-progress"), postgresql_where=(status == "in-progress")),
    )


class OwnershipEvent(Base):  # reassignment / two-key transfer history (FHIR Provenance-like)
    __tablename__ = "ownership_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    from_owner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    to_owner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    kind: Mapped[str] = mapped_column(String(30))  # assign | reassign | transfer_requested | transfer_accepted | transfer_declined | auto_pool
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)


class ServiceRequest(Base):  # FHIR ServiceRequest (referral or confirmatory test) + its Task status
    __tablename__ = "service_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(30))  # referral | confirmatory_test
    code: Mapped[str] = mapped_column(String(80))
    requester_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    performer_org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    performer_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    priority: Mapped[str] = mapped_column(String(20), default="routine")
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested|accepted|rejected|completed|revoked
    clinical_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(String(20), nullable=True)  # confirmatory: positive|negative
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    performer_org: Mapped[Organization] = relationship()


class Appointment(Base):  # FHIR Appointment
    __tablename__ = "appointments"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    service_request_id: Mapped[int | None] = mapped_column(ForeignKey("service_requests.id"), nullable=True)
    org_id: Mapped[int] = mapped_column(ForeignKey("organizations.id"))
    practitioner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    start: Mapped[datetime] = mapped_column(DateTime)
    status: Mapped[str] = mapped_column(String(20), default="booked")  # booked|fulfilled|noshow|cancelled
    barrier_code: Mapped[str | None] = mapped_column(String(40), nullable=True)
    booking_note: Mapped[str | None] = mapped_column(Text, nullable=True)  # e.g. why it is after the pathway target
    # lifecycle (added 29 Oct 2026): cancel / reschedule with a reason, reminder before the appointment
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    cancelled_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    cancel_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    rescheduled_from_id: Mapped[int | None] = mapped_column(ForeignKey("appointments.id"), nullable=True)
    reminder_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class CarePlan(Base):  # FHIR CarePlan - evidence that treatment or surveillance started
    __tablename__ = "care_plans"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # treatment | surveillance
    description: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), default="active")
    recorded_by_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    review_due_on: Mapped[date | None] = mapped_column(Date, nullable=True)  # surveillance: next review / re-test


class Recall(Base):
    """Next routine screen (or surveillance review) for one person and programme. Created after a negative
    result, a closed case or a surveillance plan; completed automatically by the next screening result."""
    __tablename__ = "recalls"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    programme: Mapped[str] = mapped_column(String(40), index=True)
    due_on: Mapped[date] = mapped_column(Date, index=True)
    reason: Mapped[str] = mapped_column(String(30))  # routine_after_negative | after_closure | surveillance
    interval_months: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_report_id: Mapped[int | None] = mapped_column(ForeignKey("diagnostic_reports.id"), nullable=True)
    source_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    care_plan_id: Mapped[int | None] = mapped_column(ForeignKey("care_plans.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="due", index=True)  # due | invited | completed | cancelled | superseded
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(200))
    invited_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    invite_count: Mapped[int] = mapped_column(Integer, default=0)
    invited_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    last_invite_channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    completed_report_id: Mapped[int | None] = mapped_column(ForeignKey("diagnostic_reports.id"), nullable=True)
    completed_case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    closed_note: Mapped[str | None] = mapped_column(String(500), nullable=True)  # cancel reason
    closed_by: Mapped[str | None] = mapped_column(String(200), nullable=True)


class GpSummary(Base):  # FHIR Composition (discharge-style summary) sent to the registered GP; internal, not SIMULATED
    """Written automatically when care starts and when a case closes; the GP acknowledges it (with an optional note)."""
    __tablename__ = "gp_summaries"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # care_started | case_closed
    to_practitioner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"), index=True)
    to_org_id: Mapped[int | None] = mapped_column(ForeignKey("organizations.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(200))
    content: Mapped[dict] = mapped_column(JSON, default=dict)  # structured sections (result, pathway, outcome, next steps)
    text: Mapped[str] = mapped_column(Text)                    # the same, as plain text (print / export)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    created_by: Mapped[str] = mapped_column(String(200))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    ack_note: Mapped[str | None] = mapped_column(String(1000), nullable=True)


class Communication(Base):  # FHIR Communication (all channels SIMULATED)
    __tablename__ = "communications"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    patient_id: Mapped[int | None] = mapped_column(ForeignKey("patients.id"), nullable=True)
    recipient_practitioner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    direction: Mapped[str] = mapped_column(String(10), default="outbound")
    category: Mapped[str] = mapped_column(String(30))
    channel: Mapped[str] = mapped_column(String(20))  # app | sms | whatsapp | phone | internal
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    template_key: Mapped[str | None] = mapped_column(String(80), nullable=True)
    template_variant: Mapped[str | None] = mapped_column(String(20), nullable=True)
    content: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))  # completed | failed | pending
    simulated: Mapped[bool] = mapped_column(Boolean, default=True)
    attempt_no: Mapped[int] = mapped_column(Integer, default=1)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class CallTask(Base):  # FHIR Task (code=phone-outreach) created when digital channels fail
    __tablename__ = "call_tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    owner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | completed
    outcome: Mapped[str | None] = mapped_column(String(30), nullable=True)  # reached | no_answer | wrong_number
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QuestionnaireResponse(Base):  # FHIR QuestionnaireResponse - teach-back check
    __tablename__ = "questionnaire_responses"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    language: Mapped[str] = mapped_column(String(8))
    answers: Mapped[dict] = mapped_column(JSON)
    score: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    passed: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 1-5 self-reported
    mode: Mapped[str] = mapped_column(String(20), default="self")  # self | navigator
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AnswerReopen(Base):  # patient asked to change answers they already sent (one new submission, time-limited)
    __tablename__ = "answer_reopens"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the patient's login that asked
    form: Mapped[str] = mapped_column(String(30), default="teach_back")
    previous_response_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # answers kept in history
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    used_response_id: Mapped[int | None] = mapped_column(Integer, nullable=True)  # the new submission
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# --------------------------------------------------------------------------- clinical record (doctor view)
# source: "manual" (entered by a clinician in the app) | "fhir" (imported FHIR R4 resource) |
# "sample_backfill" (synthetic history for SYN- sample patients only, see services/sample_data.py)
class Encounter(Base):  # FHIR Encounter - consultation / visit / call with clinical notes (staff-only)
    __tablename__ = "encounters"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    appointment_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kind: Mapped[str] = mapped_column(String(30))  # gp_visit | specialist_visit | colonoscopy | colposcopy | ...
    start: Mapped[datetime] = mapped_column(DateTime)
    practitioner_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    clinician_display: Mapped[str | None] = mapped_column(String(200), nullable=True)  # imported, not in directory
    org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # clinician note: never shown to the patient
    outcome: Mapped[str | None] = mapped_column(String(300), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="finished")
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class MedicationRequest(Base):  # FHIR MedicationRequest / MedicationStatement
    __tablename__ = "medication_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    dose: Mapped[str | None] = mapped_column(String(80), nullable=True)
    frequency: Mapped[str | None] = mapped_column(String(80), nullable=True)
    route: Mapped[str | None] = mapped_column(String(40), nullable=True)
    start_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    end_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | stopped | completed
    prescriber_id: Mapped[int | None] = mapped_column(ForeignKey("practitioners.id"), nullable=True)
    prescriber_display: Mapped[str | None] = mapped_column(String(200), nullable=True)
    reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    recorded_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class AllergyIntolerance(Base):  # FHIR AllergyIntolerance (substance "No known drug allergies" = NKDA recorded)
    __tablename__ = "allergy_intolerances"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    substance: Mapped[str] = mapped_column(String(200))
    reaction: Mapped[str | None] = mapped_column(String(200), nullable=True)
    severity: Mapped[str | None] = mapped_column(String(20), nullable=True)  # mild | moderate | severe
    status: Mapped[str] = mapped_column(String(20), default="active")
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Condition(Base):  # FHIR Condition - key conditions / problem list
    __tablename__ = "conditions"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    display: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # ICD-10
    clinical_status: Mapped[str] = mapped_column(String(20), default="active")  # active | resolved
    onset: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)


class Investigation(Base):  # FHIR Observation outside the screening programme (e.g. lipids, eGFR, histology)
    __tablename__ = "investigations"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    name: Mapped[str] = mapped_column(String(200))
    code: Mapped[str | None] = mapped_column(String(40), nullable=True)  # LOINC
    value: Mapped[str | None] = mapped_column(String(200), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    reference_range: Mapped[str | None] = mapped_column(String(60), nullable=True)
    flag: Mapped[str] = mapped_column(String(10), default="N")  # N normal | H high | L low | A abnormal
    effective_at: Mapped[datetime] = mapped_column(DateTime)
    performer: Mapped[str | None] = mapped_column(String(200), nullable=True)  # ordering/reporting source
    source: Mapped[str] = mapped_column(String(20), default="manual")
    external_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class DataMigration(Base):  # one-time data steps already applied to this database (guard: run once)
    __tablename__ = "data_migrations"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class ConsultRequest(Base):  # FHIR CommunicationRequest/ServiceRequest(e-consult)
    __tablename__ = "consult_requests"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    requester_type: Mapped[str] = mapped_column(String(20))  # patient | practitioner
    requester_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    target_practitioner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    question: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(20), default="routine")
    status: Mapped[str] = mapped_column(String(20), default="requested")  # requested | answered
    response: Mapped[str | None] = mapped_column(Text, nullable=True)
    responded_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    due_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    responded_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Escalation(Base):
    __tablename__ = "escalations"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("cases.id"), index=True)
    step_task_id: Mapped[int] = mapped_column(ForeignKey("step_tasks.id"))
    level: Mapped[int] = mapped_column(Integer)  # 1 reminder | 2 supervisor | 3 clinical lead | 4 director
    kind: Mapped[str] = mapped_column(String(10), default="step")  # step | pathway (consolidated target breach)
    target_practitioner_id: Mapped[int] = mapped_column(ForeignKey("practitioners.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    acknowledged_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# --------------------------------------------------------------------------- governance
class AuditEvent(Base):  # FHIR AuditEvent - append-only, hash-chained
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    actor_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    actor_role: Mapped[str] = mapped_column(String(40))
    actor_display: Mapped[str] = mapped_column(String(200))
    action: Mapped[str] = mapped_column(String(2))  # C R U D E (FHIR AuditEvent.action)
    event: Mapped[str] = mapped_column(String(80))
    entity_type: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    entity_id: Mapped[str | None] = mapped_column(String(40), nullable=True, index=True)
    case_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    outcome: Mapped[str] = mapped_column(String(10), default="success")
    path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))


@event.listens_for(Session, "before_flush")
def _audit_append_only(session: Session, _ctx, _instances) -> None:
    for obj in list(session.dirty) + list(session.deleted):
        if isinstance(obj, AuditEvent):
            raise PermissionError("AuditEvent rows are append-only")


class ContentApproval(Base):  # human sign-off on one exact version (sha256) of patient-facing content
    __tablename__ = "content_approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    content_id: Mapped[str] = mapped_column(String(120), index=True)  # e.g. pathway:repeat_fit:ar
    sha256: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(20))  # clinical | translation
    user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    display: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text)
    readability: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    approved_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class Lesson(Base):
    __tablename__ = "lessons"
    id: Mapped[int] = mapped_column(primary_key=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True, index=True)
    kind: Mapped[str] = mapped_column(String(30))  # overdue | drop_off | incident | no_show | rejected_referral | unreachable | declined | teach_back_failed
    reason_code: Mapped[str] = mapped_column(String(60))
    state: Mapped[str | None] = mapped_column(String(40), nullable=True)
    programme: Mapped[str | None] = mapped_column(String(40), nullable=True)
    tier: Mapped[str | None] = mapped_column(String(20), nullable=True)
    language: Mapped[str | None] = mapped_column(String(8), nullable=True)
    region: Mapped[str | None] = mapped_column(String(60), nullable=True)
    channel: Mapped[str | None] = mapped_column(String(20), nullable=True)
    target_org_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class RuleProposal(Base):
    __tablename__ = "rule_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(40))  # sla_hours | reminder_fraction | template_variant | channel_order | referral_checklist
    target: Mapped[dict] = mapped_column(JSON)
    current_value: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    proposed_value: Mapped[dict] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text)
    evidence: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    fingerprint: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    decided_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)


class SlaRule(Base):
    __tablename__ = "sla_rules"
    id: Mapped[int] = mapped_column(primary_key=True)
    state: Mapped[str] = mapped_column(String(40))
    tier: Mapped[str] = mapped_column(String(20))
    hours: Mapped[float] = mapped_column(Float)
    reminder_fraction: Mapped[float] = mapped_column(Float, default=0.5)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(60), default="default")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ConfigEntry(Base):  # versioned key/value config changed only via approved proposals or admin
    __tablename__ = "config_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(120), index=True)
    value: Mapped[dict | list | str | float | None] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    source: Mapped[str] = mapped_column(String(60), default="default")
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, unique=True)
    algorithm: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))  # failed_gates | awaiting_approval | production | retired | rejected | rolled_back
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    trained_on_n: Mapped[int] = mapped_column(Integer)
    validated_on_n: Mapped[int] = mapped_column(Integer)
    features: Mapped[list] = mapped_column(JSON)
    metrics: Mapped[dict] = mapped_column(JSON)
    fairness: Mapped[dict] = mapped_column(JSON)
    gates: Mapped[dict] = mapped_column(JSON)
    reference_stats: Mapped[dict] = mapped_column(JSON)
    artifact_path: Mapped[str] = mapped_column(String(300))
    artifact_sha256: Mapped[str] = mapped_column(String(64))
    compared_to_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_by_user_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decision_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_card: Mapped[str] = mapped_column(Text, default="")


class ModelArtifact(Base):
    """Copy of each model file kept in the database, keyed by its SHA-256, so a server with a temporary disk (or a
    fresh container after a redeploy) can restore the exact, integrity-checked file. Never edited once written."""
    __tablename__ = "model_artifacts"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    version: Mapped[int] = mapped_column(Integer, index=True)
    filename: Mapped[str] = mapped_column(String(200))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class SimulationRun(Base):
    __tablename__ = "simulation_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    params: Mapped[dict] = mapped_column(JSON)
    results: Mapped[dict] = mapped_column(JSON)
    label: Mapped[str] = mapped_column(String(40), default="SIMULATED")


class PatientInvite(Base):
    """One-time activation code for a patient account (created by staff from the patient record).
    Only hashes are stored; the code is shown once to staff and sent in a SIMULATED SMS. Single use, expires."""
    __tablename__ = "patient_invites"
    id: Mapped[int] = mapped_column(primary_key=True)
    patient_id: Mapped[int] = mapped_column(ForeignKey("patients.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(64), index=True)
    link_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    code_last4: Mapped[str] = mapped_column(String(4))
    purpose: Mapped[str] = mapped_column(String(20), default="activate")  # activate | reset (account already active)
    channel: Mapped[str] = mapped_column(String(20), default="sms")  # sms | in_person
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    created_by: Mapped[str] = mapped_column(String(200))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    revoked_by: Mapped[str | None] = mapped_column(String(200), nullable=True)
    revoke_reason: Mapped[str | None] = mapped_column(String(300), nullable=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    communication_id: Mapped[int | None] = mapped_column(ForeignKey("communications.id"), nullable=True)


class IngestMessage(Base):  # inbound HL7 v2 / FHIR message log (idempotency + human patient-match review)
    __tablename__ = "ingest_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    source: Mapped[str] = mapped_column(String(20))  # hl7v2 | fhir
    message_id: Mapped[str] = mapped_column(String(120), unique=True)  # MSH-10 / Bundle.identifier
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    status: Mapped[str] = mapped_column(String(30))  # processed | needs_match_review | rejected | resolved
    patient_identifier: Mapped[str | None] = mapped_column(String(60), nullable=True)
    birth_year: Mapped[int | None] = mapped_column(Integer, nullable=True)
    programme: Mapped[str | None] = mapped_column(String(40), nullable=True)
    value: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(60), nullable=True)
    unit: Mapped[str | None] = mapped_column(String(30), nullable=True)
    test: Mapped[str | None] = mapped_column(String(20), nullable=True)
    result_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    cytology: Mapped[str | None] = mapped_column(String(30), nullable=True)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("diagnostic_reports.id"), nullable=True)
    case_id: Mapped[int | None] = mapped_column(ForeignKey("cases.id"), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    resolved_by_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    payload_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)  # detects a reused control ID
    flags: Mapped[list | None] = mapped_column(JSON, nullable=True)  # message-level review reasons
    other_observations: Mapped[list | None] = mapped_column(JSON, nullable=True)  # further values, same test
    alt_identifiers: Mapped[list | None] = mapped_column(JSON, nullable=True)  # PID-3 repetitions
    patient_demographics: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # name/DOB/sex/phone in the message
    ordering_clinician: Mapped[str | None] = mapped_column(String(200), nullable=True)  # kept for match review
    screening_site: Mapped[str | None] = mapped_column(String(200), nullable=True)
    specimen_collected_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


# ---- performance indexes (iteration 4: profiled at 10k+ cases) ------------------------------------------
Index("ix_steps_status_case", StepTask.status, StepTask.case_id)          # queue/sweep: in-progress steps
Index("ix_steps_case_state", StepTask.case_id, StepTask.state)            # KPIs: first entry per state
Index("ix_cases_state_programme", Case.state, Case.programme)             # open-case scans
Index("ix_calltasks_status_case", CallTask.status, CallTask.case_id)
Index("ix_escalations_open", Escalation.acknowledged_at, Escalation.case_id)
Index("ix_consults_status_case", ConsultRequest.status, ConsultRequest.case_id)
Index("ix_consults_target_status", ConsultRequest.target_practitioner_id, ConsultRequest.status)
Index("ix_comms_case_cat_status", Communication.case_id, Communication.category, Communication.status)
Index("ix_consents_patient_scope", Consent.patient_id, Consent.scope, Consent.status)
Index("ix_audit_actor_role", AuditEvent.actor_role)
Index("ix_audit_event", AuditEvent.event)


# --------------------------------------------------------------------------- safety interlock: no silent downgrade
import contextvars  # noqa: E402

from sqlalchemy import event as _event  # noqa: E402

TIER_CHANGE_AUTHORISED: contextvars.ContextVar[bool] = contextvars.ContextVar("tier_change_authorised", default=False)


class TierDowngradeError(ValueError):
    """A case's clinical urgency tier may only be lowered by an authorised clinician with a reason."""


@_event.listens_for(Case.tier, "set")
def _no_silent_tier_downgrade(target, value, oldvalue, initiator):  # pragma: no branch - tiny guard
    from .clinical_rules import is_downgrade

    if isinstance(oldvalue, str) and isinstance(value, str) and is_downgrade(oldvalue, value) \
            and not TIER_CHANGE_AUTHORISED.get():
        raise TierDowngradeError(f"case {target.id}: tier {oldvalue} -> {value} is a downgrade and needs an "
                                 "authorised clinician decision (workflow.resolve_review)")
    return value
