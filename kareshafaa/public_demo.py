"""Public demo safety mode (KARESHAFAA_PUBLIC_DEMO=1). Off by default; normal mode is unchanged.

When on:
* every page shows a "Synthetic data demo, not for clinical use" banner;
* admin-only / destructive actions are refused with 403 for everyone (all public-demo users are demo logins):
  model training, approval, rejection and rollback; rule-change proposals; content sign-off; SLA sweeps and the
  simulation re-run; and, defensively, any PUT/PATCH/DELETE and any user/role/admin/reset endpoint;
* API writes are rate-limited per session (or per client IP before login): KARESHAFAA_DEMO_WRITE_LIMIT writes per
  KARESHAFAA_DEMO_WRITE_WINDOW_S seconds, answered 429 + Retry-After beyond that (per worker process);
* the demo database is restored from a pristine seed copy (KARESHAFAA_DEMO_SEED_DB, default
  data/kareshafaa_demo_seed.db; created from the current database on first use) on every server start
  (`python -m kareshafaa.serve`, or `python -m kareshafaa.public_demo` before uvicorn) and nightly at
  KARESHAFAA_DEMO_RESET_HOUR (UAE time, default 03:00) by one elected worker.
"""
from __future__ import annotations

import asyncio
import fcntl
import logging
import os
import re
import sqlite3
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy.engine import make_url

from .config import Settings, get_settings

log = logging.getLogger("kareshafaa.public_demo")
UAE = ZoneInfo("Asia/Dubai")
BANNER = "Synthetic data demo, not for clinical use"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

# (method, path regex) refused in public demo mode
BLOCKED: tuple[tuple[str, re.Pattern[str]], ...] = tuple((m, re.compile(p)) for m, p in (
    ("POST", r"^/api/governance/models/train$"),
    ("POST", r"^/api/governance/models/[^/]+/(approve|reject)$"),
    ("POST", r"^/api/governance/models/rollback$"),
    ("POST", r"^/api/governance/proposals/(generate|[^/]+/decide)$"),
    ("POST", r"^/api/governance/content/signoff$"),
    ("POST", r"^/api/sla/sweep$"),
    ("POST", r"^/api/analytics/simulation/run$"),
    ("*", r"^/api/(admin|users?|roles?|reset|seed)(/|$)"),  # user creation/role/reset/deactivate included
    ("POST", r"^/api/auth/password$"),  # shared demo accounts keep the password the organisers set
    ("PUT", r"^/api/"), ("PATCH", r"^/api/"), ("DELETE", r"^/api/"),
))
BLOCKED_DETAIL = ("disabled in the public synthetic-data demo (model training/promotion, sweeps, data reset, "
                  "user/role edits and deletes are turned off here)")
# also refused: password changes of the shared demo accounts (POST /api/auth/password)


READ_ONLY_ALLOWED = frozenset({("GET", "/api/admin/users")})  # the user list stays viewable (read only)


def is_blocked(method: str, path: str) -> bool:
    if (method, path) in READ_ONLY_ALLOWED:
        return False
    return any((m == "*" or m == method) and rx.search(path) for m, rx in BLOCKED)


class WriteRateLimiter:
    """Sliding-window limit on API writes per key (session token or client IP), per process."""

    def __init__(self, limit: int, window_s: float) -> None:
        self.limit, self.window = limit, window_s
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str, now: float | None = None) -> float:
        """Record one write; returns 0 if allowed, else seconds until the next write is allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault(key, deque())
            while q and now - q[0] >= self.window:
                q.popleft()
            if len(q) >= self.limit:
                return max(0.1, self.window - (now - q[0]))
            q.append(now)
            if len(self._hits) > 10_000:  # bound memory: drop idle keys
                for k in [k for k, v in self._hits.items() if not v or now - v[-1] >= self.window]:
                    self._hits.pop(k, None)
            return 0.0


def client_key(headers, cookies, client_host: str | None, cookie_name: str) -> str:
    auth = headers.get("authorization", "")
    tok = auth[7:].strip() if auth.lower().startswith("bearer ") else cookies.get(cookie_name)
    if tok:
        return "s:" + tok[-32:]
    fwd = headers.get("x-forwarded-for", "")
    return "ip:" + (fwd.split(",")[0].strip() or client_host or "?")


# ---------------------------------------------------------------------------------------- reset from a seed copy
def _sqlite_path(s: Settings) -> Path | None:
    u = make_url(s.database_url)
    if not u.drivername.startswith("sqlite") or not u.database or u.database == ":memory:":
        return None
    return Path(u.database).resolve()


def seed_copy_path(s: Settings) -> Path:
    return Path(s.demo_seed_db) if s.demo_seed_db else s.data_dir / "kareshafaa_demo_seed.db"


def _backup(src: Path, dst: Path) -> None:
    a, b = sqlite3.connect(src), sqlite3.connect(dst, timeout=30)
    try:
        a.backup(b)
    finally:
        a.close()
        b.close()


def _writer_lock(s: Settings, db: Path):
    """Hold the audit-chain writer lock (same file as kareshafaa.audit) so a reset never interleaves a write."""
    from .audit import lock_key

    fd = os.open(str(s.data_dir / f".audit-writer-{lock_key(str(db))}.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX)
    return fd


def reset_from_seed(s: Settings | None = None) -> dict:
    """Restore the demo database from the seed copy (creating the seed copy from the current DB if missing)."""
    s = s or get_settings()
    db = _sqlite_path(s)
    if db is None:
        return {"reset": False, "reason": "public-demo reset supports SQLite databases only"}
    seed = seed_copy_path(s)
    if not db.exists():
        return {"reset": False, "reason": f"database {db} does not exist (run the seed first)"}
    if not seed.exists():
        _backup(db, seed)
        log.warning("public demo: no seed copy found; snapshot of the current database saved as %s", seed)
        return {"reset": False, "seed_created": str(seed)}
    fd = _writer_lock(s, db)
    try:
        _backup(seed, db)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
    try:
        from .services import queue as _q
        _q._BASE_CACHE.bump()  # this process's work-queue cache (others expire within queue_cache_ttl_s)
    except Exception:  # noqa: BLE001
        pass
    log.warning("public demo: database %s restored from seed copy %s", db, seed)
    return {"reset": True, "from": str(seed), "to": str(db)}


def next_reset_at(now: datetime, hour: int) -> datetime:
    local = now.astimezone(UAE)
    at = local.replace(hour=hour, minute=0, second=0, microsecond=0)
    if at <= local:
        at += timedelta(days=1)
    return at


def _elect_nightly_resetter(s: Settings) -> int | None:
    fd = os.open(str(s.data_dir / ".demo-reset.lock"), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd  # held for the life of the process
    except BlockingIOError:
        os.close(fd)
        return None


async def nightly_reset_loop() -> None:
    s = get_settings()
    if _elect_nightly_resetter(s) is None:
        return
    while True:
        at = next_reset_at(datetime.now(UAE), s.demo_reset_hour)
        await asyncio.sleep(max(1.0, (at - datetime.now(UAE)).total_seconds()))
        try:
            await asyncio.to_thread(reset_from_seed, s)
            await asyncio.to_thread(_after_reset)
        except Exception:  # noqa: BLE001
            log.exception("public demo: nightly reset failed")


def _after_reset() -> None:
    """The seed copy may predate newer columns and holds no demo passwords: migrate, then re-provision."""
    from .accounts import provision_on_startup
    from .db import init_db

    init_db()
    provision_on_startup()


def main() -> int:
    """CLI: restore the demo DB from its seed copy if KARESHAFAA_PUBLIC_DEMO is on (no-op otherwise)."""
    logging.basicConfig(level=logging.INFO)
    s = get_settings()
    if not s.public_demo and "--force" not in sys.argv:
        print("public demo mode is off; nothing reset")
        return 0
    print(reset_from_seed(s))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
