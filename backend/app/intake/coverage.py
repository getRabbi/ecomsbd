"""Official Meta connections cover the matching phone apps (V4 Phase 6).

A shop whose Facebook Page (``MESSENGER``) or WhatsApp Cloud API number
(``WHATSAPP``) is connected already receives those chats through the Meta
webhook, into the same Intake service and draft table (rule L1). A linked phone
reading the same chats from its notifications would only make a second copy.
So while such a connection delivers, the server lists the matching apps in the
shop's ``shop_intake_settings.covered_packages``; devices drop them from
``enabled_packages`` on their next rules refresh (the rules ETag changes). When
the connection stops delivering — disconnected, paused, token expired, webhook
failing — the apps come back on the next refresh.

Only apps the official path really delivers are covered: a Page's inbox is
Meta Business Suite, a Cloud API number's chats are WhatsApp Business. The
personal Messenger and WhatsApp apps are never covered: sellers also sell from
personal accounts, and the webhook never sees those chats. Covering too little
costs a duplicate message that joins the open draft; covering too much loses
orders.

The launch gate (:func:`launch_open`) decides whether a seller sees Messenger /
WhatsApp at all instead of "Coming soon": the operator's
``META_APP_REVIEW_APPROVED`` attestation, the Meta app set up, the shop's
``meta_official_path_enabled`` flag, and a passing health check.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.feature_flags import FeatureFlagService, FlagKey
from app.core.clock import utc_now
from app.core.config import get_settings
from app.core.context import ActorType, RequestContext, clear_context, set_context
from app.core.logging import get_logger
from app.db.session import session_scope, system_session
from app.intake.rules import set_covered_packages, shop_settings
from app.integrations.models import IntegrationConnection

__all__ = [
    "COVERED_BY",
    "HEALTH_WINDOW",
    "META_PROVIDERS",
    "check_passed",
    "covered_packages_for",
    "launch_open",
    "refresh_coverage",
    "sweep_coverage",
]

log = get_logger(__name__)

#: The phone apps each official connection delivers.
COVERED_BY: dict[str, tuple[str, ...]] = {
    # A Facebook Page's inbox (the seller's personal Messenger is not it).
    "MESSENGER": ("com.facebook.pages.app",),
    # A WhatsApp Business number on the Cloud API (personal WhatsApp is not it).
    "WHATSAPP": ("com.whatsapp.w4b",),
}
META_PROVIDERS = frozenset(COVERED_BY)
#: A health check (or a processed webhook) younger than this counts as passing.
HEALTH_WINDOW = timedelta(hours=24)


def _delivering() -> list[sa.ColumnElement[bool]]:
    """A connection the webhook can deliver chats through right now."""
    return [
        IntegrationConnection.state == "CONNECTED",
        sa.or_(
            IntegrationConnection.webhook_state.is_(None),
            IntegrationConnection.webhook_state != "FAILING",
        ),
    ]


async def _flag_on(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    return await FeatureFlagService(db).is_enabled(
        FlagKey.META_OFFICIAL_PATH_ENABLED, tenant_id=tenant_id
    )


async def covered_packages_for(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """What the shop's covered apps should be, from its connections now."""
    if not await _flag_on(db, tenant_id):
        return []
    providers = (
        await db.scalars(
            sa.select(IntegrationConnection.provider).where(
                IntegrationConnection.tenant_id == tenant_id,
                IntegrationConnection.provider.in_(sorted(META_PROVIDERS)),
                *_delivering(),
            )
        )
    ).all()
    return sorted({package for provider in providers for package in COVERED_BY[provider]})


async def refresh_coverage(db: AsyncSession, tenant_id: uuid.UUID) -> list[str]:
    """Write the shop's covered apps if they changed; return them.

    Recomputed from the connections every time rather than added and removed
    per event, so a missed event is corrected by the next call (and by the
    hourly sweep). A shop with nothing to cover and no settings row gets none.
    """
    # Sessions do not autoflush: the connection's new state must reach the
    # query below, or the coverage is computed from the old one.
    await db.flush()
    wanted = await covered_packages_for(db, tenant_id)
    row = await shop_settings(db, tenant_id)
    current = sorted(row.covered_packages) if row is not None else []
    if current != wanted:
        await set_covered_packages(db, tenant_id, wanted)
        log.info(
            "intake coverage changed",
            extra={"tenant_id": str(tenant_id), "covered": wanted, "was": current},
        )
    return wanted


async def sweep_coverage() -> dict[str, int]:
    """Re-derive coverage for every shop that has (or had) a Meta connection.

    Catches the changes no hook sees at once: a token found expired while a
    webhook was processed or a message sent, a webhook subscription failing,
    the flag switched off.
    """
    async with system_session("intake: coverage sweep") as db:
        tenants = (
            await db.scalars(
                sa.select(IntegrationConnection.tenant_id)
                .where(IntegrationConnection.provider.in_(sorted(META_PROVIDERS)))
                .distinct()
            )
        ).all()
    counts = {"shops": 0, "failed": 0}
    for tenant_id in tenants:
        token = set_context(
            RequestContext(
                trace_id=uuid.uuid4().hex,
                tenant_id=tenant_id,
                actor_type=ActorType.SYSTEM,
                job_name="intake_coverage_sweep",
            )
        )
        try:
            async with session_scope() as db:
                await refresh_coverage(db, tenant_id)
            counts["shops"] += 1
        except Exception as exc:
            counts["failed"] += 1
            log.warning(
                "intake coverage sweep failed",
                extra={"tenant_id": str(tenant_id), "error": type(exc).__name__},
            )
        finally:
            clear_context(token)
    return counts


async def check_passed(provider: str) -> bool:
    """Whether the official path is proven to work for ``provider`` right now.

    True when at least one connection of that provider, on any shop (the
    operator connects a test Page / number first), is delivering and its last
    health check or processed webhook succeeded within :data:`HEALTH_WINDOW`
    with no error after it. Only a yes/no leaves this function; no other
    shop's data does.
    """
    since = utc_now() - HEALTH_WINDOW
    async with system_session("integrations: meta launch check") as db:
        found = await db.scalar(
            sa.select(IntegrationConnection.id)
            .where(
                IntegrationConnection.provider == provider,
                *_delivering(),
                IntegrationConnection.last_success_at >= since,
                sa.or_(
                    IntegrationConnection.last_error_at.is_(None),
                    IntegrationConnection.last_error_at < IntegrationConnection.last_success_at,
                ),
            )
            .limit(1)
        )
    return found is not None


async def launch_open(
    db: AsyncSession, tenant_id: uuid.UUID, provider: str, *, available: bool
) -> bool:
    """Whether this shop sees ``provider`` instead of "Coming soon".

    Every condition must hold, cheapest first: a Meta provider, the operator's
    App Review attestation, the Meta app set up (``available``), the shop's
    flag, and a passing health check.
    """
    if provider not in META_PROVIDERS:
        return False
    if not get_settings().meta_app_review_approved or not available:
        return False
    if not await _flag_on(db, tenant_id):
        return False
    return await check_passed(provider)
