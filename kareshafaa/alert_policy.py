"""Alert (escalation) policy - pure functions shared by the live SLA sweep (services/escalation.py) and the offline
precision/recall evaluation (scripts/alert_eval.py), so the evaluation measures exactly the code that runs.

Two clocks:
* STEP clock - the step's own SLA (already capped at the pathway target while the target is in the future).
  Used while the pathway target has not been missed. L1 reminder at the reminder fraction, then the L2-L4 ladder.
  Deduplicated per step (case.escalation_level is reset when a step opens).
* PATHWAY clock - once the end-to-end pathway target (Case.target_due_at) has passed without care, the breach is a
  single upstream breach of the case. The ladder then runs on the pathway fraction
  (now - case created) / (target - case created) and alerts are deduplicated per CASE (case.target_alert_level):
  one consolidated alert per case per level, never repeated when a new step opens. Steps that open already
  breached do not get an L1 reminder (they are past it) but do inherit the ladder.
The first alert on a true breach is never suppressed: a step-clock breach and the pathway-target breach are
separate events with separate deduplication counters.
* WAITING - a booked appointment (`waiting_until` = end of the appointment's UAE day): nothing is due and nothing
  escalates before then, on either clock (a late booking was already recorded as a missed target when it was
  booked). Once it has passed with no attendance recorded, the STEP clock is breached and runs the L2-L4 ladder
  (no L1: the reminder point is gone), so the case comes back as overdue the next day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class Ladder:
    reminder_fraction: float = 0.5
    clinical_lead_after_breach_fraction: float = 0.5
    director_after_fraction: float = 2.0


def ladder_level(frac: float, tier: str, lad: Ladder, *, allow_l1: bool = True) -> int:
    level = 0
    if frac >= lad.reminder_fraction and allow_l1:
        level = 1
    if frac >= 1:
        level = 3 if tier == "critical" else 2
    if frac >= 1 + lad.clinical_lead_after_breach_fraction:
        level = 3
    if frac >= lad.director_after_fraction and tier in ("critical", "urgent"):
        level = 4
    return level


def _frac(start: datetime, due: datetime, now: datetime) -> float:
    total = (due - start).total_seconds()
    return (now - start).total_seconds() / total if total > 0 else 1.0


@dataclass(frozen=True)
class AlertDecision:
    clock: str            # "step" | "pathway" | "waiting" | "none"
    level: int            # level required now on that clock
    frac: float
    new_levels: tuple     # levels to raise now (after deduplication)
    breached: bool        # the clock in use is past its deadline


def target_missed(target_due: datetime | None, care_reached: datetime | None, now: datetime) -> bool:
    return target_due is not None and now >= target_due and (care_reached is None or care_reached > target_due)


def decide(*, tier: str, step_started: datetime, step_due: datetime | None, case_created: datetime,
           target_due: datetime | None, care_reached: datetime | None, now: datetime,
           step_level_alerted: int, target_level_alerted: int, lad: Ladder = Ladder(),
           waiting_until: datetime | None = None) -> AlertDecision:
    if waiting_until is not None:
        if now < waiting_until:
            return AlertDecision("waiting", 0, 0.0, (), False)
        frac = _frac(step_started, waiting_until, now)
        level = ladder_level(frac, tier, lad, allow_l1=False)
        return AlertDecision("step", level, frac, tuple(lv for lv in range(step_level_alerted + 1, level + 1) if lv >= 2),
                             True)
    if target_missed(target_due, care_reached, now) and care_reached is None:
        frac = _frac(case_created, target_due, now)
        level = ladder_level(frac, tier, lad, allow_l1=False)
        new = tuple(lv for lv in range(target_level_alerted + 1, level + 1) if lv >= 2)
        return AlertDecision("pathway", level, frac, new, True)
    if step_due is None:
        return AlertDecision("none", 0, 0.0, (), False)
    frac = _frac(step_started, step_due, now)
    level = ladder_level(frac, tier, lad)
    return AlertDecision("step", level, frac, tuple(range(step_level_alerted + 1, level + 1)), frac >= 1)


def opened_already_breached(target_due: datetime | None, care_reached: datetime | None, step_started: datetime) -> bool:
    return target_due is not None and care_reached is None and step_started >= target_due
