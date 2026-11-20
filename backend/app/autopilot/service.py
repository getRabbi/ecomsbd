"""Autopilot (V4 Phase 4): a ready chat draft becomes an order on its own (L11).

Opt-in, default off, behind two gates. Any failure leaves the draft with the
seller and records why; Autopilot never books — the order goes to customer
confirmation, and only a confirmed order is booked, by the WP2.4
"Customer confirmed → book courier" workflow (L10).

*   **Shop gate** (:func:`checklist`, shown in Settings and re-checked on every
    run): the default courier is connected, not known to be failing, switched
    on and live-verified; delivery rules are complete; voice is available or
    the seller works the call queue by hand; enough confirmed orders; consent;
    the plan; plus the switches (flag, shop toggle, pause).
*   **Order gate** (:func:`_order_gate`): one phone, a known zone, every item
    matched and in stock, the COD the shop's own rules calculate, no recent
    duplicate, a policy decision other than seller review, the daily cap.

Only ids and machine codes are stored or logged: no phone, name or text.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from app.autopilot.models import AutopilotDecision, AutopilotOutcome
from app.core.clock import business_date, business_day_bounds, ensure_utc, utc_now
from app.core.errors import AppError, ConflictError
from app.delivery_rules.models import ShopDeliveryRules

__all__ = [
    "MIN_CONFIRMED_ORDERS",
    "PAUSE_FOR",
    "CheckItem",
    "checklist",
    "convert_draft",
    "device_upload_allowed",
    "is_paused",
    "pause",
    "resume",
    "run_reason",
    "status_view",
    "today",
    "update",
]

#: Confirmed orders a shop needs before Autopilot may run (spec, fixed).
MIN_CONFIRMED_ORDERS = 20
#: "Pause 1 hour" on Home.
PAUSE_FOR = timedelta(hours=1)
#: The recipe key of the Autopilot workflow.
RECIPE_KEY = "autopilot"
#: Couriers whose booking would need location ids Autopilot cannot resolve yet.
_LOCATION_ID_COURIERS = frozenset({"pathao", "redx"})


@dataclass(frozen=True, slots=True)
class CheckItem:
    """One line of the Settings checklist."""

    key: str
    ok: bool
    #: The machine code a run is held with while this line fails.
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "ok": self.ok, "reason": self.reason, "detail": self.detail}


async def _row(db: AsyncSession, tenant_id: uuid.UUID) -> ShopDeliveryRules | None:
    return await db.scalar(
        sa.select(ShopDeliveryRules).where(ShopDeliveryRules.tenant_id == tenant_id)
    )


def _paused(row: ShopDeliveryRules | None, now: datetime | None = None) -> bool:
    return (
        row is not None
        and row.autopilot_paused_until is not None
        and ensure_utc(row.autopilot_paused_until) > (now or utc_now())
    )


async def is_paused(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """Whether the shop pressed "Pause 1 hour" (queued IVR calls hold meanwhile)."""
    return _paused(await _row(db, tenant_id))


async def _flag(db: AsyncSession, key: Any, tenant_id: uuid.UUID) -> bool:
    from app.common.feature_flags import FeatureFlagService

    return await FeatureFlagService(db).is_enabled(key, tenant_id=tenant_id)


# --------------------------------------------------------------------------- #
# Shop gate
# --------------------------------------------------------------------------- #


async def _courier_problem(
    db: AsyncSession, tenant_id: uuid.UUID, provider: str | None
) -> str | None:
    """Why the shop's default courier is not ready, or ``None``."""
    from app.common.feature_flags import COURIER_PROVIDER_FLAGS
    from app.common.provider_health import HealthState, ProviderHealth, scope_key_for
    from app.couriers.capabilities import load_manifest
    from app.couriers.models import CourierAccount, CourierAccountStatus

    if provider is None:
        return "NO_DEFAULT_COURIER"
    flag = COURIER_PROVIDER_FLAGS.get(provider)
    if flag is None or not await _flag(db, flag, tenant_id):
        return "PROVIDER_OFF"
    manifest = load_manifest(provider)
    if manifest is None or not manifest.is_live_verified:
        return "NOT_LIVE_VERIFIED"
    accounts = (
        await db.scalars(
            sa.select(CourierAccount).where(
                CourierAccount.tenant_id == tenant_id,
                CourierAccount.provider == provider,
                CourierAccount.status == str(CourierAccountStatus.CONNECTED),
            )
        )
    ).all()
    if not any(account.is_usable for account in accounts):
        return "NOT_CONNECTED"
    health = (
        await db.scalars(
            sa.select(ProviderHealth).where(
                ProviderHealth.provider == provider,
                ProviderHealth.scope_key.in_([scope_key_for(tenant_id), scope_key_for(None)]),
            )
        )
    ).all()
    bad = {str(HealthState.DOWN), str(HealthState.NEEDS_RECONNECT)}
    if any(row.state in bad or not row.allows_request() for row in health):
        return "UNHEALTHY"
    return None


async def checklist(
    db: AsyncSession, tenant_id: uuid.UUID, row: ShopDeliveryRules | None = None
) -> list[CheckItem]:
    """The shop-level gate, line by line (Settings shows it as is)."""
    from app.common.feature_flags import FlagKey
    from app.confirmations.calls import voice_gate
    from app.confirmations.models import ConfirmationState, OrderConfirmation
    from app.entitlements.catalog import Entitlement
    from app.entitlements.service import EntitlementService

    row = row if row is not None else await _row(db, tenant_id)
    items: list[CheckItem] = []

    plan = await EntitlementService(db).is_allowed(tenant_id, Entitlement.AUTOPILOT)
    items.append(CheckItem("PLAN", plan, "NOT_IN_PLAN"))

    engine = await _flag(db, FlagKey.CONFIRMATION_ENGINE_ENABLED, tenant_id)
    items.append(CheckItem("CONFIRMATIONS", engine, "CONFIRMATION_ENGINE_OFF"))

    provider = row.default_courier if row is not None else None
    courier = await _courier_problem(db, tenant_id, provider)
    items.append(
        CheckItem(
            "COURIER",
            courier is None,
            "COURIER_NOT_READY",
            {"provider": provider, "problem": courier},
        )
    )

    charges = (
        row is not None
        and row.inside_dhaka_charge_paisa > 0
        and row.dhaka_suburb_charge_paisa > 0
        and row.outside_dhaka_charge_paisa > 0
    )
    rules_on = await _flag(db, FlagKey.DELIVERY_RULES_ENABLED, tenant_id)
    items.append(
        CheckItem(
            "DELIVERY_RULES",
            bool(rules_on and charges and provider),
            "DELIVERY_RULES_INCOMPLETE",
            {"feature": rules_on, "zone_charges": bool(charges), "default_courier": bool(provider)},
        )
    )

    gate = await voice_gate(db, tenant_id, ignore_pause=True)
    manual = bool(row is not None and row.autopilot_manual_queue)
    items.append(
        CheckItem(
            "VOICE",
            gate.on or manual,
            "VOICE_UNAVAILABLE",
            {"voice": gate.on, "voice_reason": gate.reason, "manual_queue": manual},
        )
    )

    confirmed = int(
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(OrderConfirmation)
            .where(
                OrderConfirmation.tenant_id == tenant_id,
                OrderConfirmation.state == str(ConfirmationState.CONFIRMED),
            )
        )
        or 0
    )
    items.append(
        CheckItem(
            "HISTORY",
            confirmed >= MIN_CONFIRMED_ORDERS,
            "NOT_ENOUGH_CONFIRMED",
            {"confirmed": confirmed, "needed": MIN_CONFIRMED_ORDERS},
        )
    )

    consent = row is not None and row.autopilot_consent_at is not None
    items.append(CheckItem("CONSENT", consent, "CONSENT_MISSING"))
    return items


async def run_reason(
    db: AsyncSession, tenant_id: uuid.UUID, row: ShopDeliveryRules | None = None
) -> str | None:
    """Why Autopilot may not act for this shop right now, or ``None``.

    The switches first (kill switch, shop status, toggle, pause), then the
    checklist in its display order.
    """
    from app.common.feature_flags import FlagKey
    from app.tenants.models import Tenant, TenantStatus

    if not await _flag(db, FlagKey.AUTOPILOT_ENABLED, tenant_id):
        return "AUTOPILOT_DISABLED"
    status = await db.scalar(sa.select(Tenant.status).where(Tenant.id == tenant_id))
    if status != str(TenantStatus.ACTIVE):
        return "SHOP_INACTIVE"
    row = row if row is not None else await _row(db, tenant_id)
    if row is None or not row.autopilot_on:
        return "AUTOPILOT_OFF"
    if _paused(row):
        return "AUTOPILOT_PAUSED"
    for item in await checklist(db, tenant_id, row):
        if not item.ok:
            return item.reason
    return None


async def device_upload_allowed(db: AsyncSession, tenant_id: uuid.UUID) -> bool:
    """The heartbeat's ``autopilot_on``: may linked phones upload without a tap (L4)?

    The seller's switch and consent, not the whole checklist: a draft made
    from an upload is still held for the seller when another line fails.
    """
    from app.common.feature_flags import FlagKey
    from app.entitlements.catalog import Entitlement
    from app.entitlements.service import EntitlementService

    row = await _row(db, tenant_id)
    if row is None or not row.autopilot_on or row.autopilot_consent_at is None or _paused(row):
        return False
    if not await _flag(db, FlagKey.AUTOPILOT_ENABLED, tenant_id):
        return False
    return await EntitlementService(db).is_allowed(tenant_id, Entitlement.AUTOPILOT)


# --------------------------------------------------------------------------- #
# Order gate
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class _Line:
    product_id: uuid.UUID
    variant_id: uuid.UUID | None
    name: str | None
    quantity: int
    unit_price_paisa: int
    variant_label: str | None = None
    discount_paisa: int = 0
    note: str | None = None


@dataclass(frozen=True, slots=True)
class _Payload:
    """What the seller's quick-order sheet would send, from the draft itself."""

    phone: str
    customer_name: str | None
    address: str
    district: str | None
    items: list[_Line]
    cod_amount_paisa: int
    delivery_fee_paisa: int
    area: str | None = None
    discount_paisa: int = 0
    note: str | None = None


def _uuid(value: Any) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value)) if value else None
    except ValueError:
        return None


async def _lines(db: AsyncSession, draft: Any) -> tuple[str | None, list[_Line]]:
    """Every item matched to one product (and variant), with a catalogue price."""
    lines: list[_Line] = []
    if not draft.items:
        return "ITEM_NOT_MATCHED", lines
    for item in draft.items:
        match = item.get("match") or {}
        product_id = _uuid(match.get("product_id"))
        variant_status = match.get("variant_status", "NONE")
        variant_id = _uuid(match.get("variant_id"))
        if (
            match.get("status") != "MATCHED"
            or product_id is None
            or variant_status not in ("MATCHED", "NONE")
            or (variant_status == "MATCHED" and variant_id is None)
        ):
            return "ITEM_NOT_MATCHED", lines
        quantity = item.get("quantity") or 1
        price = match.get("unit_price_paisa")
        if type(quantity) is not int or quantity < 1:
            return "ITEM_NOT_MATCHED", lines
        if type(price) is not int or price <= 0:
            return "COD_NOT_CALCULATED", lines
        lines.append(
            _Line(
                product_id=product_id,
                variant_id=variant_id if variant_status == "MATCHED" else None,
                name=match.get("product_name") or item.get("name"),
                quantity=quantity,
                unit_price_paisa=price,
                variant_label=match.get("variant_name") if variant_status == "MATCHED" else None,
            )
        )
    return None, lines


async def _in_stock(db: AsyncSession, lines: list[_Line]) -> bool:
    """The automation ``items_in_stock`` rule: a product that does not track
    stock is not judged; a variant always is."""
    from app.products.models import Product, ProductVariant

    for line in lines:
        if line.variant_id is not None:
            stock = await db.scalar(
                sa.select(ProductVariant.stock_on_hand).where(ProductVariant.id == line.variant_id)
            )
        else:
            found = (
                await db.execute(
                    sa.select(Product.stock_on_hand, Product.stock_tracking_enabled).where(
                        Product.id == line.product_id
                    )
                )
            ).first()
            if found is None:
                return False
            if not found[1]:
                continue
            stock = found[0]
        if stock is None or stock < line.quantity:
            return False
    return True


async def _converted_today(db: AsyncSession, tenant_id: uuid.UUID) -> int:
    start, end = business_day_bounds(business_date(None), None)
    return int(
        await db.scalar(
            sa.select(sa.func.count())
            .select_from(AutopilotDecision)
            .where(
                AutopilotDecision.tenant_id == tenant_id,
                AutopilotDecision.outcome == str(AutopilotOutcome.CONVERTED),
                AutopilotDecision.created_at >= start,
                AutopilotDecision.created_at < end,
            )
        )
        or 0
    )


async def _order_gate(
    db: AsyncSession, draft: Any, row: ShopDeliveryRules
) -> tuple[str | None, _Payload | None]:
    from app.api.deps import get_hasher, get_vault
    from app.chat_orders import service as chats
    from app.chat_orders.models import OPEN_STATES, READY_FOR_REVIEW, ChatOrderDraft
    from app.common.phone import try_normalize_bd_phone
    from app.confirmations.facts import collect_for
    from app.confirmations.models import ConfirmationDecision
    from app.confirmations.policy import PolicyRules, decide
    from app.customers.service import CustomerService
    from app.delivery_rules.service import DeliveryRulesService
    from app.orders.cod import CodLine, calculate_cod
    from app.orders.parser.districts import find_district
    from app.orders.zones import resolve_zone

    if draft.status != READY_FOR_REVIEW or draft.missing_fields or not draft.address:
        return "DRAFT_NOT_READY", None
    if draft.uncertain_fields:
        return "FIELDS_UNCERTAIN", None
    numbers = {n.e164 for n in (try_normalize_bd_phone(p) for p in draft.phones or []) if n}
    selected = try_normalize_bd_phone(draft.selected_phone)
    if (
        len(numbers) != 1
        or len(draft.phones) != 1
        or selected is None
        or selected.e164 not in numbers
    ):
        return "PHONE_NOT_SINGLE", None
    phone = selected.e164
    problem, lines = await _lines(db, draft)
    if problem is not None:
        return problem, None
    zone = resolve_zone(draft.address)
    if zone is None:
        return "ZONE_UNRESOLVED", None
    if row.default_courier in _LOCATION_ID_COURIERS:
        # Their booking sends location ids Autopilot cannot yet resolve and
        # check (decision 2026-11-20): held, never booked on a guessed area.
        return "LOCATION_UNRESOLVED", None
    rules = await DeliveryRulesService(db).get(draft.tenant_id)
    charge = rules.zone_charge(zone)
    calculated = (
        calculate_cod(
            [CodLine(unit_price_paisa=x.unit_price_paisa, quantity=x.quantity) for x in lines],
            zone_charge_paisa=charge,
        )
        if charge is not None
        else None
    )
    if calculated is None:
        return "COD_NOT_CALCULATED", None
    if draft.cod_source == "CHAT_MISMATCH" or draft.cod_amount_paisa != calculated:
        return "COD_MISMATCH", None
    if not await _in_stock(db, lines):
        return "OUT_OF_STOCK", None
    if draft.duplicate_of_order_id is not None or "DUPLICATE_ORDER" in (draft.warnings or []):
        return "DUPLICATE_RECENT", None
    other = await db.scalar(
        sa.select(ChatOrderDraft.id)
        .where(
            ChatOrderDraft.id != draft.id,
            ChatOrderDraft.status.in_(OPEN_STATES),
            ChatOrderDraft.selected_phone == draft.selected_phone,
        )
        .limit(1)
    )
    if other is not None or await chats._recent_order(db, phone) is not None:
        return "DUPLICATE_RECENT", None
    customers = CustomerService(db, hasher=get_hasher(), vault=get_vault())
    customer = await customers.find_by_phone(phone)
    facts = await collect_for(db, customer.id if customer else None, calculated)
    decision, _reason = decide(facts, PolicyRules.from_values(rules))
    if decision is ConfirmationDecision.SELLER_REVIEW:
        return "POLICY_SELLER_REVIEW", None
    if await _converted_today(db, draft.tenant_id) >= row.autopilot_daily_cap:
        return "DAILY_CAP_REACHED", None
    return None, _Payload(
        phone=phone,
        customer_name=draft.customer_name,
        address=draft.address,
        district=find_district(draft.address),
        items=lines,
        cod_amount_paisa=calculated,
        delivery_fee_paisa=charge or 0,
    )


# --------------------------------------------------------------------------- #
# Convert
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Converted:
    outcome: AutopilotOutcome
    order_id: uuid.UUID | None
    customer_id: uuid.UUID | None
    reason: str | None


async def convert_draft(
    db: AsyncSession, draft_id: uuid.UUID, *, execution_id: uuid.UUID | None
) -> Converted:
    """Run both gates and turn the draft into an order, once per draft.

    A draft already decided returns that decision unchanged. Every refusal
    from the order service (a plan limit, a product that changed) holds the
    draft too: the seller sees it as before, with the reason.
    """
    from app.api.deps import get_hasher, get_vault
    from app.chat_orders import service as chats
    from app.customers.service import CustomerService
    from app.orders.models import Order
    from app.orders.service import OrderService
    from app.product_events.models import ProductEventName
    from app.product_events.service import ProductEventService

    earlier = await db.scalar(
        sa.select(AutopilotDecision).where(AutopilotDecision.draft_id == draft_id)
    )
    if earlier is not None:
        order = await db.get(Order, earlier.order_id) if earlier.order_id else None
        return Converted(
            AutopilotOutcome(earlier.outcome),
            earlier.order_id,
            order.customer_id if order is not None else None,
            earlier.reason_code,
        )
    draft = await chats.get_draft(db, draft_id, lock=True)
    tenant_id = draft.tenant_id
    row = await _row(db, tenant_id)
    reason = await run_reason(db, tenant_id, row)
    payload: _Payload | None = None
    if reason is None and row is not None:
        reason, payload = await _order_gate(db, draft, row)
    order = None
    if reason is None and payload is not None:
        orders = OrderService(
            db, customers=CustomerService(db, hasher=get_hasher(), vault=get_vault())
        )
        try:
            async with db.begin_nested():
                order = (await chats.confirm(db, draft, payload, orders, None)).order
        except AppError:
            reason, order = "ORDER_REFUSED", None
    decision = AutopilotDecision(
        tenant_id=tenant_id,
        draft_id=draft.id,
        outcome=str(AutopilotOutcome.CONVERTED if order else AutopilotOutcome.HELD),
        reason_code=None if order else reason,
        order_id=order.id if order else None,
        execution_id=execution_id,
    )
    db.add(decision)
    await db.flush()
    if order is None:
        return Converted(AutopilotOutcome.HELD, None, None, reason)
    await ProductEventService(db).core_action(
        ProductEventName.FIRST_AUTOPILOT_ORDER, tenant_id=tenant_id
    )
    return Converted(AutopilotOutcome.CONVERTED, order.id, order.customer_id, None)


# --------------------------------------------------------------------------- #
# The switch
# --------------------------------------------------------------------------- #


async def _workflow(db: AsyncSession) -> dict[str, bool]:
    from app.automation.models import AutomationRule

    rows = (
        await db.scalars(
            sa.select(AutomationRule.enabled).where(AutomationRule.recipe_key == RECIPE_KEY)
        )
    ).all()
    return {"installed": bool(rows), "enabled": any(rows)}


async def status_view(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    row = await _row(db, tenant_id)
    items = await checklist(db, tenant_id, row)
    paused = _paused(row)
    return {
        "on": bool(row is not None and row.autopilot_on),
        "consent_at": row.autopilot_consent_at.isoformat()
        if row is not None and row.autopilot_consent_at
        else None,
        "paused_until": ensure_utc(row.autopilot_paused_until).isoformat()
        if row is not None and paused and row.autopilot_paused_until
        else None,
        "manual_queue": bool(row is not None and row.autopilot_manual_queue),
        "daily_cap": row.autopilot_daily_cap if row is not None else None,
        "ready": all(item.ok for item in items),
        "checklist": [item.as_dict() for item in items],
        "reason": await run_reason(db, tenant_id, row),
        "workflow": await _workflow(db),
    }


async def _audit(db: AsyncSession, action: Any, tenant_id: uuid.UUID, **context: Any) -> None:
    from app.common.audit import record_audit

    await record_audit(
        db, action, entity_type="autopilot", entity_id=tenant_id, context=context or None
    )


async def update(
    db: AsyncSession,
    tenant_id: uuid.UUID,
    actor_id: uuid.UUID,
    *,
    on: bool | None,
    manual_queue: bool | None,
    consent: bool,
) -> dict[str, Any]:
    """Change the switch. Turning on needs every checklist line (``409`` lists
    the failing ones, and nothing is saved); consent is recorded once."""
    from app.common.audit import AuditAction

    row = await db.scalar(
        sa.select(ShopDeliveryRules)
        .where(ShopDeliveryRules.tenant_id == tenant_id)
        .with_for_update()
    )
    if row is None:
        # Never created here: saving the rows' defaults would change how
        # chat drafts are priced (WP0.4). The seller saves delivery rules first.
        raise ConflictError(
            "Save your delivery charges first",
            details={"code": "AUTOPILOT_NOT_READY", "failing": ["DELIVERY_RULES_INCOMPLETE"]},
        )
    changed: list[str] = []
    if manual_queue is not None and manual_queue != row.autopilot_manual_queue:
        row.autopilot_manual_queue = manual_queue
        changed.append("manual_queue")
    if consent and row.autopilot_consent_at is None:
        row.autopilot_consent_at, row.autopilot_consent_by = utc_now(), actor_id
        changed.append("consent")
    if on is True and not row.autopilot_on:
        failing = [item.reason for item in await checklist(db, tenant_id, row) if not item.ok]
        if failing:
            raise ConflictError(
                "Autopilot is not ready for this shop",
                details={"code": "AUTOPILOT_NOT_READY", "failing": failing},
            )
        row.autopilot_on = True
        changed.append("on")
    elif on is False and row.autopilot_on:
        row.autopilot_on, row.autopilot_paused_until = False, None
        changed.append("off")
    await db.flush()
    if changed:
        await _audit(db, AuditAction.AUTOPILOT_UPDATED, tenant_id, changed=changed)
    return await status_view(db, tenant_id)


async def pause(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Stop for an hour: no draft is converted and queued IVR calls hold
    (they stay queued, nothing is cancelled). Pressing it again restarts the hour."""
    from app.common.audit import AuditAction

    row = await db.scalar(
        sa.select(ShopDeliveryRules)
        .where(ShopDeliveryRules.tenant_id == tenant_id)
        .with_for_update()
    )
    if row is None or not row.autopilot_on:
        raise ConflictError("Autopilot is not on", details={"code": "AUTOPILOT_OFF"})
    row.autopilot_paused_until = utc_now() + PAUSE_FOR
    await db.flush()
    await _audit(db, AuditAction.AUTOPILOT_PAUSED, tenant_id)
    return await status_view(db, tenant_id)


async def resume(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    from app.common.audit import AuditAction

    row = await db.scalar(
        sa.select(ShopDeliveryRules)
        .where(ShopDeliveryRules.tenant_id == tenant_id)
        .with_for_update()
    )
    if row is not None and row.autopilot_paused_until is not None:
        row.autopilot_paused_until = None
        await db.flush()
        await _audit(db, AuditAction.AUTOPILOT_RESUMED, tenant_id)
    return await status_view(db, tenant_id)


# --------------------------------------------------------------------------- #
# Home card
# --------------------------------------------------------------------------- #


async def today(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    """Today's orders by Autopilot and how their confirmations stand, plus
    the last five decisions with their reason."""
    from app.confirmations.models import ConfirmationState, OrderConfirmation
    from app.orders.models import Order, OrderStatus

    day = business_date(None)
    start, end = business_day_bounds(day, None)
    rows = (
        await db.scalars(
            sa.select(AutopilotDecision).where(
                AutopilotDecision.tenant_id == tenant_id,
                AutopilotDecision.created_at >= start,
                AutopilotDecision.created_at < end,
            )
        )
    ).all()
    order_ids = [r.order_id for r in rows if r.order_id is not None]
    latest: dict[uuid.UUID, str] = {}
    cancelled_orders: set[uuid.UUID] = set()
    if order_ids:
        for order_id, state in (
            await db.execute(
                sa.select(OrderConfirmation.order_id, OrderConfirmation.state)
                .where(OrderConfirmation.order_id.in_(order_ids))
                .order_by(OrderConfirmation.created_at, OrderConfirmation.id)
            )
        ).all():
            latest[order_id] = state
        cancelled_orders = set(
            (
                await db.scalars(
                    sa.select(Order.id).where(
                        Order.id.in_(order_ids), Order.status == str(OrderStatus.CANCELLED)
                    )
                )
            ).all()
        )
    confirmed = cancelled = 0
    for order_id in order_ids:
        state = latest.get(order_id)
        if order_id in cancelled_orders or state == str(ConfirmationState.CANCELLED):
            cancelled += 1
        elif state == str(ConfirmationState.CONFIRMED):
            confirmed += 1
    recent_rows = (
        await db.scalars(
            sa.select(AutopilotDecision)
            .where(AutopilotDecision.tenant_id == tenant_id)
            .order_by(AutopilotDecision.created_at.desc(), AutopilotDecision.id.desc())
            .limit(5)
        )
    ).all()
    numbers: dict[uuid.UUID, str] = {}
    recent_orders = [r.order_id for r in recent_rows if r.order_id is not None]
    if recent_orders:
        found = await db.execute(
            sa.select(Order.id, Order.order_number).where(Order.id.in_(recent_orders))
        )
        numbers = {oid: str(number) for oid, number in found.all()}
    return {
        "date": day.isoformat(),
        "converted": len(order_ids),
        "confirmed": confirmed,
        "cancelled": cancelled,
        "waiting": len(order_ids) - confirmed - cancelled,
        "held": sum(1 for r in rows if r.outcome == str(AutopilotOutcome.HELD)),
        "recent": [
            {
                "draft_id": str(r.draft_id),
                "outcome": r.outcome,
                "reason": r.reason_code,
                "order_id": str(r.order_id) if r.order_id else None,
                "order_number": numbers.get(r.order_id) if r.order_id else None,
                "at": ensure_utc(r.created_at).isoformat(),
            }
            for r in recent_rows
        ],
    }
