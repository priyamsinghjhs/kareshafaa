"""Runs last (file name): after every other test has changed the shared database through the API and services,
the stored work-queue SLA thresholds must still equal a fresh recompute (catches writes that bypass the ORM hook)."""
from kareshafaa import queue_store as qs


def test_stored_thresholds_still_current_after_whole_suite(db):
    assert qs.refresh(db.connection(), None, write=False) == 0, qs.last_stale[:5]
    db.rollback()
