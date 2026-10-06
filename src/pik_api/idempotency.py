"""Idempotency-Key storage for unsafe requests."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from pik_api.errors import APIError
from pik_api.ids import isoformat, new_id
from pik_api.models import IdempotencyKey

_KEY = re.compile(r"^[A-Za-z0-9_.:-]{1,200}$")


@dataclass
class IdempotencyState:
    record_id: str | None = None
    replay_status: int | None = None
    replay_body: str | None = None

    @property
    def replay(self) -> bool:
        return self.replay_body is not None and self.replay_status is not None


def request_hash(method: str, path: str, body: bytes) -> str:
    raw = method.upper().encode("ascii") + b"\n" + path.encode("utf-8") + b"\n" + body
    return hashlib.sha256(raw).hexdigest()


def begin_idempotency(
    db: Session,
    *,
    partner_id: str,
    method: str,
    path: str,
    body: bytes,
    key: str | None,
) -> IdempotencyState:
    if key is None or key == "":
        return IdempotencyState()
    if not _KEY.match(key):
        raise APIError(
            400,
            "invalid_request_error",
            "invalid_idempotency_key",
            "Idempotency-Key must be 1-200 characters: letters, digits, underscore, hyphen, colon, or dot.",
        )
    digest = request_hash(method, path, body)
    existing = _find(db, partner_id, key)
    if existing is not None:
        return _from_existing(existing, digest)
    record = IdempotencyKey(
        id=new_id("idem"),
        partner_id=partner_id,
        key=key,
        method=method.upper(),
        path=path,
        request_hash=digest,
        state="processing",
        status_code=None,
        response_body=None,
        created_at=isoformat(),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        existing = _find(db, partner_id, key)
        if existing is None:
            raise
        return _from_existing(existing, digest)
    return IdempotencyState(record_id=record.id)


def complete_idempotency(db: Session, state: IdempotencyState, status_code: int, body: str) -> None:
    if state.record_id is None or state.replay:
        return
    record = db.get(IdempotencyKey, state.record_id)
    if record is None:
        return
    record.state = "complete"
    record.status_code = status_code
    record.response_body = body
    db.commit()


def _find(db: Session, partner_id: str, key: str) -> IdempotencyKey | None:
    return db.scalars(select(IdempotencyKey).where(IdempotencyKey.partner_id == partner_id, IdempotencyKey.key == key)).first()


def _from_existing(existing: IdempotencyKey, digest: str) -> IdempotencyState:
    if existing.request_hash != digest:
        raise APIError(
            409,
            "idempotency_error",
            "idempotency_key_conflict",
            "This Idempotency-Key was already used with a different request.",
        )
    if existing.state == "complete" and existing.response_body is not None and existing.status_code is not None:
        return IdempotencyState(replay_status=existing.status_code, replay_body=existing.response_body)
    raise APIError(
        409,
        "idempotency_error",
        "idempotency_in_progress",
        "A request with this Idempotency-Key is still in progress.",
    )
