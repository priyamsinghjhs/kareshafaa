"""One-time, guarded data steps for the SYN- sample patients only (never real records).

1. reanchor_sample_case_dates  - open SYN- sample cases whose current step has been overdue for months (the
   synthetic history stops some journeys mid-pathway) get their whole case timeline moved forward so the step is
   now overdue by a realistic 3-16 days. One monotonic time map per case keeps every interval and the order of
   events; the last 2 days (anything a person may have just done on the live system) are never moved; future
   planned times are not moved. The append-only audit log is not rewritten: each move is itself audited.
2. backfill_sample_clinical_history - realistic, pathway-consistent background history (conditions, allergies,
   medicines, consultations, investigations) for SYN- sample patients, marked source="sample_backfill".

Both are recorded in data_migrations and run once per database (bootstrap, under the bootstrap lock).
Selection is strictly: Patient.mrn starts with "SYN-" AND Patient.synthetic. Re-running is a no-op.
"""
from __future__ import annotations

import logging
import random
import time
from datetime import datetime, timedelta

from sqlalchemy import DateTime, select, update
from sqlalchemy.orm import Session

from .. import audit
from ..clinical_rules import ELIGIBLE_AGE
from ..db import Base
from ..models import (AllergyIntolerance, Appointment, CarePlan, Case, Condition, Consent, DataMigration,
                      DiagnosticReport, Encounter, Investigation, MedicationRequest, Organization, Patient,
                      Practitioner, StepTask, utcnow)
from ..security import SYSTEM

log = logging.getLogger("kareshafaa.sample_data")
SAMPLE_PREFIX = "SYN-"
SOURCE = "sample_backfill"
STALE_AFTER = timedelta(days=21)     # a step overdue longer than this is a stale sample date
PROTECT = timedelta(days=2)          # never move anything from the last 2 days


def is_sample(p: Patient | None) -> bool:
    return bool(p is not None and p.synthetic and (p.mrn or "").startswith(SAMPLE_PREFIX))


# ------------------------------------------------------------------------------------------ 1. dates
def _case_tables():
    out = []
    for t in Base.metadata.sorted_tables:
        if t.name in ("audit_events", "data_migrations") or "case_id" not in t.c:
            continue
        cols = [c.name for c in t.columns if isinstance(c.type, DateTime)]
        if cols:
            out.append((t, cols))
    return out


def stale_sample_cases(db: Session, now: datetime, only_case_ids: set[int] | None = None) -> list[tuple[Case, StepTask]]:
    q = (select(Case, StepTask).join(StepTask, (StepTask.case_id == Case.id) & (StepTask.status == "in-progress"))
         .join(Patient, Patient.id == Case.patient_id)
         .where(Case.state != "closed", Patient.mrn.like(SAMPLE_PREFIX + "%"), Patient.synthetic.is_(True),
                StepTask.due_at.is_not(None), StepTask.due_at < now - STALE_AFTER))
    if only_case_ids is not None:
        q = q.where(Case.id.in_(only_case_ids))
    return list(db.execute(q.order_by(Case.id)).all())


def _time_map(due: datetime, new_due: datetime, now: datetime):
    hi = now - PROTECT
    delta = new_due - due
    ratio = (hi - new_due) / (hi - due) if hi > due else 1.0

    def f(t: datetime | None) -> datetime | None:
        if t is None or t >= hi:
            return t
        if t <= due:
            return t + delta
        return new_due + (t - due) * ratio
    return f


def reanchor_sample_case_dates(db: Session, now: datetime | None = None,
                               only_case_ids: set[int] | None = None) -> dict:
    now = now or utcnow()
    moved, skipped = [], []
    for case, step in stale_sample_cases(db, now, only_case_ids):
        p = db.get(Patient, case.patient_id)
        # a refusal recorded during this case but not linked to it would change order relative to the moved
        # events (withdrawal timing must stay exact); linked consents move with the case. Deaths: never moved.
        if not is_sample(p) or p.deceased or db.scalar(select(Consent.id).where(
                Consent.patient_id == p.id, Consent.decision == "deny", Consent.recorded_at >= case.created_at,
                (Consent.case_id.is_(None)) | (Consent.case_id != case.id)).limit(1)) is not None:
            skipped.append(case.id)
            continue
        k = 3 + (case.id * 7919) % 14  # deterministic 3..16 days overdue
        new_due = now - timedelta(days=k)
        f = _time_map(step.due_at, new_due, now)
        rep = db.get(DiagnosticReport, case.report_id)
        lo, hi_age = ELIGIBLE_AGE.get(case.programme, (0, 200))
        if rep is not None and not (lo <= f(rep.issued_at).year - p.birth_year <= hi_age) \
                and lo <= rep.issued_at.year - p.birth_year <= hi_age:
            skipped.append(case.id)  # moving it would change programme age eligibility
            continue
        rows = 0
        for t, cols in _case_tables():
            sel = select(t.c.id, *[t.c[c] for c in cols]).where(t.c.case_id == case.id)
            if t.name == "diagnostic_reports":
                sel = select(t.c.id, *[t.c[c] for c in cols]).where((t.c.case_id == case.id) | (t.c.id == case.report_id))
            for r in db.execute(sel).all():
                vals = {c: f(r[i + 1]) for i, c in enumerate(cols)}
                vals = {c: v for c, v in vals.items() if v != r[cols.index(c) + 1]}
                if vals:
                    db.execute(update(t).where(t.c.id == r[0]).values(**vals))
                    rows += 1
        ct = Case.__table__
        cvals = {c: f(getattr(case, c)) for c in ("created_at", "state_entered_at", "due_at", "target_due_at",
                                                  "care_reached_at")}
        db.execute(update(ct).where(ct.c.id == case.id).values(**cvals, row_version=case.row_version + 1))
        audit.record(db, SYSTEM, "U", "sample.dates_reanchored", entity_type="Case", entity_id=case.id,
                     case_id=case.id, ts=now,
                     detail={"reason": "SYN- sample case: step overdue since the sample data was generated",
                             "old_step_due": step.due_at.isoformat(), "new_step_due": new_due.isoformat(),
                             "shift_days": round((new_due - step.due_at).total_seconds() / 86400, 1),
                             "rows_updated": rows + 1})
        moved.append(case.id)
    db.expire_all()
    return {"moved": len(moved), "skipped": len(skipped), "case_ids": moved[:50]}


# ------------------------------------------------------------------------------------------ 2. history
BACKGROUND = [  # display, ICD-10, base probability, age slope per year over 40
    ("Essential hypertension", "I10", 0.08, 0.012),
    ("Dyslipidaemia", "E78.5", 0.12, 0.008),
    ("Hypothyroidism", "E03.9", 0.04, 0.001),
    ("Asthma", "J45.909", 0.06, 0.0),
]
ALLERGIES = [("Penicillin", "Rash (urticaria)", "moderate", 0.09), ("Sulfonamide antibiotics", "Rash", "mild", 0.03),
             ("NSAIDs (ibuprofen)", "Wheeze / bronchospasm", "moderate", 0.025), ("Latex", "Contact dermatitis", "mild", 0.02),
             ("Shellfish", "Lip swelling", "moderate", 0.025)]


def _specialist(db: Session, org_id: int | None, word: str) -> Practitioner | None:
    q = select(Practitioner).where(Practitioner.role == "specialist", Practitioner.name.ilike(f"%{word}%"))
    return db.scalars(q.where(Practitioner.org_id == org_id)).first() or db.scalars(q).first()


def _history_for(db: Session, p: Patient, now: datetime) -> list:
    rng = random.Random(f"kareshafaa-history-{p.mrn}")
    rows: list = []
    cases = db.scalars(select(Case).where(Case.patient_id == p.id).order_by(Case.created_at)).all()
    first = min([c.created_at for c in cases] or [now - timedelta(days=60)])
    age = now.year - p.birth_year
    gp_id = p.gp_id
    lab = db.scalar(select(Organization).where(Organization.kind == "lab"))
    lab_name = lab.name if lab else "Laboratory"
    centre = db.get(Organization, p.managing_org_id)
    centre_name = centre.name if centre else "Family health centre"

    def past(t: datetime) -> bool:
        return t <= now - timedelta(minutes=5)

    # conditions (background, age-dependent) ---------------------------------------------------
    conds = set()
    for name, code, base, slope in BACKGROUND:
        pr = base + slope * max(0, age - 40)
        if name == "Hypothyroidism" and p.gender == "female":
            pr *= 2.5
        if rng.random() < min(pr, 0.55):
            conds.add(name)
            onset = first - timedelta(days=rng.randint(400, 3000))
            rows.append(Condition(patient_id=p.id, display=name, code=code, clinical_status="active", onset=onset,
                                  recorded_at=onset, source=SOURCE))
    if p.haemoglobinopathy:
        rows.append(Condition(patient_id=p.id, display="Haemoglobin variant (e.g. thalassaemia trait)", code="D56.3",
                              clinical_status="active", onset=first - timedelta(days=4000), recorded_at=first - timedelta(days=4000),
                              source=SOURCE))
    if p.pregnant:
        rows.append(Condition(patient_id=p.id, display="Pregnancy", code="Z33.1", clinical_status="active",
                              onset=first - timedelta(days=60), recorded_at=first - timedelta(days=50), source=SOURCE))

    # allergies (always one row: an allergy or an explicit NKDA) -----------------------------------
    roll, acc, allergy = rng.random(), 0.0, None
    for sub, rx, sev, pr in ALLERGIES:
        acc += pr
        if roll < acc and not (sub.startswith("NSAID") and "Asthma" not in conds and rng.random() < 0.5):
            allergy = (sub, rx, sev)
            break
    if allergy:
        rows.append(AllergyIntolerance(patient_id=p.id, substance=allergy[0], reaction=allergy[1], severity=allergy[2],
                                       status="active", recorded_at=first - timedelta(days=rng.randint(500, 4000)),
                                       source=SOURCE))
    else:
        rows.append(AllergyIntolerance(patient_id=p.id, substance="No known drug allergies", status="active",
                                       recorded_at=first - timedelta(days=rng.randint(200, 2000)), source=SOURCE))

    # long-term medicines consistent with the conditions ---------------------------------------------
    def med(name, dose, freq, start, status="active", end=None, reason=None, prescriber=gp_id, route="oral",
            case_id=None, stop_reason=None, note=None):
        if not past(start):
            return
        rows.append(MedicationRequest(patient_id=p.id, case_id=case_id, name=name, dose=dose, frequency=freq,
                                      route=route, start_date=start, end_date=end, status=status,
                                      prescriber_id=prescriber, reason=reason, stop_reason=stop_reason, note=note,
                                      source=SOURCE, created_at=start))
    onset = {r.display: r.onset for r in rows if isinstance(r, Condition)}
    if "Essential hypertension" in conds:
        st = onset["Essential hypertension"] + timedelta(days=14)
        if p.pregnant:
            med("Methyldopa", "250 mg", "Twice daily", st, reason="Hypertension in pregnancy")
        elif rng.random() < 0.3:
            sw = st + timedelta(days=rng.randint(60, 300))
            med("Amlodipine", "5 mg", "Once daily", st, status="stopped", end=sw, reason="Hypertension",
                stop_reason="Ankle swelling - switched to perindopril")
            med("Perindopril", "4 mg", "Once daily", sw, reason="Hypertension")
        else:
            med("Amlodipine", "5 mg", "Once daily", st, reason="Hypertension")
    if "Dyslipidaemia" in conds and not p.pregnant:
        med("Atorvastatin", "20 mg", "Once daily at night", onset["Dyslipidaemia"] + timedelta(days=30),
            reason="Dyslipidaemia (cardiovascular risk)")
    if "Hypothyroidism" in conds:
        med("Levothyroxine", "50 micrograms", "Once daily before breakfast", onset["Hypothyroidism"] + timedelta(days=10),
            reason="Hypothyroidism")
    if "Asthma" in conds:
        med("Salbutamol inhaler", "100 micrograms/dose", "2 puffs when required", onset["Asthma"] + timedelta(days=1),
            reason="Asthma", route="inhaled")
    if p.pregnant:
        med("Folic acid", "400 micrograms", "Once daily", first - timedelta(days=55), reason="Pregnancy")

    # annual GP review before the first screening case -----------------------------------------------
    rv = first - timedelta(days=rng.randint(40, 200))
    probs = ", ".join(sorted(conds)) or "no long-term conditions"
    bp = f"{rng.randint(112, 138 if 'Essential hypertension' in conds else 128)}/{rng.randint(70, 88)}"
    rows.append(Encounter(patient_id=p.id, kind="gp_visit", start=rv, practitioner_id=gp_id,
                          org_id=p.managing_org_id, reason="Annual health check",
                          notes=f"Annual review at {centre_name}. Problems: {probs}. BP {bp} mmHg. "
                                f"Medicines reviewed and reconciled. Screening invitations discussed.",
                          outcome="Routine follow-up in 12 months", source=SOURCE, created_at=rv))
    egfr = max(45, min(115, int(rng.gauss(100 - max(0, age - 40) * 0.8, 8))))
    rows.append(Investigation(patient_id=p.id, name="eGFR (kidney function)", code="33914-3", value=str(egfr),
                              unit="mL/min/1.73m²", reference_range="> 60", flag="N" if egfr >= 60 else "L",
                              effective_at=rv + timedelta(hours=1), performer=lab_name, source=SOURCE, created_at=rv))
    ldl = round(rng.uniform(3.6, 4.9) if "Dyslipidaemia" in conds else rng.uniform(1.9, 3.3), 1)
    rows.append(Investigation(patient_id=p.id, name="LDL cholesterol", code="13457-7", value=str(ldl), unit="mmol/L",
                              reference_range="< 3.4", flag="H" if ldl >= 3.4 else "N",
                              effective_at=rv + timedelta(hours=1), performer=lab_name, source=SOURCE, created_at=rv))
    if "Hypothyroidism" in conds:
        tsh = round(rng.uniform(0.8, 3.9), 2)
        rows.append(Investigation(patient_id=p.id, name="TSH", code="3016-3", value=str(tsh), unit="mIU/L",
                                  reference_range="0.4–4.0", flag="N", effective_at=rv + timedelta(hours=1),
                                  performer=lab_name, source=SOURCE, created_at=rv))

    # pathway-specific history for each case -------------------------------------------------------
    for c in cases:
        plans = db.scalars(select(CarePlan).where(CarePlan.case_id == c.id).order_by(CarePlan.id)).all()
        plan = plans[-1] if plans else None
        desc = (plan.description if plan else "") or ""
        attended = db.scalars(select(Appointment).where(Appointment.case_id == c.id, Appointment.status == "fulfilled")
                              .order_by(Appointment.start)).all()
        rep = db.get(DiagnosticReport, c.report_id)
        val = ""
        if rep is not None and rep.observations:
            o = rep.observations[0]
            val = f"{o.value if o.value is not None else (o.value_text or '')} {o.unit or ''}".strip()
        if c.programme == "colorectal_fit" and rep is not None and rep.conclusion != "negative" and past(c.created_at + timedelta(days=3)):
            hb = round(rng.uniform(10.2, 11.8), 1) if rng.random() < 0.2 else round(rng.uniform(12.2, 15.4), 1)
            rows.append(Investigation(patient_id=p.id, case_id=c.id, name="Haemoglobin (full blood count)", code="718-7",
                                      value=str(hb), unit="g/dL", reference_range="12.0–16.0" if p.gender == "female" else "13.0–17.0",
                                      flag="L" if hb < (12.0 if p.gender == "female" else 13.0) else "N",
                                      effective_at=c.created_at + timedelta(days=3), performer=lab_name, source=SOURCE,
                                      created_at=c.created_at + timedelta(days=3)))
        for a in attended:
            if not past(a.start):
                continue
            if c.programme == "colorectal_fit":
                sp = _prac(db, a.practitioner_id) or _specialist(db, a.org_id, "Gastro")
                med("Sodium picosulfate / magnesium citrate (bowel preparation)", "1 sachet",
                    "2 doses the day before the procedure", a.start - timedelta(days=1), status="completed",
                    end=a.start - timedelta(hours=10), reason="Bowel preparation for colonoscopy",
                    prescriber=sp.id if sp else None, case_id=c.id)
                note = ("Colonoscopy to caecum, good bowel preparation. " +
                        ("Two small adenomatous-looking polyps removed (cold snare), retrieved for histology."
                         if "adenoma" in desc else "Suspicious lesion seen; biopsies taken and tattooed."
                         if "lesion" in desc else "No significant abnormality seen."))
                rows.append(Encounter(patient_id=p.id, case_id=c.id, appointment_id=a.id, kind="colonoscopy", start=a.start,
                                      practitioner_id=sp.id if sp else None, org_id=a.org_id,
                                      reason=(f"Positive FIT ({val})" if any(k in (c.rule_id or "") for k in ("POS", "URGENT"))
                                              else f"Colonoscopy after clinical review of the FIT result ({val})"),
                                      notes=note, outcome=desc or "Procedure completed",
                                      source=SOURCE, created_at=a.start))
                if ("adenoma" in desc or "lesion" in desc) and past(a.start + timedelta(days=7)):
                    rows.append(Investigation(patient_id=p.id, case_id=c.id, name="Histology – colonic biopsy/polyps",
                                              value=("Tubular adenoma(s), low-grade dysplasia, completely excised"
                                                     if "adenoma" in desc else "Abnormal - reported to colorectal MDT"),
                                              flag="A", effective_at=a.start + timedelta(days=7),
                                              performer=(_oname_db(db, a.org_id) or "Hospital") + " – Histopathology",
                                              source=SOURCE, created_at=a.start + timedelta(days=7)))
            elif c.programme == "cervical":
                sp = _prac(db, a.practitioner_id) or _specialist(db, a.org_id, "Colposcopy")
                cin = "CIN2/3" in desc
                rows.append(Encounter(patient_id=p.id, case_id=c.id, appointment_id=a.id, kind="colposcopy", start=a.start,
                                      practitioner_id=sp.id if sp else None, org_id=a.org_id,
                                      reason=f"Screening result: {val or rep.display if rep else 'HPV positive'}",
                                      notes=("Colposcopy: acetowhite area at the transformation zone; punch biopsy taken."
                                             if cin else "Colposcopy: satisfactory, transformation zone seen; minor or no change."),
                                      outcome=desc or "Colposcopy completed", source=SOURCE, created_at=a.start))
                if past(a.start + timedelta(days=10)) and desc:
                    rows.append(Investigation(patient_id=p.id, case_id=c.id, name="Cervical biopsy histology",
                                              value="CIN 2/3 (high-grade)" if cin else "CIN 1 / no CIN", flag="A" if cin else "N",
                                              effective_at=a.start + timedelta(days=10),
                                              performer=(_oname_db(db, a.org_id) or "Hospital") + " – Histopathology",
                                              source=SOURCE, created_at=a.start + timedelta(days=10)))
            elif c.programme == "breast":
                sp = _prac(db, a.practitioner_id) or _specialist(db, a.org_id, "Breast")
                rows.append(Encounter(patient_id=p.id, case_id=c.id, appointment_id=a.id, kind="breast_clinic", start=a.start,
                                      practitioner_id=sp.id if sp else None, org_id=a.org_id,
                                      reason=f"Screening mammogram {val}".strip(),
                                      notes="Triple assessment: clinical examination, diagnostic mammogram and ultrasound; "
                                            "image-guided core biopsy where indicated.",
                                      outcome=desc or "Assessment completed", source=SOURCE, created_at=a.start))
            elif c.programme == "diabetes_hba1c":
                sp = _prac(db, a.practitioner_id) or _specialist(db, a.org_id, "Endocrin")
                rows.append(Encounter(patient_id=p.id, case_id=c.id, appointment_id=a.id, kind="specialist_visit",
                                      start=a.start, practitioner_id=sp.id if sp else None, org_id=a.org_id,
                                      reason=f"HbA1c {val}".strip(), notes="Endocrinology review: glycaemic control, "
                                      "complications screen and treatment plan discussed with the patient.",
                                      outcome=desc or "Reviewed", source=SOURCE, created_at=a.start))
        if c.programme == "diabetes_hba1c" and plan is not None and past(plan.created_at):
            t = plan.created_at - timedelta(hours=1)
            if "diabetes confirmed" in desc:
                rows.append(Encounter(patient_id=p.id, case_id=c.id, kind="gp_visit", start=t, practitioner_id=plan.recorded_by_id,
                                      org_id=p.managing_org_id, reason=f"Raised HbA1c ({val}) confirmed on repeat test",
                                      notes="Type 2 diabetes confirmed and explained. Education and lifestyle plan; "
                                            "metformin started (eGFR checked). Annual eye, foot and kidney checks booked.",
                                      outcome="Diabetes care plan started", source=SOURCE, created_at=t))
                rows.append(Condition(patient_id=p.id, display="Type 2 diabetes mellitus", code="E11.9",
                                      clinical_status="active", onset=t, recorded_at=t, source=SOURCE))
                med("Metformin", "500 mg", "Twice daily with meals", t, reason="Type 2 diabetes",
                    prescriber=plan.recorded_by_id, case_id=c.id)
                if past(t + timedelta(days=1)):
                    acr = round(rng.uniform(0.5, 2.6), 1)
                    rows.append(Investigation(patient_id=p.id, case_id=c.id, name="Urine albumin:creatinine ratio",
                                              code="9318-7", value=str(acr), unit="mg/mmol", reference_range="< 3.0",
                                              flag="N", effective_at=t + timedelta(days=1), performer=lab_name,
                                              source=SOURCE, created_at=t + timedelta(days=1)))
            elif "intensified" in desc:
                rows.append(Condition(patient_id=p.id, display="Type 2 diabetes mellitus", code="E11.9",
                                      clinical_status="active", onset=t, recorded_at=t, source=SOURCE))
                med("Metformin", "1 g", "Twice daily with meals", t - timedelta(days=200), reason="Type 2 diabetes")
                med("Sitagliptin", "100 mg", "Once daily", t, reason="Type 2 diabetes - treatment intensified",
                    prescriber=plan.recorded_by_id, case_id=c.id)
            elif "Endocrinology review" in desc:
                rows.append(Condition(patient_id=p.id, display="Type 2 diabetes mellitus", code="E11.9",
                                      clinical_status="active", onset=t, recorded_at=t, source=SOURCE))
                med("Metformin", "500 mg", "Twice daily with meals", t, reason="Type 2 diabetes",
                    prescriber=plan.recorded_by_id, case_id=c.id)
            elif "Prediabetes" in desc or "below the diabetes threshold" in desc:
                rows.append(Encounter(patient_id=p.id, case_id=c.id, kind="gp_visit", start=t, practitioner_id=plan.recorded_by_id,
                                      org_id=p.managing_org_id, reason=f"HbA1c in the prediabetes range ({val})",
                                      notes="Result explained. Lifestyle advice (diet, activity, weight); diabetes-prevention "
                                            "programme offered. No medicine started.",
                                      outcome="Repeat HbA1c in 12 months", source=SOURCE, created_at=t))
                rows.append(Condition(patient_id=p.id, display="Prediabetes (intermediate hyperglycaemia)", code="R73.03",
                                      clinical_status="active", onset=t, recorded_at=t, source=SOURCE))
        if c.programme == "colorectal_fit" and "adenoma" in desc and plan is not None:
            rows.append(Condition(patient_id=p.id, display="Colorectal adenoma - polypectomy, on surveillance", code="D12.6",
                                  clinical_status="active", onset=plan.created_at, recorded_at=plan.created_at, source=SOURCE))
        if c.programme == "cervical" and "CIN2/3" in desc and plan is not None:
            rows.append(Condition(patient_id=p.id, display="CIN 2/3 (cervical intraepithelial neoplasia)", code="N87.1",
                                  clinical_status="active", onset=plan.created_at, recorded_at=plan.created_at, source=SOURCE))
        if plan is not None and attended and rng.random() < 0.25 and past(plan.created_at + timedelta(days=14)):
            t = plan.created_at + timedelta(days=14)
            rows.append(Encounter(patient_id=p.id, case_id=c.id, kind="teleconsult", start=t,
                                  practitioner_id=plan.recorded_by_id, reason="Results and plan follow-up",
                                  notes="Video call: results explained in the patient's language; questions answered; "
                                        "plan confirmed.", outcome=desc[:300] or "Plan confirmed", source=SOURCE, created_at=t))
    out, seen = [], set()
    for r in rows:
        if _when(r) is not None and _when(r) > now:
            continue
        key = ((type(r).__name__, r.display) if isinstance(r, Condition) else
               (type(r).__name__, r.name, r.status) if isinstance(r, MedicationRequest) and r.status == "active" else None)
        if key is not None:
            if key in seen:
                continue  # e.g. two diabetes cases: one problem-list entry, one active metformin
            seen.add(key)
        out.append(r)
    return out


def _prac(db: Session, pid: int | None) -> Practitioner | None:
    return db.get(Practitioner, pid) if pid else None


def _oname_db(db: Session, oid: int | None) -> str | None:
    o = db.get(Organization, oid) if oid else None
    return o.name if o else None


def _when(r) -> datetime | None:
    for a in ("start", "start_date", "effective_at", "onset", "recorded_at"):
        v = getattr(r, a, None)
        if v is not None:
            return v
    return None


def backfill_sample_clinical_history(db: Session, now: datetime | None = None,
                                     only_patient_ids: set[int] | None = None) -> dict:
    now = now or utcnow()
    q = select(Patient).where(Patient.mrn.like(SAMPLE_PREFIX + "%"), Patient.synthetic.is_(True)).order_by(Patient.id)
    if only_patient_ids is not None:
        q = q.where(Patient.id.in_(only_patient_ids))
    done = set(db.scalars(select(AllergyIntolerance.patient_id).where(AllergyIntolerance.source == SOURCE)))
    n_pat, counts = 0, {}
    for p in db.scalars(q).all():
        if p.id in done or not is_sample(p):
            continue  # idempotent: this patient already has its sample history
        rows = _history_for(db, p, now)
        db.add_all(rows)
        for r in rows:
            counts[type(r).__name__] = counts.get(type(r).__name__, 0) + 1
        n_pat += 1
        if n_pat % 200 == 0:
            db.flush()
    db.flush()
    if n_pat:
        audit.record(db, SYSTEM, "C", "sample.history_backfilled", entity_type="Patient", ts=now,
                     detail={"patients": n_pat, "rows": counts, "selection": "mrn LIKE 'SYN-%' AND synthetic",
                             "source": SOURCE})
    return {"patients": n_pat, "rows": counts}


# ------------------------------------------------------------------------------------------ runner
MIGRATIONS = [
    ("reanchor_sample_case_dates_v1", reanchor_sample_case_dates),
    ("backfill_sample_clinical_history_v1", backfill_sample_clinical_history),
]


BATCHED = {"backfill_sample_clinical_history_v1"}


def pending_sample_patient_ids(db: Session) -> list[int]:
    """SYN- sample patients that do not have their sample history yet (the allergy/NKDA row is the marker)."""
    done = select(AllergyIntolerance.patient_id).where(AllergyIntolerance.source == SOURCE)
    return list(db.scalars(select(Patient.id).where(Patient.mrn.like(SAMPLE_PREFIX + "%"), Patient.synthetic.is_(True),
                                                    Patient.id.not_in(done)).order_by(Patient.id)))


def _applied(db: Session, name: str) -> bool:
    return db.scalar(select(DataMigration.id).where(DataMigration.name == name)) is not None


def run_pending(session_factory, now: datetime | None = None, *, batch_size: int | None = None, pause: float = 0.0,
                stop=None) -> dict:
    """Apply each not-yet-applied step; a failure is logged and retried at the next start (it never blocks the
    server). With batch_size the history backfill commits every batch_size patients (progress is kept if the
    process stops; the step is marked applied only when no patient is left) and sleeps `pause` s between batches."""
    out = {}
    for name, fn in MIGRATIONS:
        if stop is not None and stop.is_set():
            out[name] = "stopped"
            break
        db: Session = session_factory()
        try:
            if _applied(db, name):
                out[name] = "already applied"
                continue
            if batch_size and name in BATCHED:
                total = {"patients": 0, "rows": {}, "batches": 0}
                ids = pending_sample_patient_ids(db)
                db.close()
                for k in range(0, len(ids), batch_size):
                    if stop is not None and stop.is_set():
                        break
                    bdb: Session = session_factory()
                    try:
                        res = fn(bdb, now, only_patient_ids=set(ids[k:k + batch_size]))
                        bdb.commit()
                    except Exception:
                        bdb.rollback()
                        raise
                    finally:
                        bdb.close()
                    total["patients"] += res["patients"]
                    total["batches"] += 1
                    for t, n in res["rows"].items():
                        total["rows"][t] = total["rows"].get(t, 0) + n
                    if pause:
                        time.sleep(pause)
                db = session_factory()
                if pending_sample_patient_ids(db) or _applied(db, name):
                    out[name] = {"partial": total} if not _applied(db, name) else "already applied"
                    continue
                res = total
            else:
                res = fn(db, now)
            db.add(DataMigration(name=name, applied_at=now or utcnow(), detail=res))
            audit.record(db, SYSTEM, "U", "data_migration.applied", entity_type="DataMigration", entity_id=name,
                         ts=now, detail=res)
            db.commit()
            out[name] = res
        except Exception as e:  # pragma: no cover - logged, retried next start
            db.rollback()
            log.exception("data step %s failed (retried next start)", name)
            out[name] = f"failed: {type(e).__name__}"
        finally:
            db.close()
    return out


def run_in_background(stop=None, *, batch_size: int = 100, pause: float = 0.5) -> dict | None:
    """Run the pending steps once per database, from one process only (PostgreSQL advisory try-lock, or a file
    lock for SQLite). Returns None when another process holds the lock or nothing is pending."""
    import fcntl
    import os

    from ..config import get_settings
    from ..db import get_engine, session_factory
    sf = session_factory()
    with sf() as db:
        if all(_applied(db, n) for n, _ in MIGRATIONS):
            return None
    eng = get_engine()
    if eng.dialect.name == "postgresql":
        from sqlalchemy import text
        with eng.connect() as conn:
            if not conn.execute(text("SELECT pg_try_advisory_lock(727002)")).scalar():
                return None
            try:
                return run_pending(sf, batch_size=batch_size, pause=pause, stop=stop)
            finally:
                conn.execute(text("SELECT pg_advisory_unlock(727002)"))
    fd = os.open(str(get_settings().data_dir / ".data-steps.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return None
        return run_pending(sf, batch_size=batch_size, pause=pause, stop=stop)
    finally:
        os.close(fd)
