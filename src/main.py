"""Kabil.ai backend — FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.exception_handlers import install_exception_handlers
from src.api.routes import applications as applications_routes
from src.api.routes import auth as auth_routes
from src.api.routes import calendly_webhook as calendly_webhook_routes
from src.api.routes import dashboard as dashboard_routes
from src.api.routes import events as events_routes
from src.api.routes import jobs as jobs_routes
from src.api.routes import public as public_routes
from src.api.routes import talent_pool as talent_pool_routes
from src.api.routes import whatsapp_webhook as whatsapp_webhook_routes
from src.config import Settings, get_settings
from src.logging_config import configure_logging
from src.utils.correlation_id import CorrelationIdMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan hook — startup / shutdown work lives here."""
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the FastAPI application.

    A factory keeps tests able to inject overridden settings cleanly.
    """
    cfg = settings or get_settings()
    configure_logging(cfg)

    app = FastAPI(
        title="Kabil.ai Backend",
        version=cfg.app_version,
        lifespan=lifespan,
    )
    app.state.settings = cfg

    # Middleware execution order in Starlette is LIFO: the last middleware
    # added runs first. CorrelationIdMiddleware is added last so it wraps
    # CORS, guaranteeing the X-Correlation-ID header is set on every
    # response (including CORS preflights).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Correlation-ID"],
        expose_headers=["X-Correlation-ID"],
    )
    app.add_middleware(CorrelationIdMiddleware)

    install_exception_handlers(app, cfg)

    app.include_router(auth_routes.router)
    app.include_router(dashboard_routes.router)
    app.include_router(events_routes.router)
    app.include_router(jobs_routes.router)
    app.include_router(applications_routes.jobs_applications_router)
    app.include_router(applications_routes.applications_router)
    app.include_router(public_routes.router)
    app.include_router(talent_pool_routes.router)
    app.include_router(whatsapp_webhook_routes.router)
    app.include_router(calendly_webhook_routes.router)

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": cfg.app_version}

    return app


app = create_app()
