"""Start-up bootstrap takes its OWN advisory lock (never the timed-jobs key 727001, which the running instance holds
for its whole life), so a new instance can finish bootstrap while the previous one is still serving. Mocked server
with session advisory-lock semantics; no live database."""
from __future__ import annotations

import pytest

from kareshafaa import bootstrap, worker_lock
from kareshafaa.worker_lock import BOOTSTRAP_LOCK_KEY, TIMED_JOBS_LOCK_KEY, PgAdvisoryLock

URL = "postgresql+psycopg://app:x@ep-cool-name-123-pooler.us-west-2.aws.neon.tech/neondb?sslmode=require"


class LockNotAvailable(Exception):
    sqlstate = "55P03"


class Srv:
    """Session advisory locks by key; a blocking pg_advisory_lock on a held key times out (lock_timeout)."""

    def __init__(self):
        self.holders: dict[int, object] = {}
        self.sql: list[tuple[str, dict | None]] = []
        self.urls: list[str] = []

    def engine(self, url):
        self.urls.append(url)
        return Eng(self)


class Eng:
    def __init__(self, srv):
        self.srv = srv

    def connect(self):
        return Conn(self.srv)

    def dispose(self):
        pass


class _Res:
    def __init__(self, v):
        self.v = v

    def scalar(self):
        return self.v


class Conn:
    def __init__(self, srv):
        self.srv, self.lock_timeout = srv, None

    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.srv.sql.append((sql, params))
        if "set_config('lock_timeout'" in sql:
            self.lock_timeout = int(params["v"])
            return _Res(params["v"])
        k = params["k"]
        if "pg_try_advisory_lock" in sql:
            got = self.srv.holders.setdefault(k, self) is self
            return _Res(got)
        if "pg_advisory_lock" in sql:
            if self.srv.holders.get(k, self) is not self:
                raise LockNotAvailable("canceling statement due to lock timeout")
            self.srv.holders[k] = self
            return _Res(None)
        if "pg_advisory_unlock" in sql:
            was = self.srv.holders.get(k) is self
            if was:
                del self.srv.holders[k]
            return _Res(was)
        if "pg_locks" in sql:
            return _Res(self.srv.holders.get(k) is self)
        raise AssertionError(sql)

    def close(self):
        for k in [k for k, v in self.srv.holders.items() if v is self]:
            del self.srv.holders[k]


def test_bootstrap_key_is_its_own():
    assert BOOTSTRAP_LOCK_KEY == 727003
    assert BOOTSTRAP_LOCK_KEY not in (727001, 727002) and TIMED_JOBS_LOCK_KEY == 727001


def test_running_timed_jobs_holder_does_not_block_bootstrap():
    srv = Srv()
    live = PgAdvisoryLock(URL, engine_factory=srv.engine)  # the old instance, still running
    assert live.acquire() and srv.holders[TIMED_JOBS_LOCK_KEY] is live._conn
    with bootstrap.postgres_bootstrap_lock(URL, engine_factory=srv.engine):
        assert BOOTSTRAP_LOCK_KEY in srv.holders
        assert srv.holders[TIMED_JOBS_LOCK_KEY] is live._conn  # untouched
    assert BOOTSTRAP_LOCK_KEY not in srv.holders and live.check()
    blocking = [(sql.split("(")[0].split()[-1], p["k"]) for sql, p in srv.sql
                if "pg_advisory_lock(" in sql or "pg_advisory_unlock(" in sql]
    assert blocking == [("pg_advisory_lock", BOOTSTRAP_LOCK_KEY), ("pg_advisory_unlock", BOOTSTRAP_LOCK_KEY)]
    assert all("-pooler" not in u for u in srv.urls)  # direct (unpooled) host for the session lock
    live.release()


def test_bootstrap_session_waits_long_not_the_request_pool_timeout():
    srv = Srv()
    seen = {}
    real = Conn.execute

    def spy(self, stmt, params=None):
        r = real(self, stmt, params)
        if "pg_advisory_lock(" in str(stmt):
            seen["lock_timeout"] = self.lock_timeout
        return r

    Conn.execute = spy
    try:
        with bootstrap.postgres_bootstrap_lock(URL, engine_factory=srv.engine):
            pass
    finally:
        Conn.execute = real
    assert seen["lock_timeout"] == bootstrap.BOOTSTRAP_LOCK_WAIT_MS == 120_000


def test_overlapping_bootstrap_times_out_with_a_clear_error():
    srv = Srv()
    with bootstrap.postgres_bootstrap_lock(URL, engine_factory=srv.engine):
        with pytest.raises(bootstrap.BootstrapLockTimeout, match="another bootstrap") as ei:
            with bootstrap.postgres_bootstrap_lock(URL, engine_factory=srv.engine):
                pytest.fail("must not get the lock")
        assert "727001" not in str(ei.value) and "neon.tech" not in str(ei.value)
    assert srv.holders == {}


def test_lock_engine_is_dedicated_and_has_no_short_lock_timeout(monkeypatch):
    seen = {}
    monkeypatch.setattr(bootstrap, "create_engine", lambda url, **kw: seen.update(kw) or "eng")
    assert bootstrap._bootstrap_lock_engine(worker_lock.direct_url(URL)) == "eng"
    from sqlalchemy.pool import NullPool
    assert seen["poolclass"] is NullPool and seen["isolation_level"] == "AUTOCOMMIT"
    assert "lock_timeout" not in str(seen["connect_args"])


def test_run_on_postgres_prepares_schema_inside_the_bootstrap_lock(monkeypatch):
    from kareshafaa.config import get_settings

    s = get_settings()
    srv = Srv()
    monkeypatch.setattr(s, "database_url", URL)
    monkeypatch.setattr(s, "worker_lock_database_url", None)
    monkeypatch.setattr(bootstrap, "_bootstrap_lock_engine", srv.engine)
    live = PgAdvisoryLock(URL, engine_factory=srv.engine)
    assert live.acquire()  # previous instance keeps the timed-jobs lock throughout

    def prepare(_s):
        assert BOOTSTRAP_LOCK_KEY in srv.holders  # init_db and data copy run under the lock
        return {"database": "postgresql", "action": "existing data kept"}

    monkeypatch.setattr(bootstrap, "_prepare", prepare)
    assert bootstrap.run()["action"] == "existing data kept"
    assert BOOTSTRAP_LOCK_KEY not in srv.holders and live.check()
    live.release()


def test_prepare_runs_init_db_first(monkeypatch):
    calls = []
    monkeypatch.setattr(bootstrap, "init_db", lambda: calls.append("init_db") or (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        bootstrap._prepare(None)
    assert calls == ["init_db"]
