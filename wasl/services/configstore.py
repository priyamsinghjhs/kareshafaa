"""Versioned configuration entries (channel ladder, template variants...)."""
from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ConfigEntry, utcnow

DEFAULT_CHANNEL_ORDER = ["app", "sms", "whatsapp", "phone"]


def get_config(db: Session, key: str, default: Any = None) -> Any:
    row = db.scalar(select(ConfigEntry).where(ConfigEntry.key == key, ConfigEntry.active.is_(True))
                    .order_by(ConfigEntry.version.desc()).limit(1))
    return default if row is None else row.value


def set_config(db: Session, key: str, value: Any, source: str) -> ConfigEntry:
    prev = db.scalars(select(ConfigEntry).where(ConfigEntry.key == key, ConfigEntry.active.is_(True))).all()
    version = 1
    for p in prev:
        p.active = False
        version = max(version, p.version + 1)
    row = ConfigEntry(key=key, value=value, version=version, active=True, source=source, updated_at=utcnow())
    db.add(row)
    db.flush()
    return row


def channel_order(db: Session, language: str) -> list[str]:
    return list(get_config(db, f"channel_order:{language}", None) or get_config(db, "channel_order:default",
                                                                                 DEFAULT_CHANNEL_ORDER))


def template_variant(db: Session, category: str, language: str) -> str:
    return str(get_config(db, f"template_variant:{category}:{language}", "standard"))


def all_active(db: Session) -> list[ConfigEntry]:
    return list(db.scalars(select(ConfigEntry).where(ConfigEntry.active.is_(True)).order_by(ConfigEntry.key)))
