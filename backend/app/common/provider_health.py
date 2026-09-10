"""Provider health and circuit breaking.

Master spec sections 49 and 75, and the Phase F brief's section 22. The rule
that matters most is this one:

    **Do not decide a provider is down from one failed request.**

A single timeout is normal. What health tracks is a *pattern*: recent successes
against recent errors, whether the failures are authentication failures (which
never fix themselves), how long ago the provider last worked, and whether a
breaker has opened.

The other rule is section 75's: a degraded provider degrades **the action that
needs it**, not the app. A courier whose booking API is down must not stop a
seller reading their money screen, so health is recorded per (provider,
capability) and the UI reacts to the capability.

Health is a cache of observations, never a source of truth about money. Nothing
in here decides whether a parcel was delivered or a payment arrived.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.common.audit import AuditAction, record_audit
from app.core.clock import utc_now
from app.db.base import Base, PrimaryKeyMixin
from app.db.types import GUID, TZDateTime

__all__ = [
    "BreakerState",
    "HealthState",
    "HealthView",
    "ProviderHealth",
    "ProviderHealthService",
    "ProviderKind",
    "scope_key_for",
]


def scope_key_for(tenant_id: uuid.UUID | None) -> str:
    """The scope a health row belongs to: one tenant, or the platform."""
    return str(tenant_id) if tenant_id is not None else "*"


class ProviderKind(StrEnum):
    """What sort of provider this is. Health is tracked the same way for all."""

    COURIER = "COURIER"
    SMS = "SMS"
    PUSH = "PUSH"
    BILLING = "BILLING"
    RISK = "RISK"
    AI_PARSER = "AI_PARSER"
    STORAGE = "STORAGE"


class HealthState(StrEnum):
    """Master spec section 49's provider dashboard states."""

    HEALTHY = "HEALTHY"
    #: Working, but erroring more than usual or slower than usual.
    DEGRADED = "DEGRADED"
    #: Consistently failing. The breaker is open.
    DOWN = "DOWN"
    #: Credentials were rejected. This never recovers on its own — a person has
    #: to reconnect the account, so it is a distinct state from DOWN.
    NEEDS_RECONNECT = "NEEDS_RECONNECT"
    #: Never called, or no observation recent enough to judge.
    UNKNOWN = "UNKNOWN"


class BreakerState(StrEnum):
    CLOSED = "CLOSED"
    OPEN = "OPEN"
    #: One trial request is allowed through to see whether it recovered.
    HALF_OPEN = "HALF_OPEN"


#: How many consecutive failures open the breaker. Chosen so a single timeout
#: and a single retry cannot take a provider out of service.
FAILURE_THRESHOLD = 5
#: Consecutive failures that mark DEGRADED before DOWN.
DEGRADED_THRESHOLD = 2
#: How long the breaker stays open before a trial request.
BREAKER_COOLDOWN = timedelta(minutes=2)
#: An observation older than this says nothing about now.
OBSERVATION_TTL = timedelta(hours=6)


class ProviderHealth(Base, PrimaryKeyMixin):
    """Rolling health for one provider capability.

    Optionally per tenant: a courier account with bad credentials is *that
    shop's* problem, and marking the provider globally down because one seller
    mistyped an API key would take the feature away from everyone. A row with
    ``tenant_id IS NULL`` is the platform-wide view.
    """

    __tablename__ = "provider_health"
    __table_args__ = (
        # Keyed on ``scope_key`` rather than on the nullable ``tenant_id``.
        # SQL treats NULLs as distinct in a unique constraint, so a platform-wide
        # row would be duplicated on every observation — and the lookup, written
        # as ``tenant_id == None``, would compile to ``= NULL`` and match nothing.
        sa.UniqueConstraint("provider", "capability", "scope_key", name="uq_provider_health_scope"),
        sa.Index("ix_provider_health_state", "state"),
        sa.CheckConstraint("consecutive_failures >= 0", name="failures_non_negative"),
    )

    kind: Mapped[str] = mapped_column(sa.String(16), nullable=False)
    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: ``"*"`` for the provider as a whole; otherwise a named capability, so a
    #: courier whose status lookup is broken can still take bookings.
    capability: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="*")
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True, index=True)
    #: ``str(tenant_id)`` or ``"*"`` for the platform-wide row. See the table
    #: args for why this exists rather than a nullable column in the key.
    scope_key: Mapped[str] = mapped_column(sa.String(40), nullable=False, default="*")

    state: Mapped[str] = mapped_column(sa.String(20), nullable=False, default=HealthState.UNKNOWN)
    breaker_state: Mapped[str] = mapped_column(
        sa.String(12), nullable=False, default=BreakerState.CLOSED
    )

    success_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    error_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    auth_failure_count: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)
    consecutive_failures: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    #: Exponential moving average, milliseconds. Cheap to keep and enough to
    #: notice a provider that is up but four times slower than yesterday.
    latency_ms_ema: Mapped[int] = mapped_column(sa.Integer, nullable=False, default=0)

    last_success_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    last_error_code: Mapped[str | None] = mapped_column(sa.String(80), nullable=True)
    #: Never the provider's raw response: that can carry a token.
    last_error_summary: Mapped[str | None] = mapped_column(sa.String(300), nullable=True)

    breaker_opened_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime, nullable=False, default=utc_now, onupdate=utc_now
    )

    def allows_request(self, *, at: datetime | None = None) -> bool:
        """Whether a caller should attempt this provider right now."""
        moment = at or utc_now()
        if self.breaker_state != BreakerState.OPEN:
            return True
        if self.breaker_opened_at is None:
            return True
        return moment - self.breaker_opened_at >= BREAKER_COOLDOWN


@dataclass(frozen=True, slots=True)
class HealthView:
    """One provider's health, as the console and the client see it."""

    kind: str
    provider: str
    capability: str
    state: str
    breaker_state: str
    success_count: int
    error_count: int
    auth_failure_count: int
    latency_ms: int
    last_success_at: datetime | None
    last_error_at: datetime | None
    last_error_code: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "provider": self.provider,
            "capability": self.capability,
            "state": self.state,
            "breaker_state": self.breaker_state,
            "success_count": self.success_count,
            "error_count": self.error_count,
            "auth_failure_count": self.auth_failure_count,
            "latency_ms": self.latency_ms,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_error_at": self.last_error_at.isoformat() if self.last_error_at else None,
            "last_error_code": self.last_error_code,
        }


class ProviderHealthService:
    """Records observations and derives a health state from them."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def _find(
        self, provider: str, capability: str, tenant_id: uuid.UUID | None
    ) -> ProviderHealth | None:
        """Load one health row.

        Deliberately *not* ``populate_existing``: this session mutates the row
        through the ORM, and the sessionmaker runs with autoflush off, so
        re-populating from the database would silently discard the failure that
        was just recorded and re-close a breaker that had opened.
        """
        return (
            await self._db.execute(
                sa.select(ProviderHealth).where(
                    ProviderHealth.provider == provider,
                    ProviderHealth.capability == capability,
                    ProviderHealth.scope_key == scope_key_for(tenant_id),
                )
            )
        ).scalar_one_or_none()

    async def _row(
        self,
        kind: ProviderKind,
        provider: str,
        capability: str,
        tenant_id: uuid.UUID | None,
    ) -> ProviderHealth:
        existing = await self._find(provider, capability, tenant_id)
        if existing is not None:
            return existing

        row = ProviderHealth(
            kind=str(kind),
            provider=provider,
            capability=capability,
            tenant_id=tenant_id,
            scope_key=scope_key_for(tenant_id),
            state=str(HealthState.UNKNOWN),
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def record_success(
        self,
        kind: ProviderKind,
        provider: str,
        *,
        capability: str = "*",
        tenant_id: uuid.UUID | None = None,
        latency_ms: int | None = None,
    ) -> ProviderHealth:
        row = await self._row(kind, provider, capability, tenant_id)
        previous = row.state

        row.success_count += 1
        row.consecutive_failures = 0
        row.last_success_at = utc_now()
        row.breaker_state = str(BreakerState.CLOSED)
        row.breaker_opened_at = None
        row.state = str(HealthState.HEALTHY)
        if latency_ms is not None:
            row.latency_ms_ema = _ema(row.latency_ms_ema, latency_ms)

        await self._announce(row, previous)
        await self._db.flush()
        return row

    async def record_failure(
        self,
        kind: ProviderKind,
        provider: str,
        *,
        capability: str = "*",
        tenant_id: uuid.UUID | None = None,
        error_code: str | None = None,
        summary: str | None = None,
        is_auth_failure: bool = False,
        latency_ms: int | None = None,
    ) -> ProviderHealth:
        """Record one failed call and re-derive the state.

        One failure moves nothing to ``DOWN``. An *authentication* failure is
        different: it will not fix itself, so it goes straight to
        ``NEEDS_RECONNECT`` and the seller is asked to reconnect the account
        rather than being told to wait.
        """
        row = await self._row(kind, provider, capability, tenant_id)
        previous = row.state

        row.error_count += 1
        row.consecutive_failures += 1
        row.last_error_at = utc_now()
        row.last_error_code = error_code
        row.last_error_summary = summary[:300] if summary else None
        if latency_ms is not None:
            row.latency_ms_ema = _ema(row.latency_ms_ema, latency_ms)

        if is_auth_failure:
            row.auth_failure_count += 1
            row.state = str(HealthState.NEEDS_RECONNECT)
            row.breaker_state = str(BreakerState.OPEN)
            row.breaker_opened_at = utc_now()
        elif row.consecutive_failures >= FAILURE_THRESHOLD:
            row.state = str(HealthState.DOWN)
            row.breaker_state = str(BreakerState.OPEN)
            row.breaker_opened_at = utc_now()
        elif row.consecutive_failures >= DEGRADED_THRESHOLD:
            row.state = str(HealthState.DEGRADED)
        else:
            # A single failure against a provider that has been working is not
            # news. Keep the state and let the pattern speak.
            row.state = (
                str(HealthState.DEGRADED)
                if previous == str(HealthState.DOWN)
                else previous or str(HealthState.HEALTHY)
            )

        await self._announce(row, previous)
        await self._db.flush()
        return row

    async def _announce(self, row: ProviderHealth, previous: str) -> None:
        if row.state == previous:
            return
        await record_audit(
            self._db,
            AuditAction.PROVIDER_HEALTH_CHANGED,
            entity_type="provider_health",
            entity_id=row.id,
            context={
                "provider": row.provider,
                "capability": row.capability,
                "from": previous,
                "to": row.state,
                "consecutive_failures": row.consecutive_failures,
            },
            tenant_id=row.tenant_id,
        )

    async def allows(
        self,
        provider: str,
        *,
        capability: str = "*",
        tenant_id: uuid.UUID | None = None,
    ) -> bool:
        """Whether the circuit breaker permits a call right now."""
        row = await self._find(provider, capability, tenant_id)
        if row is None:
            return True
        if row.allows_request() and row.breaker_state == str(BreakerState.OPEN):
            # Cooldown elapsed: let one request through to test the water.
            row.breaker_state = str(BreakerState.HALF_OPEN)
            return True
        return row.allows_request()

    async def snapshot(
        self, *, tenant_id: uuid.UUID | None = None, include_platform: bool = True
    ) -> list[HealthView]:
        """Health for the console and the client, freshest first.

        An observation older than :data:`OBSERVATION_TTL` is reported as
        ``UNKNOWN`` rather than as whatever it was six hours ago — a stale
        "HEALTHY" is a worse answer than "we do not know".
        """
        scopes = [scope_key_for(tenant_id)]
        if tenant_id is not None and include_platform:
            scopes.append(scope_key_for(None))
        conditions = [ProviderHealth.scope_key.in_(scopes)]

        rows = (
            (
                await self._db.execute(
                    sa.select(ProviderHealth)
                    .where(*conditions)
                    .order_by(ProviderHealth.provider, ProviderHealth.capability)
                )
            )
            .scalars()
            .all()
        )

        now = utc_now()
        views: list[HealthView] = []
        for row in rows:
            latest = max(
                [value for value in (row.last_success_at, row.last_error_at) if value is not None],
                default=None,
            )
            stale = latest is None or (now - latest) > OBSERVATION_TTL
            views.append(
                HealthView(
                    kind=row.kind,
                    provider=row.provider,
                    capability=row.capability,
                    state=str(HealthState.UNKNOWN) if stale else row.state,
                    breaker_state=row.breaker_state,
                    success_count=row.success_count,
                    error_count=row.error_count,
                    auth_failure_count=row.auth_failure_count,
                    latency_ms=row.latency_ms_ema,
                    last_success_at=row.last_success_at,
                    last_error_at=row.last_error_at,
                    last_error_code=row.last_error_code,
                )
            )
        return views

    async def reset(
        self,
        provider: str,
        *,
        capability: str = "*",
        tenant_id: uuid.UUID | None = None,
    ) -> ProviderHealth | None:
        """Close the breaker and clear the failure streak.

        The repair action behind "force provider status refresh" (section 103).
        Counters are kept: a support engineer should still be able to see that
        the provider failed 40 times this morning.
        """
        row = await self._find(provider, capability, tenant_id)
        if row is None:
            return None
        previous = row.state
        row.consecutive_failures = 0
        row.breaker_state = str(BreakerState.CLOSED)
        row.breaker_opened_at = None
        row.state = str(HealthState.UNKNOWN)
        await self._announce(row, previous)
        await self._db.flush()
        return row


def _ema(current: int, sample: int, *, weight: float = 0.3) -> int:
    """Exponential moving average, kept as an integer.

    A first observation becomes the average outright; otherwise the new sample
    is worth 30%, which reacts to a slowdown within a handful of calls without
    swinging on one outlier.
    """
    if current <= 0:
        return max(0, sample)
    return round(current * (1 - weight) + sample * weight)
