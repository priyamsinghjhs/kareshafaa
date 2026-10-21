"""Runtime configuration. Every setting can be overridden with a WASL_* environment variable."""
from __future__ import annotations

import os
import secrets
from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WASL_", env_file=".env", extra="ignore")

    env: str = "dev"  # dev | test | prod
    database_url: str = f"sqlite:///{BASE_DIR / 'data' / 'wasl.db'}"
    data_dir: Path = BASE_DIR / "data"
    model_dir: Path = BASE_DIR / "data" / "models"
    # Secret for signing session tokens. Never hard-coded: read from env, else a random
    # key is generated once and stored in data_dir/.secret_key (0600) for local dev.
    secret_key: str | None = None
    token_ttl_minutes: int = 8 * 60
    demo_logins: bool = True  # role-switcher demo logins; MUST be false outside demos
    sla_config_path: Path | None = None  # optional JSON overriding default SLA hours

    # analytics / privacy
    suppression_threshold: int = 5  # small-cell suppression: n < threshold is hidden

    # ML governance gates
    min_auc_floor: float = 0.60
    promotion_min_auc_delta: float = 0.0  # candidate AUC must exceed current by more than this
    max_ece: float = 0.10
    calibration_slope_range: tuple[float, float] = (0.7, 1.3)  # held-out calibration slope gate
    max_citl: float = 0.05  # |mean predicted - observed| on the held-out set
    # 'none' (default): log-loss-fitted models are used as-is; 'auto': Platt recalibration chosen on a temporal tuning
    # slice of the training data. At demo/pilot volumes (<5k labelled cases) 'auto' over-corrected on the hold-out.
    ml_recalibration: str = "none"
    # Re-opened case (CLOSED -> active): False (default, the safer choice) keeps the ORIGINAL pathway target, so a
    # case re-opened after its target is flagged breached at once and escalates (more alerts, not fewer). True gives
    # the re-opened episode a fresh target for its tier. CLINICAL LEAD TO CONFIRM (round 19/20 open question).
    reopen_restarts_target: bool = False
    # --- load / graceful degradation (round 21) -----------------------------------------------------------
    db_pool_size: int = 20  # per worker process (Postgres-ready; SQLite uses the same pool)
    db_max_overflow: int = 20
    db_pool_timeout_s: float = 3.0  # wait for a pooled connection, then 503 + Retry-After (was SQLAlchemy's 30 s)
    db_busy_timeout_s: float = 5.0  # SQLite busy_timeout / Postgres lock_timeout for one statement
    writer_lock_timeout_s: float = 5.0  # wait for the audit-chain writer lock, then 503 (nothing is saved)
    max_inflight_requests: int = 16  # per worker; beyond this a request is shed at once with 503 + Retry-After
    retry_after_s: int = 2
    # per-request time budget (round 23, wasl/request_timeout.py); 0 disables. Long admin jobs are exempt.
    request_timeout_s: float = 30.0
    request_timeout_exempt: tuple[str, ...] = ("/api/audit/verify", "/api/governance/models/train", "/api/sla/sweep",
                                               "/api/demo/")
    queue_cache_ttl_s: float = 2.0  # work-queue base data, per process; 0 disables (see services/queue.py)
    gil_switch_interval_ms: float = 0.5  # see main.lifespan (sqlite row-fetch convoy)
    fairness_auc_tolerance: float = 0.05  # group AUC CI must not lie wholly below overall - tol
    fairness_tpr_tolerance: float = 0.15  # group recall-at-top CI must not lie wholly below overall - tol
    fairness_cal_tolerance: float = 0.10  # group calibration-in-the-large CI must not lie wholly outside +/- tol
    fairness_min_group_n: int = 50
    fairness_min_group_pos: int = 10
    priority_top_fraction: float = 0.30  # share of cases flagged "high outreach priority"
    drift_psi_warn: float = 0.10
    drift_psi_alert: float = 0.25

    # synthetic data
    seed: int = 20261120
    n_patients: int = 2000

    # escalation ladder (fractions of the step SLA)
    reminder_fraction: float = 0.5
    clinical_lead_after_breach_fraction: float = 0.5  # L3 fires at (1 + this) x SLA
    director_after_fraction: float = 2.0  # L4 (T0/T1 only) fires at this multiple of the SLA
    phone_attempts_before_unreachable: int = 2
    sweep_interval_seconds: int = 300  # background SLA/escalation job; 0 disables (tests, multi-replica: run one)
    # automated patient messages (SLA reminders) only inside this UAE-local window; deferred otherwise
    patient_message_window: tuple[int, int] = (8, 21)

    cors_origins: list[str] = Field(default_factory=list)


def _resolve_secret(s: Settings) -> str:
    if s.secret_key:
        return s.secret_key
    if s.env == "prod":
        raise RuntimeError("WASL_SECRET_KEY must be set in prod")
    s.data_dir.mkdir(parents=True, exist_ok=True)
    p = s.data_dir / ".secret_key"
    if p.exists():
        return p.read_text().strip()
    key = secrets.token_urlsafe(48)
    p.write_text(key)
    os.chmod(p, 0o600)
    return key


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    s.model_dir.mkdir(parents=True, exist_ok=True)
    if s.env == "prod" and s.demo_logins:
        raise RuntimeError("WASL_DEMO_LOGINS must be false in prod")
    s.secret_key = _resolve_secret(s)
    return s
