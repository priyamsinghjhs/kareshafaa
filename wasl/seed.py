"""Build a fresh synthetic database: directory, ~2,000-patient cohort with replayed history,
demo patients, edge cases, SLA sweep, model v1 (approved) + v2 candidate, proposals, simulation.

Usage: python -m wasl.seed [--patients N] [--seed S]
"""
from __future__ import annotations

import argparse
import random
import time
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from . import audit
from .comms.service import record_call, send_with_ladder
from .config import get_settings
from .db import init_db, session_scope
from .ml import registry
from .models import Case, ConsultRequest, Patient, Practitioner, SimulationRun, User, utcnow
from .security import SYSTEM, Actor
from .services import workflow as wf
from .services.configstore import DEFAULT_CHANNEL_ORDER, set_config
from .services.escalation import run_sweep
from .services.improve import generate_proposals
from .sla import seed_sla_rules
from .statemachine import S
from .synth.cohort import SynthPatient, make_cohort
from .synth.directory import build_directory, build_users
from .synth.history import Replayer, actor_from_user, add_patient
from .synth.simulator import run_simulation

SEED_ACTOR = Actor(user_id=None, role="system", display="Synthetic data seeder", is_human=False)


def seed(n_patients: int | None = None, seed_value: int | None = None, *, train: bool = True, verbose: bool = True,
         realistic: bool = False, simulate: bool = True) -> dict:
    """realistic=False (demo default): every synthetic result is actionable so the queue is full of work.
    realistic=True (seed-large): population positivity rates, negatives, data defects, duplicates and late
    results at the calibrated rates in docs/synthetic_data.md."""
    cfg = get_settings()
    n = n_patients or cfg.n_patients
    sv = seed_value or cfg.seed
    t_start = time.time()
    init_db(drop=True)
    if train:  # never touch model files when seeding a separate (e.g. large) database without training
        for f in cfg.model_dir.glob("*.joblib"):
            f.unlink()
    rng = random.Random(sv)
    now = utcnow()
    stats: dict = {}
    with session_scope() as db:
        seed_sla_rules(db)
        set_config(db, "channel_order:default", DEFAULT_CHANNEL_ORDER, "default")
        d = build_directory(db)
        users = build_users(db, d)
        users_by_pid = {u.practitioner_id: u for u in users if u.practitioner_id}
        _demo_content_signoffs(db, d, users_by_pid)
        audit.record(db, SEED_ACTOR, "C", "seed.started", detail={"n_patients": n, "seed": sv, "synthetic": True})
        rep = Replayer(db, rng, d, users_by_pid, now, enriched=not realistic)
        cohort = make_cohort(n, sv, min_age=18 if realistic else 25)
        for i, sp in enumerate(cohort):
            t0 = now - timedelta(days=rng.uniform(0.5, 300))
            p = add_patient(db, sp, d, rng, now, age_at=t0)  # sp.age is the age when screened
            rep.run(p, sp, t0)
            if i % 100 == 99:
                db.commit()
                if verbose:
                    print(f"  replayed {i + 1}/{n} synthetic patients ({time.time() - t_start:.0f}s)", flush=True)
        db.commit()
        demo = _demo_patients(db, d, rng, now, users_by_pid)
        _edge_cases(db, d, rng, now, users_by_pid)
        db.commit()
        sweep = run_sweep(db, now=now)
        stats["sweep"] = sweep
        db.commit()
        if train:
            lead_user = users_by_pid[d["leads"]["colorectal_fit"].id]
            lead = actor_from_user(lead_user)
            admin = db.scalar(select(User).where(User.username == "admin"))
            admin_actor = Actor(user_id=admin.id, role="admin", display=admin.display_name)
            v1 = registry.train_candidate(db, admin_actor, "logreg", now=now)
            stats["v1"] = {"status": v1.status, "auc": v1.metrics["auc"], "ece": v1.metrics["ece"]}
            if v1.status == "awaiting_approval":
                registry.approve(db, v1.version, lead, "Seed bootstrap: initial outreach-priority model reviewed (demo).",
                                 now=now)
                stats["v1"]["status"] = v1.status
            db.commit()
            v2 = registry.train_candidate(db, admin_actor, "gbm", now=now)
            stats["v2"] = {"status": v2.status, "auc": v2.metrics["auc"], "ece": v2.metrics["ece"]}
            if v2.status == "awaiting_approval" and registry.production(db) is None:
                # v1 failed a gate (e.g. calibration or fairness): the lead reviews the passing candidate instead
                registry.approve(db, v2.version, lead, "Seed bootstrap: v1 failed a promotion gate; passing candidate "
                                                       "reviewed (demo).", now=now)
                stats["v2"]["status"] = v2.status
            db.commit()
            director = actor_from_user(users_by_pid[d["director"].id])
            stats["proposals"] = len(generate_proposals(db, director))
        stats["replay"] = rep.stats
        if simulate:
            sim = run_simulation(n=n, seed=sv)
            db.add(SimulationRun(params={"n": n, "seed": sv, "replications": sim["replications"]}, results=sim))
        audit.record(db, SEED_ACTOR, "C", "seed.finished", detail={"seconds": round(time.time() - t_start, 1)})
        stats["cases"] = db.query(Case).count()
        stats["open"] = db.query(Case).filter(Case.state != S.CLOSED).count()
        stats["demo_patients"] = demo
    stats["seconds"] = round(time.time() - t_start, 1)
    return stats


DEMO_PATIENTS = [  # named demo stories (synthetic people), aligned with the design-doc demo script
    # lang, nationality, given, family, gender, age, programme, region, value, value_text, smartphone
    ("ar", "Emirati", "Fatima", "Al Kaabi", "female", 52, "colorectal_fit", "Al Ain", 260.0, None, True),
    ("en", "Western", "Emma", "Clarke", "female", 49, "breast", "Abu Dhabi", None, "BI-RADS 0", True),
    ("hi", "Indian", "Ramesh", "Kumar", "male", 58, "diabetes_hba1c", "Abu Dhabi", 8.1, None, True),
    ("ur", "Pakistani", "Ayesha", "Khan", "female", 41, "cervical", "Abu Dhabi", None, "HPV other hr", True),
    ("tl", "Filipino", "Maria", "Santos", "female", 46, "breast", "Abu Dhabi", None, "BI-RADS 4", False),
]


PATHWAY_PROGRAMME = {"repeat_fit": "colorectal_fit", "repeat_sample": "cervical", "surveillance_1y": "cervical",
                     "colposcopy_urgent": "cervical", "prediabetes_gp": "diabetes_hba1c"}


def _demo_content_signoffs(db: Session, d: dict, users_by_pid: dict) -> None:
    """SYNTHETIC DEMO: each programme's clinical lead signs off the ENGLISH pathway wording through the same
    governed path a real lead would use. Arabic/Hindi/Urdu drafts are deliberately NOT signed off: they need a
    professional translator first, so patients in those languages get English + an interpreter-supported call."""
    from .comms import governance as cg  # noqa: PLC0415
    from .comms.content import PATHWAY_TEXT_EN  # noqa: PLC0415

    for pw in PATHWAY_TEXT_EN:
        lead = actor_from_user(users_by_pid[d["leads"][PATHWAY_PROGRAMME[pw]].id])
        cg.approve(db, lead, f"pathway:{pw}:en", "clinical",
                   "SYNTHETIC DEMO sign-off of prototype wording (readability checks passed).")
    lead = actor_from_user(users_by_pid[d["leads"]["colorectal_fit"].id])
    cg.approve(db, lead, "pathway_fallback:neutral:en", "clinical", "SYNTHETIC DEMO sign-off of the neutral fallback.")
    db.flush()


def _demo_patients(db, d, rng, now, users_by_pid) -> list[str]:
    """Named demo patients with a fresh result, notified, ready for the teach-back (log in as patient.<lang>)."""
    out = []
    for i, (lang, nat, given, family, gender, age, prog, region, value, text, smart) in enumerate(DEMO_PATIENTS):
        sp = SynthPatient(idx=90000 + i, given=given, family=family, gender=gender, age=age, nationality=nat, language=lang,
                          region=region, phone_valid=True, has_smartphone=smart, whatsapp=smart, alt_contact=True,
                          prior_no_shows=0, prior_screens=2, responsiveness=1.0, programme=prog, share_consent=True,
                          analytics_consent=True)
        p = add_patient(db, sp, d, rng, now)
        t = now - timedelta(hours=20)
        _, case = wf.ingest_result(db, p, prog, value=value, value_text=text, performer_org_id=d["lab"].id, now=t,
                                   unit={"colorectal_fit": "ug/g", "diabetes_hba1c": "%"}.get(prog))
        coord = actor_from_user(users_by_pid[d["coordinators"][prog][0].id])
        triager = actor_from_user(users_by_pid[p.gp_id]) if case.tier in ("critical", "urgent") else SYSTEM
        wf.transition(db, case, S.TRIAGED, triager, now=t + timedelta(minutes=10),
                      note="clinician confirmed T1 (BI-RADS 4)" if triager is not SYSTEM else None)
        send_with_ladder(db, case, "result_notification", SYSTEM, now=t + timedelta(hours=1))
        if S(case.state) != S.PATIENT_NOTIFIED:
            record_call(db, case, coord, "reached", note="navigator call in patient's language", now=t + timedelta(hours=2))
        u = User(username=f"patient.{lang}", display_name=f"{given} {family} (patient, {lang})", role="patient",
                 patient_id=p.id)
        db.add(u)
        db.flush()
        out.append(f"{u.username} ({given} {family}) -> case #{case.id}")
    return out


def _edge_cases(db, d, rng, now, users_by_pid) -> None:
    a = lambda prac: actor_from_user(users_by_pid[prac.id])  # noqa: E731
    # 1) a GP leaves: their open cases must be moved to the team pool by the sweep (never unowned)
    leaver = db.scalar(select(Practitioner).where(Practitioner.name.like("Dr. Carlos Reyes%")))
    if leaver:
        leaver.active = False
    # 2) pending two-key transfers (patient moving provider)
    open_cases = db.scalars(select(Case).where(Case.state.in_([S.ACKNOWLEDGED, S.CONSULTATION]))).all()
    for case in open_cases[:3]:
        other = [o for o in d["phc"] if o.id != case.managing_org_id][0]
        to = d["gps"][other.id][0]
        coord = a(d["coordinators"][case.programme][0])
        try:
            wf.request_transfer(db, case, to.id, coord, "Patient moved home; registered with a new family health centre.",
                                now=now - timedelta(hours=30))
        except Exception:  # noqa: BLE001 - consent may block; that is fine for the demo
            pass
    # 3) open clinician-to-clinician and patient-to-clinician consultations
    cons = db.scalars(select(Case).where(Case.state.in_([S.CONSULTATION, S.CONFIRMATORY_TEST, S.REFERRAL_SENT]))).all()
    for case in cons[:6]:
        gp_p = db.get(Practitioner, case.patient.gp_id)
        if not gp_p or not gp_p.active:
            continue
        hosp = [h for h in d["hosp"] if h.region == case.patient.region] or d["hosp"]
        from .programmes import PROGRAMMES

        spec = d["specialists"][(hosp[0].id, PROGRAMMES[case.programme].specialty)]
        wf.create_consult(db, case, a(gp_p), "Is a direct-to-test pathway appropriate given the result and history?",
                          target_practitioner_id=spec.id, now=now - timedelta(hours=rng.uniform(2, 60)))
    pts = db.scalars(select(Case).where(Case.state == S.ACKNOWLEDGED)).all()
    for case in pts[3:6]:
        from .synth.history import patient_actor

        wf.create_consult(db, case, patient_actor(case.patient), "I am worried about the test. Can I speak to a doctor first?",
                          now=now - timedelta(hours=rng.uniform(2, 30)))


def main() -> None:
    ap = argparse.ArgumentParser(description="Seed Wasl with SYNTHETIC data")
    ap.add_argument("--patients", type=int, default=None)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--no-train", action="store_true")
    ap.add_argument("--realistic", action="store_true", help="population-calibrated mix incl. negatives and defects")
    ap.add_argument("--no-sim", action="store_true", help="skip the discrete-event simulation")
    args = ap.parse_args()
    stats = seed(args.patients, args.seed, train=not args.no_train, realistic=args.realistic, simulate=not args.no_sim)
    import json

    print(json.dumps(stats, indent=2, default=str))


if __name__ == "__main__":
    main()
