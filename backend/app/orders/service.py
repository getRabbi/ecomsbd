"""Order service.

Two rules from the master spec shape every method here.

**Creating an order never books a courier** (sections 11, 62.17). They are
separate explicit actions, because a courier create is an irreversible external
side effect and an order is just a record. This service has no provider
dependency at all.

**Stock moves when the policy says it moves, and always through the ledger**
(sections 10.4, 20). The default decrement moment is a successful booking, so
confirming an order does not touch stock; the movement is written by the booking
path in Phase C. Cancelling an order restores only what was actually decremented.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.outbox import OutboxTopic, enqueue
from app.common.pagination import Cursor, apply_cursor
from app.common.phone import normalize_digits, try_normalize_bd_phone
from app.core.clock import business_date as business_date_for
from app.core.context import current_context, current_tenant_id
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ValidationError
from app.core.ids import new_id
from app.customers.models import Customer
from app.customers.service import CustomerService, normalize_address_text
from app.entitlements.catalog import Entitlement
from app.entitlements.service import EntitlementService
from app.orders.duplicates import DuplicateCheck, detect_duplicates
from app.orders.models import (
    Order,
    OrderChannel,
    OrderItem,
    OrderStatus,
    can_transition,
)
from app.orders.numbering import (
    DEFAULT_ORDER_PREFIX,
    MAX_ALLOCATION_ATTEMPTS,
    allocate_order_number,
    parse_order_number,
)
from app.products.models import Product, ProductVariant
from app.products.service import StockService

__all__ = ["OrderDraft", "OrderItemDraft", "OrderService"]


@dataclass(slots=True)
class OrderItemDraft:
    """One requested line."""

    product_id: uuid.UUID | None = None
    #: Required when there is no ``product_id`` (section 20 allows free-text).
    name: str | None = None
    quantity: int = 1
    unit_price_paisa: int | None = None
    discount_paisa: int = 0
    variant_label: str | None = None
    note: str | None = None
    #: Required for a product with variants: stock is deducted per variant.
    variant_id: uuid.UUID | None = None


@dataclass(slots=True)
class OrderDraft:
    """A requested order."""

    phone: str
    items: list[OrderItemDraft] = field(default_factory=list)
    customer_name: str | None = None
    address: str | None = None
    district: str | None = None
    area: str | None = None
    cod_amount_paisa: int | None = None
    discount_paisa: int = 0
    delivery_fee_paisa: int = 0
    note: str | None = None
    source_text: str | None = None
    channel: OrderChannel = OrderChannel.MANUAL
    #: Device-minted id. Makes an offline create replay-safe.
    client_id: uuid.UUID | None = None
    order_number_prefix: str = DEFAULT_ORDER_PREFIX
    tenant_timezone: str | None = None


class OrderService:
    """Create and maintain orders."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        customers: CustomerService,
        entitlements: EntitlementService | None = None,
    ) -> None:
        self._db = session
        self._customers = customers
        self._stock = StockService(session)
        # Built here rather than required from every caller, so metering is the
        # default and there is no "forgot to pass it" path that silently ships
        # an unenforced quota. Master spec section 43.
        self._entitlements = entitlements or EntitlementService(session)

    # ------------------------------------------------------------- create ---

    async def create(
        self, draft: OrderDraft, *, check_duplicates: bool = True
    ) -> tuple[Order, DuplicateCheck | None]:
        """Create an order and report anything that looks like a duplicate.

        The duplicate check is advisory (section 9). The order is created either
        way, and the caller shows the warning; blocking would break the
        legitimate case of a customer ordering twice in a day.
        """
        if not draft.items:
            raise ValidationError("An order needs at least one item")

        # Section 26's orders/month cap, spent before the record exists. Every
        # creation path — the API, offline sync and CSV import — goes through
        # here, so there is one place the quota is enforced and one place it
        # could be bypassed if this moved into a controller.
        tenant_id = current_tenant_id()
        if tenant_id is None:
            raise ForbiddenError("An order can only be created inside a shop")
        await self._entitlements.consume(tenant_id, Entitlement.ORDERS_MONTHLY_LIMIT)

        number = try_normalize_bd_phone(draft.phone)
        if number is None:
            raise ValidationError(
                "A valid Bangladeshi mobile number is required",
                details={"field": "phone"},
            )

        # An offline replay of the same queued mutation must find the order it
        # already created rather than making a second one.
        client_id = draft.client_id or new_id()
        existing = (
            await self._db.execute(sa.select(Order).where(Order.client_id == client_id))
        ).scalar_one_or_none()
        if existing is not None:
            return existing, None

        customer, _ = await self._customers.get_or_create(
            phone=number.e164,
            name=draft.customer_name,
            address=draft.address,
        )
        if customer.is_blocked:
            # A seller-private block. Surfaced as a conflict the seller can
            # override by unblocking, never as a silent refusal.
            raise ConflictError(
                "This customer is blocked in your shop",
                details={"customer_id": str(customer.id)},
            )

        items = await self._build_items(draft.items)
        subtotal = sum(item.line_total_paisa for item in items)
        cod_amount = (
            draft.cod_amount_paisa
            if draft.cod_amount_paisa is not None
            else max(0, subtotal - draft.discount_paisa + draft.delivery_fee_paisa)
        )

        duplicates: DuplicateCheck | None = None
        if check_duplicates:
            duplicates = await detect_duplicates(
                self._db,
                phone_hmac=customer.phone_search_hmac,
                cod_amount_paisa=cod_amount,
                item_names=[item.product_name for item in items],
                customer_id=customer.id,
            )

        address = customer.default_address()
        order = await self._insert_with_number(
            draft=draft,
            customer=customer,
            items=items,
            subtotal=subtotal,
            cod_amount=cod_amount,
            address_raw=draft.address or (address.raw_address if address else None),
        )

        await self._customers.note_order_placed(customer)

        await record_audit(
            self._db,
            AuditAction.ORDER_CREATED,
            entity_type="order",
            entity_id=order.id,
            context={
                "order_number": order.order_number,
                "channel": str(draft.channel),
                "item_count": len(items),
            },
        )
        if duplicates is not None and duplicates.possible_duplicate:
            await record_audit(
                self._db,
                AuditAction.ORDER_DUPLICATE_WARNED,
                entity_type="order",
                entity_id=order.id,
                context={
                    "candidates": [candidate.order_number for candidate in duplicates.candidates]
                },
            )
        await enqueue(
            self._db,
            OutboxTopic.ORDER_CREATED,
            {"order_id": str(order.id), "order_number": order.order_number},
        )
        await self._db.flush()
        return order, duplicates

    async def _insert_with_number(
        self,
        *,
        draft: OrderDraft,
        customer: Customer,
        items: list[OrderItem],
        subtotal: int,
        cod_amount: int,
        address_raw: str | None,
    ) -> Order:
        """Insert the order, retrying if another device took the same number.

        Each attempt runs in a savepoint so a unique violation rolls back only
        the failed insert, leaving the surrounding transaction — the customer
        record, its address — intact.
        """
        today = business_date_for(draft.tenant_timezone)
        last_error: IntegrityError | None = None

        for attempt in range(MAX_ALLOCATION_ATTEMPTS):
            order_number = await allocate_order_number(
                self._db,
                business_date=today,
                prefix=draft.order_number_prefix,
                attempt=attempt,
            )
            order = Order(
                order_number=order_number,
                client_id=draft.client_id or new_id(),
                customer_id=customer.id,
                customer_phone_hmac=customer.phone_search_hmac,
                customer_name=customer.name or draft.customer_name,
                customer_phone_masked=customer.phone_masked,
                delivery_address_raw=address_raw,
                delivery_address_normalized=(
                    normalize_address_text(address_raw) if address_raw else None
                ),
                delivery_district=draft.district,
                delivery_area=draft.area,
                status=OrderStatus.DRAFT,
                channel=draft.channel,
                business_date=today,
                subtotal_paisa=subtotal,
                discount_paisa=draft.discount_paisa,
                delivery_fee_paisa=draft.delivery_fee_paisa,
                cod_amount_paisa=cod_amount,
                note=draft.note,
                source_text=draft.source_text,
                created_by_user_id=current_context().user_id,
            )
            order.items = items

            try:
                async with self._db.begin_nested():
                    self._db.add(order)
                    await self._db.flush()
            except IntegrityError as error:
                last_error = error
                # Another device won this number. Detach and try the next.
                self._db.expunge(order)
                continue
            return order

        raise ConflictError("Could not allocate an order number. Please try again.") from last_error

    async def _build_items(self, drafts: list[OrderItemDraft]) -> list[OrderItem]:
        """Resolve drafts into items, snapshotting product economics."""
        items: list[OrderItem] = []

        for position, line in enumerate(drafts):
            if line.quantity < 1:
                raise ValidationError("Item quantity must be at least 1")

            product: Product | None = None
            if line.product_id is not None:
                product = await self._db.get(Product, line.product_id)
                if product is None:
                    raise NotFoundError(
                        "Product not found", details={"product_id": str(line.product_id)}
                    )

            variant = await self._resolve_variant(product, line.variant_id)

            name = (line.name or (product.name if product else "")).strip()
            if not name:
                raise ValidationError("Each item needs a product or a name")

            unit_price = line.unit_price_paisa
            if unit_price is None:
                if variant is not None and variant.price_paisa is not None:
                    unit_price = variant.price_paisa
                else:
                    unit_price = product.default_selling_price_paisa if product else 0

            unit_cost = product.cost_paisa if product else 0
            if variant is not None and variant.cost_paisa is not None:
                unit_cost = variant.cost_paisa

            items.append(
                OrderItem(
                    product_id=product.id if product else None,
                    variant_id=variant.id if variant else None,
                    position=position,
                    product_name=name,
                    sku=(variant.sku if variant and variant.sku else None)
                    or (product.sku if product else None),
                    variant_label=variant.name if variant else line.variant_label,
                    quantity=line.quantity,
                    unit_price_paisa=unit_price,
                    # The snapshot that keeps historical profit correct when the
                    # product's cost changes later (section 18.2).
                    unit_cost_snapshot_paisa=unit_cost,
                    discount_paisa=line.discount_paisa,
                    note=line.note,
                )
            )
        return items

    async def _resolve_variant(
        self, product: Product | None, variant_id: uuid.UUID | None
    ) -> ProductVariant | None:
        """The variant a line sells, which a variant product must name.

        A line for a variant product without one would have no stock to deduct
        at dispatch, and guessing a size is how a shelf stops matching the app.
        """
        if product is None or not product.has_variants:
            if variant_id is not None and (product is None or not product.has_variants):
                raise ValidationError("That product has no variants")
            return None
        if variant_id is None:
            raise ValidationError(
                f"Choose a variant of {product.name}",
                details={"product_id": str(product.id)},
            )
        variant = await self._db.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id:
            raise NotFoundError("Variant not found", details={"variant_id": str(variant_id)})
        return variant

    # -------------------------------------------------------------- read ----

    async def get(self, order_id: uuid.UUID) -> Order:
        order = await self._db.get(Order, order_id)
        if order is None or order.deleted_at is not None:
            raise NotFoundError("Order not found")
        return order

    async def list_orders(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        status: OrderStatus | None = None,
        customer_id: uuid.UUID | None = None,
        search: str | None = None,
    ) -> list[Order]:
        """List orders newest first.

        Search covers order number, phone and customer name (section 129), with
        Bangla numerals normalised first so a seller searching `০১৭…` finds the
        same orders as one searching `017…`.
        """
        stmt = sa.select(Order).where(Order.deleted_at.is_(None))

        if status is not None:
            stmt = stmt.where(Order.status == status)
        if customer_id is not None:
            stmt = stmt.where(Order.customer_id == customer_id)

        if search:
            term = normalize_digits(search.strip())
            conditions: list[sa.ColumnElement[bool]] = [
                Order.order_number.ilike(f"%{term}%"),
                sa.func.lower(sa.func.coalesce(Order.customer_name, "")).like(f"%{term.lower()}%"),
            ]
            if parse_order_number(term) is not None:
                conditions.append(Order.order_number == term.upper())

            number = try_normalize_bd_phone(term)
            if number is not None:
                conditions.append(
                    Order.customer_phone_hmac == self._customers.phone_search_hash(number.e164)
                )
            stmt = stmt.where(sa.or_(*conditions))

        stmt = apply_cursor(stmt, Order, cursor)

        stmt = stmt.order_by(Order.created_at.desc(), Order.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    # ------------------------------------------------------------ mutate ----

    async def update(
        self,
        order_id: uuid.UUID,
        *,
        items: list[OrderItemDraft] | None = None,
        cod_amount_paisa: int | None = None,
        discount_paisa: int | None = None,
        delivery_fee_paisa: int | None = None,
        note: str | None = None,
        address: str | None = None,
        district: str | None = None,
        area: str | None = None,
        expected_version: int | None = None,
    ) -> Order:
        """Edit an order that has not entered fulfilment.

        ``expected_version`` implements section 37's version-based conflict
        rule: a client that read version 3 and sends version 3 wins; one that
        read an older copy is told its edit conflicts instead of silently
        overwriting someone else's change.
        """
        order = await self.get(order_id)

        if not order.is_editable:
            raise ConflictError(
                "This order can no longer be edited",
                details={"status": order.status},
            )
        if expected_version is not None and expected_version != order.version:
            raise ConflictError(
                "This order changed since you loaded it",
                details={"expected_version": expected_version, "version": order.version},
            )

        changed: list[str] = []

        if items is not None:
            order.items = await self._build_items(items)
            order.subtotal_paisa = sum(item.line_total_paisa for item in order.items)
            changed.append("items")
        if discount_paisa is not None:
            order.discount_paisa = discount_paisa
            changed.append("discount")
        if delivery_fee_paisa is not None:
            order.delivery_fee_paisa = delivery_fee_paisa
            changed.append("delivery_fee")
        if cod_amount_paisa is not None:
            order.cod_amount_paisa = cod_amount_paisa
            changed.append("cod_amount")
        elif "items" in changed or "discount" in changed or "delivery_fee" in changed:
            order.cod_amount_paisa = max(
                0,
                order.subtotal_paisa - order.discount_paisa + order.delivery_fee_paisa,
            )
        if note is not None:
            order.note = note
            changed.append("note")
        if address is not None:
            order.delivery_address_raw = address
            order.delivery_address_normalized = normalize_address_text(address)
            changed.append("address")
        if district is not None:
            order.delivery_district = district
            changed.append("district")
        if area is not None:
            order.delivery_area = area
            changed.append("area")

        if changed:
            order.version += 1
            await record_audit(
                self._db,
                AuditAction.ORDER_UPDATED,
                entity_type="order",
                entity_id=order.id,
                context={"changed": sorted(changed)},
            )
        await self._db.flush()
        return order

    async def transition(
        self,
        order_id: uuid.UUID,
        target: OrderStatus,
        *,
        reason: str | None = None,
    ) -> Order:
        """Move an order to a new status, rejecting illegal jumps."""
        order = await self.get(order_id)
        current = order.order_status

        if current == target:
            return order
        if not can_transition(current, target):
            raise ConflictError(
                f"An order cannot go from {current} to {target}",
                details={"from": str(current), "to": str(target)},
            )

        order.mark_status(target)
        if target is OrderStatus.CANCELLED:
            order.cancellation_reason = reason

        await record_audit(
            self._db,
            AuditAction.ORDER_STATUS_CHANGED
            if target is not OrderStatus.CANCELLED
            else AuditAction.ORDER_CANCELLED,
            entity_type="order",
            entity_id=order.id,
            reason=reason,
            context={"from": str(current), "to": str(target)},
        )
        await enqueue(
            self._db,
            OutboxTopic.ORDER_STATUS_CHANGED,
            {
                "order_id": str(order.id),
                "from": str(current),
                "to": str(target),
            },
        )
        await self._db.flush()
        return order

    async def check_duplicates_for(
        self,
        *,
        phone: str,
        cod_amount_paisa: int,
        item_names: list[str] | None = None,
        exclude_order_id: uuid.UUID | None = None,
    ) -> DuplicateCheck:
        """Duplicate check without creating anything.

        Called while the seller is still typing, so the warning appears before
        they commit rather than after.
        """
        number = try_normalize_bd_phone(phone)
        if number is None:
            return DuplicateCheck(possible_duplicate=False)
        return await detect_duplicates(
            self._db,
            phone_hmac=self._customers.phone_search_hash(number.e164),
            cod_amount_paisa=cod_amount_paisa,
            item_names=item_names,
            exclude_order_id=exclude_order_id,
        )
