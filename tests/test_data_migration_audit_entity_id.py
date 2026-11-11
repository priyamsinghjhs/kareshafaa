"""The sample-data email/insurance step failed on Postgres because its name did not fit audit entity_id.

PostgreSQL enforces VARCHAR(n); SQLite does not. The start-up data step
`backfill_sample_email_insurance_answer_v1` (41 characters) was written as
`audit_events.entity_id` (then VARCHAR(40)). SQLAlchemy logged the
`data_migration.applied` INSERT and Postgres raised DataError, so the step was
never marked applied and retried at every start. The column (and the matching
fallback-audit column) is now VARCHAR(80), in line with `data_migrations.name`.
"""
from __future__ import annotations

from kareshafaa import audit
from kareshafaa.db import _widen_string_columns, get_engine
from kareshafaa.models import AuditEvent, AuditFallbackEvent, DataMigration
from kareshafaa.security import SYSTEM
from kareshafaa.services.sample_data import MIGRATIONS

EMAIL_INSURANCE_STEP = "backfill_sample_email_insurance_answer_v1"


def test_data_migration_names_fit_audit_entity_id_and_document_the_postgres_typemod():
    # Postgres VARCHAR(40) rejects a 41-character value with DataError; 80 is enough.
    assert len(EMAIL_INSURANCE_STEP) == 41
    assert len(EMAIL_INSURANCE_STEP) > 40
    assert EMAIL_INSURANCE_STEP in [n for n, _ in MIGRATIONS]
    for model in (AuditEvent, AuditFallbackEvent):
        limit = model.entity_id.type.length
        assert limit is not None and limit >= 80
        assert all(len(name) <= limit for name, _ in MIGRATIONS)
    assert DataMigration.name.type.length >= max(len(n) for n, _ in MIGRATIONS)


def test_data_migration_applied_audit_accepts_the_email_insurance_step_name(db):
    ev = audit.record(db, SYSTEM, "U", "data_migration.applied", entity_type="DataMigration",
                      entity_id=EMAIL_INSURANCE_STEP, detail={"emails": 0, "has_insurance_yes": 0,
                                                              "has_insurance_no": 0})
    db.flush()
    assert ev.entity_id == EMAIL_INSURANCE_STEP
    loaded = db.get(AuditEvent, ev.id)
    assert loaded is not None and loaded.entity_id == EMAIL_INSURANCE_STEP
    db.rollback()


def test_widen_string_columns_is_a_no_op_on_sqlite():
    eng = get_engine()
    assert eng.dialect.name == "sqlite"
    _widen_string_columns(eng)  # must not raise or rewrite the SQLite schema
