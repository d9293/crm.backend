"""FastAPI application.

BE-00 scope: the application boots, connects to PostgreSQL, reports health and
readiness, and fails in the contract's error shape. Domain routers are added
by their sprints.

The OpenAPI document served here is generated from the code. `api/openapi.yaml`
is the agreed contract and remains the source of truth until the
implementation catches up to it; CI checks that generation works, and BE-00's
successor tickets reconcile the two.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from app.config import get_settings
from app.db import get_engine
from app.errors import CORRELATION_HEADER, correlation_id_for, register_error_handlers

logger = logging.getLogger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Verify configuration at boot rather than on the first request.

    A missing database password or an unreachable server should stop the
    container from reporting healthy, so an orchestrator notices immediately
    instead of a user discovering it.
    """
    settings = get_settings()
    logger.info("Starting CRM backend", extra={"environment": settings.environment})
    yield
    logger.info("Shutting down CRM backend")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ACE / CSKA CRM API",
        version="0.1.0",
        docs_url="/docs" if settings.environment != "production" else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.environment != "production" else None,
        lifespan=lifespan,
    )

    # Credentialed CORS with an explicit origin list. A wildcard is both
    # rejected by browsers when credentials are sent and unsafe, so the
    # configuration has no way to express one.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["Content-Type", "If-Match", "Idempotency-Key", "X-CSRF-Token"],
        expose_headers=["ETag", CORRELATION_HEADER],
    )

    @app.middleware("http")
    async def attach_correlation_id(request: Request, call_next):  # type: ignore[no-untyped-def]
        """One id per request, echoed on every response.

        Set before routing so an error raised anywhere downstream — including
        an unhandled one — reports the same id the client can quote.
        """
        correlation_id = correlation_id_for(request)
        response: Response = await call_next(request)
        response.headers[CORRELATION_HEADER] = correlation_id
        return response

    register_error_handlers(app)
    app.include_router(build_health_router())

    return app


def build_health_router() -> APIRouter:
    """Liveness and readiness, deliberately distinguished.

    `/health` answers "is this process alive" and must not touch the database:
    an orchestrator uses it to decide whether to restart the container, and
    restarting the API because PostgreSQL is briefly unavailable turns a
    database blip into an outage.

    `/ready` answers "can this process serve traffic" and does check the
    database, because a backend that cannot reach PostgreSQL has nothing
    useful to offer.
    """
    router = APIRouter(tags=["Operations"])

    @router.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @router.get("/ready", include_in_schema=False)
    async def ready() -> Response:
        from fastapi.responses import JSONResponse

        try:
            with get_engine().connect() as connection:
                connection.execute(text("SELECT 1"))
        except Exception:
            # Logged without the connection URL, which contains the password.
            logger.exception("Readiness check failed: database unreachable")
            return JSONResponse(
                status_code=503,
                content={"status": "unavailable", "database": "unreachable"},
            )

        return JSONResponse(status_code=200, content={"status": "ready", "database": "ok"})

    return router


app = create_app()
