"""Ordering clinician, screening site and specimen collection time for a typed-in result (POST /api/results).

Same rules as message ingest (services/ingest.py): a time without an offset is UAE local time; a specimen older
than STALE_RESULT_DAYS goes to clinician review (``stale_result``). A typed-in collection time in the future is
refused outright (it is a typing mistake the person can fix), where a lab message is kept and flagged instead.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

from ..models import utcnow
from .errors import Invalid
from .ingest import FUTURE_TOLERANCE, STALE_RESULT_DAYS

_UAE = timezone(timedelta(hours=4))
_CTRL = re.compile(r"[\x00-\x1f\x7f]")


def _text(v: str | None, what: str) -> str | None:
    v = (v or "").strip()
    if not v:
        return None
    if _CTRL.search(v):
        raise Invalid(f"{what} contains control characters")
    if len(v) < 2:
        raise Invalid(f"{what} is too short")
    return v[:200]


def to_utc(t: datetime) -> datetime:
    return (t if t.tzinfo else t.replace(tzinfo=_UAE)).astimezone(timezone.utc).replace(tzinfo=None)


def check_result_meta(ordering: str | None, site: str | None, collected: datetime | None,
                      now: datetime | None = None) -> tuple[dict, list[str]]:
    now = now or utcnow()
    extra: list[str] = []
    at = to_utc(collected) if collected is not None else None
    if at is not None:
        if at > now + FUTURE_TOLERANCE:
            raise Invalid("specimen collection date is in the future")
        if at.year < 2000:
            raise Invalid("specimen collection date is not plausible")
        if now - at > timedelta(days=STALE_RESULT_DAYS):
            extra.append("stale_result")
    return ({"ordering_clinician": _text(ordering, "ordering clinician"), "screening_site": _text(site, "screening site"),
             "collected_at": at}, extra)
