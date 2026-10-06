"""Sign, enqueue, and deliver outbound webhooks."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from pik_api.config import Settings
from pik_api.errors import APIError
from pik_api.ids import isoformat, new_id, parse_iso, utcnow
from pik_api.models import Event, WebhookDelivery, WebhookEndpoint
from pik_api.webhooks.urls import canonical_json, loads
from pik_sdk.webhooks import sign

log = logging.getLogger("pik.webhooks")


class Transport(Protocol):
    def post(self, url: str, content: bytes, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        """Return HTTP status and a short response excerpt."""


class HttpxTransport:
    def post(self, url: str, content: bytes, headers: dict[str, str], timeout: float) -> tuple[int, str]:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(url, content=content, headers=headers)
        text = response.text[:500]
        return response.status_code, text


@dataclass
class AttemptResult:
    status: str
    signature: str
    response_status: int | None
    error: str | None


def backoff_seconds(attempt_count: int, settings: Settings) -> float:
    """Exponential delay after ``attempt_count`` failures. ``attempt_count`` is 1-based."""
    exponent = max(0, attempt_count - 1)
    delay = settings.webhook_backoff_base_seconds * (2**exponent)
    return min(settings.webhook_backoff_cap_seconds, delay)


def envelope(event: Event) -> dict:
    return {
        "id": event.id,
        "type": event.type,
        "api_version": "2026-10-06",
        "created_at": event.created_at,
        "data": {"object": loads(event.data_json)},
    }


def emit_event(
    db: Session,
    *,
    partner_id: str,
    event_type: str,
    data: dict,
    only_endpoint_id: str | None = None,
    ignore_subscription: bool = False,
) -> Event:
    event = Event(
        id=new_id("evt"),
        partner_id=partner_id,
        type=event_type,
        data_json=canonical_json(data),
        created_at=isoformat(),
    )
    db.add(event)
    db.flush()
    body = canonical_json(envelope(event))
    endpoints = db.scalars(
        select(WebhookEndpoint).where(
            WebhookEndpoint.partner_id == partner_id,
            WebhookEndpoint.status == "active",
        )
    ).all()
    moment = event.created_at
    for endpoint in endpoints:
        if only_endpoint_id is not None and endpoint.id != only_endpoint_id:
            continue
        subscribed = loads(endpoint.events_json)
        if not ignore_subscription and not _subscribed(subscribed, event_type):
            continue
        db.add(
            WebhookDelivery(
                id=new_id("whd"),
                partner_id=partner_id,
                endpoint_id=endpoint.id,
                event_id=event.id,
                event_type=event_type,
                body=body,
                status="pending",
                attempt_count=0,
                next_attempt_at=moment,
                last_response_status=None,
                last_error=None,
                created_at=moment,
                updated_at=moment,
                delivered_at=None,
            )
        )
    return event


def _subscribed(subscribed: list, event_type: str) -> bool:
    if not isinstance(subscribed, list):
        return False
    return "*" in subscribed or event_type in subscribed


def delivery_headers(delivery: WebhookDelivery, signature: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "User-Agent": "PIK-Webhooks/0.1",
        "Pik-Signature": signature,
        "Pik-Delivery": delivery.id,
        "Pik-Event-Id": delivery.event_id,
        "Pik-Event-Type": delivery.event_type,
    }


def attempt_delivery(
    db: Session,
    delivery: WebhookDelivery,
    transport: Transport,
    settings: Settings,
    *,
    now_iso: str | None = None,
) -> AttemptResult:
    moment = now_iso or isoformat()
    endpoint = db.get(WebhookEndpoint, delivery.endpoint_id)
    if endpoint is None or endpoint.status != "active":
        delivery.status = "dead_letter"
        delivery.last_error = "endpoint_disabled"
        delivery.updated_at = moment
        db.commit()
        return AttemptResult(delivery.status, "", None, delivery.last_error)

    timestamp = int(parse_iso(moment).timestamp())
    payload = delivery.body.encode("utf-8")
    signature = sign(payload, endpoint.secret, timestamp=timestamp)
    headers = delivery_headers(delivery, signature)
    response_status: int | None = None
    try:
        response_status, text = transport.post(
            endpoint.url,
            payload,
            headers,
            settings.webhook_timeout_seconds,
        )
        delivery.last_response_status = response_status
        if 200 <= response_status < 300:
            delivery.attempt_count += 1
            delivery.status = "succeeded"
            delivery.delivered_at = moment
            delivery.last_error = None
            delivery.updated_at = moment
            db.commit()
            log.info("webhook delivered id=%s status=%s", delivery.id, response_status)
            return AttemptResult("succeeded", signature, response_status, None)
        error = f"HTTP {response_status}: {text[:200]}"
    except Exception as exc:  # transport failures are expected and retried
        error = f"{type(exc).__name__}: {exc}"
        delivery.last_response_status = None
    _schedule_failure(delivery, settings, moment, error[:500])
    db.commit()
    log.info("webhook attempt failed id=%s attempt=%s error=%s", delivery.id, delivery.attempt_count, delivery.last_error)
    return AttemptResult(delivery.status, signature, response_status, delivery.last_error)


def _schedule_failure(delivery: WebhookDelivery, settings: Settings, moment: str, error: str) -> None:
    delivery.attempt_count += 1
    delivery.last_error = error
    delivery.updated_at = moment
    if delivery.attempt_count >= settings.webhook_max_attempts:
        delivery.status = "dead_letter"
        delivery.next_attempt_at = moment
        return
    delay = backoff_seconds(delivery.attempt_count, settings)
    delivery.status = "failed"
    next_at = parse_iso(moment) + timedelta(seconds=delay)
    delivery.next_attempt_at = isoformat(next_at)


def process_due(
    db: Session,
    transport: Transport,
    settings: Settings,
    *,
    now_iso: str | None = None,
    limit: int = 50,
) -> int:
    moment = now_iso or isoformat()
    due = list(
        db.scalars(
            select(WebhookDelivery)
            .where(WebhookDelivery.status.in_(("pending", "failed")))
            .where(WebhookDelivery.next_attempt_at <= moment)
            .order_by(WebhookDelivery.next_attempt_at.asc(), WebhookDelivery.id.asc())
            .limit(limit)
        )
    )
    processed = 0
    for delivery in due:
        delivery.status = "in_flight"
        delivery.updated_at = moment
        db.commit()
        attempt_delivery(db, delivery, transport, settings, now_iso=moment)
        processed += 1
    return processed


def recover_stuck(db: Session, settings: Settings, *, now_iso: str | None = None) -> int:
    """Requeue deliveries left in_flight by a crashed worker."""
    moment = now_iso or isoformat()
    cutoff = isoformat(parse_iso(moment) - timedelta(seconds=settings.stuck_delivery_seconds))
    stuck = list(
        db.scalars(
            select(WebhookDelivery).where(
                WebhookDelivery.status == "in_flight",
                WebhookDelivery.updated_at <= cutoff,
            )
        )
    )
    for delivery in stuck:
        _schedule_failure(delivery, settings, moment, "worker_interrupted")
    if stuck:
        db.commit()
    return len(stuck)


def replay_delivery(db: Session, delivery: WebhookDelivery) -> WebhookDelivery:
    if delivery.status == "in_flight":
        raise APIError(
            409,
            "invalid_request_error",
            "delivery_in_flight",
            "This delivery is in flight. Wait for the attempt to finish before replaying it.",
        )
    moment = isoformat()
    delivery.status = "pending"
    delivery.attempt_count = 0
    delivery.next_attempt_at = moment
    delivery.last_error = None
    delivery.last_response_status = None
    delivery.delivered_at = None
    delivery.updated_at = moment
    db.commit()
    return delivery


def require_json_object(data: dict, limit: int = 65_536) -> str:
    try:
        encoded = json.dumps(data, ensure_ascii=False)
    except (TypeError, ValueError) as exc:
        raise APIError(400, "invalid_request_error", "validation_error", "Event data must be JSON.") from exc
    if len(encoded.encode("utf-8")) > limit:
        raise APIError(
            400,
            "invalid_request_error",
            "payload_too_large",
            "Event data exceeds 64 KiB.",
        )
    return encoded
