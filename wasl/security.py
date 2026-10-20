"""Authentication (signed tokens), actors and role-based permissions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass, field

from .config import get_settings

ROLES = ("patient", "coordinator", "gp", "specialist", "clinical_lead", "analyst", "admin")

_CLINICAL = {
    "case:read", "case:write", "queue:read", "comm:send", "consult:create", "consult:respond",
    "appointment:write", "lesson:write", "escalation:ack", "fhir:export", "consent:write",
}
PERMISSIONS: dict[str, frozenset[str]] = {
    "patient": frozenset({"self:read", "self:respond"}),
    "coordinator": frozenset(_CLINICAL | {"case:triage", "case:assign", "case:transfer", "referral:create",
                                          "call:record", "sweep:run", "case:create"}),
    "gp": frozenset(_CLINICAL | {"case:triage", "case:triage_urgent", "case:assign", "case:transfer",
                                 "referral:create", "careplan:write", "closure:authorise", "call:record",
                                 "confirm:order", "confirm:result", "case:create"}),
    "specialist": frozenset(_CLINICAL | {"referral:decide", "careplan:write", "closure:authorise",
                                         "case:triage_urgent", "confirm:result", "case:transfer"}),
    "clinical_lead": frozenset(_CLINICAL | {"case:triage", "case:triage_urgent", "case:assign", "case:transfer",
                                            "referral:create", "referral:decide", "careplan:write",
                                            "closure:authorise", "case:reopen", "call:record", "confirm:order",
                                            "confirm:result", "analytics:read", "audit:read", "model:read",
                                            "model:train", "model:approve", "model:rollback", "proposal:decide",
                                            "proposal:generate", "lesson:read", "sweep:run", "case:create",
                                            "simulation:run"}),
    "analyst": frozenset({"analytics:read", "model:read", "lesson:read"}),
    "admin": frozenset({"audit:read", "model:read", "model:train", "sweep:run", "proposal:generate",
                        "lesson:read", "config:read", "simulation:run", "analytics:read"}),
}
# Roles that see every case in the programme; others see only cases their organisation is involved in.
PROGRAMME_WIDE_ROLES = frozenset({"coordinator", "clinical_lead"})


def has_perm(role: str, perm: str) -> bool:
    return perm in PERMISSIONS.get(role, frozenset())


@dataclass(frozen=True)
class Actor:
    user_id: int | None
    role: str
    display: str
    practitioner_id: int | None = None
    patient_id: int | None = None
    org_id: int | None = None
    is_human: bool = True
    extra: dict = field(default_factory=dict, compare=False, hash=False)

    def can(self, perm: str) -> bool:
        return has_perm(self.role, perm)


SYSTEM = Actor(user_id=None, role="system", display="Wasl rules engine", is_human=False)
ML_SERVICE = Actor(user_id=None, role="ml_service", display="Outreach-priority model", is_human=False)


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def make_token(user_id: int, ttl_minutes: int | None = None) -> str:
    s = get_settings()
    payload = {"uid": user_id, "exp": int(time.time()) + 60 * (ttl_minutes or s.token_ttl_minutes)}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(s.secret_key.encode(), body.encode(), hashlib.sha256).digest())  # type: ignore[union-attr]
    return f"{body}.{sig}"


def verify_token(token: str) -> int | None:
    s = get_settings()
    try:
        body, sig = token.split(".", 1)
        expect = _b64(hmac.new(s.secret_key.encode(), body.encode(), hashlib.sha256).digest())  # type: ignore[union-attr]
        if not hmac.compare_digest(sig, expect):
            return None
        payload = json.loads(_unb64(body))
        if int(payload["exp"]) < time.time():
            return None
        return int(payload["uid"])
    except (ValueError, KeyError, json.JSONDecodeError):
        return None
