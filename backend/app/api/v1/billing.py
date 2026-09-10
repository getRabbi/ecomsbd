"""Billing endpoints.

Master spec section 39's billing routes, completed in Phase F::

    GET  /v1/billing/entitlements
    GET  /v1/billing/plans
    GET  /v1/billing/subscription
    GET  /v1/billing/usage
    GET  /v1/billing/channel
    GET  /v1/billing/history
    POST /v1/billing/play/verify
    POST /v1/billing/restore
    POST /v1/billing/web/checkout
    POST /v1/billing/web/confirm
    POST /v1/billing/cancel
    POST /v1/billing/webhooks/{provider}

Three rules are visible in the routing itself:

*   every purchase route requires :attr:`~app.tenants.roles.Permission.BILLING_MANAGE`,
    which only an Owner holds (section 88);
*   the webhook route is **unauthenticated by necessity** and therefore verifies
    a provider signature before it reads the body, and returns success on a
    replay so a provider does not retry forever;
*   what the client may *offer* comes from ``GET /billing/channel``, not from a
    platform check in the app (section 27.1).
"""

from __future__ import annotations

from typing import Annotated

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Path, Request, Response, status

from app.api.deps import (
    BillingServiceDep,
    DbSession,
    DistributionChannelDep,
    EntitlementsDep,
    FeatureFlagsDep,
    ProviderRegistryDep,
    TenantPrincipal,
    require_permission,
)
from app.api.v1.billing_schemas import (
    BillingChannelResponse,
    BillingProviderStateResponse,
    BillingTransactionResponse,
    CancelSubscriptionPayload,
    CheckoutPayload,
    CheckoutResponse,
    ConfirmCheckoutPayload,
    PlayVerifyPayload,
    PurchaseResultResponse,
    RestorePurchasesPayload,
    RestorePurchasesResponse,
    SubscriptionEventResponse,
    SubscriptionResponse,
    UsageResponse,
    UsageView,
)
from app.api.v1.schemas import EntitlementsResponse, MoneyAmount, PlanResponse
from app.billing.models import (
    BillingProviderKind,
    BillingTransaction,
    SubscriptionEvent,
)
from app.billing.providers.registry import PROVIDER_FLAGS
from app.billing.service import PurchaseOutcome
from app.core.clock import utc_now
from app.core.errors import ValidationError
from app.entitlements.catalog import PlanCode, plan_catalog
from app.entitlements.models import Subscription
from app.tenants.roles import Permission

router = APIRouter(prefix="/billing", tags=["billing"])

BillingManager = Annotated[object, Depends(require_permission(Permission.BILLING_MANAGE))]


# --------------------------------------------------------------------------- #
# Read
# --------------------------------------------------------------------------- #


@router.get(
    "/entitlements",
    response_model=EntitlementsResponse,
    summary="The active shop's plan and entitlements",
)
async def get_entitlements(
    principal: TenantPrincipal,
    entitlements: EntitlementsDep,
) -> EntitlementsResponse:
    """Server-authoritative entitlement state.

    The client uses this to shape its UI. It is never the basis of an
    authorisation decision: every paid operation re-checks server-side
    (master spec section 27.3).
    """
    snapshot = await entitlements.snapshot(principal.require_tenant())
    return EntitlementsResponse(
        plan=str(snapshot.plan),
        status=snapshot.status,
        source=snapshot.source,
        valid_until=snapshot.valid_until,
        entitlements=snapshot.entitlements,
    )


@router.get("/plans", response_model=list[PlanResponse], summary="Available plans")
async def list_plans(principal: TenantPrincipal) -> list[PlanResponse]:
    """Plan catalogue.

    Prices are the section 26 hypothesis pending seller validation, and are
    overridable from configuration — so this reads the resolved catalogue rather
    than the module constant.
    """
    return [
        PlanResponse(
            code=str(plan.code),
            name=plan.name,
            price=MoneyAmount(amount_paisa=plan.price_paisa),
            entitlements={str(k): v for k, v in plan.entitlements.items()},
        )
        for plan in plan_catalog().values()
    ]


@router.get("/usage", response_model=UsageResponse, summary="Metered usage this period")
async def get_usage(principal: TenantPrincipal, entitlements: EntitlementsDep) -> UsageResponse:
    """Server-authoritative usage counters (master spec section 43).

    The client never counts. It displays what this returns, so an app that was
    offline for a day cannot believe it has quota it has already spent.
    """
    tenant_id = principal.require_tenant()
    plan = await entitlements.plan_for(tenant_id)
    views = await entitlements.usage(tenant_id)
    return UsageResponse(
        plan=str(plan.code),
        usage=[UsageView.model_validate(view.as_dict()) for view in views],
    )


@router.get(
    "/subscription", response_model=SubscriptionResponse | None, summary="Current subscription"
)
async def get_subscription(
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    entitlements: EntitlementsDep,
) -> SubscriptionResponse | None:
    tenant_id = principal.require_tenant()
    subscription = await billing.current_subscription(tenant_id)
    if subscription is None:
        return None
    snapshot = await entitlements.snapshot(tenant_id)
    return _subscription_response(subscription, in_grace=snapshot.in_grace)


@router.get(
    "/channel",
    response_model=BillingChannelResponse,
    summary="What this build may offer for purchase",
)
async def get_channel(
    principal: TenantPrincipal,
    registry: ProviderRegistryDep,
    flags: FeatureFlagsDep,
    channel: DistributionChannelDep,
) -> BillingChannelResponse:
    """The one place the billing call-to-action is decided (section 27.1).

    A Play build never receives an external payment provider here, so no screen
    has to remember the policy.
    """
    tenant_id = principal.tenant_id
    disabled = {
        flag
        for provider_kind, flag in PROVIDER_FLAGS.items()
        if not await flags.is_enabled(flag, tenant_id=tenant_id)
    }
    policy = registry.policy(channel, disabled_flags=frozenset(disabled))

    return BillingChannelResponse(
        channel=str(policy.channel),
        can_purchase=policy.can_purchase_in_app,
        allows_external_payment_link=policy.allows_external_payment_cta,
        providers=[
            BillingProviderStateResponse(
                provider=str(state.provider),
                available=state.available,
                blocker=str(state.blocker),
                allowed_in_channel=state.allowed_in_channel,
                detail=None,  # operator detail is admin-only
            )
            for state in policy.providers
        ],
        unavailable_message_bn=(
            None
            if policy.can_purchase_in_app
            else "এখন অ্যাপ থেকে প্ল্যান কেনা যাচ্ছে না। আমরা শীঘ্রই চালু করছি।"
        ),
    )


@router.get(
    "/history",
    response_model=list[BillingTransactionResponse],
    summary="Billing transaction history",
)
async def billing_history(
    principal: TenantPrincipal,
    db: DbSession,
    _: BillingManager,
    limit: int = 50,
) -> list[BillingTransactionResponse]:
    """Every billing change this shop has had (Phase F brief, section 5).

    Includes refusals. A seller whose payment did not verify should be able to
    see that it was attempted and why it failed, rather than seeing nothing and
    concluding the money vanished.
    """
    rows = (
        (
            await db.execute(
                sa.select(BillingTransaction)
                .where(BillingTransaction.tenant_id == principal.require_tenant())
                .order_by(BillingTransaction.occurred_at.desc())
                .limit(min(limit, 200))
            )
        )
        .scalars()
        .all()
    )
    return [
        BillingTransactionResponse(
            id=row.id,
            provider=row.provider,
            plan=row.plan_code,
            kind=row.kind,
            state=row.state,
            amount=MoneyAmount(amount_paisa=row.amount_paisa, currency=row.currency),
            verification_result=row.verification_result,
            detail=row.verification_detail,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


@router.get(
    "/events",
    response_model=list[SubscriptionEventResponse],
    summary="Subscription state history",
)
async def subscription_events(
    principal: TenantPrincipal,
    db: DbSession,
    _: BillingManager,
    limit: int = 50,
) -> list[SubscriptionEventResponse]:
    rows = (
        (
            await db.execute(
                sa.select(SubscriptionEvent)
                .where(SubscriptionEvent.tenant_id == principal.require_tenant())
                .order_by(SubscriptionEvent.created_at.desc())
                .limit(min(limit, 200))
            )
        )
        .scalars()
        .all()
    )
    return [
        SubscriptionEventResponse(
            id=row.id,
            event_type=row.event_type,
            from_status=row.from_status,
            to_status=row.to_status,
            provider=row.provider,
            reason=row.reason,
            occurred_at=row.occurred_at,
        )
        for row in rows
    ]


# --------------------------------------------------------------------------- #
# Purchase
# --------------------------------------------------------------------------- #


@router.post(
    "/play/verify",
    response_model=PurchaseResultResponse,
    summary="Verify a Google Play purchase server-side",
)
async def verify_play_purchase(
    payload: PlayVerifyPayload,
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    entitlements: EntitlementsDep,
    channel: DistributionChannelDep,
    _: BillingManager,
) -> PurchaseResultResponse:
    """Master spec section 90.

    This endpoint does **not** grant anything by itself. It hands the token to
    the provider and grants only on the provider's answer. A token already bound
    to a different shop is refused with ``BILLING_VERIFICATION_FAILED`` and
    audited.
    """
    tenant_id = principal.require_tenant()
    outcome = await billing.verify_play_purchase(
        tenant_id=tenant_id,
        user_id=principal.user_id,
        purchase_token=payload.purchase_token,
        product_id=payload.product_id,
        package_name=payload.package_name,
        channel=channel,
    )
    entitlements.invalidate(tenant_id)
    return _purchase_response(outcome, in_grace=False)


@router.post(
    "/restore",
    response_model=RestorePurchasesResponse,
    summary="Re-verify purchases the device still holds",
)
async def restore_purchases(
    payload: RestorePurchasesPayload,
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    entitlements: EntitlementsDep,
    channel: DistributionChannelDep,
    _: BillingManager,
) -> RestorePurchasesResponse:
    """Section 90's restore path, for a reinstall or a new device.

    Each purchase runs the full verification, so restore can never grant more
    than a fresh purchase would — including the wrong-tenant refusal.
    """
    tenant_id = principal.require_tenant()
    outcomes = await billing.restore_purchases(
        tenant_id=tenant_id,
        user_id=principal.user_id,
        purchases=[p.model_dump(exclude_none=True) for p in payload.purchases],
        channel=channel,
    )
    entitlements.invalidate(tenant_id)
    subscription = await billing.current_subscription(tenant_id)
    return RestorePurchasesResponse(
        restored=sum(1 for outcome in outcomes if outcome.granted),
        results=[_purchase_response(outcome, in_grace=False) for outcome in outcomes],
        subscription=_subscription_response(subscription) if subscription else None,
    )


@router.post(
    "/web/checkout", response_model=CheckoutResponse, summary="Start a web/direct checkout"
)
async def start_checkout(
    payload: CheckoutPayload,
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    registry: ProviderRegistryDep,
    flags: FeatureFlagsDep,
    channel: DistributionChannelDep,
    _: BillingManager,
) -> CheckoutResponse:
    """Begin a purchase outside the app store.

    Refused outright in a Play build (section 27.1): the channel policy decides,
    and this endpoint enforces the same decision the UI was told about, so a
    client that ignores ``GET /billing/channel`` gains nothing.
    """
    plan = _plan_or_400(payload.plan)
    provider_kind = _provider_or_400(payload.provider, default=BillingProviderKind.BKASH_WEB)

    disabled = {
        flag
        for _kind, flag in PROVIDER_FLAGS.items()
        if not await flags.is_enabled(flag, tenant_id=principal.tenant_id)
    }
    policy = registry.policy(channel, disabled_flags=frozenset(disabled))
    if provider_kind not in {state.provider for state in policy.purchasable}:
        raise ValidationError(
            f"{provider_kind} cannot be used from a {channel} build",
            details={"channel": str(channel), "provider": str(provider_kind)},
        )

    session = await billing.start_checkout(
        tenant_id=principal.require_tenant(), plan=plan, provider_kind=provider_kind
    )
    return CheckoutResponse(
        provider=str(session.provider),
        checkout_reference=session.checkout_reference,
        plan=str(session.plan),
        amount=MoneyAmount(amount_paisa=session.amount_paisa, currency=session.currency),
        redirect_url=session.redirect_url,
        expires_at=session.expires_at,
        client_payload=session.client_payload,
    )


@router.post("/web/confirm", response_model=PurchaseResultResponse, summary="Confirm a web payment")
async def confirm_checkout(
    payload: ConfirmCheckoutPayload,
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    entitlements: EntitlementsDep,
    channel: DistributionChannelDep,
    _: BillingManager,
) -> PurchaseResultResponse:
    """Ask the provider about our own reference, never trusting the callback."""
    tenant_id = principal.require_tenant()
    outcome = await billing.confirm_checkout(
        tenant_id=tenant_id,
        provider_kind=_provider_or_400(payload.provider, default=BillingProviderKind.BKASH_WEB),
        reference=payload.reference,
        channel=channel,
    )
    entitlements.invalidate(tenant_id)
    return _purchase_response(outcome, in_grace=False)


@router.post("/cancel", response_model=SubscriptionResponse, summary="Cancel the subscription")
async def cancel_subscription(
    payload: CancelSubscriptionPayload,
    principal: TenantPrincipal,
    billing: BillingServiceDep,
    entitlements: EntitlementsDep,
    _: BillingManager,
) -> SubscriptionResponse:
    """Stop renewing. Access continues to the end of the paid period.

    A seller-facing cancel is always ``at_period_end``: they have paid for the
    period and keep it. Only an admin path may end one immediately.
    """
    tenant_id = principal.require_tenant()
    subscription = await billing.cancel_subscription(
        tenant_id=tenant_id, at_period_end=True, reason=payload.reason
    )
    entitlements.invalidate(tenant_id)
    return _subscription_response(subscription)


# --------------------------------------------------------------------------- #
# Webhooks
# --------------------------------------------------------------------------- #


@router.post(
    "/webhooks/{provider}",
    status_code=status.HTTP_200_OK,
    summary="Provider billing notification",
    include_in_schema=False,
)
async def billing_webhook(
    request: Request,
    response: Response,
    billing: BillingServiceDep,
    provider: Annotated[str, Path(max_length=32)],
) -> dict[str, object]:
    """Ingest a provider notification.

    Unauthenticated by necessity, so:

    *   the signature is verified **before** the body is interpreted;
    *   an unverifiable signature returns 401 and the attempt is recorded;
    *   a replay returns 200 with ``duplicate`` counted, because a provider that
        gets an error will retry the same event forever.
    """
    try:
        provider_kind = BillingProviderKind(provider)
    except ValueError:
        response.status_code = status.HTTP_404_NOT_FOUND
        return {"accepted": False, "reason": "unknown_provider"}

    body = await request.body()
    headers = {key.lower(): value for key, value in request.headers.items()}
    result = await billing.handle_webhook(provider_kind=provider_kind, headers=headers, body=body)
    if not result.get("accepted"):
        response.status_code = status.HTTP_401_UNAUTHORIZED
    return result


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _plan_or_400(value: str) -> PlanCode:
    try:
        return PlanCode(value)
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a plan code") from exc


def _provider_or_400(value: str | None, *, default: BillingProviderKind) -> BillingProviderKind:
    if value is None:
        return default
    try:
        kind = BillingProviderKind(value)
    except ValueError as exc:
        raise ValidationError(f"'{value}' is not a billing provider") from exc
    if kind is BillingProviderKind.MANUAL_ADMIN:
        # Section 92: manual billing is support-controlled. Naming it from a
        # seller-facing route must not reach the provider at all.
        raise ValidationError("That billing provider is not available")
    return kind


def _subscription_response(
    subscription: Subscription, *, in_grace: bool | None = None
) -> SubscriptionResponse:
    grace = (
        in_grace
        if in_grace is not None
        else bool(subscription.grace_until and subscription.grace_until > utc_now())
    )
    reference = subscription.provider_reference
    return SubscriptionResponse(
        id=subscription.id,
        plan=subscription.plan_code,
        status=subscription.status,
        provider=subscription.source,
        distribution_channel=subscription.distribution_channel,
        current_period_start=subscription.current_period_start,
        current_period_end=subscription.current_period_end,
        trial_end=subscription.trial_end,
        grace_until=subscription.grace_until,
        cancel_at_period_end=subscription.cancel_at_period_end,
        in_grace=grace,
        status_reason=subscription.status_reason,
        verified_at=subscription.verified_at,
        last_synced_at=subscription.last_synced_at,
        provider_reference_suffix=reference[-4:] if reference else None,
    )


def _purchase_response(outcome: PurchaseOutcome, *, in_grace: bool) -> PurchaseResultResponse:
    return PurchaseResultResponse(
        granted=outcome.granted,
        result=str(outcome.result),
        detail=outcome.detail,
        subscription=(
            _subscription_response(outcome.subscription, in_grace=in_grace)
            if outcome.subscription is not None
            else None
        ),
    )


__all__ = ["router"]
