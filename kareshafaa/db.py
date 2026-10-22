"""Database engine/session. Swap SQLite for Postgres by changing KARESHAFAA_DATABASE_URL."""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine, _SessionLocal
    if _engine is None:
        cfg = get_settings()
        url = cfg.database_url
        kwargs: dict = {"future": True, "pool_pre_ping": True}
        memory = url.startswith("sqlite") and (":memory:" in url or url.rstrip("/") in ("sqlite:", "sqlite:"))
        if not memory:
            # round 21: bounded waits everywhere - a saturated server answers 503 + Retry-After (main.py) instead of
            # holding requests for SQLAlchemy's default 30 s pool wait. Same settings serve Postgres.
            kwargs.update(pool_size=cfg.db_pool_size, max_overflow=cfg.db_max_overflow,
                          pool_timeout=cfg.db_pool_timeout_s, pool_recycle=1800)
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False, "timeout": cfg.db_busy_timeout_s}
        elif url.startswith("postgresql"):
            ms = int(cfg.db_busy_timeout_s * 1000)
            kwargs["connect_args"] = {"options": f"-c lock_timeout={ms} -c idle_in_transaction_session_timeout=60000"}
        _engine = create_engine(url, **kwargs)
        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _sqlite_pragmas(dbapi_conn, _rec):  # pragma: no cover - trivial
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA synchronous=NORMAL")
                cur.close()

        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, autoflush=True)
    return _engine


def reset_engine() -> None:
    """Used by tests to point at a fresh database."""
    global _engine, _SessionLocal
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _SessionLocal = None


def session_factory() -> sessionmaker[Session]:
    get_engine()
    assert _SessionLocal is not None
    return _SessionLocal


@contextmanager
def session_scope() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
    finally:
        s.close()


APPEND_ONLY_TRIGGERS_SQLITE = [
    """CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;""",
    """CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_events
       BEGIN SELECT RAISE(ABORT, 'audit_events is append-only'); END;""",
]

APPEND_ONLY_TRIGGERS_PG = [
    """CREATE OR REPLACE FUNCTION kareshafaa_audit_append_only() RETURNS trigger AS $$
       BEGIN RAISE EXCEPTION 'audit_events is append-only'; END; $$ LANGUAGE plpgsql;""",
    "DROP TRIGGER IF EXISTS audit_append_only ON audit_events;",
    """CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_events
       FOR EACH ROW EXECUTE FUNCTION kareshafaa_audit_append_only();""",
]


def init_db(drop: bool = False) -> None:
    """Create tables, missing indexes and append-only triggers. Serialised with a file lock so several
    uvicorn workers starting together do not race on DDL."""
    import os

    from .config import get_settings

    d = get_settings().data_dir
    d.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(d / ".init.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover
            pass
        _init_db(drop)
    finally:
        os.close(fd)  # closing the descriptor releases the lock


def _init_db(drop: bool) -> None:
    from . import models  # noqa: F401  (register tables)

    eng = get_engine()
    if drop:
        if eng.dialect.name == "sqlite" and eng.url.database not in (None, "", ":memory:"):
            import os

            path = eng.url.database
            reset_engine()
            for suffix in ("", "-wal", "-shm"):
                if os.path.exists(path + suffix):
                    os.remove(path + suffix)
            eng = get_engine()
        else:
            Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    _add_missing_columns(eng)
    # create_all skips indexes added later to existing tables: add any that are missing (idempotent)
    for table in Base.metadata.sorted_tables:
        for idx in table.indexes:
            idx.create(eng, checkfirst=True)
    stmts = APPEND_ONLY_TRIGGERS_SQLITE if eng.dialect.name == "sqlite" else APPEND_ONLY_TRIGGERS_PG
    with eng.begin() as conn:
        for st in stmts:
            conn.execute(text(st))


def _add_missing_columns(eng: Engine) -> None:
    """create_all never alters an existing table: add columns introduced later (nullable or with a constant
    default) so older databases keep working. Idempotent; only ever ADDs columns, never drops or rewrites data."""
    from sqlalchemy import inspect

    insp = inspect(eng)
    for table in Base.metadata.sorted_tables:
        if not insp.has_table(table.name):
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            ddl_type = col.type.compile(dialect=eng.dialect)
            default = ""
            if col.default is not None and getattr(col.default, "is_scalar", False):
                v = col.default.arg
                default = f" DEFAULT {('TRUE' if v else 'FALSE') if isinstance(v, bool) else repr(v)}"
            with eng.begin() as conn:
                conn.execute(text(f'ALTER TABLE {table.name} ADD COLUMN {col.name} {ddl_type}{default}'))
