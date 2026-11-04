"""Normalised / phonetic name keys for duplicate detection (Arabic transliteration aware).

Each patient has a few short keys in ``patient_name_keys`` (one row per key, indexed), derived from the given and
family names only. They are a search index, not patient data: nothing a person entered is changed, and the keys
are rebuilt from the names whenever a name changes (ORM event below). ``duplicate_candidates`` narrows candidates
in SQL with these keys (plus strong identifiers and the birth date), so it never has to scan, or cap, a whole
birth year of records.

Per name token two keys are stored:
* the canonical token: lower case, accents and Arabic diacritics removed, Arabic letter forms unified, particles
  (Al-/El-/Al/bin/ibn..., also a glued Al/El on longer names) split off and dropped, Abdul/Abd al/Abdel joined to the following name ("abdrahman"), common
  transliteration variants mapped to one spelling (Mohammed/Muhammad/Mohamed -> "muhammad", Yousef/Yusuf -> "yusuf",
  Hussein/Husain -> "husayn", ...) and generic spelling variants folded (oo/ou -> u, ee -> i, doubled letters, a
  final h after a vowel, kh/gh/q/ph);
* a consonant skeleton ("~mhmd"), which catches remaining vowel variants and small typos as CANDIDATES only (the
  final decision is the name similarity score, see services/patients.py).
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import delete, event, exists, insert, select
from sqlalchemy.orm import Session

PARTICLES = {"al", "el", "ul", "bin", "bint", "ibn", "binti", "abu", "bu", "ben", "da", "de", "van", "von"}
ABD = ("abdul", "abdel", "abdal", "abdol", "abdu", "abd")
VARIANTS: dict[str, tuple[str, ...]] = {
    "muhammad": ("mohammed", "mohammad", "mohamed", "mohamad", "muhammed", "muhamad", "muhamed", "mohamud", "mohd",
                 "mohammod", "mouhamed", "mohummad", "mhd", "muhammet", "mehmet"),
    "ahmad": ("ahmed", "ahmet", "ahmd"),
    "mahmud": ("mahmoud", "mahmood", "mahmut"),
    "yusuf": ("yousef", "youssef", "yousif", "yusef", "yousuf", "yossef", "yusif", "youcef", "yosef"),
    "ibrahim": ("ebrahim", "ibraheem", "ibrahem", "ebraheem"),
    "husayn": ("hussein", "husain", "hussain", "hossein", "husein", "hussien", "husayn", "hosein"),
    "hasan": ("hassan", "hasen", "hassen"),
    "abdallah": ("abdullah", "abdalla", "abdulla", "abdellah", "abdallah"),
    "fatima": ("fatma", "fatemah", "fathima", "fatimah", "fatema", "fatme"),
    "aisha": ("ayesha", "aysha", "aicha", "aishah", "ayisha", "aysa"),
    "khalid": ("khaled", "khalied", "kaled", "kalid"),
    "umar": ("omar", "omer", "umer"),
    "uthman": ("othman", "osman", "usman", "othmane"),
    "mustafa": ("mostafa", "moustafa", "mustapha", "mostapha"),
    "nur": ("nour", "noor", "noura"),
    "zaynab": ("zainab", "zeinab", "zaynab", "zenab"),
    "maryam": ("mariam", "miriam", "maryem", "meryem", "mariyam"),
    "khadija": ("khadeeja", "khadijah", "khadeejah", "khadidja", "hatice"),
    "said": ("saeed", "saed", "saeid", "sayeed"),
    "rashid": ("rasheed", "rachid", "rashed", "rached"),
    "yasin": ("yassin", "yaseen", "yassine", "yacine"),
    "sulayman": ("suleiman", "sulaiman", "soliman", "sulaman", "suleyman", "slimane"),
    "issa": ("eisa", "isa", "eissa"),
    "musa": ("mousa", "moussa", "mosa"),
    "salih": ("saleh", "salah"),
    "abbas": ("abas",),
    "jamal": ("gamal", "djamel", "jamaal"),
    "mansuri": ("mansoori", "mansouri", "mansoury", "mansori"),
}
_CANON = {v: k for k, vs in VARIANTS.items() for v in vs}
_AR_FORMS = str.maketrans({"أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا", "ة": "ه", "ى": "ي", "ؤ": "و", "ئ": "ي", "ـ": ""})
_AR_TASHKEEL = re.compile("[\u064b-\u065f\u0670]")


def _fold(tok: str) -> str:
    """Generic Latin spelling folds (only for tokens not in the variant table)."""
    t = tok
    for a, b in (("ph", "f"), ("kh", "k"), ("gh", "g"), ("q", "k"), ("ck", "k"), ("dh", "d"), ("oo", "u"), ("ou", "u"),
                 ("ee", "i"), ("ie", "i"), ("ei", "i")):
        t = t.replace(a, b)
    t = re.sub(r"(.)\1+", r"\1", t)  # doubled letters
    if len(t) > 3 and t.endswith("ah"):
        t = t[:-1]
    if len(t) > 3 and t.endswith("y"):
        t = t[:-1] + "i"
    return t


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").casefold()
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = _AR_TASHKEEL.sub("", s).translate(_AR_FORMS)
    s = s.replace("'", "").replace("\u2019", "").replace("`", "")
    return re.sub(r"[^a-z\u0600-\u06ff\u0900-\u097f]+", " ", s)


def canonical_tokens(name: str) -> list[str]:
    """'Abd al-Rahman Al-Mansoori' -> ['abdrahman', 'mansuri']; 'Mohammed' -> ['muhammad']."""
    raw = _norm(name).split()
    out: list[str] = []
    i = 0
    while i < len(raw):
        t = raw[i]
        if t in ABD and i + 1 < len(raw):  # Abdul Rahman / Abd al Rahman / Abdel Rahman -> abdrahman
            j = i + 1
            if raw[j] in ("al", "el", "ul", "ar", "ur", "as", "an") and j + 1 < len(raw):
                j += 1
            out.append("abd" + _token(raw[j]))
            i = j + 1
            continue
        for p in ("abdul", "abdel", "abdal", "abdol"):  # Abdulrahman / Abdelrahman -> abdrahman
            if t.startswith(p) and len(t) > len(p) + 2 and t not in _CANON:
                t = "abd" + t[len(p):]
                break
        if t in PARTICLES:
            i += 1
            continue
        if not t.isascii() and t.startswith("ال") and len(t) > 3:  # Arabic article al- glued to the name
            t = t[2:]
        if t.isascii() and len(t) > 5 and t[:2] in ("al", "el") and t not in _CANON:  # Almansoori -> mansoori
            t = t[2:]  # (applied the same way to every record, so "Alexander" still equals "Alexander")
        out.append(_token(t))
        i += 1
    return [t for t in out if t]


def _token(t: str) -> str:
    if t.startswith("abd") and len(t) > 5 and t not in _CANON:
        return "abd" + _token_plain(t[3:])
    return _token_plain(t)


def _token_plain(t: str) -> str:
    if t in _CANON:
        return _CANON[t]
    if t in VARIANTS:
        return t
    if not t.isascii():
        return t  # Arabic / Devanagari script: unified letter forms, no transliteration
    f = _fold(t)
    return _CANON.get(f, f)


def skeleton(tok: str) -> str:
    if not tok.isascii():
        return tok
    s = tok[0] + re.sub(r"[aeiouyhw]", "", tok[1:])
    return re.sub(r"(.)\1+", r"\1", s)


def canonical_name(given: str, family: str) -> str:
    return " ".join(canonical_tokens(f"{given} {family}"))


def name_keys(given: str | None, family: str | None) -> set[str]:
    keys: set[str] = set()
    for t in canonical_tokens(f"{given or ''} {family or ''}"):
        if len(t) < 2:
            continue
        keys.add(t[:40])
        sk = skeleton(t)
        if t.isascii() and len(sk) >= 2:
            keys.add(("~" + sk)[:40])
    return keys


# ------------------------------------------------------------------------------------------ index maintenance
def _table():
    from ..models import PatientNameKey
    return PatientNameKey.__table__


def write_keys(conn_or_session, patient_id: int, given: str | None, family: str | None) -> None:
    t = _table()
    conn_or_session.execute(delete(t).where(t.c.patient_id == patient_id))
    keys = sorted(name_keys(given, family))
    if keys:
        conn_or_session.execute(insert(t), [{"patient_id": patient_id, "key": k} for k in keys])


@event.listens_for(Session, "after_flush")
def _sync_on_flush(session: Session, _ctx) -> None:
    from sqlalchemy import inspect as sa_inspect

    from ..models import Patient

    todo = []
    for obj in list(session.new) + list(session.dirty):
        if not isinstance(obj, Patient) or obj.id is None:
            continue
        st = sa_inspect(obj)
        if obj in session.new or st.attrs.given_name.history.has_changes() or st.attrs.family_name.history.has_changes():
            todo.append(obj)
    if todo:
        conn = session.connection()
        for p in todo:
            write_keys(conn, p.id, p.given_name, p.family_name)


def missing_patient_ids(db: Session, limit: int | None = None) -> list[int]:
    from ..models import Patient

    t = _table()
    q = select(Patient.id).where(~exists().where(t.c.patient_id == Patient.id)).order_by(Patient.id)
    return list(db.scalars(q.limit(limit) if limit else q))


def ensure_all(db_factory, batch: int = 500, stop=None) -> int:
    """Build keys for every patient that has none (records created before this index, or copied in by the
    bootstrap). Idempotent and safe to run twice or concurrently: it only fills patients without keys, and the keys
    are a pure function of the names. Changes no patient field. Returns the number of patients indexed."""
    from ..models import Patient

    n = 0
    while stop is None or not stop.is_set():
        db = db_factory()
        try:
            ids = missing_patient_ids(db, batch)
            if not ids:
                return n
            rows = db.execute(select(Patient.id, Patient.given_name, Patient.family_name).where(Patient.id.in_(ids))).all()
            conn = db.connection()
            t = _table()
            conn.execute(delete(t).where(t.c.patient_id.in_(ids)))
            vals = [{"patient_id": pid, "key": k} for pid, g, f in rows for k in sorted(name_keys(g, f))]
            if vals:
                conn.execute(insert(t), vals)
            db.commit()
            n += len(ids)
            if len(ids) < batch:
                return n
        finally:
            db.close()
    return n
