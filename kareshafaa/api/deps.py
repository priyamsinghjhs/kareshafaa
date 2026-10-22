"""FastAPI dependencies: DB session, authenticated actor, permission checks."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..db import get_db
from ..models import User
from ..security import Actor, verify_token

COOKIE = "kareshafaa_session"


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE)


def actor_from_user(u: User) -> Actor:
    return Actor(user_id=u.id, role=u.role, display=u.display_name, practitioner_id=u.practitioner_id,
                 patient_id=u.patient_id, org_id=u.practitioner.org_id if u.practitioner else None)


def resolve_actor(request: Request, db: Session) -> Actor | None:
    tok = token_from_request(request)
    if not tok:
        return None
    uid = verify_token(tok)
    if uid is None:
        return None
    u = db.get(User, uid)
    if u is None or not u.active:
        return None
    return actor_from_user(u)


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    a = resolve_actor(request, db)
    if a is None:
        raise HTTPException(status_code=401, detail="not authenticated - use a demo login")
    request.state.actor = a
    return a


def require(perm: str) -> Callable[..., Actor]:
    def dep(actor: Actor = Depends(current_actor)) -> Actor:
        if not actor.can(perm):
            raise HTTPException(status_code=403, detail=f"role '{actor.role}' lacks permission '{perm}'")
        return actor

    return dep
