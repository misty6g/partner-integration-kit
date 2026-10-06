"""FastAPI application factory."""

from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from pik_api import __version__
from pik_api.auth import get_db
from pik_api.config import Settings, load_settings
from pik_api.db import init_database
from pik_api.errors import APIError
from pik_api.ids import new_id
from pik_api.rate_limit import build_limiter
from pik_api.routes import account_router, events_router, orders_router, webhooks_router
from pik_api.schemas import HealthOut, ServiceOut
from pik_api.seed import seed_demo_partners
from pik_api.webhooks.delivery import HttpxTransport

_REQUEST_ID = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
log = logging.getLogger("pik.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO))
    engine, session_factory = init_database(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if app.state.settings.seed_demo:
            db = app.state.session_factory()
            try:
                seed_demo_partners(db)
            finally:
                db.close()
        yield
        app.state.engine.dispose()

    app = FastAPI(
        title="Partner Integration Kit API",
        version=__version__,
        summary="Orders, events, and signed webhooks for partner integrations.",
        description=(
            "HTTP API for a partner integration. Authenticate with an API key, "
            "create orders, poll the event log, and register webhook endpoints that "
            "receive HMAC-SHA256 signed deliveries."
        ),
        servers=[{"url": "http://127.0.0.1:8400", "description": "Local"}],
        openapi_tags=[
            {"name": "Account", "description": "Identify the caller."},
            {"name": "Orders", "description": "Create and transition orders."},
            {"name": "Events", "description": "Poll platform events or publish your own."},
            {"name": "Webhooks", "description": "Register endpoints and inspect deliveries."},
        ],
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.limiter = build_limiter(settings.redis_url)
    app.state.transport = HttpxTransport()

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        incoming = request.headers.get("X-Request-Id", "")
        request.state.request_id = incoming if _REQUEST_ID.match(incoming) else new_id("req")
        response = await call_next(request)
        response.headers["X-Request-Id"] = request.state.request_id
        decision = getattr(request.state, "rate_limit", None)
        if decision is not None:
            response.headers["X-RateLimit-Limit"] = str(decision.limit)
            response.headers["X-RateLimit-Remaining"] = str(decision.remaining)
            response.headers["X-RateLimit-Reset"] = str(decision.reset_epoch)
        return response

    @app.exception_handler(APIError)
    async def api_error(request: Request, exc: APIError) -> JSONResponse:
        payload = exc.as_dict(request.app.state.settings.docs_base, getattr(request.state, "request_id", None))
        return JSONResponse(status_code=exc.status, content=payload, headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for item in exc.errors():
            details.append(
                {
                    "loc": [str(part) for part in item.get("loc", [])],
                    "msg": item.get("msg", "Invalid value"),
                    "type": item.get("type", "value_error"),
                }
            )
        error = APIError(
            400,
            "invalid_request_error",
            "validation_error",
            "Request validation failed.",
            details=details,
        )
        payload = error.as_dict(request.app.state.settings.docs_base, getattr(request.state, "request_id", None))
        return JSONResponse(status_code=400, content=payload)

    @app.get("/", response_model=ServiceOut, include_in_schema=False)
    def root() -> ServiceOut:
        return ServiceOut(service="partner-integration-kit", version=__version__, docs="/docs", health="/v1/health")

    @app.get("/v1/health", response_model=HealthOut, tags=["Account"], operation_id="getHealth", summary="Liveness")
    def health() -> HealthOut:
        return HealthOut(status="ok")

    @app.get("/v1/ready", response_model=HealthOut, tags=["Account"], operation_id="getReady", summary="Database readiness")
    def ready(request: Request) -> HealthOut:
        db = request.app.state.session_factory()
        try:
            db.connection()
        finally:
            db.close()
        return HealthOut(status="ok")

    app.include_router(account_router)
    app.include_router(orders_router)
    app.include_router(events_router)
    app.include_router(webhooks_router)

    # Referenced so the dependency module stays imported for tests that patch it.
    _ = get_db
    return app
