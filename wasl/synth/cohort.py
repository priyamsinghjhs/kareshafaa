"""Synthetic patient cohort. Distributions are ASSUMPTIONS chosen to exercise the workflow;
they are not statistics about any real population."""
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
    r, acc = rng.random(), 0.0
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

    @property
    def age_band(self) -> str:
        return age_band(self.age)


def eligible_programmes(gender: str, age: int) -> list[tuple[str, float]]:
    out = [("diabetes_hba1c", .30)]
    if age >= 40:
        out.append(("colorectal_fit", .30))
    if gender == "female" and 25 <= age <= 65:
        out.append(("cervical", .25))
    if gender == "female" and 40 <= age <= 69:
        out.append(("breast", .25))
    tot = sum(w for _, w in out)
    return [(k, w / tot) for k, w in out]


def make_cohort(n: int, seed: int) -> list[SynthPatient]:
    rng = random.Random(seed)
    out = []
    for i in range(n):
        nat = pick(rng, NATIONALITY)
        lang = pick(rng, LANG_BY_NAT[nat])
        gender = "female" if rng.random() < 0.52 else "male"
        age = int(min(75, max(25, rng.gauss(48, 12))))
        region = pick(rng, REGIONS)
        worker = nat in ("Indian", "Pakistani", "Filipino") and rng.random() < 0.6  # lower-income worker profile
        has_smart = rng.random() < (0.55 if worker else 0.9) - (0.15 if age >= 60 else 0)
        phone_valid = rng.random() < (0.86 if worker else 0.95)
        whatsapp = phone_valid and rng.random() < (0.95 if worker else 0.8)
        resp = min(1.0, max(0.05, rng.betavariate(5, 2) - (0.12 if age < 40 else 0) - (0.08 if region == "Al Dhafra" else 0)))
        prior_ns = rng.choices([0, 1, 2, 3], weights=[70, 18, 8, 4])[0]
        prior_scr = rng.choices([0, 1, 2, 3, 4], weights=[25, 25, 20, 15, 15])[0]
        programme = pick(rng, eligible_programmes(gender, age))
        m, f = FIRST[nat]
        out.append(SynthPatient(
            idx=i, given=rng.choice(f if gender == "female" else m), family=rng.choice(LAST[nat]), gender=gender, age=age,
            nationality=nat, language=lang, region=region, phone_valid=phone_valid, has_smartphone=has_smart,
            whatsapp=whatsapp, alt_contact=rng.random() < 0.35, prior_no_shows=prior_ns, prior_screens=prior_scr,
            responsiveness=round(resp, 3), programme=programme, share_consent=rng.random() > 0.04,
            analytics_consent=rng.random() > 0.02))
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
