"""One-click demo scenarios (demo mode only). Each creates a NEW synthetic patient and drives the case,
through the real service layer and guards, to the start of a story from the KareShafaa demo script."""
from __future__ import annotations

import random
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .comms.service import record_call, send_with_ladder, submit_teach_back
from .models import Case, Organization, Patient, Practitioner, User, utcnow
from .security import SYSTEM, Actor
from .services import workflow as wf
from .services.errors import Forbidden, NotFound
from .services.escalation import run_sweep
from .services.ingest import ingest, parse_hl7v2
from .statemachine import S
from .synth.cohort import SynthPatient
from .synth.history import actor_from_user, add_patient, patient_actor

CORRECT = {"q1": "a", "q2": "b", "q3": "a"}

SCENARIOS: dict[str, dict] = {
    "fatima_rejected_referral": {
        "title": "Fatima, 52, Arabic, Al Ain - positive FIT; hospital rejects the referral (capacity)",
        "requirements": "R1 visibility · R3 Arabic + teach-back · R4 cross-provider · R6",
        "login": "aisha.rahman",
        "next": ["Open the case: HL7 ORU^R01 ingest, Arabic notification, teach-back passed are on the timeline",
                 "It sits in the 'Rejected' queue with reason 'capacity'",
                 "Log in as Dr. Omar Haddad (GP) and re-send the referral to Oasis Teaching Hospital",
                 "Log in as the specialist there, accept, book, record attendance and a care plan, close with evidence"],
    },
    "maria_urgent_escalation": {
        "title": "Maria, 46, Tagalog speaker - BI-RADS 4 (T1); digital channels fail; SLA breach escalates",
        "requirements": "R2 ownership/SLA · R5 escalation · R3 channel fallback · closure guard",
        "login": "fatima.al.mansoori",
        "next": ["Digital ladder failed (no smartphone) - a navigator phone task is at the top of the queue",
                 "The clock was fast-forwarded for this case: L1 -> L2 -> L3 -> L4 escalations are on the timeline",
                 "Try 'Close case' without evidence: it is blocked by the closure guard",
                 "Record the call (reached), then continue the pathway"],
    },
    "ramesh_transfer": {
        "title": "Ramesh, 58, Hindi - HbA1c 8.1% confirmed; moves to another clinic (two-key transfer)",
        "requirements": "R2 one accountable owner · R4 transfer · R1",
        "login": "yousef.al.marzouqi",
        "next": ["The current owner stays accountable while the transfer is pending",
                 "As Dr. Yousef Al Marzouqi (receiving GP) accept the transfer - ownership flips, audit logged",
                 "Record the GP visit care plan and close with evidence"],
    },
    "unreachable_ladder": {
        "title": "Unreachable patient - digital ladder and two navigator calls failed",
        "requirements": "R5 safe escalation · authorised human closure",
        "login": "omar.haddad",
        "next": ["The case is in 'Unreachable' after the full contact ladder",
                 "As the GP, sign off an authorised closure 'unable to contact' with a rationale, or re-try contact"],
    },
    "ramadan_no_show": {
        "title": "No-show during Ramadan clinic hours - barrier captured, rebook",
        "requirements": "R5 exceptions · R6 lessons learned (barrier codes feed proposals)",
        "login": "aisha.rahman",
        "next": ["The appointment was a no-show with barrier 'ramadan_hours'; a simulated 'we missed you' message went out",
                 "Rebook the appointment; the lesson feeds the governance proposals"],
    },
}


def _actor(db: Session, username: str) -> Actor:
    u = db.scalar(select(User).where(User.username == username))
    if u is None:
        raise NotFound(f"demo user {username} missing - re-seed")
    return actor_from_user(u)


def _patient(db: Session, rng: random.Random, given: str, family: str, nat: str, lang: str, gender: str, age: int,
             programme: str, region: str, org_name: str, smart: bool = True, phone: bool = True) -> Patient:
    n = db.scalar(select(Patient.id).order_by(Patient.id.desc()).limit(1)) or 0
    sp = SynthPatient(idx=500000 + n + 1, given=given, family=family, gender=gender, age=age, nationality=nat, language=lang,
                      region=region, phone_valid=phone, has_smartphone=smart, whatsapp=smart, alt_contact=False,
                      prior_no_shows=0, prior_screens=1, responsiveness=0.9, programme=programme, share_consent=True,
                      analytics_consent=True)
    orgs = db.scalars(select(Organization).where(Organization.kind == "primary_care")).all()
    org = next(o for o in orgs if org_name in o.name)
    gps = db.scalars(select(Practitioner).where(Practitioner.org_id == org.id, Practitioner.role == "gp",
                                                Practitioner.active.is_(True))).all()
    d = {"phc": [org], "gps": {org.id: gps}}
    p = add_patient(db, sp, d, rng, utcnow())
    p.mrn = f"SYN-DEMO-{p.id}"
    return p


def run_scenario(db: Session, key: str, actor: Actor) -> dict:
    from .config import get_settings

    cfg = get_settings()
    if cfg.env == "prod" or not cfg.demo_logins:
        raise Forbidden("the guided walkthrough is switched off on this server")
    if key not in SCENARIOS:
        raise NotFound("unknown scenario")
    if not actor.is_human or actor.role not in ("coordinator", "clinical_lead", "admin", "gp"):
        raise Forbidden("the guided walkthrough can be started by staff roles")
    rng = random.Random()
    now = utcnow()
    t = now - timedelta(hours=6)
    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    case: Case
    if key == "fatima_rejected_referral":
        p = _patient(db, rng, "Fatima", "Al Kaabi", "Emirati", "ar", "female", 52, "colorectal_fit", "Al Ain", "Al Ain Central")
        p.gp_id = db.scalar(select(Practitioner.id).where(Practitioner.name.like("Dr. Meera Pillai%")))
        db.flush()
        msg = ("MSH|^~\\&|LIS|CENTRAL_SCREENING_LAB|KARESHAFAA|PROGRAMME|" + t.strftime("%Y%m%d%H%M%S") +
               f"||ORU^R01^ORU_R01|DEMO-{p.id}-{rng.randint(1000, 9999)}|P|2.5.1\r"
               f"PID|1||{p.mrn}^^^KARESHAFAA^MR||AL KAABI^FATIMA||{utcnow().year - 52}0301|F\r"
               "OBX|1|NM|29771-3^FIT haemoglobin^LN||260|ug/g|<120|H|||F")
        im = ingest(db, "hl7v2", parse_hl7v2(msg), _actor(db, "aisha.rahman"), now=t)
        case = db.get(Case, im.case_id)
        send_with_ladder(db, case, "result_notification", SYSTEM, now=t + timedelta(minutes=30))
        if S(case.state) != S.PATIENT_NOTIFIED:
            record_call(db, case, _actor(db, "aisha.rahman"), "reached", now=t + timedelta(hours=1))
        submit_teach_back(db, case, patient_actor(p), CORRECT, confidence=4, language="ar", now=t + timedelta(hours=2))
        gp = _actor(db, "meera.pillai")
        wf.transition(db, case, S.CONSULTATION, gp, note="GP reviewed result; colonoscopy indicated", now=t + timedelta(hours=3))
        hosp = db.scalar(select(Organization).where(Organization.name.like("Capital General%")))
        wf.create_referral(db, case, gp, hosp.id, summary="Positive FIT 260 ug/g; no red-flag symptoms; on no anticoagulants; "
                           "Arabic speaker; prefers morning appointments.", now=t + timedelta(hours=3, minutes=10))
        wf.decide_referral(db, case, _actor(db, "vikram.mehta"), False, reason_code="capacity",
                           note="No colonoscopy slots within 6 weeks; suggest Oasis Teaching Hospital.", now=t + timedelta(hours=5))
    elif key == "maria_urgent_escalation":
        p = _patient(db, rng, "Maria", "Santos", "Filipino", "tl", "female", 46, "breast", "Abu Dhabi", "Mussafah", smart=False)
        _, case = wf.ingest_result(db, p, "breast", value=None, value_text="BI-RADS 4", performer_org_id=lab.id, actor=SYSTEM,
                                   now=now - timedelta(hours=60))
        wf.transition(db, case, S.TRIAGED, _actor(db, "imran.siddiqui"), note="Duty clinician confirmed T1 (BI-RADS 4)",
                      now=now - timedelta(hours=59))
        # story: WhatsApp not delivered and SMS gateway failure -> navigator phone task (number valid for voice)
        p.phone_valid = False
        send_with_ladder(db, case, "result_notification", SYSTEM, now=now - timedelta(hours=58))
        p.phone_valid = True
        run_sweep(db, now=now, case_ids=[case.id])  # 59 h in 'triaged' vs a 24 h T1 clock -> L1..L4
    elif key == "ramesh_transfer":
        p = _patient(db, rng, "Ramesh", "Kumar", "Indian", "hi", "male", 58, "diabetes_hba1c", "Abu Dhabi", "Corniche")
        _, case = wf.ingest_result(db, p, "diabetes_hba1c", value=8.1, value_text=None, unit="%", performer_org_id=lab.id, now=t)
        wf.transition(db, case, S.TRIAGED, SYSTEM, now=t + timedelta(minutes=5))
        send_with_ladder(db, case, "result_notification", SYSTEM, now=t + timedelta(minutes=30))
        if S(case.state) != S.PATIENT_NOTIFIED:
            record_call(db, case, _actor(db, "aisha.rahman"), "reached", now=t + timedelta(hours=1))
        submit_teach_back(db, case, patient_actor(p), CORRECT, confidence=4, language="hi", now=t + timedelta(hours=2))
        gp = _actor(db, "omar.haddad")
        wf.order_confirmatory(db, case, gp, lab.id, now=t + timedelta(hours=3))
        wf.record_confirmatory_result(db, case, gp, "positive", now=t + timedelta(hours=4))
        to = db.scalar(select(Practitioner).where(Practitioner.name.like("Dr. Yousef Al Marzouqi%")))
        wf.request_transfer(db, case, to.id, gp, "Patient moved to Khalifa City (new insurer network). Repeat HbA1c 8.0% "
                            "confirms diabetes; needs GP visit and care plan.", now=t + timedelta(hours=5))
    elif key == "unreachable_ladder":
        p = _patient(db, rng, "Suresh", "Nair", "Indian", "ml", "male", 47, "colorectal_fit", "Abu Dhabi", "Corniche",
                     smart=False, phone=True)
        _, case = wf.ingest_result(db, p, "colorectal_fit", value=180, value_text=None, unit="ug/g", performer_org_id=lab.id,
                                   now=now - timedelta(days=4))
        wf.transition(db, case, S.TRIAGED, SYSTEM, now=now - timedelta(days=4))
        p.whatsapp = False
        p.phone_valid = False
        send_with_ladder(db, case, "result_notification", SYSTEM, now=now - timedelta(days=4))
        co = _actor(db, "aisha.rahman")
        record_call(db, case, co, "no_answer", note="morning call", now=now - timedelta(days=3))
        record_call(db, case, co, "wrong_number", note="evening call: number belongs to someone else", now=now - timedelta(days=2))
        wf.transition(db, case, S.UNREACHABLE, co, note="digital ladder + 2 calls failed; GP informed (simulated letter)",
                      now=now - timedelta(days=2))
    else:  # ramadan_no_show
        p = _patient(db, rng, "Bilal", "Hussain", "Pakistani", "ur", "male", 55, "colorectal_fit", "Abu Dhabi", "Khalifa City")
        t0 = now - timedelta(days=12)
        _, case = wf.ingest_result(db, p, "colorectal_fit", value=310, value_text=None, unit="ug/g", performer_org_id=lab.id, now=t0)
        wf.transition(db, case, S.TRIAGED, SYSTEM, now=t0)
        send_with_ladder(db, case, "result_notification", SYSTEM, now=t0 + timedelta(hours=1))
        if S(case.state) != S.PATIENT_NOTIFIED:
            record_call(db, case, _actor(db, "aisha.rahman"), "reached", now=t0 + timedelta(hours=2))
        submit_teach_back(db, case, patient_actor(p), CORRECT, confidence=4, language="ur", now=t0 + timedelta(hours=3))
        gp = _actor(db, "yousef.al.marzouqi")
        wf.transition(db, case, S.CONSULTATION, gp, now=t0 + timedelta(days=1))
        hosp = db.scalar(select(Organization).where(Organization.name.like("Capital General%")))
        wf.create_referral(db, case, gp, hosp.id, summary="Positive FIT 310 ug/g; fit for colonoscopy; Urdu speaker; "
                           "fasting during Ramadan - prefers late-morning slots.", now=t0 + timedelta(days=1, hours=1))
        sp = _actor(db, "vikram.mehta")
        wf.decide_referral(db, case, sp, True, now=t0 + timedelta(days=2))
        wf.book_appointment(db, case, sp, now - timedelta(days=1), now=t0 + timedelta(days=3),
                            beyond_target_reason="earliest available slot (specialist capacity)")
        wf.record_attendance(db, case, sp, False, barrier_code="ramadan_hours", now=now - timedelta(hours=20))
        send_with_ladder(db, case, "no_show", SYSTEM, now=now - timedelta(hours=19), max_channels=3)
    audit.record(db, actor, "C", "demo.scenario_started", entity_type="Task", entity_id=case.id, case_id=case.id,
                 detail={"scenario": key, "synthetic": True})
    db.flush()
    sc = SCENARIOS[key]
    return {"ok": True, "scenario": key, "case_id": case.id, "state": case.state, "title": sc["title"],
            "login": sc["login"], "next": sc["next"]}
