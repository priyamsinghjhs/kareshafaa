"""User administration (admin role only): list, create, change role/site/team, reset password, (de)activate.

There is no hard delete: deactivation keeps the account (and every audit reference to it) and signs it out.
In public demo mode every write here is refused by the public-demo guard (kareshafaa/public_demo.py); the list
stays readable so the screen can be shown."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import accounts, audit
from ..db import get_db
from ..models import utcnow, Organization, Practitioner, User
from ..programmes import PROGRAMMES
from ..security import Actor
from ..services.errors import Forbidden, Invalid, NotFound
from .deps import require
from .schemas import StaffRecordIn, UserCreateIn, UserNoteIn, UserResetIn, UserRoleIn

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


def _get(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if u is None:
        raise NotFound("user not found")
    return u


ORG_KIND_LABELS = {"screening_programme": "Screening programme office", "primary_care": "GP practice (family health centre)",
                   "hospital": "Hospital / specialist clinic", "lab": "Laboratory"}
SPECIALTY_LABELS = {"gastroenterology": "Gastroenterology", "gynaecology": "Colposcopy / Gynaecology",
                    "breast_surgery": "Breast clinic", "endocrinology": "Endocrinology"}

# What a staff directory record needs for each clinical role. Non-clinical roles (analyst, admin, translator) have
# no staff record: they never own cases, and a record would give them an organisation (= case access scope).
STAFF_RULES: dict[str, dict] = {
    "coordinator": {"org_kinds": ["screening_programme"], "org_label": "Programme office", "org_noun": "programme office", "noun": "care coordinator",
                    "programme": "required",
                    "specialty": None},
    "clinical_lead": {"org_kinds": ["screening_programme"], "org_label": "Programme office", "org_noun": "programme office", "noun": "clinical lead",
                      "programme": "optional",
                      "specialty": None},
    "gp": {"org_kinds": ["primary_care"], "org_label": "GP practice", "org_noun": "GP practice", "noun": "GP", "programme": None, "specialty": None},
    "specialist": {"org_kinds": ["hospital"], "org_label": "Hospital / clinic", "org_noun": "hospital or clinic",
                   "noun": "specialist doctor", "programme": None, "specialty": "required"},
}
assert set(STAFF_RULES) == set(accounts.CLINICAL_ROLES)


def _bad(msg: str, field: str | None = None) -> Invalid:
    e = Invalid(msg)
    if field:
        e.extra = {"field": field}  # the form puts the message next to this field
    return e


def _specialties(db: Session) -> dict[str, str]:
    out = dict(SPECIALTY_LABELS)
    for (k,) in db.execute(select(Practitioner.specialty).where(Practitioner.role == "specialist").distinct()):
        if k and k not in out:
            out[k] = k.replace("_", " ").capitalize()
    return out


def _check_role(role: str) -> None:
    if role not in accounts.ASSIGNABLE_ROLES:
        raise _bad("unknown or non-assignable role. Patient app accounts are not created here: a care coordinator or GP opens Patients → the patient record → Patient app account → Create account, which sends a one-time activation code", "role")


def _link_existing(db: Session, role: str, pid: int, user_id: int | None = None) -> Practitioner:
    label = accounts.ROLE_LABELS[role]
    if role not in accounts.CLINICAL_ROLES:
        raise _bad(f"a {label} account has no staff directory record; only clinical roles (care coordinator, GP, "
                   "specialist, clinical lead) have one", "practitioner_id")
    p = db.get(Practitioner, pid)
    if p is None or p.is_pool or not p.active:
        raise _bad("that staff directory record was not found (or is a team pool / inactive)", "practitioner_id")
    if p.role != role:
        raise _bad(f"that staff directory record is for a {accounts.ROLE_LABELS.get(p.role, p.role)}, not a {label}. "
                   "Pick a matching record or create a new one", "practitioner_id")
    other = db.scalar(select(User).where(User.practitioner_id == p.id))
    if other is not None and other.id != user_id:
        raise _bad(f"that staff directory record already has an account ({other.username})", "practitioner_id")
    return p


def _new_record(db: Session, role: str, name: str, rec: StaffRecordIn) -> Practitioner:
    """Create a staff directory record (FHIR Practitioner + PractitionerRole) for a new clinical account."""
    label = accounts.ROLE_LABELS[role]
    rule = STAFF_RULES.get(role)
    if rule is None:
        raise _bad(f"a {label} account has no staff directory record", "role")
    if rec.org_id is None:
        raise _bad(f"choose the {rule['org_noun']} where this {rule['noun']} works", "org_id")
    org = db.get(Organization, rec.org_id)
    if org is None or not org.active or org.kind not in rule["org_kinds"]:
        kinds = " or ".join(ORG_KIND_LABELS[k].lower() for k in rule["org_kinds"])
        raise _bad(f"a {label} must work at a {kinds}; choose one from the list", "org_id")
    spec = rec.specialty or None
    if rule["specialty"] == "required":
        if not spec:
            raise _bad("choose the specialty this specialist sees patients for", "specialty")
        if spec not in _specialties(db):
            raise _bad("unknown specialty; choose one from the list", "specialty")
    elif spec:
        raise _bad(f"a specialty is only recorded for specialist doctors, not a {label}", "specialty")
    prog = rec.programme or None
    if rule["programme"] is None and prog:
        raise _bad(f"a {label} is not tied to one screening programme; leave the programme empty", "programme")
    if rule["programme"] == "required" and not prog:
        raise _bad("choose the screening programme this care coordinator works on (cases are assigned by programme)", "programme")
    if prog and prog not in PROGRAMMES:
        raise _bad("unknown screening programme; choose one from the list", "programme")
    sup = None  # reports to: coordinators -> coordination supervisor; programme leads -> programme medical director
    if role == "coordinator":
        sup = db.scalar(select(Practitioner.id).where(Practitioner.role == "coordinator", Practitioner.specialty == "supervisor",
                                                      Practitioner.active.is_(True)).order_by(Practitioner.id).limit(1))
    elif role == "clinical_lead" and prog:
        sup = db.scalar(select(Practitioner.id).where(Practitioner.role == "clinical_lead", Practitioner.programme.is_(None),
                                                      Practitioner.is_pool.is_(False), Practitioner.active.is_(True))
                        .order_by(Practitioner.id).limit(1))
    p = Practitioner(name=name, role=role, specialty=spec, org_id=org.id, programme=prog, supervisor_id=sup,
                     is_pool=False, active=True)
    db.add(p)
    db.flush()
    return p


def _staff_record(db: Session, role: str, name: str, practitioner_id: int | None, rec: StaffRecordIn | None,
                  user_id: int | None = None) -> Practitioner | None:
    """Return the staff directory record the account should be linked to (existing, new, or none)."""
    if practitioner_id is not None and rec is not None:
        raise _bad("either pick an existing staff directory record or create a new one, not both", "practitioner_id")
    if practitioner_id is not None:
        return _link_existing(db, role, practitioner_id, user_id)
    if rec is not None:
        if role not in accounts.CLINICAL_ROLES:
            raise _bad(f"a {accounts.ROLE_LABELS[role]} account has no staff directory record; only clinical roles "
                       "(care coordinator, GP, specialist, clinical lead) have one", "practitioner_id")
        return _new_record(db, role, name, rec)
    if role in accounts.CLINICAL_ROLES:
        raise _bad(f"a {accounts.ROLE_LABELS[role]} needs a staff directory record (so cases can be assigned to them): "
                   "pick their existing record or choose “Create a new staff record” and fill in where they work", "practitioner_id")
    return None


def _audit_new_record(db: Session, actor: Actor, p: Practitioner, user: User) -> None:
    audit.record(db, actor, "C", "practitioner.create", entity_type="Practitioner", entity_id=p.id,
                 detail={"role": p.role, "org_id": p.org_id, "specialty": p.specialty, "programme": p.programme,
                         "for_user": user.username})


@router.get("")
def list_users(actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    users = db.scalars(select(User).order_by(User.active.desc(), User.role, User.display_name)).all()
    linked = {u.practitioner_id for u in users if u.practitioner_id}
    free = db.scalars(select(Practitioner).where(Practitioner.is_pool.is_(False), Practitioner.active.is_(True))
                      .order_by(Practitioner.name)).all()
    orgs = db.scalars(select(Organization).where(Organization.active.is_(True)).order_by(Organization.kind, Organization.name)).all()
    org_name = {o.id: o.name for o in orgs}
    return {"items": [accounts.public_user(u) for u in users],
            "roles": [{"key": r, "label": accounts.ROLE_LABELS[r], "clinical": r in accounts.CLINICAL_ROLES}
                      for r in accounts.ASSIGNABLE_ROLES],
            "staff_rules": STAFF_RULES,
            "organizations": [{"id": o.id, "name": o.name, "kind": o.kind, "kind_label": ORG_KIND_LABELS.get(o.kind, o.kind),
                               "region": o.region} for o in orgs],
            "specialties": [{"key": k, "label": v} for k, v in _specialties(db).items()],
            "programmes": [{"key": k, "label": p.name} for k, p in PROGRAMMES.items()],
            "practitioners_by_id": {p.id: {"name": p.name, "role": p.role, "org": org_name.get(p.org_id)}
                                    for p in free if p.id in linked},
            "unlinked_practitioners": [{"id": p.id, "name": p.name, "role": p.role, "org_id": p.org_id,
                                        "org": org_name.get(p.org_id), "specialty": p.specialty, "programme": p.programme}
                                       for p in free if p.id not in linked]}


@router.post("")
def create_user(body: UserCreateIn, actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    _check_role(body.role)
    if db.scalar(select(User).where(User.username == body.username)):
        raise _bad("that username is already taken", "username")
    temp = body.temporary_password or accounts.generate_password(16)
    probs = accounts.password_problems(temp, body.username, body.display_name)
    if probs:
        raise Invalid(" ".join(probs))
    is_new = body.staff_record is not None and body.practitioner_id is None
    p = _staff_record(db, body.role, body.display_name, body.practitioner_id, body.staff_record)
    site = body.site or (p.org.name if p is not None and p.org else None)
    u = User(username=body.username, display_name=body.display_name, role=body.role, practitioner_id=p.id if p else None,
             site=site, team=body.team, active=True)
    accounts.set_password(u, temp, must_change=True)
    db.add(u)
    db.flush()
    if p is not None and is_new:
        _audit_new_record(db, actor, p, u)
    audit.record(db, actor, "C", "user.create", entity_type="User", entity_id=u.id,
                 detail={"username": u.username, "role": u.role, "site": u.site, "team": u.team,
                         "practitioner_id": u.practitioner_id, "new_staff_record": bool(p is not None and is_new)})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u),
            "temporary_password": temp if not body.temporary_password else None}


@router.post("/{uid}/role")
def change_role(uid: int, body: UserRoleIn, actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    if u.id == actor.user_id and body.role != u.role:
        raise Forbidden("you cannot change your own role")
    if u.role == "patient":
        raise Invalid("patient accounts keep the patient role")
    _check_role(body.role)
    before = {"role": u.role, "site": u.site, "team": u.team, "practitioner_id": u.practitioner_id}
    cur = u.practitioner
    new_rec = None
    if body.practitioner_id is not None or body.staff_record is not None:
        p = _staff_record(db, body.role, u.display_name, body.practitioner_id, body.staff_record, user_id=u.id)
        new_rec = p if body.staff_record is not None else None
        u.practitioner_id = p.id if p else None
        u.practitioner = p
    elif body.role in accounts.CLINICAL_ROLES and (cur is None or cur.role != body.role):
        _staff_record(db, body.role, u.display_name, None, None)  # raises the clear "needs a staff record" message
    u.role, u.site, u.team = body.role, body.site if body.site is not None else u.site, body.team if body.team is not None else u.team
    if before["role"] != u.role or before["practitioner_id"] != u.practitioner_id:
        accounts.revoke_user_sessions(db, u.id)  # new permissions take effect at next sign-in
    if new_rec is not None:
        _audit_new_record(db, actor, new_rec, u)
    audit.record(db, actor, "U", "user.role_change", entity_type="User", entity_id=u.id,
                 detail={"from": before, "to": {"role": u.role, "site": u.site, "team": u.team, "practitioner_id": u.practitioner_id}})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u)}


@router.post("/{uid}/reset-password")
def reset_password(uid: int, body: UserResetIn | None = None,
                   actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    """Staff accounts only: a one-time temporary password shown once to the administrator, a change forced at next
    sign-in, every session ended, earlier failed sign-ins cleared. Patient accounts are reset by a care coordinator
    after an identity check (one-time reset code from the patient record)."""
    from ..services import signin_help

    u = _get(db, uid)
    if u.role == "patient":
        raise Forbidden("patient passwords are reset by a care coordinator from the patient record (identity check "
                        "and a one-time reset code)")
    given = body.temporary_password if body else None
    help_id = body.help_request_id if body else None
    temp = given or accounts.generate_password(16)
    probs = accounts.password_problems(temp, u.username, u.display_name)
    if probs:
        raise Invalid(" ".join(probs))
    accounts.set_password(u, temp, must_change=True)
    accounts.revoke_user_sessions(db, u.id)
    audit.record(db, actor, "U", "user.password_reset", entity_type="User", entity_id=u.id,
                 detail={"forced_change": True, "sessions_ended": True, "help_request_id": help_id})
    if help_id:
        signin_help.close_request(db, actor, help_id, "done", f"Password reset for {u.username} (temporary password, "
                                                             "change forced at next sign-in)")
    db.commit()
    return {"ok": True, "user": accounts.public_user(u), "temporary_password": None if given else temp}


@router.post("/{uid}/deactivate")
def deactivate(uid: int, body: UserNoteIn | None = None, actor: Actor = Depends(require("user:admin")),
               db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    if u.id == actor.user_id:
        raise Forbidden("you cannot deactivate your own account")
    u.active = False
    reason = (body.reason or "").strip() or None if body else None
    u.status_reason, u.status_changed_at, u.status_changed_by = reason, utcnow(), actor.display
    accounts.revoke_user_sessions(db, u.id)
    audit.record(db, actor, "U", "user.deactivate", entity_type="User", entity_id=u.id, detail={"reason": reason})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u)}


@router.post("/{uid}/activate")
def activate(uid: int, body: UserNoteIn | None = None, actor: Actor = Depends(require("user:admin")),
             db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    u.active = True
    reason = (body.reason or "").strip() or None if body else None
    u.status_reason, u.status_changed_at, u.status_changed_by = reason, utcnow(), actor.display
    audit.record(db, actor, "U", "user.activate", entity_type="User", entity_id=u.id, detail={"reason": reason})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u)}


system_router = APIRouter(prefix="/api/admin/system", tags=["admin"])


@system_router.get("")
def system_status(actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    """Admin > System: operating mode and where the data lives (no secrets: the database password is never shown)."""
    from sqlalchemy import func
    from sqlalchemy.engine import make_url

    from .. import __version__
    from ..config import get_settings, storage_is_persistent
    from ..models import Case, Patient

    s = get_settings()
    url = make_url(s.database_url)
    backend = "PostgreSQL" if url.get_backend_name() == "postgresql" else "SQLite"
    where = (url.host or "") if backend == "PostgreSQL" else "local file on this server"
    persistent = storage_is_persistent(s)
    return {"version": __version__, "mode": s.mode, "database": backend, "database_host": where,
            "storage_persistent": persistent,
            "storage_note": None if persistent else (
                "Data is stored on this server's temporary disk. It is lost when the service restarts or is "
                "redeployed. Connect a PostgreSQL database (DATABASE_URL) to keep data permanently."),
            "users_active": db.scalar(select(func.count(User.id)).where(User.active.is_(True))),
            "patients": db.scalar(select(func.count(Patient.id))), "cases": db.scalar(select(func.count(Case.id))),
            "write_limit_per_minute": s.write_limit_per_minute if s.mode == "production" else None,
            "session_idle_minutes": s.session_idle_minutes}
