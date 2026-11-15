"""Read-only Malaffi (Abu Dhabi HIE) adapter skeleton.

This is a skeleton for a future pilot. It does not connect to the live Malaffi network.
When KARESHAFAA_MALAFFI_MODE=mock (default), it returns synthetic data for demonstration.
When mode=live and credentials are set, it would call the real endpoint (not implemented).

Never writes clinical data. Match on Emirates ID only. Human review required before any action.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from ..config import get_settings


class MalaffiAdapter:
    """Read-only interface to Malaffi. Skeleton only."""

    def __init__(self, mode: str = "mock", base_url: str = "", client_id: str = ""):
        self.mode = mode
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.connected = mode == "live" and bool(base_url and client_id)

    def status(self) -> dict[str, Any]:
        return {
            "adapter": "malaffi",
            "mode": self.mode,
            "connected": self.connected,
            "read_only": True,
            "note": "Skeleton only. No live Malaffi connection. Mock mode returns synthetic data for demo.",
            "next_step": "Facility completes Accession Agreement; KareShafaa subscribes to result feeds matched on Emirates ID.",
        }

    def search_patient(self, emirates_id: str) -> dict[str, Any] | None:
        """Look up a patient by Emirates ID. Returns None if not found."""
        if not emirates_id or len(emirates_id) < 5:
            return None
        if self.mode == "mock":
            return {
                "emirates_id": emirates_id,
                "name": "Synthetic Patient",
                "dob": "1980-01-01",
                "gender": "unknown",
                "source": "malaffi-mock",
                "matched_on": "emirates_id",
            }
        return None  # live not implemented

    def get_screening_results(self, emirates_id: str, programme: str | None = None) -> list[dict[str, Any]]:
        """Return recent screening results for the patient. Read-only."""
        if self.mode != "mock":
            return []
        now = datetime.utcnow()
        return [
            {
                "programme": programme or "fit",
                "code": "2335-8",
                "display": "FIT result (synthetic)",
                "value": "positive",
                "issued_at": (now - timedelta(days=2)).isoformat(timespec="seconds"),
                "source": "malaffi-mock",
                "read_only": True,
            }
        ]

    def get_appointments(self, emirates_id: str) -> list[dict[str, Any]]:
        """Return upcoming appointments visible via the HIE. Read-only."""
        if self.mode != "mock":
            return []
        return [
            {
                "start": (datetime.utcnow() + timedelta(days=14)).isoformat(timespec="seconds"),
                "status": "booked",
                "service": "Colorectal screening follow-up (synthetic)",
                "source": "malaffi-mock",
                "read_only": True,
            }
        ]


def get_adapter() -> MalaffiAdapter:
    s = get_settings()
    mode = getattr(s, "malaffi_mode", "mock")
    url = getattr(s, "malaffi_base_url", "")
    cid = getattr(s, "malaffi_client_id", "")
    return MalaffiAdapter(mode=mode, base_url=url, client_id=cid)
