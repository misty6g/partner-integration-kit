"""Order resource."""

from __future__ import annotations

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from pik_api.auth import Db, PartnerDep
from pik_api.errors import APIError
from pik_api.idempotency import IdempotencyState, begin_idempotency, complete_idempotency
from pik_api.ids import isoformat, new_id
from pik_api.models import Order, OrderItem, Partner
from pik_api.pagination import paginate
from pik_api.schemas import OrderCreate, OrderItemOut, OrderList, OrderOut
from pik_api.webhooks.delivery import emit_event

router = APIRouter(prefix="/v1/orders", tags=["Orders"])

_ERRORS = {
    400: {"description": "Invalid request"},
    401: {"description": "Authentication failed"},
    404: {"description": "Order not found"},
    409: {"description": "Conflict"},
    429: {"description": "Rate limit exceeded"},
}


def order_out(order: Order) -> OrderOut:
    customer = None
    if order.customer_email and order.customer_name:
        customer = {"name": order.customer_name, "email": order.customer_email}
    return OrderOut(
        id=order.id,
        external_id=order.external_id,
        status=order.status,
        currency=order.currency,
        amount=order.amount,
        items=[OrderItemOut(sku=item.sku, quantity=item.quantity, unit_amount=item.unit_amount) for item in order.items],
        customer=customer,
        created_at=order.created_at,
        updated_at=order.updated_at,
    )


def order_resource(order: Order) -> dict:
    return order_out(order).model_dump(mode="json")


def _load_order(db: Session, partner_id: str, order_id: str) -> Order:
    order = db.scalars(
        select(Order).where(Order.id == order_id).options(selectinload(Order.items))
    ).first()
    if order is None or order.partner_id != partner_id:
        raise APIError(404, "invalid_request_error", "order_not_found", "No such order.")
    return order


async def _idem(request: Request, db: Session, partner: Partner, key: str | None) -> IdempotencyState:
    raw = await request.body()
    return begin_idempotency(
        db,
        partner_id=partner.id,
        method=request.method,
        path=request.url.path,
        body=raw,
        key=key,
    )


def _finish(db: Session, state: IdempotencyState, status: int, body: str, headers: dict | None = None) -> Response:
    complete_idempotency(db, state, status, body)
    response_headers = {"content-type": "application/json"}
    if state.replay:
        response_headers["Idempotent-Replayed"] = "true"
    if headers:
        response_headers.update(headers)
    return Response(content=body, status_code=status, headers=response_headers, media_type="application/json")


def _error_response(request: Request, db: Session, state: IdempotencyState, exc: APIError) -> Response:
    base = request.app.state.settings.docs_base
    request_id = getattr(request.state, "request_id", None)
    body = exc.json(base, request_id)
    return _finish(db, state, exc.status, body, exc.headers)


@router.post("", response_model=OrderOut, status_code=201, operation_id="createOrder", responses=_ERRORS, summary="Create an order")
async def create_order(
    payload: OrderCreate,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    state = await _idem(request, db, partner, idempotency_key)
    if state.replay:
        return _finish(db, state, state.replay_status or 201, state.replay_body or "")
    try:
        moment = isoformat()
        amount = sum(item.quantity * item.unit_amount for item in payload.items)
        order = Order(
            id=new_id("ord"),
            partner_id=partner.id,
            external_id=payload.external_id,
            status="open",
            currency=payload.currency,
            amount=amount,
            customer_name=payload.customer.name if payload.customer else None,
            customer_email=payload.customer.email if payload.customer else None,
            created_at=moment,
            updated_at=moment,
        )
        for position, item in enumerate(payload.items):
            order.items.append(
                OrderItem(
                    id=new_id("itm"),
                    order_id=order.id,
                    position=position,
                    sku=item.sku,
                    quantity=item.quantity,
                    unit_amount=item.unit_amount,
                )
            )
        db.add(order)
        db.flush()
        emit_event(db, partner_id=partner.id, event_type="order.created", data=order_resource(order))
        db.commit()
        db.refresh(order)
        body = order_out(order).model_dump_json()
        return _finish(db, state, 201, body)
    except IntegrityError:
        db.rollback()
        exc = APIError(
            409,
            "invalid_request_error",
            "duplicate_external_id",
            "An order with this external_id already exists for the partner.",
        )
        return _error_response(request, db, state, exc)
    except APIError as exc:
        db.rollback()
        return _error_response(request, db, state, exc)


@router.get("", response_model=OrderList, operation_id="listOrders", responses=_ERRORS, summary="List orders")
def list_orders(
    partner: PartnerDep,
    db: Db,
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: str | None = Query(default=None),
    status: str | None = Query(default=None, pattern=r"^(open|fulfilled|cancelled)$"),
) -> OrderList:
    stmt = select(Order).where(Order.partner_id == partner.id).options(selectinload(Order.items))
    if status:
        stmt = stmt.where(Order.status == status)
    rows, has_more, cursor = paginate(
        db, stmt, Order, partner_id=partner.id, starting_after=starting_after, limit=limit
    )
    return OrderList(data=[order_out(row) for row in rows], has_more=has_more, next_cursor=cursor)


@router.get("/{order_id}", response_model=OrderOut, operation_id="getOrder", responses=_ERRORS, summary="Retrieve an order")
def get_order(order_id: str, partner: PartnerDep, db: Db) -> OrderOut:
    return order_out(_load_order(db, partner.id, order_id))


@router.post("/{order_id}/cancel", response_model=OrderOut, operation_id="cancelOrder", responses=_ERRORS, summary="Cancel an open order")
async def cancel_order(
    order_id: str,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    return await _transition(request, partner, db, order_id, "cancelled", "order.cancelled", idempotency_key)


@router.post("/{order_id}/fulfill", response_model=OrderOut, operation_id="fulfillOrder", responses=_ERRORS, summary="Fulfill an open order")
async def fulfill_order(
    order_id: str,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    return await _transition(request, partner, db, order_id, "fulfilled", "order.fulfilled", idempotency_key)


async def _transition(
    request: Request,
    partner: Partner,
    db: Session,
    order_id: str,
    status: str,
    event_type: str,
    idempotency_key: str | None,
) -> Response:
    state = await _idem(request, db, partner, idempotency_key)
    if state.replay:
        return _finish(db, state, state.replay_status or 200, state.replay_body or "")
    try:
        order = _load_order(db, partner.id, order_id)
        if order.status != status:
            if order.status != "open":
                raise APIError(
                    409,
                    "invalid_request_error",
                    "invalid_order_transition",
                    f"Cannot change an order from {order.status} to {status}.",
                )
            order.status = status
            order.updated_at = isoformat()
            emit_event(db, partner_id=partner.id, event_type=event_type, data=order_resource(order))
        db.commit()
        db.refresh(order)
        body = order_out(order).model_dump_json()
        return _finish(db, state, 200, body)
    except APIError as exc:
        db.rollback()
        return _error_response(request, db, state, exc)
