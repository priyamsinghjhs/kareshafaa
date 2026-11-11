"""Aggregate KPIs with small-cell suppression. Analysts only ever receive outputs of this module."""
from __future__ import annotations

import math

from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..models import Appointment, CarePlan, Case, Consent, Lesson, Patient, QuestionnaireResponse, ServiceRequest, StepTask

SLICES = ("language", "nationality_group", "gender", "age_band", "region", "programme")


def _thr() -> int:
    return get_settings().suppression_threshold


def suppress_count(n: int, thr: int | None = None) -> int | str:
    thr = thr or _thr()
    return int(n) if n == 0 or n >= thr else f"<{thr}"


def suppress_rate(num: int, den: int, thr: int | None = None) -> float | str | None:
    thr = thr or _thr()
    if den == 0:
        return None
    if den < thr or 0 < num < thr or 0 < den - num < thr:
        return "suppressed"
    return round(num / den, 3)


Z95 = 1.96


def wilson(k: int, n: int, z: float = Z95) -> tuple[float, float] | None:
    """Wilson score 95% interval for k of n (None when n == 0)."""
    if n <= 0:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return max(0.0, c - h), min(1.0, c + h)


def prop(num: int, den: int, thr: int | None = None) -> dict[str, Any]:
    """A proportion as numerator, denominator, rate and Wilson 95% interval, with the same small-cell suppression as
    suppress_rate: a suppressed cell has no rate and no interval (never an unstable figure)."""
    num, den = int(num), int(den)
    rate = suppress_rate(num, den, thr)
    if not isinstance(rate, float):
        return {"num": suppress_count(num, thr), "den": suppress_count(den, thr), "rate": rate, "ci_low": None, "ci_high": None}
    lo, hi = wilson(num, den)
    return {"num": num, "den": den, "rate": rate, "ci_low": round(lo, 3), "ci_high": round(hi, 3)}


def secondary_suppress(cells: dict[str, int], thr: int | None = None) -> dict[str, int | str]:
    """Primary suppression of 1..thr-1, plus complementary suppression so a single hidden cell
    cannot be recovered from the total."""
    thr = thr or _thr()
    out: dict[str, int | str] = {k: suppress_count(v, thr) for k, v in cells.items()}
    hidden = [k for k, v in out.items() if isinstance(v, str)]
    if len(hidden) == 1:
        visible = sorted((v, k) for k, v in cells.items() if k not in hidden and v > 0)
        if visible:
            out[visible[0][1]] = "suppressed"
    return out


def _frames(db: Session) -> tuple[pd.DataFrame, pd.DataFrame]:
    opted_out = set(db.scalars(select(Consent.patient_id).where(Consent.scope == "analytics", Consent.decision == "deny",
                                                                  Consent.status == "active")))
    cases = pd.DataFrame(db.execute(select(
        Case.id, Case.patient_id, Case.programme, Case.tier, Case.state, Case.created_at, Case.closed_at,
        Case.closure_type, Case.closure_reason, Case.care_reached_at, Case.due_at, Patient.language,
        Patient.nationality_group, Patient.gender, Patient.age_band, Patient.region)
        .join(Patient, Patient.id == Case.patient_id)).all())
    if not cases.empty:
        cases = cases[~cases.patient_id.isin(opted_out)]
    steps = pd.DataFrame(db.execute(select(StepTask.case_id, StepTask.state, StepTask.started_at, StepTask.breached,
                                           StepTask.status, StepTask.due_at)).all())
    return cases, steps


def _first_entry(steps: pd.DataFrame, state: str) -> pd.Series:
    s = steps[steps.state == state]
    return s.groupby("case_id").started_at.min()


def _median_days(cases: pd.DataFrame, entered: pd.Series) -> float | None:
    if cases.empty:
        return None
    d = (entered.reindex(cases.id) - cases.set_index("id").created_at).dropna().dt.total_seconds() / 86400
    if len(d) < _thr():
        return None
    return round(float(np.median(d)), 1)


def kpis(db: Session, now=None) -> dict[str, Any]:
    from ..models import utcnow

    now = now or utcnow()
    cases, steps = _frames(db)
    if cases.empty:
        return {"label": "Live figures from the records in this system", "total_cases": 0}
    ids = set(cases.id)
    steps = steps[steps.case_id.isin(ids)]
    n = len(cases)
    closed = cases[cases.state == "closed"]
    open_ = cases[cases.state != "closed"]
    in_prog = steps[steps.status == "in-progress"]
    overdue = int(((in_prog.due_at.notna()) & (in_prog.due_at < now)).sum())
    care_days = ((cases.care_reached_at - cases.created_at).dropna().dt.total_seconds() / 86400)
    refs = pd.DataFrame(db.execute(select(ServiceRequest.case_id, ServiceRequest.status)
                                   .where(ServiceRequest.kind == "referral")).all())
    appts = pd.DataFrame(db.execute(select(Appointment.case_id, Appointment.status)).all())
    tb = pd.DataFrame(db.execute(select(QuestionnaireResponse.case_id, QuestionnaireResponse.passed,
                                        QuestionnaireResponse.confidence, QuestionnaireResponse.mode)).all())
    refs = refs[refs.case_id.isin(ids)] if not refs.empty else refs
    appts = appts[appts.case_id.isin(ids)] if not appts.empty else appts
    tb = tb[tb.case_id.isin(ids)] if not tb.empty else tb
    ref_cases = refs.case_id.nunique() if not refs.empty else 0
    ref_acc = refs[refs.status.isin(["accepted", "completed"])].case_id.nunique() if not refs.empty else 0
    appt_cases = appts.case_id.nunique() if not appts.empty else 0
    appt_att = appts[appts.status == "fulfilled"].case_id.nunique() if not appts.empty else 0
    noshow = int((appts.status == "noshow").sum()) if not appts.empty else 0
    conf = pd.DataFrame(db.execute(select(ServiceRequest.case_id, ServiceRequest.decided_at)
                                   .where(ServiceRequest.kind == "confirmatory_test", ServiceRequest.decided_at.is_not(None))).all())
    conf_at = conf.groupby("case_id").decided_at.min() if not conf.empty else pd.Series(dtype="datetime64[ns]")
    careplans = set(db.scalars(select(CarePlan.case_id))) & ids
    first_tb = tb.groupby("case_id").first() if not tb.empty else tb
    tb_pass = int(first_tb.passed.sum()) if not tb.empty else 0
    tb_n = len(first_tb) if not tb.empty else 0
    lessons = pd.DataFrame(db.execute(select(Lesson.kind, Lesson.reason_code)).all())
    delay = {}
    if not lessons.empty:
        lc = lessons.groupby(["kind", "reason_code"]).size().sort_values(ascending=False).head(15)
        delay = {f"{k}:{r}": suppress_count(int(v)) for (k, r), v in lc.items()}
    out = {
        "label": "Live figures from the records in this system",
        "total_cases": suppress_count(n), "open_cases": suppress_count(len(open_)),
        "open_overdue_steps": suppress_count(overdue), "closed_cases": suppress_count(len(closed)),
        "closed_with_evidence": suppress_count(int((closed.closure_type == "evidence").sum())),
        "closed_authorised_decision": suppress_count(int((closed.closure_type == "authorised_decision").sum())),
        "closure_rate": suppress_rate(len(closed), n),
        "documented_closure_rate_of_closed": 1.0 if len(closed) else None,
        "median_days_to_notification": _median_days(cases, _first_entry(steps, "patient_notified")),
        "median_days_to_acknowledgement": _median_days(cases, _first_entry(steps, "acknowledged")),
        "median_days_to_confirmation": _median_days(cases, conf_at),
        "median_days_to_consultation": _median_days(cases, _first_entry(steps, "consultation")),
        "median_days_to_care": round(float(np.median(care_days)), 1) if len(care_days) >= _thr() else None,
        "referral_acceptance_rate": suppress_rate(ref_acc, ref_cases),
        "appointment_attendance_rate": suppress_rate(appt_att, appt_cases),
        "no_show_events": suppress_count(noshow),
        "treatment_initiation_rate": suppress_rate(len(careplans), n),
        "teach_back_pass_rate_first_attempt": suppress_rate(tb_pass, tb_n),
        "mean_confidence_1to5": round(float(tb.confidence.dropna().mean()), 2) if not tb.empty and tb.confidence.notna().sum() >= _thr() else None,
        **_recall_kpis(db, now),
        "open_by_state": secondary_suppress(open_.state.value_counts().to_dict()),
        "delay_and_dropoff_reasons": delay,
        "suppression_threshold": _thr(),
    }
    return out


def fairness_slices(db: Session) -> dict[str, list[dict]]:
    cases, steps = _frames(db)
    if cases.empty:
        return {}
    tb = pd.DataFrame(db.execute(select(QuestionnaireResponse.case_id, QuestionnaireResponse.passed)).all())
    first_tb = tb.groupby("case_id").passed.first() if not tb.empty else pd.Series(dtype=bool)
    notified = _first_entry(steps, "patient_notified")
    cases = cases.assign(
        closed=cases.state == "closed", care=cases.care_reached_at.notna(),
        notified=cases.id.isin(notified.index),
        tb_pass=cases.id.map(first_tb), care_days=(cases.care_reached_at - cases.created_at).dt.total_seconds() / 86400)
    out: dict[str, list[dict]] = {}
    for sl in SLICES:
        rows = []
        for grp, g in cases.groupby(sl):
            n = len(g)
            tbn = int(g.tb_pass.notna().sum())
            rows.append({
                "group": str(grp), "n": suppress_count(n),
                "notified_rate": suppress_rate(int(g.notified.sum()), n),
                "care_reached_rate": suppress_rate(int(g.care.sum()), n),
                "closure_rate": suppress_rate(int(g.closed.sum()), n),
                "teach_back_pass_rate": suppress_rate(int(g.tb_pass.fillna(False).astype(bool).sum()), tbn),
                "median_days_to_care": round(float(g.care_days.dropna().median()), 1) if g.care_days.notna().sum() >= _thr() else None,
            })
        out[sl] = rows
    return out



def score_band_equity(db: Session) -> dict:
    """Distribution of the advisory missed-appointment risk band by language and nationality.

    Language and nationality are NOT model inputs (kareshafaa/ml/features.py). A higher score
    means the case is offered MORE outreach support, never less. Small cells are suppressed
    with the same rule as the other fairness slices.
    """
    from collections import defaultdict

    from ..services.priority import BAND_HIGH, BAND_MEDIUM, CALIBRATION_NOTE

    rows = db.execute(
        select(Case.risk_score, Patient.language, Patient.nationality_group)
        .join(Patient, Patient.id == Case.patient_id)
        .where(Case.state != "closed", Case.risk_score.is_not(None))
    ).all()
    note = (
        "Higher scores mean more outreach support, never less. Language and nationality are not "
        "used as model inputs; they are shown only so a skewed band distribution can be reviewed. "
        + CALIBRATION_NOTE
    )

    def band(score: float) -> str:
        if score >= BAND_HIGH:
            return "high"
        if score >= BAND_MEDIUM:
            return "medium"
        return "low"

    def slice_of(idx: int) -> list[dict]:
        groups: dict[str, list[float]] = defaultdict(list)
        for r in rows:
            groups[str(r[idx] or "unknown")].append(float(r[0]))
        out = []
        for g, scores in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
            n = len(scores)
            n_high = sum(1 for s in scores if band(s) == "high")
            n_med = sum(1 for s in scores if band(s) == "medium")
            n_low = n - n_high - n_med
            hidden = n < _thr()
            out.append({
                "group": g,
                "n": suppress_count(n),
                "n_high": suppress_count(n) if hidden else suppress_count(n_high),
                "n_medium": suppress_count(n) if hidden else suppress_count(n_med),
                "n_low": suppress_count(n) if hidden else suppress_count(n_low),
                "mean_score": None if hidden else round(sum(scores) / n, 3),
                "share_high": None if hidden else suppress_rate(n_high, n),
            })
            if hidden:
                out[-1]["n_high"] = out[-1]["n_medium"] = out[-1]["n_low"] = out[-1]["n"]
                out[-1]["share_high"] = None
        return out

    return {"note": note, "n_scored": suppress_count(len(rows)),
            "by_language": slice_of(1), "by_nationality_group": slice_of(2)}


# ---- aggregate cache ------------------------------------------------------------------------------------
# KPIs scan every case and step (O(N)); dashboards poll them. Results are cached per process and keyed on
# a cheap data fingerprint (max ids / counts of the tables they read), so any new event invalidates them,
# with a TTL as a backstop for in-place updates. Cached values are already suppressed aggregates.
_CACHE: dict[str, tuple[tuple, float, Any]] = {}
CACHE_TTL_S = 60.0


def _recall_kpis(db: Session, now) -> dict:
    from .recall import uptake

    u = uptake(db, now)
    return {"recall_uptake_rate": suppress_rate(u["completed_in_window"], u["denominator"]),
            "recalls_due_now": suppress_count(u["open_due_now"]), "recall_uptake_window_days": u["window_days"]}


def _fingerprint(db: Session) -> tuple:
    from sqlalchemy import func

    parts = []
    from ..models import Recall

    for m in (Case, StepTask, Consent, QuestionnaireResponse, ServiceRequest, Appointment, CarePlan, Lesson, Recall):
        parts.append(tuple(db.execute(select(func.max(m.id), func.count(m.id))).one()))
    return tuple(parts)


MIN_REFRESH_S = 10.0  # bounded staleness: under constant writes, recompute at most every 10 s per process
_LOCKS: dict[str, Any] = {}


def cached(name: str, db: Session, fn) -> Any:
    """Aggregate cache: reused while the data fingerprint is unchanged (up to CACHE_TTL_S). When data has
    changed, a value at most MIN_REFRESH_S old is still served, and only one thread recomputes (single
    flight) so a burst of dashboard requests cannot stampede the database."""
    import threading
    import time

    lock = _LOCKS.setdefault(name, threading.Lock())
    now = time.monotonic()
    hit = _CACHE.get(name)
    if hit and now - hit[1] < MIN_REFRESH_S:
        return hit[2]
    if hit and lock.locked() and now - hit[1] < CACHE_TTL_S:
        return hit[2]  # a refresh is already running: serve the previous value instead of queueing behind it
    fp = _fingerprint(db)
    if hit and hit[0] == fp and now - hit[1] < CACHE_TTL_S:
        return hit[2]
    with lock:
        hit = _CACHE.get(name)  # another thread may have refreshed it while we waited
        # round 20 load ramp: this re-check also demanded an identical fingerprint, so under constant ingest every
        # waiter recomputed in turn (KPI p95 2-6 s). Any value refreshed within MIN_REFRESH_S is fresh enough.
        if hit and time.monotonic() - hit[1] < MIN_REFRESH_S:
            return hit[2]
        val = fn()
        _CACHE[name] = (fp, time.monotonic(), val)
        return val


def clear_cache() -> None:
    _CACHE.clear()
