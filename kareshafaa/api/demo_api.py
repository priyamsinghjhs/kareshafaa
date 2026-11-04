"""One-click demo scenarios (disabled outside demo mode)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..db import get_db
from ..demo import SCENARIOS, run_scenario
from ..security import Actor
from .deps import require

router = APIRouter(prefix="/api/demo", tags=["demo"])


@router.get("/scenarios")
def scenarios(actor: Actor = Depends(require("user:admin"))) -> dict:
    return {"items": [{"key": k, **v} for k, v in SCENARIOS.items()]}


@router.post("/scenarios/{key}")
def start(key: str, actor: Actor = Depends(require("user:admin")), db: Session = Depends(get_db)) -> dict:
    out = run_scenario(db, key, actor)
    db.commit()
    return out
