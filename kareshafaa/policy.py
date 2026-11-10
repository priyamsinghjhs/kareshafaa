"""Clinical policy version and status (one place).

The clinical rules (kareshafaa/clinical_rules.py: cut-offs, tiers, pathway targets, review floors), the step clocks
(kareshafaa/sla.py) and the escalation ladder (kareshafaa/alert_policy.py) are prototype values: placeholder, verify.
They have NOT been approved by a named clinical governance body. There is deliberately no "approved" switch in code:
approval needs a named approver and an approval reference recorded here by a reviewed change, and until then the
``live`` clinical profile is refused at startup (config.get_settings).

Every result decision records the policy version in its audit event (``result.received``), so a later change of
policy can be traced to the cases it affected.
"""
from __future__ import annotations

POLICY_VERSION = "2026.11-p1"
POLICY_STATUS = "placeholder"           # placeholder, verify
POLICY_STATUS_NOTE = "placeholder, verify"
APPROVED_BY: str | None = None          # named clinical approver (role and name); none yet
APPROVAL_REF: str | None = None         # governance minute / document reference; none yet
COVERS = ("clinical_rules", "sla", "alert_policy", "statemachine")

PROFILES = ("synthetic", "live")


def is_approved() -> bool:
    return POLICY_STATUS == "approved" and bool(APPROVED_BY) and bool(APPROVAL_REF)


def summary() -> dict:
    from .statemachine import STATE_MACHINE_VERSION
    return {"version": POLICY_VERSION, "status": POLICY_STATUS, "note": POLICY_STATUS_NOTE,
            "approved": is_approved(), "approved_by": APPROVED_BY, "approval_ref": APPROVAL_REF,
            "state_machine_version": STATE_MACHINE_VERSION, "covers": list(COVERS)}


def check_profile(profile: str) -> None:
    """Refuse an unknown profile, and the live profile while the policy is not approved."""
    if profile not in PROFILES:
        raise RuntimeError(f"KARESHAFAA_CLINICAL_PROFILE must be one of {', '.join(PROFILES)}")
    if profile == "live" and not is_approved():
        raise RuntimeError(f"KARESHAFAA_CLINICAL_PROFILE=live refused: clinical policy {POLICY_VERSION} is "
                           f"'{POLICY_STATUS_NOTE}' (no named approver or approval reference). Use 'synthetic'.")
