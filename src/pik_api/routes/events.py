"""Pollable event log and partner-originated events."""

from __future__ import annotations

import re

from fastapi import APIRouter, Header, Query, Request, Response
from sqlalchemy import select

from pik_api.auth import Db, PartnerDep
from pik_api.errors import APIError
from pik_api.idempotency import begin_idempotency, complete_idempotency
from pik_api.models import Event
from pik_api.pagination import paginate
from pik_api.schemas import EventCreate, EventList, EventOut
from pik_api.webhooks.delivery import emit_event, require_json_object
from pik_api.webhooks.urls import loads

router = APIRouter(prefix="/v1/events", tags=["Events"])

_ERRORS = {
    400: {"description": "Invalid request"},
    401: {"description": "Authentication failed"},
    404: {"description": "Event not found"},
    409: {"description": "Conflict"},
    429: {"description": "Rate limit exceeded"},
}
_RESERVED = ("order.", "webhook.")
_FILTER = re.compile(r"^[a-z][a-z0-9_.]{1,63}$")


def event_out(event: Event) -> EventOut:
    return EventOut(id=event.id, type=event.type, data=loads(event.data_json), created_at=event.created_at)


@router.post("", response_model=EventOut, status_code=201, operation_id="createEvent", responses=_ERRORS, summary="Publish a partner event")
async def create_event(
    payload: EventCreate,
    request: Request,
    partner: PartnerDep,
    db: Db,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> Response:
    raw = await request.body()
    state = begin_idempotency(
        db,
        partner_id=partner.id,
        method=request.method,
        path=request.url.path,
        body=raw,
        key=idempotency_key,
    )
    if state.replay:
        return Response(
            content=state.replay_body,
            status_code=state.replay_status or 201,
            media_type="application/json",
            headers={"Idempotent-Replayed": "true"},
        )
    try:
        if payload.type.startswith(_RESERVED):
            raise APIError(
                400,
                "invalid_request_error",
                "reserved_event_type",
                "Event types starting with order. or webhook. are reserved for the platform.",
            )
        require_json_object(payload.data)
        event = emit_event(db, partner_id=partner.id, event_type=payload.type, data=payload.data)
        db.commit()
        body = event_out(event).model_dump_json()
        complete_idempotency(db, state, 201, body)
        return Response(content=body, status_code=201, media_type="application/json")
    except APIError as exc:
        db.rollback()
        base = request.app.state.settings.docs_base
        body = exc.json(base, getattr(request.state, "request_id", None))
        complete_idempotency(db, state, exc.status, body)
        return Response(content=body, status_code=exc.status, media_type="application/json", headers=exc.headers)


@router.get("", response_model=EventList, operation_id="listEvents", responses=_ERRORS, summary="List events")
def list_events(
    partner: PartnerDep,
    db: Db,
    limit: int = Query(default=20, ge=1, le=100),
    starting_after: str | None = Query(default=None),
    type: str | None = Query(default=None),
) -> EventList:
    if type is not None and not _FILTER.match(type):
        raise APIError(
            400,
            "invalid_request_error",
            "invalid_event_type",
            "Event type filters must be lowercase names such as order.created.",
        )
    stmt = select(Event).where(Event.partner_id == partner.id)
    if type:
        stmt = stmt.where(Event.type == type)
    rows, has_more, cursor = paginate(db, stmt, Event, partner_id=partner.id, starting_after=starting_after, limit=limit)
    return EventList(data=[event_out(row) for row in rows], has_more=has_more, next_cursor=cursor)


@router.get("/{event_id}", response_model=EventOut, operation_id="getEvent", responses=_ERRORS, summary="Retrieve an event")
def get_event(event_id: str, partner: PartnerDep, db: Db) -> EventOut:
    event = db.get(Event, event_id)
    if event is None or event.partner_id != partner.id:
        raise APIError(404, "invalid_request_error", "event_not_found", "No such event.")
    return event_out(event)
