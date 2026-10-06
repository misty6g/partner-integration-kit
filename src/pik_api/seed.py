"""Create partner rows and the local demo accounts."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from pik_api.auth import hash_api_key
from pik_api.ids import isoformat, new_id
from pik_api.models import ApiKey, Partner

DEMO_PARTNERS: tuple[tuple[str, str], ...] = (
    ("Acme Robotics", "pk_test_acme_7f3a9c2e1b84d0"),
    ("Northwind Outdoors", "pk_test_northwind_b41d8e0a6c"),
)


def create_partner_with_key(db: Session, name: str, raw_key: str) -> tuple[Partner, ApiKey]:
    partner = Partner(id=new_id("prt"), name=name, created_at=isoformat())
    api_key = ApiKey(
        id=new_id("key"),
        partner_id=partner.id,
        prefix=raw_key[:16],
        key_hash=hash_api_key(raw_key),
        created_at=isoformat(),
        revoked_at=None,
    )
    db.add(partner)
    db.add(api_key)
    return partner, api_key


def seed_demo_partners(db: Session) -> None:
    for name, raw_key in DEMO_PARTNERS:
        digest = hash_api_key(raw_key)
        existing = db.scalars(select(ApiKey).where(ApiKey.key_hash == digest)).first()
        if existing is None:
            create_partner_with_key(db, name, raw_key)
    db.commit()
