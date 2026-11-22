"""Feature flags.

Master spec section 45: flags can be global, per tenant, or a percentage
rollout, and a provider outage must be survivable without shipping a new app
version.

Percentage rollout is deterministic — the same tenant always lands on the same
side of a given threshold — so a seller does not see a feature appear and vanish
between requests, and a shadow-mode financial comparison (section 112) stays
comparable across runs.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.core.clock import utc_now
from app.core.ids import new_id
from app.db.base import Base
from app.db.types import GUID, JSONColumn, TZDateTime

__all__ = ["FeatureFlag", "FeatureFlagService", "FlagKey", "FlagScope"]


class FlagScope(StrEnum):
    GLOBAL = "GLOBAL"
    TENANT = "TENANT"


class FlagKey(StrEnum):
    """Known flag keys (master spec section 45).

    Every provider integration gets a kill switch before it gets an
    implementation, so a broken provider can be disabled from the server.
    """

    STEADFAST_ENABLED = "steadfast_enabled"
    PATHAO_ENABLED = "pathao_enabled"
    REDX_ENABLED = "redx_enabled"
    #: V4 WP7.2: the Carrybee courier integration.
    CARRYBEE_ENABLED = "carrybee_enabled"
    PROVIDER_AUTO_ADDRESS = "provider_auto_address"
    RISK_AGGREGATOR_FALLBACK = "risk_aggregator_fallback"
    AI_PARSE_ENABLED = "ai_parse_enabled"
    BKASH_WEB_BILLING_ENABLED = "bkash_web_billing_enabled"
    PLAY_BILLING_ENABLED = "play_billing_enabled"
    COURIER_RECOMMENDATION_ENABLED = "courier_recommendation_enabled"
    RECONCILIATION_AUTO_MATCH = "reconciliation_auto_match"
    RECONCILIATION_SHADOW_MODE = "reconciliation_shadow_mode"
    #: V4 WP0.2: server-side funnel events (``product_events``).
    PRODUCT_EVENTS_ENABLED = "product_events_enabled"
    #: V4 WP0.4: shop delivery rules API and zone-aware COD on chat drafts.
    DELIVERY_RULES_ENABLED = "delivery_rules_enabled"
    #: V4 WP0.5: invoice and shipping-label HTML.
    ORDER_DOCUMENTS_ENABLED = "order_documents_enabled"
    #: V4 WP1.1+: linked phones capture orders (DEVICE connections, ingest keys).
    INTAKE_DEVICE_ENABLED = "intake_device_enabled"
    #: V4 WP2.1+: customer confirmation of orders (``order_confirmations``).
    CONFIRMATION_ENGINE_ENABLED = "confirmation_engine_enabled"
    #: V4 WP2.3: after the last unanswered call, SMS the customer a confirm link.
    CONFIRMATION_SMS_LINK_ENABLED = "confirmation_sms_link_enabled"
    #: V4 WP2.5: a seller pays by bKash / Nagad send-money and submits the TrxID.
    MANUAL_PAYMENTS_ENABLED = "manual_payments_enabled"
    #: V4 Phase 3: IVR confirmation calls (``VoiceTransport``).
    VOICE_CONFIRMATION_ENABLED = "voice_confirmation_enabled"
    #: V4 Phase 4: Autopilot turns ready chat drafts into orders. Off is the
    #: global kill switch; a shop's own row off is that shop's.
    AUTOPILOT_ENABLED = "autopilot_enabled"
    #: V4 Phase 5: the Chrome extension captures from Messenger / WhatsApp Web.
    EXTENSION_ENABLED = "extension_enabled"
    #: V4 Phase 6: Messenger / WhatsApp official connections cover the matching
    #: phone apps and may leave "Coming soon" (with META_APP_REVIEW_APPROVED).
    META_OFFICIAL_PATH_ENABLED = "meta_official_path_enabled"
    #: V4 WP7.4: shop referral codes and the 30-day credit for both shops
    #: after the referred shop's first approved TrxID payment.
    REFERRALS_ENABLED = "referrals_enabled"
    #: V4 WP7.1: the Alpha SMS gateway (sms.net.bd) sends customer SMS (the
    #: confirm link, tracking SMS) once its admin-console account is on too.
    ALPHA_SMS_ENABLED = "alpha_sms_enabled"


#: Which flag gates each courier provider. Manual mode has no flag: it must
#: never be switchable off, because it is the fallback every other provider
#: degrades to. Shared, because both the provider listing and the bookable-
#: courier check need it and two copies would drift.
COURIER_PROVIDER_FLAGS: dict[str, str] = {
    "steadfast": FlagKey.STEADFAST_ENABLED,
    "pathao": FlagKey.PATHAO_ENABLED,
    "redx": FlagKey.REDX_ENABLED,
    "carrybee": FlagKey.CARRYBEE_ENABLED,
}


#: Values used when a flag has no row yet. Everything provider-facing is off:
#: a feature that has not been verified against real documentation must not be
#: reachable just because its row is missing.
_DEFAULTS: dict[str, bool] = {
    FlagKey.STEADFAST_ENABLED: False,
    FlagKey.PATHAO_ENABLED: False,
    FlagKey.REDX_ENABLED: False,
    FlagKey.CARRYBEE_ENABLED: False,
    FlagKey.PROVIDER_AUTO_ADDRESS: True,
    FlagKey.RISK_AGGREGATOR_FALLBACK: False,
    FlagKey.AI_PARSE_ENABLED: False,
    FlagKey.BKASH_WEB_BILLING_ENABLED: False,
    FlagKey.PLAY_BILLING_ENABLED: False,
    FlagKey.COURIER_RECOMMENDATION_ENABLED: False,
    FlagKey.RECONCILIATION_AUTO_MATCH: False,
    FlagKey.RECONCILIATION_SHADOW_MODE: True,
    FlagKey.PRODUCT_EVENTS_ENABLED: False,
    FlagKey.DELIVERY_RULES_ENABLED: False,
    FlagKey.ORDER_DOCUMENTS_ENABLED: False,
    FlagKey.INTAKE_DEVICE_ENABLED: False,
    FlagKey.CONFIRMATION_ENGINE_ENABLED: False,
    FlagKey.CONFIRMATION_SMS_LINK_ENABLED: False,
    FlagKey.MANUAL_PAYMENTS_ENABLED: False,
    FlagKey.VOICE_CONFIRMATION_ENABLED: False,
    FlagKey.AUTOPILOT_ENABLED: False,
    FlagKey.EXTENSION_ENABLED: False,
    FlagKey.META_OFFICIAL_PATH_ENABLED: False,
    FlagKey.REFERRALS_ENABLED: False,
    FlagKey.ALPHA_SMS_ENABLED: False,
}


class FeatureFlag(Base):
    """A flag row. A tenant-scoped row overrides the global row for that tenant."""

    __tablename__ = "feature_flags"
    __table_args__ = (
        sa.UniqueConstraint("key", "tenant_id", name="uq_feature_flags_key_tenant_id"),
        sa.CheckConstraint(
            "rollout_percentage >= 0 AND rollout_percentage <= 100",
            name="rollout_percentage_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=new_id)
    key: Mapped[str] = mapped_column(sa.String(80), nullable=False, index=True)
    scope: Mapped[str] = mapped_column(sa.String(20), nullable=False, default=FlagScope.GLOBAL)
    #: NULL means the global row.
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)

    enabled: Mapped[bool] = mapped_column(sa.Boolean, nullable=False, default=False)
    rollout_percentage: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONColumn, nullable=False, default=dict)
    description: Mapped[str | None] = mapped_column(sa.String(400), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False, default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )


def _in_rollout(key: str, tenant_id: uuid.UUID, percentage: int) -> bool:
    """Stable bucketing of a tenant into a percentage rollout."""
    if percentage <= 0:
        return False
    if percentage >= 100:
        return True
    digest = hashlib.sha256(f"{key}:{tenant_id}".encode()).digest()
    bucket = int.from_bytes(digest[:4], "big") % 100
    return bucket < percentage


class FeatureFlagService:
    """Resolves flags with tenant override, then global, then a safe default."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._cache: dict[tuple[str, uuid.UUID | None], bool] = {}

    async def is_enabled(self, key: FlagKey | str, *, tenant_id: uuid.UUID | None = None) -> bool:
        cache_key = (str(key), tenant_id)
        if cache_key in self._cache:
            return self._cache[cache_key]

        stmt = sa.select(FeatureFlag).where(FeatureFlag.key == str(key))
        rows = list((await self._session.execute(stmt)).scalars().all())

        tenant_row = (
            next((r for r in rows if r.tenant_id == tenant_id), None) if tenant_id else None
        )
        global_row = next((r for r in rows if r.tenant_id is None), None)

        if tenant_row is not None:
            result = tenant_row.enabled
        elif global_row is None:
            result = _DEFAULTS.get(str(key), False)
        elif global_row.enabled:
            result = True
        elif tenant_id is not None:
            result = _in_rollout(str(key), tenant_id, global_row.rollout_percentage)
        else:
            result = False

        self._cache[cache_key] = result
        return result

    async def payload(
        self, key: FlagKey | str, *, tenant_id: uuid.UUID | None = None
    ) -> dict[str, Any]:
        """Structured configuration attached to a flag (thresholds, provider tuning)."""
        stmt = sa.select(FeatureFlag).where(FeatureFlag.key == str(key))
        rows = list((await self._session.execute(stmt)).scalars().all())
        tenant_row = (
            next((r for r in rows if r.tenant_id == tenant_id), None) if tenant_id else None
        )
        if tenant_row is not None:
            return dict(tenant_row.payload)
        global_row = next((r for r in rows if r.tenant_id is None), None)
        return dict(global_row.payload) if global_row else {}

    def invalidate(self) -> None:
        self._cache.clear()
