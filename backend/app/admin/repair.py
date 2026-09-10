"""Controlled repair actions.

Master spec section 103. Its last line is the design constraint:

    **No hidden "edit database row" button.**

So a repair is not a SQL escape hatch. It is a *named* action with:

*   a declared permission;
*   a mandatory reason, stored;
*   an audit entry and a :class:`~app.admin.models.RepairActionRecord`;
*   an idempotency key, so two operators reacting to the same alert run it once.

An action that cannot run in this deployment — a provider with no credentials,
a capability that has not shipped — returns ``UNAVAILABLE`` with an explanation.
That is deliberately different from ``FAILED``: an operator needs to know
whether to fix the deployment or to chase a bug.

Every handler is expected to be safe to run twice. Where an action genuinely
cannot be (a reversal), the idempotency record is what stops the second run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.auth import AdminPrincipal
from app.admin.models import AdminPermission, RepairActionRecord, RepairOutcome
from app.billing.models import BillingWebhookEvent, WebhookProcessingState
from app.billing.providers.registry import BillingProviderRegistry
from app.billing.service import BillingService
from app.common.audit import AuditAction, record_audit
from app.common.feature_flags import FeatureFlag, FlagKey, FlagScope
from app.common.outbox import OutboxEvent, OutboxStatus
from app.common.provider_health import ProviderHealthService
from app.core.clock import utc_now
from app.core.config import Settings
from app.core.errors import NotFoundError, ValidationError
from app.core.redaction import redact_value
from app.db.tenancy import TENANT_CHECKED
from app.entitlements.catalog import PlanCode
from app.ledger.models import LedgerBucket, LedgerEntry
from app.money.models import CodReceivable
from app.notifications.models import Notification

__all__ = ["REPAIR_ACTIONS", "RepairAction", "RepairRequest", "RepairResult", "RepairRunner"]


@dataclass(frozen=True, slots=True)
class RepairRequest:
    """What an operator asked for."""

    action: str
    reason: str
    tenant_id: uuid.UUID | None = None
    target_type: str | None = None
    target_id: str | None = None
    idempotency_key: str | None = None
    params: dict[str, Any] | None = None

    def key(self) -> str:
        """The idempotency key, derived when the caller did not supply one.

        The derived key includes a fingerprint of ``params``. Without it two
        genuinely different requests — "turn pathao off" and "turn redx on" —
        would derive the same key, and the second would be reported as a replay
        of the first while changing nothing. An explicitly supplied key always
        wins, because the console knows better than this heuristic does.
        """
        if self.idempotency_key:
            return self.idempotency_key[:200]
        fingerprint = hashlib.sha256(
            json.dumps(self.params or {}, sort_keys=True, default=str).encode()
        ).hexdigest()[:16]
        parts = [self.action, str(self.tenant_id or "-"), self.target_id or "-", fingerprint]
        return ":".join(parts)[:200]


@dataclass(frozen=True, slots=True)
class RepairResult:
    outcome: RepairOutcome
    detail: str
    data: dict[str, Any] | None = None


Handler = Callable[["RepairContext"], Awaitable[RepairResult]]


@dataclass(frozen=True, slots=True)
class RepairAction:
    """A named repair and what it takes to run one."""

    name: str
    permission: AdminPermission
    summary: str
    handler: Handler
    #: True when the action changes money or entitlement. Surfaced in the
    #: console so the confirm dialog can say so.
    is_sensitive: bool = False


@dataclass(slots=True)
class RepairContext:
    """Everything a handler is allowed to touch."""

    db: AsyncSession
    settings: Settings
    admin: AdminPrincipal
    request: RepairRequest
    registry: BillingProviderRegistry

    @property
    def params(self) -> dict[str, Any]:
        return self.request.params or {}

    def require_tenant(self) -> uuid.UUID:
        if self.request.tenant_id is None:
            raise ValidationError("This repair needs a tenant")
        return self.request.tenant_id

    def require_target(self) -> str:
        if not self.request.target_id:
            raise ValidationError("This repair needs a target id")
        return self.request.target_id


# --------------------------------------------------------------------------- #
# Handlers
# --------------------------------------------------------------------------- #


async def _replay_billing_webhook(ctx: RepairContext) -> RepairResult:
    """Re-process a stored billing webhook.

    The dedupe row is *not* deleted. Instead the stored, already-verified
    payload is applied again through the ordinary path, which is itself
    idempotent — a renewal sets an absolute expiry, so replaying it changes
    nothing if it already applied.
    """
    record = await ctx.db.get(BillingWebhookEvent, uuid.UUID(ctx.require_target()))
    if record is None:
        raise NotFoundError("No such webhook event")
    if not record.signature_verified:
        return RepairResult(
            RepairOutcome.FAILED,
            "This webhook never passed signature verification and will not be replayed.",
        )

    record.state = str(WebhookProcessingState.RECEIVED)
    record.error = None
    record.processed_at = None
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        "The webhook is queued for reprocessing on the next billing sync.",
        {"webhook_id": str(record.id), "provider": record.provider},
    )


async def _retry_outbox_event(ctx: RepairContext) -> RepairResult:
    """Return a dead-lettered outbox event to the queue.

    Attempts are reset so the backoff starts again, but the attempt history is
    kept on the event's error field: an operator should be able to see it had
    already failed twelve times.
    """
    event = await ctx.db.get(OutboxEvent, uuid.UUID(ctx.require_target()))
    if event is None:
        raise NotFoundError("No such outbox event")
    if event.status != str(OutboxStatus.DEAD):
        return RepairResult(
            RepairOutcome.SUCCEEDED,
            f"Nothing to do: the event is {event.status}, not dead-lettered.",
        )

    event.status = str(OutboxStatus.PENDING)
    event.attempts = 0
    event.available_at = utc_now()
    event.locked_at = None
    event.locked_by = None
    return RepairResult(
        RepairOutcome.SUCCEEDED, "The event is back on the queue.", {"topic": event.topic}
    )


async def _rebuild_money_summary(ctx: RepairContext) -> RepairResult:
    """Re-derive a shop's money position and check it against the ledger.

    Section 104: a cache is never the only copy of a financial fact, and the
    money summary is already computed on demand rather than stored. So this
    action's real job is section 81.10's invariant — the outstanding total and
    the ledger's receivable bucket are derived by two different routes and must
    agree.

    A mismatch is *reported*, never "fixed". Adjusting one figure to match the
    other would destroy the evidence of whichever one is wrong.

    The admin session is unscoped, so every filter here names the tenant
    explicitly; reusing the tenant-scoped services would silently sum the whole
    platform.
    """
    tenant_id = ctx.require_tenant()

    receivables = (
        (await ctx.db.execute(sa.select(CodReceivable).where(CodReceivable.tenant_id == tenant_id)))
        .scalars()
        .all()
    )
    outstanding = sum(row.outstanding_paisa for row in receivables)

    ledger_rows = (
        await ctx.db.execute(
            sa.select(
                LedgerEntry.direction,
                sa.func.coalesce(sa.func.sum(LedgerEntry.amount_paisa), 0),
            )
            .where(
                LedgerEntry.tenant_id == tenant_id,
                LedgerEntry.bucket == str(LedgerBucket.COD_RECEIVABLE),
            )
            .group_by(LedgerEntry.direction)
            .execution_options(**{TENANT_CHECKED: True})
        )
    ).all()
    credits = sum(int(total) for direction, total in ledger_rows if direction == "CREDIT")
    debits = sum(int(total) for direction, total in ledger_rows if direction != "CREDIT")
    ledger_receivable = credits - debits

    agrees = outstanding == ledger_receivable
    return RepairResult(
        RepairOutcome.SUCCEEDED if agrees else RepairOutcome.FAILED,
        (
            "Receivables and the ledger agree."
            if agrees
            else "MISMATCH: the outstanding total and the ledger's receivable "
            "bucket disagree. Do not adjust either — open a support case."
        ),
        {
            "receivable_count": len(receivables),
            "outstanding_paisa": outstanding,
            "ledger_receivable_paisa": ledger_receivable,
            "difference_paisa": outstanding - ledger_receivable,
        },
    )


async def _refresh_provider_health(ctx: RepairContext) -> RepairResult:
    """Close a provider's circuit breaker and clear its failure streak."""
    provider = str(ctx.params.get("provider") or "")
    if not provider:
        raise ValidationError("A provider name is required")
    capability = str(ctx.params.get("capability") or "*")

    row = await ProviderHealthService(ctx.db).reset(
        provider, capability=capability, tenant_id=ctx.request.tenant_id
    )
    if row is None:
        return RepairResult(
            RepairOutcome.SUCCEEDED,
            f"No health record existed for {provider}; nothing was open.",
        )
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        f"{provider} breaker closed; state reset to UNKNOWN until the next call.",
        {"provider": provider, "capability": capability},
    )


async def _disable_provider_capability(ctx: RepairContext) -> RepairResult:
    """Turn a provider off with a feature flag (section 44).

    A provider outage must be survivable without shipping an app version, which
    is exactly what this is for.
    """
    raw_flag = str(ctx.params.get("flag") or "")
    try:
        flag = FlagKey(raw_flag)
    except ValueError as exc:
        raise ValidationError(
            f"'{raw_flag}' is not a known feature flag",
            details={"known": ", ".join(sorted(FlagKey))},
        ) from exc

    enabled = bool(ctx.params.get("enabled", False))
    scope_tenant = ctx.request.tenant_id

    existing = (
        await ctx.db.execute(
            sa.select(FeatureFlag).where(
                FeatureFlag.key == str(flag), FeatureFlag.tenant_id == scope_tenant
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        existing = FeatureFlag(
            key=str(flag),
            scope=str(FlagScope.TENANT if scope_tenant else FlagScope.GLOBAL),
            tenant_id=scope_tenant,
            enabled=enabled,
            description=f"Set by {ctx.admin.label}: {ctx.request.reason}"[:400],
        )
        ctx.db.add(existing)
    else:
        existing.enabled = enabled
        existing.description = f"Set by {ctx.admin.label}: {ctx.request.reason}"[:400]

    await record_audit(
        ctx.db,
        AuditAction.FEATURE_FLAG_CHANGED,
        entity_type="feature_flag",
        entity_id=str(flag),
        reason=ctx.request.reason,
        context={"flag": str(flag), "enabled": enabled, "admin": ctx.admin.label},
        tenant_id=scope_tenant,
    )
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        f"{flag} is now {'on' if enabled else 'off'}"
        + (" for this shop." if scope_tenant else " globally."),
        {"flag": str(flag), "enabled": enabled},
    )


async def _grant_support_credit(ctx: RepairContext) -> RepairResult:
    """Extend a shop's plan as support credit (section 103).

    The one repair that gives away revenue, which is why it needs its own
    permission rather than the general repair one.
    """
    tenant_id = ctx.require_tenant()
    days = int(ctx.params.get("days") or 0)
    raw_plan = str(ctx.params.get("plan") or PlanCode.STARTER)
    try:
        plan = PlanCode(raw_plan)
    except ValueError as exc:
        raise ValidationError(f"'{raw_plan}' is not a plan code") from exc
    if days <= 0 or days > 365:
        raise ValidationError("Support credit must be between 1 and 365 days")

    billing = BillingService(ctx.db, settings=ctx.settings, registry=ctx.registry)
    subscription = await billing.grant_manual_subscription(
        tenant_id=tenant_id,
        plan=plan,
        days=days,
        reason=ctx.request.reason,
        actor_id=ctx.admin.admin_id,
        actor_label=ctx.admin.label,
    )
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        f"{plan} extended by {days} day(s).",
        {
            "plan": str(plan),
            "valid_until": subscription.current_period_end.isoformat()
            if subscription.current_period_end
            else None,
        },
    )


async def _reconcile_subscription(ctx: RepairContext) -> RepairResult:
    """Re-ask the provider what a subscription's real state is."""
    tenant_id = ctx.require_tenant()
    billing = BillingService(ctx.db, settings=ctx.settings, registry=ctx.registry)
    subscription = await billing.current_subscription(tenant_id)
    if subscription is None:
        return RepairResult(RepairOutcome.SUCCEEDED, "This shop has no subscription.")

    outcome = await billing.reconcile_subscription(subscription)
    if outcome in {"unavailable", "unknown"}:
        return RepairResult(
            RepairOutcome.UNAVAILABLE,
            "The provider could not be asked, so nothing was changed. "
            "Play state arrives by notification or restore; a manual grant has "
            "no provider to reconcile against.",
            {"result": outcome},
        )
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        f"Provider truth applied: {outcome}.",
        {"result": outcome, "status": subscription.status},
    )


async def _retry_notification(ctx: RepairContext) -> RepairResult:
    """Re-queue a notification for delivery.

    Marks the row unread so the seller sees it in the centre. Delivery over a
    transport happens only where one is configured — none is in this build.
    """
    notification = await ctx.db.get(Notification, uuid.UUID(ctx.require_target()))
    if notification is None:
        raise NotFoundError("No such notification")
    notification.read_at = None
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        "Notification marked unread; it is visible in the seller's centre.",
        {"kind": notification.kind},
    )


async def _reverse_reconciliation_match(ctx: RepairContext) -> RepairResult:
    """Undo a settlement that was matched to the wrong parcel.

    Section 81.8: a reversal, never a deletion. The original ledger entries stay
    and two more undo them, so the mistake and its correction are both readable.
    """
    from app.reconciliation.service import ReconciliationService

    line_id = uuid.UUID(ctx.require_target())
    line = await ReconciliationService(ctx.db).unmatch(
        line_id, reason=f"admin repair by {ctx.admin.label}: {ctx.request.reason}"
    )
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        "Match reversed. Two compensating ledger entries were written.",
        {"payout_line_id": str(line.id)},
    )


async def _revoke_courier_credential(ctx: RepairContext) -> RepairResult:
    """Revoke a shop's courier credential.

    No courier account storage exists yet — Phase C is blocked on Steadfast
    documentation — so this reports itself unavailable rather than silently
    doing nothing, which would let an operator believe a leaked key had been
    revoked when it had not.
    """
    return RepairResult(
        RepairOutcome.UNAVAILABLE,
        "No courier credential storage exists yet (Phase C is blocked on "
        "provider documentation). Nothing was revoked.",
    )


async def _rerun_payout_parser(ctx: RepairContext) -> RepairResult:
    """Re-parse a stored payout statement.

    The source file is always retained (section 83), so re-parsing is possible
    without asking the seller to upload it again. This reports what the parser
    now makes of the file; applying the result is still a seller decision.
    """
    from app.payouts.models import PayoutSourceFile
    from app.payouts.statements import parse_statement

    source = await ctx.db.get(PayoutSourceFile, uuid.UUID(ctx.require_target()))
    if source is None:
        raise NotFoundError("No such payout source file")
    if not source.raw_content:
        return RepairResult(
            RepairOutcome.UNAVAILABLE,
            "The statement's content is not held in this deployment "
            "(R2_CREDENTIALS_REQUIRED). Ask the seller to re-upload.",
        )

    parsed = parse_statement(source.raw_content.encode("utf-8"))
    invalid = parsed.invalid_rows
    return RepairResult(
        RepairOutcome.SUCCEEDED,
        f"Re-parsed: {len(parsed.rows)} row(s), {len(invalid)} unreadable.",
        {
            "rows": len(parsed.rows),
            "unreadable_rows": len(invalid),
            "total_paisa": parsed.total_paisa,
            "mapping": parsed.mapping,
        },
    )


REPAIR_ACTIONS: dict[str, RepairAction] = {
    action.name: action
    for action in (
        RepairAction(
            name="replay_billing_webhook",
            permission=AdminPermission.REPAIR_RUN,
            summary="Re-process a stored, signature-verified billing webhook.",
            handler=_replay_billing_webhook,
        ),
        RepairAction(
            name="retry_outbox_event",
            permission=AdminPermission.REPAIR_RUN,
            summary="Return a dead-lettered outbox event to the queue.",
            handler=_retry_outbox_event,
        ),
        RepairAction(
            name="rerun_payout_parser",
            permission=AdminPermission.REPAIR_RUN,
            summary="Re-parse a retained payout statement and report the result.",
            handler=_rerun_payout_parser,
        ),
        RepairAction(
            name="reverse_reconciliation_match",
            permission=AdminPermission.REPAIR_RUN,
            summary="Reverse a settlement matched to the wrong parcel.",
            handler=_reverse_reconciliation_match,
            is_sensitive=True,
        ),
        RepairAction(
            name="rebuild_money_summary",
            permission=AdminPermission.REPAIR_RUN,
            summary="Recompute a shop's money summary from the ledger.",
            handler=_rebuild_money_summary,
        ),
        RepairAction(
            name="refresh_provider_health",
            permission=AdminPermission.REPAIR_RUN,
            summary="Close a provider's circuit breaker and clear its failure streak.",
            handler=_refresh_provider_health,
        ),
        RepairAction(
            name="revoke_courier_credential",
            permission=AdminPermission.REPAIR_RUN,
            summary="Revoke a shop's courier credential.",
            handler=_revoke_courier_credential,
            is_sensitive=True,
        ),
        RepairAction(
            name="retry_notification",
            permission=AdminPermission.REPAIR_RUN,
            summary="Re-queue a notification for the seller's centre.",
            handler=_retry_notification,
        ),
        RepairAction(
            name="reconcile_subscription",
            permission=AdminPermission.REPAIR_RUN,
            summary="Re-ask the billing provider for a subscription's real state.",
            handler=_reconcile_subscription,
        ),
        RepairAction(
            name="disable_provider_capability",
            permission=AdminPermission.FEATURE_FLAG_WRITE,
            summary="Turn a provider capability on or off with a feature flag.",
            handler=_disable_provider_capability,
            is_sensitive=True,
        ),
        RepairAction(
            name="grant_support_credit",
            permission=AdminPermission.BILLING_GRANT,
            summary="Extend a shop's plan as support credit.",
            handler=_grant_support_credit,
            is_sensitive=True,
        ),
    )
}


class RepairRunner:
    """Authorises, deduplicates, executes and records a repair."""

    #: A reason shorter than this is not a reason.
    MINIMUM_REASON_LENGTH = 8

    def __init__(
        self,
        session: AsyncSession,
        *,
        settings: Settings,
        admin: AdminPrincipal,
        registry: BillingProviderRegistry,
    ) -> None:
        self._db = session
        self._settings = settings
        self._admin = admin
        self._registry = registry

    async def run(self, request: RepairRequest) -> tuple[RepairActionRecord, RepairResult]:
        action = REPAIR_ACTIONS.get(request.action)
        if action is None:
            raise NotFoundError(
                f"'{request.action}' is not a repair action",
                details={"known": ", ".join(sorted(REPAIR_ACTIONS))},
            )

        self._admin.require(action.permission)
        if len(request.reason.strip()) < self.MINIMUM_REASON_LENGTH:
            raise ValidationError(
                "A repair action requires a reason",
                details={"minimum_length": str(self.MINIMUM_REASON_LENGTH)},
            )

        key = request.key()
        existing = (
            await self._db.execute(
                sa.select(RepairActionRecord).where(
                    RepairActionRecord.action == action.name,
                    RepairActionRecord.idempotency_key == key,
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            # Two operators reacting to the same alert. The first run stands.
            return existing, RepairResult(
                RepairOutcome.REPLAYED,
                f"Already run at {existing.created_at:%Y-%m-%d %H:%M} by {existing.admin_label}.",
                existing.result,
            )

        record = RepairActionRecord(
            action=action.name,
            idempotency_key=key,
            tenant_id=request.tenant_id,
            target_type=request.target_type,
            target_id=request.target_id,
            admin_id=self._admin.admin_id,
            admin_label=self._admin.label,
            reason=request.reason.strip(),
            outcome=str(RepairOutcome.FAILED),
        )
        self._db.add(record)
        await self._db.flush()

        context = RepairContext(
            db=self._db,
            settings=self._settings,
            admin=self._admin,
            request=request,
            registry=self._registry,
        )
        try:
            result = await action.handler(context)
        except Exception as exc:
            record.outcome = str(RepairOutcome.FAILED)
            record.error = f"{type(exc).__name__}: {exc}"[:400]
            record.completed_at = utc_now()
            await self._audit(action, record, RepairOutcome.FAILED)
            # Commit the failure record before re-raising: a repair that blew up
            # is precisely the one an operator must be able to find afterwards.
            await self._db.commit()
            raise

        record.outcome = str(result.outcome)
        record.result = redact_value(result.data or {})
        record.error = None if result.outcome is RepairOutcome.SUCCEEDED else result.detail[:400]
        record.completed_at = utc_now()
        await self._audit(action, record, result.outcome)
        return record, result

    async def _audit(
        self, action: RepairAction, record: RepairActionRecord, outcome: RepairOutcome
    ) -> None:
        await record_audit(
            self._db,
            AuditAction.ADMIN_REPAIR_ACTION,
            entity_type=record.target_type or "repair",
            entity_id=record.target_id or str(record.id),
            reason=record.reason,
            context={
                "action": action.name,
                "outcome": str(outcome),
                "admin": self._admin.label,
                "sensitive": action.is_sensitive,
            },
            tenant_id=record.tenant_id,
        )
