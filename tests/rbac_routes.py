"""Route enumeration and the intended access policy for every route (used by tests/test_rbac_matrix.py and the
safety suite). The route list is read from the app's router, so a new route fails the tests until it is classified
here. Policies:

  "perm:<p>"     the route itself requires permission <p> (``require(p)``); allowed = roles holding <p>
  "page:<key>"   server-rendered page guarded by ``ui.PAGE_PERMS[key]`` (anonymous -> redirect to /login)
  "inner:<p|p>"  any signed-in user reaches the handler, which refuses roles lacking one of the listed permissions
  "roles:<r|r>"  any signed-in user reaches the handler, which refuses every role not listed
  "signed_in"    any signed-in user (own account: /api/auth/me, password change)
  "public"       no sign-in (sign-in pages, health checks, PWA files, dev/test sign-in endpoints)
"""
from __future__ import annotations

from kareshafaa.security import PERMISSIONS, ROLES
from kareshafaa.ui import PAGE_PERMS

POLICY: dict[str, str] = {
    # ---- sign-in, own account
    "POST /api/auth/login": "public", "POST /api/auth/logout": "public",
    "POST /api/auth/password": "signed_in", "GET /api/auth/me": "signed_in",
    "GET /api/auth/demo-users": "public", "POST /api/auth/demo-login": "public",  # dev/test only (docs/operating_modes.md)
    # ---- administration
    "GET /api/admin/users": "perm:user:admin", "POST /api/admin/users": "perm:user:admin",
    "POST /api/admin/users/{uid}/role": "perm:user:admin", "POST /api/admin/users/{uid}/reset-password": "perm:user:admin",
    "POST /api/admin/users/{uid}/deactivate": "perm:user:admin", "POST /api/admin/users/{uid}/activate": "perm:user:admin",
    "GET /api/admin/system": "perm:user:admin", "POST /api/admin/system/result-reconciliation/run": "perm:user:admin",
    "GET /api/ingest/reconciliation": "perm:results:inbox", "POST /api/ingest/reconciliation/run": "perm:results:inbox",
    "POST /api/ingest/reconciliation/{item_id}/resolve": "perm:results:inbox", "POST /api/admin/system/audit-fallback/reconcile": "perm:user:admin", "POST /api/admin/system/ml-switch": "perm:ml:switch",
    "POST /api/admin/system/mail/test": "perm:user:admin",
    "GET /api/admin/about": "perm:config:read", "GET /api/admin/about/openapi.json": "perm:config:read",
    # ---- cases
    "GET /api/cases": "perm:case:read", "GET /api/cases/{case_id}": "inner:case:read",
    "GET /api/cases/{case_id}/actions": "perm:case:read", "GET /api/cases/{case_id}/fhir": "perm:fhir:export",
    "POST /api/cases/{case_id}/transition": "perm:case:read", "POST /api/cases/{case_id}/close": "perm:case:write",
    "POST /api/cases/{case_id}/review": "perm:case:triage_urgent", "POST /api/cases/{case_id}/critical-read-back": "perm:critical:readback", "POST /api/cases/{case_id}/reopen": "perm:case:reopen",
    "POST /api/cases/{case_id}/assign": "perm:case:assign", "POST /api/cases/{case_id}/transfer": "perm:case:transfer",
    "POST /api/cases/{case_id}/transfer/decision": "perm:case:read", "POST /api/cases/{case_id}/notify": "perm:comm:send",
    "POST /api/cases/{case_id}/call": "perm:call:record", "POST /api/cases/{case_id}/teach-back": "perm:call:record",
    "GET /api/cases/{case_id}/teach-back-form": "perm:case:read",
    "POST /api/cases/{case_id}/confirmatory": "perm:confirm:order",
    "POST /api/cases/{case_id}/confirmatory/result": "perm:confirm:result",
    "POST /api/cases/{case_id}/referral": "perm:referral:create",
    "POST /api/cases/{case_id}/referral/decision": "perm:referral:decide",
    "POST /api/cases/{case_id}/appointment": "perm:appointment:write",
    "POST /api/cases/{case_id}/appointment/reschedule": "perm:appointment:write",
    "POST /api/cases/{case_id}/appointment/cancel": "perm:appointment:write",
    "POST /api/cases/{case_id}/appointment/remind": "perm:comm:send",
    "POST /api/cases/{case_id}/appointment/requests/{request_id}/resolve": "perm:appointment:write",
    "POST /api/cases/{case_id}/attendance": "perm:appointment:write",
    "POST /api/cases/{case_id}/care-plan": "perm:careplan:write", "GET /api/cases/{case_id}/record": "perm:case:read",
    "POST /api/cases/{case_id}/encounters": "perm:record:write", "POST /api/cases/{case_id}/medications": "perm:record:write",
    "POST /api/cases/{case_id}/medications/{med_id}/stop": "perm:record:write",
    "POST /api/cases/{case_id}/decline": "perm:consent:write", "POST /api/cases/{case_id}/reconsider": "perm:case:write",
    "POST /api/cases/{case_id}/consent": "perm:consent:write",
    "POST /api/cases/{case_id}/patient-deceased": "perm:closure:authorise",
    "POST /api/cases/{case_id}/patient-deceased/revoke": "perm:case:reopen",
    "POST /api/cases/{case_id}/consults": "perm:consult:create", "POST /api/consults/{consult_id}/respond": "perm:consult:respond",
    "GET /api/consults": "perm:consult:respond", "POST /api/cases/{case_id}/lessons": "perm:lesson:write",
    "POST /api/escalations/{esc_id}/ack": "perm:escalation:ack", "POST /api/results": "perm:case:create",
    "POST /api/assist/suggest": "perm:case:read",  # draft note text only; the handler also checks case access
    # ---- patient app (own record only)
    "GET /api/me/case": "perm:self:read", "POST /api/me/teach-back": "perm:self:respond",
    "POST /api/me/teach-back/reopen": "perm:self:respond", "POST /api/me/teach-back/reopen/cancel": "perm:self:respond",
    "POST /api/me/consult": "perm:self:respond", "POST /api/me/decline": "perm:self:respond",
    "POST /api/me/consent": "perm:self:respond", "POST /api/me/language": "perm:self:respond",
    "POST /api/me/display-language": "perm:self:read", "GET /api/me/profile": "perm:self:read",
    "POST /api/me/appointments/{appt_id}/confirm": "perm:self:respond",
    "POST /api/me/appointments/{appt_id}/change-request": "perm:self:respond",
    "POST /api/me/requests/screening": "perm:self:respond", "POST /api/me/requests/follow-up": "perm:self:respond",
    "POST /api/me/requests/{request_id}/withdraw": "perm:self:respond",
    "POST /api/me/contact-consent": "perm:self:respond", "POST /api/me/contact": "perm:self:respond",
    # ---- queue, operations, analytics, audit
    "GET /api/queue": "perm:queue:read", "POST /api/sla/sweep": "perm:sweep:run",
    "GET /api/analytics/kpis": "perm:analytics:read", "GET /api/analytics/impact": "perm:analytics:read",
    "GET /api/analytics/fairness": "perm:analytics:read", "GET /api/analytics/lessons": "perm:lesson:read",
    "GET /api/analytics/simulation": "perm:analytics:read", "POST /api/analytics/simulation/run": "perm:simulation:run",
    "GET /api/directory/practitioners": "perm:case:read", "GET /api/directory/organizations": "perm:case:read",
    "GET /api/audit": "perm:audit:read", "GET /api/audit/verify": "perm:audit:verify",
    # ---- governance
    "GET /api/governance/models": "perm:model:read", "GET /api/governance/models/{version}": "perm:model:read",
    "GET /api/governance/models/{version}/card": "perm:model:read", "POST /api/governance/models/train": "perm:model:train",
    "POST /api/governance/models/{version}/approve": "perm:model:approve",
    "POST /api/governance/models/{version}/reject": "perm:model:approve",
    "POST /api/governance/models/rollback": "perm:model:rollback", "GET /api/governance/drift": "perm:model:read",
    "GET /api/governance/proposals": "perm:lesson:read", "POST /api/governance/proposals/generate": "perm:proposal:generate",
    "POST /api/governance/proposals/{proposal_id}/decide": "perm:proposal:decide",
    "GET /api/governance/sla-rules": "perm:lesson:read", "GET /api/governance/config": "perm:lesson:read",
    "GET /api/governance/content": "perm:content:read",
    "POST /api/governance/content/signoff": "perm:content:read",  # + content:approve / content:translation_review inside
    "GET /api/governance/rule-cards": "perm:model:read", "GET /api/governance/overrides": "perm:audit:read",
    "GET /api/governance/data-protection": "perm:model:read",
    # ---- results inbox (programme-level lab feed)
    "POST /api/ingest/hl7v2": "perm:results:inbox", "POST /api/ingest/fhir": "perm:results:inbox",
    "POST /api/ingest/fhir-history": "perm:record:write", "GET /api/ingest/sample": "perm:results:inbox",
    "GET /api/ingest/messages": "perm:results:inbox", "POST /api/ingest/messages/{msg_id}/resolve": "perm:results:inbox",
    "GET /api/demo/scenarios": "perm:user:admin", "POST /api/demo/scenarios/{key}": "perm:user:admin",
    # ---- patients, recalls, summaries, patient requests, sign-in help
    "GET /api/patients": "perm:patient:read", "GET /api/patients/options": "perm:patient:read",
    "GET /api/patients/prefill": "perm:patient:register", "POST /api/patients": "perm:patient:register",
    "GET /api/patients/{patient_id}": "perm:patient:read",
    "POST /api/patients/{patient_id}/demographics": "perm:patient:write",
    "POST /api/patients/{patient_id}/contact": "perm:patient:write", "POST /api/patients/{patient_id}/care": "perm:patient:write",
    "POST /api/patients/{patient_id}/insurance": "perm:patient:write",
    "POST /api/patients/{patient_id}/contacts": "perm:patient:write",
    "POST /api/patients/{patient_id}/account-invite": "perm:patient:invite",
    "POST /api/patients/{patient_id}/account-invite/revoke": "perm:patient:invite",
    "GET /api/recalls": "inner:recall:manage|recall:read", "GET /api/recalls/summary": "inner:recall:manage|recall:read",
    "POST /api/recalls/{recall_id}/invite": "perm:recall:manage", "POST /api/recalls/invite": "perm:recall:manage",
    "POST /api/recalls/{recall_id}/cancel": "perm:recall:manage",
    "GET /api/gp-summaries": "perm:summary:ack", "GET /api/gp-summaries/{sid}": "inner:case:read",
    "POST /api/gp-summaries/{sid}/ack": "perm:summary:ack",
    "GET /api/patient-requests": "perm:queue:read", "POST /api/patient-requests/{request_id}/confirm": "perm:appointment:write",
    "POST /api/patient-requests/{request_id}/decline": "perm:appointment:write",
    "GET /api/signin-help": "roles:coordinator|admin",  # patient resets: coordinators; staff requests: admins
    "POST /api/signin-help/{request_id}/close": "roles:coordinator|admin",
    # ---- public files and pages
    "GET /manifest.webmanifest": "public", "GET /sw.js": "public", "GET /starting": "public", "GET /offline": "public",
    "GET /favicon.ico": "public", "GET /healthz": "public", "GET /readyz": "public", "GET /fhir/metadata": "public",
    "GET /": "public", "GET /login": "public", "POST /login": "public", "GET /activate": "public",
    "GET /reset-password": "public", "POST /activate": "public", "POST /reset-password": "public",
    "GET /staff-reset-password": "public", "POST /staff-reset-password": "public",
    "GET /patient-reset-password": "public", "POST /patient-reset-password": "public",
    "GET /forgot-username": "public", "GET /forgot-password": "public", "POST /forgot-username": "public",
    "POST /forgot-password": "public", "POST /logout": "public",
    "GET /account/password": "page:/account/password", "POST /account/password": "public",  # checks the session itself
    # ---- staff and patient pages
    **{f"GET {p}": f"page:{p}" for p in ("/queue", "/patient", "/dashboard", "/governance", "/audit", "/walkthrough",
                                         "/ingest", "/consults", "/overview", "/advanced", "/admin/users", "/admin/system",
                                         "/admin/about", "/patients", "/patients/new", "/recalls", "/summaries")},
    "GET /cases/{case_id}": "page:/cases", "GET /patients/{patient_id}": "page:/patients",
}


def flat_routes(app):
    """(method, path, route) for every HTTP route, including routers mounted with include_router."""
    def walk(routes):
        for r in routes:
            if type(r).__name__ == "_IncludedRouter":
                yield from r.effective_candidates()
            else:
                yield r
    for r in walk(app.routes):
        if hasattr(r, "dependant") and getattr(r, "methods", None):
            for m in sorted(r.methods - {"HEAD"}):
                yield m, r.path, r


def route_perms(dependant) -> list[str]:
    """Permissions required through ``require(...)`` anywhere in the route's dependency tree."""
    out = []
    for d in dependant.dependencies:
        if getattr(d.call, "__qualname__", "").startswith("require.<locals>"):
            out.append(d.call.__closure__[0].cell_contents)
        out += route_perms(d)
    return out


def allowed_roles(policy: str) -> set[str]:
    kind, _, arg = policy.partition(":")
    if kind == "perm":
        return {r for r in ROLES if arg in PERMISSIONS[r]}
    if kind == "inner":
        return {r for r in ROLES if any(p in PERMISSIONS[r] for p in arg.split("|"))}
    if kind == "roles":
        return set(arg.split("|"))
    if kind == "page":
        perms = PAGE_PERMS[arg]
        return set(ROLES) if perms is None else {r for r in ROLES if any(p in PERMISSIONS[r] for p in perms)}
    return set(ROLES)  # signed_in / public
