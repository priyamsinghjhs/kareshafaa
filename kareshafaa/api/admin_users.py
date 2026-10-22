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
from ..models import Practitioner, User
from ..security import Actor
from ..services.errors import Forbidden, Invalid, NotFound
from .deps import require
from .schemas import UserCreateIn, UserNoteIn, UserResetIn, UserRoleIn

router = APIRouter(prefix="/api/admin/users", tags=["admin"])


def _get(db: Session, uid: int) -> User:
    u = db.get(User, uid)
    if u is None:
        raise NotFound("user not found")
    return u


def _check_role(db: Session, role: str, practitioner_id: int | None) -> None:
    if role not in accounts.ASSIGNABLE_ROLES:
        raise Invalid("unknown or non-assignable role (patient accounts are created from the patient record)")
    if role in accounts.CLINICAL_ROLES and practitioner_id is None:
        raise Invalid("clinical roles must be linked to a staff directory record (practitioner)")


@router.get("")
def list_users(actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    users = db.scalars(select(User).order_by(User.active.desc(), User.role, User.display_name)).all()
    linked = {u.practitioner_id for u in users if u.practitioner_id}
    free = db.scalars(select(Practitioner).where(Practitioner.is_pool.is_(False), Practitioner.active.is_(True))).all()
    return {"items": [accounts.public_user(u) for u in users],
            "roles": [{"key": r, "label": accounts.ROLE_LABELS[r]} for r in accounts.ASSIGNABLE_ROLES],
            "unlinked_practitioners": [{"id": p.id, "name": p.name, "role": p.role} for p in free if p.id not in linked]}


@router.post("")
def create_user(body: UserCreateIn, actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    _check_role(db, body.role, body.practitioner_id)
    if db.scalar(select(User).where(User.username == body.username)):
        raise Invalid("that username is already taken")
    if body.practitioner_id is not None:
        p = db.get(Practitioner, body.practitioner_id)
        if p is None:
            raise Invalid("staff directory record not found")
        if db.scalar(select(User).where(User.practitioner_id == p.id)):
            raise Invalid("that staff record already has an account")
    temp = body.temporary_password or accounts.generate_password(16)
    probs = accounts.password_problems(temp, body.username, body.display_name)
    if probs:
        raise Invalid(" ".join(probs))
    site = body.site
    if not site and body.practitioner_id:
        p = db.get(Practitioner, body.practitioner_id)
        site = p.org.name if p and p.org else None
    u = User(username=body.username, display_name=body.display_name, role=body.role, practitioner_id=body.practitioner_id,
             site=site, team=body.team, active=True)
    accounts.set_password(u, temp, must_change=True)
    db.add(u)
    db.flush()
    audit.record(db, actor, "C", "user.create", entity_type="User", entity_id=u.id,
                 detail={"username": u.username, "role": u.role, "site": u.site, "team": u.team})
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
    _check_role(db, body.role, u.practitioner_id)
    before = {"role": u.role, "site": u.site, "team": u.team}
    u.role, u.site, u.team = body.role, body.site if body.site is not None else u.site, body.team if body.team is not None else u.team
    if before["role"] != u.role:
        accounts.revoke_user_sessions(db, u.id)  # new permissions take effect at next sign-in
    audit.record(db, actor, "U", "user.role_change", entity_type="User", entity_id=u.id,
                 detail={"from": before, "to": {"role": u.role, "site": u.site, "team": u.team}})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u)}


@router.post("/{uid}/reset-password")
def reset_password(uid: int, body: UserResetIn | None = None,
                   actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    given = body.temporary_password if body else None
    temp = given or accounts.generate_password(16)
    probs = accounts.password_problems(temp, u.username, u.display_name)
    if probs:
        raise Invalid(" ".join(probs))
    accounts.set_password(u, temp, must_change=True)
    accounts.revoke_user_sessions(db, u.id)
    audit.record(db, actor, "U", "user.password_reset", entity_type="User", entity_id=u.id,
                 detail={"forced_change": True})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u), "temporary_password": None if given else temp}


@router.post("/{uid}/deactivate")
def deactivate(uid: int, body: UserNoteIn | None = None, actor: Actor = Depends(require("user:admin")),
               db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    if u.id == actor.user_id:
        raise Forbidden("you cannot deactivate your own account")
    u.active = False
    accounts.revoke_user_sessions(db, u.id)
    audit.record(db, actor, "U", "user.deactivate", entity_type="User", entity_id=u.id,
                 detail={"reason": body.reason if body else None})
    db.commit()
    return {"ok": True, "user": accounts.public_user(u)}


@router.post("/{uid}/activate")
def activate(uid: int, body: UserNoteIn | None = None, actor: Actor = Depends(require("user:admin")),
             db: Session = Depends(get_db)) -> dict:
    u = _get(db, uid)
    u.active = True
    audit.record(db, actor, "U", "user.activate", entity_type="User", entity_id=u.id,
                 detail={"reason": body.reason if body else None})
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
