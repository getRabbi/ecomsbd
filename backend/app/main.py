"""FastAPI application factory.

A modular monolith, not microservices (master spec section 30). Startup order
matters: logging first so failures are readable, then Sentry, then the model
registry and tenancy guards, and only then the routers.

Installing the tenancy guards during application startup — rather than lazily on
first query — means a request can never be served by a session whose isolation
hooks are not attached yet.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app import __version__

# Imported for its side effect: configuring every mapper at import time, so a
# broken relationship fails at startup rather than on a seller's first request.
from app import models as _models  # noqa: F401
from app.api.auth_links import router as auth_links_router
from app.api.errors import install_exception_handlers
from app.api.health import router as health_router
from app.api.middleware import BodySizeLimitMiddleware, RequestContextMiddleware
from app.api.v1.router import api_router
from app.core.config import AppEnv, Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.core.observability import init_sentry
from app.db.session import dispose_engine, get_engine
from app.db.tenancy import install_tenancy_guards

log = get_logger("app.main")

DESCRIPTION = """
**ecomsbd** — Bangladesh-first F-commerce seller financial operations.

Order to courier to delivery outcome to COD receivable to payout to
reconciliation to profit. Money is always integer paisa; every business record
is tenant-scoped; no external courier action is ever retried while its outcome
is ambiguous.
""".strip()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings

    install_tenancy_guards()
    get_engine(settings)
    if settings.push_transport == "fcm":
        from app.notifications.transport import FcmPushTransport

        app.state.push_transport = FcmPushTransport(settings)

    log.info(
        "ecomsbd backend starting",
        extra={
            "version": __version__,
            "environment": str(settings.app_env),
            "dev_otp_enabled": settings.dev_otp_enabled,
            # Which sign-in methods are on, and which of those a seller can
            # actually use. The two differ whenever a method is configured
            # ahead of its implementation, and that gap belongs in the first
            # line of the log rather than in a support ticket.
            "auth_methods_enabled": sorted(settings.enabled_auth_methods),
            "auth_methods_available": sorted(settings.available_auth_methods),
        },
    )
    if settings.dev_otp_enabled:
        log.warning(
            "DEVELOPMENT OTP PROVIDER IS ACTIVE. No SMS is sent and codes are "
            "written to the log. This configuration cannot start in production."
        )
    if settings.enabled_auth_methods - settings.available_auth_methods:
        log.warning(
            "Sign-in methods are enabled that have no implementation in this "
            "build and will not work: %s",
            sorted(settings.enabled_auth_methods - settings.available_auth_methods),
        )

    try:
        yield
    finally:
        from app.common.cache import close_cache

        await close_cache()
        await dispose_engine()
        log.info("ecomsbd backend stopped")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the application."""
    settings = settings or get_settings()

    configure_logging(level=settings.log_level, json_output=settings.log_json)
    init_sentry(settings)

    app = FastAPI(
        title="ecomsbd API",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        # Interactive docs are useful locally and are noise-plus-surface-area in
        # production, where the OpenAPI schema is published from CI instead.
        docs_url=None if settings.app_env is AppEnv.PRODUCTION else "/docs",
        redoc_url=None,
        openapi_url=None if settings.app_env is AppEnv.PRODUCTION else "/openapi.json",
    )
    app.state.settings = settings

    # Middleware is applied bottom-up, so the context middleware added last runs
    # first and every other layer sees a trace id.
    if settings.cors_allow_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_allow_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
            expose_headers=["x-trace-id", "x-request-id"],
        )
    # Host header allow-list. Empty means "something in front of this already
    # enforces one" — correct behind a proxy that terminates a known domain,
    # and wrong the moment the app is reachable directly, which is why
    # production refuses the wildcard rather than the empty list.
    if settings.trusted_hosts:
        app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.request_body_limit_bytes)
    app.add_middleware(RequestContextMiddleware, settings=settings)

    install_exception_handlers(app)

    app.include_router(health_router)
    if not settings.supabase_auth_active:
        app.include_router(auth_links_router)
    app.include_router(api_router, prefix=settings.api_v1_prefix)

    return app


app = create_app()
