"""Product and stock services.

Every stock change goes through :meth:`StockService.record_movement`. There is
no other write path to ``Product.stock_on_hand``, which is what makes the ledger
and the running total impossible to disagree with each other.

Concurrency (master spec section 76): two devices adjusting the same product at
once must not lose one of the adjustments. The balance is changed by a relative
``SET stock_on_hand = stock_on_hand + :delta`` statement rather than a
read-modify-write, so any interleaving produces the right total on both
PostgreSQL and SQLite. See :meth:`StockService._apply_delta`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.audit import AuditAction, record_audit
from app.common.pagination import Cursor, apply_cursor
from app.core.clock import utc_now
from app.core.context import current_context
from app.core.errors import ConflictError, NotFoundError, ValidationError
from app.db.tenancy import TENANT_CHECKED
from app.products.models import (
    MOVEMENT_SIGN,
    Product,
    StockMovement,
    StockMovementReason,
    StockMovementSource,
)

__all__ = ["ProductService", "StockAdjustment", "StockService"]


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

        new_balance = await self._apply_delta(
            product, adjustment.quantity_delta, allow_negative=allow_negative
        )

        movement = StockMovement(
            tenant_id=product.tenant_id,
            product_id=product.id,
            order_id=adjustment.order_id,
            order_item_id=adjustment.order_item_id,
            consignment_id=adjustment.consignment_id,
            quantity_delta=adjustment.quantity_delta,
            balance_after=new_balance,
            reason=adjustment.reason,
            source=adjustment.source,
            actor_user_id=current_context().user_id,
            note=adjustment.note,
            occurred_at=adjustment.occurred_at or utc_now(),
        )
        self._db.add(movement)
        await self._db.flush()
        return movement

    async def _apply_delta(self, product: Product, delta: int, *, allow_negative: bool) -> int:
        """Apply a **relative** change to the running balance, atomically.

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
        so the oversell check cannot be raced past either.
        """
        # `Product.__table__` is declared as FromClause on the base; the
        # concrete Table is what `sa.update()` accepts.
        table = cast("sa.Table", Product.__table__)
        condition = table.c.id == product.id
        if not allow_negative:
            condition = sa.and_(condition, table.c.stock_on_hand + delta >= 0)

        result = await self._db.execute(
            sa.update(table)
            .where(condition)
            .values(stock_on_hand=table.c.stock_on_hand + delta)
            .execution_options(**{TENANT_CHECKED: True, "synchronize_session": False})
        )
        updated_rows = cast("CursorResult[Any]", result).rowcount

        if updated_rows == 0:
            # The row exists — it was loaded above — so the guard is what
            # rejected it.
            raise ConflictError(
                "Not enough stock",
                details={
                    "product_id": str(product.id),
                    "stock_on_hand": product.stock_on_hand,
                    "requested_delta": delta,
                },
            )

        # Re-read inside the same transaction so ``balance_after`` reflects this
        # statement's own write rather than the pre-image loaded earlier.
        new_balance = (
            await self._db.execute(
                sa.select(table.c.stock_on_hand)
                .where(table.c.id == product.id)
                .execution_options(**{TENANT_CHECKED: True})
            )
        ).scalar_one()

        # Keep the in-session object consistent with what was just written.
        await self._db.refresh(product, attribute_names=["stock_on_hand"])
        return int(new_balance)

    async def _lock_product(self, product_id: uuid.UUID) -> Product:
        """Fetch a product, taking a row lock where the backend offers one.

        The lock narrows the window on PostgreSQL; correctness under concurrency
        comes from the relative update in :meth:`_apply_delta`, not from here.
        """
        stmt = sa.select(Product).where(Product.id == product_id)
        dialect = self._db.bind.dialect.name if self._db.bind is not None else ""
        if dialect == "postgresql":
            stmt = stmt.with_for_update()

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
    ) -> list[StockMovement]:
        """Movements newest first. Fetches ``limit + 1`` to detect a next page."""
        stmt = (
            sa.select(StockMovement)
            .where(StockMovement.product_id == product_id)
            .order_by(StockMovement.created_at.desc(), StockMovement.id.desc())
            .limit(limit + 1)
        )
        stmt = apply_cursor(stmt, StockMovement, cursor)
        return list((await self._db.execute(stmt)).scalars().all())

    async def recalculate_stock(self, product_id: uuid.UUID) -> int:
        """Rebuild the running total from the ledger and store it.

        The repair path for section 103's "rebuild materialised summary from
        ledger". Returns the corrected balance.
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

    async def list_products(
        self,
        *,
        limit: int = 30,
        cursor: Cursor | None = None,
        search: str | None = None,
        include_archived: bool = False,
        active_only: bool = False,
        low_stock_only: bool = False,
    ) -> list[Product]:
        stmt = sa.select(Product)

        if not include_archived:
            stmt = stmt.where(Product.archived_at.is_(None))
        if active_only:
            stmt = stmt.where(Product.is_active.is_(True))
        if low_stock_only:
            stmt = stmt.where(
                Product.stock_tracking_enabled.is_(True),
                Product.low_stock_threshold.is_not(None),
                Product.stock_on_hand <= Product.low_stock_threshold,
            )
        if search:
            term = f"%{search.strip().lower()}%"
            stmt = stmt.where(
                sa.or_(
                    sa.func.lower(Product.name).like(term),
                    sa.func.lower(sa.func.coalesce(Product.sku, "")).like(term),
                )
            )
        stmt = apply_cursor(stmt, Product, cursor)

        stmt = stmt.order_by(Product.created_at.desc(), Product.id.desc()).limit(limit + 1)
        return list((await self._db.execute(stmt)).scalars().all())

    async def _assert_sku_available(self, sku: str, *, exclude_id: uuid.UUID | None = None) -> None:
        stmt = sa.select(Product.id).where(Product.sku == sku)
        if exclude_id is not None:
            stmt = stmt.where(Product.id != exclude_id)
        if (await self._db.execute(stmt.limit(1))).scalar_one_or_none() is not None:
            raise ConflictError("Another product already uses this SKU", details={"sku": sku})
