"""Partner account."""

from __future__ import annotations

from fastapi import APIRouter, Request

from pik_api.auth import PartnerDep
from pik_api.schemas import AccountOut

router = APIRouter(prefix="/v1", tags=["Account"])


@router.get("/account", response_model=AccountOut, operation_id="getAccount", summary="Identify the authenticated partner")
def get_account(request: Request, partner: PartnerDep) -> AccountOut:
    settings = request.app.state.settings
    key = request.state.api_key
    return AccountOut(
        id=partner.id,
        name=partner.name,
        api_key_prefix=key.prefix,
        created_at=partner.created_at,
        rate_limit=settings.rate_limit,
        rate_window_seconds=settings.rate_window_seconds,
    )
