"""API-key authentication."""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import APIKeyHeader
from sqlalchemy import select
from sqlalchemy.orm import Session

from pik_api.errors import APIError
from pik_api.models import ApiKey, Partner

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False, scheme_name="ApiKeyAuth", description="Partner secret key.")


def hash_api_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def keys_match(raw_key: str, key_hash: str) -> bool:
    return hmac.compare_digest(hash_api_key(raw_key), key_hash)


def get_db(request: Request) -> Iterator[Session]:
    db = request.app.state.session_factory()
    try:
        yield db
    finally:
        db.close()


Db = Annotated[Session, Depends(get_db)]


def get_partner(
    request: Request,
    db: Db,
    raw_key: Annotated[str | None, Depends(api_key_header)] = None,
) -> Partner:
    if not raw_key:
        raise APIError(
            401,
            "authentication_error",
            "missing_api_key",
            "Send the partner API key in the X-API-Key header.",
        )
    digest = hash_api_key(raw_key)
    record = db.scalars(select(ApiKey).where(ApiKey.key_hash == digest)).first()
    if record is None or not keys_match(raw_key, record.key_hash):
        raise APIError(
            401,
            "authentication_error",
            "invalid_api_key",
            "The API key was not recognized.",
        )
    if record.revoked_at is not None:
        raise APIError(
            401,
            "authentication_error",
            "revoked_api_key",
            "This API key has been revoked. Issue a new key for the partner.",
        )
    partner = db.get(Partner, record.partner_id)
    if partner is None:
        raise APIError(401, "authentication_error", "invalid_api_key", "The API key was not recognized.")
    request.state.api_key = record
    request.state.partner = partner
    limiter = request.app.state.limiter
    settings = request.app.state.settings
    decision = limiter.hit(
        db=db,
        partner_id=partner.id,
        limit=settings.rate_limit,
        window_seconds=settings.rate_window_seconds,
    )
    request.state.rate_limit = decision
    if not decision.allowed:
        raise APIError(
            429,
            "rate_limit_error",
            "rate_limit_exceeded",
            f"Too many requests. Retry after {decision.retry_after} seconds.",
            headers={"Retry-After": str(decision.retry_after)},
        )
    return partner


PartnerDep = Annotated[Partner, Depends(get_partner)]
