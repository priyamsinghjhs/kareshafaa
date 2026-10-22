"""Aggregate KPIs with small-cell suppression. Analysts only ever receive outputs of this module."""
from __future__ import annotations

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
        return {"label": "SIMULATED DATA - synthetic cohort", "total_cases": 0}
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
        "label": "SIMULATED DATA - synthetic cohort, not real-world performance",
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


# ---- aggregate cache ------------------------------------------------------------------------------------
# KPIs scan every case and step (O(N)); dashboards poll them. Results are cached per process and keyed on
# a cheap data fingerprint (max ids / counts of the tables they read), so any new event invalidates them,
# with a TTL as a backstop for in-place updates. Cached values are already suppressed aggregates.
_CACHE: dict[str, tuple[tuple, float, Any]] = {}
CACHE_TTL_S = 60.0


def _fingerprint(db: Session) -> tuple:
    from sqlalchemy import func

    parts = []
    for m in (Case, StepTask, Consent, QuestionnaireResponse, ServiceRequest, Appointment, CarePlan, Lesson):
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
