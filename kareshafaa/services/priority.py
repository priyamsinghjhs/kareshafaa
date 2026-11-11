"""Deterministic work-queue priority and missed-appointment risk bands.

One numeric priority score combines clinical urgency, overdue status, critical flags,
the advisory missed-appointment risk score, and missing contact details. Urgency always
outranks a lower tier: a routine case cannot sort ahead of a high, urgent or critical
one, regardless of risk or days overdue (see tests/test_priority.py).

Protected attributes (language, nationality, gender, age) are never inputs. A higher
score means the case is offered MORE outreach support, never less.

The missed-appointment / loop-drop *risk* (0–1) is the existing governed model in
``kareshafaa/ml`` (logistic or gradient-boosted, trained offline on synthetic history,
fairness-gated, clinical-lead approved). This module only turns that score into a
Low/Medium/High band and uses it as one component of the queue priority. It never
moves, closes, assigns or messages a case.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

# Band thresholds are on the model's predicted probability. Documented calibration note
# is shown next to the chip. High = 50%+ predicted chance of delayed or dropped care.
BAND_HIGH = 0.50
BAND_MEDIUM = 0.25
BAND_LABELS = {"high": "High", "medium": "Medium", "low": "Low"}

CALIBRATION_NOTE = (
    "The band is a predicted chance of delayed or dropped care, from a model trained on "
    "simulated follow-up history and checked for calibration on a held-out slice. "
    "High means a predicted risk of 50% or more. It is an outreach aid only: it never "
    "sets urgency, moves a case, or decides care. Real-world calibration is unknown."
)

# Weights are versioned (priority_v1). Spacing guarantees a lower tier cannot overtake
# a higher one: the maximum of every non-tier component is 600+80+60+40+25 = 805,
# and the gap between adjacent tiers is 3000. Within a tier, red/amber SLA outranks the other
# non-tier components (max 205), so an overdue case stays ahead of an on-time higher-risk one.
PRIORITY_VERSION = "priority_v1"
TIER_POINTS = {"critical": 10000, "urgent": 7000, "high": 4000, "routine": 1000}
SLA_POINTS = {"red": 600, "amber": 300, "green": 0, "none": 0}
READ_BACK_POINTS = 80
RISK_SCALE = 60.0
MISSING_CONTACT_POINTS = 40
REVIEW_POINTS = 25
MAX_NON_TIER = SLA_POINTS["red"] + READ_BACK_POINTS + RISK_SCALE + MISSING_CONTACT_POINTS + REVIEW_POINTS

ORDERING = (
    "Priority score (urgency first, then overdue, critical read-back, missed-appointment "
    "risk, missing contact, review flag), then deadline. A lower urgency tier never sorts "
    "ahead of a higher one. The score orders work only; it never changes a case."
)
DEADLINE_ORDERING = "Deadline first (earliest due first), then case number."

# Short 'why first' phrases (staff UI, English). Arabic is derived in the page when the
# staff UI is in AR; the API also returns a stable key.
WHY = {
    "critical_read_back": "Critical result, read-back still needed",
    "overdue": "Overdue",
    "due_soon": "Due soon",
    "high_risk": "High missed-appointment risk",
    "medium_risk": "Raised missed-appointment risk",
    "missing_contact": "No valid phone or email",
    "review": "Waiting for clinical review",
    "critical": "Critical urgency",
    "urgent": "Urgent",
    "high": "High urgency",
    "routine": "Routine",
}


def risk_band(score: float | None) -> dict | None:
    """Low / Medium / High chip data, or None when the model has not scored the case."""
    if score is None:
        return None
    s = float(score)
    if s >= BAND_HIGH:
        key = "high"
    elif s >= BAND_MEDIUM:
        key = "medium"
    else:
        key = "low"
    return {"key": key, "label": BAND_LABELS[key], "score": round(s, 3)}


def _due(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", ""))
        except ValueError:
            return None
    return None


def days_overdue(due_at: Any, now: datetime | None) -> int:
    """Whole days past the deadline, 0 if not yet due or unknown. Display only (not a sort input)."""
    due, now = _due(due_at), _due(now)
    if due is None or now is None or due >= now:
        return 0
    return max(0, int((now - due).total_seconds() // 86400))


def missing_contact(row: dict) -> bool:
    if "missing_contact" in row and row["missing_contact"] is not None:
        return bool(row["missing_contact"])
    if "phone_valid" not in row and "whatsapp" not in row and "email" not in row:
        return False  # minimal test rows: do not assume contact is missing
    phone = row.get("phone_valid")
    whatsapp = row.get("whatsapp")
    email = (row.get("email") or "").strip()
    return (not phone) and (not whatsapp) and (not email)


def components(row: dict) -> list[tuple[str, float, str]]:
    """Named score parts (key, points, why-text), largest distinctive first after tier."""
    tier = row.get("tier") or "routine"
    sla = row.get("sla_status") or "none"
    risk = row.get("risk_score")
    parts: list[tuple[str, float, str]] = [
        ("tier", float(TIER_POINTS.get(tier, 0)), WHY.get(tier, tier)),
        ("sla", float(SLA_POINTS.get(sla, 0)),
         WHY["overdue"] if sla == "red" else (WHY["due_soon"] if sla == "amber" else "")),
    ]
    if row.get("read_back_needed"):
        parts.append(("read_back", float(READ_BACK_POINTS), WHY["critical_read_back"]))
    else:
        parts.append(("read_back", 0.0, ""))
    risk_pts = (float(risk) * RISK_SCALE) if risk is not None else 0.0
    if risk is not None and float(risk) >= BAND_HIGH:
        risk_why = WHY["high_risk"]
    elif risk is not None and float(risk) >= BAND_MEDIUM:
        risk_why = WHY["medium_risk"]
    else:
        risk_why = ""
    parts.append(("risk", risk_pts, risk_why))
    miss = missing_contact(row)
    parts.append(("missing_contact", float(MISSING_CONTACT_POINTS if miss else 0),
                  WHY["missing_contact"] if miss else ""))
    review = bool(row.get("review_required"))
    parts.append(("review", float(REVIEW_POINTS if review else 0), WHY["review"] if review else ""))
    return parts


def priority_score(row: dict) -> float:
    return round(sum(p for _, p, _ in components(row)), 3)


def why_first(row: dict, now: datetime | None = None) -> str:
    """One short line: the 1–2 most distinctive reasons this case is high in the list.

    Tier is already a column, so it is mentioned only when nothing else stands out.
    Days overdue are shown as a count when the step is past its deadline.
    """
    parts = components(row)
    notable = [(k, pts, txt) for k, pts, txt in parts if k != "tier" and pts > 0 and txt]
    notable.sort(key=lambda t: -t[1])
    overdue = days_overdue(row.get("due_at"), now)
    bits = []
    for k, pts, txt in notable[:2]:
        if k == "sla" and txt == WHY["overdue"] and overdue:
            bits.append(f"Overdue by {overdue} day{'s' if overdue != 1 else ''}")
        else:
            bits.append(txt)
    if not bits:
        bits = [parts[0][2]]  # the tier
    return " · ".join(b for b in bits if b)


def sort_key(row: dict, sort: str = "priority") -> tuple:
    """Default: higher priority first, then earlier deadline, then case id (stable).

    ``sort='deadline'`` is earliest due first (no due date last), then case id.
    Missing fields default so existing tests that pass a minimal row still work.
    """
    due = row.get("due_at") or "9"
    cid = row.get("id") or 0
    if sort == "deadline":
        return (0 if row.get("due_at") else 1, due, cid)
    return (-priority_score(row), 0 if row.get("due_at") else 1, due, cid)


def decorate(row: dict, now: datetime | None = None) -> dict:
    """Add risk_band, why_first and priority_score to a queue-row dict (in place)."""
    row["risk_band"] = risk_band(row.get("risk_score"))
    row["priority_score"] = priority_score(row)
    row["why_first"] = why_first(row, now)
    row["missing_contact"] = missing_contact(row)
    return row


def sql_priority_terms(rag, read_back_needed, missing_expr, review_expr, risk_col):
    """SQLAlchemy expressions that sum to the same priority_score (for ORDER BY).

    ``rag`` is the 0/1/2/3 rank expression already used by the queue (0 = red).
    """
    from sqlalchemy import Numeric, case as sa_case, cast, func, literal

    from ..models import Case

    tier_pts = sa_case(TIER_POINTS, value=Case.tier, else_=0)
    sla_pts = sa_case((rag == 0, SLA_POINTS["red"]), (rag == 1, SLA_POINTS["amber"]), else_=0)
    rb_pts = sa_case((read_back_needed, READ_BACK_POINTS), else_=0)
    risk_pts = func.round(func.coalesce(cast(risk_col, Numeric), 0), 3) * literal(RISK_SCALE)
    miss_pts = sa_case((missing_expr, MISSING_CONTACT_POINTS), else_=0)
    rev_pts = sa_case((review_expr, REVIEW_POINTS), else_=0)
    score = tier_pts + sla_pts + rb_pts + risk_pts + miss_pts + rev_pts
    return func.round(cast(score, Numeric), 3)
