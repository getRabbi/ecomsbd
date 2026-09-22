"""Product and stock services.

Every stock change goes through :meth:`StockService.record_movement`. There is
no other write path to ``Product.stock_on_hand`` or ``ProductVariant.stock_on_hand``,
which is what makes the ledger and the running totals impossible to disagree
with each other.

Concurrency (master spec section 76): two devices adjusting the same product at
once must not lose one of the adjustments. The balance is changed by a relative
``SET stock_on_hand = stock_on_hand + :delta`` statement rather than a
read-modify-write, so any interleaving produces the right total on both
PostgreSQL and SQLite. See :meth:`StockService._apply_delta`.

Idempotency (V2.2): a movement may carry an ``idempotency_key``, unique per
shop in the database. A retried request, a re-run dispatch, a resumed import or
a second tap on "restock" finds the first movement and returns it instead of
moving stock again.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, ErrorCode, NotFoundError, ValidationError
from app.db.tenancy import TENANT_CHECKED
from app.products.models import (
    MOVEMENT_SIGN,
    Product,
    ProductVariant,
    StockMovement,
    StockMovementReason,
    StockMovementSource,
)

__all__ = [
    "ProductService",
    "StockAdjustment",
    "StockService",
    "StockSummary",
    "insufficient_stock",
    "low_stock_clause",
    "out_of_stock_clause",
]


@dataclass(frozen=True, slots=True)
class StockAdjustment:
    """A requested change to stock."""

    product_id: uuid.UUID
    quantity_delta: int
    reason: StockMovementReason
    source: StockMovementSource = StockMovementSource.SELLER
    note: str | None = None
    order_id: uuid.UUID | None = None
    order_item_id: uuid.UUID | None = None
    consignment_id: uuid.UUID | None = None
    occurred_at: datetime | None = None
    #: Required when the product has variants; refused when it has none.
    variant_id: uuid.UUID | None = None
    #: Same key, same movement: the second write returns the first.
    idempotency_key: str | None = None
    reference: str | None = None
    unit_cost_paisa: int | None = None


@dataclass(frozen=True, slots=True)
class StockSummary:
    """Shop-wide stock position. Cheap aggregates, no row dump."""

    tracked_products: int
    total_units: int
    low_stock_items: int
    out_of_stock_items: int


def insufficient_stock(
    *, product: Product, variant: ProductVariant | None, available: int, requested: int
) -> ConflictError:
    """The one error every "not enough stock" path raises."""
    label = f"{product.name} ({variant.name})" if variant is not None else product.name
    return ConflictError(
        f"Not enough stock for {label}: {available} available, {requested} needed",
        code=ErrorCode.INSUFFICIENT_STOCK,
        details={
            "product_id": str(product.id),
            "variant_id": str(variant.id) if variant is not None else None,
            "stock_on_hand": available,
            "requested": requested,
        },
    )


class StockService:
    """The only writer of stock."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session

    async def record_movement(
        self, adjustment: StockAdjustment, *, allow_negative: bool = False
    ) -> StockMovement:
        """Append a movement and update the running balance atomically.

        ``allow_negative`` exists because overselling is a real seller decision
        (master spec section 76 calls for an explicit oversell setting), not
        something to silently permit or silently block.
        """
        if adjustment.quantity_delta == 0:
            raise ValidationError("A stock movement must change the quantity")

        expected_sign = MOVEMENT_SIGN[adjustment.reason]
        if expected_sign is not None:
            actual_sign = 1 if adjustment.quantity_delta > 0 else -1
            if actual_sign != expected_sign:
                direction = "increase" if expected_sign > 0 else "decrease"
                raise ValidationError(
                    f"{adjustment.reason} must {direction} stock, "
                    f"got {adjustment.quantity_delta:+d}",
                    details={"reason": str(adjustment.reason)},
                )

        if adjustment.reason is StockMovementReason.MANUAL_ADJUSTMENT and not (
            adjustment.note and adjustment.note.strip()
        ):
            # An unexplained hand-correction to stock cannot be distinguished
            # from a bug six months later.
            raise ValidationError("A manual stock adjustment requires a note")

        product = await self._lock_product(adjustment.product_id)

        if not product.stock_tracking_enabled:
            raise ConflictError(
                "This product does not track stock",
                details={"product_id": str(product.id)},
            )

        if adjustment.idempotency_key:
            existing = await self._by_key(adjustment.idempotency_key)
            if existing is not None:
                return self._replayed(existing, adjustment)

        variant = await self._resolve_variant(product, adjustment.variant_id)

        # The movement is inserted first, inside a savepoint: that claims the
        # idempotency key before any stock moves. Two racing requests with the
        # same key cannot both get past the unique constraint, and the loser's
        # savepoint rolls its stock change back with it.
        savepoint = await self._db.begin_nested()
        try:
            movement = StockMovement(
                tenant_id=product.tenant_id,
                product_id=product.id,
                variant_id=variant.id if variant is not None else None,
                order_id=adjustment.order_id,
                order_item_id=adjustment.order_item_id,
                consignment_id=adjustment.consignment_id,
                quantity_delta=adjustment.quantity_delta,
                balance_after=0,
                reason=adjustment.reason,
                source=adjustment.source,
                actor_user_id=current_context().user_id,
                note=adjustment.note,
                reference=adjustment.reference,
                unit_cost_paisa=adjustment.unit_cost_paisa,
                idempotency_key=adjustment.idempotency_key,
                occurred_at=adjustment.occurred_at or utc_now(),
            )
            self._db.add(movement)
            await self._db.flush()
            movement.balance_after = await self._apply_delta(
                product, variant, adjustment.quantity_delta, allow_negative=allow_negative
            )
            await self._db.flush()
            await savepoint.commit()
        except IntegrityError:
            await savepoint.rollback()
            existing = (
                await self._by_key(adjustment.idempotency_key)
                if adjustment.idempotency_key
                else None
            )
            if existing is None:
                raise
            return self._replayed(existing, adjustment)
        except BaseException:
            await savepoint.rollback()
            raise
        return movement

    async def assert_available(self, lines: list[tuple[uuid.UUID, uuid.UUID | None, int]]) -> None:
        """Refuse up front when stock cannot cover ``(product, variant, qty)`` lines.

        A read-only check for flows that must fail *before* an external side
        effect (a courier booking). The deduction itself still happens with the
        atomic guard, so this narrows the window rather than closing it.
        """
        needed: dict[tuple[uuid.UUID, uuid.UUID | None], int] = {}
        for product_id, variant_id, quantity in lines:
            needed[(product_id, variant_id)] = needed.get((product_id, variant_id), 0) + quantity
        for (product_id, variant_id), quantity in needed.items():
            product = await self._db.get(Product, product_id)
            if product is None or not product.stock_tracking_enabled:
                continue
            variant = None
            available = product.stock_on_hand
            if variant_id is not None:
                variant = await self._db.get(ProductVariant, variant_id)
                if variant is None:
                    continue
                available = variant.stock_on_hand
            if available < quantity:
                raise insufficient_stock(
                    product=product, variant=variant, available=available, requested=quantity
                )

    async def _by_key(self, key: str) -> StockMovement | None:
        return (
            await self._db.execute(
                sa.select(StockMovement).where(StockMovement.idempotency_key == key)
            )
        ).scalar_one_or_none()

    @staticmethod
    def _replayed(existing: StockMovement, adjustment: StockAdjustment) -> StockMovement:
        """The earlier movement, when the retry asked for the same thing."""
        if (
            existing.product_id != adjustment.product_id
            or existing.quantity_delta != adjustment.quantity_delta
            or existing.reason != str(adjustment.reason)
            or (adjustment.variant_id is not None and existing.variant_id != adjustment.variant_id)
        ):
            raise ConflictError(
                "That request id was already used for a different stock change",
                code=ErrorCode.IDEMPOTENCY_KEY_CONFLICT,
                details={"movement_id": str(existing.id)},
            )
        return existing

    async def _resolve_variant(
        self, product: Product, variant_id: uuid.UUID | None
    ) -> ProductVariant | None:
        if not product.has_variants:
            if variant_id is not None:
                raise ValidationError(
                    "This product has no variants", details={"product_id": str(product.id)}
                )
            return None
        if variant_id is None:
            raise ValidationError(
                "Choose which variant's stock changed",
                details={"product_id": str(product.id)},
            )
        variant = await self._db.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product.id:
            raise NotFoundError("Variant not found")
        return variant

    async def _apply_delta(
        self,
        product: Product,
        variant: ProductVariant | None,
        delta: int,
        *,
        allow_negative: bool,
    ) -> int:
        """Apply a **relative** change to the running balance(s), atomically.

        Deliberately a single ``SET stock_on_hand = stock_on_hand + :delta``
        rather than reading the value into Python and writing back a computed
        total. Two devices adjusting the same product at once both read the same
        starting figure, and a read-modify-write silently discards one of the
        two — the seller's stock drifts upward and the cached total stops
        matching the ledger.

        A relative update is correct under any interleaving on both PostgreSQL
        and SQLite. It does not depend on ``SELECT … FOR UPDATE``, which is a
        silent no-op on SQLite and would leave the invariant untested in CI.

        The ``stock_on_hand + :delta >= 0`` guard is part of the same statement,
        so the oversell check cannot be raced past either. For a variant the
        guard is on the variant — the thing on the shelf — and the product's
        total moves with it.
        """
        if variant is not None:
            balance = await self._relative_update(
                cast("sa.Table", ProductVariant.__table__),
                variant.id,
                delta,
                guard=not allow_negative,
            )
            if balance is None:
                raise insufficient_stock(
                    product=product,
                    variant=variant,
                    available=variant.stock_on_hand,
                    requested=-delta,
                )
            await self._relative_update(
                cast("sa.Table", Product.__table__), product.id, delta, guard=False
            )
            await self._db.refresh(variant, attribute_names=["stock_on_hand"])
            await self._db.refresh(product, attribute_names=["stock_on_hand"])
            return balance

        balance = await self._relative_update(
            cast("sa.Table", Product.__table__), product.id, delta, guard=not allow_negative
        )
        if balance is None:
            raise insufficient_stock(
                product=product, variant=None, available=product.stock_on_hand, requested=-delta
            )
        # Keep the in-session object consistent with what was just written.
        await self._db.refresh(product, attribute_names=["stock_on_hand"])
        return balance

    async def _relative_update(
        self, table: sa.Table, row_id: uuid.UUID, delta: int, *, guard: bool
    ) -> int | None:
        """``stock_on_hand += delta``; ``None`` when the guard refused it."""
        condition = table.c.id == row_id
        if guard:
            condition = sa.and_(condition, table.c.stock_on_hand + delta >= 0)
        result = await self._db.execute(
            sa.update(table)
            .where(condition)
            .values(stock_on_hand=table.c.stock_on_hand + delta)
            .execution_options(**{TENANT_CHECKED: True, "synchronize_session": False})
        )
        if cast("CursorResult[Any]", result).rowcount == 0:
            # The row exists — it was loaded by the caller — so the guard is
            # what rejected it.
            return None
        # Re-read inside the same transaction so ``balance_after`` reflects this
        # statement's own write rather than the pre-image loaded earlier.
        return int(
            (
                await self._db.execute(
                    sa.select(table.c.stock_on_hand)
                    .where(table.c.id == row_id)
                    .execution_options(**{TENANT_CHECKED: True})
                )
            ).scalar_one()
        )

    async def _lock_product(self, product_id: uuid.UUID) -> Product:
        """Fetch a product, taking a row lock where the backend offers one.

        The lock narrows the window on PostgreSQL and serialises every movement
        for one product (variants included — they are always locked through
        their product, so the lock order never inverts); correctness under
        concurrency comes from the relative update, not from here.
        """
        stmt = sa.select(Product).where(Product.id == product_id)
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update(of=Product)

        product = (await self._db.execute(stmt)).scalar_one_or_none()
        if product is None:
            raise NotFoundError("Product not found")
        return product

    async def history(
        self,
        product_id: uuid.UUID,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        variant_id: uuid.UUID | None = None,
        reasons: list[StockMovementReason] | None = None,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
    ) -> list[StockMovement]:
        """Movements newest first. Fetches ``limit + 1`` to detect a next page."""
        stmt = sa.select(StockMovement).where(StockMovement.product_id == product_id)
        if variant_id is not None:
            stmt = stmt.where(StockMovement.variant_id == variant_id)
        if reasons:
            stmt = stmt.where(StockMovement.reason.in_([str(reason) for reason in reasons]))
        if occurred_from is not None:
            stmt = stmt.where(StockMovement.occurred_at >= occurred_from)
        if occurred_to is not None:
            stmt = stmt.where(StockMovement.occurred_at < occurred_to)
        stmt = apply_cursor(stmt, StockMovement, cursor)
        stmt = stmt.order_by(StockMovement.created_at.desc(), StockMovement.id.desc()).limit(
            limit + 1
        )
        return list((await self._db.execute(stmt)).scalars().all())

    async def recalculate_stock(self, product_id: uuid.UUID) -> int:
        """Rebuild the running totals from the ledger and store them.

        The repair path for section 103's "rebuild materialised summary from
        ledger". Returns the corrected product balance.
        """
        product = await self._lock_product(product_id)
        total = (
            await self._db.execute(
                sa.select(sa.func.coalesce(sa.func.sum(StockMovement.quantity_delta), 0))
                .select_from(StockMovement)
                .where(StockMovement.product_id == product_id)
            )
        ).scalar_one()
        product.stock_on_hand = int(total)
        if product.has_variants:
            per_variant: dict[uuid.UUID | None, int] = {
                variant_id: int(total or 0)
                for variant_id, total in (
                    await self._db.execute(
                        sa.select(
                            StockMovement.variant_id, sa.func.sum(StockMovement.quantity_delta)
                        )
                        .where(
                            StockMovement.product_id == product_id,
                            StockMovement.variant_id.is_not(None),
                        )
                        .group_by(StockMovement.variant_id)
                    )
                ).all()
            }
            for variant in product.variants:
                variant.stock_on_hand = per_variant.get(variant.id, 0)
        await self._db.flush()
        return product.stock_on_hand


class ProductService:
    """Product catalogue operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._db = session
        self._stock = StockService(session)

    async def create(
        self,
        *,
        name: str,
        sku: str | None = None,
        description: str | None = None,
        cost_paisa: int = 0,
        default_selling_price_paisa: int = 0,
        stock_tracking_enabled: bool = True,
        opening_stock: int = 0,
        low_stock_threshold: int | None = None,
        opening_idempotency_key: str | None = None,
    ) -> Product:
        normalized_sku = (sku or "").strip() or None
        if normalized_sku is not None:
            await self._assert_sku_available(normalized_sku)

        product = Product(
            name=name.strip(),
            sku=normalized_sku,
            description=description,
            cost_paisa=cost_paisa,
            default_selling_price_paisa=default_selling_price_paisa,
            stock_tracking_enabled=stock_tracking_enabled,
            low_stock_threshold=low_stock_threshold,
        )
        self._db.add(product)
        await self._db.flush()

        if opening_stock and stock_tracking_enabled:
            # Even the initial count is a ledger entry, so "where did this
            # number come from" always has an answer.
            await self._stock.record_movement(
                StockAdjustment(
                    product_id=product.id,
                    quantity_delta=opening_stock,
                    reason=StockMovementReason.OPENING,
                    note="Opening stock",
                    idempotency_key=opening_idempotency_key,
                )
            )

        await record_audit(
            self._db,
            AuditAction.PRODUCT_CREATED,
            entity_type="product",
            entity_id=product.id,
            context={"name": product.name, "sku": product.sku},
        )
        await self._db.flush()
        await self._db.refresh(product, attribute_names=["variants"])
        return product

    async def get(self, product_id: uuid.UUID) -> Product:
        product = await self._db.get(Product, product_id)
        if product is None:
            raise NotFoundError("Product not found")
        return product

    async def update(
        self,
        product_id: uuid.UUID,
        *,
        name: str | None = None,
        sku: str | None = None,
        description: str | None = None,
        cost_paisa: int | None = None,
        default_selling_price_paisa: int | None = None,
        low_stock_threshold: int | None = None,
        is_active: bool | None = None,
        archived: bool | None = None,
    ) -> Product:
        product = await self.get(product_id)
        changed: list[str] = []

        if name is not None and name.strip() != product.name:
            product.name = name.strip()
            changed.append("name")
        if sku is not None:
            normalized = sku.strip() or None
            if normalized != product.sku:
                if normalized is not None:
                    await self._assert_sku_available(normalized, exclude_id=product.id)
                product.sku = normalized
                changed.append("sku")
        if description is not None:
            product.description = description
            changed.append("description")
        if cost_paisa is not None and cost_paisa != product.cost_paisa:
            # Historical order items keep their own cost snapshot, so this only
            # affects orders created from now on (master spec section 18.2).
            product.cost_paisa = cost_paisa
            changed.append("cost_paisa")
        if default_selling_price_paisa is not None:
            product.default_selling_price_paisa = default_selling_price_paisa
            changed.append("default_selling_price_paisa")
        if low_stock_threshold is not None:
            product.low_stock_threshold = low_stock_threshold
            changed.append("low_stock_threshold")
        if is_active is not None:
            product.is_active = is_active
            changed.append("is_active")
        if archived is not None:
            product.archived_at = utc_now() if archived else None
            product.is_active = product.is_active and not archived
            changed.append("archived")

        if changed:
            await record_audit(
                self._db,
                AuditAction.PRODUCT_UPDATED,
                entity_type="product",
                entity_id=product.id,
                context={"changed": sorted(changed)},
            )
        await self._db.flush()
        return product

    # ------------------------------------------------------------ variants --

    async def add_variant(
        self,
        product_id: uuid.UUID,
        *,
        name: str,
        sku: str | None = None,
        options: dict[str, str] | None = None,
        price_paisa: int | None = None,
        cost_paisa: int | None = None,
        low_stock_threshold: int | None = None,
        opening_stock: int = 0,
    ) -> ProductVariant:
        """Give a product a variant.

        The first variant turns a simple product into a variant product. Any
        stock the product held until then cannot be attributed to a variant by
        guessing, so it is moved out by one explained movement and the seller
        counts it into the variants — the history says exactly what happened.
        """
        product = await self._stock._lock_product(product_id)
        clean_name = name.strip()
        if not clean_name:
            raise ValidationError("A variant needs a name")
        if any(variant.name.lower() == clean_name.lower() for variant in product.variants):
            raise ConflictError(
                "This product already has a variant with that name",
                details={"name": clean_name},
            )
        normalized_sku = (sku or "").strip() or None
        if normalized_sku is not None:
            await self._assert_sku_available(normalized_sku)

        if not product.has_variants and product.stock_tracking_enabled and product.stock_on_hand:
            await self._stock.record_movement(
                StockAdjustment(
                    product_id=product.id,
                    quantity_delta=-product.stock_on_hand,
                    reason=StockMovementReason.MANUAL_ADJUSTMENT,
                    source=StockMovementSource.SYSTEM,
                    note="Stock moved into variants",
                ),
                allow_negative=True,
            )

        variant = ProductVariant(
            product_id=product.id,
            name=clean_name,
            sku=normalized_sku,
            options=dict(options or {}),
            price_paisa=price_paisa,
            cost_paisa=cost_paisa,
            low_stock_threshold=low_stock_threshold,
            position=len(product.variants),
        )
        self._db.add(variant)
        product.has_variants = True
        await self._db.flush()

        if opening_stock and product.stock_tracking_enabled:
            await self._stock.record_movement(
                StockAdjustment(
                    product_id=product.id,
                    variant_id=variant.id,
                    quantity_delta=opening_stock,
                    reason=StockMovementReason.OPENING,
                    note="Opening stock",
                )
            )

        await record_audit(
            self._db,
            AuditAction.PRODUCT_UPDATED,
            entity_type="product",
            entity_id=product.id,
            context={"changed": ["variants"], "variant_added": clean_name},
        )
        await self._db.flush()
        await self._db.refresh(product, attribute_names=["variants"])
        return variant

    async def update_variant(
        self,
        product_id: uuid.UUID,
        variant_id: uuid.UUID,
        *,
        name: str | None = None,
        sku: str | None = None,
        price_paisa: int | None = None,
        cost_paisa: int | None = None,
        low_stock_threshold: int | None = None,
        is_active: bool | None = None,
    ) -> ProductVariant:
        """Edit a variant. Stock is never set here — that is a movement."""
        variant = await self._db.get(ProductVariant, variant_id)
        if variant is None or variant.product_id != product_id:
            raise NotFoundError("Variant not found")
        changed: list[str] = []
        if name is not None and name.strip() and name.strip() != variant.name:
            product = await self.get(product_id)
            if any(
                other.id != variant.id and other.name.lower() == name.strip().lower()
                for other in product.variants
            ):
                raise ConflictError("This product already has a variant with that name")
            variant.name = name.strip()
            changed.append("name")
        if sku is not None:
            normalized = sku.strip() or None
            if normalized != variant.sku:
                if normalized is not None:
                    await self._assert_sku_available(normalized, exclude_variant_id=variant.id)
                variant.sku = normalized
                changed.append("sku")
        if price_paisa is not None:
            variant.price_paisa = price_paisa
            changed.append("price_paisa")
        if cost_paisa is not None:
            variant.cost_paisa = cost_paisa
            changed.append("cost_paisa")
        if low_stock_threshold is not None:
            variant.low_stock_threshold = low_stock_threshold
            changed.append("low_stock_threshold")
        if is_active is not None:
            variant.is_active = is_active
            changed.append("is_active")
        if changed:
            await record_audit(
                self._db,
                AuditAction.PRODUCT_UPDATED,
                entity_type="product",
                entity_id=product_id,
                context={"changed": ["variants"], "variant": str(variant.id), "fields": changed},
            )
        await self._db.flush()
        return variant

    async def find_by_sku(self, sku: str) -> tuple[Product, ProductVariant | None] | None:
        """The product — and variant, when the SKU is a variant's — it names."""
        product = (
            await self._db.execute(sa.select(Product).where(Product.sku == sku))
        ).scalar_one_or_none()
        if product is not None:
            return product, None
        variant = (
            await self._db.execute(sa.select(ProductVariant).where(ProductVariant.sku == sku))
        ).scalar_one_or_none()
        if variant is None:
            return None
        return await self.get(variant.product_id), variant

    # ------------------------------------------------------- seller stock --

    async def adjust_stock(
        self,
        product_id: uuid.UUID,
        *,
        quantity_delta: int,
        reason: StockMovementReason,
        note: str | None,
        variant_id: uuid.UUID | None = None,
        allow_negative: bool = False,
        client_key: str | None = None,
    ) -> StockMovement:
        """A seller's hand correction: count fix, damage, found or missing stock.

        Never sets a total. ``client_key`` is the caller's request id; it is
        namespaced so a client can never collide with a system key.
        """
        await self.get(product_id)
        key = f"client:{client_key}" if client_key else None
        replay = key is not None and await self._stock._by_key(key) is not None
        movement = await self._stock.record_movement(
            StockAdjustment(
                product_id=product_id,
                variant_id=variant_id,
                quantity_delta=quantity_delta,
                reason=reason,
                source=StockMovementSource.SELLER,
                note=note,
                idempotency_key=key,
            ),
            allow_negative=allow_negative,
        )
        if not replay:
            await record_audit(
                self._db,
                AuditAction.STOCK_ADJUSTED,
                entity_type="product",
                entity_id=product_id,
                reason=note,
                context={
                    "kind": "manual",
                    "reason": str(reason),
                    "quantity_delta": quantity_delta,
                    "variant_id": str(variant_id) if variant_id else None,
                },
            )
        return movement

    async def restock(
        self,
        product_id: uuid.UUID,
        *,
        quantity: int,
        variant_id: uuid.UUID | None = None,
        unit_cost_paisa: int | None = None,
        update_cost: bool = False,
        reference: str | None = None,
        note: str | None = None,
        received_at: datetime | None = None,
        client_key: str | None = None,
    ) -> StockMovement:
        """New goods arrived. Quantity first; cost only if asked.

        ``update_cost`` makes the unit cost the product's (or variant's) current
        cost — the figure future orders snapshot. Nothing is averaged and
        nothing already sold is revalued.
        """
        product = await self.get(product_id)
        key = f"client:{client_key}" if client_key else None
        replay = key is not None and await self._stock._by_key(key) is not None
        movement = await self._stock.record_movement(
            StockAdjustment(
                product_id=product_id,
                variant_id=variant_id,
                quantity_delta=quantity,
                reason=StockMovementReason.RESTOCK,
                source=StockMovementSource.SELLER,
                note=note,
                reference=reference,
                unit_cost_paisa=unit_cost_paisa,
                occurred_at=received_at,
                idempotency_key=key,
            )
        )
        if replay:
            return movement
        if update_cost and unit_cost_paisa is not None:
            if movement.variant_id is not None:
                variant = await self._db.get(ProductVariant, movement.variant_id)
                if variant is not None:
                    variant.cost_paisa = unit_cost_paisa
            else:
                product.cost_paisa = unit_cost_paisa
        await record_audit(
            self._db,
            AuditAction.STOCK_ADJUSTED,
            entity_type="product",
            entity_id=product_id,
            reason=note,
            context={
                "kind": "restock",
                "quantity": quantity,
                "variant_id": str(variant_id) if variant_id else None,
                "unit_cost_paisa": unit_cost_paisa,
                "cost_updated": bool(update_cost and unit_cost_paisa is not None),
            },
        )
        await self._db.flush()
        return movement

    # -------------------------------------------------------------- imports --

    async def import_row(self, values: dict[str, Any], *, idempotency_key: str) -> uuid.UUID:
        """Create or count one product import row. Returns the product id.

        * A SKU the shop already uses (product or variant) is a **stock count**:
          the file's quantity is what is on the shelf, and the difference is
          recorded as an ``IMPORT_ADJUSTMENT`` — history is never rewritten.
        * A row with a variant name adds that variant to the product of the
          same name, creating the product first when there is none.
        * Anything else is a new simple product with its opening stock.

        Every stock movement carries ``idempotency_key`` (derived from the
        import row), so a resumed or retried commit cannot move stock twice.
        """
        name = str(values["name"]).strip()
        sku = values.get("sku")
        variant_name = (values.get("variant") or "").strip() or None
        counted = values.get("opening_stock") if values.get("stock_given") else None
        threshold = values.get("low_stock_threshold")

        if sku:
            found = await self.find_by_sku(sku)
            if found is not None:
                product, variant = found
                await self._apply_count(product, variant, counted, threshold, idempotency_key)
                return product.id

        if variant_name:
            named = (
                await self._db.execute(
                    sa.select(Product)
                    .where(
                        sa.func.lower(Product.name) == name.lower(),
                        Product.archived_at.is_(None),
                    )
                    .order_by(Product.created_at)
                    .limit(1)
                )
            ).scalar_one_or_none()
            product = named or await self.create(
                name=name,
                description=values.get("description"),
                cost_paisa=values.get("cost_paisa", 0),
                default_selling_price_paisa=values.get("default_selling_price_paisa", 0),
            )
            existing = next(
                (v for v in product.variants if v.name.lower() == variant_name.lower()), None
            )
            if existing is not None:
                await self._apply_count(product, existing, counted, threshold, idempotency_key)
                return product.id
            variant = await self.add_variant(
                product.id, name=variant_name, sku=sku, low_stock_threshold=threshold
            )
            if counted:
                await self._stock.record_movement(
                    StockAdjustment(
                        product_id=product.id,
                        variant_id=variant.id,
                        quantity_delta=counted,
                        reason=StockMovementReason.OPENING,
                        source=StockMovementSource.IMPORT,
                        note="Opening stock (import)",
                        idempotency_key=idempotency_key,
                    )
                )
            return product.id

        product = await self.create(
            name=name,
            sku=sku,
            description=values.get("description"),
            cost_paisa=values.get("cost_paisa", 0),
            default_selling_price_paisa=values.get("default_selling_price_paisa", 0),
            opening_stock=counted or 0,
            low_stock_threshold=threshold,
            opening_idempotency_key=idempotency_key,
        )
        return product.id

    async def counted_change(self, sku: str | None, counted: int | None) -> int | None:
        """How far a counted quantity is from the stock a SKU holds now.

        ``None`` when the SKU is unknown or no count was given; 0 when they
        agree. The import dry run uses it to tell a genuine new count from a
        row that was simply imported before.
        """
        if not sku or counted is None:
            return None
        found = await self.find_by_sku(sku)
        if found is None:
            return None
        product, variant = found
        current = variant.stock_on_hand if variant is not None else product.stock_on_hand
        return counted - current

    async def _apply_count(
        self,
        product: Product,
        variant: ProductVariant | None,
        counted: int | None,
        threshold: int | None,
        idempotency_key: str,
    ) -> None:
        if variant is None and product.has_variants:
            raise ValidationError(
                "This SKU belongs to a product with variants; use a variant's SKU",
                details={"product_id": str(product.id)},
            )
        if threshold is not None:
            if variant is not None:
                variant.low_stock_threshold = threshold
            else:
                product.low_stock_threshold = threshold
        if counted is None or not product.stock_tracking_enabled:
            await self._db.flush()
            return
        current = variant.stock_on_hand if variant is not None else product.stock_on_hand
        delta = counted - current
        if delta:
            await self._stock.record_movement(
                StockAdjustment(
                    product_id=product.id,
                    variant_id=variant.id if variant is not None else None,
                    quantity_delta=delta,
                    reason=StockMovementReason.IMPORT_ADJUSTMENT,
                    source=StockMovementSource.IMPORT,
                    note=f"Stock count {counted} (import)",
                    idempotency_key=idempotency_key,
                ),
                # The file states what is on the shelf; a count below zero is
                # impossible, and one at zero is simply true.
                allow_negative=False,
            )
        await self._db.flush()

    # ---------------------------------------------------------------- lists --

    async def list_products(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        search: str | None = None,
        include_archived: bool = False,
        active_only: bool = False,
        low_stock_only: bool = False,
        out_of_stock_only: bool = False,
    ) -> list[Product]:
        stmt = sa.select(Product)

        if not include_archived:
            stmt = stmt.where(Product.archived_at.is_(None))
        if active_only:
            stmt = stmt.where(Product.is_active.is_(True))
        if low_stock_only:
            stmt = stmt.where(Product.stock_tracking_enabled.is_(True), low_stock_clause())
        if out_of_stock_only:
            stmt = stmt.where(Product.stock_tracking_enabled.is_(True), out_of_stock_clause())
        if search:
            term = f"%{search.strip().lower()}%"
            variant_match = sa.exists().where(
                ProductVariant.product_id == Product.id,
                sa.or_(
                    sa.func.lower(ProductVariant.name).like(term),
                    sa.func.lower(sa.func.coalesce(ProductVariant.sku, "")).like(term),
                ),
            )
            stmt = stmt.where(
                sa.or_(
                    sa.func.lower(Product.name).like(term),
                    sa.func.lower(sa.func.coalesce(Product.sku, "")).like(term),
                    variant_match,
                )
            )
        stmt = apply_cursor(stmt, Product, cursor)

        stmt = stmt.order_by(Product.created_at.desc(), Product.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def stock_summary(self) -> StockSummary:
        """Counts for the inventory header, computed in the database."""
        live = (
            Product.archived_at.is_(None),
            Product.is_active.is_(True),
            Product.stock_tracking_enabled.is_(True),
        )
        tracked, units = (
            await self._db.execute(
                sa.select(
                    sa.func.count(Product.id),
                    sa.func.coalesce(sa.func.sum(Product.stock_on_hand), 0),
                ).where(*live)
            )
        ).one()
        simple = (*live, Product.has_variants.is_(False))
        simple_low = await self._db.scalar(
            sa.select(sa.func.count(Product.id)).where(
                *simple,
                Product.low_stock_threshold.is_not(None),
                Product.stock_on_hand <= Product.low_stock_threshold,
            )
        )
        simple_out = await self._db.scalar(
            sa.select(sa.func.count(Product.id)).where(*simple, Product.stock_on_hand <= 0)
        )
        variants = (
            sa.select(sa.func.count(ProductVariant.id))
            .join(Product, Product.id == ProductVariant.product_id)
            .where(*live, ProductVariant.is_active.is_(True))
        )
        variant_low = await self._db.scalar(
            variants.where(
                ProductVariant.low_stock_threshold.is_not(None),
                ProductVariant.stock_on_hand <= ProductVariant.low_stock_threshold,
            )
        )
        variant_out = await self._db.scalar(variants.where(ProductVariant.stock_on_hand <= 0))
        return StockSummary(
            tracked_products=int(tracked or 0),
            total_units=int(units or 0),
            low_stock_items=int(simple_low or 0) + int(variant_low or 0),
            out_of_stock_items=int(simple_out or 0) + int(variant_out or 0),
        )

    async def _assert_sku_available(
        self,
        sku: str,
        *,
        exclude_id: uuid.UUID | None = None,
        exclude_variant_id: uuid.UUID | None = None,
    ) -> None:
        """A SKU names one thing in a shop — a product or a variant, not both."""
        stmt = sa.select(Product.id).where(Product.sku == sku)
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        taken = (await self._db.execute(stmt.limit(1))).scalar_one_or_none() is not None
        if not taken:
            variant_stmt = sa.select(ProductVariant.id).where(ProductVariant.sku == sku)
            if exclude_variant_id is not None:
                variant_stmt = variant_stmt.where(ProductVariant.id != exclude_variant_id)
            taken = (await self._db.execute(variant_stmt.limit(1))).scalar_one_or_none() is not None
        if taken:
            raise ConflictError("Another product already uses this SKU", details={"sku": sku})


def _variant_low_exists() -> sa.Exists:
    return sa.exists().where(
        ProductVariant.product_id == Product.id,
        ProductVariant.is_active.is_(True),
        ProductVariant.low_stock_threshold.is_not(None),
        ProductVariant.stock_on_hand <= ProductVariant.low_stock_threshold,
    )


def low_stock_clause() -> sa.ColumnElement[bool]:
    """Simple products on their own threshold; variant products on any variant's."""
    return sa.or_(
        sa.and_(
            Product.has_variants.is_(False),
            Product.low_stock_threshold.is_not(None),
            Product.stock_on_hand <= Product.low_stock_threshold,
        ),
        sa.and_(Product.has_variants.is_(True), _variant_low_exists()),
    )


def out_of_stock_clause() -> sa.ColumnElement[bool]:
    return sa.or_(
        sa.and_(Product.has_variants.is_(False), Product.stock_on_hand <= 0),
        sa.and_(
            Product.has_variants.is_(True),
            sa.exists().where(
                ProductVariant.product_id == Product.id,
                ProductVariant.is_active.is_(True),
                ProductVariant.stock_on_hand <= 0,
            ),
        ),
    )
