"""Pydantic request and response models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class APIModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CustomerIn(APIModel):
    name: str = Field(min_length=1, max_length=200, examples=["Ada Lovelace"])
    email: str = Field(
        pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        max_length=320,
        examples=["ada@analytical.example"],
    )


class CustomerOut(APIModel):
    name: str
    email: str


class OrderItemIn(APIModel):
    sku: str = Field(min_length=1, max_length=64, examples=["WIDGET-1"])
    quantity: int = Field(ge=1, le=10_000, examples=[2])
    unit_amount: int = Field(ge=0, le=100_000_000, description="Unit price in minor currency units.", examples=[2500])


class OrderItemOut(APIModel):
    sku: str
    quantity: int
    unit_amount: int


class OrderCreate(APIModel):
    external_id: str | None = Field(default=None, max_length=128, examples=["ord_partner_1001"])
    currency: str = Field(pattern=r"^[a-z]{3}$", examples=["usd"])
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)
    customer: CustomerIn | None = None


class OrderOut(APIModel):
    id: str
    external_id: str | None
    status: str
    currency: str
    amount: int
    items: list[OrderItemOut]
    customer: CustomerOut | None
    created_at: str
    updated_at: str


class OrderList(APIModel):
    data: list[OrderOut]
    has_more: bool
    next_cursor: str | None = None


class EventCreate(APIModel):
    type: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$", examples=["inventory.updated"])
    data: dict[str, Any]


class EventOut(APIModel):
    id: str
    type: str
    data: dict[str, Any]
    created_at: str


class EventList(APIModel):
    data: list[EventOut]
    has_more: bool
    next_cursor: str | None = None


class WebhookEndpointCreate(APIModel):
    url: str = Field(min_length=8, max_length=500, examples=["https://partner.example/webhooks/pik"])
    events: list[str] = Field(min_length=1, max_length=30, examples=[["order.created", "order.cancelled"]])


class WebhookEndpointUpdate(APIModel):
    url: str | None = Field(default=None, min_length=8, max_length=500)
    events: list[str] | None = Field(default=None, min_length=1, max_length=30)
    status: str | None = Field(default=None, pattern=r"^(active|disabled)$")


class WebhookEndpointOut(APIModel):
    id: str
    url: str
    events: list[str]
    status: str
    secret_hint: str
    created_at: str
    updated_at: str


class WebhookEndpointCreated(WebhookEndpointOut):
    secret: str


class WebhookEndpointList(APIModel):
    data: list[WebhookEndpointOut]
    has_more: bool
    next_cursor: str | None = None


class WebhookDeliveryOut(APIModel):
    id: str
    endpoint_id: str
    event_id: str
    event_type: str
    status: str
    attempt_count: int
    next_attempt_at: str
    last_response_status: int | None
    last_error: str | None
    created_at: str
    updated_at: str
    delivered_at: str | None


class WebhookDeliveryList(APIModel):
    data: list[WebhookDeliveryOut]
    has_more: bool
    next_cursor: str | None = None


class WebhookPingOut(APIModel):
    event_id: str
    delivery_id: str
    body: str
    signature: str
    delivery_status: str
    response_status: int | None = None


class AccountOut(APIModel):
    id: str
    name: str
    api_key_prefix: str
    created_at: str
    rate_limit: int
    rate_window_seconds: int


class HealthOut(APIModel):
    status: str


class ServiceOut(APIModel):
    service: str
    version: str
    docs: str
    health: str


class ErrorBody(APIModel):
    type: str
    code: str
    message: str
    doc_url: str
    request_id: str | None = None
    details: Any = None


class ErrorResponse(APIModel):
    error: ErrorBody
