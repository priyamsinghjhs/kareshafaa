from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..db import get_db
from ..models import User
from ..security import PERMISSIONS, Actor, make_token
from .deps import COOKIE, actor_from_user, current_actor
from .schemas import LoginIn

router = APIRouter(prefix="/api/auth", tags=["auth"])
FEATURED = ["aisha.rahman", "omar.haddad", "vikram.mehta", "fatima.al.mansoori", "layla.al.hashimi", "analyst", "admin",
            "patient.ar", "patient.en", "patient.hi", "patient.ur", "patient.tl"]


@router.get("/demo-users")
def demo_users(db: Session = Depends(get_db)) -> dict:
    if not get_settings().demo_logins:
        raise HTTPException(404, "demo logins disabled")
    users = db.scalars(select(User).where(User.active.is_(True)).order_by(User.role, User.id)).all()
    featured = {u: i for i, u in enumerate(FEATURED)}
    rows = [{"username": u.username, "display_name": u.display_name, "role": u.role, "featured": u.username in featured}
            for u in users]
    rows.sort(key=lambda r: (featured.get(r["username"], 999), r["role"], r["display_name"]))
    return {"users": rows}


@router.post("/demo-login")
def demo_login(body: LoginIn, response: Response, request: Request, db: Session = Depends(get_db)) -> dict:
    s = get_settings()
    if not s.demo_logins:
        raise HTTPException(404, "demo logins disabled")
    u = db.scalar(select(User).where(User.username == body.username, User.active.is_(True)))
    if u is None:
        raise HTTPException(401, "unknown demo user")
    token = make_token(u.id)
    response.set_cookie(COOKIE, token, httponly=True, samesite="lax", secure=s.env == "prod",
                        max_age=s.token_ttl_minutes * 60)
    a = actor_from_user(u)
    audit.record(db, a, "E", "auth.login", entity_type="User", entity_id=u.id, detail={"method": "demo"})
    db.commit()
    return {"ok": True, "token": token, "user": {"username": u.username, "display_name": u.display_name, "role": u.role}}


@router.post("/logout")
def logout(response: Response) -> dict:
    response.delete_cookie(COOKIE)
    return {"ok": True}


@router.get("/me")
def me(actor: Actor = Depends(current_actor)) -> dict:
    return {"user_id": actor.user_id, "display_name": actor.display, "role": actor.role,
            "practitioner_id": actor.practitioner_id, "patient_id": actor.patient_id, "org_id": actor.org_id,
            "permissions": sorted(PERMISSIONS.get(actor.role, []))}
