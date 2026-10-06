"""Webhook endpoint registration, delivery inspection, and replay."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from pik_api.auth import Db, PartnerDep
from pik_api.errors import APIError
from pik_api.idempotency import begin_idempotency, complete_idempotency
from pik_api.ids import isoformat, new_id
from pik_api.models import Partner, WebhookDelivery, WebhookEndpoint
from pik_api.pagination import paginate
from pik_api.schemas import (
    WebhookDeliveryList,
    WebhookDeliveryOut,
    WebhookEndpointCreate,
    WebhookEndpointCreated,
    WebhookEndpointList,
    WebhookEndpointOut,
    WebhookEndpointUpdate,
    WebhookPingOut,
)
from pik_api.webhooks.delivery import attempt_delivery, emit_event, replay_delivery
from pik_api.webhooks.urls import canonical_json, loads, validate_event_names, validate_webhook_url

router = APIRouter(prefix="/v1", tags=["Webhooks"])

_ERRORS = {
    400: {"description": "Invalid request"},
    401: {"description": "Authentication failed"},
    404: {"description": "Not found"},
    409: {"description": "Conflict"},
    429: {"description": "Rate limit exceeded"},
}


def _hint(secret: str) -> str:
    return f"whsec_…{secret[-4:]}"


def endpoint_out(endpoint: WebhookEndpoint) -> WebhookEndpointOut:
    return WebhookEndpointOut(
        id=endpoint.id,
        url=endpoint.url,
        events=loads(endpoint.events_json),
        status=endpoint.status,
        secret_hint=_hint(endpoint.secret),
        created_at=endpoint.created_at,
        updated_at=endpoint.updated_at,
    )


def delivery_out(delivery: WebhookDelivery) -> WebhookDeliveryOut:
    return WebhookDeliveryOut(
        id=delivery.id,
        endpoint_id=delivery.endpoint_id,
        event_id=delivery.event_id,
        event_type=delivery.event_type,
        status=delivery.status,
        attempt_count=delivery.attempt_count,
        next_attempt_at=delivery.next_attempt_at,
        last_response_status=delivery.last_response_status,
        last_error=delivery.last_error,
        created_at=delivery.created_at,
        updated_at=delivery.updated_at,
        delivered_at=delivery.delivered_at,
    )


def _require_endpoint(db: Session, partner_id: str, endpoint_id: str) -> WebhookEndpoint:
    endpoint = db.get(WebhookEndpoint, endpoint_id)
    if endpoint is None or endpoint.partner_id != partner_id:
        raise APIError(404, "invalid_request_error", "endpoint_not_found", "No such webhook endpoint.")
    return endpoint


def _require_delivery(db: Session, partner_id: str, delivery_id: str) -> WebhookDelivery:
    delivery = db.get(WebhookDelivery, delivery_id)
    if delivery is None or delivery.partner_id != partner_id:
        raise APIError(404, "invalid_request_error", "delivery_not_found", "No such webhook delivery.")
    return delivery


async def _guard(request: Request, db: Session, partner: Partner, key: str | None):
    raw = await request.body()
    state = begin_idempotency(
        db,
        partner_id=partner.id,
        method=request.method,
        path=request.url.path,
        body=raw,
        key=key,
    )
    if state.replay:
        headers = {"Idempotent-Replayed": "true"}
        return state, Response(content=state.replay_body, status_code=state.replay_status or 200, media_type="application/json", headers=headers)
    return state, None


def _stored_error(request: Request, db: Session, state, exc: APIError) -> Response:
    body = exc.json(request.app.state.settings.docs_base, getattr(request.state, "request_id", None))
    complete_idempotency(db, state, exc.status, body)
    return Response(content=body, status_code=exc.status, media_type="application/json", headers=exc.headers)


@router.post(
    "/webhook_endpoints",
    response_model=WebhookEndpointCreated,
    status_code=201,
    operation_id="createWebhookEndpoint",
    responses=_ERRORS,
    summary="Register a webhook endpoint",
)
async def create_webhook_endpoint(
    payload: WebhookEndpointCreate,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    state, replay = await _guard(request, db, partner, idempotency_key)
    if replay is not None:
        return replay
    try:
        url = validate_webhook_url(payload.url)
        events = validate_event_names(payload.events)
        moment = isoformat()
        secret = "whsec_" + secrets.token_urlsafe(24)
        endpoint = WebhookEndpoint(
            id=new_id("whe"),
            partner_id=partner.id,
            url=url,
            secret=secret,
            events_json=canonical_json(events),
            status="active",
            created_at=moment,
            updated_at=moment,
        )
        db.add(endpoint)
        db.commit()
        body_model = WebhookEndpointCreated(**endpoint_out(endpoint).model_dump(), secret=secret)
        body = body_model.model_dump_json()
        complete_idempotency(db, state, 201, body)
        return Response(content=body, status_code=201, media_type="application/json")
    except APIError as exc:
        db.rollback()
        return _stored_error(request, db, state, exc)


@router.get(
    "/webhook_endpoints",
    response_model=WebhookEndpointList,
    operation_id="listWebhookEndpoints",
    responses=_ERRORS,
    summary="List webhook endpoints",
)
def list_webhook_endpoints(
    partner: PartnerDep,
    db: Db,
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: str | None = Query(default=None),
) -> WebhookEndpointList:
    stmt = select(WebhookEndpoint).where(WebhookEndpoint.partner_id == partner.id)
    rows, has_more, cursor = paginate(
        db, stmt, WebhookEndpoint, partner_id=partner.id, starting_after=starting_after, limit=limit
    )
    return WebhookEndpointList(data=[endpoint_out(row) for row in rows], has_more=has_more, next_cursor=cursor)


@router.get(
    "/webhook_endpoints/{endpoint_id}",
    response_model=WebhookEndpointOut,
    operation_id="getWebhookEndpoint",
    responses=_ERRORS,
    summary="Retrieve a webhook endpoint",
)
def get_webhook_endpoint(endpoint_id: str, partner: PartnerDep, db: Db) -> WebhookEndpointOut:
    return endpoint_out(_require_endpoint(db, partner.id, endpoint_id))


@router.patch(
    "/webhook_endpoints/{endpoint_id}",
    response_model=WebhookEndpointOut,
    operation_id="updateWebhookEndpoint",
    responses=_ERRORS,
    summary="Update a webhook endpoint",
)
def update_webhook_endpoint(
    endpoint_id: str,
    payload: WebhookEndpointUpdate,
    partner: PartnerDep,
    db: Db,
) -> WebhookEndpointOut:
    endpoint = _require_endpoint(db, partner.id, endpoint_id)
    if payload.url is not None:
        endpoint.url = validate_webhook_url(payload.url)
    if payload.events is not None:
        endpoint.events_json = canonical_json(validate_event_names(payload.events))
    if payload.status is not None:
        endpoint.status = payload.status
    endpoint.updated_at = isoformat()
    db.commit()
    return endpoint_out(endpoint)


@router.delete(
    "/webhook_endpoints/{endpoint_id}",
    response_model=WebhookEndpointOut,
    operation_id="disableWebhookEndpoint",
    responses=_ERRORS,
    summary="Disable a webhook endpoint",
)
def disable_webhook_endpoint(endpoint_id: str, partner: PartnerDep, db: Db) -> WebhookEndpointOut:
    endpoint = _require_endpoint(db, partner.id, endpoint_id)
    endpoint.status = "disabled"
    endpoint.updated_at = isoformat()
    db.commit()
    return endpoint_out(endpoint)


@router.post(
    "/webhook_endpoints/{endpoint_id}/ping",
    response_model=WebhookPingOut,
    operation_id="pingWebhookEndpoint",
    responses=_ERRORS,
    summary="Send a signed webhook.ping to one endpoint",
)
def ping_webhook_endpoint(endpoint_id: str, request: Request, partner: PartnerDep, db: Db) -> WebhookPingOut:
    endpoint = _require_endpoint(db, partner.id, endpoint_id)
    if endpoint.status != "active":
        raise APIError(409, "invalid_request_error", "endpoint_not_found", "Enable the webhook endpoint before sending a ping.")
    event = emit_event(
        db,
        partner_id=partner.id,
        event_type="webhook.ping",
        data={"ok": True, "endpoint_id": endpoint.id},
        only_endpoint_id=endpoint.id,
        ignore_subscription=True,
    )
    db.commit()
    delivery = db.scalars(select(WebhookDelivery).where(WebhookDelivery.event_id == event.id)).first()
    if delivery is None:
        raise APIError(500, "api_error", "delivery_not_found", "Ping delivery was not created.")
    result = attempt_delivery(db, delivery, request.app.state.transport, request.app.state.settings)
    db.refresh(delivery)
    return WebhookPingOut(
        event_id=event.id,
        delivery_id=delivery.id,
        body=delivery.body,
        signature=result.signature,
        delivery_status=delivery.status,
        response_status=result.response_status,
    )


@router.get(
    "/webhook_deliveries",
    response_model=WebhookDeliveryList,
    operation_id="listWebhookDeliveries",
    responses=_ERRORS,
    summary="List webhook deliveries",
)
def list_webhook_deliveries(
    partner: PartnerDep,
    db: Db,
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern=r"^(pending|in_flight|succeeded|failed|dead_letter)$"),
    endpoint_id: str | None = Query(default=None),
) -> WebhookDeliveryList:
    stmt = select(WebhookDelivery).where(WebhookDelivery.partner_id == partner.id)
    if status:
        stmt = stmt.where(WebhookDelivery.status == status)
    if endpoint_id:
        stmt = stmt.where(WebhookDelivery.endpoint_id == endpoint_id)
    rows, has_more, cursor = paginate(
        db, stmt, WebhookDelivery, partner_id=partner.id, starting_after=starting_after, limit=limit
    )
    return WebhookDeliveryList(data=[delivery_out(row) for row in rows], has_more=has_more, next_cursor=cursor)


@router.get(
    "/webhook_deliveries/{delivery_id}",
    response_model=WebhookDeliveryOut,
    operation_id="getWebhookDelivery",
    responses=_ERRORS,
    summary="Retrieve a webhook delivery",
)
def get_webhook_delivery(delivery_id: str, partner: PartnerDep, db: Db) -> WebhookDeliveryOut:
    return delivery_out(_require_delivery(db, partner.id, delivery_id))


@router.post(
    "/webhook_deliveries/{delivery_id}/replay",
    response_model=WebhookDeliveryOut,
    operation_id="replayWebhookDelivery",
    responses=_ERRORS,
    summary="Requeue a webhook delivery",
)
async def replay_webhook_delivery(
    delivery_id: str,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    state, replay = await _guard(request, db, partner, idempotency_key)
    if replay is not None:
        return replay
    try:
        delivery = _require_delivery(db, partner.id, delivery_id)
        replay_delivery(db, delivery)
        body = delivery_out(delivery).model_dump_json()
        complete_idempotency(db, state, 200, body)
        return Response(content=body, status_code=200, media_type="application/json")
    except APIError as exc:
        db.rollback()
        return _stored_error(request, db, state, exc)
