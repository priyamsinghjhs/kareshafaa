"""Shared fixtures: a small, freshly seeded synthetic database in a temp dir (never the demo DB)."""
from __future__ import annotations

import os
import random
from collections.abc import Callable, Iterator

import pytest

N_TEST_PATIENTS = 400


@pytest.fixture(scope="session", autouse=True)
def _env(tmp_path_factory) -> Iterator[None]:
    d = tmp_path_factory.mktemp("kareshafaa")
    os.environ.update({
        "KARESHAFAA_ENV": "test",
        "KARESHAFAA_DATABASE_URL": f"sqlite:///{d / 'test.db'}",
        "KARESHAFAA_DATA_DIR": str(d),
        "KARESHAFAA_MODEL_DIR": str(d / "models"),
        "KARESHAFAA_SECRET_KEY": "test-only-secret-not-for-production",
        "KARESHAFAA_DEMO_LOGINS": "true",
        "KARESHAFAA_SWEEP_INTERVAL_SECONDS": "0",
        "KARESHAFAA_DATA_STEPS": "off",
    })
    from kareshafaa.config import get_settings
    from kareshafaa.db import reset_engine

    get_settings.cache_clear()
    reset_engine()
    yield
    reset_engine()


@pytest.fixture(scope="session")
def seeded(_env) -> dict:
    from kareshafaa.seed import seed

    return seed(N_TEST_PATIENTS, 1234, train=True, verbose=False)


@pytest.fixture(scope="session")
def app(seeded):
    from kareshafaa.main import create_app

    return create_app()


@pytest.fixture(scope="session")
def client(app):
    from fastapi.testclient import TestClient

    with TestClient(app) as c:
        yield c


@pytest.fixture()
def db(seeded):
    from kareshafaa.db import session_factory

    s = session_factory()()
    try:
        yield s
    finally:
        s.rollback()
        s.close()


@pytest.fixture(scope="session")
def tokens(client) -> dict[str, str]:
    return {}


@pytest.fixture()
def as_user(client, tokens) -> Callable[[str], dict]:
    """Return auth headers for a demo user (bearer token)."""

    def _h(username: str) -> dict:
        if username not in tokens:
            r = client.post("/api/auth/demo-login", json={"username": username})
            assert r.status_code == 200, r.text
            tokens[username] = r.json()["token"]
            client.cookies.clear()  # tests authenticate explicitly with bearer tokens
        return {"Authorization": f"Bearer {tokens[username]}"}

    return _h


_counter = iter(range(10_000))


@pytest.fixture()
def make_patient(db) -> Callable[..., int]:
    """Create a fresh synthetic patient (deterministic channels) and return its id."""
    from kareshafaa.models import Consent, Patient
    from kareshafaa.models import utcnow

    def _mk(org_id: int = 3, gp_id: int = 19, *, smartphone: bool = False, phone_valid: bool = False,
            whatsapp: bool = False, language: str = "en", share_external: str | None = None) -> int:
        i = next(_counter)
        p = Patient(mrn=f"TEST-{i}-{random.randint(0, 10**9)}", given_name="Test", family_name=f"Patient{i}",
                    gender="female", birth_year=1970, age_band="50-59", nationality_group="emirati", language=language,
                    region="Abu Dhabi", phone_valid=phone_valid, has_smartphone=smartphone, whatsapp=whatsapp,
                    managing_org_id=org_id, gp_id=gp_id, responsiveness=0.8)
        db.add(p)
        db.flush()
        db.add(Consent(patient_id=p.id, scope="care_coordination", decision="permit", recorded_at=utcnow()))
        if share_external:
            db.add(Consent(patient_id=p.id, scope="share_external", decision=share_external, recorded_at=utcnow()))
        db.commit()
        return p.id

    return _mk


@pytest.fixture()
def new_case(client, as_user, make_patient) -> Callable[..., dict]:
    """Ingest an actionable screening result through the API and return the case summary."""

    def _nc(programme: str = "colorectal_fit", value: float | None = 250, value_text: str | None = None,
            unit: str | None = None, **pkw) -> dict:
        pid = make_patient(**pkw)
        if unit is None and value is not None:
            unit = {"colorectal_fit": "ug/g", "diabetes_hba1c": "%"}.get(programme)
        r = client.post("/api/results", headers=as_user("aisha.rahman"),
                        json={"patient_id": pid, "programme": programme, "value": value, "value_text": value_text,
                              "unit": unit})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["actionable"], body
        return body["case"]

    return _nc
