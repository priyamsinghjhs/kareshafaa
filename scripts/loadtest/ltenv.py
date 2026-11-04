"""Shared environment guard for the load-test scripts: every script works on a throw-away SQLite database in a
temporary data directory and refuses anything else (never a hosted or production database)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def setup(data_dir: str | os.PathLike, *, sweep_off: bool = True, queue_cache_ttl: float | None = None) -> Path:
    d = Path(data_dir).resolve()
    if REPO in d.parents or d == REPO:
        sys.exit(f"refusing: data dir {d} is inside the repository; use a temp dir such as /tmp/lt/5000")
    d.mkdir(parents=True, exist_ok=True)
    os.environ.pop("DATABASE_URL", None)  # never pick up a hosted connection string from the shell
    os.environ["KARESHAFAA_DATABASE_URL"] = f"sqlite:///{d / 'kareshafaa.db'}"
    os.environ["KARESHAFAA_DATA_DIR"] = str(d)
    os.environ["KARESHAFAA_MODEL_DIR"] = str(d / "models")
    os.environ["KARESHAFAA_DEMO_CREDENTIALS_FILE"] = str(d / "credentials.txt")
    os.environ.setdefault("KARESHAFAA_MODE", "standard")
    if sweep_off:
        os.environ["KARESHAFAA_SWEEP_INTERVAL_SECONDS"] = "0"
    if queue_cache_ttl is not None:
        os.environ["KARESHAFAA_QUEUE_CACHE_TTL_S"] = str(queue_cache_ttl)
    sys.path.insert(0, str(REPO))
    return d


def read_credentials(data_dir: str | os.PathLike) -> dict[str, str]:
    """username -> password from the local credentials file the app writes at start-up (never printed)."""
    out: dict[str, str] = {}
    for line in (Path(data_dir) / "credentials.txt").read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = parts[2]
    return out
