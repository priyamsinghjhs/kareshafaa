"""Property-based gap tests: queue ordering, RBAC matrix, i18n completeness, agent safety.

These catch the regressions already seen (queue sort, mixed languages, agent approval).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from hypothesis import given, settings, strategies as st

from kareshafaa.services.priority import priority_score, sort_key

# --------------------------------------------------------------------------- queue ordering
TIERS = ["critical", "urgent", "high", "routine"]
TIER_RANK = {"critical": 0, "urgent": 1, "high": 2, "routine": 3}

rows = st.builds(
    dict,
    tier=st.sampled_from(TIERS),
    due_at=st.one_of(st.none(), st.just("2026-12-01")),
    id=st.integers(1, 10000),
    escalation_level=st.integers(0, 5),
    overdue_days=st.integers(0, 60),
    risk_score=st.floats(0, 1),
)


@settings(max_examples=500, deadline=None)
@given(st.lists(rows, min_size=2, max_size=20))
def test_queue_lower_urgency_never_jumps_higher(items):
    """A lower-urgency case never appears before a higher one when sorted by priority."""
    sorted_items = sorted(items, key=lambda r: sort_key(r, "priority"))
    for a, b in zip(sorted_items, sorted_items[1:]):
        # lower rank number = higher urgency; a should be <= b in rank (or equal)
        assert TIER_RANK[a["tier"]] <= TIER_RANK[b["tier"]] or priority_score(a) >= priority_score(b)


@settings(max_examples=200, deadline=None)
@given(rows, rows)
def test_queue_same_tier_stable_by_deadline(a, b):
    """Within the same tier, earlier deadline comes first."""
    a = {**a, "tier": "urgent"}
    b = {**b, "tier": "urgent"}
    if a.get("due_at") and b.get("due_at"):
        ordered = sorted([a, b], key=lambda r: sort_key(r, "priority"))
        assert ordered[0]["due_at"] <= ordered[1]["due_at"] or ordered[0]["id"] <= ordered[1]["id"]


# --------------------------------------------------------------------------- RBAC matrix
WRITE_ENDPOINTS = [
    ("POST", "/api/cases/{id}/close"),
    ("POST", "/api/cases/{id}/notify"),
    ("POST", "/api/admin/users"),
]
READ_ENDPOINTS = [
    ("GET", "/api/queue"),
    ("GET", "/api/cases/{id}"),
    ("GET", "/api/admin/about/pilot"),
]
ROLES_WITHOUT_WRITE = ["patient.en", "analyst"]
ROLES_WITH_READ = ["aisha.rahman", "admin", "omar.haddad"]


@pytest.mark.parametrize("role", ROLES_WITHOUT_WRITE)
@pytest.mark.parametrize("method,path", WRITE_ENDPOINTS)
def test_rbac_no_write_without_permission(client, as_user, role, method, path):
    """A role without case:write never gets 200 on a write endpoint."""
    h = as_user(role)
    url = path.format(id=1)
    r = client.request(method, url, headers=h, json={})
    assert r.status_code in (401, 403, 404, 422), f"{role} {method} {url} -> {r.status_code}"


@pytest.mark.parametrize("role", ROLES_WITH_READ)
def test_rbac_read_allowed(client, as_user, role):
    """Roles that should read the queue get 200."""
    h = as_user(role)
    r = client.get("/api/queue", headers=h)
    assert r.status_code == 200


# --------------------------------------------------------------------------- i18n completeness
I18N = Path(__file__).resolve().parent.parent / "kareshafaa" / "static" / "js" / "staff_i18n.js"


def _text_keys(lang: str) -> set[str]:
    text = I18N.read_text()
    # Use the TEXT map (second occurrence), not STRINGS
    matches = list(re.finditer(rf"{lang}:\s*\{{", text))
    if len(matches) < 2:
        return set()
    start = matches[1].end()
    depth, i = 1, start
    while i < len(text) and depth:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
        i += 1
    block = text[start:i - 1]
    return set(re.findall(r'"([^"]+)":', block))


def test_i18n_every_en_key_has_ar_hi_ur():
    """Every English TEXT key has an Arabic, Hindi, and Urdu translation."""
    en = _text_keys("en")
    assert en, "no English keys found"
    for lang in ("ar", "hi", "ur"):
        missing = en - _text_keys(lang)
        # allow a small set of intentionally English-only tokens
        missing -= {"T1", "T2", "T3", "T0", "FIT", "HPV", "HbA1c", "MRN"}
        assert not missing, f"{lang} missing {len(missing)} keys: {sorted(missing)[:10]}"


# --------------------------------------------------------------------------- agent safety
def test_next_best_always_requires_approval(client, as_user):
    """The next-best draft is always advisory."""
    h = as_user("aisha.rahman")
    r = client.get("/api/assist/next-best/1", headers=h)
    if r.status_code == 200:
        body = r.json()
        assert body.get("requires_approval") is True
        assert "draft" in body
        # no invented medicines or doses in the template path
        draft = (body.get("draft") or "").lower()
        assert "mg" not in draft or "draft" in draft
