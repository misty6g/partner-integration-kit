"""Runtime configuration loaded from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./pik.db"
    seed_demo: bool = True
    rate_limit: int = 120
    rate_window_seconds: int = 60
    webhook_max_attempts: int = 5
    webhook_timeout_seconds: float = 5.0
    webhook_backoff_base_seconds: float = 1.0
    webhook_backoff_cap_seconds: float = 32.0
    webhook_tolerance_seconds: int = 300
    worker_poll_seconds: float = 0.5
    stuck_delivery_seconds: int = 60
    redis_url: str | None = None
    docs_base: str = "https://github.com/misty6g/partner-integration-kit/blob/main/"
    log_level: str = "INFO"


def load_settings() -> Settings:
    database_url = os.environ.get("PIK_DATABASE_URL") or os.environ.get("DATABASE_URL") or "sqlite:///./pik.db"
    redis_url = os.environ.get("PIK_REDIS_URL") or os.environ.get("REDIS_URL") or None
    return Settings(
        database_url=database_url,
        seed_demo=_bool("PIK_SEED_DEMO", True),
        rate_limit=_int("PIK_RATE_LIMIT", 120),
        rate_window_seconds=_int("PIK_RATE_WINDOW_SECONDS", 60),
        webhook_max_attempts=_int("PIK_WEBHOOK_MAX_ATTEMPTS", 5),
        webhook_timeout_seconds=_float("PIK_WEBHOOK_TIMEOUT_SECONDS", 5.0),
        webhook_backoff_base_seconds=_float("PIK_WEBHOOK_BACKOFF_BASE_SECONDS", 1.0),
        webhook_backoff_cap_seconds=_float("PIK_WEBHOOK_BACKOFF_CAP_SECONDS", 32.0),
        webhook_tolerance_seconds=_int("PIK_WEBHOOK_TOLERANCE_SECONDS", 300),
        worker_poll_seconds=_float("PIK_WORKER_POLL_SECONDS", 0.5),
        stuck_delivery_seconds=_int("PIK_STUCK_DELIVERY_SECONDS", 60),
        redis_url=redis_url,
        docs_base=os.environ.get(
            "PIK_DOCS_BASE",
            "https://github.com/misty6g/partner-integration-kit/blob/main/",
        ),
        log_level=os.environ.get("PIK_LOG_LEVEL", "INFO"),
    )
