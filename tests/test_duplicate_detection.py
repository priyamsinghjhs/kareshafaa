"""Duplicate detection at registration: candidates narrowed in SQL by strong identifiers and by date of birth plus a
normalised / phonetic name key (Arabic transliteration aware), with no candidate cap that could drop a true match."""
from __future__ import annotations

import time
from datetime import date

import pytest
from sqlalchemy import create_engine, func, insert, select
from sqlalchemy.orm import sessionmaker

from kareshafaa.db import Base
from kareshafaa.models import Patient, PatientNameKey
from kareshafaa.services import name_keys as nk
from kareshafaa.services import patients as svc


@pytest.mark.parametrize("a,b", [
    ("Mohammed Al-Hashimi", "Muhammad Hashimi"), ("Mohamed El Hashimi", "MUHAMMAD AL HASHIMI"),
    ("Abd al-Rahman Al Mansoori", "Abdulrahman Almansoori"), ("Abdel Rahman Mansouri", "Abd-al-Rahman Al-Mansoori"),
    ("Yousef Al-Ali", "Yusuf Ali"), ("Hussein Ibrahim", "Husain Ebrahim"), ("Fatima Hassan", "Fatma Hasan"),
    ("Aïsha Khalid", "Ayesha Khaled"), ("Noor Al-Zaabi", "Nour Alzaabi"), ("ZAINAB  al-qasimi", "Zaynab Al Qasimi"),
])
def test_transliteration_variants_share_one_canonical_name(a, b):
    assert sorted(nk.canonical_tokens(a)) == sorted(nk.canonical_tokens(b)), (a, b)
    assert svc.name_similarity(a, b) >= 0.95


def test_arabic_script_forms_and_article_are_unified():
    assert nk.canonical_tokens("أحمد الهاشمي") == nk.canonical_tokens("احمد هاشمي")
    assert nk.canonical_tokens("فاطِمة") == nk.canonical_tokens("فاطمه")


def test_near_miss_swapped_and_unrelated_names():
    assert svc.name_similarity("Fatima Hassan", "Hassan Fatima") >= 0.95  # swapped given / family
    assert svc.name_similarity("Mariam Al Qasimi", "Maryam Qasimi") >= 0.95
    assert svc.name_similarity("Khalid Al Mazrouei", "Khaled Almazroui") >= 0.82  # near miss
    assert svc.name_similarity("Fatima Hassan", "John Smith") < 0.5
    assert svc.name_similarity("Omar Saeed", "Layla Rashid") < 0.6


# ------------------------------------------------------------------------------------------------ SQL narrowing
@pytest.fixture()
def big_db(tmp_path):
    """A separate database with many patients (never the shared test database)."""
    eng = create_engine(f"sqlite:///{tmp_path / 'dups.db'}")
    Base.metadata.create_all(eng)
    for t in Base.metadata.sorted_tables:
        for idx in t.indexes:
            idx.create(eng, checkfirst=True)
    sf = sessionmaker(bind=eng, expire_on_commit=False)
    yield sf
    eng.dispose()


def _row(i, given, family, *, birth_date=None, birth_year=1980, phone=None, email=None):
    return {"mrn": f"T-{i}", "given_name": given, "family_name": family, "gender": "male",
            "birth_year": birth_date.year if birth_date else birth_year, "birth_date": birth_date,
            "age_band": "40-49", "nationality_group": "Emirati", "language": "ar", "region": "Abu Dhabi",
            "managing_org_id": 1, "synthetic": False, "phone": phone, "email": email}


GIVEN = ["Omar", "Layla", "Saif", "Hind", "Rashid", "Amna", "Sultan", "Mira", "Tariq", "Huda", "Faisal", "Reem"]
FAMILY = ["Al Nuaimi", "Al Ketbi", "Al Shamsi", "Al Dhaheri", "Al Suwaidi", "Al Marri", "Al Falasi", "Al Kaabi"]


def test_true_match_beyond_5000_candidates_is_found_quickly(big_db):
    """25k older records in ONE birth year (no full date of birth): the old check looked at the first 5,000 only."""
    rows = [_row(i, f"{GIVEN[i % 12]}{i}", f"{FAMILY[i % 8]}{i // 7}", birth_year=1980) for i in range(25_000)]
    rows.append(_row(25_000, "Muhammad", "Al-Hashimi", birth_year=1980))  # the true match, last of all
    rows.append(_row(25_001, "Mohammed", "Hashimi", birth_date=date(1975, 3, 1)))  # same name, different birth date
    with big_db() as db:
        db.execute(insert(Patient), rows)
        db.commit()
        assert nk.ensure_all(big_db, batch=2000) == 25_002  # backfill of the index
        assert nk.ensure_all(big_db, batch=2000) == 0  # safe to run twice: nothing left, nothing duplicated
        n_keys = db.scalar(select(func.count(PatientNameKey.id)))
        assert nk.ensure_all(big_db) == 0 and db.scalar(select(func.count(PatientNameKey.id))) == n_keys
        t0 = time.perf_counter()
        out = svc.duplicate_candidates(db, given="Mohammed", family="El Hashimi", birth_date=date(1980, 6, 12))
        took = time.perf_counter() - t0
        names = [c["name"] for c in out["similar"]]
        assert "Muhammad Al-Hashimi" in names  # found although it is record 25,001 in that birth year
        assert "Mohammed Hashimi" not in names  # same name, different full date of birth: not a match
        assert took < 1.0, took


def test_records_without_keys_are_still_checked(big_db):
    with big_db() as db:
        db.execute(insert(Patient), [_row(1, "Fatma", "Hasan", birth_date=date(1990, 1, 5))])  # no keys yet
        db.commit()
        out = svc.duplicate_candidates(db, given="Fatima", family="Hassan", birth_date=date(1990, 1, 5))
        assert [c["name"] for c in out["similar"]] == ["Fatma Hasan"]


def test_strong_keys_phone_email_and_different_dob(big_db):
    with big_db() as db:
        db.execute(insert(Patient), [
            _row(1, "Salem", "Al Ameri", birth_date=date(1985, 2, 2), phone="+971501234567"),
            _row(2, "Mohammed", "Al Ali", birth_date=date(1960, 7, 7), phone="+971509999999", email="fam@example.com"),
            _row(3, "Mohammed", "Al Ali", birth_date=date(1960, 7, 7)),
        ])
        db.commit()
        nk.ensure_all(big_db)
        # same DOB and phone, name spelled very differently: still a candidate
        out = svc.duplicate_candidates(db, given="Saleem", family="Ameri", birth_date=date(1985, 2, 2),
                                       phone="+971501234567")
        assert out["similar"] and "phone number" in out["similar"][0]["why"]
        # son with his father's name and phone, another birth date: never a blocking match, only a warning
        out = svc.duplicate_candidates(db, given="Mohammed", family="Al Ali", birth_date=date(1992, 4, 4),
                                       phone="+971509999999", email="FAM@example.com")
        assert out["similar"] == []
        assert len(out["weak"]) == 1 and "different date of birth" in out["weak"][0]["why"]
        # same name, different DOB, no shared identifier: nothing at all
        out = svc.duplicate_candidates(db, given="Mohammed", family="Al Ali", birth_date=date(1992, 4, 4))
        assert out["similar"] == [] and out["weak"] == []


def test_keys_follow_name_changes(db, make_patient):
    pid = make_patient()
    p = db.get(Patient, pid)
    keys = set(db.scalars(select(PatientNameKey.key).where(PatientNameKey.patient_id == pid)))
    assert keys == nk.name_keys(p.given_name, p.family_name)
    p.given_name, p.family_name = "Abdul Rahman", "Al-Mansoori"
    db.commit()
    keys = set(db.scalars(select(PatientNameKey.key).where(PatientNameKey.patient_id == pid)))
    assert {"abdrahman", "mansuri"} <= keys and not any(k.startswith("test") for k in keys)


def test_registration_flags_a_transliteration_duplicate(client, as_user, db):
    opts = client.get("/api/patients/options", headers=as_user("aisha.rahman")).json()
    hc = opts["health_centres"][0]["id"]
    body = {"given_name": "Abdulrahman", "family_name": "Al-Zaabi", "birth_date": "1971-09-14", "gender": "male",
            "nationality": "United Arab Emirates", "language": "ar", "emirate": "Abu Dhabi", "region": "Al Ain",
            "managing_org_id": hc, "phone": "0501112233"}
    h = as_user("aisha.rahman")
    r = client.post("/api/patients", headers=h, json=body)
    assert r.status_code == 200, r.text
    r2 = client.post("/api/patients", headers=h, json={**body, "given_name": "Abd al-Rahman", "family_name": "Alzaabi",
                                                       "phone": None})
    assert r2.status_code == 409 and r2.json()["code"] == "possible_duplicate"
    assert r2.json()["candidates"][0]["id"] == r.json()["patient_id"]
