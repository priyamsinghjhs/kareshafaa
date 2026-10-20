"""Alert precision / recall on a seeded cohort (default: the 20k realistic DB).

Replays the stored step timelines (start, end, due, tier, pathway target, care reached) through the alert
policy at the live sweep cadence (5 minutes) and compares the alerts with an INDEPENDENT ground-truth labelling
of true breaches:
  * step breach   - a step whose own deadline (stored due_at, earlier than the pathway target or after care was
                    reached) passed while the step was still open;
  * target breach - the case's end-to-end pathway target passed before care was reached and before closure.
Hard requirement: every true breach that lasts longer than one sweep interval gets an L2+ alert within one
sweep interval (recall = 100 %). Precision = first alert per (true breach, level) / all L2+ alerts.
Policies compared: LEGACY (Iteration 6: step clock only, repeat per step) vs CURRENT (wasl/alert_policy.py).

Usage: WASL_DATABASE_URL=sqlite:///./data/wasl_large.db python scripts/alert_eval.py [--out docs/alert_precision.md]
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import create_engine, text  # noqa: E402

from wasl.alert_policy import Ladder, decide, ladder_level  # noqa: E402

SWEEP = timedelta(minutes=5)
LAD = Ladder()
EPOCH = datetime(2000, 1, 1)


def grid(t: datetime) -> datetime:
    n = math.ceil((t - EPOCH) / SWEEP)
    return EPOCH + n * SWEEP


def _p(x):
    if x is None or isinstance(x, datetime):
        return x
    return datetime.fromisoformat(str(x))


def legacy_decide(tier, s_start, s_due, now, step_alerted):
    """Iteration-6 behaviour: step clock only; steps opened after a missed target have due == start (frac = 1)."""
    if s_due is None:
        return "none", 0, ()
    total = (s_due - s_start).total_seconds()
    frac = (now - s_start).total_seconds() / total if total > 0 else 1.0
    lv = ladder_level(frac, tier, LAD)
    return "step", lv, tuple(range(step_alerted + 1, lv + 1))


def replay(case, steps, horizon, policy):
    """Return list of alerts (time, level, clock, step_id)."""
    alerts = []
    tgt_alerted = 0
    created, target, care = case["created_at"], case["target_due_at"], case["care_reached_at"]
    for st in steps:
        s, e, due = st["started_at"], st["ended_at"] or horizon, st["due_at"]
        step_alerted = 0
        cands = {grid(s)}
        if due is not None and due > s:
            cands |= {grid(s + f * (due - s)) for f in (LAD.reminder_fraction, 1, 1 + LAD.clinical_lead_after_breach_fraction,
                                                        LAD.director_after_fraction)}
        elif due is not None:
            cands.add(grid(s))
        if target is not None and target > created:
            cands |= {grid(created + f * (target - created)) for f in (1, 1 + LAD.clinical_lead_after_breach_fraction,
                                                                         LAD.director_after_fraction)}
        for now in sorted(c for c in cands if s <= c < e):
            care_now = care if (care is not None and care <= now) else None
            if policy == "legacy":
                clock, lv, new = legacy_decide(case["tier"], s, due, now, step_alerted)
                step_alerted = max(step_alerted, lv)
            else:
                d = decide(tier=case["tier"], step_started=s, step_due=due, case_created=created, target_due=target,
                           care_reached=care_now, now=now, step_level_alerted=step_alerted,
                           target_level_alerted=tgt_alerted, lad=LAD)
                clock, lv, new = d.clock, d.level, d.new_levels
                if clock == "pathway":
                    tgt_alerted = max(tgt_alerted, lv)
                else:
                    step_alerted = max(step_alerted, lv)
            for x in new:
                alerts.append((now, x, clock, st["id"]))
    return alerts


def truth(case, steps, horizon):
    """Independent labelling of true breaches: {key: (start_time, end_time)}."""
    ev = {}
    created, target, care, closed = case["created_at"], case["target_due_at"], case["care_reached_at"], case["closed_at"]
    if target is not None and (care is None or care > target) and (closed is None or closed > target) and horizon > target:
        end = min(x for x in (care, closed, horizon) if x is not None)
        ev[("target", case["id"])] = (target, end)
    for st in steps:
        s, e, due = st["started_at"], st["ended_at"] or horizon, st["due_at"]
        if due is None:
            continue
        post_care = care is not None and care <= s
        if post_care or target is None or due < target:
            if e > due:
                ev[("step", st["id"])] = (due, e)
    return ev


def key_of(alert, case, steps_by_id):
    t, lv, clock, sid = alert
    if clock == "pathway":
        return ("target", case["id"])
    st = steps_by_id[sid]
    target, care = case["target_due_at"], case["care_reached_at"]
    post_care = care is not None and care <= st["started_at"]
    if target is not None and not post_care and st["due_at"] is not None and st["due_at"] >= target:
        return ("target", case["id"])
    return ("step", sid)


NARRATIVE = """
## Policy evaluated (adopted 21 Oct 2026)

* **Step clock.** Each workflow step has its own SLA. One L2 alert when it breaches (L3 for critical tier), then
  L3 at 1.5x and L4 at 2x the step window (L4 for critical/urgent only). L1 is a pre-breach reminder at 50 %.
* **Pathway clock.** When the clinical pathway target date (e.g. colonoscopy within 4 weeks of a positive FIT) is
  missed before care is reached, the case gets **one consolidated alert per escalation level**, attributed to the
  upstream breach. A step that opens after the target was missed is born breached (`inherited_breach`), does
  not raise its own repeat alert and does not record a second lesson; the ladder still escalates L2 -> L3 -> L4 on
  the pathway clock while the breach persists.
* **Never suppressed.** The first alert on a true breach is never deduplicated away: deduplication keys are per
  step (step clock) and per case (pathway clock) and only suppress *repeats at the same level*.

## How truth is labelled

The truth labelling in this script is written independently of `wasl/alert_policy.py`. From each stored case
timeline it derives the breach events a human auditor would count: a pathway-target breach (target passed before
care was reached) and step breaches for steps whose own deadline came before the target (or after care). A
true breach is **detected** if an L2+ alert for it is raised within one sweep (5 min) of the breach. Precision
counts an alert as a true positive only if it is the first alert at that level for a distinct true breach;
repeats for the same breach are false positives.

## Limits

* Precision here means "every L2+ alert corresponds to a distinct, real breach at a new level". It does not
  measure whether the team found the alert clinically useful; that needs pilot feedback (alert-fatigue survey).
* The labelling uses the same stored timestamps as the system, so data-capture errors would affect both.
* Breaches that resolve within one sweep (counted under `resolved_within_one_sweep`) are recorded at step close
  but cannot be alerted in time; with a 5-minute sweep this was 2 events in this cohort.
* `scripts/alert_eval.py` exits with status 1 if any true breach is missed; `tests/test_cohort_full.py` runs it on
  the 20k cohort.
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=os.environ.get("WASL_DATABASE_URL", "sqlite:///./data/wasl_large.db"))
    ap.add_argument("--out", default="docs/alert_precision.md")
    a = ap.parse_args()
    eng = create_engine(a.db)
    with eng.connect() as c:
        cases = {r.id: dict(r._mapping) for r in c.execute(text(
            "select id, tier, created_at, target_due_at, care_reached_at, closed_at from cases"))}
        steps = defaultdict(list)
        for r in c.execute(text("select id, case_id, state, started_at, ended_at, due_at from step_tasks "
                                "where state != 'closed' order by case_id, started_at, id")):
            steps[r.case_id].append(dict(r._mapping))
        horizon = _p(c.execute(text("select max(ts) from audit_events")).scalar())
    for cs in cases.values():
        for k in ("created_at", "target_due_at", "care_reached_at", "closed_at"):
            cs[k] = _p(cs[k])
    for lst in steps.values():
        for st in lst:
            for k in ("started_at", "ended_at", "due_at"):
                st[k] = _p(st[k])
    res = {}
    for policy in ("legacy", "current"):
        tp = fp = l1 = 0
        detected = missed = short = 0
        fp_kinds = Counter()
        missed_examples = []
        ladder_expected = ladder_reached = 0
        for cid, case in cases.items():
            sts = steps.get(cid, [])
            by_id = {s["id"]: s for s in sts}
            alerts = replay(case, sts, horizon, policy)
            ev = truth(case, sts, horizon)
            seen = set()
            first_l2 = {}
            for al in alerts:
                if al[1] == 1:
                    l1 += 1
                    continue
                k = key_of(al, case, by_id)
                if k in ev and (k, al[1]) not in seen:
                    tp += 1
                    seen.add((k, al[1]))
                    first_l2.setdefault(k, al[0])
                else:
                    fp += 1
                    fp_kinds["repeat_for_same_breach" if k in ev else "no_true_breach"] += 1
            for k, (t0, t1) in ev.items():
                if t1 - t0 <= SWEEP:
                    short += 1  # resolved within one sweep: recorded as a breach at step close, no alert possible
                    continue
                if k in first_l2 and first_l2[k] <= grid(t0) + SWEEP:
                    detected += 1
                else:
                    missed += 1
                    if len(missed_examples) < 5:
                        missed_examples.append({"case": cid, "event": k, "at": str(t0), "until": str(t1),
                                                "first_alert": str(first_l2.get(k))})
                if k[0] == "target":  # does the ladder keep escalating while the target breach persists?
                    win = case["target_due_at"] - case["created_at"]
                    for f, lvl in ((1 + LAD.clinical_lead_after_breach_fraction, 3), (LAD.director_after_fraction, 4)):
                        due_t = case["created_at"] + f * win
                        if lvl == 4 and case["tier"] not in ("critical", "urgent"):
                            continue
                        if lvl == 3 and case["tier"] == "critical":
                            continue
                        if t1 - due_t > SWEEP:
                            ladder_expected += 1
                            ladder_reached += int((k, lvl) in seen)
        total = tp + fp
        n_ev = detected + missed
        res[policy] = {"alerts_l2plus": total, "true_positive": tp, "false_positive": fp, "fp_kinds": dict(fp_kinds),
                       "precision": tp / total if total else 1.0, "true_breaches_alertable": n_ev,
                       "detected_within_one_sweep": detected, "missed": missed,
                       "recall": detected / n_ev if n_ev else 1.0, "resolved_within_one_sweep": short,
                       "l1_reminders": l1, "ladder_escalations_expected": ladder_expected,
                       "ladder_escalations_reached": ladder_reached, "missed_examples": missed_examples}
    print(json.dumps(res, indent=1))
    if a.out:
        with open(a.out, "w") as f:
            f.write("# Alert precision and recall (Iteration 7)\n\n")
            f.write(f"Cohort: `{a.db}`; {len(cases):,} cases; horizon {horizon}. Sweep cadence 5 minutes. Generated by "
                    "`scripts/alert_eval.py`. True breaches are labelled independently of the alert policy (see the "
                    "script docstring). L1 reminders are pre-breach nudges and are reported separately.\n\n")
            f.write("| Metric | Legacy (Iteration 6) | Current policy |\n|---|---|---|\n")
            for k in ("true_breaches_alertable", "detected_within_one_sweep", "missed", "recall", "alerts_l2plus",
                      "true_positive", "false_positive", "precision", "ladder_escalations_expected",
                      "ladder_escalations_reached", "l1_reminders", "resolved_within_one_sweep"):
                fmt = (lambda v: f"{v:.2%}") if k in ("precision", "recall") else (lambda v: f"{v:,}")
                f.write(f"| {k} | {fmt(res['legacy'][k])} | {fmt(res['current'][k])} |\n")
            f.write(f"\nFalse-positive kinds - legacy: {res['legacy']['fp_kinds']}; current: {res['current']['fp_kinds']}\n")
            f.write(NARRATIVE)
        with open(a.out.replace(".md", ".json"), "w") as f:
            json.dump(res, f, indent=1)
    return 1 if res["current"]["missed"] else 0


if __name__ == "__main__":
    sys.exit(main())
