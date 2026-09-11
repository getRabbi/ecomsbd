"""Sentry integration.

Master spec section 49 requires Sentry on both mobile and backend. Two settings
here are not defaults and are chosen deliberately:

*   ``send_default_pii=False`` — this system holds customer phone numbers and
    courier credentials. Section 33 forbids logging them, and an error report is
    a log.
*   a ``before_send`` scrubber — Sentry captures local variables and request
    data, which is exactly where an OTP or an API key would otherwise leak. The
    same redaction used for logs is applied to the event.

Sentry is optional: with no DSN the application runs normally.
"""

from __future__ import annotations

from typing import Any

from app import __version__
from app.core.config import Settings
from app.core.context import current_context
from app.core.logging import get_logger
from app.core.redaction import redact_value

__all__ = ["init_sentry"]

log = get_logger(__name__)


def _scrub(event: dict[str, Any], _hint: dict[str, Any]) -> dict[str, Any] | None:
    """Redact an outgoing Sentry event and tag it with the trace context."""
    for section in ("request", "extra", "contexts", "breadcrumbs"):
        if section in event:
            event[section] = redact_value(event[section])

    context = current_context()
    tags = event.setdefault("tags", {})
    tags["trace_id"] = context.trace_id
    if context.tenant_id:
        tags["tenant_id"] = str(context.tenant_id)
    return event


def init_sentry(settings: Settings) -> bool:
    """Initialise Sentry if a DSN is configured. Returns whether it was enabled."""
    if settings.sentry_dsn is None:
        log.info("sentry: not configured")
        return False

    try:
        import sentry_sdk
        from sentry_sdk.integrations.asyncio import AsyncioIntegration
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.sqlalchemy import SqlalchemyIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration
    except ImportError:  # pragma: no cover - sentry is an optional runtime dep
        log.warning("sentry: SDK not installed; error reporting disabled")
        return False

    sentry_sdk.init(
        dsn=settings.sentry_dsn.get_secret_value(),
        environment=settings.sentry_environment or str(settings.app_env),
        # Without a release every regression looks like it has always been
        # there, and "did the deploy cause this?" stops being answerable.
        release=settings.sentry_release or f"ecomsbd-backend@{__version__}",
        traces_sample_rate=settings.sentry_traces_sample_rate,
        # This application handles phone numbers and courier credentials.
        send_default_pii=False,
        # Sentry types this hook against its own Event alias; the runtime
        # object is a plain dict, which is what _scrub walks.
        before_send=_scrub,  # type: ignore[arg-type]
        integrations=[
            StarletteIntegration(),
            FastApiIntegration(),
            SqlalchemyIntegration(),
            AsyncioIntegration(),
        ],
    )
    log.info("sentry: enabled", extra={"environment": str(settings.app_env)})
    return True
