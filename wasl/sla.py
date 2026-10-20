"""SLA timers by urgency tier (configurable) and RAG status."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import get_settings
from .calendar_uae import add_working_hours_utc
from .models import SlaRule
from .programmes import WORKING_DAY_TIERS

# Hours allowed in each state before the step is overdue, per tier (T0 critical, T1 urgent, T2 soon, T3 routine).
# ILLUSTRATIVE prototype defaults aligned with the Wasl design doc - configuration, not clinical guidance.
# T2/T3 clocks count UAE working days (see calendar_uae); T0/T1 clocks run 24/7.
DEFAULT_SLA_HOURS: dict[str, tuple[float, float, float, float]] = {
    "issued": (1, 4, 24, 48),
    "triaged": (4, 24, 48, 120),
    "patient_notified": (24, 48, 120, 240),
    "acknowledged": (24, 72, 168, 336),
    "confirmatory_test": (48, 168, 336, 672),
    "consultation": (24, 72, 168, 336),
    "referral_sent": (24, 48, 120, 240),
    "referral_rejected": (8, 24, 48, 120),
    "referral_accepted": (24, 72, 240, 480),
    "appointment_booked": (168, 336, 720, 1440),
    "appointment_attended": (48, 120, 240, 480),
    "treatment_or_surveillance_started": (48, 72, 168, 336),
    "no_show": (8, 24, 48, 120),
    "unreachable": (24, 72, 168, 336),
    "declined_pending_review": (24, 48, 120, 240),
}
TIER_INDEX = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}


def load_default_hours() -> dict[str, tuple[float, float, float]]:
    s = get_settings()
    hours = dict(DEFAULT_SLA_HOURS)
    if s.sla_config_path and s.sla_config_path.exists():
        override = json.loads(s.sla_config_path.read_text())
        for state, vals in override.items():
            if state in hours and len(vals) == 4:
                hours[state] = tuple(float(v) for v in vals)  # type: ignore[assignment]
    return hours


def seed_sla_rules(db: Session) -> None:
    if db.scalar(select(SlaRule).limit(1)):
        return
    rf = get_settings().reminder_fraction
    for state, vals in load_default_hours().items():
        for tier, idx in TIER_INDEX.items():
            db.add(SlaRule(state=state, tier=tier, hours=vals[idx], reminder_fraction=rf))
    db.flush()


@dataclass
class SlaLookup:
    rules: dict[tuple[str, str], SlaRule]

    @classmethod
    def load(cls, db: Session) -> SlaLookup:
        rows = db.scalars(select(SlaRule).where(SlaRule.active.is_(True))).all()
        return cls({(r.state, r.tier): r for r in rows})

    def hours(self, state: str, tier: str) -> float | None:
        r = self.rules.get((state, tier))
        return r.hours if r else None

    def reminder_fraction(self, state: str, tier: str) -> float:
        r = self.rules.get((state, tier))
        return r.reminder_fraction if r else get_settings().reminder_fraction


def due_at(start: datetime, hours: float | None, tier: str | None = None) -> datetime | None:
    if hours is None:
        return None
    if tier in WORKING_DAY_TIERS:
        return add_working_hours_utc(start, hours)  # calendar applied in UAE local time
    return start + timedelta(hours=hours)


def rag_status(started: datetime, due: datetime | None, now: datetime, reminder_fraction: float = 0.5) -> tuple[str, float]:
    """Return (green|amber|red|none, fraction_elapsed)."""
    if due is None:
        return "none", 0.0
    total = (due - started).total_seconds()
    frac = (now - started).total_seconds() / total if total > 0 else 1.0
    if frac >= 1:
        return "red", frac
    if frac >= reminder_fraction:
        return "amber", frac
    return "green", frac
