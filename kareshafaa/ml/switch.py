"""AI kill switch for the advisory outreach-priority model.

Two levels, both must be on for the model to score:
  * deployment: setting ``ml_enabled`` (env ``KARESHAFAA_ML_ENABLED``)
  * runtime: config key ``ml:advisory_enabled``, switched by an admin on Admin > System (permission ``ml:switch``),
    with a written reason, audited.

Switching off clears every open case's score at once, so the work queue falls back to deadline order. The model is
advisory only in every state: it never moves, closes or assigns a case (see workflow.transition and
tests/test_properties.py).
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy.orm import Session

from .. import audit
from ..config import get_settings
from ..models import Case, utcnow
from ..security import Actor
from ..services.configstore import get_config, set_config
from ..services.errors import Forbidden, Invalid

KEY = "ml:advisory_enabled"
PERMISSION = "ml:switch"


def runtime_enabled(db: Session) -> bool:
    return get_config(db, KEY, True) is not False


def enabled(db: Session) -> bool:
    return bool(get_settings().ml_enabled) and runtime_enabled(db)


def status(db: Session) -> dict:
    dep = bool(get_settings().ml_enabled)
    run = runtime_enabled(db)
    return {"enabled": dep and run, "deployment_enabled": dep, "runtime_enabled": run}


def clear_scores(db: Session) -> int:
    return db.query(Case).filter(Case.state != "closed").update(
        {Case.risk_score: None, Case.risk_model_version: None, Case.risk_rationale: None}, synchronize_session=False)


def set_enabled(db: Session, actor: Actor, on: bool, reason: str, now: datetime | None = None) -> dict:
    now = now or utcnow()
    if not actor.is_human or actor.user_id is None or not actor.can(PERMISSION):
        raise Forbidden("only an administrator may switch the AI suggestions on or off")
    if not reason or len(reason.strip()) < 10:
        raise Invalid("a reason of at least 10 characters is required")
    set_config(db, KEY, bool(on), source=f"user:{actor.user_id}")
    cleared = 0
    if not on:
        cleared = clear_scores(db)
    audit.record(db, actor, "U", "ml.switched_on" if on else "ml.switched_off", entity_type="Config", entity_id=KEY,
                 ts=now, detail={"reason": reason.strip()[:300], "scores_cleared": cleared})
    db.flush()
    if on:
        from .registry import score_open_cases
        score_open_cases(db, now=now)
    return status(db)
