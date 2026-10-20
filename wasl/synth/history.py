"""Replays a synthetic 'with Wasl' operating history through the REAL service layer.

Every synthetic event (result, triage, message, call, teach-back, referral, appointment, closure)
is executed by the same guarded workflow functions the API uses, back-dated in time, so the seeded
database obeys the same state machine, ownership, closure guard and audit rules as live use.
Probabilities are ASSUMPTIONS; outputs are SIMULATED.
"""
from __future__ import annotations

import math
import random
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from ..comms.content import SUPPORTED_LANGUAGES
from ..comms.service import record_call, send_with_ladder, submit_teach_back
from ..models import Case, Consent, Patient, User
from ..calendar_uae import is_ramadan, is_working_day, next_working_slot
from ..programmes import CLINICIAN_CONFIRMED_TIERS, PROGRAMMES
from ..security import SYSTEM, Actor
from ..services import workflow as wf
from ..statemachine import S
from .cohort import SynthPatient, risk_multiplier

TIER_F = {"critical": 0.2, "urgent": 0.4, "high": 0.8, "routine": 1.3}
TB_FAIL = {"en": 0.10, "ar": 0.12, "hi": 0.30, "ur": 0.32}
CARE_TEXT = {  # (treatment, surveillance) - illustrative pathway outcomes
    "colorectal_fit": ("Colonoscopy: lesion biopsied; colorectal MDT referral and treatment plan agreed.",
                       "Colonoscopy: adenomas removed (polypectomy); surveillance colonoscopy in 3 years."),
    "cervical": ("Colposcopy + biopsy CIN2/3: LLETZ excision treatment booked.",
                 "Colposcopy: CIN1 / no lesion; repeat HPV co-test in 12 months."),
    "breast": ("Image-guided core biopsy: malignant; breast MDT treatment plan agreed.",
               "Core biopsy benign (concordant); return to routine screening with 12-month imaging."),
    "diabetes_hba1c": ("Endocrinology: diabetes treatment intensified; education and retinal screening booked.",
                       "Endocrinology review: GP-led diabetes plan with 3-monthly HbA1c."),
}
CONFIRM_POSITIVE = {"cervical": 0.45, "breast": 0.30, "diabetes_hba1c": 0.75}


class Stop(Exception):
    """Simulated time has reached 'now': the case stays open in its current state."""


def actor_from_user(u: User) -> Actor:
    return Actor(user_id=u.id, role=u.role, display=u.display_name, practitioner_id=u.practitioner_id,
                 org_id=u.practitioner.org_id if u.practitioner else None)


def patient_actor(p: Patient) -> Actor:
    return Actor(user_id=None, role="patient", display=f"Patient {p.mrn}", patient_id=p.id)


def screening_value(rng: random.Random, programme: str) -> tuple[float | None, str | None]:
    if programme == "colorectal_fit":
        return round(rng.choice([rng.uniform(120, 399), rng.uniform(120, 399), rng.uniform(400, 1200)]), 0), None
    if programme == "cervical":
        return None, rng.choices(["HPV16", "HPV18", "HPV other hr"], weights=[30, 15, 55])[0]
    if programme == "breast":
        return None, rng.choices(["BI-RADS 0", "BI-RADS 4", "BI-RADS 5"], weights=[70, 24, 6])[0]
    r = rng.random()
    if r < 0.02:
        return round(rng.uniform(15, 17.5), 1), None  # T0: very high HbA1c (hyperglycaemic crisis risk)
    return round(rng.uniform(10, 13.5) if r < 0.16 else rng.uniform(6.5, 9.9), 1), None


class Replayer:
    def __init__(self, db: Session, rng: random.Random, d: dict, users_by_pid: dict[int, User], now: datetime):
        self.db, self.rng, self.d, self.now = db, rng, d, now
        self.ua = {pid: actor_from_user(u) for pid, u in users_by_pid.items()}

    # helpers --------------------------------------------------------------
    def ln(self, median_h: float, spread: float = 0.6) -> float:
        return max(0.05, self.rng.lognormvariate(math.log(max(median_h, 0.05)), spread))

    def adv(self, hours: float) -> datetime:
        self.t += timedelta(hours=hours)
        if self.t > self.now:
            raise Stop
        return self.t

    def staff(self, case: Case) -> datetime:
        """Routine staff work happens on UAE working days in clinic hours; T0/T1 use the on-call roster."""
        if case.tier not in CLINICIAN_CONFIRMED_TIERS:
            self.t = next_working_slot(self.t, hour=self.rng.randint(8, 14))
            if self.t > self.now:
                raise Stop
        return self.t

    def patient_delay(self) -> float:
        """Patients respond more slowly during Ramadan and public holidays (assumption)."""
        d = self.t.date()
        return 1.35 if is_ramadan(d) else 1.2 if not is_working_day(d) else 1.0

    def a(self, practitioner) -> Actor:
        return self.ua[practitioner.id]

    def coord(self, case: Case) -> Actor:
        return self.a(self.rng.choice(self.d["coordinators"][case.programme]))

    def gp(self, case: Case) -> Actor:
        gp = self.db.get(type(case.owner), case.patient.gp_id)
        return self.ua[gp.id]

    # replay ---------------------------------------------------------------
    def run(self, patient: Patient, sp: SynthPatient, t0: datetime) -> Case | None:
        self.t = t0
        value, text = screening_value(self.rng, sp.programme)
        _, case = wf.ingest_result(self.db, patient, sp.programme, value=value, value_text=text,
                                   performer_org_id=self.d["lab"].id, now=t0)
        if case is None:
            return None
        try:
            self._journey(case, patient, sp)
            if S(case.state) != S.CLOSED:
                self._resolve(case)
        except Stop:
            pass
        return case

    def _resolve(self, case: Case) -> None:
        """A stalled case is escalated; most are later resolved by a human (simulated follow-up)."""
        db, rng = self.db, self.rng
        self.adv(self.ln(24 * 30))
        if rng.random() < 0.15:
            return  # remains open and overdue (visible in the work queue)
        lead = self.a(self.d["leads"][case.programme])
        st = S(case.state)
        if st == S.TREATMENT_STARTED:
            wf.close_case(db, case, lead, "evidence", evidence_type="care_plan", note="Late closure documentation.", now=self.t)
        elif st == S.CONFIRMATORY_TEST and wf._latest(db, wf.ServiceRequest, case.id, kind="confirmatory_test",
                                                        result="negative"):
            wf.close_case(db, case, self.gp(case), "evidence", evidence_type="negative_confirmatory_result",
                          note="Negative result documented after escalation.", now=self.t)
        elif st == S.DECLINED_REVIEW:
            wf.close_case(db, case, self.gp(case), "authorised_decision", reason="informed_decline",
                          note="Informed decline confirmed by phone after escalation.", now=self.t)
        elif st in (S.PATIENT_NOTIFIED, S.CONFIRMATORY_TEST, S.NO_SHOW, S.UNREACHABLE, S.ACKNOWLEDGED):
            if st != S.UNREACHABLE:
                for _ in range(2):
                    self.adv(self.ln(30))
                    record_call(db, case, self.coord(case), "no_answer", now=self.t)
            wf.close_case(db, case, lead, "authorised_decision", reason="unable_to_contact",
                          note="Escalated to clinical lead; full contact ladder exhausted; GP informed.", now=self.t)
        elif st in (S.CONSULTATION, S.REFERRAL_REJECTED):
            wf.close_case(db, case, lead, "authorised_decision", reason="care_elsewhere_verified",
                          note="Patient chose another provider; care verified by coordinator.", now=self.t)

    def _journey(self, case: Case, patient: Patient, sp: SynthPatient) -> None:
        db, rng, rm, tf = self.db, self.rng, risk_multiplier(sp), TIER_F[case.tier]
        lead = self.a(self.d["leads"][case.programme])
        # triage: rules engine for high/routine, clinician confirmation for urgent
        clin = case.tier in CLINICIAN_CONFIRMED_TIERS
        self.adv(self.ln(0.5 if case.tier == "critical" else 2.0 if clin else 0.3))
        wf.transition(db, case, S.TRIAGED, self.gp(case) if clin else SYSTEM, now=self.t)
        # notification via channel ladder, then phone-call tasks
        self.adv(self.ln(3))
        send_with_ladder(db, case, "result_notification", SYSTEM, now=self.t)
        calls = 0
        while S(case.state) == S.TRIAGED:
            self.adv(self.ln(20 * tf + 6))
            reached = sp.phone_valid and rng.random() < 0.35 + 0.5 * sp.responsiveness
            record_call(db, case, self.coord(case), "reached" if reached else ("no_answer" if sp.phone_valid else "wrong_number"),
                        now=self.t)
            calls += 1
            if not reached and calls >= 3:
                wf.transition(db, case, S.UNREACHABLE, self.coord(case), note="3 calls failed after digital ladder", now=self.t)
                self.adv(self.ln(24 * 10))
                if sp.alt_contact and rng.random() < 0.5:
                    record_call(db, case, self.coord(case), "reached", note="reached via alternate contact", now=self.t)
                elif rng.random() < 0.8:
                    wf.close_case(db, case, lead, "authorised_decision", reason="unable_to_contact",
                                  note="Full contact ladder exhausted; GP informed; letter sent (simulated).", now=self.t)
                    return
                else:
                    return
        # patient response: teach-back, decline or silence
        self.adv(self.ln(30 * rm * self.patient_delay()))
        if rng.random() < min(0.5, 0.06 * rm ** 1.5):  # never responds in-app
            if rng.random() < 0.5:
                return  # stays in patient_notified -> overdue -> escalations
            self.adv(self.ln(72))
            record_call(db, case, self.coord(case), "reached", now=self.t)
            submit_teach_back(db, case, self.coord(case), {"q1": "a", "q2": "b", "q3": "a"}, confidence=3,
                              language="en", now=self.t)
        elif rng.random() < 0.03 * rm:
            wf.record_decline(db, case, patient_actor(patient), "Patient prefers not to have further tests now.", now=self.t)
            self.adv(self.ln(72))
            if rng.random() < 0.8:
                wf.close_case(db, case, self.gp(case), "authorised_decision", reason="informed_decline",
                              note="Informed decline: risks and alternatives explained; re-offer at next round.", now=self.t)
            return
        else:
            lang = sp.language if sp.language in SUPPORTED_LANGUAGES else "en"
            fail_p = TB_FAIL.get(sp.language, 0.42) * (0.7 + 0.6 * (1 - sp.responsiveness))
            passed = rng.random() >= fail_p
            ans = {"q1": "a", "q2": "b", "q3": "a"} if passed else rng.choice(
                [{"q1": "b", "q2": "b", "q3": "a"}, {"q1": "a", "q2": "a", "q3": "a"}, {"q1": "a", "q2": "b", "q3": "c"}])
            submit_teach_back(db, case, patient_actor(patient), ans, confidence=rng.choice([4, 5] if passed else [2, 3]),
                              language=lang, now=self.t)
            if not passed:
                self.adv(self.ln(24))
                record_call(db, case, self.coord(case), "reached", note="navigator explained in patient's language", now=self.t)
                if rng.random() < 0.9:
                    submit_teach_back(db, case, self.coord(case), {"q1": "a", "q2": "b", "q3": "a"}, confidence=4,
                                      language=lang, now=self.t)
                else:
                    return
        if S(case.state) != S.ACKNOWLEDGED:
            return
        prog = PROGRAMMES[case.programme]
        gp = self.gp(case)
        if case.needs_confirmation:
            self.adv(self.ln(48 * tf))
            self.staff(case)
            wf.order_confirmatory(db, case, gp, self.d["lab"].id, now=self.t)
            self.adv(self.ln(24 * 7 * tf * math.sqrt(rm)))
            if rng.random() < 0.05 * rm:
                return  # did not attend confirmatory test -> open
            self.staff(case)
            pos = rng.random() < CONFIRM_POSITIVE[case.programme]
            wf.record_confirmatory_result(db, case, gp, "positive" if pos else "negative", now=self.t)
            if not pos:
                self.adv(self.ln(48))
                if rng.random() < 0.9:
                    wf.close_case(db, case, gp, "evidence", evidence_type="negative_confirmatory_result",
                                  note="Negative confirmatory result; return to routine screening.", now=self.t)
                return
        else:
            self.adv(self.ln(48 * tf))
            self.staff(case)
            wf.transition(db, case, S.CONSULTATION, gp, note="GP consultation completed", now=self.t)
        if case.programme == "diabetes_hba1c" and not (case.tier in ("critical", "high") and rng.random() < 0.5):
            self.adv(self.ln(24 * 5 * tf))
            self.staff(case)
            wf.start_care(db, case, gp, "treatment", "GP visit: diabetes confirmed; education, lifestyle plan, metformin review, "
                          "annual eye/foot/kidney checks scheduled.",
                          now=self.t)
            self.adv(self.ln(72))
            if rng.random() < 0.92:
                wf.close_case(db, case, gp, "evidence", evidence_type="care_plan", note="Care plan active.", now=self.t)
            return
        self._referral(case, patient, sp, gp, rm, tf)

    def _hospital_for(self, region: str, exclude: int | None = None):
        hs = [h for h in self.d["hosp"] if h.id != exclude]
        local = [h for h in hs if h.region == region]
        return local[0] if local and self.rng.random() < 0.8 else self.rng.choice(hs)

    def _referral(self, case: Case, patient: Patient, sp: SynthPatient, gp: Actor, rm: float, tf: float) -> None:
        db, rng = self.db, self.rng
        spec_key = PROGRAMMES[case.programme].specialty
        self.adv(self.ln(24 * 2 * tf))
        if wf.consent_status(db, patient.id, "share_external") == "deny":
            if rng.random() < 0.85:
                wf.record_consent(db, patient, self.coord(case), "share_external", "permit",
                                  note="Patient agreed to share referral with hospital after discussion.", now=self.t)
                self.adv(self.ln(24))
            else:
                return
        hosp = self._hospital_for(patient.managing_org.region)
        for attempt in range(2):
            spec = self.d["specialists"][(hosp.id, spec_key)]
            summary = "Screen-positive result; patient informed and teach-back passed; please assess." if attempt == 0 else \
                "Re-referral with full structured summary: result, history, medications, allergies, preferred language."
            wf.create_referral(db, case, gp if attempt == 0 else self.coord(case), hosp.id, performer_id=spec.id,
                               summary=summary, now=self.t)
            capacity = 2.5 if "Capital" in hosp.name else 1.0
            self.adv(self.ln(24 * tf * capacity))
            self.staff(case)
            sa = self.a(spec)
            p_rej = 0.28 if "Western" in hosp.name else 0.10
            if attempt == 0 and rng.random() < p_rej:
                reason = "incomplete_info" if "Western" in hosp.name and rng.random() < 0.7 else \
                    rng.choices(["capacity", "incomplete_info", "wrong_service", "out_of_network"], weights=[45, 25, 15, 15])[0]
                wf.decide_referral(db, case, sa, False, reason_code=reason, note=f"Rejected: {reason.replace('_', ' ')}",
                                   now=self.t)
                self.adv(self.ln(24 * 2))
                if rng.random() > 0.85:
                    return  # rejected referral left unresolved
                hosp = self._hospital_for("Abu Dhabi", exclude=hosp.id)
                continue
            wf.decide_referral(db, case, sa, True, performer_id=spec.id, now=self.t)
            break
        spec_actor = self.a(self.d["specialists"][(hosp.id, spec_key)])
        self.adv(self.ln(24 * 3 * tf))
        for visit in range(3):
            wait_days = {"critical": 2, "urgent": 6, "high": 14, "routine": 28}[case.tier] * self.rng.uniform(0.6, 1.6)
            start = next_working_slot(self.t + timedelta(days=wait_days), hour=self.rng.randint(8, 15))
            wf.book_appointment(db, case, spec_actor if visit == 0 else self.coord(case), start, now=self.t)
            self.t = start
            if self.t > self.now:
                raise Stop
            p_dna = 0.07 * rm * (1.6 if is_ramadan(start.date()) else 1.0) * (1 + 0.25 * sp.prior_no_shows)
            if rng.random() < min(0.6, p_dna):
                wf.record_attendance(db, case, spec_actor, False, barrier_code=self._barrier(sp, case), now=self.t)
                self.adv(self.ln(48))
                if rng.random() > 0.75:
                    return  # no-show not rebooked
                continue
            wf.record_attendance(db, case, spec_actor, True, now=self.t)
            break
        else:
            return
        self.adv(self.ln(24 * 5 * tf))
        self.staff(case)
        kind = "treatment" if rng.random() < 0.35 else "surveillance"
        wf.start_care(db, case, spec_actor, kind, CARE_TEXT[case.programme][0 if kind == "treatment" else 1], now=self.t)
        self.adv(self.ln(48))
        if rng.random() < 0.93:
            wf.close_case(db, case, spec_actor, "evidence", evidence_type="care_plan", note="Care plan documented.", now=self.t)

    def _barrier(self, sp: SynthPatient, case: Case) -> str:
        r = self.rng.random()
        if is_ramadan(self.t.date()) and r < 0.35:
            return "ramadan_hours"
        if sp.region == "Al Dhafra" and r < 0.5:
            return "transport"
        if sp.age < 40 and r < 0.4:
            return "work"
        if case.programme == "colorectal_fit" and r < 0.35:
            return "fear_anxiety"
        if sp.language in ("tl", "ml") and r < 0.3:
            return "language"
        return self.rng.choice(["forgot", "childcare", "cost", "unknown", "work", "transport"])


def add_patient(db: Session, sp: SynthPatient, d: dict, rng: random.Random, now: datetime) -> Patient:
    phcs = [o for o in d["phc"] if o.region == sp.region] or d["phc"]
    org = rng.choice(phcs)
    gp = rng.choice(d["gps"][org.id])
    p = Patient(mrn=f"SYN-{100000 + sp.idx}", given_name=sp.given, family_name=sp.family, gender=sp.gender,
                birth_year=now.year - sp.age, age_band=sp.age_band, nationality_group=sp.nationality, language=sp.language,
                region=sp.region, phone_valid=sp.phone_valid, has_smartphone=sp.has_smartphone, whatsapp=sp.whatsapp,
                alt_contact=sp.alt_contact, prior_no_shows=sp.prior_no_shows, prior_screens=sp.prior_screens,
                responsiveness=sp.responsiveness, managing_org_id=org.id, gp_id=gp.id)
    p.managing_org = org
    db.add(p)
    db.flush()
    db.add(Consent(patient_id=p.id, scope="care_coordination", decision="permit", recorded_at=now - timedelta(days=400),
                   note="Programme enrolment (synthetic)"))
    if not sp.share_consent:
        db.add(Consent(patient_id=p.id, scope="share_external", decision="deny", recorded_at=now - timedelta(days=400)))
    if not sp.analytics_consent:
        db.add(Consent(patient_id=p.id, scope="analytics", decision="deny", recorded_at=now - timedelta(days=400)))
    if rng.random() < 0.04:
        db.add(Consent(patient_id=p.id, scope="comm_whatsapp", decision="deny", recorded_at=now - timedelta(days=400)))
    return p
