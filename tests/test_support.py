"""Configuration, seeding, worker loop, and CLI edges."""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient

from pik_api.config import load_settings
from pik_api.ids import isoformat, parse_iso
from pik_api.main import create_app
from pik_api.rate_limit import DatabaseRateLimiter, RedisRateLimiter, build_limiter
from pik_api.seed import DEMO_PARTNERS, seed_demo_partners
from pik_cli.checks import _error_detail, check_api_key, check_tls, run_checks
from tests.conftest import order_body


def test_load_settings_reads_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("PIK_DATABASE_URL", f"sqlite:///{tmp_path}/from-env.db")
    monkeypatch.setenv("PIK_SEED_DEMO", "no")
    monkeypatch.setenv("PIK_RATE_LIMIT", "7")
    monkeypatch.setenv("PIK_WEBHOOK_BACKOFF_BASE_SECONDS", "0.25")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:6399/0")
    settings = load_settings()
    assert settings.database_url.endswith("from-env.db")
    assert settings.seed_demo is False
    assert settings.rate_limit == 7
    assert settings.webhook_backoff_base_seconds == 0.25
    assert settings.redis_url == "redis://127.0.0.1:6399/0"


def test_demo_seed_is_idempotent_and_lifespan_loads_it(tmp_path):
    from pik_api.config import Settings

    settings = Settings(database_url=f"sqlite:///{tmp_path}/demo.db", seed_demo=True, rate_limit=1000)
    app = create_app(settings)
    db = app.state.session_factory()
    try:
        seed_demo_partners(db)
        seed_demo_partners(db)
    finally:
        db.close()
    with TestClient(app) as client:
        for name, key in DEMO_PARTNERS:
            response = client.get("/v1/account", headers={"X-API-Key": key})
            assert response.status_code == 200
            assert response.json()["name"] == name


def test_timestamps_round_trip_naive_and_offset():
    naive = parse_iso("2026-10-06T15:00:00.000000+00:00").replace(tzinfo=None)
    rendered = isoformat(naive)
    assert rendered.endswith("Z")
    assert parse_iso(rendered).tzinfo is not None


def test_build_limiter_selects_redis_when_configured():
    pytest.importorskip("redis")
    assert isinstance(build_limiter(None), DatabaseRateLimiter)
    limiter = build_limiter("redis://127.0.0.1:6399/0")
    assert isinstance(limiter, RedisRateLimiter)


def test_worker_sleeps_between_ticks_and_main_handles_interrupt(monkeypatch, tmp_path, app):
    from pik_api.worker import main, serve

    sleeps: list[float] = []
    monkeypatch.setattr("pik_api.worker.tick", lambda *args, **kwargs: 0)
    assert serve(app.state.settings, None, None, sleep=sleeps.append, max_ticks=2) == 0
    assert sleeps == [app.state.settings.worker_poll_seconds]

    monkeypatch.setenv("PIK_DATABASE_URL", f"sqlite:///{tmp_path}/worker.db")
    monkeypatch.setenv("PIK_SEED_DEMO", "0")

    def interrupt(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr("pik_api.worker.serve", interrupt)
    assert main() == 0


def test_cli_skips_optional_checks_and_formats_errors(client, partner):
    report = run_checks(client, api_key=partner["key"], webhook_url=None)
    assert [check.status for check in report.checks] == ["PASS", "SKIP", "SKIP", "SKIP"]
    assert report.failed == 0

    class Boom:
        def get(self, *_args, **_kwargs):
            raise httpx.ConnectError("connection refused")

    failed = check_api_key(Boom(), "pk_test_x")  # type: ignore[arg-type]
    assert failed.status == "FAIL"
    plain = httpx.Response(500, text="gateway exploded")
    assert "gateway exploded" in _error_detail(plain)
    structured = httpx.Response(400, json={"error": {"message": "nope"}})
    assert _error_detail(structured) == "nope"
    refused = check_tls("https://127.0.0.1:9/hooks", timeout=0.5)
    assert refused.status == "FAIL"
    weird = check_tls("ftp://files.example/hooks", timeout=0.5)
    assert weird.status == "FAIL"


def test_event_and_delivery_lookups_and_order_without_customer(client, auth):
    missing_event = client.get("/v1/events/evt_missing", headers=auth)
    assert missing_event.status_code == 404
    assert missing_event.json()["error"]["code"] == "event_not_found"
    empty = client.get("/v1/events", headers=auth, params={"type": "inventory.missing"})
    assert empty.json()["data"] == []
    missing_delivery = client.get("/v1/webhook_deliveries/whd_missing", headers=auth)
    assert missing_delivery.status_code == 404

    created = client.post(
        "/v1/orders",
        headers=auth,
        json={"currency": "usd", "items": [{"sku": "PLAIN", "quantity": 1, "unit_amount": 100}]},
    )
    assert created.status_code == 201
    assert created.json()["customer"] is None
    assert created.json()["external_id"] is None


def test_ping_targets_only_the_requested_endpoint(client, auth):
    first = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://127.0.0.1:9/a", "events": ["order.created"]},
    ).json()
    client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://127.0.0.1:9/b", "events": ["*"]},
    )
    ping = client.post(f"/v1/webhook_endpoints/{first['id']}/ping", headers=auth)
    assert ping.status_code == 200
    deliveries = client.get("/v1/webhook_deliveries", headers=auth).json()["data"]
    assert len(deliveries) == 1
    assert deliveries[0]["endpoint_id"] == first["id"]
    assert deliveries[0]["event_type"] == "webhook.ping"


def test_blocked_metadata_hostname(client, auth):
    response = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://metadata.google.internal/computeMetadata/v1/", "events": ["order.created"]},
    )
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "webhook_url_blocked"
    bad_event = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://hooks.partner.example/pik", "events": ["Not Valid"]},
    )
    assert bad_event.status_code == 400
    assert order_body()["currency"] == "usd"
