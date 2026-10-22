"""Authentication: username + password login with server-side sessions, logout, own password change.

The HTML forms (/login, /logout, /account/password in main.py) and the JSON endpoints here share the same
functions, so both paths get the same rate limiting, lockout, CSRF checks and audit events."""
from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import accounts, audit
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import PERMISSIONS, Actor, make_token
from .deps import COOKIE, CSRF_COOKIE, actor_from_user, current_actor, token_from_request
from .schemas import LoginIn, PasswordChangeIn, PasswordLoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])
ANON = Actor(user_id=None, role="anonymous", display="anonymous", is_human=True)
FEATURED = ["aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori", "layla.al.hashimi", "analyst", "admin",
            "translator", "patient.ar", "patient.en", "patient.hi", "patient.ur", "patient.tl"]


def client_ip(request: Request) -> str | None:
    return request.client.host if request.client else None


def cookie_secure(request: Request) -> bool:
    s = get_settings()
    if s.cookie_secure is not None:
        return s.cookie_secure
    return request.url.scheme == "https" or s.env == "prod" or s.public_demo


def set_session_cookie(response: Response, request: Request, token: str) -> None:
    s = get_settings()
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=cookie_secure(request),
                        max_age=s.token_ttl_minutes * 60, path="/")


def clear_session_cookie(response: Response, request: Request) -> None:
    response.delete_cookie(COOKIE, path="/", httponly=True, samesite="lax", secure=cookie_secure(request))


def login_csrf_token(request: Request) -> str:
    tok = request.cookies.get(CSRF_COOKIE)
    return tok if tok and 20 <= len(tok) <= 64 else secrets.token_urlsafe(24)


def ensure_login_csrf(request: Request, response: Response, tok: str | None = None) -> str:
    """Pre-login double-submit CSRF token (cookie + hidden field / header)."""
    tok = tok or login_csrf_token(request)
    response.set_cookie(CSRF_COOKIE, tok, httponly=True, samesite="strict", secure=cookie_secure(request), path="/",
                        max_age=2 * 3600)
    return tok


def login_csrf_ok(request: Request, presented: str | None) -> bool:
    import hmac

    c = request.cookies.get(CSRF_COOKIE)
    return bool(c and presented) and hmac.compare_digest(c, presented)  # type: ignore[arg-type]


def do_login(db: Session, request: Request, username: str, password: str) -> accounts.LoginResult:
    res = accounts.authenticate(db, username, password, client_ip(request))
    if res.ok:
        u = res.user
        audit.record(db, actor_from_user(u), "E", "auth.login", entity_type="User", entity_id=u.id,  # type: ignore[union-attr]
                     detail={"method": "password", "ip": client_ip(request)})
    else:
        audit.record(db, ANON, "E", "auth.login_failed", entity_type="User", outcome="failure",
                     detail={"username": (username or "").strip().lower()[:80], "reason": res.reason,
                             "ip": client_ip(request)})
    db.commit()
    return res


def start_session(db: Session, request: Request, response: Response, user: User) -> str:
    token = accounts.create_session(db, user, client_ip(request), request.headers.get("user-agent"))
    db.commit()
    set_session_cookie(response, request, token)
    return token


def do_logout(db: Session, request: Request, response: Response) -> None:
    tok = token_from_request(request)
    if tok:
        found = accounts.lookup_session(db, tok) if tok.startswith(accounts.SESSION_PREFIX) else None
        accounts.revoke_session(db, tok)
        if found:
            audit.record(db, actor_from_user(found[1]), "E", "auth.logout", entity_type="User", entity_id=found[1].id)
        db.commit()
    clear_session_cookie(response, request)


def do_password_change(db: Session, request: Request, actor: Actor, current: str, new: str) -> list[str]:
    """Returns problems (empty = changed). Other sessions of the user are signed out."""
    s = get_settings()
    u = db.get(User, actor.user_id)
    if u is None:
        return ["Account not found."]
    if s.public_demo and u.is_demo_account:
        return ["Shared demo accounts cannot change their password on the public demo."]
    if not accounts.verify_password(u.password_hash, current):
        audit.record(db, actor, "U", "user.password_change", entity_type="User", entity_id=u.id, outcome="failure",
                     detail={"reason": "current_password_wrong"})
        db.commit()
        return ["Your current password is not correct."]
    probs = accounts.password_problems(new, u.username, u.display_name)
    if not probs and accounts.verify_password(u.password_hash, new):
        probs = ["Choose a password different from your current one."]
    if probs:
        return probs
    accounts.set_password(u, new, must_change=False)
    accounts.revoke_user_sessions(db, u.id, except_token=token_from_request(request))
    audit.record(db, actor, "U", "user.password_change", entity_type="User", entity_id=u.id)
    db.commit()
    return []


def home_for(role: str) -> str:
    from ..ui import home_for as _h

    return _h(role)


# --------------------------------------------------------------------------------------------- JSON API
@router.post("/login")
def login(body: PasswordLoginIn, request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    if not login_csrf_ok(request, request.headers.get("x-csrf-token")):
        raise HTTPException(403, "security check failed - reload the sign-in page and try again")
    res = do_login(db, request, body.username, body.password)
    if not res.ok:
        if res.reason in ("locked", "rate_limited"):
            raise HTTPException(429, accounts.LOCKED_ERROR)
        raise HTTPException(401, accounts.GENERIC_LOGIN_ERROR)
    u = res.user
    token = start_session(db, request, response, u)  # type: ignore[arg-type]
    nxt = "/account/password" if u.must_change_password else home_for(u.role)  # type: ignore[union-attr]
    return {"ok": True, "next": nxt, "csrf": accounts.csrf_for(token), "user": accounts.public_user(u)}  # type: ignore[arg-type]


@router.post("/logout")
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> dict:
    do_logout(db, request, response)
    return {"ok": True}


@router.post("/password")
def change_password(body: PasswordChangeIn, request: Request, actor: Actor = Depends(current_actor),
                    db: Session = Depends(get_db)) -> dict:
    probs = do_password_change(db, request, actor, body.current_password, body.new_password)
    if probs:
        raise HTTPException(422, " ".join(probs))
    return {"ok": True}


@router.get("/me")
def me(request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)) -> dict:
    u = db.get(User, actor.user_id)
    tok = token_from_request(request) or ""
    return {"user_id": actor.user_id, "display_name": actor.display, "role": actor.role,
            "role_label": accounts.ROLE_LABELS.get(actor.role, actor.role),
            "username": u.username if u else None, "site": u.site if u else None, "team": u.team if u else None,
            "must_change_password": bool(u and u.must_change_password),
            "practitioner_id": actor.practitioner_id, "patient_id": actor.patient_id, "org_id": actor.org_id,
            "permissions": sorted(PERMISSIONS.get(actor.role, [])),
            "csrf": accounts.csrf_for(tok) if request.cookies.get(COOKIE) == tok and tok else None}


# --------------------------------------------------------------------------------------------- dev/test only
def _one_click_enabled() -> bool:
    s = get_settings()
    return s.demo_logins and not s.public_demo and s.env != "prod"


@router.get("/demo-users", include_in_schema=False)
def demo_users(db: Session = Depends(get_db)) -> dict:
    if not _one_click_enabled():
        raise HTTPException(404, "not found")
    users = db.scalars(select(User).where(User.active.is_(True)).order_by(User.role, User.id)).all()
    featured = {u: i for i, u in enumerate(FEATURED)}
    rows = [{"username": u.username, "display_name": u.display_name, "role": u.role, "featured": u.username in featured}
            for u in users]
    rows.sort(key=lambda r: (featured.get(r["username"], 999), r["role"], r["display_name"]))
    return {"users": rows}


@router.post("/demo-login", include_in_schema=False)
def demo_login(body: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)) -> dict:
    """Passwordless token for automated tests and local scripts only. Refused (404) in public demo mode and prod;
    there is no button for it anywhere in the UI."""
    if not _one_click_enabled():
        raise HTTPException(404, "not found")
    u = db.scalar(select(User).where(User.username == body.username, User.active.is_(True)))
    if u is None:
        raise HTTPException(401, "unknown user")
    token = make_token(u.id)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=cookie_secure(request),
                        max_age=get_settings().token_ttl_minutes * 60)
    audit.record(db, actor_from_user(u), "E", "auth.login", entity_type="User", entity_id=u.id, detail={"method": "test-token"})
    db.commit()
    return {"ok": True, "token": token, "user": {"username": u.username, "display_name": u.display_name, "role": u.role}}
