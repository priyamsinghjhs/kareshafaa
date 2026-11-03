"""Patient registration and the patient record (FHIR Patient): create, find, edit, duplicate check.

Who may do what (server-side, kareshafaa/security.py):
* patient:read     - coordinator, GP, specialist, clinical lead (org scoping below)
* patient:register - coordinator, GP (front-line registration; a clinical lead or admin does not register people)
* patient:write    - coordinator, GP (demographics, contact, insurance, next of kin)
* admin manages USER ACCOUNTS only and never sees or edits clinical/patient data.

Access scoping: programme-wide roles (coordinator, clinical lead) see every patient; a GP sees patients of their own
organisation; a GP or specialist also sees a patient whose case they may view (same rules as the case screen).

Identity safety:
* An Emirates ID already on another record blocks registration (one person, one record) and shows that record.
* Same date of birth (or birth year on older records) + a similar name is a POSSIBLE duplicate: registration stops
  with the candidates listed; the user may continue only with a written reason ("create anyway"), which is audited.
* birth_year and age_band are always derived from the full date of birth, so the rules engine, analytics and the
  outreach model keep working unchanged.
"""
from __future__ import annotations

import re
import secrets
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import audit
from ..models import AuditEvent, Case, Communication, Organization, Patient, Practitioner, utcnow
from ..security import PROGRAMME_WIDE_ROLES, Actor
from .access import can_view_case
from .errors import DomainError, Forbidden, Invalid, NotFound

EMIRATES = ("Abu Dhabi", "Dubai", "Sharjah", "Ajman", "Umm Al Quwain", "Ras Al Khaimah", "Fujairah")
ABU_DHABI_REGIONS = ("Abu Dhabi", "Al Ain", "Al Dhafra")  # programme regions used by analytics / directory
GENDERS = {"female": "Female", "male": "Male", "other": "Other", "unknown": "Not stated"}
ID_TYPES = {"emirates_id": "Emirates ID", "passport": "Passport", "gcc_id": "GCC national ID", "other": "Other document"}
GCC_COUNTRIES = ("Saudi Arabia", "Kuwait", "Bahrain", "Qatar", "Oman")
DOC_NUMBER_RE = re.compile(r"^[A-Z0-9]{5,20}$")
LANGUAGES = {"ar": "Arabic", "en": "English", "hi": "Hindi", "ur": "Urdu", "tl": "Tagalog", "ml": "Malayalam"}
CHANNELS = {"app": "App", "sms": "SMS", "whatsapp": "WhatsApp", "phone": "Phone call"}
# country -> nationality_group (the fairness-monitoring groups used across analytics and the model card)
NATIONALITIES: dict[str, str] = {
    "United Arab Emirates": "Emirati",
    "India": "Indian", "Pakistan": "Pakistani", "Philippines": "Filipino",
    "Egypt": "Other Arab", "Jordan": "Other Arab", "Syria": "Other Arab", "Lebanon": "Other Arab", "Sudan": "Other Arab",
    "Yemen": "Other Arab", "Iraq": "Other Arab", "Palestine": "Other Arab", "Saudi Arabia": "Other Arab",
    "Oman": "Other Arab", "Morocco": "Other Arab", "Tunisia": "Other Arab", "Algeria": "Other Arab",
    "Bahrain": "Other Arab", "Kuwait": "Other Arab", "Qatar": "Other Arab",
    "United Kingdom": "Western", "United States": "Western", "Canada": "Western", "Australia": "Western",
    "France": "Western", "Germany": "Western", "Ireland": "Western", "South Africa": "Western",
    "Bangladesh": "Other", "Sri Lanka": "Other", "Nepal": "Other", "Afghanistan": "Other", "Iran": "Other",
    "Indonesia": "Other", "China": "Other", "Ethiopia": "Other", "Kenya": "Other", "Nigeria": "Other",
    "Russia": "Other", "Other": "Other",
}
MRN_RE = re.compile(r"^[A-Z0-9][A-Z0-9-]{3,39}$")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9-]{1,63})*\.[A-Za-z]{2,24}$")
NAME_RE = re.compile(r"^[^\d<>{}\[\]@#$%^*=+|\\/~`\"]{1,100}$")
MIN_OVERRIDE_REASON = 10


class Conflict(DomainError):
    """409 with a machine-readable code and data the page shows (e.g. the possible duplicate records)."""

    def __init__(self, message: str, code: str, **extra):
        super().__init__(message)
        self.code = code
        self.extra = extra


# ------------------------------------------------------------------------------------------------ validation
def emirates_id_check_digit(first14: str) -> int:
    total = 0
    for i, ch in enumerate(reversed(first14)):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10


def normalise_emirates_id(raw: str | None) -> str | None:
    """784-YYYY-NNNNNNN-C (15 digits, Luhn check digit). Returns the dashed form; raises Invalid with a plain reason."""
    if raw is None or not str(raw).strip():
        return None
    digits = re.sub(r"[\s-]", "", str(raw))
    if not digits.isdigit() or len(digits) != 15:
        raise Invalid("Emirates ID must have 15 digits, in the form 784-YYYY-NNNNNNN-C")
    if not digits.startswith("784"):
        raise Invalid("Emirates ID must start with 784")
    yr = int(digits[3:7])
    if not 1900 <= yr <= utcnow().year:
        raise Invalid("Emirates ID year part (digits 4-7) is not a valid year")
    if emirates_id_check_digit(digits[:14]) != int(digits[14]):
        raise Invalid("Emirates ID check digit does not match - please check the number on the card")
    return f"{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}"


def normalise_document_number(raw: str | None, label: str = "document number") -> str | None:
    """Passport / GCC ID / other: letters and digits only, 5-20 characters, stored in capitals (spaces and dashes
    typed between groups are dropped, as printed numbers often contain them)."""
    if raw is None or not str(raw).strip():
        return None
    s = re.sub(r"[\s-]", "", str(raw)).upper()
    if not DOC_NUMBER_RE.match(s):
        raise Invalid(f"{label}: 5 to 20 letters and digits")
    return s


def doc_label(id_type: str | None, country: str | None = None) -> str:
    name = ID_TYPES.get(id_type or "", "Identity document")
    return f"{name} ({country})" if country and id_type != "emirates_id" else name


def normalise_phone(raw: str | None) -> str | None:
    """UAE numbers to +971 format (05X XXX XXXX -> +9715XXXXXXXX); other countries need a leading +."""
    if raw is None or not str(raw).strip():
        return None
    s = str(raw).strip()
    plus = s.startswith("+") or s.startswith("00")
    d = re.sub(r"[\s().-]", "", s).lstrip("+")
    if d.startswith("00"):
        d = d[2:]
    if not d.isdigit():
        raise Invalid("phone number may contain only digits, spaces, dashes and a leading +")
    if not plus and d.startswith("0") and len(d) in (9, 10):  # local UAE: 05XXXXXXXX mobile, 0XXXXXXXX landline
        d = "971" + d[1:]
    elif not plus and len(d) == 9 and d.startswith("5"):
        d = "971" + d
    if d.startswith("971"):
        rest = d[3:]
        if not (re.fullmatch(r"5[0-9]{8}", rest) or re.fullmatch(r"[2-9][0-9]{7}", rest)):
            raise Invalid("UAE phone number should be a mobile like 05X XXX XXXX or a landline like 02 XXX XXXX")
    elif not (plus and 8 <= len(d) <= 15):
        raise Invalid("phone number: use a UAE number (05X XXX XXXX) or an international number starting with +")
    return "+" + d


def validate_email(raw: str | None) -> str | None:
    if raw is None or not str(raw).strip():
        return None
    e = str(raw).strip()
    if len(e) > 200 or not EMAIL_RE.match(e):
        raise Invalid("email address is not valid (example: name@example.com)")
    return e.lower()


def validate_birth_date(d: date | None, now: datetime | None = None) -> date:
    now = now or utcnow()
    if d is None:
        raise Invalid("date of birth is required")
    if d > now.date():
        raise Invalid("date of birth cannot be in the future")
    if now.year - d.year > 120:
        raise Invalid("date of birth is more than 120 years ago - please check the year")
    return d


def age_on(d: date, at: datetime | date | None = None) -> int:
    at = (at or utcnow())
    at = at.date() if isinstance(at, datetime) else at
    return at.year - d.year - ((at.month, at.day) < (d.month, d.day))


def age_band(age: int) -> str:  # same bands as the synthetic cohort (analytics / fairness slices)
    return "25-39" if age < 40 else "40-49" if age < 50 else "50-59" if age < 60 else "60-75"


def _clean_name(v: str | None, what: str) -> str:
    v = (v or "").strip()
    v = re.sub(r"\s+", " ", v)
    if not v:
        raise Invalid(f"{what} is required")
    if not NAME_RE.match(v):
        raise Invalid(f"{what} contains characters that are not allowed in a name (digits or symbols)")
    return v


def _norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"[^a-z\u0600-\u06ff\u0900-\u097f ]+", " ", s)
    toks = [t for t in s.split() if t not in ("al", "el", "bin", "bint", "ibn", "abu", "binti")]
    return " ".join(toks)


def name_similarity(a: str, b: str) -> float:
    na, nb = _norm_name(a), _norm_name(b)
    if not na or not nb:
        return 0.0
    straight = SequenceMatcher(None, na, nb).ratio()
    swapped = SequenceMatcher(None, " ".join(sorted(na.split())), " ".join(sorted(nb.split()))).ratio()
    return max(straight, swapped)


# ------------------------------------------------------------------------------------------------ access
def can_view_patient(db: Session, actor: Actor, p: Patient) -> bool:
    if actor.role == "patient":
        return actor.patient_id == p.id
    if not actor.can("patient:read"):
        return False
    if actor.role in PROGRAMME_WIDE_ROLES:
        return True
    if actor.org_id is not None and actor.org_id == p.managing_org_id:
        return True
    if actor.practitioner_id is not None and p.gp_id == actor.practitioner_id:
        return True
    return any(can_view_case(db, actor, c) for c in db.scalars(select(Case).where(Case.patient_id == p.id)))


def get_patient(db: Session, actor: Actor, patient_id: int) -> Patient:
    p = db.get(Patient, patient_id)
    if p is None:
        raise NotFound("patient not found")
    if not can_view_patient(db, actor, p):
        raise Forbidden("you do not have access to this patient record")
    return p


def can_write_patient(db: Session, actor: Actor, p: Patient) -> bool:
    return actor.is_human and actor.can("patient:write") and can_view_patient(db, actor, p)


# ------------------------------------------------------------------------------------------------ duplicates
def duplicate_candidates(db: Session, *, given: str, family: str, birth_date: date | None,
                         national_id: str | None = None, exclude_id: int | None = None,
                         document: tuple[str | None, str | None, str | None] | None = None) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"same_emirates_id": [], "similar": []}
    if document:  # same passport / GCC ID / other document: a strong candidate (needs the audited "create anyway")
        for p in same_document(db, *document, exclude_id=exclude_id):
            out["similar"].append(_brief(p, f"Same {doc_label(document[0], document[1]).lower()} number", 1.0))
    if national_id:
        for p in db.scalars(select(Patient).where(Patient.national_id == national_id)):
            if p.id != exclude_id:
                out["same_emirates_id"].append(_brief(p, "Same Emirates ID", 1.0))
    if birth_date is not None:
        full = f"{given} {family}"
        q = select(Patient).where(or_(Patient.birth_date == birth_date,
                                      (Patient.birth_date.is_(None)) & (Patient.birth_year == birth_date.year)))
        for p in db.scalars(q.limit(5000)):
            if p.id == exclude_id or any(c["id"] == p.id for c in out["same_emirates_id"] + out["similar"]):
                continue
            score = name_similarity(full, f"{p.given_name} {p.family_name}")
            fam = name_similarity(family, p.family_name)
            initial = _norm_name(given)[:1] == _norm_name(p.given_name)[:1]
            if score >= 0.82 or (fam >= 0.9 and initial):
                why = ("Same date of birth" if p.birth_date == birth_date else "Same birth year") + " and a similar name"
                out["similar"].append(_brief(p, why, round(score, 2)))
    out["similar"].sort(key=lambda c: -c["score"])
    out["similar"] = out["similar"][:10]
    return out


def _brief(p: Patient, why: str, score: float) -> dict:
    return {"id": p.id, "mrn": p.mrn, "name": f"{p.given_name} {p.family_name}",
            "birth_date": p.birth_date.isoformat() if p.birth_date else None, "birth_year": p.birth_year,
            "emirates_id": _mask_eid(p.national_id), "document": doc_brief(p), "why": why, "score": score}


def doc_brief(p: Patient) -> dict | None:
    """Identity document for lists (number masked: last 4 characters)."""
    if p.id_type and p.id_type != "emirates_id" and p.id_number:
        return {"type": p.id_type, "label": doc_label(p.id_type, p.id_country),
                "number": "•" * max(0, len(p.id_number) - 4) + p.id_number[-4:]}
    if p.national_id:
        return {"type": "emirates_id", "label": "Emirates ID", "number": _mask_eid(p.national_id)}
    return None


def _mask_eid(eid: str | None) -> str | None:
    return None if not eid else eid[:9] + "****-" + eid[-1]


# ------------------------------------------------------------------------------------------------ create / edit
@dataclass
class Fields:
    """Validated, normalised patient fields (shared by registration and edit)."""
    values: dict[str, Any]
    warnings: list[str]


DEMOGRAPHIC_KEYS = ("given_name", "family_name", "birth_date", "gender", "national_id", "id_type", "id_number",
                    "id_country", "nationality", "language")
CONTACT_KEYS = ("phone", "email", "address_line", "city", "emirate", "region", "preferred_channel", "whatsapp",
                "has_smartphone")
CARE_KEYS = ("managing_org_id", "gp_id", "mrn")


def validate_fields(db: Session, data: dict[str, Any], *, partial: bool = False, now: datetime | None = None) -> Fields:
    now = now or utcnow()
    v: dict[str, Any] = {}
    warn: list[str] = []
    has = (lambda k: k in data) if partial else (lambda k: True)
    if has("given_name"):
        v["given_name"] = _clean_name(data.get("given_name"), "given name")
    if has("family_name"):
        v["family_name"] = _clean_name(data.get("family_name"), "family name")
    if has("birth_date"):
        bd = data.get("birth_date")
        if isinstance(bd, str):
            try:
                bd = date.fromisoformat(bd)
            except ValueError as e:
                raise Invalid("date of birth must be a date (YYYY-MM-DD)") from e
        v["birth_date"] = validate_birth_date(bd, now)
    if has("gender"):
        g = (data.get("gender") or "").strip().lower()
        if g not in GENDERS:
            raise Invalid(f"sex must be one of: {', '.join(GENDERS.values())}")
        v["gender"] = g
    if has("national_id"):
        v["national_id"] = normalise_emirates_id(data.get("national_id"))
        if v["national_id"] and v.get("birth_date") and int(v["national_id"][4:8]) != v["birth_date"].year:
            warn.append("The year in the Emirates ID differs from the year of birth - please check both.")
    if has("nationality"):
        n = (data.get("nationality") or "").strip()
        if n not in NATIONALITIES:
            raise Invalid("nationality: choose a country from the list (or Other)")
        v["nationality"] = n
    if has("id_type") and (not partial or any(k in data for k in ("id_type", "id_number", "id_country"))):
        _validate_document(data, v, warn)
    if has("language"):
        lg = (data.get("language") or "").strip()
        if lg not in LANGUAGES:
            raise Invalid(f"preferred language must be one of: {', '.join(LANGUAGES.values())}")
        v["language"] = lg
    if has("phone"):
        v["phone"] = normalise_phone(data.get("phone"))
    if has("email"):
        v["email"] = validate_email(data.get("email"))
    if has("address_line"):
        a = (data.get("address_line") or "").strip()
        if len(a) > 300:
            raise Invalid("address: at most 300 characters")
        v["address_line"] = a or None
    if has("city"):
        c = (data.get("city") or "").strip()
        if len(c) > 100:
            raise Invalid("city / area: at most 100 characters")
        v["city"] = c or None
    if has("emirate"):
        e = (data.get("emirate") or "").strip()
        if e not in EMIRATES:
            raise Invalid("emirate: choose one of the seven emirates")
        v["emirate"] = e
    emirate = v.get("emirate")
    if emirate == "Abu Dhabi":
        if not partial or "region" in data:
            r = (data.get("region") or "").strip()
            if r not in ABU_DHABI_REGIONS:
                raise Invalid("region: choose Abu Dhabi, Al Ain or Al Dhafra")
            v["region"] = r
    elif emirate:
        v["region"] = emirate
    if has("preferred_channel"):
        ch = (data.get("preferred_channel") or "").strip() or None
        if ch is not None and ch not in CHANNELS:
            raise Invalid("preferred channel must be App, SMS, WhatsApp or Phone call")
        v["preferred_channel"] = ch
    for b in ("whatsapp", "has_smartphone"):
        if has(b) and b in data:
            v[b] = bool(data.get(b))
    if has("managing_org_id"):
        org = db.get(Organization, data.get("managing_org_id")) if data.get("managing_org_id") else None
        if org is None or org.kind != "primary_care" or not org.active:
            raise Invalid("registered health centre: choose an active family health centre")
        v["managing_org_id"] = org.id
    if has("gp_id"):
        gid = data.get("gp_id")
        if gid:
            gp = db.get(Practitioner, gid)
            org_id = v.get("managing_org_id") or data.get("managing_org_id")
            if gp is None or gp.role != "gp" or not gp.active or gp.is_pool:
                raise Invalid("family doctor: choose an active GP")
            if org_id and gp.org_id != org_id:
                raise Invalid("family doctor must work at the registered health centre")
            v["gp_id"] = gp.id
        else:
            v["gp_id"] = None
    if v.get("preferred_channel") in ("sms", "whatsapp", "phone") and not (v.get("phone") or data.get("_existing_phone")):
        raise Invalid("a phone number is needed for the preferred channel chosen")
    return Fields(v, warn)


def _apply(p: Patient, v: dict[str, Any], now: datetime) -> list[str]:
    changed = []
    for k, val in v.items():
        if k.startswith("_"):
            continue
        if getattr(p, k, None) != val:
            setattr(p, k, val)
            changed.append(k)
    if "birth_date" in v and p.birth_date:
        p.birth_year = p.birth_date.year
        p.age_band = age_band(age_on(p.birth_date, now))
    if "nationality" in v and p.nationality:
        p.nationality_group = NATIONALITIES.get(p.nationality, "Other")
    if "phone" in v:
        p.phone_valid = bool(p.phone)
        if not p.phone:
            p.whatsapp = False
    sync_identity(p)
    return changed


def sync_identity(p: Patient) -> None:
    """id_type names the passport / GCC / other document in id_number; without one it is emirates_id when an
    Emirates ID is on file (national_id), else empty. Both documents can be on file at the same time."""
    if not p.id_number:
        p.id_type, p.id_country = ("emirates_id" if p.national_id else None), None


def _new_mrn(db: Session) -> str:
    while True:
        m = f"KS-{secrets.randbelow(10**8):08d}"
        if db.scalar(select(Patient.id).where(Patient.mrn == m)) is None:
            return m


def _validate_document(data: dict[str, Any], v: dict[str, Any], warn: list[str]) -> None:
    """Identity document chosen on the form. Emirates ID -> national_id (check digit); others -> id_number/id_country.
    An empty number means "no document of this type" (the ID stays optional)."""
    t = (data.get("id_type") or "").strip() or None
    raw = data.get("id_number")
    if t is None:
        if raw not in (None, "") and str(raw).strip():
            raise Invalid("identity document: choose the document type")
        if "id_type" in data:  # "no document" chosen: clear the non-Emirates document (an Emirates ID is kept)
            v.update({"id_type": None, "id_number": None, "id_country": None})
        return
    if t not in ID_TYPES:
        raise Invalid("identity document type must be Emirates ID, Passport, GCC national ID or Other")
    if t == "emirates_id":
        nid = normalise_emirates_id(raw if raw not in (None, "") else data.get("national_id"))
        v["national_id"] = nid
        bd = v.get("birth_date")
        if nid and bd and int(nid[4:8]) != bd.year:
            warn.append("The year in the Emirates ID differs from the year of birth - please check both.")
        return
    label = {"passport": "passport number", "gcc_id": "GCC national ID number"}.get(t, "document number")
    num = normalise_document_number(raw, label)
    if num is None:
        v.update({"id_type": None, "id_number": None, "id_country": None})
        return
    country = (data.get("id_country") or "").strip() or None
    nat = v.get("nationality") or data.get("nationality") or data.get("_existing_nationality")
    if t == "passport" and country is None:
        country = nat if nat in NATIONALITIES and nat != "Other" else None
        if country is None:
            raise Invalid("passport: choose the issuing country")
    if t == "gcc_id":
        country = country or (nat if nat in GCC_COUNTRIES else None)
        if country not in GCC_COUNTRIES:
            raise Invalid(f"GCC national ID: issuing country must be one of {', '.join(GCC_COUNTRIES)}")
    if country is not None and country not in NATIONALITIES:
        raise Invalid("issuing country: choose a country from the list")
    v.update({"id_type": t, "id_number": num, "id_country": country})


def same_document(db: Session, id_type: str | None, id_country: str | None, id_number: str | None,
                  exclude_id: int | None = None) -> list[Patient]:
    if not id_type or id_type == "emirates_id" or not id_number:
        return []
    q = select(Patient).where(Patient.id_type == id_type, Patient.id_number == id_number)
    q = q.where(Patient.id_country == id_country) if id_country else q.where(Patient.id_country.is_(None))
    return [p for p in db.scalars(q) if p.id != exclude_id]


def register_patient(db: Session, actor: Actor, data: dict[str, Any], *, create_anyway_reason: str | None = None,
                     source: str = "staff_form", now: datetime | None = None) -> tuple[Patient, list[str]]:
    """Create a patient record. Raises Conflict (409) on an Emirates ID match or a possible duplicate."""
    now = now or utcnow()
    if not actor.is_human or not actor.can("patient:register"):
        raise Forbidden("your role cannot register patients (coordinators and GPs register patients)")
    f = validate_fields(db, data, now=now)
    v = f.values
    mrn = (data.get("mrn") or "").strip().upper()
    if mrn:
        if not MRN_RE.match(mrn):
            raise Invalid("MRN: 4-40 capital letters, digits or dashes")
        if mrn.startswith("SYN-"):
            raise Invalid("MRN prefix SYN- is reserved for synthetic sample records")
        if db.scalar(select(Patient.id).where(Patient.mrn == mrn)) is not None:
            raise Conflict("that MRN is already used by another patient record", "duplicate_mrn")
    dups = duplicate_candidates(db, given=v["given_name"], family=v["family_name"], birth_date=v["birth_date"],
                                national_id=v.get("national_id"),
                                document=(v.get("id_type"), v.get("id_country"), v.get("id_number")))
    if dups["same_emirates_id"]:
        audit.record(db, actor, "E", "patient.register_blocked", entity_type="Patient", ts=now,
                     detail={"reason": "same_emirates_id", "existing": [c["id"] for c in dups["same_emirates_id"]]})
        db.commit()
        raise Conflict("A patient record with this Emirates ID already exists. Open that record instead of creating "
                       "a second one.", "duplicate_emirates_id", candidates=dups["same_emirates_id"])
    reason = (create_anyway_reason or "").strip()
    if dups["similar"] and len(reason) < MIN_OVERRIDE_REASON:
        raise Conflict("Possible duplicate: a patient with the same date of birth and a similar name is already "
                       "registered. Check the records below. If this is a different person, give the reason and "
                       "choose 'Create anyway'.", "possible_duplicate", candidates=dups["similar"],
                       min_reason=MIN_OVERRIDE_REASON)
    age = age_on(v["birth_date"], now)
    p = Patient(mrn=mrn or f"TMP-{secrets.token_hex(8)}", given_name=v["given_name"], family_name=v["family_name"],
                gender=v["gender"], birth_year=v["birth_date"].year, age_band=age_band(age),
                nationality_group=NATIONALITIES.get(v["nationality"], "Other"), language=v["language"],
                region=v.get("region") or v.get("emirate") or "Abu Dhabi", phone_valid=bool(v.get("phone")),
                has_smartphone=bool(v.get("has_smartphone", False)), whatsapp=bool(v.get("whatsapp", False)) and bool(v.get("phone")),
                alt_contact=False, prior_no_shows=0, prior_screens=0, responsiveness=0.8,
                managing_org_id=v["managing_org_id"], gp_id=v.get("gp_id"), synthetic=False,
                registration_source=source, registered_at=now, registered_by=actor.display, updated_at=now,
                updated_by=actor.display)
    _apply(p, {k: val for k, val in v.items() if k not in ("given_name", "family_name", "gender", "language",
                                                           "managing_org_id", "gp_id")}, now)
    db.add(p)
    db.flush()
    if not mrn:
        p.mrn = _new_mrn(db)
    audit.record(db, actor, "C", "patient.registered", entity_type="Patient", entity_id=p.id, ts=now,
                 detail={"mrn": p.mrn, "source": source, "warnings": f.warnings,
                         **({"possible_duplicates": [c["id"] for c in dups["similar"]],
                             "create_anyway_reason": reason} if dups["similar"] else {})})
    if dups["similar"]:
        p.duplicate_override_reason = reason
        audit.record(db, actor, "E", "patient.duplicate_override", entity_type="Patient", entity_id=p.id, ts=now,
                     detail={"candidates": [c["id"] for c in dups["similar"]], "reason": reason})
    return p, f.warnings


def update_patient(db: Session, actor: Actor, p: Patient, data: dict[str, Any], *, section: str,
                   create_anyway_reason: str | None = None, now: datetime | None = None) -> tuple[list[str], list[str]]:
    """Staff edit of one section of the record. Returns (changed field names, warnings). Audited (field names)."""
    now = now or utcnow()
    if not can_write_patient(db, actor, p):
        raise Forbidden("your role cannot edit patient details")
    allowed = {"demographics": DEMOGRAPHIC_KEYS, "contact": CONTACT_KEYS, "care": ("managing_org_id", "gp_id")}.get(section)
    if allowed is None:
        raise Invalid("unknown section")
    unknown = set(data) - set(allowed)
    if unknown:
        raise Invalid(f"fields not editable in this section: {', '.join(sorted(unknown))}")
    if section == "care" and "gp_id" in data and "managing_org_id" not in data:
        data = {**data, "managing_org_id": p.managing_org_id}
    f = validate_fields(db, {**data, "_existing_phone": p.phone if "phone" not in data else None,
                             "_existing_nationality": p.nationality}, partial=True, now=now)
    v = f.values
    if section == "demographics":
        nid = v.get("national_id", p.national_id)
        if "national_id" in v and nid and nid != p.national_id:
            same = [c for c in duplicate_candidates(db, given=p.given_name, family=p.family_name, birth_date=None,
                                                    national_id=nid, exclude_id=p.id)["same_emirates_id"]]
            if same:
                raise Conflict("This Emirates ID is already on another patient record.", "duplicate_emirates_id",
                               candidates=same)
    if v.get("id_number") and (v["id_number"], v.get("id_country"), v.get("id_type")) != (p.id_number, p.id_country, p.id_type):
        other = same_document(db, v.get("id_type"), v.get("id_country"), v["id_number"], exclude_id=p.id)
        if other:  # saved (the same document can be shared by mistake on either record), but flagged and audited
            f.warnings.append(f"Another record has the same {doc_label(v.get('id_type'), v.get('id_country')).lower()} "
                              f"number: {', '.join(o.mrn for o in other[:3])}. Check both records.")
            audit.record(db, actor, "E", "patient.duplicate_document", entity_type="Patient", entity_id=p.id, ts=now,
                         detail={"id_type": v.get("id_type"), "others": [o.id for o in other]})
    changed = _apply(p, v, now)
    if changed:
        p.updated_at, p.updated_by = now, actor.display
        audit.record(db, actor, "U", f"patient.{section}_updated", entity_type="Patient", entity_id=p.id, ts=now,
                     detail={"fields": changed, "by_role": actor.role})
    return changed, f.warnings


# ------------------------------------------------------------------------------------------------ search
def search_patients(db: Session, actor: Actor, q: str, limit: int = 30) -> list[dict]:
    if not actor.can("patient:read"):
        raise Forbidden("role lacks patient:read")
    q = (q or "").strip()
    if len(q) < 2:
        return []
    conds = []
    digits = re.sub(r"\D", "", q)
    like = f"%{q}%"
    conds += [Patient.mrn.ilike(like), Patient.given_name.ilike(like), Patient.family_name.ilike(like),
              (Patient.given_name + " " + Patient.family_name).ilike(like)]
    if len(digits) == 15:
        conds.append(Patient.national_id == f"{digits[:3]}-{digits[3:7]}-{digits[7:14]}-{digits[14]}")
    elif len(digits) >= 5:
        conds.append(func.replace(Patient.national_id, "-", "").like(f"%{digits}%"))
        conds.append(Patient.phone.like(f"%{digits[-9:]}%"))
    doc = re.sub(r"[\s-]", "", q).upper()
    if 5 <= len(doc) <= 20 and doc.isalnum():  # passport / GCC ID / other document number: exact match
        conds.append(Patient.id_number == doc)
    rows = db.scalars(select(Patient).where(or_(*conds)).order_by(Patient.family_name, Patient.given_name).limit(400))
    out = []
    for p in rows:
        if can_view_patient(db, actor, p):
            out.append(patient_row(db, p))
        if len(out) >= limit:
            break
    return out


def patient_row(db: Session, p: Patient) -> dict:
    org = db.get(Organization, p.managing_org_id)
    open_cases = db.scalar(select(func.count()).select_from(Case).where(Case.patient_id == p.id,
                                                                         Case.state != "closed")) or 0
    return {"id": p.id, "mrn": p.mrn, "name": f"{p.given_name} {p.family_name}", "gender": p.gender,
            "birth_date": p.birth_date.isoformat() if p.birth_date else None, "birth_year": p.birth_year,
            "emirates_id": _mask_eid(p.national_id), "document": doc_brief(p), "phone": p.phone,
            "health_centre": org.name if org else None,
            "open_cases": open_cases, "deceased": p.deceased, "sample": (p.mrn or "").startswith("SYN-")}


def recent_changes(db: Session, p: Patient, limit: int = 15) -> list[dict]:
    rows = db.scalars(select(AuditEvent).where(AuditEvent.entity_type == "Patient", AuditEvent.entity_id == str(p.id),
                                               AuditEvent.event.like("patient.%"))
                      .order_by(AuditEvent.id.desc()).limit(limit))
    return [{"at": a.ts.isoformat(), "by": a.actor_display, "role": a.actor_role, "event": a.event,
             "fields": (a.detail or {}).get("fields") or [], "summary": EVENT_TEXT.get(a.event, a.event)} for a in rows]


EVENT_TEXT = {"patient.registered": "Patient registered", "patient.demographics_updated": "Personal details changed",
              "patient.contact_updated": "Contact details changed", "patient.care_updated": "Health centre / GP changed",
              "patient.duplicate_override": "Registered despite a possible duplicate (reason recorded)",
              "patient.self_contact_updated": "Patient updated their own contact details",
              "patient.insurance_updated": "Insurance changed", "patient.contacts_updated": "Next of kin / guardian changed"}


# ------------------------------------------------------------------------------------------------ record view
def _iso(d) -> str | None:
    return d.isoformat() if d else None


def build_patient_record(db: Session, actor: Actor, p: Patient, now: datetime | None = None) -> dict[str, Any]:
    from ..models import Consent, DiagnosticReport
    from ..programmes import PROGRAMMES
    from ..statemachine import LABELS

    now = now or utcnow()
    org = db.get(Organization, p.managing_org_id)
    gp = db.get(Practitioner, p.gp_id) if p.gp_id else None
    cases = []
    for c in db.scalars(select(Case).where(Case.patient_id == p.id).order_by(Case.id.desc())):
        vis = can_view_case(db, actor, c)
        cases.append({"id": c.id, "programme": c.programme, "programme_name": PROGRAMMES[c.programme].name,
                      "state": c.state, "state_label": LABELS.get(c.state, c.state), "tier": c.tier,
                      "created_at": _iso(c.created_at), "closed_at": _iso(c.closed_at),
                      "closure_reason": c.closure_reason, "can_open": vis})
    results = [{"id": r.id, "programme": r.programme, "display": r.display, "category": r.category,
                "conclusion": r.conclusion, "issued_at": _iso(r.issued_at), "case_id": r.case_id,
                "ordered_by": r.ordering_clinician, "site": r.screening_site, "collected_at": _iso(r.specimen_collected_at),
                "values": "; ".join(f"{o.value if o.value is not None else (o.value_text or '')}"
                                    f"{(' ' + o.unit) if o.unit else ''}".strip() for o in r.observations)}
               for r in db.scalars(select(DiagnosticReport).where(DiagnosticReport.patient_id == p.id)
                                   .order_by(DiagnosticReport.issued_at.desc()).limit(50))]
    consents = [{"scope": k.scope, "decision": k.decision, "status": k.status, "at": _iso(k.recorded_at), "note": k.note}
                for k in db.scalars(select(Consent).where(Consent.patient_id == p.id, Consent.status == "active")
                                    .order_by(Consent.id))]
    age = age_on(p.birth_date, now) if p.birth_date else max(0, now.year - (p.birth_year or now.year))
    return {
        "id": p.id, "mrn": p.mrn, "sample": (p.mrn or "").startswith("SYN-"),
        "demographics": {"given_name": p.given_name, "family_name": p.family_name,
                         "birth_date": _iso(p.birth_date), "birth_year": p.birth_year, "age": age,
                         "age_estimated": p.birth_date is None, "gender": p.gender,
                         "national_id": p.national_id, "nationality": p.nationality,
                         "id_type": p.id_type or ("emirates_id" if p.national_id else None),
                         "id_number": p.id_number or p.national_id, "id_country": p.id_country,
                         "other_document": (
                             {"type": p.id_type, "label": doc_label(p.id_type, p.id_country), "number": p.id_number}
                             if p.id_number else None),
                         "nationality_group": p.nationality_group, "language": p.language},
        "contact": {"phone": p.phone, "email": p.email, "address_line": p.address_line, "city": p.city,
                    "emirate": p.emirate or ("Abu Dhabi" if p.region in ABU_DHABI_REGIONS else None), "region": p.region, "preferred_channel": p.preferred_channel,
                    "whatsapp": p.whatsapp, "has_smartphone": p.has_smartphone, "phone_valid": p.phone_valid},
        "care": {"managing_org_id": p.managing_org_id, "managing_org": org.name if org else None,
                 "gp_id": p.gp_id, "gp": gp.name if gp else None},
        "registration": {"source": p.registration_source, "at": _iso(p.registered_at), "by": p.registered_by,
                         "duplicate_override_reason": p.duplicate_override_reason,
                         "updated_at": _iso(p.updated_at), "updated_by": p.updated_by},
        "deceased": p.deceased, "consents": consents, "cases": cases, "results": results,
        "changes": recent_changes(db, p),
        "can_write": can_write_patient(db, actor, p),
        "can_add_result": actor.is_human and actor.can("case:create"),
        "account": _account(db, actor, p),
        "recalls": _recalls(db, p),
        **_coverage(db, p),
    }


def _coverage(db: Session, p: Patient) -> dict:
    from .coverage import view

    return view(db, p)


def _recalls(db: Session, p: Patient) -> list[dict]:
    from .recall import patient_recalls

    return patient_recalls(db, p)


def _account(db: Session, actor: Actor, p: Patient) -> dict:
    from .invites import account_status

    st = account_status(db, p)
    st["can_invite"] = bool(actor.is_human and actor.can("patient:invite") and can_write_patient(db, actor, p)
                            and not p.deceased)
    st["can_reset"] = bool(st["can_invite"] and actor.role == "coordinator")  # resets: coordinators only
    st["invite_messages"] = [{"at": m.sent_at.isoformat(), "channel": m.channel, "content": m.content}
                             for m in db.scalars(select(Communication).where(Communication.patient_id == p.id,
                                                                             Communication.category == "account_invite")
                                                 .order_by(Communication.id.desc()).limit(3))]
    return st


def options(db: Session) -> dict[str, Any]:
    orgs = db.scalars(select(Organization).where(Organization.kind == "primary_care", Organization.active.is_(True))
                      .order_by(Organization.name)).all()
    gps = db.scalars(select(Practitioner).where(Practitioner.role == "gp", Practitioner.active.is_(True),
                                                Practitioner.is_pool.is_(False)).order_by(Practitioner.name)).all()
    return {"emirates": list(EMIRATES), "abu_dhabi_regions": list(ABU_DHABI_REGIONS), "genders": GENDERS,
            "id_types": ID_TYPES, "gcc_countries": list(GCC_COUNTRIES),
            "languages": LANGUAGES, "channels": CHANNELS, "nationalities": sorted(NATIONALITIES, key=lambda n: (n == "Other", n)),
            "health_centres": [{"id": o.id, "name": o.name, "region": o.region} for o in orgs],
            "gps": [{"id": g.id, "name": g.name, "org_id": g.org_id} for g in gps], **_coverage_options()}


def _coverage_options() -> dict:
    from .coverage import options as cov_options

    return cov_options()
