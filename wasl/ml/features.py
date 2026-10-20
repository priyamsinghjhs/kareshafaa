"""Feature and label construction for the outreach-priority (drop-off / delay risk) model.

Protected attributes gender and nationality are EXCLUDED from model inputs and used only to
evaluate fairness. The model output is used only to order outreach work - never for triage,
clinical decisions, state changes or closure.
"""
from __future__ import annotations

from datetime import datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import Case, Patient, utcnow

# age_band is NOT a model input (Iteration 7): with it, both demo candidates ranked 40-49-year-olds last (recall at
# top 30 % 0.05-0.10 vs 0.80-1.0 for 60-75) and failed the fairness gate; without it AUC and ECE improved and all
# gates passed. Age band stays a fairness-monitoring attribute (FAIRNESS_ATTRS).
CAT_FEATURES = ["programme", "tier", "language", "region"]
NUM_FEATURES = ["has_smartphone", "whatsapp", "phone_valid", "alt_contact", "prior_no_shows", "prior_screens",
                "early_failed_contacts", "early_notified", "early_teach_back_failed"]
EARLY_WINDOW_HOURS = 72  # engagement signals observed only in the first 72h after the result (no label leakage)
FEATURES = CAT_FEATURES + NUM_FEATURES
FAIRNESS_ATTRS = ["language", "nationality_group", "gender", "age_band", "region"]
EXCLUDED_FROM_INPUTS = ["gender", "nationality_group"]
TARGET_DAYS = {"critical": 14, "urgent": 30, "high": 60, "routine": 90}
LABEL_DEFINITION = ("1 = delay or drop-off: care (treatment/surveillance started or evidence-based closure) was NOT "
                    "reached within the tier target (urgent 30d, high 60d, routine 90d), including authorised "
                    "closures for decline / unable to contact. 0 = care reached within target.")


def case_frame(db: Session, *, open_only: bool = False) -> pd.DataFrame:
    q = select(Case.id.label("case_id"), Case.programme, Case.tier, Case.state, Case.created_at, Case.closed_at,
               Case.closure_type, Case.care_reached_at, Patient.language, Patient.region, Patient.age_band,
               Patient.gender, Patient.nationality_group, Patient.has_smartphone, Patient.whatsapp,
               Patient.phone_valid, Patient.alt_contact, Patient.prior_no_shows, Patient.prior_screens
               ).join(Patient, Patient.id == Case.patient_id)
    if open_only:
        q = q.where(Case.state != "closed")
    df = pd.DataFrame(db.execute(q).all())
    if df.empty:
        return df
    for c in ("has_smartphone", "whatsapp", "phone_valid", "alt_contact"):
        df[c] = df[c].astype(int)
    return _early_signals(db, df)


def _early_signals(db: Session, df: pd.DataFrame) -> pd.DataFrame:
    """Engagement in the first EARLY_WINDOW_HOURS after case creation (identical for training and scoring)."""
    from ..models import Communication, QuestionnaireResponse, StepTask

    win = pd.Timedelta(hours=EARLY_WINDOW_HOURS)
    created = df.set_index("case_id").created_at
    com = pd.DataFrame(db.execute(select(Communication.case_id, Communication.sent_at, Communication.status)
                                  .where(Communication.patient_id.is_not(None), Communication.status == "failed")).all(),
                       columns=["case_id", "sent_at", "status"])
    qr = pd.DataFrame(db.execute(select(QuestionnaireResponse.case_id, QuestionnaireResponse.created_at,
                                        QuestionnaireResponse.passed)).all(), columns=["case_id", "created_at", "passed"])
    st = pd.DataFrame(db.execute(select(StepTask.case_id, StepTask.started_at)
                                 .where(StepTask.state == "patient_notified")).all(), columns=["case_id", "started_at"])

    def within(frame: pd.DataFrame, tcol: str) -> pd.DataFrame:
        if frame.empty:
            return frame
        frame = frame[frame.case_id.isin(created.index)]
        c0 = frame.case_id.map(created)
        return frame[(frame[tcol] >= c0) & (frame[tcol] <= c0 + win)]

    fc = within(com, "sent_at").groupby("case_id").size() if not com.empty else pd.Series(dtype=int)
    nt = set(within(st, "started_at").case_id) if not st.empty else set()
    q = within(qr, "created_at")
    tbf = set(q[~q.passed.astype(bool)].case_id) if not q.empty else set()
    df["early_failed_contacts"] = df.case_id.map(fc).fillna(0).astype(int)
    df["early_notified"] = df.case_id.isin(nt).astype(int)
    df["early_teach_back_failed"] = df.case_id.isin(tbf).astype(int)
    return df


def add_labels(df: pd.DataFrame, now: datetime | None = None) -> pd.DataFrame:
    now = now or utcnow()
    if df.empty:
        return df.assign(label=pd.Series(dtype=int), known=pd.Series(dtype=bool))
    target = df.tier.map(TARGET_DAYS).astype(float)
    age_days = (now - df.created_at).dt.total_seconds() / 86400
    care_days = (df.care_reached_at - df.created_at).dt.total_seconds() / 86400
    timely = care_days.notna() & (care_days <= target)
    known = (df.state == "closed") | (age_days >= target) | timely
    df = df.assign(label=(~timely).astype(int), known=known)
    return df


def training_frame(db: Session, now: datetime | None = None) -> pd.DataFrame:
    df = add_labels(case_frame(db), now)
    return df[df.known].sort_values("created_at").reset_index(drop=True) if not df.empty else df
