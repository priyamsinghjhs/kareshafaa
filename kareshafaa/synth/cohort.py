"""Synthetic patient cohort. Sex ratio follows the Abu Dhabi Census 2023; eligibility ages follow the
Department of Health Abu Dhabi standards; other shares are labelled ASSUMPTIONS in docs/synthetic_data.md.
Nothing here describes a real person."""
from __future__ import annotations

import random
from dataclasses import dataclass

# Abu Dhabi-like mix of a screening-eligible population (ASSUMED shares, broadly reflecting a majority-expatriate
# emirate with large Indian, Pakistani and Filipino communities; not official statistics).
NATIONALITY = [("Emirati", .20), ("Indian", .28), ("Pakistani", .12), ("Other Arab", .14), ("Filipino", .10),
               ("Western", .06), ("Other", .10)]
LANG_BY_NAT = {
    "Emirati": [("ar", .93), ("en", .07)], "Other Arab": [("ar", .88), ("en", .12)],
    "Indian": [("hi", .42), ("ml", .33), ("en", .25)],  # Malayalam: large Keralite community
    "Pakistani": [("ur", .86), ("en", .14)], "Filipino": [("tl", .60), ("en", .40)],
    "Western": [("en", 1.0)], "Other": [("en", .65), ("ar", .20), ("ur", .15)],
}
REGIONS = [("Abu Dhabi", .60), ("Al Ain", .28), ("Al Dhafra", .12)]
FIRST = {
    "Emirati": (["Ahmed", "Mohammed", "Saeed", "Khalid", "Hamad", "Sultan"], ["Fatima", "Maryam", "Shamma", "Hessa", "Moza", "Latifa"]),
    "Other Arab": (["Omar", "Yousef", "Tarek", "Karim", "Sami"], ["Lina", "Rania", "Dina", "Hala", "Nour"]),
    "Indian": (["Rajesh", "Suresh", "Arun", "Ramesh", "Vijay", "Anil"], ["Priya", "Sunita", "Deepa", "Lakshmi", "Anjali", "Meena"]),
    "Pakistani": (["Imran", "Bilal", "Faisal", "Usman", "Tariq"], ["Ayesha", "Farah", "Sana", "Nadia", "Rabia"]),
    "Filipino": (["Jose", "Mark", "Paolo", "Ramon"], ["Maria", "Grace", "Joy", "Liza"]),
    "Western": (["James", "Thomas", "Daniel", "Lukas"], ["Emma", "Sophie", "Anna", "Claire"]),
    "Other": (["Ali", "David", "Kofi", "Ivan"], ["Amina", "Elena", "Mei", "Zara"]),
}
LAST = {
    "Emirati": ["Al Mazrouei", "Al Kaabi", "Al Shamsi", "Al Nuaimi", "Al Dhaheri"], "Other Arab": ["Haddad", "Khoury", "Mansour", "Saleh"],
    "Indian": ["Sharma", "Nair", "Patel", "Kumar", "Menon", "Pillai"],
    "Pakistani": ["Khan", "Iqbal", "Hussain", "Malik", "Qureshi"], "Filipino": ["Santos", "Reyes", "Cruz", "Bautista"],
    "Western": ["Smith", "Muller", "Brown", "Rossi"], "Other": ["Mensah", "Petrov", "Chen", "Okafor"],
}


def pick(rng: random.Random, pairs):
    tot = sum(w for _, w in pairs)  # normalised: a table that does not sum to 1 must not bias the last item
    r, acc = rng.random() * tot, 0.0
    for k, w in pairs:
        acc += w
        if r <= acc:
            return k
    return pairs[-1][0]


def age_band(age: int) -> str:
    return "25-39" if age < 40 else "40-49" if age < 50 else "50-59" if age < 60 else "60-75"


@dataclass
class SynthPatient:
    idx: int
    given: str
    family: str
    gender: str
    age: int
    nationality: str
    language: str
    region: str
    phone_valid: bool
    has_smartphone: bool
    whatsapp: bool
    alt_contact: bool
    prior_no_shows: int
    prior_screens: int
    responsiveness: float
    programme: str
    share_consent: bool
    analytics_consent: bool
    pregnant: bool = False
    haemoglobinopathy: bool = False
    specialist_care: bool = False
    deceased: bool = False
    out_of_age: bool = False

    @property
    def age_band(self) -> str:
        return age_band(self.age)


FEMALE_SHARE = 0.33  # Abu Dhabi Census 2023: 1,248,395 of 3,789,860 residents are female
AGE_BANDS = [((18, 24), .10), ((25, 34), .30), ((35, 44), .28), ((45, 54), .17), ((55, 64), .10), ((65, 74), .04),
             ((75, 84), .01)]  # ASSUMPTION: adult residents, shaped by the census median age of 33
ELIGIBLE = {"colorectal_fit": (40, 75), "cervical": (25, 65), "breast": (40, 84), "diabetes_hba1c": (18, 120)}  # DoH
PROGRAMME_WEIGHT = {"diabetes_hba1c": .40, "colorectal_fit": .25, "cervical": .20, "breast": .15}  # ASSUMPTION
OUT_OF_AGE = 0.01  # ASSUMPTION: opportunistic screening outside the programme age range


def eligible_programmes(gender: str, age: int) -> list[tuple[str, float]]:
    out = []
    for k, (lo, hi) in ELIGIBLE.items():
        if k in ("cervical", "breast") and gender != "female":
            continue
        if lo <= age <= hi:
            out.append((k, PROGRAMME_WEIGHT[k]))
    tot = sum(w for _, w in out)
    return [(k, w / tot) for k, w in out]


def make_cohort(n: int, seed: int, *, min_age: int = 25) -> list[SynthPatient]:
    """min_age=25 keeps the historical demo/simulator cohort; seed-large uses 18 (all adults)."""
    rng = random.Random(seed)
    out = []
    for i in range(n):
        nat = pick(rng, NATIONALITY)
        lang = pick(rng, LANG_BY_NAT[nat])
        gender = "female" if rng.random() < FEMALE_SHARE else "male"
        while True:
            lo, hi = pick(rng, AGE_BANDS)
            age = rng.randint(lo, hi)
            if age >= min_age:
                break
        region = pick(rng, REGIONS)
        worker = nat in ("Indian", "Pakistani", "Filipino") and rng.random() < 0.6  # lower-income worker profile
        has_smart = rng.random() < (0.55 if worker else 0.9) - (0.15 if age >= 60 else 0)
        phone_valid = rng.random() < (0.86 if worker else 0.95)
        whatsapp = phone_valid and rng.random() < (0.95 if worker else 0.8)
        resp = min(1.0, max(0.05, rng.betavariate(5, 2) - (0.12 if age < 40 else 0) - (0.08 if region == "Al Dhafra" else 0)))
        prior_ns = rng.choices([0, 1, 2, 3], weights=[70, 18, 8, 4])[0]
        prior_scr = rng.choices([0, 1, 2, 3, 4], weights=[25, 25, 20, 15, 15])[0]
        out_of_age = rng.random() < OUT_OF_AGE
        if out_of_age:  # screened outside the programme age range (must go to human review, never be dropped)
            cands = [k for k, (lo_, hi_) in ELIGIBLE.items() if not (lo_ <= age <= hi_)
                     and not (k in ("cervical", "breast") and gender != "female")]
            programme = rng.choice(cands) if cands else pick(rng, eligible_programmes(gender, age))
            out_of_age = bool(cands)
        else:
            programme = pick(rng, eligible_programmes(gender, age))
        m, f = FIRST[nat]
        out.append(SynthPatient(
            idx=i, given=rng.choice(f if gender == "female" else m), family=rng.choice(LAST[nat]), gender=gender, age=age,
            nationality=nat, language=lang, region=region, phone_valid=phone_valid, has_smartphone=has_smart,
            whatsapp=whatsapp, alt_contact=rng.random() < 0.35, prior_no_shows=prior_ns, prior_screens=prior_scr,
            responsiveness=round(resp, 3), programme=programme, share_consent=rng.random() > 0.04,
            analytics_consent=rng.random() > 0.02,
            pregnant=gender == "female" and 18 <= age <= 45 and programme in ("diabetes_hba1c", "cervical")
            and rng.random() < 0.015,                                        # ASSUMPTION
            haemoglobinopathy=rng.random() < 0.05,                           # ASSUMPTION (carrier states common in the Gulf)
            specialist_care=rng.random() < 0.003,                            # ASSUMPTION
            deceased=rng.random() < 0.0005,                                  # ASSUMPTION (death before result handling)
            out_of_age=out_of_age))
    return out


def risk_multiplier(p: SynthPatient) -> float:
    """Latent drop-off propensity used by the history generator and the simulator (assumption)."""
    m = 1.0
    m *= 1.0 + 0.9 * (1 - p.responsiveness)
    m *= 1.25 if not p.has_smartphone else 1.0
    m *= 1.0 + 0.25 * p.prior_no_shows
    m *= 1.2 if p.age < 40 else 1.0
    m *= 1.25 if p.region == "Al Dhafra" else 1.0
    m *= 1.3 if p.language in ("tl", "ml") else 1.0
    m *= 0.85 if p.prior_screens >= 2 else 1.0
    return m
