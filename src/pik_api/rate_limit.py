"""Fixed-window rate limiting in SQLite, with an optional Redis backend."""

from __future__ import annotations

import time
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.orm import Session

from pik_api.models import RateWindow


@dataclass(frozen=True)
class RateDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_epoch: int
    retry_after: int


class DatabaseRateLimiter:
    """One row per partner per window. Safe for the API and worker sharing SQLite."""

    def hit(self, *, db: Session, partner_id: str, limit: int, window_seconds: int, now: float | None = None) -> RateDecision:
        current = time.time() if now is None else now
        window = max(1, int(window_seconds))
        start = int(current // window) * window
        # One statement so concurrent requests cannot both insert the window row.
        db.execute(
            text(
                """
                INSERT INTO rate_windows (partner_id, window_start, count)
                VALUES (:partner_id, :window_start, 1)
                ON CONFLICT(partner_id, window_start)
                DO UPDATE SET count = count + 1
                """
            ),
            {"partner_id": partner_id, "window_start": start},
        )
        db.commit()
        row = db.get(RateWindow, (partner_id, start))
        count = row.count if row is not None else limit + 1
        reset = start + window
        retry_after = max(1, int(reset - current))
        allowed = count <= limit
        remaining = max(0, limit - count)
        return RateDecision(allowed=allowed, limit=limit, remaining=remaining, reset_epoch=reset, retry_after=retry_after)


class RedisRateLimiter:
    """Optional backend selected when ``PIK_REDIS_URL`` is set.

    ``client`` must implement ``incr(key) -> int`` and ``expire(key, seconds) -> object``.
    """

    def __init__(self, client: object) -> None:
        self.client = client

    def hit(self, *, db: Session, partner_id: str, limit: int, window_seconds: int, now: float | None = None) -> RateDecision:
        del db
        current = time.time() if now is None else now
        window = max(1, int(window_seconds))
        start = int(current // window) * window
        key = f"pik:rl:{partner_id}:{start}"
        count = int(self.client.incr(key))  # type: ignore[attr-defined]
        if count == 1:
            self.client.expire(key, window)  # type: ignore[attr-defined]
        reset = start + window
        retry_after = max(1, int(reset - current))
        allowed = count <= limit
        remaining = max(0, limit - count)
        return RateDecision(allowed=allowed, limit=limit, remaining=remaining, reset_epoch=reset, retry_after=retry_after)


def build_limiter(redis_url: str | None) -> DatabaseRateLimiter | RedisRateLimiter:
    if not redis_url:
        return DatabaseRateLimiter()
    import redis

    client = redis.Redis.from_url(redis_url, socket_timeout=1.0, socket_connect_timeout=1.0)
    return RedisRateLimiter(client)
