"""Rate limiting in SQLite and the optional Redis backend."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

from fastapi.testclient import TestClient
from sqlalchemy import select

from pik_api.main import create_app
from pik_api.models import RateWindow
from pik_api.rate_limit import RedisRateLimiter
from pik_api.seed import create_partner_with_key


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expiry: dict[str, int] = {}

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        return self.store[key]

    def expire(self, key: str, seconds: int) -> bool:
        self.expiry[key] = seconds
        return True


def test_fixed_window_returns_429(tmp_path, settings):
    limited = replace(settings, rate_limit=2, rate_window_seconds=60, database_url=f"sqlite:///{tmp_path}/limited.db")
    app = create_app(limited)
    db = app.state.session_factory()
    try:
        create_partner_with_key(db, "Acme Robotics", "pk_test_limited_key_0001")
        db.commit()
    finally:
        db.close()
    headers = {"X-API-Key": "pk_test_limited_key_0001"}
    with TestClient(app) as client:
        assert client.get("/v1/account", headers=headers).status_code == 200
        assert client.get("/v1/account", headers=headers).status_code == 200
        blocked = client.get("/v1/account", headers=headers)
    assert blocked.status_code == 429
    assert blocked.json()["error"]["code"] == "rate_limit_exceeded"
    assert int(blocked.headers["Retry-After"]) >= 1
    assert blocked.headers["X-RateLimit-Remaining"] == "0"


def test_sqlite_window_survives_concurrent_increments(tmp_path, settings):
    app = create_app(replace(settings, database_url=f"sqlite:///{tmp_path}/concurrent.db", rate_limit=1000))
    db = app.state.session_factory()
    try:
        partner, _key = create_partner_with_key(db, "Acme Robotics", "pk_test_concurrent_key_01")
        db.commit()
        partner_id = partner.id
    finally:
        db.close()

    def hit() -> None:
        session = app.state.session_factory()
        try:
            decision = app.state.limiter.hit(db=session, partner_id=partner_id, limit=1000, window_seconds=60)
            assert decision.allowed
        finally:
            session.close()

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _index: hit(), range(40)))
    db = app.state.session_factory()
    try:
        rows = list(db.scalars(select(RateWindow)))
        assert sum(row.count for row in rows) == 40
    finally:
        db.close()
        app.state.engine.dispose()


def test_redis_limiter_expires_the_first_hit_and_blocks():
    client = FakeRedis()
    limiter = RedisRateLimiter(client)
    first = limiter.hit(db=None, partner_id="prt_1", limit=1, window_seconds=30, now=1_700_000_000)
    second = limiter.hit(db=None, partner_id="prt_1", limit=1, window_seconds=30, now=1_700_000_001)
    assert first.allowed is True
    assert first.remaining == 0
    assert second.allowed is False
    assert len(client.expiry) == 1
    assert list(client.expiry.values()) == [30]
