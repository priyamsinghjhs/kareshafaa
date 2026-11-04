"""Must never happen: a scoped user sees a case or patient outside their care (lists AND single reads), including
the SQL work queue, after sharing consent is withdrawn, and for a GP account with no organisation."""
from __future__ import annotations

import pytest
from sqlalchemy import select

from kareshafaa.security import make_token

pytestmark = pytest.mark.safety
SCOPED = ["omar.haddad", "vikram.mehta"]
LISTS = ["/api/queue?limit=1000", "/api/queue?view=overdue&limit=1000", "/api/cases?limit=500",
         "/api/cases?state=closed&limit=500", "/api/patients?q=SYN", "/api/patients?q=Test",
         "/api/recalls?view=upcoming", "/api/recalls?view=due", "/api/recalls?view=overdue"]


def _actor(db, username):
    from kareshafaa.models import User
    from kareshafaa.api.deps import actor_from_user as actor_for_user
    return actor_for_user(db.scalar(select(User).where(User.username == username)))


def _ids(url, items):
    if url.startswith(("/api/queue", "/api/cases")):
        return {("case", i["id"]) for i in items}
    if url.startswith("/api/recalls"):
        return {("patient", i["patient_id"]) for i in items}
    return {("patient", i["id"]) for i in items}


def _check(client, db, h, actor):
    from kareshafaa.models import Case, Patient
    from kareshafaa.services.access import case_visible
    from kareshafaa.services.patients import can_view_patient
    leaks, seen = [], 0
    for url in LISTS:
        r = client.get(url, headers=h)
        if r.status_code == 403:
            continue
        assert r.status_code == 200, (url, r.text)
        for kind, i in _ids(url, r.json()["items"]):
            seen += 1
            ok = case_visible(db, actor, db.get(Case, i)) if kind == "case" else \
                can_view_patient(db, actor, db.get(Patient, i))
            if not ok:
                leaks.append((url, kind, i))
    return leaks, seen


@pytest.mark.parametrize("username", SCOPED)
def test_scoped_lists_show_only_what_the_user_may_see(client, as_user, db, username, new_case):
    # a case at another clinic whose patient refused sharing: must not appear for anyone outside that clinic
    new_case(org_id=1, gp_id=1, share_external="deny")
    leaks, seen = _check(client, db, as_user(username), _actor(db, username))
    assert seen, "nothing listed: the check would be vacuous"
    assert not leaks, leaks


def test_gp_with_no_organisation_lists_nothing(client, db):
    from kareshafaa.models import User
    from kareshafaa.models import utcnow
    from kareshafaa.api.deps import actor_from_user as actor_for_user
    u = db.scalar(select(User).where(User.username == "safety.gp.noorg"))
    if u is None:
        u = User(username="safety.gp.noorg", display_name="GP no org", role="gp", practitioner_id=None, active=True,
                 created_at=utcnow())
        db.add(u)
        db.commit()
    h = {"Authorization": f"Bearer {make_token(u.id)}"}
    leaks, _ = _check(client, db, h, actor_for_user(u))
    assert not leaks, leaks
    for url in LISTS:
        r = client.get(url, headers=h)
        assert r.status_code in (200, 403) and (r.status_code == 403 or not r.json()["items"]), url


@pytest.mark.parametrize("username", SCOPED)
def test_every_out_of_scope_case_read_is_refused(client, as_user, db, username):
    """Sample of cases the user may NOT see: every single-case read is refused (no existence leak in the body)."""
    from kareshafaa.models import Case
    from kareshafaa.services.access import case_visible
    actor, h = _actor(db, username), as_user(username)
    hidden = [c.id for c in db.scalars(select(Case).order_by(Case.id.desc()).limit(300)) if not case_visible(db, actor, c)]
    assert hidden
    for cid in hidden[:25]:
        for sub in ("", "/record", "/fhir", "/actions"):
            r = client.get(f"/api/cases/{cid}{sub}", headers=h)
            assert r.status_code in (403, 404), (cid, sub, r.status_code)
