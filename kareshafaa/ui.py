"""View layer: role-aware navigation, role home pages, server-side page access and plain-language labels.

Page access here is in ADDITION to the API permission checks (every /api route enforces its own permission);
it stops a role from even opening another role's screens."""
from __future__ import annotations

from .accounts import ROLE_LABELS
from .security import has_perm
from .programmes import WORKING_DAY_TIERS
from .sla import load_default_hours

FOOTER = ("KareShafaa v0.1 · Prototype software, not a certified medical device. "
          "Clinical decisions remain with the treating clinician.")

# icon keys refer to inline SVG symbols in templates/_icons.html
NAV: dict[str, list[tuple[str, str, str]]] = {  # role -> [(href, label, icon)]
    "coordinator": [("/queue", "Today's queue", "list"), ("/ingest", "Results inbox", "inbox")],
    "gp": [("/queue?view=review", "Needs review", "check"), ("/queue", "All my cases", "list"),
           ("/consults", "Ask a specialist", "chat")],
    "specialist": [("/queue?view=review", "Needs review", "check"), ("/queue", "My cases", "list"),
                   ("/consults", "Consult requests", "chat")],
    "clinical_lead": [("/overview", "Overview", "home"), ("/queue", "Work queue", "list"),
                      ("/governance", "Approvals", "shield"), ("/dashboard", "Outcomes", "chart"),
                      ("/audit", "Audit trail", "key")],
    "analyst": [("/dashboard", "Outcomes", "chart"), ("/governance", "Models & rules", "shield")],
    "admin": [("/admin/users", "Users", "users"), ("/admin/system", "System", "home"), ("/dashboard", "Outcomes", "chart"),
              ("/advanced", "Advanced", "tools")],
    "translator": [("/governance?tab=content", "Content sign-off", "shield")],
    "patient": [("/patient", "My results", "heart")],
}

HOME: dict[str, str] = {"coordinator": "/queue", "gp": "/queue?view=review", "specialist": "/queue?view=review",
                        "clinical_lead": "/overview", "analyst": "/dashboard", "admin": "/admin/users",
                        "translator": "/governance?tab=content", "patient": "/patient"}

# page path -> any-of permissions (None = any signed-in user)
PAGE_PERMS: dict[str, tuple[str, ...] | None] = {
    "/queue": ("queue:read",), "/cases": ("case:read",), "/patient": ("self:read",),
    "/dashboard": ("analytics:read",), "/governance": ("model:read", "content:read"), "/audit": ("audit:read",),
    "/walkthrough": ("user:admin",), "/ingest": ("case:create",), "/consults": ("consult:respond",),
    "/overview": ("proposal:decide",), "/advanced": ("config:read",), "/admin/users": ("user:admin",), "/admin/system": ("user:admin",),
    "/account/password": None,
}


def home_for(role: str) -> str:
    return HOME.get(role, "/account/password")


def nav_for(role: str) -> list[dict]:
    return [{"href": h, "label": l, "icon": i} for h, l, i in NAV.get(role, [])]


def can_open(role: str, page: str) -> bool:
    perms = PAGE_PERMS.get(page, ())
    if perms is None:
        return True
    return any(has_perm(role, p) for p in perms)


def _fmt_hours(h: float, working: bool) -> str:
    if h < 24:
        return f"{h:g} hour{'s' if h != 1 else ''}"
    days = h / 24
    unit = "working day" if working else "day"
    return f"{days:g} {unit}{'s' if days != 1 else ''}"


def tier_help() -> dict:
    """Plain-language meaning of each urgency tier, from the ACTIVE default SLA table (first step: triage of an
    issued result). Display only - the clocks themselves are computed by kareshafaa/sla.py."""
    first = load_default_hours()["issued"]
    names = [("critical", "T0", "Critical"), ("urgent", "T1", "Urgent"), ("high", "T2", "Soon"), ("routine", "T3", "Routine")]
    out = {}
    for i, (key, code, name) in enumerate(names):
        working = key in WORKING_DAY_TIERS  # T2/T3 clocks count UAE working days; T0/T1 run 24/7
        out[key] = {"code": code, "name": name, "first_action": _fmt_hours(first[i], working),
                    "text": f"{name} ({code}): first action within {_fmt_hours(first[i], working)}"
                            f"{'' if working else ', day or night'}"}
    return out


def role_label(role: str) -> str:
    return ROLE_LABELS.get(role, role.replace("_", " ").capitalize())
