"""Orders, pagination, idempotency, and partner isolation."""

from __future__ import annotations

from tests.conftest import order_body


def test_create_get_and_list_orders(client, auth):
    created = client.post("/v1/orders", headers=auth, json=order_body())
    assert created.status_code == 201
    order = created.json()
    assert order["id"].startswith("ord_")
    assert order["status"] == "open"
    assert order["amount"] == 5000
    assert order["customer"]["email"] == "ada@analytical.example"
    assert order["items"][0]["sku"] == "WIDGET-1"

    fetched = client.get(f"/v1/orders/{order['id']}", headers=auth)
    assert fetched.status_code == 200
    assert fetched.json()["id"] == order["id"]

    listed = client.get("/v1/orders", headers=auth, params={"status": "open", "limit": 10})
    assert listed.status_code == 200
    assert listed.json()["data"][0]["id"] == order["id"]
    assert listed.json()["has_more"] is False


def test_validation_error_shape_and_unicode_customer(client, auth):
    bad = client.post("/v1/orders", headers=auth, json=order_body(currency="USD", items=[]))
    assert bad.status_code == 400
    error = bad.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"]
    assert error["request_id"]

    created = client.post(
        "/v1/orders",
        headers=auth,
        json=order_body(external_id="ord_unicode", customer={"name": "José", "email": "jose@example.com"}),
    )
    assert created.status_code == 201
    assert created.json()["customer"]["name"] == "José"


def test_cursor_pagination_walks_every_order_once(client, auth):
    ids = []
    for index in range(5):
        response = client.post("/v1/orders", headers=auth, json=order_body(external_id=f"ord_{index}"))
        assert response.status_code == 201
        ids.append(response.json()["id"])
    seen = []
    cursor = None
    pages = 0
    while True:
        params = {"limit": 2}
        if cursor:
            params["starting_after"] = cursor
        page = client.get("/v1/orders", headers=auth, params=params)
        assert page.status_code == 200
        body = page.json()
        pages += 1
        seen.extend(item["id"] for item in body["data"])
        if not body["has_more"]:
            assert body["next_cursor"] is None
            break
        cursor = body["next_cursor"]
    assert pages == 3
    assert seen == list(reversed(ids))


def test_invalid_cursor_and_foreign_order_are_not_visible(client, auth, other_partner):
    created = client.post("/v1/orders", headers=auth, json=order_body())
    order_id = created.json()["id"]
    foreign = client.get(f"/v1/orders/{order_id}", headers={"X-API-Key": other_partner["key"]})
    assert foreign.status_code == 404
    assert foreign.json()["error"]["code"] == "order_not_found"

    missing = client.get("/v1/orders", headers=auth, params={"starting_after": "ord_does_not_exist"})
    assert missing.status_code == 400
    assert missing.json()["error"]["code"] == "invalid_cursor"

    stolen = client.get("/v1/orders", headers={"X-API-Key": other_partner["key"]}, params={"starting_after": order_id})
    assert stolen.status_code == 400
    assert stolen.json()["error"]["code"] == "invalid_cursor"


def test_idempotency_replays_and_conflicts(client, auth):
    headers = {**auth, "Idempotency-Key": "create-1001"}
    first = client.post("/v1/orders", headers=headers, json=order_body())
    second = client.post("/v1/orders", headers=headers, json=order_body())
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]
    assert second.headers["Idempotent-Replayed"] == "true"
    listed = client.get("/v1/orders", headers=auth)
    assert len(listed.json()["data"]) == 1

    conflict = client.post(
        "/v1/orders",
        headers=headers,
        json=order_body(items=[{"sku": "OTHER", "quantity": 1, "unit_amount": 100}]),
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_key_conflict"


def test_invalid_idempotency_key_and_duplicate_external_id(client, auth):
    bad_key = client.post("/v1/orders", headers={**auth, "Idempotency-Key": "has spaces"}, json=order_body())
    assert bad_key.status_code == 400
    assert bad_key.json()["error"]["code"] == "invalid_idempotency_key"

    assert client.post("/v1/orders", headers=auth, json=order_body()).status_code == 201
    duplicate = client.post("/v1/orders", headers=auth, json=order_body(external_id="ord_partner_1001"))
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "duplicate_external_id"


def test_fulfill_and_cancel_transitions(client, auth):
    first = client.post("/v1/orders", headers=auth, json=order_body(external_id="ord_a")).json()
    second = client.post("/v1/orders", headers=auth, json=order_body(external_id="ord_b")).json()

    fulfilled = client.post(f"/v1/orders/{first['id']}/fulfill", headers=auth)
    assert fulfilled.status_code == 200
    assert fulfilled.json()["status"] == "fulfilled"
    again = client.post(f"/v1/orders/{first['id']}/fulfill", headers=auth)
    assert again.status_code == 200

    illegal = client.post(f"/v1/orders/{first['id']}/cancel", headers=auth)
    assert illegal.status_code == 409
    assert illegal.json()["error"]["code"] == "invalid_order_transition"

    cancelled = client.post(f"/v1/orders/{second['id']}/cancel", headers=auth)
    assert cancelled.json()["status"] == "cancelled"
    repeat = client.post(f"/v1/orders/{second['id']}/cancel", headers={**auth, "Idempotency-Key": "cancel-b"})
    assert repeat.status_code == 200
    replay = client.post(f"/v1/orders/{second['id']}/cancel", headers={**auth, "Idempotency-Key": "cancel-b"})
    assert replay.headers["Idempotent-Replayed"] == "true"

    events = client.get("/v1/events", headers=auth, params={"type": "order.fulfilled"}).json()
    assert len(events["data"]) == 1
    assert events["data"][0]["data"]["id"] == first["id"]


def test_idempotency_in_progress_short_circuits(client, app, auth, partner):
    import json

    from pik_api.idempotency import begin_idempotency

    raw = json.dumps(order_body()).encode()
    db = app.state.session_factory()
    try:
        begin_idempotency(
            db,
            partner_id=partner["id"],
            method="POST",
            path="/v1/orders",
            body=raw,
            key="still-running",
        )
    finally:
        db.close()
    response = client.post(
        "/v1/orders",
        headers={**auth, "Idempotency-Key": "still-running", "Content-Type": "application/json"},
        content=raw,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_in_progress"
