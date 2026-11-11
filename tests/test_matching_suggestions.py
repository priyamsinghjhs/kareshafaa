"""Fuzzy result-to-patient matching suggestions: transliteration, DOB, Emirates ID, MRN. Never auto-links."""
from __future__ import annotations

from datetime import date, datetime

import pytest
from sqlalchemy import select

from kareshafaa.models import IngestMessage, Patient, utcnow
from kareshafaa.services.matching import suggest_for_message
from kareshafaa.services.name_keys import canonical_tokens, write_keys


def _patient(db, *, given, family, birth=date(1980, 5, 15), mrn=None, eid=None, year=None, **kw):
    i = abs(hash((given, family, mrn, eid))) % 10**9
    p = Patient(mrn=mrn or f"MATCH-{i}", given_name=given, family_name=family, gender="female",
                birth_date=birth, birth_year=year or (birth.year if birth else 1980), age_band="40-49",
                nationality_group="emirati", language="ar", region="Abu Dhabi",
                national_id=eid, managing_org_id=3, gp_id=19, **kw)
    db.add(p)
    db.flush()
    write_keys(db, p.id, p.given_name, p.family_name)
    db.flush()
    return p


def _msg(*, given="Mohammed", family="Al-Mansoori", birth="1980-05-15", ident="UNKNOWN-MRN", year=1980,
         eid_ident=None, alt=None, **kw):
    demo = {"given_name": given, "family_name": family, "birth_date": birth}
    demo.update(kw.get("demo", {}))
    return IngestMessage(source="hl7v2", message_id=f"MSG-{ident}-{id(demo)}", received_at=utcnow(),
                         status="needs_match_review", patient_identifier=eid_ident or ident,
                         birth_year=year, programme="diabetes_hba1c", error="identifier not found",
                         patient_demographics=demo, alt_identifiers=alt)


def test_muhammad_variants_and_al_prefix_match_same_person(db):
    p = _patient(db, given="Muhammad", family="Mansoori", birth=date(1980, 5, 15))
    # Mohammed / Mohamed / Al- and El- prefixes all fold to the same keys
    assert canonical_tokens("Mohammed Al-Mansoori") == canonical_tokens("Muhammad Mansoori")
    assert canonical_tokens("Mohamed El-Mansoori") == canonical_tokens("Muhammad Mansoori")
    for given, family in (("Mohammed", "Al-Mansoori"), ("Mohamed", "El-Mansoori"), ("Muhammed", "Al Mansoori")):
        msg = _msg(given=given, family=family, ident=f"NONE-{given}")
        found = suggest_for_message(db, msg)
        ids = [c["id"] for c in found]
        assert p.id in ids, (given, family, found)
        hit = next(c for c in found if c["id"] == p.id)
        assert hit["score"] >= 0.82 and hit["confidence"] in ("high", "medium")
        assert "name" in hit["why"].lower() and "birth" in hit["why"].lower()
        assert any("variant" in r.lower() or "similar name" in r.lower() for r in hit["reasons"])


def test_suggestions_are_top_three_with_confidence_and_reasons(db):
    p1 = _patient(db, given="Fatima", family="Hassan", birth=date(1975, 1, 2), mrn="F-1")
    p2 = _patient(db, given="Fatimah", family="Hasan", birth=date(1975, 1, 2), mrn="F-2")
    p3 = _patient(db, given="Fatma", family="Hassan", birth=date(1975, 1, 2), mrn="F-3")
    p4 = _patient(db, given="Fatima", family="Hassan", birth=date(1999, 1, 2), mrn="F-4")  # same name, different DOB
    msg = _msg(given="Fatima", family="Hassan", birth="1975-01-02", ident="NOPE", year=1975)
    found = suggest_for_message(db, msg)
    assert len(found) <= 3
    assert all({"id", "name", "score", "confidence", "reasons", "why"} <= set(c) for c in found)
    assert [c["score"] for c in found] == sorted((c["score"] for c in found), reverse=True)
    ids = {c["id"] for c in found}
    assert p1.id in ids
    assert p4.id not in ids  # different date of birth, no strong identifier — not a candidate on name alone unless very tight


def test_emirates_id_last_four_and_partial_mrn(db):
    p = _patient(db, given="Aisha", family="Rahman", birth=date(1990, 7, 7),
                 eid="784-1990-1234567-1", mrn="MRN-778899")
    # last 4 of the digit form 784199012345671 is 5671; message has only that partial plus the name and DOB
    msg = _msg(given="Aisha", family="Rahman", birth="1990-07-07", ident="5671", year=1990)
    found = suggest_for_message(db, msg)
    assert any(c["id"] == p.id for c in found)
    hit = next(c for c in found if c["id"] == p.id)
    assert "last 4" in hit["why"].lower() or "similar name" in hit["why"].lower() or "birth" in hit["why"].lower()


def test_exact_id_with_birth_year_clash_is_flagged_not_hidden(db):
    p = _patient(db, given="Omar", family="Haddad", birth=date(1972, 3, 3), year=1972, mrn="OMAR-72")
    msg = _msg(given="Omar", family="Haddad", birth="1980-03-03", ident="OMAR-72", year=1980)
    found = suggest_for_message(db, msg)
    assert found and found[0]["id"] == p.id
    assert "birth_year_differs" in found[0]["flags"]
    assert "differs" in found[0]["why"].lower()
    assert found[0]["score"] < 0.95  # lower than a clean exact match


def test_never_auto_links_on_ingest(client, as_user, db):
    """A similar name is suggested, but ingest still leaves the message unmatched until a person confirms."""
    p = _patient(db, given="Muhammad", family="Mansoori", birth=date(1980, 5, 15), mrn="REAL-MRN-1")
    db.commit()
    h = {**as_user("aisha.rahman"), "Content-Type": "text/plain"}
    hl7 = "\r".join([
        "MSH|^~\\&|LIS|LAB|KARESHAFAA|PROG|20261020093000||ORU^R01^ORU_R01|SUG-1|P|2.5.1",
        "PID|1||NOT-A-REAL-MRN^^^KARESHAFAA^MR||MANSOORI^MOHAMMED||19800515|M",
        "OBX|1|NM|4548-4^HbA1c^LN||7.8|%|||||F",
    ])
    r = client.post("/api/ingest/hl7v2", headers=h, content=hl7)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "needs_match_review"
    assert body.get("case") in (None, {}, []) or not body.get("ok") or body["status"] != "processed"
    items = client.get("/api/ingest/messages?status=needs_match_review", headers=as_user("aisha.rahman")).json()["items"]
    mine = next(i for i in items if i["message_id"] == "SUG-1")
    assert mine["status"] == "needs_match_review"
    assert any(c["id"] == p.id for c in mine["candidates"]), mine["candidates"]
    # still unmatched: no case linked
    assert mine["case_id"] is None


def test_suggest_for_empty_message_is_empty(db):
    msg = IngestMessage(source="hl7v2", message_id="EMPTY-1", received_at=utcnow(),
                        status="needs_match_review", programme="breast", error="no patient identifier")
    db.add(msg)
    db.flush()
    assert suggest_for_message(db, msg) == []
