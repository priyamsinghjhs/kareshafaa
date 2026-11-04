"""Must never happen: a duplicate patient identity created silently (no audited 'create anyway' reason)."""
from __future__ import annotations

import threading
from collections import Counter

import pytest
from sqlalchemy import func, select

from tests.test_patient_registration import person

pytestmark = pytest.mark.safety
CO = "aisha.rahman"


def _concurrent(client, h, bodies):
    out, bar = [], threading.Barrier(len(bodies))

    def go(b):
        bar.wait()
        out.append(client.post("/api/patients", headers=h, json=b).status_code)

    ts = [threading.Thread(target=go, args=(b,)) for b in bodies]
    [t.start() for t in ts]
    [t.join() for t in ts]
    return sorted(out)


def test_concurrent_registrations_with_the_same_emirates_id_create_one_record(client, as_user, db):
    from kareshafaa.models import Patient
    body = person()
    codes = _concurrent(client, as_user(CO), [body] * 4)
    assert codes == [200, 409, 409, 409], codes
    assert db.scalar(select(func.count()).select_from(Patient).where(Patient.national_id == body["national_id"])) == 1


def test_concurrent_registrations_of_the_same_person_without_an_id_need_a_reason(client, as_user, db):
    from kareshafaa.models import Patient
    body = person(national_id=None, given_name="Mariam", family_name="Concurrent Qx", birth_date="1979-05-06")
    codes = _concurrent(client, as_user(CO), [body] * 3)
    assert codes == [200, 409, 409], codes
    assert db.scalar(select(func.count()).select_from(Patient).where(Patient.family_name == "Concurrent Qx")) == 1


def test_unknown_patient_in_a_lab_message_never_creates_a_patient(client, as_user, db):
    from kareshafaa.models import Patient
    before = db.scalar(select(func.count()).select_from(Patient))
    h = as_user(CO)
    from tests.test_ingest import hl7
    r = client.post("/api/ingest/hl7v2", headers={**h, "Content-Type": "text/plain"},
                    content=hl7("SYN-NOT-A-PATIENT-9", "300", "SAFETY-1"))
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.scalar(select(func.count()).select_from(Patient)) == before


def test_no_duplicate_identity_exists_anywhere(db):
    """Global scan: no two records share an Emirates ID, and every registration that matched an existing record
    carries the audited override (who, why)."""
    from kareshafaa.models import AuditEvent, Patient
    from kareshafaa.services.patients import duplicate_candidates
    ids = Counter(db.scalars(select(Patient.national_id).where(Patient.national_id.is_not(None))))
    assert not [e for e, n in ids.items() if n > 1], "two patient records share an Emirates ID"
    registered = set(db.scalars(select(AuditEvent.entity_id).where(AuditEvent.event == "patient.registered")))
    overridden = set(db.scalars(select(AuditEvent.entity_id).where(AuditEvent.event == "patient.duplicate_override")))
    silent = []
    for p in db.scalars(select(Patient).where(Patient.id.in_([int(i) for i in registered if str(i).isdigit()]))):
        cands = duplicate_candidates(db, given=p.given_name, family=p.family_name, birth_date=p.birth_date,
                                     national_id=p.national_id, exclude_id=p.id)
        older = [c for c in cands["same_emirates_id"] + cands["similar"] if c["id"] < p.id]
        if older and str(p.id) not in {str(o) for o in overridden}:
            silent.append((p.id, [c["id"] for c in older]))
    assert not silent, f"duplicates created without an audited reason: {silent[:5]}"
