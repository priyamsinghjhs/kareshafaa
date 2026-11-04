"""Loop-closure impact report for the clinical lead (and analysts): did each abnormal result reach care, how fast,
against the pathway target, who was lost, and is every group served equally. Aggregates only, with the same
small-cell suppression as the other analytics; patients who opted out of analytics are excluded.

Definitions (shown on the page and in the CSV so nobody has to guess):
* mature case: created at least LOOP_MATURITY_DAYS ago, so it had time to finish its pathway.
* loop closed: the patient reached care (treatment or surveillance started, or a fulfilled specialist appointment),
  or the case closed with evidence (incl. a negative confirmatory test).
* closed by decision: closed by an authorised clinician decision with a reason (e.g. informed decline, care elsewhere).
* lost to follow-up: still open, more than LOST_GRACE_DAYS past its pathway target, with no step change for
  LOST_STALE_DAYS.
* first specialist contact: the first fulfilled appointment of the case.
"""
from __future__ import annotations

import csv
import io
from datetime import timedelta
from typing import Any

import numpy as np
import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from ..clinical_rules import TIER_TARGET_DAYS
from ..models import Appointment, Case, utcnow
from .analytics import _frames, _thr, suppress_count, suppress_rate
from .patients import LANGUAGES

LOOP_MATURITY_DAYS = 90   # CONFIRM: a case counts in closure / lost rates once it is 90 days old
LOST_GRACE_DAYS = 30      # CONFIRM: open and more than 30 days past its pathway target ...
LOST_STALE_DAYS = 30      # CONFIRM: ... with no step change for 30 days = lost to follow-up
EQUITY_GAP_PP = 10.0      # CONFIRM: flag a group whose loop-closure rate is 10+ percentage points below the overall rate
EQUITY_SLICES = (("language", "Language"), ("nationality_group", "Nationality group"), ("region", "Region"))
TIER_LABEL = {"critical": "Critical (T0)", "urgent": "Urgent (T1)", "high": "Soon (T2)", "routine": "Routine (T3)"}
PROGRAMME_LABEL = {"colorectal_fit": "Bowel (FIT)", "cervical": "Cervical (HPV)", "breast": "Breast (mammogram)",
                   "diabetes_hba1c": "Diabetes (HbA1c)"}
DEFINITIONS = {
    "mature_case": f"created at least {LOOP_MATURITY_DAYS} days ago",
    "loop_closed": "reached care (treatment or surveillance started, or specialist appointment attended) or closed with evidence",
    "closed_by_decision": "closed by an authorised clinician decision with a recorded reason",
    "lost_to_follow_up": f"still open, more than {LOST_GRACE_DAYS} days past its pathway target, no step change for {LOST_STALE_DAYS} days",
    "first_specialist_contact": "first attended appointment of the case, counted from the day the result arrived",
    "within_target": "first specialist contact on or before the case's end-to-end pathway target date",
    "recall_uptake": "of recalls due long enough ago, the share screened within the uptake window",
}


def _rate(num: int, den: int) -> float | str | None:
    return suppress_rate(int(num), int(den))


def _pp(v) -> float | None:
    return round(v * 100, 1) if isinstance(v, (int, float)) else None


def _frame(db: Session, programme: str | None, now) -> pd.DataFrame:
    cases, steps = _frames(db)
    if cases.empty:
        return cases
    if programme:
        cases = cases[cases.programme == programme]
    extra = pd.DataFrame(db.execute(select(Case.id, Case.target_due_at, Case.state_entered_at, Case.synthetic)).all(),
                         columns=["id", "target_due_at", "state_entered_at", "synthetic"])
    cases = cases.merge(extra, on="id", how="left")
    first = pd.DataFrame(db.execute(select(Appointment.case_id, func.min(Appointment.start))
                                    .where(Appointment.status == "fulfilled").group_by(Appointment.case_id)).all(),
                         columns=["id", "first_contact"])
    cases = cases.merge(first, on="id", how="left")
    if not steps.empty:
        last = steps.groupby("case_id").started_at.max().rename("last_step").reset_index().rename(columns={"case_id": "id"})
        cases = cases.merge(last, on="id", how="left")
    else:
        cases["last_step"] = pd.NaT
    last_change = cases[["state_entered_at", "last_step"]].max(axis=1)
    mature_cut = now - timedelta(days=LOOP_MATURITY_DAYS)
    is_open = cases.state != "closed"
    cases = cases.assign(
        mature=cases.created_at <= mature_cut,
        loop_closed=cases.care_reached_at.notna() | cases.first_contact.notna() | (cases.closure_type == "evidence"),
        by_decision=(cases.state == "closed") & (cases.closure_type == "authorised_decision") & cases.care_reached_at.isna(),
        lost=is_open & cases.target_due_at.notna() & (cases.target_due_at < now - timedelta(days=LOST_GRACE_DAYS))
        & (last_change < now - timedelta(days=LOST_STALE_DAYS)),
        days_to_contact=(cases.first_contact - cases.created_at).dt.total_seconds() / 86400,
        met_target=cases.first_contact.notna() & cases.target_due_at.notna() & (cases.first_contact <= cases.target_due_at),
        waiting_past_target=is_open & cases.first_contact.isna() & cases.target_due_at.notna() & (cases.target_due_at < now),
    )
    return cases


def _median(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(np.median(s)), 1) if len(s) >= _thr() else None


def _p90(s: pd.Series) -> float | None:
    s = s.dropna()
    return round(float(np.percentile(s, 90)), 1) if len(s) >= _thr() else None


def _group_rows(m: pd.DataFrame, overall: float | None, col: str) -> list[dict]:
    rows = []
    for grp, g in m.groupby(col):
        n = len(g)
        rate = suppress_rate(int(g.loop_closed.sum()), n)
        gap = round((overall - rate) * 100, 1) if isinstance(rate, float) and isinstance(overall, float) else None
        label = LANGUAGES.get(str(grp), str(grp)) if col == "language" else str(grp)
        rows.append({"group": label, "code": str(grp), "n": suppress_count(n), "loop_closure_rate": rate,
                     "lost_rate": suppress_rate(int(g.lost.sum()), n),
                     "median_days_to_specialist": _median(g.days_to_contact),
                     "gap_pp": gap, "flag": bool(gap is not None and gap >= EQUITY_GAP_PP)})
    return sorted(rows, key=lambda r: -(r["n"] if isinstance(r["n"], int) else 0))


BARRIER_LABEL = {
    "transport": "Transport", "work": "Work hours", "fear_anxiety": "Fear or anxiety", "cost": "Cost",
    "language": "Language", "forgot": "Forgot", "childcare": "Childcare", "ramadan_hours": "Ramadan hours",
    "unknown": "Not given", "patient_not_responding": "Patient not responding to contact",
    "contact_details_wrong": "Contact details wrong", "patient_declined": "Patient declined",
    "teach_back_failed": "Did not understand the result (teach-back)",
}
# Where a loop breaks, from the lessons log (one row per failure event, written by the workflow) and from the
# barrier the patient gave (in the patient app, or to staff after a missed appointment).
PATIENT_SIDE_KINDS = {"no_show": "Missed appointment", "unreachable": "Could not be reached",
                      "declined": "Declined", "teach_back_failed": "Did not understand the result",
                      "overdue_patient": "Step overdue"}
# an overdue step whose recorded reason sits with the patient counts on the patient side
PATIENT_REASONS = {"patient_not_responding", "contact_details_wrong", "language_barrier", "patient_travel",
                   "patient_anxiety"}
SYSTEM_SIDE_KINDS = {"overdue": "Step overdue", "rejected_referral": "Referral rejected", "incident": "Incident"}


def barrier_analysis(db: Session, case_ids: set[int] | None) -> dict[str, Any]:
    """Why loops fail: patient-reported barriers and where the service itself stalled. Counts are events, not
    patients; small counts are suppressed like every other figure on this report."""
    from ..models import AppointmentRequest, Lesson, PatientRequest

    def keep(cid):
        return case_ids is None or cid in case_ids

    reported: dict[str, int] = {}
    for cid, code in db.execute(select(Appointment.case_id, Appointment.barrier_code)
                                .where(Appointment.status == "noshow", Appointment.barrier_code.is_not(None))).all():
        if keep(cid):
            reported[code] = reported.get(code, 0) + 1
    app_answers = 0
    for model in (AppointmentRequest, PatientRequest):
        for cid, code in db.execute(select(model.case_id, model.barrier_code).where(model.barrier_code.is_not(None))).all():
            if keep(cid):
                reported[code] = reported.get(code, 0) + 1
                app_answers += 1
    patient_side: dict[str, int] = {}
    system_side: dict[str, dict[str, int]] = {}
    for cid, kind, reason in db.execute(select(Lesson.case_id, Lesson.kind, Lesson.reason_code)).all():
        if not keep(cid):
            continue
        if kind == "overdue" and reason in PATIENT_REASONS:
            kind = "overdue_patient"
        if kind in PATIENT_SIDE_KINDS:
            key = kind if kind in ("teach_back_failed", "no_show") else f"{kind}:{reason}"
            patient_side[key] = patient_side.get(key, 0) + 1
        elif kind in SYSTEM_SIDE_KINDS:
            system_side.setdefault(kind, {})
            system_side[kind][reason] = system_side[kind].get(reason, 0) + 1

    def rows(d: dict[str, int], label) -> list[dict]:
        tot = sum(d.values())
        return [{"code": k, "label": label(k), "events": suppress_count(v), "share": suppress_rate(v, tot)}
                for k, v in sorted(d.items(), key=lambda kv: -kv[1])]

    def plabel(k: str) -> str:
        kind, _, reason = k.partition(":")
        base = PATIENT_SIDE_KINDS.get(kind, kind)
        return f"{base}: {BARRIER_LABEL.get(reason, reason.replace('_', ' '))}" if reason else base

    sys_rows = []
    for kind, d in system_side.items():
        for r in rows(d, lambda k: k.replace("_", " ").capitalize()):
            sys_rows.append({"kind": kind, "kind_label": SYSTEM_SIDE_KINDS[kind], **r})
    sys_rows.sort(key=lambda r: -(r["events"] if isinstance(r["events"], int) else 0))
    tot_p, tot_s = sum(patient_side.values()), sum(sum(d.values()) for d in system_side.values())
    return {
        "patient_reported": rows(reported, lambda k: BARRIER_LABEL.get(k, k.replace("_", " "))),
        "patient_reported_from_app": suppress_count(app_answers),
        "patient_side": rows(patient_side, plabel),
        "system_side": sys_rows[:12],
        "split": {"patient_side_events": suppress_count(tot_p), "system_side_events": suppress_count(tot_s),
                  "system_share": suppress_rate(tot_s, tot_p + tot_s)},
        "note": ("Events, not patients: one case can fail more than once. 'Patient reported' is what the patient said "
                 "got in the way (in the patient app, or to staff after a missed appointment). 'System side' are steps "
                 "the service itself left overdue or a referral it rejected."),
    }


def impact_report(db: Session, programme: str | None = None, now=None) -> dict[str, Any]:
    from .recall import uptake

    now = now or utcnow()
    c = _frame(db, programme, now)
    total = len(c)
    syn = int(c.synthetic.fillna(True).astype(bool).sum()) if total else 0
    base = {"generated_at": now.isoformat(timespec="seconds"), "programme": programme,
            "programmes": PROGRAMME_LABEL, "definitions": DEFINITIONS,
            "settings": {"loop_maturity_days": LOOP_MATURITY_DAYS, "lost_grace_days": LOST_GRACE_DAYS,
                         "lost_stale_days": LOST_STALE_DAYS, "equity_gap_pp": EQUITY_GAP_PP, "suppression_threshold": _thr()},
            "data": {"cases": suppress_count(total), "synthetic_cases": suppress_count(syn), "all_synthetic": bool(total) and syn == total,
                     "label": (f"Synthetic sample records: all {total} cases are generated test data, not real patients" if total and syn == total
                               else f"{syn} of {total} cases are synthetic sample records; the rest were entered in this system")}}
    if not total:
        return base | {"headline": {}, "by_tier": [], "by_programme": [], "equity": {}}
    m = c[c.mature]
    nm = len(m)
    overall = suppress_rate(int(m.loop_closed.sum()), nm)
    u = uptake(db, now)
    headline = {
        "mature_cases": suppress_count(nm),
        "loop_closure_rate": overall,
        "closed_by_decision_rate": _rate(m.by_decision.sum(), nm),
        "lost_to_follow_up_rate": _rate(m.lost.sum(), nm),
        "lost_to_follow_up_now": suppress_count(int(c.lost.sum())),
        "median_days_to_specialist": _median(c.days_to_contact),
        "within_target_rate": _rate(c.met_target.sum(), c.first_contact.notna().sum()),
        "waiting_past_target_now": suppress_count(int(c.waiting_past_target.sum())),
        "recall_uptake_rate": suppress_rate(u["completed_in_window"], u["denominator"]),
        "recall_uptake_window_days": u["window_days"],
    }
    by_tier = []
    for tier in ("critical", "urgent", "high", "routine"):
        g = c[c.tier == tier]
        if g.empty:
            continue
        seen = g[g.first_contact.notna()]
        by_tier.append({"tier": tier, "label": TIER_LABEL[tier], "target_days_default": TIER_TARGET_DAYS.get(tier),
                        "cases": suppress_count(len(g)), "seen": suppress_count(len(seen)),
                        "median_days": _median(seen.days_to_contact), "p90_days": _p90(seen.days_to_contact),
                        "within_target_rate": _rate(seen.met_target.sum(), len(seen)),
                        "waiting_past_target": suppress_count(int(g.waiting_past_target.sum()))})
    by_prog = []
    for prog, g in c.groupby("programme"):
        gm = g[g.mature]
        by_prog.append({"programme": prog, "label": PROGRAMME_LABEL.get(prog, prog), "cases": suppress_count(len(g)),
                        "loop_closure_rate": _rate(gm.loop_closed.sum(), len(gm)), "lost_rate": _rate(gm.lost.sum(), len(gm)),
                        "median_days_to_specialist": _median(g.days_to_contact)})
    equity = {col: {"label": lab, "rows": _group_rows(m, overall if isinstance(overall, float) else None, col)} for col, lab in EQUITY_SLICES}
    barriers = barrier_analysis(db, set(int(x) for x in c.id))
    return base | {"headline": headline, "by_tier": by_tier, "by_programme": by_prog, "equity": equity,
                   "barriers": barriers}


def impact_csv(rep: dict) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["# KareShafaa loop-closure impact report", rep["generated_at"]])
    w.writerow(["# data", rep["data"]["label"]])
    w.writerow(["# programme", rep["programme"] or "all"])
    for k, v in rep["definitions"].items():
        w.writerow(["# definition", k, v])
    w.writerow(["# small groups", f"counts below {rep['settings']['suppression_threshold']} are suppressed"])
    w.writerow(["section", "group_type", "group", "metric", "value"])
    for k, v in rep.get("headline", {}).items():
        w.writerow(["headline", "", "all", k, v])
    for r in rep.get("by_tier", []):
        for k in ("cases", "seen", "median_days", "p90_days", "within_target_rate", "waiting_past_target", "target_days_default"):
            w.writerow(["time_to_specialist", "tier", r["label"], k, r[k]])
    for r in rep.get("by_programme", []):
        for k in ("cases", "loop_closure_rate", "lost_rate", "median_days_to_specialist"):
            w.writerow(["programme", "programme", r["label"], k, r[k]])
    for col, e in rep.get("equity", {}).items():
        for r in e["rows"]:
            for k in ("n", "loop_closure_rate", "lost_rate", "median_days_to_specialist", "gap_pp", "flag"):
                w.writerow(["equity", col, r["group"], k, r[k]])
    b = rep.get("barriers") or {}
    for r in b.get("patient_reported", []):
        w.writerow(["barrier", "patient_reported", r["label"], "events", r["events"]])
    for r in b.get("patient_side", []):
        w.writerow(["barrier", "patient_side", r["label"], "events", r["events"]])
    for r in b.get("system_side", []):
        w.writerow(["barrier", "system_side", f"{r['kind_label']}: {r['label']}", "events", r["events"]])
    return out.getvalue()
