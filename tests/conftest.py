"""Shared fixtures for the API tests."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from pik_api.config import Settings
from pik_api.main import create_app
from pik_api.seed import create_partner_with_key

ACME_KEY = "pk_test_fixture_acme_key_0001"
NORTH_KEY = "pk_test_fixture_north_key_002"


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(
        database_url=f"sqlite:///{tmp_path}/pik.db",
        seed_demo=False,
        rate_limit=1000,
        rate_window_seconds=60,
        webhook_max_attempts=5,
        webhook_timeout_seconds=2.0,
        webhook_backoff_base_seconds=1.0,
        webhook_backoff_cap_seconds=32.0,
        stuck_delivery_seconds=60,
    )


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app):
    with TestClient(app) as test_client:
        yield test_client


def _partner(app, name: str, raw_key: str) -> dict:
    db = app.state.session_factory()
    try:
        record, _api_key = create_partner_with_key(db, name, raw_key)
        db.commit()
        return {"id": record.id, "name": name, "key": raw_key}
    finally:
        db.close()


@pytest.fixture
def partner(app) -> dict:
    return _partner(app, "Acme Robotics", ACME_KEY)


@pytest.fixture
def other_partner(app) -> dict:
    return _partner(app, "Northwind Outdoors", NORTH_KEY)


@pytest.fixture
def auth(partner) -> dict[str, str]:
    return {"X-API-Key": partner["key"]}


def order_body(**overrides) -> dict:
    body = {
        "external_id": "ord_partner_1001",
        "currency": "usd",
        "customer": {"name": "Ada Lovelace", "email": "ada@analytical.example"},
        "items": [{"sku": "WIDGET-1", "quantity": 2, "unit_amount": 2500}],
    }
    body.update(overrides)
    return body
