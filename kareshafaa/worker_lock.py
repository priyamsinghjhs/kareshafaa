"""Election of the ONE process that runs the timed jobs (SLA sweep, retention purge, audit-fallback reconciliation).

* PostgreSQL: a session-level advisory lock, ``pg_try_advisory_lock(TIMED_JOBS_LOCK_KEY)``, taken on a DEDICATED
  connection (not from the request pool, autocommit, TCP keepalives) and held for the life of the worker. Only one
  session in the whole database cluster can hold it, so exactly one process across every container and every
  uvicorn worker runs the timed jobs. Before each run the holder re-checks that its session still holds the lock
  (``pg_locks``); if the connection was lost (database restart, network cut, Neon compute restart) the lock is gone
  with it, the process stops running jobs and tries to take the lock again on a new connection. Processes that lost
  the election retry every interval, so another container takes over within one interval if the holder dies. The
  lock is released (``pg_advisory_unlock`` and connection closed) at shutdown; if the process is killed the database
  releases it when the session ends.

  Session-level advisory locks need a real, unpooled session: use the database's DIRECT connection. Behind a
  transaction-mode pooler (PgBouncer, Neon's ``-pooler`` host) the lock would belong to whichever server connection
  happened to run the statement, so it can be released or "held" by the wrong client. For Neon the lock connection
  therefore drops ``-pooler`` from the host automatically; any other pooler needs
  ``KARESHAFAA_WORKER_LOCK_DATABASE_URL`` set to a direct URL.
* SQLite (one server): a non-blocking ``flock`` on a lock file per database in DATA_DIR, held for the life of the
  process (only processes on the same host can share a SQLite file anyway).
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger("kareshafaa")
# Fixed 64-bit advisory-lock keys, one per purpose (never share a key between purposes):
#   727001 = timed-jobs worker (held for the life of the elected process)
#   727002 = one-time data steps (services/sample_data.py)
#   727003 = start-up bootstrap (bootstrap.py; held only while bootstrap runs). It must NOT be 727001: the running
#            instance holds 727001 for its whole life, so a new instance waiting on it at start-up never finishes and
#            the deploy fails (Render keeps the old instance until the new one is healthy).
#   4242   = audit writer, per transaction
TIMED_JOBS_LOCK_KEY = 727001
DATA_STEPS_LOCK_KEY = 727002
BOOTSTRAP_LOCK_KEY = 727003


def direct_url(url: str) -> str:
    """The URL for the lock's dedicated session: Neon's pooled host (ep-xxx-pooler.region.aws.neon.tech) mapped to the
    direct host (ep-xxx.region.aws.neon.tech). Other URLs are returned unchanged."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    host = u.host or ""
    if host.endswith(".neon.tech") and "-pooler." in host:
        return u.set(host=host.replace("-pooler.", ".", 1)).render_as_string(hide_password=False)
    return url


def looks_pooled(url: str) -> bool:
    """Heuristic: a pooler host name or PgBouncer's usual ports (6432; 6543 at Supabase)."""
    from sqlalchemy.engine import make_url

    u = make_url(url)
    return "pooler" in (u.host or "") or u.port in (6432, 6543)


class PgAdvisoryLock:
    """Session-level advisory lock on a dedicated autocommit connection. `engine_factory` is injectable for tests."""

    def __init__(self, url: str, key: int = TIMED_JOBS_LOCK_KEY, engine_factory=None) -> None:
        self.url, self.key = url, key
        self._engine_factory = engine_factory or self._default_engine
        self._engine = None
        self._conn = None

    def _default_engine(self, url: str):
        from sqlalchemy import create_engine
        from sqlalchemy.pool import NullPool

        # no pooler, no statement/idle-in-transaction timeouts on this session; keepalives detect a dead link
        return create_engine(url, poolclass=NullPool, isolation_level="AUTOCOMMIT",
                             connect_args={"application_name": "kareshafaa-timed-jobs", "keepalives": 1,
                                           "keepalives_idle": 30, "keepalives_interval": 10, "keepalives_count": 3,
                                           "connect_timeout": 10})

    @property
    def held(self) -> bool:
        return self._conn is not None

    def acquire(self) -> bool:
        """Try once (never waits). True = this process now runs the timed jobs."""
        from sqlalchemy import text

        if self._conn is not None:
            return self.check()
        try:
            if self._engine is None:
                self._engine = self._engine_factory(direct_url(self.url))
            conn = self._engine.connect()
        except Exception as e:  # noqa: BLE001 - database not reachable: not elected this time
            log.warning("timed-jobs lock: cannot connect (%s); will retry", type(e).__name__)
            return False
        try:
            got = bool(conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": self.key}).scalar())
        except Exception as e:  # noqa: BLE001
            log.warning("timed-jobs lock: query failed (%s); will retry", type(e).__name__)
            _close(conn)
            return False
        if not got:
            _close(conn)
            return False
        self._conn = conn
        return True

    def check(self) -> bool:
        """Still the holder? Verifies on the lock's own session; a lost session drops the lock (re-acquired next time)."""
        from sqlalchemy import text

        if self._conn is None:
            return False
        try:
            ok = bool(self._conn.execute(text(
                "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype = 'advisory' AND granted "
                "AND pid = pg_backend_pid() AND classid = (CAST(:k AS bigint) >> 32)::oid "
                "AND objid = (CAST(:k AS bigint) & 4294967295)::oid "
                "AND objsubid = 1)"), {"k": self.key}).scalar())
        except Exception as e:  # noqa: BLE001 - connection gone: the database already released the lock
            log.warning("timed-jobs lock: lost the lock session (%s)", type(e).__name__)
            ok = False
        if not ok:
            _close(self._conn)
            self._conn = None
        return ok

    def renew(self) -> bool:
        """check(), and if the lock was lost try to take it again at once (another process may have it by now)."""
        return self.check() or self.acquire()

    def release(self) -> None:
        from sqlalchemy import text

        if self._conn is not None:
            try:
                self._conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": self.key})
            except Exception:  # noqa: BLE001 - session gone: nothing to release
                pass
            _close(self._conn)
            self._conn = None
        if self._engine is not None:
            self._engine.dispose()
            self._engine = None


class FileLock:
    """SQLite: one process per database (same host) via a non-blocking flock held for the life of the process."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._fd: int | None = None

    @property
    def held(self) -> bool:
        return self._fd is not None

    def acquire(self) -> bool:
        if self._fd is not None:
            return True
        try:
            import fcntl
        except ImportError:  # pragma: no cover - non-POSIX: single process
            return True
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            os.close(fd)
            return False
        self._fd = fd
        return True

    def check(self) -> bool:
        return self._fd is not None

    def renew(self) -> bool:
        return self.check() or self.acquire()

    def release(self) -> None:
        if self._fd is not None:
            os.close(self._fd)  # closing the descriptor releases the flock
            self._fd = None


def _close(conn) -> None:
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass


def timed_jobs_lock():
    """The lock for this database: PgAdvisoryLock on PostgreSQL, FileLock on SQLite."""
    from sqlalchemy.engine import make_url

    from .audit import lock_key
    from .config import get_settings

    s = get_settings()
    u = make_url(s.database_url)
    if u.get_backend_name() == "postgresql":
        url = s.worker_lock_database_url or s.database_url
        if looks_pooled(direct_url(url)):
            log.warning("timed-jobs lock: the database URL looks like a connection pooler (PgBouncer); session advisory "
                        "locks are unreliable there - set KARESHAFAA_WORKER_LOCK_DATABASE_URL to the direct URL")
        return PgAdvisoryLock(url)
    ident = os.path.realpath(u.database) if u.database and u.database != ":memory:" else str(u)
    # one sweeper per DATABASE (round 21: a shared lock let a server on another database "own" the sweep)
    return FileLock(str(s.data_dir / f".sla-sweeper-{lock_key(ident)}.lock"))
