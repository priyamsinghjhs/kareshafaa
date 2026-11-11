"""Best time of day and channel to contact a patient, learned from outreach history.

Uses this patient's answered vs unanswered attempts (by channel and UAE hour band) when
there is enough history, and falls back to a cohort prior otherwise. Protected attributes
are not used to rank or withhold contact. The suggestion is advisory: staff choose when
and how to reach the patient, and nothing is sent on its own.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..calendar_uae import to_local
from ..models import CallTask, Communication, Patient, utcnow

# UAE local hour bands (Asia/Dubai). 'other' is overnight / early morning.
BANDS = ("morning", "afternoon", "evening", "other")
BAND_HOURS = {"morning": (8, 12), "afternoon": (12, 17), "evening": (17, 21)}
BAND_LABEL = {
    "morning": "Morning (08:00–12:00 UAE)",
    "afternoon": "Afternoon (12:00–17:00 UAE)",
    "evening": "Evening (17:00–21:00 UAE)",
    "other": "Outside clinic hours",
}
CHANNELS = ("phone", "whatsapp", "sms", "app", "email")
CHANNEL_LABEL = {
    "phone": "Phone call", "whatsapp": "WhatsApp", "sms": "SMS", "app": "Patient app", "email": "Email",
}
# At least this many attempts on the patient before their own rates outweigh the cohort.
PATIENT_MIN = 3
# Laplace smoothing: (success + ALPHA) / (n + 2*ALPHA)
ALPHA = 1.0
# Blend toward the cohort so a couple of flukes do not dominate.
PATIENT_WEIGHT = 0.7


def hour_band(utc_naive: datetime) -> str:
    hour = to_local(utc_naive).hour
    for name, (lo, hi) in BAND_HOURS.items():
        if lo <= hour < hi:
            return name
    return "other"


def _ok_call(outcome: str | None) -> bool | None:
    if outcome == "reached":
        return True
    if outcome in ("no_answer", "wrong_number"):
        return False
    return None


def _ok_comm(status: str | None) -> bool | None:
    if status == "completed":
        return True
    if status == "failed":
        return False
    return None


def _attempts(db: Session, *, patient_id: int | None = None) -> list[tuple[str, str, bool]]:
    """(channel, hour_band, success) rows. Phone outcomes come from call tasks; digital from communications."""
    out: list[tuple[str, str, bool]] = []
    from ..models import Case
    cq = select(CallTask.outcome, CallTask.completed_at, CallTask.created_at).where(CallTask.status == "completed")
    if patient_id is not None:
        cq = cq.join(Case, Case.id == CallTask.case_id).where(Case.patient_id == patient_id)
    for outcome, done, created in db.execute(cq).all():
        ok = _ok_call(outcome)
        when = done or created
        if ok is None or when is None:
            continue
        out.append(("phone", hour_band(when), ok))
    mq = select(Communication.channel, Communication.status, Communication.sent_at).where(
        Communication.patient_id.is_not(None),
        Communication.direction == "outbound",
        Communication.channel.in_(CHANNELS),
        Communication.channel != "internal",
    )
    if patient_id is not None:
        mq = mq.where(Communication.patient_id == patient_id)
    for channel, status, sent in db.execute(mq).all():
        ok = _ok_comm(status)
        if ok is None or sent is None or channel not in CHANNELS:
            continue
        # phone communications are skipped when we already count CallTask rows for that patient;
        # at cohort level both are useful (call-task outcomes are higher quality and kept).
        if channel == "phone" and patient_id is not None:
            continue
        if channel == "phone" and patient_id is None:
            continue  # cohort phone rates come from call tasks only
        out.append((channel, hour_band(sent), bool(ok)))
    return out


def _rates(rows: list[tuple[str, str, bool]]) -> dict[tuple[str, str], tuple[float, int, int]]:
    """(channel, band) -> (smoothed success rate, successes, n)."""
    tallies: dict[tuple[str, str], list[int]] = defaultdict(lambda: [0, 0])
    for ch, band, ok in rows:
        tallies[(ch, band)][1] += 1
        tallies[(ch, band)][0] += int(ok)
    out = {}
    for key, (s, n) in tallies.items():
        out[key] = ((s + ALPHA) / (n + 2 * ALPHA), s, n)
    return out


def _blend(patient: dict, cohort: dict, n_patient: int) -> dict[tuple[str, str], tuple[float, int, int]]:
    keys = set(patient) | set(cohort)
    w = PATIENT_WEIGHT if n_patient >= PATIENT_MIN else (0.3 if n_patient else 0.0)
    out = {}
    for k in keys:
        pr, ps, pn = patient.get(k, (0.5, 0, 0))
        cr, cs, cn = cohort.get(k, (0.5, 0, 0))
        # if only the cohort has this cell, use it
        if pn == 0:
            out[k] = (cr, cs, cn)
        elif cn == 0:
            out[k] = (pr, ps, pn)
        else:
            out[k] = (w * pr + (1 - w) * cr, ps, pn)
    return out


def _available(p: Patient) -> list[str]:
    """Channels this patient can actually be reached on, preferred first."""
    have = []
    if p.whatsapp:
        have.append("whatsapp")
    if p.phone_valid:
        have.append("phone")
        have.append("sms")
    if p.has_smartphone:
        have.append("app")
    if (p.email or "").strip():
        have.append("email")
    pref = p.preferred_channel if p.preferred_channel in have else None
    if pref:
        have = [pref] + [c for c in have if c != pref]
    return list(dict.fromkeys(have))


def suggest(db: Session, patient: Patient, now: datetime | None = None) -> dict[str, Any]:
    """Advisory best channel and UAE hour band, with a short reason and the evidence used."""
    now = now or utcnow()
    available = _available(patient)
    personal = _attempts(db, patient_id=patient.id)
    cohort = _attempts(db, patient_id=None)
    n_personal = len(personal)
    source = "patient" if n_personal >= PATIENT_MIN else ("blended" if n_personal else "cohort")
    rates = _blend(_rates(personal), _rates(cohort), n_personal)

    # restrict to channels the patient can actually use; if none, keep all for a generic hint.
    # When this patient has enough of their own history, only consider cells THEY have tried —
    # a popular cohort channel they have never used must not override a channel that already works for them.
    personal_cells = set(_rates(personal))
    use_cells = rates if n_personal < PATIENT_MIN else {k: rates[k] for k in rates if k in personal_cells}
    candidates = [(ch, band, use_cells[(ch, band)]) for (ch, band) in use_cells if not available or ch in available]
    if not candidates:
        # no history at all: clinic-hours default on the preferred / first available channel
        ch = available[0] if available else "phone"
        band = "morning"
        reason = (
            "No contact history yet. Try the patient's preferred channel during clinic hours "
            "(08:00–17:00 UAE)."
            if not available or not patient.preferred_channel
            else f"No contact history yet. The record says they prefer {CHANNEL_LABEL.get(patient.preferred_channel, patient.preferred_channel)}; try clinic hours (08:00–17:00 UAE)."
        )
        return {
            "channel": ch, "channel_label": CHANNEL_LABEL.get(ch, ch),
            "hour_band": band, "hour_band_label": "Clinic hours (08:00–17:00 UAE)",
            "reason": reason, "source": "default", "n_patient_attempts": n_personal,
            "success_rate": None, "successes": 0, "attempts": 0,
            "available_channels": available,
        }

    # highest smoothed rate; ties prefer a preferred channel and a daytime band
    pref = patient.preferred_channel
    day = {"morning": 0, "afternoon": 1, "evening": 2, "other": 3}

    def key(item):
        ch, band, (rate, s, n) = item
        return (-rate, 0 if ch == pref else 1, day.get(band, 9), 0 if ch in available[:1] else 1)

    ch, band, (rate, succ, n) = sorted(candidates, key=key)[0]
    if source == "patient":
        reason = (
            f"Best from this patient's previous outreach: {succ} of {n} {CHANNEL_LABEL[ch].lower()} "
            f"attempts in the {BAND_LABEL[band].lower()} were answered."
            if n else f"Based on this patient's previous outreach, try {CHANNEL_LABEL[ch].lower()} in the {BAND_LABEL[band].lower()}."
        )
    elif source == "blended":
        reason = (
            f"Based on this patient's {n_personal} previous attempt(s) and what usually works for others: "
            f"try {CHANNEL_LABEL[ch].lower()} in the {BAND_LABEL[band].lower()}."
        )
    else:
        reason = (
            f"No history for this patient yet. Other patients are most often reached by "
            f"{CHANNEL_LABEL[ch].lower()} in the {BAND_LABEL[band].lower()}."
        )
    return {
        "channel": ch, "channel_label": CHANNEL_LABEL.get(ch, ch),
        "hour_band": band, "hour_band_label": BAND_LABEL[band],
        "reason": reason, "source": source, "n_patient_attempts": n_personal,
        "success_rate": round(float(rate), 3), "successes": int(succ), "attempts": int(n),
        "available_channels": available,
    }
