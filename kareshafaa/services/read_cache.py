"""Short-lived read caches for the slow staff paths.

Case detail, the staff directory and the SLA table are read on every case
open. On a single-worker free host those reads stack up. Each cache is
per-process, bounded, and dropped on a domain write so a view is never more
than a few seconds behind a change. Writes still re-check the case row
version, so a cached view cannot cause a wrong update.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable

_LOCK = threading.Lock()
_STORE: dict[str, tuple[float, Any]] = {}
_GEN = 0


def bump() -> None:
    """Drop every cached value. Called when this process commits a domain write."""
    global _GEN
    with _LOCK:
        _GEN += 1
        _STORE.clear()


def get(key: str, ttl: float, load: Callable[[], Any]) -> Any:
    """Return a cached value, or compute and store it. ttl <= 0 disables."""
    if ttl <= 0:
        return load()
    now = time.monotonic()
    with _LOCK:
        hit = _STORE.get(key)
        gen = _GEN
    if hit and hit[0] == gen and now - hit[1] < ttl:
        return hit[2]
    val = load()
    with _LOCK:
        if len(_STORE) > 512:
            _STORE.clear()
        _STORE[key] = (_GEN, time.monotonic(), val)
    return val
