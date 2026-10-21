"""Round 23: per-request time budget with all-or-nothing writes.

Python cannot kill a thread, so cancellation is cooperative at the database boundary, and the guarantees are about
DATA, not CPU:

* When a request passes `request_timeout_s`, the middleware answers 503 + Retry-After at once (the client is never
  left hanging), logs the stuck thread's stack, counts it (`TIMEOUTS`, shown on /healthz) and writes an
  `http.request_timeout` audit event.
* Before cutting off, it marks the request timed out under a lock. From then on, every SQL statement and every
  COMMIT from that request raises `RequestTimedOut`, so the stuck thread can never save anything later.
* Its open transaction is rolled back at once. If the thread is inside a SQL statement, that statement is
  interrupted (`sqlite3.Connection.interrupt`) and the thread's own error path rolls back. If the thread is stuck
  in Python code, the session is rolled back from the timeout handler. Either way the writer lock is released,
  so other requests are not blocked behind it.
* A request that has ALREADY STARTED or FINISHED a commit is never cut off: it is allowed to finish (logged as
  slow). A 503 "nothing was saved" is therefore always true, and a request is never left half-saved.

Residual gap: the stuck thread keeps its pool thread until its Python code returns. The admission limit and the
round-22 watchdog (thread-pool heartbeat) bound the damage.
"""
from __future__ import annotations

import contextvars
import logging
import sys
import threading
import traceback

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

log = logging.getLogger("wasl")
TIMEOUTS = {"count": 0, "slow_committing": 0}


class RequestTimedOut(Exception):
    """The request ran past its time budget; nothing more may be read or saved on its behalf."""


class RequestState:
    def __init__(self, path: str) -> None:
        self.path = path
        self.lock = threading.Lock()
        self.timed_out = False
        self.committing = 0
        self.committed = False
        self.threads: set[int] = set()
        self.sessions: list[Session] = []
        self.active_conns: dict[int, object] = {}  # thread id -> dbapi connection currently executing


CURRENT: contextvars.ContextVar[RequestState | None] = contextvars.ContextVar("wasl_request", default=None)


@event.listens_for(Engine, "before_cursor_execute")
def _before_exec(conn, _cursor, _stmt, _params, _ctx, _many):
    st = CURRENT.get()
    if st is None:
        return
    with st.lock:
        if st.timed_out:
            raise RequestTimedOut(f"request {st.path} timed out; statement refused")
        tid = threading.get_ident()
        st.threads.add(tid)
        st.active_conns[tid] = conn.connection.dbapi_connection


@event.listens_for(Engine, "after_cursor_execute")
def _after_exec(_conn, _cursor, _stmt, _params, _ctx, _many):
    st = CURRENT.get()
    if st is not None:
        with st.lock:
            st.active_conns.pop(threading.get_ident(), None)


@event.listens_for(Engine, "handle_error")
def _on_error(_exc_ctx):
    st = CURRENT.get()
    if st is not None:
        with st.lock:
            st.active_conns.pop(threading.get_ident(), None)


@event.listens_for(Session, "after_begin")
def _register_session(session, _tx, _conn):
    st = CURRENT.get()
    if st is not None:
        with st.lock:
            if st.timed_out:
                raise RequestTimedOut(f"request {st.path} timed out; transaction refused")
            if session not in st.sessions:
                st.sessions.append(session)


@event.listens_for(Session, "before_commit")
def _before_commit(_session):
    st = CURRENT.get()
    if st is None:
        return
    with st.lock:
        if st.timed_out:
            raise RequestTimedOut(f"request {st.path} timed out; commit refused (nothing saved)")
        st.committing += 1


def _commit_ended(_session, committed: bool) -> None:
    st = CURRENT.get()
    if st is None:
        return
    with st.lock:
        if st.committing:
            st.committing -= 1
        st.committed = st.committed or committed


event.listen(Session, "after_commit", lambda s: _commit_ended(s, True))


def try_cut_off(st: RequestState) -> bool:
    """Mark the request timed out unless it has started/finished a commit. Returns True if it was cut off."""
    with st.lock:
        if st.committing or st.committed:
            return False
        st.timed_out = True
        return True


def abort_transactions(st: RequestState) -> None:
    """Called (in a worker thread) after try_cut_off: interrupt running statements, roll back idle sessions."""
    with st.lock:
        running = dict(st.active_conns)
        sessions = list(st.sessions)
    for dbapi in running.values():
        interrupt = getattr(dbapi, "interrupt", None)  # sqlite3; the owner thread then rolls back itself
        if interrupt:
            try:
                interrupt()
            except Exception:  # noqa: BLE001
                log.warning("could not interrupt a statement of a timed-out request", exc_info=True)
    if running:
        return
    for s in sessions:  # the owner thread is in Python code, not in the database: roll back for it
        try:
            if s.in_transaction():
                s.rollback()  # releases the audit writer lock (after_rollback) and the SQLite write lock
        except Exception:  # noqa: BLE001
            log.warning("rollback of a timed-out request's session failed", exc_info=True)


def stacks(st: RequestState) -> str:
    frames = sys._current_frames()
    ids = st.threads or {t.ident for t in threading.enumerate() if "worker" in t.name.lower() or "anyio" in t.name.lower()}
    out = []
    for tid in ids:
        f = frames.get(tid)
        if f is not None:
            out.append(f"--- thread {tid} ---\n" + "".join(traceback.format_stack(f)))
    return "\n".join(out) or "(no request thread found)"
