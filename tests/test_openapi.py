"""OpenAPI document covers the partner surface."""

from __future__ import annotations

import json
from pathlib import Path


def test_committed_spec_matches_the_app(app):
    committed = json.loads((Path(__file__).resolve().parents[1] / "openapi" / "openapi.json").read_text(encoding="utf-8"))
    assert committed == app.openapi()


def test_openapi_security_and_operations(app):
    spec = app.openapi()
    assert spec["info"]["title"] == "Partner Integration Kit API"
    paths = spec["paths"]
    for path in (
        "/v1/account",
        "/v1/orders",
        "/v1/orders/{order_id}",
        "/v1/orders/{order_id}/cancel",
        "/v1/orders/{order_id}/fulfill",
        "/v1/events",
        "/v1/webhook_endpoints",
        "/v1/webhook_endpoints/{endpoint_id}/ping",
        "/v1/webhook_deliveries",
        "/v1/webhook_deliveries/{delivery_id}/replay",
    ):
        assert path in paths
    create_order = paths["/v1/orders"]["post"]
    assert create_order["operationId"] == "createOrder"
    parameters = {item["name"] for item in create_order.get("parameters", [])}
    assert "Idempotency-Key" in parameters
    assert "ApiKeyAuth" in spec["components"]["securitySchemes"]
    scheme = spec["components"]["securitySchemes"]["ApiKeyAuth"]
    assert scheme["in"] == "header"
    assert scheme["name"] == "X-API-Key"
    assert paths["/v1/health"]["get"].get("security") in (None, [])
    assert create_order["security"] == [{"ApiKeyAuth": []}]
