"""Versioned static URLs: /static/<path>?v=<content hash>. A changed file gets a new URL, so browsers may keep every
versioned file for a year (Cache-Control: immutable) and still always load the current one after a deploy."""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

STATIC = Path(__file__).resolve().parent / "static"
LONG_CACHE = "public, max-age=31536000, immutable"
SHORT_CACHE = "public, max-age=300"  # unversioned static URLs (manifest icons, samples, old bookmarks)


@lru_cache(maxsize=256)
def _hash(rel: str) -> str:
    try:
        return hashlib.sha256((STATIC / rel).read_bytes()).hexdigest()[:10]
    except OSError:
        return "0"


def asset(rel: str) -> str:
    rel = rel.lstrip("/")
    if rel.startswith("static/"):
        rel = rel[len("static/"):]
    return f"/static/{rel}?v={_hash(rel)}"


def cache_control(query: str) -> str:
    return LONG_CACHE if "v=" in (query or "") else SHORT_CACHE
