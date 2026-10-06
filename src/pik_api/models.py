"""Relational model for partners, orders, events, and webhook deliveries."""

from __future__ import annotations

from sqlalchemy import ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class Partner(Base):
    __tablename__ = "partners"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40))

    api_keys: Mapped[list[ApiKey]] = relationship(back_populates="partner")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    prefix: Mapped[str] = mapped_column(String(24))
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[str] = mapped_column(String(40))
    revoked_at: Mapped[str | None] = mapped_column(String(40), nullable=True)

    partner: Mapped[Partner] = relationship(back_populates="api_keys")


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        UniqueConstraint("partner_id", "external_id", name="uq_orders_partner_external"),
        Index("ix_orders_partner_created", "partner_id", "created_at", "id"),
    )

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    external_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    amount: Mapped[int] = mapped_column(Integer)
    customer_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    customer_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))

    items: Mapped[list[OrderItem]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        order_by="OrderItem.position",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    sku: Mapped[str] = mapped_column(String(64))
    quantity: Mapped[int] = mapped_column(Integer)
    unit_amount: Mapped[int] = mapped_column(Integer)

    order: Mapped[Order] = relationship(back_populates="items")


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_partner_created", "partner_id", "created_at", "id"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    type: Mapped[str] = mapped_column(String(80), index=True)
    data_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40))


class WebhookEndpoint(Base):
    __tablename__ = "webhook_endpoints"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    secret: Mapped[str] = mapped_column(String(120))
    events_json: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))


class WebhookDelivery(Base):
    __tablename__ = "webhook_deliveries"
    __table_args__ = (Index("ix_deliveries_due", "status", "next_attempt_at"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    endpoint_id: Mapped[str] = mapped_column(ForeignKey("webhook_endpoints.id"), index=True)
    event_id: Mapped[str] = mapped_column(ForeignKey("events.id"), index=True)
    event_type: Mapped[str] = mapped_column(String(80))
    body: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[str] = mapped_column(String(40))
    last_response_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    delivered_at: Mapped[str | None] = mapped_column(String(40), nullable=True)


class IdempotencyKey(Base):
    __tablename__ = "idempotency_keys"
    __table_args__ = (UniqueConstraint("partner_id", "key", name="uq_idempotency_partner_key"),)

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), index=True)
    key: Mapped[str] = mapped_column(String(200))
    method: Mapped[str] = mapped_column(String(10))
    path: Mapped[str] = mapped_column(String(200))
    request_hash: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(20))
    status_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    response_body: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40))


class RateWindow(Base):
    __tablename__ = "rate_windows"

    partner_id: Mapped[str] = mapped_column(ForeignKey("partners.id"), primary_key=True)
    window_start: Mapped[int] = mapped_column(Integer, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
