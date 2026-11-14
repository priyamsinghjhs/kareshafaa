"""FastAPI dependencies: DB session, authenticated actor, permission checks."""
from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..accounts import SESSION_PREFIX, lookup_session
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import Actor, verify_token

COOKIE = "kareshafaa_session"
CSRF_COOKIE = "kareshafaa_csrf"  # pre-login double-submit token for the login form
# reachable while a password change is pending (everything else answers 403 until the password is changed)
PASSWORD_CHANGE_ALLOWED = ("/api/auth/me", "/api/auth/password", "/api/auth/logout")


def token_from_request(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.cookies.get(COOKIE)


def actor_from_user(u: User) -> Actor:
    return Actor(user_id=u.id, role=u.role, display=u.display_name, practitioner_id=u.practitioner_id,
                 patient_id=u.patient_id, org_id=u.practitioner.org_id if u.practitioner else None,
                 extra={"must_change_password": bool(getattr(u, "must_change_password", False)),
                        "username": u.username})


def resolve_user(request: Request, db: Session) -> User | None:
    tok = token_from_request(request)
    if not tok:
        return None
    if tok.startswith(SESSION_PREFIX):  # real login: server-side session (idle + absolute timeout, revocable)
        found = lookup_session(db, tok)
        return found[1] if found else None
    # legacy signed token: only from the dev/test token endpoint (never enabled in public demo or prod)
    s = get_settings()
    if not s.demo_logins or s.public_demo or s.env == "prod":
        return None
    uid = verify_token(tok)
    if uid is None:
        return None
    u = db.get(User, uid)
    if u is None or not u.active:
        return None
    return u


def resolve_actor(request: Request, db: Session) -> Actor | None:
    u = resolve_user(request, db)
    return actor_from_user(u) if u is not None else None


def current_actor(request: Request, db: Session = Depends(get_db)) -> Actor:
    a = resolve_actor(request, db)
    if a is None:
        raise HTTPException(status_code=401, detail="not signed in - please sign in again")
    request.state.actor = a
    if a.extra.get("must_change_password") and request.url.path not in PASSWORD_CHANGE_ALLOWED:
        raise HTTPException(status_code=403, detail="please change your temporary password first")
    return a


def require(perm: str) -> Callable[..., Actor]:
    def dep(actor: Actor = Depends(current_actor)) -> Actor:
        if not actor.can(perm):
            raise HTTPException(status_code=403, detail=f"role '{actor.role}' lacks permission '{perm}'")
        return actor

    return dep
