"""EDGE-CASE synthetic cohort (round 20): stratified, NOT prevalence-calibrated.

Every person gets (programme, clinical severity of the clean result, scenario). Severity is stratified uniformly
over the tiers the rules engine can produce for that programme; the scenario is drawn from SCENARIOS. Results are
drawn from category generators whose values come from docs/clinical_rules.md (cut-offs, BI-RADS, ASCCP
categories), and the severity label is the RULES ENGINE's own decision on the clean result (never hand-coded),
so a category can never be filed under the wrong tier. Every event is executed through the real service layer
(ingest, HL7 parser, workflow, comms), back-dated, exactly like the other cohorts.

Purpose: exercise every supported (disease x severity x scenario) combination for testing, validation and model
robustness. Rates here are design choices, not epidemiology: never quote them as prevalence. All data SYNTHETIC.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta

from sqlalchemy import select

from ..calendar_uae import to_utc
from ..clinical_rules import PatientContext, ResultInput, evaluate
from ..comms.content import SUPPORTED_LANGUAGES
from ..models import Case, Patient
from ..security import SYSTEM
from ..services import ingest as ing
from ..services import workflow as wf
from ..services.errors import DomainError
from ..statemachine import S
from .cohort import ELIGIBLE, FIRST, LAST, SynthPatient
from .history import Replayer, Stop, add_patient, patient_actor
from .results import SynthResult

TIERS = ["critical", "urgent", "high", "routine", "negative"]
PROGRAMMES = ["diabetes_hba1c", "breast", "cervical", "colorectal_fit"]


def _u(rng, a, b, nd=1):
    return round(rng.uniform(a, b), nd)


# ---- result categories (values from docs/clinical_rules.md; label -> generator) -------------------------------
def _cx(hpv, cyto=None, test="HPV"):
    return lambda rng: SynthResult(None, hpv, None, test, cytology=cyto)


CATEGORIES: dict[str, dict[str, object]] = {
    "colorectal_fit": {
        "FIT <80 ug/g": lambda r: SynthResult(_u(r, 0, 79.8), None, "ug/g", "FIT"),
        "FIT qualitative negative": lambda r: SynthResult(None, "negative", None, "FIT"),
        "FIT 80-399 ug/g": lambda r: SynthResult(round(r.uniform(80, 399)), None, "ug/g", "FIT"),
        "FIT qualitative positive": lambda r: SynthResult(None, "positive", None, "FIT"),
        "FIT >=400 ug/g": lambda r: SynthResult(round(min(3000, 400 * math.exp(r.expovariate(1.6)))), None, "ug/g", "FIT"),
        "FIT invalid/spoilt kit": lambda r: SynthResult(None, r.choice(["invalid", "spoilt", "insufficient"]), None, "FIT"),
    },
    "cervical": {
        "HPV negative": _cx("HPV negative"), "HPV negative + NILM": _cx("HPV negative", "NILM"),
        "HPV other, reflex pending": _cx("HPV other hr"), "HPV other + NILM": _cx("HPV other hr", "NILM"),
        "HPV other + unsatisfactory": _cx("HPV other hr", "unsatisfactory"),
        "cytology-only ASC-US": _cx("ASC-US", None, "CYTOLOGY"),
        "HPV16 + NILM": _cx("HPV16", "NILM"), "HPV18 + ASC-US": _cx("HPV18", "ASC-US"),
        "HPV other + LSIL": _cx("HPV other hr", "LSIL"), "HPV invalid + LSIL": _cx("HPV invalid", "LSIL"),
        "HPV negative + LSIL": _cx("HPV negative", "LSIL"),
        "HPV16 + HSIL": _cx("HPV16", "HSIL"), "HPV other + ASC-H": _cx("HPV other hr", "ASC-H"),
        "HPV18 + AGC": _cx("HPV18", "AGC"), "HPV16 + SCC": _cx("HPV16", "SCC"),
        "HPV negative + HSIL": _cx("HPV negative", "HSIL"), "HPV invalid": _cx("HPV invalid"),
    },
    "breast": {f"BI-RADS {c}": (lambda c: lambda r: SynthResult(None, f"BI-RADS {c}", None, "MAMMO"))(c)
               for c in ["0", "1", "2", "3", "4", "4A", "4B", "4C", "5", "6"]},
    "diabetes_hba1c": {
        "HbA1c normal %": lambda r: SynthResult(_u(r, 4.0, 5.6), None, "%", "HBA1C"),
        "HbA1c normal mmol/mol": lambda r: SynthResult(round(r.uniform(20, 38)), None, "mmol/mol", "HBA1C"),
        "FPG normal mg/dL": lambda r: SynthResult(round(r.uniform(70, 99)), None, "mg/dL", "FPG"),
        "FPG normal mmol/L": lambda r: SynthResult(_u(r, 3.9, 5.5), None, "mmol/L", "FPG"),
        "HbA1c prediabetes %": lambda r: SynthResult(_u(r, 5.7, 6.4), None, "%", "HBA1C"),
        "HbA1c prediabetes mmol/mol": lambda r: SynthResult(round(r.uniform(39, 47)), None, "mmol/mol", "HBA1C"),
        "FPG prediabetes mg/dL": lambda r: SynthResult(round(r.uniform(100, 125)), None, "mg/dL", "FPG"),
        "HbA1c diabetes range %": lambda r: SynthResult(_u(r, 6.5, 9.9), None, "%", "HBA1C"),
        "HbA1c diabetes range mmol/mol": lambda r: SynthResult(round(r.uniform(48, 85)), None, "mmol/mol", "HBA1C"),
        "FPG diabetes range mmol/L": lambda r: SynthResult(_u(r, 7.0, 16.6), None, "mmol/L", "FPG"),
        "HbA1c unexpectedly low": lambda r: SynthResult(_u(r, 3.0, 3.9), None, "%", "HBA1C"),
        "HbA1c high %": lambda r: SynthResult(_u(r, 10.0, 14.9), None, "%", "HBA1C"),
        "HbA1c high mmol/mol": lambda r: SynthResult(round(r.uniform(86, 139)), None, "mmol/mol", "HBA1C"),
        "FPG high mg/dL": lambda r: SynthResult(round(r.uniform(300, 499)), None, "mg/dL", "FPG"),
        "HbA1c critical %": lambda r: SynthResult(_u(r, 15.0, 19.5), None, "%", "HBA1C"),
        "HbA1c critical mmol/mol": lambda r: SynthResult(round(r.uniform(140, 190)), None, "mmol/mol", "HBA1C"),
        "FPG critical mg/dL": lambda r: SynthResult(round(r.uniform(500, 900)), None, "mg/dL", "FPG"),
        "FPG critical mmol/L": lambda r: SynthResult(_u(r, 27.7, 45), None, "mmol/L", "FPG"),
        "FPG hypoglycaemic mg/dL": lambda r: SynthResult(round(r.uniform(30, 53)), None, "mg/dL", "FPG"),
        "FPG hypoglycaemic mmol/L": lambda r: SynthResult(_u(r, 1.7, 2.9), None, "mmol/L", "FPG"),
    },
}
# values exactly at / either side of every cut-off (clinical_rules.md: the cut-off value is in the HIGHER category)
BOUNDARY: dict[str, list[SynthResult]] = {
    "colorectal_fit": [SynthResult(v, None, "ug/g", "FIT") for v in (79.9, 80.0, 80.1, 399.9, 400.0, 400.1)],
    "diabetes_hba1c": [SynthResult(v, None, "%", "HBA1C") for v in (3.9, 4.0, 5.6, 5.7, 6.4, 6.5, 9.9, 10.0, 14.9, 15.0)]
    + [SynthResult(v, None, "mmol/mol", "HBA1C") for v in (38.0, 38.7, 39.0, 47.4, 47.5, 48.0, 85.6, 85.7, 86.0, 139.8, 139.9, 140.0)]
    + [SynthResult(v, None, "mg/dL", "FPG") for v in (53.0, 54.0, 99.0, 100.0, 125.0, 126.0, 299.0, 300.0, 499.0, 500.0)]
    + [SynthResult(v, None, "mmol/L", "FPG") for v in (2.9, 3.0, 5.5, 5.6, 6.9, 7.0, 16.6, 16.7, 27.6, 27.7)],
}
NEUTRAL = {"colorectal_fit": PatientContext(age=55, sex="male"), "diabetes_hba1c": PatientContext(age=50, sex="male"),
           "cervical": PatientContext(age=40, sex="female"), "breast": PatientContext(age=55, sex="female")}


def severity(programme: str, r: SynthResult) -> str:
    """The RULES ENGINE's severity for the clean result in a neutral eligible context."""
    d = evaluate(ResultInput(programme, r.value, r.value_text, r.unit, r.test, r.status, r.cytology), NEUTRAL[programme])
    return "negative" if d.disposition == "negative" else (d.tier or "urgent")  # the tier the case is worked at


def _bucket() -> dict[str, dict[str, list[str]]]:
    rng, out = random.Random(1), {}
    for prog, cats in CATEGORIES.items():
        b: dict[str, set[str]] = {}
        for label, fn in cats.items():
            for _ in range(40):
                b.setdefault(severity(prog, fn(rng)), set()).add(label)
        out[prog] = {t: sorted(v) for t, v in b.items()}
    return out


BUCKETS = _bucket()  # programme -> tier -> category labels that can produce it
SUPPORTED_TIERS = {p: [t for t in TIERS if t in BUCKETS[p]] for p in PROGRAMMES}

# ---- scenarios -------------------------------------------------------------------------------------------------
NEEDS_CASE = {"died_mid_pathway", "consent_withdrawn", "reopened", "no_show", "repeated_reschedule", "out_of_area_move"}
SCENARIOS: list[tuple[str, float]] = [
    ("baseline", 4.0), ("boundary_value", 1.5), ("multimorbidity", 1), ("duplicate_result", 1), ("conflicting_result", 1),
    ("missing_value", 1), ("unknown_value", 1), ("unit_mismatch", 1), ("amended_result", 1), ("preliminary_then_final", 1),
    ("pregnancy", 1), ("age_youngest", 1), ("age_oldest", 1), ("haemoglobinopathy", 1), ("specialist_care", 1),
    ("deceased_before_result", 1), ("died_mid_pathway", 1), ("consent_withdrawn", 1), ("reopened", 1),
    ("interpreter_need", 1), ("no_show", 1.5), ("repeated_reschedule", 1.5), ("out_of_area_move", 1),
    ("ramadan_holiday", 1), ("stale_specimen", 1), ("future_specimen", 1), ("malformed_hl7", 1.5),
    ("duplicate_identity", 1),
]


def not_applicable(prog: str, tier: str, scn: str) -> str | None:
    """Why a (programme, severity, scenario) cell is NOT a supported combination (None = supported)."""
    if tier not in SUPPORTED_TIERS[prog]:
        return "tier not produced by the rules for this programme"
    if scn in NEEDS_CASE and tier == "negative":
        return "needs an open case; a negative result creates none"
    if scn in ("boundary_value", "unit_mismatch") and prog in ("breast", "cervical"):
        return "categorical result: no numeric cut-off or unit"
    if scn == "boundary_value" and prog == "colorectal_fit" and tier == "routine":
        return "spoilt-kit category has no numeric cut-off"
    if scn in ("no_show", "repeated_reschedule") and prog == "diabetes_hba1c" and tier in ("routine", "negative", "urgent"):
        return "GP-led / review pathway: no specialist appointment is booked"
    if scn in ("no_show", "repeated_reschedule") and prog == "cervical" and tier == "routine":
        return "routine cervical pathways are repeat test / surveillance, no specialist appointment"
    return None


LANGS = ["en", "ar", "hi", "ur", "ml", "tl"]  # 4 supported content languages + 2 needing an interpreter
NAT_FOR_LANG = {"en": ["Western", "Indian", "Other"], "ar": ["Emirati", "Other Arab"], "hi": ["Indian"], "ur": ["Pakistani"],
                "ml": ["Indian"], "tl": ["Filipino"]}
LOINC = {"FIT": ("29771-3", "FIT haemoglobin"), "HPV": ("77379-6", "HPV high risk"), "CYTOLOGY": ("10524-7", "Cytology"),
         "MAMMO": ("24606-6", "Mammogram"), "HBA1C": ("4548-4", "HbA1c"), "FPG": ("1558-6", "Fasting glucose")}
MALFORMED = ["bad_delimiters", "not_hl7", "unknown_loinc", "missing_pid", "two_pids", "dob_mismatch", "bom_mllp",
             "sn_comparator"]
# Ramadan 2026 and the days before UAE public holidays (calendar_uae.py)
RAMADAN_HOLIDAY_WINDOWS = [(date(2026, 2, 18), date(2026, 3, 19)), (date(2026, 3, 17), date(2026, 3, 19)),
                           (date(2026, 5, 23), date(2026, 5, 25)), (date(2025, 11, 28), date(2025, 11, 30))]


@dataclass
class EdgePlan:
    programme: str
    tier: str
    category: str
    scenario: str
    result: SynthResult


def plan_cohort(n: int, seed: int) -> list[tuple[SynthPatient, EdgePlan]]:
    rng = random.Random(seed)
    out = []
    names = [x for x, _ in SCENARIOS]
    weights = [w for _, w in SCENARIOS]
    for i in range(n):
        prog = PROGRAMMES[i % 4] if rng.random() < 0.8 else rng.choice(PROGRAMMES)
        scn = rng.choices(names, weights)[0]
        tiers = [t for t in SUPPORTED_TIERS[prog] if not_applicable(prog, t, scn) is None]
        if not tiers:  # e.g. boundary for breast: keep the scenario mix honest -> plain baseline instead
            scn, tiers = "baseline", SUPPORTED_TIERS[prog]
        tier = rng.choice(tiers)
        if scn == "boundary_value":
            cands = [b for b in BOUNDARY[prog] if severity(prog, b) == tier]
            if cands:
                r = rng.choice(cands)
                cat = f"boundary {r.value:g} {r.unit}"
            else:
                scn = "baseline"
        if scn != "boundary_value":
            for _ in range(200):
                cat = rng.choice(BUCKETS[prog][tier])
                r = CATEGORIES[prog][cat](rng)
                if severity(prog, r) == tier:
                    break
        gender = "female" if prog in ("cervical", "breast") else rng.choice(["male", "female"])
        lo, hi = ELIGIBLE[prog]
        hi = min(hi, 84)
        age = rng.randint(max(lo, 40 if scn == "multimorbidity" else lo), hi)
        if scn == "pregnancy":
            gender = "female"
            age = rng.randint(max(lo, 18), min(hi, 45))
        elif scn == "age_youngest":
            age = rng.choice([lo, lo, lo + 1, lo - 1]) if prog != "diabetes_hba1c" else rng.choice([18, 18, 19, 17])
        elif scn == "age_oldest":
            age = rng.choice([hi, hi, hi - 1, hi + 1]) if prog in ("colorectal_fit", "cervical") else rng.randint(85, 95)
        if scn == "multimorbidity" and prog == "diabetes_hba1c" and gender == "male":
            age = max(age, 40)  # eligible for FIT as a second programme
        lang = rng.choice(["ml", "tl"]) if scn == "interpreter_need" else rng.choice(LANGS)
        nat = rng.choice(NAT_FOR_LANG[lang])
        m, f = FIRST[nat]
        engaged = scn in ("no_show", "repeated_reschedule", "reopened")
        sp = SynthPatient(
            idx=200000 + i, given=rng.choice(f if gender == "female" else m), family=rng.choice(LAST[nat]), gender=gender,
            age=age, nationality=nat, language=lang, region=rng.choice(["Abu Dhabi", "Abu Dhabi", "Al Ain", "Al Dhafra"]),
            phone_valid=engaged or rng.random() < 0.92, has_smartphone=rng.random() < 0.75, whatsapp=rng.random() < 0.7,
            alt_contact=rng.random() < 0.35, prior_no_shows=rng.choices([0, 1, 2, 3], [70, 18, 8, 4])[0],
            prior_screens=rng.randint(0, 4), responsiveness=1.0 if engaged else round(rng.uniform(0.2, 1.0), 3),
            programme=prog, share_consent=engaged or rng.random() > 0.04, analytics_consent=rng.random() > 0.02,
            pregnant=scn == "pregnancy", haemoglobinopathy=scn == "haemoglobinopathy",
            specialist_care=scn == "specialist_care", deceased=scn == "deceased_before_result",
            out_of_age=scn in ("age_youngest", "age_oldest") and not (lo <= age <= ELIGIBLE[prog][1]))
        out.append((sp, EdgePlan(prog, tier, cat, scn, r)))
    return out


def _mutate(rng, prog: str, r: SynthResult, scn: str) -> SynthResult:
    if scn == "missing_value":
        return replace(r, value=None, value_text=None, cytology=None)
    if scn == "unknown_value":
        return replace(r, value=None, value_text=rng.choice(["see comment", "result to follow", "#VALUE!", "?"]), cytology=None)
    if scn == "unit_mismatch":
        if prog == "colorectal_fit":
            return replace(r, value=r.value if r.value is not None else 120.0, value_text=None, unit="ng/mL")
        if r.test == "FPG":  # mg/dL number labelled mmol/L (or the reverse)
            return replace(r, unit="mmol/L" if r.unit == "mg/dL" else "mg/dL")
        if r.unit == "%":  # IFCC number labelled %
            return replace(r, value=round((r.value - 2.152) / 0.09148), unit="%")
        return replace(r, value=round(r.value * 0.09148 + 2.152, 1), unit="mmol/mol")
    if scn == "amended_result":
        return replace(r, status=rng.choice(["amended", "corrected"]))
    if scn == "preliminary_then_final":
        return replace(r, status="preliminary")
    return r


def hl7_message(p: Patient, r: SynthResult, ctrl: str, *, collected: datetime | None, variant: str | None = None,
                birth_year: int | None = None) -> str:
    ts = (collected or datetime(2026, 1, 1)).strftime("%Y%m%d%H%M") + "+0000"
    code, disp = LOINC.get(r.test or "", ("29771-3", "FIT"))
    if variant == "unknown_loinc":
        code = "99999-9"
    st = {"final": "F", "amended": "A", "corrected": "C", "preliminary": "P"}.get(r.status, "F")
    segs = [f"MSH|^~\\&|LIS|CENTRAL_LAB|KARESHAFAA|PROG|{ts}||ORU^R01^ORU_R01|{ctrl}|P|2.5.1"]
    pid = f"PID|1||{p.mrn}^^^KARESHAFAA^MR~{p.national_id}^^^UAE^NI||{p.family_name.upper()}^{p.given_name.upper()}||" \
          f"{birth_year or p.birth_year}0101|{p.gender[0].upper()}"
    if variant != "missing_pid":
        segs.append(pid)
    if variant == "two_pids":
        segs.append("PID|2||SYN-OTHER^^^KARESHAFAA^MR||OTHER^PERSON||19700101|F")
    segs.append(f"OBR|1|ORD-{ctrl}|FIL-{ctrl}|{code}^{disp}^LN|||{ts}")
    if r.value is not None:
        if variant == "sn_comparator":
            segs.append(f"OBX|1|SN|{code}^{disp}^LN||>^{r.value:g}|{r.unit or ''}|||||{st}|||{ts}")
        else:
            segs.append(f"OBX|1|NM|{code}^{disp}^LN||{r.value:g}|{r.unit or ''}|||||{st}|||{ts}")
    else:
        segs.append(f"OBX|1|ST|{code}^{disp}^LN||{r.value_text or ''}||||||{st}|||{ts}")
    if r.cytology:
        segs.append(f"OBX|2|ST|10524-7^Cytology^LN||{r.cytology}||||||{st}|||{ts}")
    msg = "\r".join(segs)
    if variant == "bad_delimiters":
        msg = msg.replace("MSH|^~\\&|", "MSH|^^^^|", 1)
    if variant == "not_hl7":
        msg = "LAB RESULT " + msg.replace("\r", " / ")
    if variant == "bom_mllp":
        msg = "\ufeff\x0b" + msg + "\x1c\r"
    return msg


class EdgeReplayer(Replayer):
    """Replays edge-case journeys; overrides outcomes for scenario patients through Replayer.force()."""

    def __init__(self, *a, **kw):
        super().__init__(*a, enriched=False, **kw)
        self.scn: str | None = None
        self.manifest: list[dict] = []
        self.failures: dict[str, int] = {}

    def force(self, key, default, **kw):
        if self.scn in ("no_show", "repeated_reschedule", "reopened"):
            forced = {"reach": True, "silent": False, "decline": False, "tb_pass": True, "confirm_dna": False,
                      "confirm_pos": True, "refer_dm": True, "norebook": False}
            if key in forced:
                return forced[key]
            if key == "dna" and self.scn != "reopened":
                return kw.get("visit", 0) < (1 if self.scn == "no_show" else 2)
        return default

    def _fail(self, what: str) -> None:
        self.failures[what] = self.failures.get(what, 0) + 1

    def _lead(self, prog: str):
        return self.a(self.d["leads"][prog])

    def _ingest_direct(self, p: Patient, prog: str, r: SynthResult, t: datetime) -> Case | None:
        _, case = wf.ingest_result(self.db, p, prog, value=r.value, value_text=r.value_text, unit=r.unit, test=r.test,
                                   status=r.status, cytology=r.cytology, performer_org_id=self.d["lab"].id, now=t)
        if case is not None and case.tier not in ("critical", "urgent") and S(case.state) == S.ISSUED \
                and not case.review_required:
            wf.transition(self.db, case, S.TRIAGED, SYSTEM, now=t, note="auto-triaged by deterministic programme rule")
        return case

    def _ingest_hl7(self, p: Patient, r: SynthResult, t: datetime, ctrl: str, **kw) -> tuple[Case | None, object]:
        try:
            parsed = ing.parse_hl7v2(hl7_message(p, r, ctrl, **kw))
        except DomainError:
            return None, "rejected"
        msg = ing.ingest(self.db, "hl7v2", parsed, SYSTEM, now=t)
        return (self.db.get(Case, msg.case_id) if msg.case_id else None), msg

    def _run_journey(self, case: Case, p: Patient, sp: SynthPatient, r: SynthResult) -> None:
        try:
            self._journey_with_followups(case, p, sp, r)
        except Stop:
            pass

    def run_edge(self, p: Patient, sp: SynthPatient, plan: EdgePlan, t0: datetime) -> dict:
        rng, db, scn, prog = self.rng, self.db, plan.scenario, plan.programme
        self.scn, self.t, self.horizon = scn, t0, None
        rec = {"patient_id": p.id, "programme": prog, "tier": plan.tier, "category": plan.category, "scenario": scn,
               "applied": False}
        r = _mutate(rng, prog, plan.result, scn)
        case = None
        if scn in ("stale_specimen", "future_specimen"):
            coll = t0 - timedelta(days=rng.uniform(181, 700)) if scn == "stale_specimen" else \
                t0 + timedelta(days=rng.choice([rng.uniform(2, 30), 365]))
            case, msg = self._ingest_hl7(p, r, t0, f"EDGE-{p.id}", collected=coll)
            rec["applied"] = bool(case is not None and {"stale_result", "collection_date_in_future"} & set(case.review_reasons or []))
        elif scn == "malformed_hl7":
            variant = MALFORMED[p.id % len(MALFORMED)]
            if variant == "sn_comparator" and r.value is None:
                variant = "unknown_loinc"
            rec["variant"] = variant
            by = (p.birth_year - 7) if variant == "dob_mismatch" else None
            case, msg = self._ingest_hl7(p, r, t0, f"EDGE-{p.id}-M", collected=t0 - timedelta(hours=6), variant=variant,
                                         birth_year=by)
            rec["held"] = msg if isinstance(msg, str) else msg.status
            rec["applied"] = True
            if case is None:  # rejected or held: the lab resends / a coordinator resolves; nothing is lost
                self.t = t0 + timedelta(hours=self.ln(20))
                if self.t <= self.now:
                    if not isinstance(msg, str) and msg.status == "needs_match_review":
                        coord = self.a(self.d["coordinators"][prog][0])
                        msg = ing.resolve_match(db, msg.id, p.id, coord, now=self.t)
                        case = db.get(Case, msg.case_id) if msg.case_id else None
                    else:
                        case, _ = self._ingest_hl7(p, r, self.t, f"EDGE-{p.id}-R", collected=t0 - timedelta(hours=6))
        else:
            case = self._ingest_direct(p, prog, r, t0)
            rec["applied"] = scn in ("baseline", "boundary_value", "missing_value", "unknown_value", "unit_mismatch",
                                     "amended_result", "preliminary_then_final", "pregnancy", "age_youngest",
                                     "age_oldest", "haemoglobinopathy", "specialist_care", "deceased_before_result",
                                     "interpreter_need", "ramadan_holiday")
        rec["case_id"] = case.id if case else None
        if scn in ("duplicate_result", "conflicting_result", "duplicate_identity"):
            self.t = t0 + timedelta(hours=self.ln(30))
            if self.t <= self.now:
                if scn == "duplicate_result":
                    self._ingest_direct(p, prog, r, self.t)
                elif scn == "conflicting_result":
                    other = [t for t in SUPPORTED_TIERS[prog] if t != plan.tier]
                    ot = rng.choice(other)
                    for _ in range(100):
                        r2 = CATEGORIES[prog][rng.choice(BUCKETS[prog][ot])](rng)
                        if severity(prog, r2) == ot:
                            break
                    rec["second_tier"] = ot
                    self._ingest_direct(p, prog, r2, self.t)
                else:
                    sp2 = replace(sp, idx=sp.idx + 300000, given=sp.given + "a")
                    p2 = add_patient(db, sp2, self.d, rng, self.now, age_at=t0)
                    p2.national_id = p.national_id
                    db.flush()
                    c2 = self._ingest_direct(p2, prog, r, self.t)
                    rec["second_case_id"] = c2.id if c2 else None
                rec["applied"] = True
            self.t = t0
        if scn == "multimorbidity":
            others = [q for q, (lo, hi) in ELIGIBLE.items() if q != prog and lo <= sp.age <= hi
                      and not (q in ("cervical", "breast") and sp.gender != "female")]
            extra = []
            for q in rng.sample(others, min(len(others), rng.choice([1, 1, 2]))):
                tq = rng.choice([t for t in SUPPORTED_TIERS[q] if t != "negative"])
                for _ in range(100):
                    rq = CATEGORIES[q][rng.choice(BUCKETS[q][tq])](rng)
                    if severity(q, rq) == tq:
                        break
                cq = self._ingest_direct(p, q, rq, t0 + timedelta(hours=rng.uniform(0, 72)))
                if cq is not None:
                    extra.append(cq.id)
                    try:
                        self.t = cq.created_at
                        self._run_journey(cq, p, replace(sp, programme=q), rq)
                    except DomainError:
                        self._fail("multimorbidity_journey")
            rec["extra_cases"] = extra
            rec["applied"] = bool(extra)
            self.t = t0
        if case is None:
            self.manifest.append(rec)
            return rec
        if scn in ("died_mid_pathway", "consent_withdrawn", "out_of_area_move"):
            self.horizon = t0 + timedelta(hours=rng.uniform(4, 24 * 20))
        self.t = max(self.t, case.created_at)
        try:
            self._run_journey(case, p, sp, r)
        except DomainError:
            self._fail("journey:" + scn)
        cut, self.horizon = self.horizon, None
        try:
            self._after(case, p, sp, plan, rec, cut)
        except Stop:
            pass
        except DomainError as e:
            self._fail(f"{scn}:{type(e).__name__}")
        self.manifest.append(rec)
        return rec

    def _after(self, case: Case, p: Patient, sp: SynthPatient, plan: EdgePlan, rec: dict, cut: datetime | None) -> None:
        db, rng, scn = self.db, self.rng, plan.scenario
        lead = self._lead(case.programme)
        if scn in ("died_mid_pathway", "consent_withdrawn", "out_of_area_move"):
            if S(case.state) == S.CLOSED or cut is None or cut > self.now:
                return  # the journey finished before the event (counted as not applied)
            self.t = cut
            if scn == "died_mid_pathway":
                wf.record_death(db, p, lead, "Death registry notification received.", now=self.t)
                rec["applied"] = True
                self.adv(self.ln(48))
                if rng.random() < 0.85:
                    for c in db.scalars(select(Case).where(Case.patient_id == p.id, Case.state != S.CLOSED)):
                        wf.close_case(db, c, lead, "authorised_decision", reason="deceased",
                                      note="Death recorded from the registry; GP informed.", now=self.t)
            elif scn == "consent_withdrawn":
                wf.record_consent(db, p, patient_actor(p), "care_coordination", "deny",
                                  note="Patient asked to stop care coordination.", now=self.t)
                rec["applied"] = True
                self.adv(self.ln(48))
                if rng.random() < 0.6 and S(case.state) in (S.PATIENT_NOTIFIED, S.ACKNOWLEDGED, S.CONFIRMATORY_TEST,
                                                            S.CONSULTATION, S.NO_SHOW):
                    gp = self.gp(case)
                    wf.record_decline(db, case, gp, "Patient confirmed by phone they decline further follow-up now.",
                                      now=self.t)
                    self.adv(self.ln(24))
                    wf.close_case(db, case, gp, "authorised_decision", reason="informed_decline",
                                  note="Informed decline after consent withdrawal; risks explained; GP informed.",
                                  now=self.t)
            else:
                if rng.random() < 0.7:
                    other = [o for o in self.d["phc"] if o.region != p.region] or self.d["phc"]
                    org = rng.choice(other)
                    to = self.d["gps"][org.id][0]
                    wf.request_transfer(db, case, to.id, self.coord(case),
                                        "Patient moved to another emirate region; registered with a new health centre.",
                                        now=self.t)
                    rec["applied"] = True
                    self.adv(self.ln(36))
                    if rng.random() < 0.8:
                        wf.decide_transfer(db, case, self.a(to), True, "Accepted: patient registered with us.", now=self.t)
                        rec["transfer_accepted"] = True
                else:
                    wf.close_case(db, case, lead, "authorised_decision", reason="moved_abroad",
                                  note="Patient moved abroad; result summary and referral letter given.",
                                  now=self.t)
                    rec["applied"] = True
        elif scn == "reopened":
            if S(case.state) != S.CLOSED or case.closed_at is None:
                return
            self.t = case.closed_at
            self.adv(24 * rng.uniform(3, 45))
            wf.transition(db, case, S.TRIAGED, lead, note="Re-opened: patient reports new symptoms; GP asks for review "
                                                           "of the closed pathway.", now=self.t)
            rec["applied"] = True
        elif scn in ("no_show", "repeated_reschedule"):
            from ..models import Appointment

            n = db.query(Appointment).filter(Appointment.case_id == case.id, Appointment.status == "noshow").count()
            rec["no_shows"] = n
            rec["applied"] = n >= (1 if scn == "no_show" else 2)


def edge_t0(rng: random.Random, scn: str, now: datetime) -> datetime:
    if scn == "ramadan_holiday":
        a, b = rng.choice(RAMADAN_HOLIDAY_WINDOWS)
        d = a + timedelta(days=rng.randint(0, (b - a).days))
        return to_utc(datetime(d.year, d.month, d.day, rng.randint(7, 15), rng.randint(0, 59)))
    return now - timedelta(days=rng.uniform(0.5, 300))
