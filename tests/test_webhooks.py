"""Signed deliveries, retries, the dead-letter queue, and replay."""

from __future__ import annotations

from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import httpx
import pytest

from pik_api.ids import isoformat, parse_iso
from pik_api.models import WebhookDelivery, WebhookEndpoint
from pik_api.webhooks.delivery import attempt_delivery, backoff_seconds, process_due, recover_stuck
from pik_sdk.webhooks import verify
from tests.conftest import order_body


class ScriptedTransport:
    def __init__(self, outcomes: list) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[dict] = []

    def post(self, url: str, content: bytes, headers: dict, timeout: float):
        self.calls.append({"url": url, "content": content, "headers": headers, "timeout": timeout})
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def test_backoff_doubles_and_caps():
    from pik_api.config import Settings

    settings = Settings(webhook_backoff_base_seconds=1, webhook_backoff_cap_seconds=8)
    assert backoff_seconds(1, settings) == 1
    assert backoff_seconds(2, settings) == 2
    assert backoff_seconds(3, settings) == 4
    assert backoff_seconds(4, settings) == 8
    assert backoff_seconds(5, settings) == 8


def test_endpoint_secret_is_returned_once_and_ping_is_signed(client, auth):
    server, url = _listening_server(status=200)
    try:
        created = client.post(
            "/v1/webhook_endpoints",
            headers={**auth, "Idempotency-Key": "endpoint-1"},
            json={"url": url, "events": ["order.created"]},
        )
        assert created.status_code == 201
        secret = created.json()["secret"]
        assert secret.startswith("whsec_")
        replay = client.post(
            "/v1/webhook_endpoints",
            headers={**auth, "Idempotency-Key": "endpoint-1"},
            json={"url": url, "events": ["order.created"]},
        )
        assert replay.headers["Idempotent-Replayed"] == "true"
        assert replay.json()["secret"] == secret
        listed = client.get("/v1/webhook_endpoints", headers=auth)
        assert "secret" not in listed.json()["data"][0]
        assert listed.json()["data"][0]["secret_hint"].endswith(secret[-4:])

        ping = client.post(f"/v1/webhook_endpoints/{created.json()['id']}/ping", headers=auth)
        assert ping.status_code == 200
        body = ping.json()
        assert body["delivery_status"] == "succeeded"
        verify(body["body"].encode("utf-8"), body["signature"], secret, now=int(parse_iso(isoformat()).timestamp()))
        assert server.bodies
    finally:
        server.shutdown()


def test_subscription_filters_and_partner_events(client, auth):
    created = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://127.0.0.1:9/hooks", "events": ["order.cancelled"]},
    )
    assert created.status_code == 201
    order = client.post("/v1/orders", headers=auth, json=order_body(external_id="filter-me")).json()
    quiet = client.get("/v1/webhook_deliveries", headers=auth)
    assert quiet.json()["data"] == []

    client.post(f"/v1/orders/{order['id']}/cancel", headers=auth)
    deliveries = client.get("/v1/webhook_deliveries", headers=auth).json()["data"]
    assert len(deliveries) == 1
    assert deliveries[0]["event_type"] == "order.cancelled"
    assert deliveries[0]["status"] in {"pending", "failed", "dead_letter", "in_flight"}

    published = client.post(
        "/v1/events",
        headers=auth,
        json={"type": "inventory.updated", "data": {"sku": "WIDGET-1", "available": 4}},
    )
    assert published.status_code == 201
    reserved = client.post("/v1/events", headers=auth, json={"type": "order.created", "data": {"id": "nope"}})
    assert reserved.status_code == 400
    assert reserved.json()["error"]["code"] == "reserved_event_type"
    bad_filter = client.get("/v1/events", headers=auth, params={"type": "Order.Created"})
    assert bad_filter.status_code == 400
    assert bad_filter.json()["error"]["code"] == "invalid_event_type"

    huge = client.post("/v1/events", headers=auth, json={"type": "catalog.sync", "data": {"blob": "x" * 70000}})
    assert huge.status_code == 400
    assert huge.json()["error"]["code"] == "payload_too_large"


def test_retries_then_dead_letter_then_replay(app, client, auth):
    created = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://127.0.0.1:9/hooks", "events": ["order.created"]},
    )
    endpoint_id = created.json()["id"]
    order = client.post("/v1/orders", headers=auth, json=order_body(external_id="retry-me"))
    assert order.status_code == 201

    settings = replace(app.state.settings, webhook_max_attempts=3, webhook_backoff_base_seconds=1)
    transport = ScriptedTransport([(500, "nope"), (500, "nope"), TimeoutError("timed out")])
    moment = "2099-01-01T00:00:00.000000Z"
    db = app.state.session_factory()
    try:
        first = process_due(db, transport, settings, now_iso=moment)
        assert first == 1
        delivery = db.query(WebhookDelivery).one()
        assert delivery.status == "failed"
        assert delivery.attempt_count == 1
        assert delivery.last_response_status == 500
        delay = (parse_iso(delivery.next_attempt_at) - parse_iso(moment)).total_seconds()
        assert delay == pytest.approx(1)

        second_at = "2099-01-01T00:00:01.000000Z"
        assert process_due(db, transport, settings, now_iso=second_at) == 1
        db.refresh(delivery)
        assert delivery.status == "failed"
        assert delivery.attempt_count == 2

        third_at = "2099-01-01T00:00:03.000000Z"
        assert process_due(db, transport, settings, now_iso=third_at) == 1
        db.refresh(delivery)
        assert delivery.status == "dead_letter"
        assert delivery.attempt_count == 3
        assert delivery.last_error.startswith("TimeoutError")
        delivery_id = delivery.id
    finally:
        db.close()

    blocked = client.post(
        f"/v1/webhook_deliveries/{delivery_id}/replay",
        headers=auth,
    )
    # The row is dead_letter, so replay is allowed.
    assert blocked.status_code == 200
    assert blocked.json()["status"] == "pending"
    assert blocked.json()["attempt_count"] == 0

    transport_ok = ScriptedTransport([(200, "ok")])
    db = app.state.session_factory()
    try:
        assert process_due(db, transport_ok, settings, now_iso="2099-01-01T00:00:04.000000Z") == 1
        delivery = db.get(WebhookDelivery, delivery_id)
        assert delivery.status == "succeeded"
        assert delivery.delivered_at is not None
        verify(
            transport_ok.calls[0]["content"],
            transport_ok.calls[0]["headers"]["Pik-Signature"],
            created.json()["secret"],
            now=int(parse_iso("2099-01-01T00:00:04.000000Z").timestamp()),
        )
    finally:
        db.close()

    fetched = client.get(f"/v1/webhook_deliveries/{delivery_id}", headers=auth)
    assert fetched.json()["status"] == "succeeded"
    assert client.get("/v1/webhook_deliveries", headers=auth, params={"endpoint_id": endpoint_id}).json()["data"]


def test_replay_rejects_in_flight_and_disabled_endpoint_dead_letters(app, client, auth):
    created = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://hooks.partner.example/pik", "events": ["*"]},
    )
    client.post("/v1/orders", headers=auth, json=order_body(external_id="inflight"))
    db = app.state.session_factory()
    try:
        delivery = db.query(WebhookDelivery).one()
        delivery.status = "in_flight"
        db.commit()
        delivery_id = delivery.id
        endpoint = db.get(WebhookEndpoint, created.json()["id"])
        endpoint.status = "disabled"
        # A second delivery is created through the attempt path below.
        db.commit()
    finally:
        db.close()

    conflict = client.post(f"/v1/webhook_deliveries/{delivery_id}/replay", headers=auth)
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "delivery_in_flight"

    db = app.state.session_factory()
    try:
        delivery = db.get(WebhookDelivery, delivery_id)
        result = attempt_delivery(db, delivery, ScriptedTransport([(200, "ok")]), app.state.settings)
        assert result.status == "dead_letter"
        assert result.error == "endpoint_disabled"
    finally:
        db.close()


def test_recover_stuck_requeues_an_abandoned_attempt(app, client, auth):
    client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://hooks.partner.example/pik", "events": ["order.created"]},
    )
    client.post("/v1/orders", headers=auth, json=order_body(external_id="stuck"))
    db = app.state.session_factory()
    try:
        delivery = db.query(WebhookDelivery).one()
        delivery.status = "in_flight"
        delivery.updated_at = "2026-10-06T14:00:00.000000Z"
        db.commit()
        recovered = recover_stuck(db, app.state.settings, now_iso="2026-10-06T15:00:00.000000Z")
        assert recovered == 1
        db.refresh(delivery)
        assert delivery.status == "failed"
        assert delivery.last_error == "worker_interrupted"
    finally:
        db.close()


def test_rejects_blocked_and_credentialed_webhook_urls(client, auth):
    blocked = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "http://169.254.169.254/latest/meta-data", "events": ["order.created"]},
    )
    assert blocked.status_code == 400
    assert blocked.json()["error"]["code"] == "webhook_url_blocked"
    userinfo = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://user:pass@hooks.example/pik", "events": ["order.created"]},
    )
    assert userinfo.status_code == 400
    assert userinfo.json()["error"]["code"] == "invalid_webhook_url"
    missing = client.get("/v1/webhook_endpoints/whe_missing", headers=auth)
    assert missing.status_code == 404


def test_worker_tick_delivers_pending_rows(app, client, auth):
    from pik_api.worker import serve

    client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://hooks.partner.example/pik", "events": ["order.created"]},
    )
    client.post("/v1/orders", headers=auth, json=order_body(external_id="worker"))
    transport = ScriptedTransport([(200, "ok")])
    assert serve(app.state.settings, app.state.session_factory, transport, max_ticks=1) == 0
    db = app.state.session_factory()
    try:
        delivery = db.query(WebhookDelivery).one()
        assert delivery.status == "succeeded"
    finally:
        db.close()
    assert transport.calls[0]["headers"]["User-Agent"] == "PIK-Webhooks/0.1"


def _listening_server(status: int):
    bodies: list[bytes] = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            bodies.append(self.rfile.read(length))
            self.send_response(status)
            self.end_headers()

        def log_message(self, _format, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.bodies = bodies
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_address[1]}/hooks"
    return server, url


def test_httpx_transport_posts_bytes():
    from pik_api.webhooks.delivery import HttpxTransport

    server, url = _listening_server(204)
    try:
        status, _text = HttpxTransport().post(url, b"{}", {"Content-Type": "application/json"}, 2)
        assert status == 204
        assert server.bodies == [b"{}"]
    finally:
        server.shutdown()


def test_update_endpoint_and_foreign_delivery_hidden(client, auth, other_partner):
    created = client.post(
        "/v1/webhook_endpoints",
        headers=auth,
        json={"url": "https://hooks.partner.example/pik", "events": ["order.created"]},
    ).json()
    patched = client.patch(
        f"/v1/webhook_endpoints/{created['id']}",
        headers=auth,
        json={"url": "https://hooks.partner.example/v2", "events": ["order.created", "order.fulfilled"], "status": "disabled"},
    )
    assert patched.status_code == 200
    assert patched.json()["status"] == "disabled"
    removed = client.delete(f"/v1/webhook_endpoints/{created['id']}", headers=auth)
    assert removed.json()["status"] == "disabled"
    hidden = client.get(f"/v1/webhook_endpoints/{created['id']}", headers={"X-API-Key": other_partner["key"]})
    assert hidden.status_code == 404
    ping = client.post(f"/v1/webhook_endpoints/{created['id']}/ping", headers=auth)
    assert ping.status_code == 409
