"""Request models (input validation)."""
from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

Note = Field(default=None, max_length=2000)


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class LoginIn(_In):
    username: str = Field(min_length=1, max_length=80)


class TransitionIn(_In):
    to_state: str = Field(max_length=40)
    owner_id: int | None = None
    note: str | None = Note


class CloseIn(_In):
    closure_type: Literal["evidence", "authorised_decision"]
    evidence_type: Literal["appointment_fulfilled", "care_plan", "negative_confirmatory_result"] | None = None
    evidence_id: int | None = None
    reason: str | None = Field(default=None, max_length=60)
    note: str | None = Note


class AssignIn(_In):
    owner_id: int
    reason: str = Field(min_length=3, max_length=500)


class TransferIn(_In):
    to_owner_id: int
    note: str = Field(min_length=5, max_length=1000)


class DecisionIn(_In):
    accept: bool
    note: str | None = Note


class ReferralIn(_In):
    performer_org_id: int
    performer_id: int | None = None
    summary: str = Field(default="", max_length=4000)


class ReferralDecisionIn(_In):
    accept: bool
    reason_code: str | None = Field(default=None, max_length=40)
    note: str | None = Note
    performer_id: int | None = None


class ConfirmOrderIn(_In):
    performer_org_id: int


class ConfirmResultIn(_In):
    result: Literal["positive", "negative"]


class AppointmentIn(_In):
    start: datetime
    org_id: int | None = None
    practitioner_id: int | None = None


class AttendanceIn(_In):
    attended: bool
    barrier_code: str | None = Field(default=None, max_length=40)


class CarePlanIn(_In):
    kind: Literal["treatment", "surveillance"]
    description: str = Field(min_length=5, max_length=2000)


class ConsultIn(_In):
    question: str = Field(min_length=5, max_length=4000)
    target_practitioner_id: int | None = None
    urgency: Literal["critical", "urgent", "high", "routine"] | None = None


class ConsultResponseIn(_In):
    response: str = Field(min_length=2, max_length=4000)


class NoteIn(_In):
    note: str = Field(min_length=3, max_length=2000)


class ConsentIn(_In):
    scope: str = Field(max_length=40)
    decision: Literal["permit", "deny"]
    note: str | None = Note


class CallIn(_In):
    outcome: Literal["reached", "no_answer", "wrong_number"]
    note: str | None = Note


class NotifyIn(_In):
    category: Literal["result_notification", "reminder", "reassurance", "appointment_confirmation", "no_show"]


class TeachBackIn(_In):
    answers: dict[str, Literal["a", "b", "c"]]
    confidence: int | None = Field(default=None, ge=1, le=5)
    language: Literal["en", "ar", "hi", "ur"] = "en"


class LessonIn(_In):
    kind: Literal["overdue", "drop_off", "incident", "no_show", "rejected_referral", "unreachable", "declined"]
    reason_code: str = Field(min_length=2, max_length=60)
    detail: str | None = Note


class ResultIn(_In):
    patient_id: int
    programme: Literal["colorectal_fit", "cervical", "breast", "diabetes_hba1c"]
    value: float | None = Field(default=None, ge=0, le=100000)
    value_text: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, max_length=30)  # required for numeric results, otherwise -> human review
    test: Literal["FIT", "HPV", "CYTOLOGY", "MAMMO", "HBA1C", "FPG"] | None = None
    status: Literal["final", "preliminary", "amended", "corrected", "entered-in-error", "cancelled"] = "final"
    cytology: str | None = Field(default=None, max_length=30)


class ReviewIn(_In):
    note: str = Field(min_length=10, max_length=1000)
    tier: Literal["critical", "urgent", "high", "routine"] | None = None


class TrainIn(_In):
    algorithm: Literal["logreg", "gbm"] = "gbm"


class ApprovalIn(_In):
    note: str = Field(min_length=5, max_length=2000)


class ProposalDecisionIn(_In):
    approve: bool
    note: str = Field(min_length=5, max_length=2000)


class LanguageIn(_In):
    language: Literal["en", "ar", "hi", "ur"]


class SweepIn(_In):
    case_ids: list[int] | None = None
    # DEMO ONLY: evaluate SLAs as if the clock were N hours ahead (single case, demo mode, audited)
    hours_ahead: float = Field(default=0, ge=0, le=720)
