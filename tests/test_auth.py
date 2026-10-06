"""Authentication, health, and account."""

from __future__ import annotations

from pik_api.ids import isoformat
from pik_api.models import ApiKey


def test_health_ready_and_root_do_not_require_a_key(client):
    health = client.get("/v1/health")
    ready = client.get("/v1/ready")
    root = client.get("/")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert root.json()["service"] == "partner-integration-kit"
    assert "X-RateLimit-Limit" not in health.headers


def test_missing_invalid_and_revoked_keys(client, app, partner):
    missing = client.get("/v1/account")
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "missing_api_key"

    invalid = client.get("/v1/account", headers={"X-API-Key": "pk_test_not_a_real_key"})
    assert invalid.status_code == 401
    assert invalid.json()["error"]["code"] == "invalid_api_key"

    db = app.state.session_factory()
    try:
        record = db.query(ApiKey).one()
        record.revoked_at = isoformat()
        db.commit()
    finally:
        db.close()
    revoked = client.get("/v1/account", headers={"X-API-Key": partner["key"]})
    assert revoked.status_code == 401
    assert revoked.json()["error"]["code"] == "revoked_api_key"


def test_account_echoes_prefix_and_rate_limit_headers(client, auth, partner):
    response = client.get("/v1/account", headers={**auth, "X-Request-Id": "req_partner_trace"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == partner["id"]
    assert body["name"] == "Acme Robotics"
    assert body["api_key_prefix"] == partner["key"][:16]
    assert body["rate_limit"] == 1000
    assert response.headers["X-Request-Id"] == "req_partner_trace"
    assert response.headers["X-RateLimit-Limit"] == "1000"
    assert int(response.headers["X-RateLimit-Remaining"]) == 999


def test_rejects_a_hostile_request_id(client, auth):
    response = client.get("/v1/account", headers={**auth, "X-Request-Id": "bad id with spaces"})
    assert response.status_code == 200
    assert response.headers["X-Request-Id"].startswith("req_")
