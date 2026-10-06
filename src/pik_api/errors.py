"""Consistent partner-facing errors."""

from __future__ import annotations

import json
from typing import Any

DOC_PATHS: dict[str, str] = {
    "missing_api_key": "docs/authentication.md#missing-api-key",
    "invalid_api_key": "docs/authentication.md#invalid-api-key",
    "revoked_api_key": "docs/authentication.md#revoked-api-key",
    "rate_limit_exceeded": "docs/rate-limits.md#rate-limit-exceeded",
    "invalid_idempotency_key": "docs/idempotency.md#invalid-idempotency-key",
    "idempotency_key_conflict": "docs/idempotency.md#idempotency-key-conflict",
    "idempotency_in_progress": "docs/idempotency.md#idempotency-request-in-progress",
    "invalid_cursor": "docs/pagination.md#invalid-pagination-cursor",
    "order_not_found": "docs/orders.md#order-not-found",
    "invalid_order_transition": "docs/orders.md#invalid-order-transition",
    "duplicate_external_id": "docs/orders.md#duplicate-external-id",
    "validation_error": "docs/orders.md#order-validation-failed",
    "reserved_event_type": "docs/events.md#reserved-event-type",
    "invalid_event_type": "docs/events.md#invalid-event-type-filter",
    "payload_too_large": "docs/events.md#payload-too-large",
    "event_not_found": "docs/events.md#event-not-found",
    "webhook_url_blocked": "docs/webhooks.md#webhook-endpoint-unreachable",
    "invalid_webhook_url": "docs/webhooks.md#webhook-endpoint-unreachable",
    "endpoint_not_found": "docs/webhooks.md#endpoint-not-found",
    "delivery_not_found": "docs/webhooks.md#dead-letter-deliveries",
    "delivery_in_flight": "docs/webhooks.md#replaying-a-delivery",
    "signature_mismatch": "docs/webhooks.md#signature-mismatch",
    "timestamp_expired": "docs/webhooks.md#timestamp-outside-tolerance",
    "malformed_signature": "docs/webhooks.md#malformed-signature-header",
}


class APIError(Exception):
    def __init__(
        self,
        status: int,
        type_: str,
        code: str,
        message: str,
        *,
        details: Any = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.type = type_
        self.code = code
        self.message = message
        self.details = details
        self.headers = headers or {}

    def as_dict(self, docs_base: str, request_id: str | None = None) -> dict[str, Any]:
        error: dict[str, Any] = {
            "type": self.type,
            "code": self.code,
            "message": self.message,
            "doc_url": docs_base + DOC_PATHS.get(self.code, "docs/errors.md"),
        }
        if request_id:
            error["request_id"] = request_id
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}

    def json(self, docs_base: str, request_id: str | None = None) -> str:
        return json.dumps(self.as_dict(docs_base, request_id), separators=(",", ":"), sort_keys=True)
