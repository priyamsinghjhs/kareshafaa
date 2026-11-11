"""SLA sweep and escalation ladder (KareShafaa design 2.5):
L1 nudge to owner + patient reminder (50 % of SLA) -> L2 programme coordinator/supervisor (breach)
-> L3 programme clinical lead (1.5x SLA; T0 goes straight here on breach)
-> L4 programme medical director / duty clinician (2x SLA, T0/T1 only; T2/T3 go to the weekly review list).

Escalations alert named humans. They never change a case's clinical state or close a case.
The only automatic ownership change is the safety net that moves a case whose owner has become
inactive to the programme's team pool, so that a case is never without an accountable owner.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import Case, Communication, Escalation, OwnershipEvent, Practitioner, StepTask, utcnow
from ..security import SYSTEM, Actor
from ..alert_policy import AlertDecision, Ladder, decide
from ..sla import SlaLookup
from ..statemachine import S
from .access import consent_status
from .workflow import add_lesson, programme_clinical_lead, programme_director, programme_pool

PATIENT_FACING = {S.PATIENT_NOTIFIED, S.NO_SHOW, S.APPOINTMENT_BOOKED, S.ACKNOWLEDGED}
BREACH_REASON = {
    "issued": "triage_delay", "triaged": "notification_delay", "patient_notified": "patient_not_responding",
    "acknowledged": "awaiting_next_step", "confirmatory_test": "awaiting_result", "consultation": "consultation_delay",
    "referral_sent": "referral_response_delay", "referral_rejected": "rerouting_delay",
    "referral_accepted": "booking_delay", "appointment_booked": "appointment_wait",
    "appointment_attended": "care_plan_delay", "treatment_or_surveillance_started": "closure_documentation_delay",
    "no_show": "rebooking_delay", "unreachable": "unreachable_review_delay",
    "declined_pending_review": "decline_review_delay",
}
LEVEL_NAMES = {1: "L1 reminder to owner", 2: "L2 programme coordinator", 3: "L3 clinical lead", 4: "L4 medical director"}


def _supervisor(db: Session, owner: Practitioner) -> Practitioner:
    if owner.supervisor_id:
        sup = db.get(Practitioner, owner.supervisor_id)
        if sup and sup.active:
            return sup
    sup = db.scalar(select(Practitioner).where(Practitioner.role == "coordinator", Practitioner.specialty == "supervisor",
                                               Practitioner.active.is_(True)))
    return sup or owner


def _decision(case: Case, step: StepTask, now: datetime, sla: SlaLookup) -> AlertDecision:
    cfg = get_settings()
    lad = Ladder(sla.reminder_fraction(step.state, case.tier), cfg.clinical_lead_after_breach_fraction,
                 cfg.director_after_fraction)
    # booked appointment: the step is due at the end of the appointment's UAE day (workflow.appointment_wait_due);
    # nothing escalates before then, on either clock (alert_policy WAITING)
    waiting = step.due_at if step.state == S.APPOINTMENT_BOOKED and step.status == "in-progress" else None
    return decide(waiting_until=waiting, tier=case.tier, step_started=step.started_at, step_due=step.due_at, case_created=case.created_at,
                  target_due=case.target_due_at, care_reached=case.care_reached_at, now=now,
                  step_level_alerted=case.escalation_level or 0, target_level_alerted=case.target_alert_level or 0,
                  lad=lad)


def required_level(case: Case, step: StepTask, now: datetime, sla: SlaLookup) -> tuple[int, str, float]:
    d = _decision(case, step, now, sla)
    rag = "none" if d.clock == "none" else "green" if d.clock == "waiting" else ("red" if d.breached else ("amber" if d.level >= 1 else "green"))
    return d.level, rag, d.frac


def run_sweep(db: Session, now: datetime | None = None, actor: Actor = SYSTEM, case_ids: list[int] | None = None) -> dict:
    """Raise escalations per kareshafaa/alert_policy.py: step-clock alerts deduplicated per step; once the pathway
    target is missed, ONE consolidated alert per case per level (attributed to the upstream breach)."""
    now = now or utcnow()
    sla = SlaLookup.load(db)
    q = select(Case).where(Case.state != S.CLOSED)
    if case_ids:
        q = q.where(Case.id.in_(case_ids))
    out = {"checked": 0, "reminders": 0, "supervisor": 0, "clinical_lead": 0, "director": 0, "auto_pooled": 0,
           "breaches": 0, "target_breach_alerts": 0}
    cases = db.scalars(q).all()
    # bulk-load (no per-case queries): in-progress steps for the open cases and the small practitioner table
    sq = select(StepTask).where(StepTask.status == "in-progress")
    if case_ids:
        sq = sq.where(StepTask.case_id.in_(case_ids))
    steps = {s.case_id: s for s in db.scalars(sq)}
    pracs = {p.id: p for p in db.scalars(select(Practitioner))}
    # pass 1 (read-only): find the cases with something new. Nothing is written here, so the audit writer lock (and
    # SQLite's write lock) is not held while every open case is evaluated - it is taken at the first write in pass 2
    # and held only while the changed cases are processed (a 20k-open-case sweep used to hold it for the whole scan,
    # so request audit writes timed out behind it).
    todo = []
    alerts: list[dict] = []  # staff alert e-mails, sent after the case loop (comms/mail.py; off without alert_to)
    for case in cases:
        out["checked"] += 1
        step = steps.get(case.id)
        if step is None:
            continue
        owner = pracs.get(case.owner_id)
        if owner is not None and owner.active:
            d = _decision(case, step, now, sla)
            if not d.new_levels and (not d.breached or step.breached):
                continue  # fast path: nothing new for this case
        todo.append((case, step, owner))
    for case, step, owner in todo:
        if owner is None or not owner.active:
            pool = programme_pool(db, case.programme)
            db.add(OwnershipEvent(case_id=case.id, from_owner_id=case.owner_id, to_owner_id=pool.id, kind="auto_pool",
                                  reason="owner inactive - moved to team pool (unassigned queue)", at=now))
            audit.record(db, actor, "U", "case.auto_pooled", entity_type="Task", entity_id=case.id, case_id=case.id,
                         ts=now, detail={"from_owner": case.owner_id, "to_pool": pool.id})
            case.owner_id = pool.id
            step.owner_id = pool.id
            owner = pool
            out["auto_pooled"] += 1
        d = _decision(case, step, now, sla)
        if d.breached and not step.breached:
            step.breached = True
            out["breaches"] += 1
            if not step.inherited_breach:  # an inherited breach was already recorded once for the case
                reason = "pathway_target_missed" if d.clock == "pathway" else BREACH_REASON.get(step.state, "sla_breach")
                add_lesson(db, case, "overdue", reason, actor, now=now,
                           detail=f"{'pathway target' if d.clock == 'pathway' else 'SLA'} breached in {step.state} "
                                  f"({d.frac:.0%})")
        for lvl in d.new_levels:
            if lvl == 1:
                target = owner
            elif lvl == 2:
                target = _supervisor(db, owner)
            else:
                target = programme_clinical_lead(db, case.programme)
            if lvl == 4:
                target = programme_director(db)
            what = ("pathway target missed (consolidated case alert)" if d.clock == "pathway"
                    else f"{step.state} at {d.frac:.0%} of SLA")
            # critical-result fast lane: from the first breach (level 2 and above) the alert is a PHONE call to the
            # person, not a message in a queue (services/critical.py)
            channel = "phone" if (case.tier == "critical" and lvl >= 2) else "internal"
            esc = Escalation(case_id=case.id, step_task_id=step.id, level=lvl, target_practitioner_id=target.id,
                             created_at=now, kind=d.clock, note=f"{LEVEL_NAMES[lvl]}: {what}"
                             + (" - by phone (critical result)" if channel == "phone" else ""), channel=channel)
            db.add(esc)
            db.add(Communication(case_id=case.id, recipient_practitioner_id=target.id, category="escalation",
                                 channel=channel, content=f"[SIMULATED {'phone escalation' if channel == 'phone' else 'internal alert'}] "
                                 f"{esc.note} - case #{case.id}", status="completed", simulated=True, sent_at=now))
            db.flush()
            audit.record(db, actor, "C", f"escalation.level{lvl}", entity_type="Escalation", entity_id=esc.id,
                         case_id=case.id, ts=now, detail={"target": target.id, "state": step.state, "clock": d.clock,
                                                          "fraction": round(d.frac, 3), "channel": channel})
            alerts.append({"case_id": case.id, "level": lvl, "state": step.state, "clock": d.clock, "note": esc.note,
                           "escalation_id": esc.id, "target": target.name, "tier": case.tier, "synthetic": _is_synthetic_case(case)})
            out[{1: "reminders", 2: "supervisor", 3: "clinical_lead", 4: "director"}[lvl]] += 1
            out["target_breach_alerts"] += int(d.clock == "pathway")
            if lvl == 1 and S(case.state) in PATIENT_FACING and not case.patient.deceased \
                    and consent_status(db, case.patient_id, "care_coordination") != "deny":
                if in_patient_window(now):
                    from ..comms.service import send_with_ladder

                    send_with_ladder(db, case, "reminder", actor, now=now, max_channels=3)
                else:  # queued, sent by the first sweep inside the window (FHIR Communication status on-hold)
                    db.add(Communication(case_id=case.id, patient_id=case.patient_id, category="reminder",
                                         channel="scheduled", content="[SIMULATED] reminder deferred: outside the "
                                         "08:00-21:00 UAE patient message window", status="on-hold",
                                         simulated=True, sent_at=now))
                    out["reminders_deferred"] = out.get("reminders_deferred", 0) + 1
        if d.clock == "pathway":
            case.target_alert_level = max(case.target_alert_level or 0, d.level)
        else:
            case.escalation_level = max(case.escalation_level or 0, d.level)
    db.flush()
    if alerts:
        mailed = email_alerts(db, alerts, now, actor)
        if mailed:
            out["alert_emails"] = mailed
    if in_patient_window(now):
        out["reminders_sent_after_deferral"] = _send_deferred_reminders(db, now, actor, case_ids)
        n_appt = _appointment_reminders(db, now, actor, case_ids)
        if n_appt:
            out["appointment_reminders"] = n_appt
    audit.record(db, actor, "E", "sla.sweep", ts=now, detail=out)
    return out


def _is_synthetic_case(case: Case) -> bool:
    from ..comms.mail import is_synthetic_patient  # noqa: PLC0415

    return is_synthetic_patient(case.patient)


def _alert_body(a: dict) -> str:
    from ..config import get_settings  # noqa: PLC0415

    base = (get_settings().public_base_url or "").rstrip("/")
    lines = [f"Case #{a['case_id']} has a new escalation: {LEVEL_NAMES.get(a['level'], 'L' + str(a['level']))}.", "",
             f"Escalation: {a['note']}", f"Current step: {a['state']}", f"Clock: {a['clock']}", f"Tier: {a['tier']}"]
    if a.get("target"):
        lines.append(f"Alert sent to: {a['target']}")
    if base:
        lines += ["", f"Open the case: {base}/cases/{a['case_id']}"]
    lines += ["", "Acknowledge the escalation on the case page in KareShafaa (sign-in required).",
              "This e-mail holds no patient name or contact details. E-mail is not end-to-end encrypted."]
    return "\n".join(lines)


def email_alerts(db: Session, alerts: list[dict], now: datetime, actor: Actor = SYSTEM) -> int:
    """E-mail staff/operational copies of new escalations to KARESHAFAA_ALERT_TO (from the app mailbox). Never raises:
    the worklist escalation is already recorded; a mail failure is audited (no credentials). Synthetic SYN- cases are
    skipped unless KARESHAFAA_MAIL_ALLOW_SYNTHETIC. At most mail_alerts_per_sweep single alerts; the rest go in one
    summary e-mail."""
    from ..comms import mail  # noqa: PLC0415

    try:
        if not mail.alerts_enabled():
            return 0
        s = get_settings()
        todo = [a for a in alerts if s.mail_allow_synthetic or not a["synthetic"]]
        if not todo:
            return 0
        cap = max(0, int(s.mail_alerts_per_sweep))
        single, rest = todo[:cap], todo[cap:]
        sent = 0
        jobs = [(f"KareShafaa: case #{a['case_id']} escalation L{a['level']}", _alert_body(a), a) for a in single]
        if rest:
            ids = ", ".join(f"#{a['case_id']} L{a['level']}" for a in rest[:200])
            jobs.append((f"KareShafaa: {len(rest)} more escalations", f"{len(rest)} more escalations were raised in "
                         f"this deadline check:\n{ids}\n\nOpen the work queue in KareShafaa to act on them.", None))
        for subject, body, a in jobs:
            try:
                mail.send_email(s.alert_to, subject, body)
                sent += 1
            except mail.MailError as e:
                audit.record(db, actor, "E", "escalation.alert_email_failed", entity_type="Escalation",
                             entity_id=a["escalation_id"] if a else None, case_id=a["case_id"] if a else None, ts=now,
                             detail={"error": str(e)[:200]})
                if "sign-in" in str(e) or "connect" in str(e):
                    break  # wrong password: do not retry for every alert in this sweep
        return sent
    except Exception as e:  # noqa: BLE001 - an e-mail problem must never fail the escalation sweep
        import logging  # noqa: PLC0415

        logging.getLogger("kareshafaa.mail").warning("escalation alert e-mail skipped: %s", type(e).__name__)
        return 0


def remind_appointment(db: Session, case: Case, actor: Actor, now: datetime | None = None) -> dict:
    """One appointment reminder (SIMULATED, patient's language) for the booked appointment of this case."""
    from ..calendar_uae import local_display
    from ..comms.service import send_with_ladder
    from ..models import Appointment
    from .errors import DomainError

    now = now or utcnow()
    appt = db.scalar(select(Appointment).where(Appointment.case_id == case.id, Appointment.status == "booked")
                     .order_by(Appointment.id.desc()).limit(1))
    if appt is None or S(case.state) != S.APPOINTMENT_BOOKED:
        raise DomainError("no booked appointment")
    if appt.start <= now:
        raise DomainError("the appointment time has passed: record attendance or a no-show")
    res = send_with_ladder(db, case, "appointment_reminder", actor, now=now, date=local_display(appt.start), max_channels=3)
    appt.reminder_sent_at = now
    audit.record(db, actor, "C", "appointment.reminder_sent", entity_type="Appointment", entity_id=appt.id,
                 case_id=case.id, ts=now, detail={"start": appt.start.isoformat(), "delivered": res.get("delivered")})
    return res


def _appointment_reminders(db: Session, now: datetime, actor: Actor, case_ids: list[int] | None) -> int:
    from ..clinical_rules import APPOINTMENT_REMINDER_HOURS, APPOINTMENT_REMINDER_MIN_HOURS
    from ..models import Appointment
    from .errors import DomainError

    q = (select(Appointment, Case).join(Case, Case.id == Appointment.case_id)
         .where(Appointment.status == "booked", Appointment.reminder_sent_at.is_(None), Case.state == S.APPOINTMENT_BOOKED,
                Appointment.start > now + timedelta(hours=APPOINTMENT_REMINDER_MIN_HOURS),
                Appointment.start <= now + timedelta(hours=APPOINTMENT_REMINDER_HOURS)))
    if case_ids:
        q = q.where(Case.id.in_(case_ids))
    n = 0
    for appt, case in db.execute(q.limit(500)).all():
        if case.patient.deceased or consent_status(db, case.patient_id, "care_coordination") == "deny":
            appt.reminder_sent_at = now  # handled: no automated message for this person (audited)
            audit.record(db, actor, "E", "appointment.reminder_skipped", entity_type="Appointment", entity_id=appt.id,
                         case_id=case.id, ts=now, detail={"why": "deceased or no consent to automated messages"})
            continue
        try:
            remind_appointment(db, case, actor, now)
            n += 1
        except DomainError as e:
            appt.reminder_sent_at = now
            audit.record(db, actor, "E", "appointment.reminder_skipped", entity_type="Appointment", entity_id=appt.id,
                         case_id=case.id, ts=now, detail={"why": str(e)[:200]})
    return n


def in_patient_window(now_utc: datetime) -> bool:
    """Automated patient messages only between 08:00 and 21:00 UAE time (no 2 a.m. SMS from an SLA job)."""
    from ..calendar_uae import to_local

    start, end = get_settings().patient_message_window
    return start <= to_local(now_utc).hour < end


def _send_deferred_reminders(db: Session, now: datetime, actor: Actor, case_ids: list[int] | None) -> int:
    """Dispatch reminders queued outside the message window (once each); drop them if no longer relevant."""
    from ..comms.service import send_with_ladder

    q = select(Communication).where(Communication.category == "reminder", Communication.status == "on-hold",
                                    Communication.channel == "scheduled")
    if case_ids:
        q = q.where(Communication.case_id.in_(case_ids))
    sent = 0
    for held in db.scalars(q).all():
        case = db.get(Case, held.case_id)
        held.status = "stopped"  # the placeholder itself is never a delivery
        if case is None or S(case.state) not in PATIENT_FACING or case.patient.deceased \
                or consent_status(db, case.patient_id, "care_coordination") == "deny":
            held.content += " - cancelled (case moved on)"
            continue
        held.content += f" - dispatched {now.isoformat(timespec='minutes')} UTC"
        send_with_ladder(db, case, "reminder", actor, now=now, max_channels=3)
        sent += 1
    return sent


def acknowledge_escalation(db: Session, esc: Escalation, actor: Actor, note: str, now: datetime | None = None) -> Escalation:
    from .errors import DomainError, Forbidden

    from ..signoff import require_signoff

    require_signoff(actor, "escalation.ack", note)
    if esc.acknowledged_at:
        raise DomainError("already acknowledged")
    esc.acknowledged_at = now or utcnow()
    esc.acknowledged_by_user_id = actor.user_id
    esc.note = (esc.note or "") + f" | ack: {note}"
    audit.record(db, actor, "U", "escalation.acknowledged", entity_type="Escalation", entity_id=esc.id,
                 case_id=esc.case_id, ts=esc.acknowledged_at)
    return esc
