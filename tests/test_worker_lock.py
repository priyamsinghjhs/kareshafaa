"""One process runs the timed jobs: flock on SQLite (real), session advisory lock on PostgreSQL (mocked server that
behaves like pg_try_advisory_lock / pg_locks / pg_advisory_unlock, with session-end release)."""
from __future__ import annotations

import asyncio

import pytest

from kareshafaa import main, worker_lock
from kareshafaa.worker_lock import FileLock, PgAdvisoryLock, direct_url, looks_pooled


# ------------------------------------------------------------------------------------------------ SQLite path
def test_file_lock_single_holder_and_takeover(tmp_path):
    p = str(tmp_path / "x.lock")
    a, b = FileLock(p), FileLock(p)
    assert a.acquire() and a.check() and a.renew()
    assert not b.acquire() and not b.renew() and not b.held
    a.release()
    assert not a.held and b.renew() and b.held  # the other process takes over at its next check
    b.release()


def test_default_lock_is_file_lock_on_sqlite():
    lk = worker_lock.timed_jobs_lock()
    assert isinstance(lk, FileLock) and ".sla-sweeper-" in lk.path


def test_elect_and_release_in_main(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "_TIMED_JOBS_LOCK", None)
    p = str(tmp_path / "y.lock")
    monkeypatch.setattr(worker_lock, "timed_jobs_lock", lambda: FileLock(p))
    assert main._elect_sweeper() is True and main._elect_sweeper() is True  # re-check keeps it
    other = FileLock(p)
    assert not other.acquire()
    main._release_sweeper()
    assert main._TIMED_JOBS_LOCK is None and other.acquire()
    other.release()


def test_sweep_loop_runs_jobs_only_when_elected_and_releases_on_stop(monkeypatch):
    runs, released = [], []
    state = {"elected": [False, True, True]}

    def elect():
        return state["elected"].pop(0) if state["elected"] else True

    async def jobs():
        runs.append(1)

    monkeypatch.setattr(main, "_elect_sweeper", elect)
    monkeypatch.setattr(main, "_run_timed_jobs", jobs)
    monkeypatch.setattr(main, "_release_sweeper", lambda: released.append(1))

    async def go():
        t = asyncio.create_task(main._sweep_loop(0))
        for _ in range(50):
            await asyncio.sleep(0.01)
            if len(runs) >= 2:
                break
        t.cancel()
        with pytest.raises(asyncio.CancelledError):
            await t
    asyncio.run(go())
    assert len(runs) >= 2 and released == [1]  # not run while not elected; lock released on shutdown


# ------------------------------------------------------------------------------------------------ PostgreSQL path
class FakePg:
    """Advisory-lock semantics of one PostgreSQL server: one session holds a key; closing or losing the session
    releases it."""

    def __init__(self):
        self.holder = None
        self.connects = 0
        self.urls = []

    def engine(self, url):
        self.urls.append(url)
        return FakeEngine(self)


class FakeEngine:
    def __init__(self, srv):
        self.srv = srv

    def connect(self):
        if getattr(self.srv, "down", False):
            raise OSError("connection refused")
        self.srv.connects += 1
        return FakeConn(self.srv)

    def dispose(self):
        pass


class _Res:
    def __init__(self, v):
        self.v = v

    def scalar(self):
        return self.v


class FakeConn:
    def __init__(self, srv):
        self.srv, self.alive = srv, True

    def execute(self, stmt, params=None):
        if not self.alive:
            raise OSError("server closed the connection unexpectedly")
        sql = str(stmt)
        assert params == {"k": worker_lock.TIMED_JOBS_LOCK_KEY}
        if "pg_try_advisory_lock" in sql:
            if self.srv.holder is None:
                self.srv.holder = self
            return _Res(self.srv.holder is self)
        if "pg_locks" in sql:
            assert "pg_backend_pid()" in sql and "objsubid = 1" in sql
            return _Res(self.srv.holder is self)
        if "pg_advisory_unlock" in sql:
            was = self.srv.holder is self
            if was:
                self.srv.holder = None
            return _Res(was)
        raise AssertionError(sql)

    def close(self):
        self.alive = False
        if self.srv.holder is self:  # session end releases session-level advisory locks
            self.srv.holder = None

    def drop(self):  # network cut / database restart: the server ends the session
        self.close()


URL = "postgresql+psycopg://app:x@ep-cool-name-123-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require"


def test_only_one_container_holds_the_pg_lock():
    srv = FakePg()
    a = PgAdvisoryLock(URL, engine_factory=srv.engine)
    b = PgAdvisoryLock(URL, engine_factory=srv.engine)
    assert a.acquire() and a.held
    assert not b.acquire() and not b.held  # its connection is closed again, not kept open
    assert a.check() and a.renew() and not b.renew()
    a.release()
    assert srv.holder is None and not a.held
    assert b.renew() and srv.holder is b._conn


def test_pg_lock_uses_direct_neon_host():
    srv = FakePg()
    PgAdvisoryLock(URL, engine_factory=srv.engine).acquire()
    assert "-pooler" not in srv.urls[0] and "ep-cool-name-123.us-west-2.aws.neon.tech" in srv.urls[0]
    assert direct_url("postgresql+psycopg://u:p@db.internal:5432/k") == "postgresql+psycopg://u:p@db.internal:5432/k"
    assert looks_pooled("postgresql://u:p@h:6432/d") and looks_pooled("postgresql://u:p@x-pooler.h/d")
    assert not looks_pooled(direct_url(URL))


def test_lost_session_is_detected_and_lock_taken_over():
    srv = FakePg()
    a = PgAdvisoryLock(URL, engine_factory=srv.engine)
    b = PgAdvisoryLock(URL, engine_factory=srv.engine)
    assert a.acquire()
    a._conn.drop()  # the database released a's lock with its session
    assert b.renew()  # another container takes over at its next interval
    assert not a.check() and not a.held  # a notices and stops running the jobs
    assert not a.renew()  # and cannot re-take it while b holds it
    b._conn.drop()
    assert a.renew() and a.held  # a re-takes it on a NEW connection
    assert srv.connects >= 4


def test_database_unreachable_means_not_elected():
    srv = FakePg()
    srv.down = True
    a = PgAdvisoryLock(URL, engine_factory=srv.engine)
    assert a.acquire() is False and not a.held
    srv.down = False
    assert a.acquire() is True


def test_timed_jobs_lock_picks_pg_on_postgres(monkeypatch):
    from kareshafaa.config import get_settings

    s = get_settings()
    monkeypatch.setattr(s, "database_url", URL)
    monkeypatch.setattr(s, "worker_lock_database_url", None)
    lk = worker_lock.timed_jobs_lock()
    assert isinstance(lk, PgAdvisoryLock) and lk.key == 727001 and lk.url == URL
    monkeypatch.setattr(s, "worker_lock_database_url", "postgresql+psycopg://u:p@direct.host:5432/d")
    assert worker_lock.timed_jobs_lock().url.endswith("direct.host:5432/d")


def test_pg_engine_is_dedicated_autocommit_without_pool():
    lk = PgAdvisoryLock(URL)
    eng = lk._default_engine(direct_url(URL))
    from sqlalchemy.pool import NullPool
    assert isinstance(eng.pool, NullPool)
    assert eng.dialect.name == "postgresql"
    assert eng.dialect._on_connect_isolation_level == "AUTOCOMMIT"  # never "idle in transaction" while held
    eng.dispose()
